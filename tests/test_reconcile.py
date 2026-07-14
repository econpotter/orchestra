import json
import subprocess
import time
from pathlib import Path

import pytest

from orchestra.config import load_config
from orchestra.dispatch import dispatch
from orchestra.queue import find_issue, read_queue
from orchestra.reconcile import reconcile
from orchestra.registry import load_registry
from orchestra.selection import pid_alive


FAKE = Path(__file__).parent / "fake_agent.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _setup(root: Path, *, status: str = "validated", network: bool = False) -> None:
    (root / "queue").mkdir()
    (root / "queue" / "wf.md").write_text(
        f"## #001 wf: task\nStatus: {status}\nPriority: 1\nPlan: null\nSpec: null\n"
        f"Depends On: null\nRetries: 0\nWorker: null\nNetwork: "
        f"{'true' if network else 'false'}\nAcceptance:\n- [ ] done\n"
        "### Decisions\n### Blocked Reason\n"
    )
    (root / "PROJECTS.md").write_text(
        "# Projects\n\n## wf\n- Path: projects/wf\n- Branch: main\n"
        "- Purpose: test\n- Queue: queue/wf.md\n- Focus: none\n"
    )
    (root / "prompts").mkdir()
    for name in ("validator.md", "worker.md", "verify-review.md"):
        (root / "prompts" / name).write_text("do {issue}\n")
    (root / "config.yaml").write_text(f"""\
slots: 1
roles:
  validator: {{ harness: fake, model: m, prompt: prompts/validator.md }}
  worker: {{ harness: fake, model: m, prompt: prompts/worker.md }}
  verifier: {{ harness: fake, model: m, prompt: prompts/verify-review.md }}
harnesses:
  fake:
    kind: codex
    executable: "{FAKE}"
    preflight: false
    attempts_cap: 2
sandbox: {{ enabled: false }}
hold_network_issues: false
""")
    repo = root / "projects" / "wf"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("test\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")


def _run(root: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> tuple[str, dict]:
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", mode)
    config = load_config(root / "config.yaml")
    assert dispatch(root, config, started="test") == ["wf#001"]
    deadline = time.time() + 10
    while time.time() < deadline:
        registry = load_registry(root / ".orchestra" / "workers.json")
        if all(not pid_alive(handle.pid) for handle in registry.values()):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("supervisor did not exit")
    handle = next(iter(registry.values()))
    reconcile(root, config)
    issue = find_issue(read_queue(root / "queue" / "wf.md"), 1)
    manifest = json.loads(Path(handle.manifest).read_text())
    return issue.status, manifest


def test_worker_commit_and_structured_result_advance(tmp_path: Path, monkeypatch):
    _setup(tmp_path)
    status, manifest = _run(tmp_path, monkeypatch, "commit")
    assert status == "committed"
    assert manifest["terminal_outcome"] == "success"


def test_worker_self_reported_block_never_claims_success(tmp_path: Path, monkeypatch):
    _setup(tmp_path)
    status, manifest = _run(tmp_path, monkeypatch, "blocked")
    assert status == "blocked"
    assert manifest["retry_disposition"] == "blocked"


def test_structured_quota_failure_resumes_same_session_with_bound(tmp_path: Path, monkeypatch):
    _setup(tmp_path, network=True)
    status, manifest = _run(tmp_path, monkeypatch, "session_limit")
    assert status == "validated"
    assert manifest["failure_category"] == "quota_failure"
    assert manifest["retry_disposition"] == "resume"
    assert manifest["session_id"] == "fake-thread"


def test_network_metadata_does_not_hold_by_default(tmp_path: Path):
    _setup(tmp_path, status="open", network=True)
    config = load_config(tmp_path / "config.yaml")
    reconcile(tmp_path, config)
    assert find_issue(read_queue(tmp_path / "queue" / "wf.md"), 1).status == "validated"


def test_recover_finalization_uses_durable_process_and_structured_evidence(tmp_path: Path):
    from orchestra.attempt import AttemptStore
    from orchestra.harness import CodexExecAdapter, role_schema
    from orchestra.reconcile import _recover_finalization
    from orchestra.supervisor import run_attempt

    fake = Path(__file__).parent / "fake_structured_harness.py"
    store = AttemptStore(tmp_path)
    attempt = store.create(
        attempt_id="recovery", project="wf", number=1, role="worker", harness="codex",
        model="m", worktree=tmp_path, branch="b", start_commit="a", prompt="p",
        instruction_bundle="", configuration={
            "executable": str(fake), "sandbox": "workspace-write", "limits": {},
        }, capabilities=CodexExecAdapter.capabilities, parent_attempt=None,
    )
    attempt.schema_path.write_text(json.dumps(role_schema("worker")))
    assert run_attempt(attempt.path) == 0
    store.update(attempt, state="running", terminal_outcome="", failure_category="")
    assert _recover_finalization(store, attempt) is True
    recovered = store.load("recovery")
    assert recovered.data["state"] == "completed"
    assert recovered.data["terminal_outcome"] == "success"
    assert recovered.data["recovered_finalization"] is True
