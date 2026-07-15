from __future__ import annotations

import json
import hashlib
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestra.attempt import Attempt, AttemptStore
from orchestra.harness import (
    HarnessLaunch,
    HarnessOutcome,
    NormalizedEvent,
    adapter_for,
    parse_role_result,
    role_contract_instruction,
)
from orchestra.selection import process_start_time


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, data: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, path)


def _role_result(attempt: Attempt, normalized: list[NormalizedEvent]):
    data: object | None = None
    if attempt.provider_output_path.exists():
        try:
            data = json.loads(attempt.provider_output_path.read_text())
        except ValueError:
            return None
    if data is None and attempt.data["harness"] == "claude":
        for event in reversed(normalized):
            if event.native_type == "result":
                data = event.details.get("structured_output")
                break
    if data is None:
        return None
    try:
        return parse_role_result(attempt.data["role"], data)
    except ValueError:
        return None


def _outer_argv(argv: list[str], attempt: Attempt, config: dict[str, Any]) -> list[str]:
    if config.get("outer_sandbox_enabled"):
        unit = str(config.get("outer_sandbox_unit", ""))
        if config.get("outer_sandbox_kind") != "systemd" \
                or os.environ.get("ORCHESTRA_OUTER_SANDBOX") != unit:
            raise RuntimeError("verified outer sandbox is not active")
        return argv
    if config.get("read_only_binds"):
        raise RuntimeError("read-only worktree seeds require the outer sandbox")
    return argv


def compose_harness_prompt(
    base_prompt: str,
    instructions: str,
    instruction_policy: str,
    role: str,
) -> str:
    """Compose one harness prompt without duplicating native instructions or schemas."""
    if instruction_policy == "native_project":
        sections = [base_prompt]
    elif instruction_policy == "explicit_bundle":
        sections = [base_prompt]
        if instructions:
            sections.append("# Resolved project instructions\n\n" + instructions)
    else:
        raise ValueError(f"unknown instruction policy: {instruction_policy}")
    sections.append(
        "# Required terminal response\n\n"
        "Only your terminal response is schema-constrained; progress may be concise prose. "
        + role_contract_instruction(role)
    )
    return "\n\n".join(sections)


