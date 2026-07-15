from __future__ import annotations

import json
import hashlib
import sys
import subprocess
import uuid
import shutil
import os
import time
from dataclasses import asdict
from pathlib import Path

from orchestra import git_ops, layout
from orchestra.attempt import AttemptStore
from orchestra.config import Config, validate_config
from orchestra.enginelock import engine_lock
from orchestra.issue import Issue, branch_name
from orchestra.projects import Project, read_projects
from orchestra.harness import (
    adapter_for,
    preflight_authentication,
    preflight_harness,
    role_schema,
)
from orchestra.envelope import build_execution_envelope, execution_envelope_fingerprint
from orchestra.prompting import (
    CODEX_INSTRUCTION_MAX_BYTES,
    InstructionBundle,
    render_prompt,
    resolve_configured_instruction,
    resolve_instruction_provenance,
)
from orchestra.provenance import runtime_provenance
from orchestra.queue import read_queue
from orchestra.worktree import prepare_read_only_binds, seed_worktree
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


def _launch_fingerprint(argv: list[str]) -> str:
    return hashlib.sha256(
        json.dumps(argv, separators=(",", ":")).encode()
    ).hexdigest()


def _supervisor_service_argv(root: Path, attempt, config: Config) -> list[str]:
    unit = str(attempt.data["configuration"]["outer_sandbox_unit"])
    worktree = Path(attempt.data["worktree"])
    properties = ["ProtectSystem=strict", "ProtectHome=read-only",
                  f"ReadWritePaths={attempt.directory}"]
    if attempt.data["role"] == "worker":
        properties.append(f"ReadWritePaths={worktree}")
        common_git = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=worktree, text=True, capture_output=True, check=True,
        ).stdout.strip()
        properties.append(f"ReadWritePaths={common_git}")
    attempt_config = attempt.data["configuration"]
    for _source, target in attempt_config.get("read_only_binds", ()):
        properties.append(f"ReadOnlyPaths={target}")
    envelope = attempt_config.get("execution_envelope", {})
    for path in envelope.get("read_write_paths", ()):
        properties.append(f"ReadWritePaths={path}")
    for path in envelope.get("inaccessible_paths", ()):
        properties.append(f"InaccessiblePaths={path}")
    environment = {
        "ORCHESTRA_OUTER_SANDBOX": unit,
        "PATH": os.environ.get("PATH", ""),
        **dict(envelope.get("environment", ())),
    }
    argv = [config.sandbox.executable, "--user", "--quiet", "--collect", "--unit", unit,
            f"--working-directory={root}"]
    for key, value in environment.items():
        argv.append(f"--setenv={key}={value}")
    for prop in properties:
        argv += ["--property", prop]
    return argv + ["--", sys.executable, "-m", "orchestra.supervisor", str(attempt.path)]


