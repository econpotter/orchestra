from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


FAILURE_CATEGORIES = {
    "authentication_failure", "quota_failure", "upstream_failure", "harness_failure",
    "protocol_failure", "tool_observation_failure", "environment_failure",
    "acceptance_failure", "needs_human", "cancelled", "time_limit",
}
ROLE_OUTCOMES = {
    "worker": ("committed", "blocked"),
    "validator": ("validated", "blocked"),
    "verifier": ("accept", "reject", "blocked"),
}


@dataclass(frozen=True)
class RoleResult:
    schema_version: int
    outcome: str
    decisions: str
    failure_category: str
    evidence: str
    requires_human: bool


@dataclass(frozen=True)
class NormalizedEvent:
    kind: str
    native_type: str
    details: dict[str, Any]


@dataclass(frozen=True)
class HarnessOutcome:
    terminal: str
    category: str = ""
    evidence: str = ""


@dataclass(frozen=True)
class HarnessLaunch:
    executable: str
    model: str
    reasoning_effort: str
    cwd: Path
    prompt_file: Path
    schema_file: Path
    output_file: Path
    sandbox: str
    extra_args: tuple[str, ...] = ()
    resume_session: str = ""


def role_schema(role: str) -> dict[str, Any]:
    if role not in ROLE_OUTCOMES:
        raise ValueError(f"unknown role: {role}")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version", "outcome", "decisions", "failure_category",
            "evidence", "requires_human",
        ],
        "properties": {
            "schema_version": {"type": "integer", "const": 1},
            "outcome": {"type": "string", "enum": list(ROLE_OUTCOMES[role])},
            "decisions": {"type": "string"},
            "failure_category": {"type": "string", "enum": ["", *sorted(FAILURE_CATEGORIES)]},
            "evidence": {"type": "string"},
            "requires_human": {"type": "boolean"},
        },
    }


def parse_role_result(role: str, data: object) -> RoleResult:
    if role not in ROLE_OUTCOMES or not isinstance(data, dict):
        raise ValueError("role result must be an object for a known role")
    required = set(role_schema(role)["required"])
    if set(data) != required:
        raise ValueError(f"role result fields must be exactly {sorted(required)}")
    if data["schema_version"] != 1:
        raise ValueError("unsupported role result schema_version")
    if data["outcome"] not in ROLE_OUTCOMES[role]:
        raise ValueError(f"invalid {role} outcome: {data['outcome']!r}")
    if not all(isinstance(data[key], str) for key in
               ("outcome", "decisions", "failure_category", "evidence")):
        raise ValueError("role result string fields must be strings")
    if not isinstance(data["requires_human"], bool):
        raise ValueError("requires_human must be a boolean")
    success = data["outcome"] in {"committed", "validated", "accept", "reject"}
    if success and data["failure_category"]:
        raise ValueError("successful outcome cannot have failure_category")
    if not success and data["failure_category"] not in FAILURE_CATEGORIES:
        raise ValueError("unsuccessful outcome requires a stable failure_category")
    if data["failure_category"] in {"needs_human", "acceptance_failure"} \
            and not data["requires_human"]:
        raise ValueError("human/acceptance failures must require human review")
    return RoleResult(**data)


class HarnessAdapter(Protocol):
    name: str
    capabilities: dict[str, bool]

    def build_argv(self, launch: HarnessLaunch) -> list[str]: ...
    def normalize(self, raw: dict[str, Any]) -> list[NormalizedEvent]: ...
    def classify(self, *, process_exit: int, events: list[NormalizedEvent],
                 result: RoleResult | None) -> HarnessOutcome: ...