def run_attempt(manifest_path: str | Path) -> int:
    manifest_path = Path(manifest_path)
    root = manifest_path.parents[3]
    store = AttemptStore(root)
    attempt = store.load(manifest_path.parent.name)
    adapter = adapter_for(attempt.data["harness"])
    config = attempt.data["configuration"]
    if attempt.data.get("preflight_error"):
        store.update(
            attempt, state="completed", completed_at=_now(), terminal_outcome="turn_failed",
            failure_category=str(
                attempt.data.get("preflight_error_category", "harness_failure")
            ),
            failure_evidence=f"preflight failed: {attempt.data['preflight_error']}",
        )
        return 0
    try:
        launch = HarnessLaunch(
            executable=str(config["executable"]), model=attempt.data["model"],
            reasoning_effort=str(config.get("reasoning_effort", "high")),
            cwd=Path(attempt.data["worktree"]), prompt_file=attempt.prompt_path,
            schema_file=attempt.schema_path, output_file=attempt.provider_output_path,
            sandbox=str(config.get("sandbox", "workspace-write")),
            extra_args=tuple(config.get("extra_args", [])),
            resume_session=str(config.get("resume_session", "")),
            instruction_policy=str(config.get("instruction_policy", "native_project")),
            delegation=str(config.get("delegation", "disabled")),
        )
        argv = _outer_argv(adapter.build_argv(launch), attempt, config)
        prompt = compose_harness_prompt(
            attempt.prompt_path.read_text(),
            attempt.instructions_path.read_text(),
            str(config.get("instruction_policy", "native_project")),
            str(attempt.data["role"]),
        )
    except Exception as exc:  # noqa: BLE001 - preparation failure must finalize durably
        attempt.stderr_path.write_text(f"supervisor preparation failed: {exc}\n")
        store.update(
            attempt, state="completed", completed_at=_now(), terminal_outcome="turn_failed",
            failure_category="environment_failure", failure_evidence=str(exc),
        )
        return 0
    launch_evidence = {
        "argv": argv,
        "cwd": str(launch.cwd),
        "environment": {
            name: os.environ.get(name, "") for name in (
                "CODEX_HOME", "CLAUDE_CONFIG_DIR", "HOME", "PATH",
                "ORCHESTRA_OUTER_SANDBOX",
            )
        },
    }
    store.update(
        attempt,
        effective_prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        harness_launch_sha256=hashlib.sha256(
            json.dumps(launch_evidence, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )
    normalized: list[NormalizedEvent] = []
    malformed = False
    lock = threading.Lock()
    last_event = time.monotonic()
    active_tools: dict[str, tuple[str, float, str]] = {}
    session_id = ""

    try:
        proc = subprocess.Popen(
            argv, cwd=attempt.data["worktree"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
            start_new_session=True,
        )
    except Exception as exc:  # noqa: BLE001 - wrapper failure is durable attempt evidence
        attempt.stderr_path.write_text(f"supervisor launch failed: {exc}\n")
        store.update(
            attempt, state="completed", completed_at=_now(), terminal_outcome="turn_failed",
            failure_category="harness_failure", failure_evidence=str(exc), process_exit=None,
        )
        return 0
    store.update(
        attempt, state="running", pid=proc.pid,
        proc_start=process_start_time(proc.pid) or "", started_at=_now(),
    )

    def read_stdout() -> None:
        nonlocal malformed, last_event, session_id
        assert proc.stdout is not None
        with attempt.stdout_path.open("a") as raw_stream:
            for offset, line in enumerate(proc.stdout):
                raw_stream.write(line)
                raw_stream.flush()
                try:
                    raw = json.loads(line)
                except ValueError:
                    malformed = True
                    event = NormalizedEvent("protocol_error", "malformed_jsonl", {
                        "offset": offset, "line": line.rstrip("\n"),
                    })
                    events = [event]
                else:
                    events = adapter.normalize(raw)
                with lock:
                    for event in events:
                        normalized.append(event)
                        payload = {
                            "kind": event.kind, "native_type": event.native_type,
                            "details": event.details, "offset": offset, "observed_at": _now(),
                        }
                        store.append_event(attempt, payload)
                        last_event = time.monotonic()
                        if event.kind == "session_started":
                            session_id = str(event.details.get("session_id", ""))
                        if event.kind == "tool_started":
                            tool_id = str(event.details.get("tool_id") or event.details.get("tool"))
                            active_tools[tool_id] = (
                                str(event.details.get("tool", "")), last_event, _now()
                            )
                        elif event.kind == "tool_completed":
                            tool_id = str(event.details.get("tool_id") or event.details.get("tool"))
                            active_tools.pop(tool_id, None)
                        oldest = min(active_tools.values(), key=lambda value: value[1]) \
                            if active_tools else None
                        store.update(
                            attempt, latest_event=event.kind, session_id=session_id,
                            active_tool=", ".join(value[0] for value in active_tools.values()),
                            active_tool_started_at=oldest[2] if oldest else "",
                        )

    def read_stderr() -> None:
        assert proc.stderr is not None
        with attempt.stderr_path.open("a") as stream:
            for line in proc.stderr:
                stream.write(line)
                stream.flush()

    stdout_thread = threading.Thread(target=read_stdout)
    stderr_thread = threading.Thread(target=read_stderr)
    stdout_thread.start()
    stderr_thread.start()
    assert proc.stdin is not None
    proc.stdin.write(prompt + "\n")
    proc.stdin.close()

    limits = config.get("limits", {})
    wall_limit = float(limits.get("wall_seconds", 0))
    idle_limit = float(limits.get("idle_seconds", 0))
    tool_limit = float(limits.get("active_tool_seconds", 0))
    grace = float(limits.get("grace_seconds", 10))
    begun = time.monotonic()
    limit_triggered = ""
    while proc.poll() is None:
        now = time.monotonic()
        with lock:
            has_active_tools = bool(active_tools)
            oldest_active = min((value[1] for value in active_tools.values()), default=0)
            observed_last_event = last_event
        stop_requested = attempt.stop_path.exists()
        if stop_requested:
            limit_triggered = "cancelled"
        elif wall_limit and now - begun > wall_limit:
            limit_triggered = "wall_seconds"
        elif not has_active_tools and idle_limit and now - observed_last_event > idle_limit:
            limit_triggered = "idle_seconds"
        elif has_active_tools and tool_limit and now - oldest_active > tool_limit:
            limit_triggered = "active_tool_seconds"
        if limit_triggered:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + grace
            while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            break
        time.sleep(0.1)
    process_exit = proc.wait()
    stdout_thread.join()
    stderr_thread.join()
    process_completed_at = _now()
    _atomic_json(attempt.process_path, {
        "process_exit": process_exit,
        "process_signal": -process_exit if process_exit < 0 else None,
        "completed_at": process_completed_at,
        "limit_triggered": limit_triggered,
    })

    result = _role_result(attempt, normalized)
    if result is not None:
        _atomic_json(attempt.canonical_result_path, asdict(result))
    if limit_triggered:
        category = "cancelled" if limit_triggered == "cancelled" else "time_limit"
        outcome = HarnessOutcome("turn_failed", category, f"limit triggered: {limit_triggered}")
    elif malformed:
        outcome = HarnessOutcome("turn_failed", "protocol_failure", "malformed stdout JSONL")
    elif active_tools:
        outcome = HarnessOutcome(
            "turn_failed", "tool_observation_failure",
            "harness exited while a tool remained active",
        )
    else:
        outcome = adapter.classify(process_exit=process_exit, events=normalized, result=result)
    store.update(
        attempt, state="completed", process_exit=process_exit, completed_at=process_completed_at,
        process_signal=(-process_exit if process_exit < 0 else None),
        terminal_outcome=outcome.terminal, failure_category=outcome.category,
        failure_evidence=outcome.evidence, limit_triggered=limit_triggered,
    )
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m orchestra.supervisor MANIFEST")
    return run_attempt(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())
