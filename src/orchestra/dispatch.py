from __future__ import annotations

import sys
from pathlib import Path

from orchestra import git_ops, layout
from orchestra.adapter import launch
from orchestra.config import Config, validate_config
from orchestra.enginelock import engine_lock
from orchestra.issue import Issue, branch_name
from orchestra.projects import Project, read_projects
from orchestra.prompting import render_prompt
from orchestra.queue import read_queue
from orchestra.worktree import seed_worktree
from orchestra.worktree_db import create_worktree_db
from orchestra.registry import (
    WorkerHandle,
    issue_key,
    load_registry,
    save_registry,
)
from orchestra.selection import process_start_time, role_for_issue, select_dispatchable
from orchestra.validate import validate_structural

# The terminal status that can still be sitting in the LIVE queue. `merge_and_archive`
# moves an approved issue straight from `awaiting_review` to `archived` (there is no
# distinct `merged` resting state — the merge is the transition, not a status), then removes
# it from the live queue. The archive write happens first, so a crash between the two writes
# can leave an `archived` row briefly in both files; catching it here keeps its number
# resolvable as a dependency during that window. `archived` in the archive file is counted
# wholesale below.
_LIVE_DONE_STATUSES = {"archived"}


def done_numbers(root: str | Path, project: Project) -> set[int]:
    root = Path(root)
    nums: set[int] = set()
    qf = layout.queue_file(root, project.name)
    if qf.exists():
        nums |= {i.number for i in read_queue(qf) if i.status in _LIVE_DONE_STATUSES}
    af = layout.archive_file(root, project.name)
    if af.exists():
        nums |= {i.number for i in read_queue(af)}
    return nums


def build_context(
    root: str | Path,
    project: Project,
    issue: Issue,
    role: str,
    *,
    workdir: Path,
    result_file_path: Path,
    model: str,
    config: Config,
) -> dict:
    def _ref(ref: str | None) -> str:
        if not ref:
            return ""
        return f"{project.path}/{ref}" if role == "validator" else ref

    wf = config.workflows.get(project.workflow, {})
    workflow = "\n".join(f"{k}: {v}" for k, v in wf.items())
    return {
        "python": sys.executable,
        "repo": str(Path(root)),
        "model": model,
        "role": role,
        "workdir": str(workdir),
        "result_file": str(result_file_path),
        "results_dir": str(result_file_path.parent),
        "branch": branch_name(issue),
        "issue": f"{issue.number:03d}",
        "project": project.name,
        "title": issue.title,
        "plan": _ref(issue.plan),
        "spec": _ref(issue.spec),
        "acceptance": "\n".join(
            f"- [{'x' if a.checked else ' '}] {a.text}" for a in issue.acceptance
        ),
        "workflow": workflow,
        "decisions": issue.decisions,
        "verifier_feedback": issue.verifier_feedback,
        "rerun_checks": "yes" if config.verify_rerun_checks else "no",
    }


def dispatch(root: str | Path, config: Config, *, started: str) -> list[str]:
    # Serialize engine ops so a manual dispatch/tick alongside the timer can't double-launch.
    root = Path(root)
    with engine_lock(root) as acquired:
        if not acquired:
            return []
        return _dispatch(root, config, started=started)


def _dispatch(root: str | Path, config: Config, *, started: str) -> list[str]:
    validate_config(config)
    root = Path(root)
    if (root / ".orchestra" / "paused").exists():
        return []
    reg = load_registry(root / ".orchestra" / "workers.json")
    free = config.slots - len(reg)
    if free <= 0:
        return []
    active = set(reg)

    candidates: list[tuple[Project, Issue, str]] = []
    for project in read_projects(root / "PROJECTS.md"):
        done = done_numbers(root, project)
        qf = layout.queue_file(root, project.name)
        if not qf.exists():
            continue
        issues = read_queue(qf)
        known = {i.number for i in issues}
        dep_graph = {i.number: i.depends_on for i in issues}
        for issue in issues:
            role = role_for_issue(issue, active, done)
            if not role:
                continue
            if role == "validator":
                res = validate_structural(
                    issue, project_path=project.path, orchestra_root=root,
                    known_ids=known, archived_ids=done, base_branch=project.branch,
                    dep_graph=dep_graph,
                )
                if not res.ok:
                    continue  # structurally invalid — reconcile will block it
                if config.workflows and project.workflow not in config.workflows:
                    continue  # unknown workflow — reconcile will block it
                if not config.validate_semantic:
                    continue  # deterministic: reconcile promotes open->validated, no agent
            candidates.append((project, issue, role))

    chosen = select_dispatchable(candidates, free)

    launched: list[str] = []
    # Each issue's launch is isolated: one failure (missing provider binary, bad base
    # branch, worktree error) must NOT crash the whole dispatch — it would orphan
    # already-launched agents and skip reconcile this tick. On failure we log + skip and
    # let the next tick retry (dispatch can't mark the issue blocked — only reconcile
    # writes the queue). The registry is persisted in `finally` so launched handles are
    # never lost.
    try:
        for project, issue, role in chosen:
            key = issue_key(project.name, issue.number)
            try:
                if role == "validator":
                    workdir = root
                    start_sha = ""
                else:
                    workdir = layout.worktree_dir(root, project.name, issue.number)
                    if not workdir.exists():
                        git_ops.create_worktree(
                            root / project.path, workdir, branch_name(issue), project.branch
                        )
                        seed_worktree(
                            root / project.path, workdir, project.worktree_seed
                        )
                    # Ensure this issue's Postgres clone exists and the worktree .env
                    # points at it. Run every dispatch (not only on first creation) and
                    # idempotently, so a crash-retry whose worktree survived a failed
                    # create still gets its DB. A failure raises here and is caught by
                    # the per-issue handler below — it isolates this launch, never the loop.
                    if project.worktree_db:
                        create_worktree_db(
                            root / project.path / ".env", workdir / ".env", issue.number
                        )
                    start_sha = git_ops.branch_head(root / project.path, branch_name(issue))
                rf = layout.result_file(root, project.name, issue.number)
                completion = layout.completion_file(root, project.name, issue.number)
                stop = layout.stop_file(root, project.name, issue.number)
                for stale in (rf, completion, stop):
                    if stale.exists():
                        stale.unlink()
                role_cfg = config.roles[role]
                provider = config.providers[role_cfg.provider]
                ctx = build_context(
                    root, project, issue, role,
                    workdir=Path(workdir), result_file_path=rf, model=role_cfg.model,
                    config=config,
                )
                prompt_text = render_prompt(root, role_cfg.prompt, ctx)
                log = root / ".orchestra" / "logs" / f"{key}.log"
                pid = launch(
                    provider, config.sandbox, ctx,
                    prompt_text=prompt_text, cwd=Path(workdir), log_path=log,
                    completion_path=completion, stop_path=stop,
                )
                reg[key] = WorkerHandle(
                    project=project.name, number=issue.number, role=role,
                    branch=branch_name(issue), worktree=str(workdir), pid=pid,
                    log=str(log), result_file=str(rf), started=started,
                    start_sha=start_sha, proc_start=process_start_time(pid) or "",
                    completion_file=str(completion), stop_file=str(stop),
                )
                launched.append(key)
            except Exception as exc:  # noqa: BLE001 — isolate one bad issue from the rest
                print(f"dispatch: skipping {key} — launch failed: {exc}", file=sys.stderr)
                continue
    finally:
        save_registry(root / ".orchestra" / "workers.json", reg)
    return launched
