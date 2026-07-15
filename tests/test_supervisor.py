import json
from pathlib import Path

import pytest

from orchestra.attempt import AttemptStore
from orchestra.harness import CodexExecAdapter, role_schema
from orchestra.supervisor import compose_harness_prompt, run_attempt


FAKE = Path(__file__).parent / "fake_structured_harness.py"


def _attempt(tmp_path: Path):
    FAKE.chmod(0o755)
    store = AttemptStore(tmp_path)
    attempt = store.create(
        attempt_id="a1", project="wf", number=1, role="worker", harness="codex",
        model="m", worktree=tmp_path, branch="b", start_commit="abc", prompt="do it",
        instruction_bundle="rules",
        configuration={
            "executable": str(FAKE), "reasoning_effort": "high",
            "sandbox": "danger-full-access", "extra_args": [],
            "instruction_policy": "explicit_bundle",
            "limits": {"wall_seconds": 0, "idle_seconds": 0,
                       "active_tool_seconds": 0, "grace_seconds": 1},
        },
        capabilities=CodexExecAdapter.capabilities, parent_attempt=None,
    )
    attempt.schema_path.write_text(json.dumps(role_schema("worker")))
    return store, attempt


def test_native_project_prompt_excludes_instruction_bundle_and_schema_dump():
    prompt = compose_harness_prompt(
        "do it", "PROJECT SENTINEL", "native_project", "verifier"
    )
    assert "PROJECT SENTINEL" not in prompt
    assert '"schema_version"' not in prompt
    assert prompt.count("Valid verifier outcomes: accept, reject, blocked.") == 1


def test_explicit_bundle_prompt_includes_instructions_and_contract_exactly_once():
    prompt = compose_harness_prompt(
        "do it", "PROJECT SENTINEL", "explicit_bundle", "worker"
    )
    assert prompt.count("PROJECT SENTINEL") == 1
    assert prompt.count("Valid worker outcomes: committed, blocked.") == 1
    assert '"properties"' not in prompt


def test_prompt_composition_rejects_unknown_instruction_policy():
    with pytest.raises(ValueError, match="unknown instruction policy"):
        compose_harness_prompt("do it", "rules", "ambient", "worker")


def test_preflight_authentication_failure_keeps_specific_category(tmp_path: Path):
    store, attempt = _attempt(tmp_path)
    store.update(
        attempt,
        preflight_error="isolated Codex is not authenticated",
        preflight_error_category="authentication_failure",
    )
    assert run_attempt(attempt.path) == 0
    loaded = store.load("a1")
    assert loaded.data["failure_category"] == "authentication_failure"


def test_supervisor_separates_streams_and_writes_canonical_result(tmp_path: Path):
    store, attempt = _attempt(tmp_path)
    store.update(attempt, harness_version="codex-cli 1.2.3")
    assert run_attempt(attempt.path) == 0
    loaded = store.load("a1")
    assert loaded.data["state"] == "completed"
    assert loaded.data["terminal_outcome"] == "success"
    assert "thread.started" in loaded.stdout_path.read_text()
    assert "fake diagnostic" in loaded.stderr_path.read_text()
    assert "fake diagnostic" not in loaded.stdout_path.read_text()
    result = json.loads(loaded.canonical_result_path.read_text())
    assert result["outcome"] == "committed"
    assert loaded.data["session_id"] == "fake-thread"
    assert loaded.data["harness_version"] == "codex-cli 1.2.3"
    assert len(loaded.data["harness_launch_sha256"]) == 64
    assert json.loads(loaded.process_path.read_text())["process_exit"] == 0


def test_supervisor_retains_malformed_stdout_and_fails_protocol(tmp_path: Path, monkeypatch):
    store, attempt = _attempt(tmp_path)
    monkeypatch.setenv("ORCHESTRA_FAKE_STRUCTURED_MODE", "malformed")
    assert run_attempt(attempt.path) == 0
    loaded = store.load("a1")
    assert loaded.data["terminal_outcome"] == "turn_failed"
    assert loaded.data["failure_category"] == "protocol_failure"
    assert loaded.stdout_path.read_text() == "not-json\n"
    assert not loaded.canonical_result_path.exists()


def test_supervisor_wall_limit_cancels_process_group(tmp_path: Path, monkeypatch):
    store, attempt = _attempt(tmp_path)
    attempt.data["configuration"]["limits"]["wall_seconds"] = 1
    store.update(attempt, configuration=attempt.data["configuration"])
    monkeypatch.setenv("ORCHESTRA_FAKE_STRUCTURED_MODE", "sleep")
    assert run_attempt(attempt.path) == 0
    loaded = store.load("a1")
    assert loaded.data["failure_category"] == "time_limit"
    assert loaded.data["limit_triggered"] == "wall_seconds"


def test_supervisor_fails_loud_when_tool_never_completes(tmp_path: Path, monkeypatch):
    store, attempt = _attempt(tmp_path)
    monkeypatch.setenv("ORCHESTRA_FAKE_STRUCTURED_MODE", "dangling_tool")
    assert run_attempt(attempt.path) == 0
    loaded = store.load("a1")
    assert loaded.data["failure_category"] == "tool_observation_failure"


def test_quiet_active_tool_is_not_an_idle_stall(tmp_path: Path, monkeypatch):
    store, attempt = _attempt(tmp_path)
    attempt.data["configuration"]["limits"]["idle_seconds"] = 1
    store.update(attempt, configuration=attempt.data["configuration"])
    monkeypatch.setenv("ORCHESTRA_FAKE_STRUCTURED_MODE", "tool_sleep")
    assert run_attempt(attempt.path) == 0
    loaded = store.load("a1")
    assert loaded.data["terminal_outcome"] == "success"
    assert loaded.data["limit_triggered"] == ""
