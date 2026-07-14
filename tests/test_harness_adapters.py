import json
from pathlib import Path

import pytest

from orchestra.harness import (
    ClaudePrintAdapter,
    CodexExecAdapter,
    HarnessLaunch,
    RoleResult,
    parse_role_result,
    role_schema,
)


FIXTURES = Path(__file__).parent / "fixtures" / "harness_protocols"


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


def test_role_result_rejects_invalid_or_contradictory_values():
    data = {
        "schema_version": 1,
        "outcome": "committed",
        "decisions": "",
        "failure_category": "time_limit",
        "evidence": "",
        "requires_human": False,
    }
    with pytest.raises(ValueError, match="successful outcome cannot have failure_category"):
        parse_role_result("worker", data)


def test_verifier_reject_is_a_valid_review_outcome_not_infrastructure_failure():
    result = parse_role_result("verifier", {
        "schema_version": 1, "outcome": "reject", "decisions": "fix the race",
        "failure_category": "", "evidence": "test demonstrates race",
        "requires_human": False,
    })
    assert result.outcome == "reject"


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
    assert argv[-1] == "-"


def test_codex_fixture_normalizes_complete_lifecycle():
    adapter = CodexExecAdapter()
    normalized = [event for raw in _events("codex-success.jsonl")
                  for event in adapter.normalize(raw)]
    assert [event.kind for event in normalized] == [
        "session_started", "turn_started", "agent_message", "turn_completed"
    ]
    assert normalized[0].details["session_id"] == "thread-fixture"


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


def test_codex_success_requires_terminal_lifecycle_and_valid_result():
    adapter = CodexExecAdapter()
    events = [event for raw in _events("codex-success.jsonl") for event in adapter.normalize(raw)]
    result = RoleResult(1, "committed", "", "", "commit abc", False)
    assert adapter.classify(process_exit=0, events=events, result=result).terminal == "success"
    assert adapter.classify(process_exit=0, events=events[:-1], result=result).category == "protocol_failure"
