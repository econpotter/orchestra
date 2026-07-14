from __future__ import annotations

import os
import re
import signal
import sys
import time
from pathlib import Path

from orchestra import git_ops, layout
from orchestra.archive import merge_and_archive
from orchestra.config import Config
from orchestra.dispatch import done_numbers
from orchestra.enginelock import engine_lock
from orchestra.issue import block_issue, exception_detail
from orchestra.projects import Project, find_project, read_projects
from orchestra.queue import find_issue, read_queue, write_queue
from orchestra.registry import WorkerHandle, issue_key, load_registry, save_registry
from orchestra.result import read_result
from orchestra.selection import worker_alive
from orchestra.validate import validate_structural

_REG_PATH = (".orchestra", "workers.json")


def _crash_retry(issue, config: Config) -> bool:
    """A crashed agent (dead process, no result) is re-queued to its prior dispatchable
    state instead of blocking, up to `crash_retries_cap`. Increments and returns True when
    a retry is allowed; returns False once the cap is hit (caller then blocks). The counter
    resets to 0 whenever the issue reaches a terminal via a real result, so the cap bounds a
    crash *loop*, not the issue's lifetime."""
    if issue.crash_retries < config.crash_retries_cap:
        issue.crash_retries += 1
        return True
    return False


def _matching_transient_error(log_path: str, patterns: list[str]) -> str | None:
    """Return the configured pattern matching the end of a worker log, if any.

    Worker logs can be large, so inspect only their final 64 KiB. The provider's terminal
    error is written at exit and therefore belongs in this tail; a missing or unreadable log
    is deliberately treated as an ordinary crash rather than silently retrying it.
    """
    try:
        with Path(log_path).open("rb") as log:
            log.seek(0, 2)
            log.seek(max(0, log.tell() - 65_536))
            tail = log.read().decode(errors="replace")
    except OSError:
        return None
    return next((pattern for pattern in patterns if re.search(pattern, tail)), None)


def _retry_transient_worker_crash(issue, config: Config, *, key: str, pattern: str) -> bool:
    """Count and log a classified transient worker crash when it may be retried."""
    if not _crash_retry(issue, config):
        return False
    print(
        f"reconcile: worker crash for {key} classified transient by {pattern!r}; "
        f"retry {issue.crash_retries}/{config.crash_retries_cap}",
        file=sys.stderr,
    )
    return True


def _validated_status(issue, config: Config) -> str:
    """Return the post-validation status under the configured network policy."""
    if issue.network and config.hold_network_issues:
        return "held"
    return "validated"


def _merge_failure_reason(exc: BaseException) -> str:
    """A never-empty, human-readable reason for an autoapprove merge failure (issue #006:
    merges were dying with a blank reason). Detail extraction is shared with retry-merge via
    `exception_detail`."""
    return f"autoapprove: merge failed after retry: {exception_detail(exc)}"


def _autoapprove_merge(
    root: Path,
    project: Project,
    number: int,
    *,
    rebase_cap: int,
    tmpdir: str | Path | None,
    attempts: int = 2,
) -> tuple[str | None, BaseException | None]:
    """Run merge_and_archive, retrying a transient failure once before giving up (the
    observed #006 failure — a tmpfs quota — was transient). Returns (result, None) on
    success or (None, last_exception) if every attempt raised. Never propagates: a merge
    failure must block the issue loudly, not crash the reconcile cycle."""
    last: BaseException | None = None
    for _ in range(attempts):
        try:
            result = merge_and_archive(
                root, project, number, rebase_cap=rebase_cap, tmpdir=tmpdir
            )
            return result, None
        except Exception as exc:  # noqa: BLE001 — any failure blocks loudly; see docstring
            last = exc
    return None, last


def _is_stalled(handle: WorkerHandle, idle_minutes: int) -> bool:
    if idle_minutes <= 0:
        return False
    log = Path(handle.log)
    if not log.exists():
        return False
    return (time.time() - log.stat().st_mtime) > idle_minutes * 60


def reconcile(root: str | Path, config: Config) -> list[tuple[str, str]]:
    # Serialize engine ops (see dispatch) — a concurrent reconcile/dispatch skips.
    root = Path(root)
    with engine_lock(root) as acquired:
        if not acquired:
            return []
        return _reconcile(root, config)