def _start_supervisor(root: Path, attempt, config: Config) -> tuple[int, str]:
    if not config.sandbox.enabled:
        environment = os.environ.copy()
        environment.update(dict(
            attempt.data["configuration"].get("execution_envelope", {}).get(
                "environment", ()
            )
        ))
        argv = [sys.executable, "-m", "orchestra.supervisor", str(attempt.path)]
        AttemptStore(root).update(
            attempt, supervisor_launch_sha256=_launch_fingerprint(argv)
        )
        process = subprocess.Popen(
            argv,
            cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, text=True, env=environment,
        )
        return process.pid, ""

    unit = str(attempt.data["configuration"]["outer_sandbox_unit"])
    argv = _supervisor_service_argv(root, attempt, config)
    AttemptStore(root).update(
        attempt, supervisor_launch_sha256=_launch_fingerprint(argv)
    )
    result = subprocess.run(argv, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"outer supervisor service failed: {result.stderr.strip()}")
    for _ in range(40):
        query = subprocess.run(
            ["systemctl", "--user", "show", "--property=MainPID", "--value", unit],
            text=True, capture_output=True, check=False,
        )
        if query.returncode == 0 and query.stdout.strip().isdigit():
            pid = int(query.stdout.strip())
            if pid > 0:
                return pid, unit
        time.sleep(0.05)
    raise RuntimeError(f"outer supervisor service {unit} did not expose a MainPID")


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
            attempt = None
            try:
                if role == "validator":
                    workdir = root
                    start_sha = ""
                    read_only_binds = []
                else:
                    workdir = layout.worktree_dir(root, project.name, issue.number)
                    if not workdir.exists():
                        git_ops.create_worktree(
                            root / project.path, workdir, branch_name(issue), project.branch
                        )
                        read_only_binds = seed_worktree(
                            root / project.path, workdir, project.worktree_seed
                        )
                    else:
                        read_only_binds = prepare_read_only_binds(
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
                role_cfg = config.roles[role]
                harness = config.harnesses[role_cfg.harness]
                ctx = build_context(
                    root, project, issue, role,
                    workdir=Path(workdir), model=role_cfg.model, config=config,
                )
                prompt_text = render_prompt(root, role_cfg.prompt, ctx)
                instruction_bundle = resolve_instruction_provenance(
                    workdir, boundary=root, harness_kind=harness.kind
                )
                if harness.environment.instructions_file:
                    automation_text, automation_source = resolve_configured_instruction(
                        root, harness.environment.instructions_file
                    )
                    if harness.kind == "codex":
                        project_bytes = sum(
                            len((Path(workdir) / source.path).read_bytes())
                            for source in instruction_bundle.sources
                        )
                        if len(automation_text.encode()) + project_bytes \
                                > CODEX_INSTRUCTION_MAX_BYTES:
                            raise ValueError(
                                "combined Codex automation and project instructions exceed "
                                f"the default {CODEX_INSTRUCTION_MAX_BYTES}-byte discovery limit"
                            )
                    instruction_bundle = InstructionBundle(
                        text=(
                            f"# Automation instructions\n\n{automation_text.rstrip()}\n\n"
                            + instruction_bundle.text
                        ),
                        sources=(automation_source, *instruction_bundle.sources),
                    )
                instructions = instruction_bundle.text
                store = AttemptStore(root)
                parent = store.latest(project.name, issue.number, role)
                retry_parent = (parent if parent and parent.data.get("retry_disposition")
                                in {"resume", "fresh_attempt"} else None)
                chain_start_sha = (str(retry_parent.data.get("start_commit", start_sha))
                                   if retry_parent else start_sha)
                attempt_id = f"{project.name}-{issue.number:03d}-{role}-{uuid.uuid4().hex[:12]}"
                supervisor_unit = f"orchestra-supervisor-{attempt_id.replace('_', '-')[:40]}"
                attempt_config = {
                    "kind": harness.kind, "executable": harness.executable,
                    "reasoning_effort": harness.reasoning_effort,
                    "sandbox": harness.sandbox, "extra_args": harness.extra_args,
                    "attempts_cap": harness.attempts_cap, "limits": asdict(harness.limits),
                    "resume_session": (retry_parent.data.get("session_id", "")
                                       if retry_parent and
                                       retry_parent.data.get("retry_disposition") == "resume"
                                       else ""),
                    "dispatch_status": issue.status,
                    "outer_sandbox_enabled": config.sandbox.enabled,
                    "outer_sandbox_kind": config.sandbox.kind,
                    "outer_sandbox_executable": config.sandbox.executable,
                    "outer_sandbox_unit": supervisor_unit,
                    "read_only_binds": [[str(source), str(target)]
                                        for source, target in read_only_binds],
                    "instruction_policy": role_cfg.instruction_policy,
                    "delegation": role_cfg.delegation,
                }
                adapter = adapter_for(harness.kind)
                envelope = build_execution_envelope(
                    root, role_cfg.harness, harness, adapter.capabilities,
                    home=Path.home(), instruction_policy=role_cfg.instruction_policy,
                )
                for path in envelope.read_write_paths:
                    Path(path).mkdir(parents=True, mode=0o700, exist_ok=True)
                attempt_config["execution_envelope"] = asdict(envelope)
                instruction_setup_error = ""
                if harness.kind == "codex" and harness.environment.instructions_file:
                    codex_home = Path(dict(envelope.environment)["CODEX_HOME"])
                    installed_instructions = codex_home / "AGENTS.md"
                    if (codex_home / "AGENTS.override.md").exists():
                        instruction_setup_error = (
                            "isolated Codex AGENTS.override.md shadows configured automation "
                            "instructions; remove it and rerun harness setup"
                        )
                    elif not installed_instructions.is_file():
                        instruction_setup_error = (
                            "isolated Codex automation instructions are not installed; "
                            f"run orchestra harness setup {role_cfg.harness}"
                        )
                    elif installed_instructions.read_text() != automation_text:
                        instruction_setup_error = (
                            "isolated Codex automation instructions have drifted; "
                            f"rerun orchestra harness setup {role_cfg.harness}"
                        )
                attempt = store.create(
                    attempt_id=attempt_id, project=project.name, number=issue.number,
                    role=role, harness=harness.kind, model=role_cfg.model,
                    worktree=Path(workdir), branch=branch_name(issue),
                    start_commit=chain_start_sha,
                    prompt=prompt_text, instruction_bundle=instructions,
                    configuration=attempt_config,
                    capabilities=envelope.effective_capabilities,
                    parent_attempt=retry_parent.attempt_id if retry_parent else None,
                )
                store.update(
                    attempt,
                    instruction_policy=role_cfg.instruction_policy,
                    instruction_sources=[asdict(source) for source in instruction_bundle.sources],
                    delegation_policy=role_cfg.delegation,
                    supported_capabilities=adapter.capabilities,
                    execution_envelope_sha256=execution_envelope_fingerprint(envelope),
                    engine_provenance=runtime_provenance(),
                )
                if instruction_setup_error:
                    store.update(
                        attempt,
                        preflight="failed",
                        preflight_error=instruction_setup_error,
                        preflight_error_category="environment_failure",
                    )
                schema_text = json.dumps(role_schema(role), indent=2)
                attempt.schema_path.write_text(schema_text)
                store.update(attempt, result_schema_sha256=hashlib.sha256(
                    schema_text.encode()).hexdigest())
                if harness.preflight and not instruction_setup_error:
                    try:
                        version = preflight_harness(harness.kind, harness.executable)
                        executable_path = shutil.which(harness.executable) or harness.executable
                        store.update(
                            attempt, harness_version=version,
                            harness_executable=str(Path(executable_path).resolve()),
                            preflight="passed",
                        )
                    except Exception as exc:  # noqa: BLE001 - reconciler owns queue outcome
                        store.update(attempt, preflight="failed", preflight_error=str(exc))
                    else:
                        environment = os.environ.copy()
                        environment.update(dict(envelope.environment))
                        try:
                            preflight_authentication(
                                harness.kind, harness.executable, environment
                            )
                        except Exception as exc:  # noqa: BLE001 - durable failure evidence
                            store.update(
                                attempt,
                                preflight="failed",
                                preflight_error=str(exc),
                                preflight_error_category="authentication_failure",
                            )
                supervisor_pid, supervisor_unit = _start_supervisor(root, attempt, config)
                reg[key] = WorkerHandle(
                    project=project.name, number=issue.number, role=role,
                    branch=branch_name(issue), worktree=str(workdir), pid=supervisor_pid,
                    attempt_id=attempt_id, manifest=str(attempt.path),
                    stdout=str(attempt.stdout_path), stderr=str(attempt.stderr_path),
                    started=started, start_sha=chain_start_sha,
                    proc_start=process_start_time(supervisor_pid) or "",
                    supervisor_unit=supervisor_unit,
                )
                launched.append(key)
            except Exception as exc:  # noqa: BLE001 — isolate one bad issue from the rest
                print(f"dispatch: skipping {key} — launch failed: {exc}", file=sys.stderr)
                if attempt is not None:
                    store.update(
                        attempt, state="completed", terminal_outcome="turn_failed",
                        failure_category="harness_failure",
                        failure_evidence=f"supervisor launch failed: {exc}",
                    )
                    reg[key] = WorkerHandle(
                        project=project.name, number=issue.number, role=role,
                        branch=branch_name(issue), worktree=str(workdir), pid=0,
                        attempt_id=attempt.attempt_id, manifest=str(attempt.path),
                        stdout=str(attempt.stdout_path), stderr=str(attempt.stderr_path),
                        started=started, start_sha=(str(attempt.data.get("start_commit", start_sha))),
                        proc_start="",
                        supervisor_unit=str(attempt_config.get("outer_sandbox_unit", "")),
                    )
                continue
    finally:
        save_registry(root / ".orchestra" / "workers.json", reg)
    return launched
