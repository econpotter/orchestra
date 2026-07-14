import json
from pathlib import Path

from orchestra.attempt import AttemptStore


def test_attempt_manifest_exists_before_launch_and_paths_are_distinct(tmp_path: Path):
    store = AttemptStore(tmp_path)
    manifest = store.create(
        attempt_id="attempt-1", project="wf", number=7, role="worker",
        harness="codex", model="gpt-test", worktree=tmp_path / "wt",
        branch="issue/007-x", start_commit="abc", prompt="do work",
        instruction_bundle="rules", configuration={"reasoning_effort": "high"},
        capabilities={"structured_events": True}, parent_attempt=None,
    )
    assert manifest.path.is_file()
    persisted = json.loads(manifest.path.read_text())
    assert persisted["state"] == "created"
    assert manifest.stdout_path != manifest.stderr_path
    assert manifest.provider_output_path != manifest.canonical_result_path
    assert persisted["prompt_sha256"] != persisted["instruction_sha256"]


def test_attempt_updates_manifest_atomically_and_retains_events(tmp_path: Path):
    store = AttemptStore(tmp_path)
    manifest = store.create(
        attempt_id="attempt-1", project="wf", number=7, role="worker",
        harness="codex", model="m", worktree=tmp_path, branch="b",
        start_commit="abc", prompt="p", instruction_bundle="i", configuration={},
        capabilities={}, parent_attempt=None,
    )
    store.append_event(manifest, {"kind": "turn_started", "offset": 0})
    store.update(manifest, state="running", pid=123, session_id="thread-1")
    loaded = store.load("attempt-1")
    assert loaded.data["state"] == "running"
    assert loaded.data["pid"] == 123
    assert json.loads(loaded.events_path.read_text().strip())["kind"] == "turn_started"
    assert not manifest.path.with_suffix(".json.tmp").exists()


def test_attempt_parent_links_resume_without_mutating_parent(tmp_path: Path):
    store = AttemptStore(tmp_path)
    common = dict(project="wf", number=7, role="worker", harness="codex", model="m",
                  worktree=tmp_path, branch="b", start_commit="abc", prompt="p",
                  instruction_bundle="i", configuration={}, capabilities={})
    store.create(attempt_id="a1", parent_attempt=None, **common)
    child = store.create(attempt_id="a2", parent_attempt="a1", **common)
    assert child.data["parent_attempt"] == "a1"
    assert store.load("a1").data["parent_attempt"] is None