def _reconcile(root: str | Path, config: Config) -> list[tuple[str, str]]:
    root = Path(root)
    reg = load_registry(root / _REG_PATH[0] / _REG_PATH[1])
    projects = read_projects(root / "PROJECTS.md")
    transitions: list[tuple[str, str]] = []

    for key, handle in list(reg.items()):
        project = find_project(projects, handle.project)
        if project is None:
            del reg[key]
            continue
        qf = layout.queue_file(root, handle.project)
        issues = read_queue(qf)
        issue = find_issue(issues, handle.number)
        if issue is None:
            del reg[key]
            continue

        alive = worker_alive(handle)
        if alive and not _is_stalled(handle, config.stall_idle_minutes):
            if handle.role == "worker" and issue.status in {"validated", "needs_rework"}:
                issue.status = "in_progress"
                write_queue(qf, issues)
                transitions.append((key, "in_progress"))
            continue

        if alive:  # stalled
            if handle.stop_file:
                Path(handle.stop_file).parent.mkdir(parents=True, exist_ok=True)
                Path(handle.stop_file).touch()
            else:  # legacy worker without wrapper control file
                try:
                    os.kill(handle.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            block_issue(issue, f"stall: idle > {config.stall_idle_minutes}m")
            rf = Path(handle.result_file)
            if rf.exists():
                rf.unlink()
        else:
            res = read_result(handle.result_file)
            # Clear any stale block from a prior cycle up front; the blocked branches below
            # re-set it, so every non-blocked outcome (committed, validated, awaiting_review,
            # re-queued crash-retry) lands with an empty blocked_reason.
            issue.blocked_reason = ""
            # A crash is unambiguous: process dead AND no result file. A self-reported
            # block (result present) is a real verdict and is never crash-retried.
            if handle.role == "validator":
                if res and res.result == "validated":
                    issue.status = _validated_status(issue, config)
                    issue.crash_retries = 0
                elif res:  # self-reported invalid — not a crash
                    block_issue(issue, res.blocked_reason)
                elif _crash_retry(issue, config):  # re-validate
                    issue.status = "open"
                else:
                    block_issue(issue, "crash: validator produced no result")
            elif handle.role == "worker":
                new_head = git_ops.branch_head(root / project.path, handle.branch)
                if new_head != handle.start_sha:
                    issue.status = "committed"
                    issue.crash_retries = 0
                    if res and res.decisions:
                        issue.decisions = (
                            f"{issue.decisions}\n{res.decisions}".strip()
                            if issue.decisions
                            else res.decisions
                        )
                elif res:  # self-reported block (no commit) — not a crash
                    block_issue(issue, res.blocked_reason)
                elif (pattern := _matching_transient_error(
                    handle.log, config.crash_transient_error_patterns
                )) and _retry_transient_worker_crash(issue, config, key=key, pattern=pattern):
                    # Re-dispatch under the configured network policy, reusing the worktree.
                    issue.status = _validated_status(issue, config)
                else:
                    if pattern:
                        print(
                            f"reconcile: worker crash for {key} classified transient by "
                            f"{pattern!r}; retry cap {issue.crash_retries}/"
                            f"{config.crash_retries_cap} exhausted",
                            file=sys.stderr,
                        )
                        block_issue(
                            issue,
                            "crash: transient error classified by "
                            f"{pattern!r}; retry cap exhausted after "
                            f"{issue.crash_retries}/{config.crash_retries_cap} retries",
                        )
                    else:
                        block_issue(issue, "crash: no new commit and no result")
            elif handle.role == "verifier":
                if res and res.result == "accept":
                    issue.verifier_feedback = ""
                    issue.status = "awaiting_review"
                    issue.crash_retries = 0
                elif res and res.result == "reject":
                    complaints = res.decisions or res.blocked_reason
                    issue.crash_retries = 0
                    if issue.retries < config.retries_cap:
                        issue.retries += 1
                        issue.verifier_feedback = complaints
                        issue.status = "needs_rework"
                    else:
                        issue.verifier_feedback = complaints
                        issue.status = "awaiting_review"
                elif _crash_retry(issue, config):  # re-verify the committed diff
                    issue.status = "committed"
                else:
                    block_issue(issue, "crash: verifier produced no result")

            rf = Path(handle.result_file)
            if rf.exists():
                rf.unlink()

        write_queue(qf, issues)
        transitions.append((key, issue.status))
        del reg[key]

    save_registry(root / _REG_PATH[0] / _REG_PATH[1], reg)

    for project in projects:
        qf = layout.queue_file(root, project.name)
        if not qf.exists():
            continue
        issues = read_queue(qf)
        known = {i.number for i in issues}
        done = done_numbers(root, project)
        dep_graph = {i.number: i.depends_on for i in issues}
        changed = False
        for issue in issues:
            if issue.status == "held" and issue.network and not config.hold_network_issues:
                issue.status = "validated"
                issue.blocked_reason = ""
                transitions.append((issue_key(project.name, issue.number), "validated"))
                changed = True
                continue
            if issue.status != "open":
                continue
            validation = validate_structural(
                issue, project_path=project.path, orchestra_root=root,
                known_ids=known, archived_ids=done, base_branch=project.branch,
                dep_graph=dep_graph,
            )
            reasons = list(validation.reasons)
            if config.workflows and project.workflow not in config.workflows:
                reasons.append(f"unknown workflow '{project.workflow}'")
            if reasons:
                block_issue(issue, "invalid: " + "; ".join(reasons))
                transitions.append((issue_key(project.name, issue.number), "blocked"))
                changed = True
            elif not config.validate_semantic:
                # Deterministic validation: no LLM validator agent — promote here.
                issue.status = _validated_status(issue, config)
                issue.blocked_reason = ""  # clear any stale invalid from a prior cycle
                transitions.append((issue_key(project.name, issue.number), issue.status))
                changed = True
        if changed:
            write_queue(qf, issues)

    if config.autoapprove:
        merge_tmpdir = (root / config.merge_tmpdir) if config.merge_tmpdir else None
        for project in projects:
            qf = layout.queue_file(root, project.name)
            if not qf.exists():
                continue
            issues = read_queue(qf)
            for issue in list(issues):
                if issue.status != "awaiting_review":
                    continue
                key = issue_key(project.name, issue.number)
                result, err = _autoapprove_merge(
                    root, project, issue.number,
                    rebase_cap=config.retries_cap, tmpdir=merge_tmpdir,
                )
                if err is not None:
                    refreshed = read_queue(qf)
                    current = find_issue(refreshed, issue.number)
                    if current is not None:
                        block_issue(current, _merge_failure_reason(err))
                        write_queue(qf, refreshed)
                        transitions.append((key, "blocked"))
                    else:
                        transitions.append((key, "failed"))
                    continue
                assert result is not None  # err is None ⇒ merge returned a status
                transitions.append((key, "archived" if result == "archived" else result))

    return transitions
