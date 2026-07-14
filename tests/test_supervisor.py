import json
from pathlib import Path

from orchestra.attempt import AttemptStore
from orchestra.harness import CodexExecAdapter, role_schema
from orchestra.supervisor import run_attempt


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
            "limits": {"wall_seconds": 0, "idle_seconds": 0,
                       "active_tool_seconds": 0, "grace_seconds": 1},
        },
        capabilities=CodexExecAdapter.capabilities, parent_attempt=None,
    )
    attempt.schema_path.write_text(json.dumps(role_schema("worker")))
    return store, attempt


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
