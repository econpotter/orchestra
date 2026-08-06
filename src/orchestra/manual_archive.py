from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from orchestra import git_ops, layout
from orchestra.issue import block_issue, branch_name
from orchestra.projects import Project
from orchestra.queue import find_issue, read_queue, write_queue
from orchestra.registry import issue_key, load_registry
from orchestra.worktree_db import drop_worktree_db

_TERMINAL_STATUSES = {"archived", "dropped"}


@dataclass
class ManualArchiveOutcome:
    branch: str
    unmerged_commits: int


def manual_archive(
    root: str | Path,
    project: Project,
    number: int,
    *,
    status: str,
    reason: str,
) -> ManualArchiveOutcome:
    """Retire a live issue outside the merge-and-archive path: `orchestra archive` (the work
    landed by hand) or `orchestra drop` (the issue is moot; nothing landed). `status` is
    'archived' or 'dropped'; `reason` is stored verbatim in `Archive-Reason`.

    Guards, in order — each a loud refusal (raise), never a silent skip, so the CLI can
    report and move on to the next number in a multi-number invocation:
    1. the number must exist in the live queue;
    2. no live handle for it in workers.json (mirrors `cmd_hold`, cli.py:827) — a concurrent
       or unreconciled attempt racing this write would clobber it or orphan the worker;
    3. it must not already be terminal (archived or dropped).
    """
    root = Path(root)
    qf = layout.queue_file(root, project.name)
    issues = read_queue(qf)
    issue = find_issue(issues, number)
    if issue is None:
        raise ValueError(f"issue #{number} not found in {project.name}")

    reg = load_registry(root / ".orchestra" / "workers.json")
    if issue_key(project.name, number) in reg:
        raise ValueError(
            f"issue #{number} has an active or unreconciled attempt; "
            "kill and reconcile it first"
        )

    if issue.status in _TERMINAL_STATUSES:
        raise ValueError(f"issue #{number} is already {issue.status}")

    issue.status = status
    issue.archive_reason = reason

    repo = root / project.path
    branch = branch_name(issue)

    af = layout.archive_file(root, project.name)
    af.parent.mkdir(parents=True, exist_ok=True)
    archived = read_queue(af) if af.exists() else []
    archived.append(issue)
    # Write archive BEFORE removing from the live queue (same order as archive.py:87): a
    # crash between the two writes leaves the issue recoverable rather than lost.
    write_queue(af, archived)

    write_queue(qf, [i for i in issues if i.number != number])

    # Worktree removal is best-effort: the queue transition already succeeded above.
    try:
        git_ops.remove_worktree(repo, layout.worktree_dir(root, project.name, number))
    except subprocess.CalledProcessError as exc:
        print(
            f"warning: worktree removal failed ({status} succeeded): {exc.stderr.strip()}",
            file=sys.stderr,
        )
    if project.worktree_db:
        drop_worktree_db(repo / ".env", number)  # best-effort; warns, never blocks

    # The branch survives (deleting it is out of scope — it could destroy unread work), so
    # report what there is to clean up: its name and how far it has diverged from base.
    unmerged = (
        git_ops.unmerged_commit_count(repo, branch, project.branch)
        if git_ops.branch_exists(repo, branch)
        else 0
    )
    return ManualArchiveOutcome(branch=branch, unmerged_commits=unmerged)


def cascade_dropped(root: str | Path, project: Project, number: int, reason: str) -> list[int]:
    """Block every live issue in this project depending on a just-dropped `number`.

    A dropped number can never become a satisfied dependency, and reconcile only
    re-validates issues sitting at `open` (reconcile.py:288) — an issue already at
    `validated` would never be re-checked, never get a role, and sit forever with nothing
    recorded. Blocking immediately surfaces it in `orchestra status` instead. `archive`
    never calls this: an archived dependency IS satisfied."""
    root = Path(root)
    qf = layout.queue_file(root, project.name)
    issues = read_queue(qf)
    blocked: list[int] = []
    for issue in issues:
        if number in issue.depends_on:
            block_issue(issue, f"depends on dropped #{number}: {reason}")
            blocked.append(issue.number)
    if blocked:
        write_queue(qf, issues)
    return blocked
