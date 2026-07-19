import json
import subprocess
from pathlib import Path

import pytest

from orchestra.harness import (
    ClaudePrintAdapter,
    CodexExecAdapter,
    HarnessLaunch,
    NormalizedEvent,
    RoleResult,
    _category_from_events,
    parse_role_result,
    role_contract_instruction,
    role_schema,
    preflight_authentication,
    preflight_harness,
)


FIXTURES = Path(__file__).parent / "fixtures" / "harness_protocols"


def test_claude_preflight_requires_safe_mode(monkeypatch, tmp_path: Path):
    executable = tmp_path / "claude"
    executable.write_text("")
    executable.chmod(0o755)

    def run(argv, **_kwargs):
        output = " ".join(("--output-format", "--json-schema", "--setting-sources",
                           "--resume", "--permission-mode"))
        if argv[-1] == "--version":
            output = "2.1.0"
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr("orchestra.harness.subprocess.run", run)
    with pytest.raises(RuntimeError, match="--safe-mode"):
        preflight_harness("claude", str(executable))


def test_codex_authentication_preflight_uses_isolated_environment(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "Logged in", "")

    monkeypatch.setattr("orchestra.harness.subprocess.run", run)
    preflight_authentication("codex", "codex", {"CODEX_HOME": "/isolated"})
    assert calls == [(["codex", "login", "status"], {
        "text": True, "capture_output": True, "timeout": 15, "check": False,
        "env": {"CODEX_HOME": "/isolated"},
    })]


def test_codex_authentication_preflight_fails_loud_without_credentials(monkeypatch):
    monkeypatch.setattr(
        "orchestra.harness.subprocess.run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "Not logged in", ""),
    )
    with pytest.raises(RuntimeError, match="authentication preflight failed"):
        preflight_authentication("codex", "codex", {})


def test_claude_authentication_preflight_uses_isolated_environment(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '{"loggedIn":true}', "")

    monkeypatch.setattr("orchestra.harness.subprocess.run", run)
    preflight_authentication("claude", "claude", {"CLAUDE_CONFIG_DIR": "/isolated"})
    assert calls[0][0] == ["claude", "auth", "status", "--json"]
    assert calls[0][1]["env"] == {"CLAUDE_CONFIG_DIR": "/isolated"}


def _events(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines()]


def test_worker_result_schema_is_strict_and_versioned():
    schema = role_schema("worker")
    assert "$schema" not in schema
    assert schema["additionalProperties"] is False
    assert schema["properties"]["outcome"]["enum"] == ["committed", "blocked"]
    assert set(schema["required"]) == {
        "schema_version", "outcome", "decisions", "failure_category",
        "evidence", "requires_human",
    }


@pytest.mark.parametrize(("role", "outcome"), [
    ("worker", "committed"),
    ("validator", "validated"),
    ("verifier", "accept"),
    ("verifier", "reject"),
])
def test_non_blocked_role_result_requires_empty_failure_category(role: str, outcome: str):
    data = {
        "schema_version": 1,
        "outcome": outcome,
        "decisions": "",
        "failure_category": "acceptance_failure",
        "evidence": "",
        "requires_human": True,
    }
    with pytest.raises(ValueError, match="non-blocked outcome requires empty failure_category"):
        parse_role_result(role, data)
    data["failure_category"] = ""
    data["requires_human"] = False
    assert parse_role_result(role, data).outcome == outcome


@pytest.mark.parametrize("role", ["worker", "validator", "verifier"])
def test_blocked_role_result_requires_stable_failure_category(role: str):
    data = {
        "schema_version": 1,
        "outcome": "blocked",
        "decisions": "",
        "failure_category": "",
        "evidence": "",
        "requires_human": False,
    }
    with pytest.raises(ValueError, match="blocked outcome requires a stable failure_category"):
        parse_role_result(role, data)
    data["failure_category"] = "time_limit"
    assert parse_role_result(role, data).outcome == "blocked"


def test_verifier_reject_is_a_valid_review_outcome_not_infrastructure_failure():
    result = parse_role_result("verifier", {
        "schema_version": 1, "outcome": "reject", "decisions": "fix the race",
        "failure_category": "", "evidence": "test demonstrates race",
        "requires_human": False,
    })
    assert result.outcome == "reject"


@pytest.mark.parametrize("role", ["worker", "validator", "verifier"])
def test_role_contract_instruction_is_generated_from_role_outcomes(role: str):
    instruction = role_contract_instruction(role)
    for outcome in role_schema(role)["properties"]["outcome"]["enum"]:
        assert outcome in instruction
    assert "Only blocked" in instruction
    assert "failure_category" in instruction


def test_codex_adapter_builds_reproducible_structured_command(tmp_path: Path):
    launch = HarnessLaunch(
        executable="codex", model="gpt-test", reasoning_effort="high",
        cwd=tmp_path, prompt_file=tmp_path / "prompt.md",
        schema_file=tmp_path / "schema.json", output_file=tmp_path / "provider.json",
        sandbox="danger-full-access", extra_args=(),
    )
    argv = CodexExecAdapter().build_argv(launch)
    assert argv[:2] == ["codex", "exec"]
    for flag in ("--json", "--ignore-user-config", "--strict-config", "--output-schema",
                 "--output-last-message", "--color"):
        assert flag in argv
    assert 'model_reasoning_effort="high"' in argv
    assert argv[argv.index("--disable") + 1] == "multi_agent"
    assert argv[-1] == "-"