class CodexExecAdapter:
    name = "codex"
    capabilities = {
        "structured_events": True, "native_result_schema": True,
        "durable_session": True, "resume_session": True, "active_tool_events": True,
        "token_usage": True, "explicit_config_isolation": True, "graceful_cancel": True,
    }

    def build_argv(self, launch: HarnessLaunch) -> list[str]:
        base = [launch.executable, "exec"]
        if launch.resume_session:
            base += ["resume", "--json", "--ignore-user-config", "--strict-config",
                     "--model", launch.model]
            if launch.sandbox == "danger-full-access":
                base.append("--dangerously-bypass-approvals-and-sandbox")
            base += [
                "--output-schema", str(launch.schema_file),
                "--output-last-message", str(launch.output_file),
                "--config", f'model_reasoning_effort="{launch.reasoning_effort}"',
                *launch.extra_args, launch.resume_session, "-",
            ]
            return base
        base += [
            "--json", "--ignore-user-config", "--strict-config", "--model", launch.model,
            "-C", str(launch.cwd), "--sandbox", launch.sandbox, "--color", "never",
            "--output-schema", str(launch.schema_file),
            "--output-last-message", str(launch.output_file),
            "--config", f'model_reasoning_effort="{launch.reasoning_effort}"',
            *launch.extra_args, "-",
        ]
        return base

    def normalize(self, raw: dict[str, Any]) -> list[NormalizedEvent]:
        native = str(raw.get("type", ""))
        details: dict[str, Any] = {}
        kind = ""
        if native == "thread.started":
            kind, details = "session_started", {"session_id": raw.get("thread_id", "")}
        elif native == "turn.started":
            kind = "turn_started"
        elif native == "turn.completed":
            kind, details = "turn_completed", {"usage": raw.get("usage", {})}
        elif native in {"turn.failed", "error"}:
            kind, details = "turn_failed", dict(raw)
        elif native in {"item.started", "item.completed"}:
            item = raw.get("item") or {}
            item_type = item.get("type", "")
            if item_type in {"command_execution", "command"}:
                kind = "tool_started" if native == "item.started" else "tool_completed"
                details = {"tool": "command", "tool_id": item.get("id", "command"),
                           "item": item}
            elif item_type == "agent_message" and native == "item.completed":
                kind, details = "agent_message", {"text": item.get("text", "")}
        return [NormalizedEvent(kind, native, details)] if kind else []

    def classify(self, *, process_exit: int, events: list[NormalizedEvent],
                 result: RoleResult | None) -> HarnessOutcome:
        if process_exit != 0:
            category = _category_from_events(events) or "harness_failure"
            return HarnessOutcome("turn_failed", category, f"process exit {process_exit}")
        kinds = [event.kind for event in events]
        if "session_started" not in kinds or "turn_started" not in kinds \
                or not kinds or kinds[-1] != "turn_completed":
            return HarnessOutcome("turn_failed", "protocol_failure", "missing terminal lifecycle")
        if any(event.kind == "turn_failed" for event in events):
            return HarnessOutcome("turn_failed", _category_from_events(events) or "upstream_failure")
        if result is None:
            return HarnessOutcome("turn_failed", "protocol_failure", "missing valid role result")
        return HarnessOutcome("success")


