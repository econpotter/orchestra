from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from orchestra import git_ops, layout
from orchestra.issue import block_issue, branch_name
from orchestra.projects import Project
from orchestra.queue import find_issue, read_queue, write_queue
from orchestra.worktree_db import drop_worktree_db


def merge_and_archive(
    root: str | Path,
    project: Project,
    number: int,
    *,
    rebase_cap: int = 2,
    tmpdir: str | Path | None = None,
) -> str:
    root = Path(root)
    qf = layout.queue_file(root, project.name)
    issues = read_queue(qf)
    issue = find_issue(issues, number)
    if issue is None:
        raise FileNotFoundError(f"issue #{number} not found in {project.name}")
    if issue.status != "awaiting_review":
        raise ValueError(
            f"issue #{number} is {issue.status!r}, not 'awaiting_review' — refusing to merge"
        )
    repo = root / project.path
    issue_branch = branch_name(issue)
    wt = layout.worktree_dir(root, project.name, number)

    # Fail soft on conflict (Phase H #2): if the branch conflicts with the current base —
    # because another issue merged since it branched — don't error; send it back to rework
    # off the updated base. The merge itself never lands a conflicted tree.
    if git_ops.merge_conflicts(repo, project.branch, issue_branch):
        # Livelock guard: reuse the `retries` counter. Past the cap, stop reworking and
        # block for a human (the base keeps moving / the conflict is intractable).
        if issue.retries >= rebase_cap:
            block_issue(
                issue,
                f"rebase: still conflicts with '{project.branch}' after {issue.retries} "
                f"reworks — needs a manual merge",
            )
            write_queue(qf, issues)
            return "blocked"

        issue.retries += 1
        issue.status = "needs_rework"
        issue.verifier_feedback = (
            f"rebase: your branch conflicts with '{project.branch}' (another issue merged "
            f"since you branched). Re-implement your plan on top of current '{project.branch}' "
            f"and regenerate any derived artifacts so it merges clean."
        )
        write_queue(qf, issues)
        # Discard the stale worktree + branch so dispatch cuts a fresh one off the base.
        try:
            git_ops.remove_worktree(repo, wt)
        except subprocess.CalledProcessError:
            pass  # worktree may already be absent — fine
        if project.worktree_db:
            drop_worktree_db(repo / ".env", number)  # best-effort; warns, never blocks
        git_ops.delete_branch(repo, issue_branch)
        # The branch MUST be gone, or dispatch reuses it (off the stale base) and we loop.
        # If it survives, surface it instead of silently re-conflicting.
        if git_ops.branch_exists(repo, issue_branch):
            block_issue(
                issue,
                f"rebase: could not remove stale branch {issue_branch} for a clean recut "
                f"— manual cleanup needed",
            )
            write_queue(qf, issues)
            return "blocked"
        return "reworked"

    git_ops.merge_branch(repo, issue_branch, project.branch, tmpdir=tmpdir)

    issue.status = "archived"
    af = layout.archive_file(root, project.name)
    af.parent.mkdir(parents=True, exist_ok=True)
    archived = read_queue(af) if af.exists() else []
    archived.append(issue)
    # Write archive BEFORE removing from active queue so a crash leaves the issue recoverable.
    write_queue(af, archived)

    write_queue(qf, [i for i in issues if i.number != number])

    # Worktree removal is best-effort: merge + archive already succeeded above.
    try:
        git_ops.remove_worktree(repo, layout.worktree_dir(root, project.name, number))
    except subprocess.CalledProcessError as exc:
        print(
            f"warning: worktree removal failed (merge + archive succeeded): {exc.stderr.strip()}",
            file=sys.stderr,
        )
    if project.worktree_db:
        drop_worktree_db(repo / ".env", number)  # best-effort; warns, never blocks
    return "archived"
