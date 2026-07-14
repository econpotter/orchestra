from __future__ import annotations

import json
from pathlib import Path

from orchestra import git_ops, layout
from orchestra.archive import merge_and_archive
from orchestra.attempt import Attempt, AttemptStore
from orchestra.config import Config
from orchestra.dispatch import done_numbers
from orchestra.enginelock import engine_lock
from orchestra.issue import block_issue, exception_detail
from orchestra.projects import Project, find_project, read_projects
from orchestra.queue import find_issue, read_queue, write_queue
from orchestra.registry import issue_key, load_registry, save_registry
from orchestra.harness import parse_role_result
from orchestra.outcome import AttemptEvidence, decide_attempt
from orchestra.selection import worker_alive
from orchestra.validate import validate_structural

_REG_PATH = (".orchestra", "workers.json")


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


def _canonical_result(attempt: Attempt):
    if not attempt.canonical_result_path.is_file():
        return None
    try:
        return parse_role_result(
            attempt.data["role"], json.loads(attempt.canonical_result_path.read_text())
        )
    except (ValueError, OSError):
        return None


def _attempt_count(store: AttemptStore, attempt: Attempt) -> int:
    count = 1
    parent_id = attempt.data.get("parent_attempt")
    seen = {attempt.attempt_id}
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = store.load(parent_id)
        count += 1
        parent_id = parent.data.get("parent_attempt")
    return count


def _blocked_evidence(attempt: Attempt, reason: str) -> str:
    category = attempt.data.get("failure_category") or "attempt_failure"
    evidence = reason or attempt.data.get("failure_evidence") or "no failure evidence"
    return f"{category}: {evidence} (attempt {attempt.attempt_id})"


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
        if alive:
            if handle.role == "worker" and issue.status in {"validated", "needs_rework"}:
                issue.status = "in_progress"
                write_queue(qf, issues)
                transitions.append((key, "in_progress"))
            continue

        store = AttemptStore(root)
        attempt = store.load(handle.attempt_id)
        issue.blocked_reason = ""
        if attempt.data.get("state") != "completed":
            store.update(
                attempt, state="completed", terminal_outcome="turn_failed",
                failure_category="harness_failure",
                failure_evidence="supervisor exited without finalizing manifest",
            )
        result = _canonical_result(attempt)
        new_head = (git_ops.branch_head(root / project.path, handle.branch)
                    if handle.role != "validator" else "")
        new_commit = handle.role != "validator" and new_head != handle.start_sha
        if new_head:
            store.update(attempt, terminal_commit=new_head)
        attempts_cap = int(attempt.data["configuration"].get("attempts_cap", 1))
        decision = decide_attempt(AttemptEvidence(
            role=handle.role, new_commit=new_commit, result=result,
            terminal=str(attempt.data.get("terminal_outcome", "turn_failed")),
            failure_category=str(attempt.data.get("failure_category", "protocol_failure")),
            session_id=str(attempt.data.get("session_id", "")),
            resume_capable=bool(attempt.data.get("capabilities", {}).get("resume_session")),
            attempts_used=_attempt_count(store, attempt), attempts_cap=attempts_cap,
        ))
        store.update(attempt, retry_disposition=decision.action)

        if decision.action in {"resume", "fresh_attempt"}:
            prior = attempt.data["configuration"].get("dispatch_status", "validated")
            issue.status = ("open" if handle.role == "validator"
                            else "committed" if handle.role == "verifier"
                            else prior if prior == "needs_rework" else _validated_status(issue, config))
        elif decision.action == "committed":
            issue.status = "committed"
            issue.crash_retries = 0
            if result and result.decisions:
                issue.decisions = "\n".join(filter(None, (issue.decisions, result.decisions)))
        elif decision.action == "validated":
            issue.status = _validated_status(issue, config)
        elif decision.action == "accept":
            issue.verifier_feedback = ""
            issue.status = "awaiting_review"
        elif decision.action == "reject":
            complaints = (result.decisions or result.evidence) if result else decision.reason
            issue.verifier_feedback = complaints
            if issue.retries < config.retries_cap:
                issue.retries += 1
                issue.status = "needs_rework"
            else:
                issue.status = "awaiting_review"
        else:
            block_issue(issue, _blocked_evidence(attempt, decision.reason))

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