class ClaudePrintAdapter:
    name = "claude"
    capabilities = dict(CodexExecAdapter.capabilities)

    def build_argv(self, launch: HarnessLaunch) -> list[str]:
        argv = [launch.executable, "-p"]
        if launch.resume_session:
            argv += ["--resume", launch.resume_session]
        argv += [
            "--model", launch.model, "--effort", launch.reasoning_effort,
            "--output-format", "stream-json", "--verbose",
            "--json-schema", launch.schema_file.read_text(),
            "--permission-mode", ("bypassPermissions" if launch.sandbox == "danger-full-access"
                                  else "acceptEdits"),
            "--setting-sources", "project,local", *launch.extra_args,
        ]
        return argv

    def normalize(self, raw: dict[str, Any]) -> list[NormalizedEvent]:
        native = str(raw.get("type", ""))
        subtype = str(raw.get("subtype", ""))
        if native == "system" and subtype == "init":
            return [NormalizedEvent("session_started", "system/init", {
                "session_id": raw.get("session_id", ""), "model": raw.get("model", ""),
                "version": raw.get("claude_code_version", ""),
            })]
        if native == "system" and subtype == "api_retry":
            return [NormalizedEvent("provider_retrying", "system/api_retry", dict(raw))]
        if native == "assistant":
            details = dict(raw)
            events = [NormalizedEvent("agent_message", "assistant", details)]
            for block in (raw.get("message") or {}).get("content", ()):
                if block.get("type") == "tool_use":
                    events.append(NormalizedEvent("tool_started", "assistant/tool_use", {
                        "tool": block.get("name", ""), "tool_id": block.get("id", ""),
                        "item": block,
                    }))
            return events
        if native == "user":
            events = []
            for block in (raw.get("message") or {}).get("content", ()):
                if block.get("type") == "tool_result":
                    events.append(NormalizedEvent("tool_completed", "user/tool_result", {
                        "tool": block.get("tool_use_id", ""),
                        "tool_id": block.get("tool_use_id", ""), "item": block,
                    }))
            return events
        if native == "result":
            kind = "turn_failed" if raw.get("is_error") else "turn_completed"
            return [NormalizedEvent(kind, "result", dict(raw))]
        if native in {"tool_use", "tool_result"}:
            kind = "tool_started" if native == "tool_use" else "tool_completed"
            return [NormalizedEvent(kind, native, dict(raw))]
        return []

    def classify(self, *, process_exit: int, events: list[NormalizedEvent],
                 result: RoleResult | None) -> HarnessOutcome:
        category = _category_from_events(events)
        if process_exit != 0:
            return HarnessOutcome("turn_failed", category or "harness_failure",
                                  f"process exit {process_exit}")
        kinds = [event.kind for event in events]
        if "session_started" not in kinds or not kinds:
            return HarnessOutcome("turn_failed", "protocol_failure", "missing init/result")
        if category or kinds[-1] == "turn_failed":
            return HarnessOutcome("turn_failed", category or "upstream_failure")
        if kinds[-1] != "turn_completed" or result is None:
            return HarnessOutcome("turn_failed", "protocol_failure", "missing valid result")
        return HarnessOutcome("success")


def _category_from_events(events: list[NormalizedEvent]) -> str:
    text = json.dumps([event.details for event in events]).lower()
    if "authentication" in text or "http 401" in text or '"error_status": 401' in text:
        return "authentication_failure"
    if "quota" in text or "rate_limit" in text or "usage limit" in text:
        return "quota_failure"
    if any(event.kind == "turn_failed" for event in events):
        return "upstream_failure"
    return ""


def adapter_for(name: str) -> HarnessAdapter:
    if name == "codex":
        return CodexExecAdapter()
    if name == "claude":
        return ClaudePrintAdapter()
    raise ValueError(f"unsupported harness: {name}")


def preflight_harness(kind: str, executable: str) -> str:
    """Fail before dispatch if the configured CLI cannot honor the adapter contract."""
    resolved = shutil.which(executable) if "/" not in executable else executable
    if not resolved or not Path(resolved).is_file():
        raise RuntimeError(f"harness executable not found: {executable}")
    required = {
        "codex": ("--json", "--output-schema", "--output-last-message",
                  "--ignore-user-config", "--strict-config", "--sandbox"),
        "claude": ("--output-format", "--json-schema", "--setting-sources",
                   "--resume", "--permission-mode"),
    }[kind]
    help_result = subprocess.run(
        [resolved, "exec", "--help"] if kind == "codex" else [resolved, "--help"],
        text=True, capture_output=True, timeout=15, check=False,
    )
    help_text = help_result.stdout + help_result.stderr
    missing = [flag for flag in required if flag not in help_text]
    if help_result.returncode or missing:
        detail = f"missing flags: {', '.join(missing)}" if missing else help_text.strip()
        raise RuntimeError(f"{kind} harness preflight failed: {detail}")
    if kind == "codex":
        resume = subprocess.run(
            [resolved, "exec", "resume", "--help"], text=True, capture_output=True,
            timeout=15, check=False,
        )
        resume_text = resume.stdout + resume.stderr
        resume_required = ("--json", "--output-schema", "--output-last-message",
                           "--ignore-user-config", "--strict-config")
        missing_resume = [flag for flag in resume_required if flag not in resume_text]
        if resume.returncode or missing_resume:
            raise RuntimeError(
                f"codex resume preflight failed: missing flags: {', '.join(missing_resume)}"
            )
    version_result = subprocess.run(
        [resolved, "--version"], text=True, capture_output=True, timeout=15, check=False,
    )
    if version_result.returncode:
        raise RuntimeError(f"{kind} harness version check failed: {version_result.stderr.strip()}")
    return (version_result.stdout or version_result.stderr).strip()