@pytest.mark.parametrize(("delegation", "flag"), [
    ("disabled", "--disable"),
    ("required", "--enable"),
])
def test_codex_adapter_owns_delegation_feature(tmp_path: Path, delegation: str, flag: str):
    launch = HarnessLaunch(
        executable="codex", model="gpt-test", reasoning_effort="high",
        cwd=tmp_path, prompt_file=tmp_path / "prompt.md",
        schema_file=tmp_path / "schema.json", output_file=tmp_path / "provider.json",
        sandbox="danger-full-access", delegation=delegation,
    )
    argv = CodexExecAdapter().build_argv(launch)
    assert argv[argv.index(flag) + 1] == "multi_agent"
    opposite = "--enable" if flag == "--disable" else "--disable"
    assert opposite not in argv


def test_codex_allowed_delegation_does_not_override_feature(tmp_path: Path):
    launch = HarnessLaunch(
        executable="codex", model="gpt-test", reasoning_effort="high",
        cwd=tmp_path, prompt_file=tmp_path / "prompt.md",
        schema_file=tmp_path / "schema.json", output_file=tmp_path / "provider.json",
        sandbox="danger-full-access", delegation="allowed",
    )
    argv = CodexExecAdapter().build_argv(launch)
    assert "--enable" not in argv
    assert "--disable" not in argv


def test_codex_fixture_normalizes_complete_lifecycle():
    adapter = CodexExecAdapter()
    normalized = [event for raw in _events("codex-success.jsonl")
                  for event in adapter.normalize(raw)]
    assert [event.kind for event in normalized] == [
        "session_started", "turn_started", "agent_message", "turn_completed"
    ]
    assert normalized[0].details["session_id"] == "thread-fixture"


def test_stale_token_mid_run_classifies_transient_not_hard_auth_failure():
    # #010: the token-refresh-race messages from orchestra#009 must classify as the transient
    # `authentication_expired` (which requeues), distinct from a genuine `authentication_failure`.
    for message in ("auth token expired mid-run",
                    "verifier died in stale-token window",
                    "please reauthenticate: session expired"):
        events = [NormalizedEvent("turn_failed", "result", {"error": message})]
        assert _category_from_events(events) == "authentication_expired"


def test_invalid_credentials_still_classifies_as_hard_auth_failure():
    # A genuinely unauthenticated harness (invalid credentials / 401) stays a terminal block.
    events = [NormalizedEvent("turn_failed", "result", {
        "error": "Failed to authenticate. API Error: 401 Invalid authentication credentials",
    })]
    assert _category_from_events(events) == "authentication_failure"


def test_claude_optimistic_success_with_auth_error_is_failure():
    adapter = ClaudePrintAdapter()
    normalized = [event for raw in _events("claude-authentication-failure.jsonl")
                  for event in adapter.normalize(raw)]
    outcome = adapter.classify(process_exit=0, events=normalized, result=None)
    assert outcome.category == "authentication_failure"
    assert outcome.terminal == "turn_failed"


def test_claude_adapter_passes_configured_effort_and_schema_value(tmp_path: Path):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    launch = HarnessLaunch(
        executable="claude", model="haiku", reasoning_effort="high", cwd=tmp_path,
        prompt_file=tmp_path / "prompt", schema_file=schema,
        output_file=tmp_path / "provider", sandbox="danger-full-access",
    )
    argv = ClaudePrintAdapter().build_argv(launch)
    assert argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--json-schema") + 1] == schema.read_text()
    assert argv[argv.index("--disallowedTools") + 1] == "Agent"
    assert "--safe-mode" not in argv


def test_claude_allowed_delegation_does_not_disable_agent_tool(tmp_path: Path):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    launch = HarnessLaunch(
        executable="claude", model="haiku", reasoning_effort="high", cwd=tmp_path,
        prompt_file=tmp_path / "prompt", schema_file=schema,
        output_file=tmp_path / "provider", sandbox="danger-full-access",
        delegation="allowed",
    )
    assert "--disallowedTools" not in ClaudePrintAdapter().build_argv(launch)


def test_claude_explicit_bundle_uses_safe_mode(tmp_path: Path):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    launch = HarnessLaunch(
        executable="claude", model="haiku", reasoning_effort="high", cwd=tmp_path,
        prompt_file=tmp_path / "prompt", schema_file=schema,
        output_file=tmp_path / "provider", sandbox="danger-full-access",
        instruction_policy="explicit_bundle",
    )
    argv = ClaudePrintAdapter().build_argv(launch)
    assert "--safe-mode" in argv
    assert "--disable-slash-commands" in argv
    assert "--bare" not in argv


def test_codex_success_requires_terminal_lifecycle_and_valid_result():
    adapter = CodexExecAdapter()
    events = [event for raw in _events("codex-success.jsonl") for event in adapter.normalize(raw)]
    result = RoleResult(1, "committed", "", "", "commit abc", False)
    assert adapter.classify(process_exit=0, events=events, result=result).terminal == "success"
    assert adapter.classify(process_exit=0, events=events[:-1], result=result).category == "protocol_failure"
