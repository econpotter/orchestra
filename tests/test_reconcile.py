import os
import subprocess
import sys
import time
from pathlib import Path

from orchestra.config import load_config
from orchestra.dispatch import dispatch
from orchestra.queue import find_issue, read_queue
from orchestra.reconcile import reconcile
from orchestra.registry import issue_key, load_registry
from orchestra.selection import pid_alive

REPO_ROOT = Path(__file__).resolve().parent.parent
FAKE = REPO_ROOT / "tests" / "fake_agent.py"

CONFIG = f"""\
slots: 5
retries_cap: 1
roles:
  validator: {{ provider: fake, model: m, prompt: prompts/validator.md }}
  worker:    {{ provider: fake, model: m, prompt: prompts/worker.md }}
  verifier:  {{ provider: fake, model: m, prompt: prompts/verify-review.md }}
providers:
  fake:
    argv: ["{sys.executable}", "{FAKE}", "--role", "{{role}}", "--result-file", "{{result_file}}"]
    prompt: stdin
sandbox: {{ enabled: true, argv_prefix: [] }}
validate:
  semantic: true
"""

PROJECTS = """\
# Projects

## wf
- Path: projects/wf
- Branch: main
- Purpose: test
- Queue: queue/wf.md
- Focus: none
"""


def _issue(num, status, priority=5, retries=0, crash_retries=0, network=False, blocked_reason=""):
    return (
        f"## #{num:03d} wf: t\nStatus: {status}\nPriority: {priority}\n"
        f"Plan: null\nSpec: docs/specs/x.md\nDepends On: null\n"
        f"Network: {'true' if network else 'false'}\n"
        f"Retries: {retries}\nCrash-Retries: {crash_retries}\nWorker: null\nAcceptance:\n- [ ] do it\n"
        f"### Decisions\n### Blocked Reason\n{blocked_reason + chr(10) if blocked_reason else ''}"
    )


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _setup(root: Path, issues_text: str, *, config_text: str | None = None):
    (root / "queue").mkdir(parents=True)
    (root / "queue" / "wf.md").write_text(issues_text)
    (root / "PROJECTS.md").write_text(PROJECTS)
    (root / "config.yaml").write_text(config_text or CONFIG)
    (root / "prompts").mkdir()
    for name in ("validator.md", "worker.md", "verify-review.md"):
        (root / "prompts" / name).write_text("do {issue}\n")
    (root / "projects" / "wf" / "docs" / "specs").mkdir(parents=True)
    (root / "projects" / "wf" / "docs" / "specs" / "x.md").write_text("spec")
    repo = root / "projects" / "wf"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("x\n")
    _git(repo, "add", "-A")  # commit specs/plans too, so base-branch validation sees them
    _git(repo, "commit", "-m", "init")


def _wait_all_dead(root: Path, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        reg = load_registry(root / ".orchestra" / "workers.json")
        if all(not pid_alive(h.pid) for h in reg.values()):
            return
        time.sleep(0.1)
    raise AssertionError("agents still alive")


def _status(root: Path, num: int) -> str:
    return find_issue(read_queue(root / "queue" / "wf.md"), num).status


def _archived_status(root: Path, num: int) -> str | None:
    qf = root / "queue" / "archive" / "wf.md"
    if not qf.exists():
        return None
    issue = find_issue(read_queue(qf), num)
    return issue.status if issue else None


def test_validator_pass_sets_validated(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "validated")
    _setup(tmp_path, _issue(1, "open"))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    assert _status(tmp_path, 1) == "validated"
    assert load_registry(tmp_path / ".orchestra" / "workers.json") == {}


def test_worker_commit_sets_committed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "commit")
    _setup(tmp_path, _issue(2, "validated"))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    assert _status(tmp_path, 2) == "committed"


def test_worker_commit_clears_stale_blocked_reason(tmp_path: Path, monkeypatch):
    # An issue re-run after a prior crash carries a stale blocked_reason; committing must
    # clear it so a committed issue never shows a phantom block.
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "commit")
    _setup(tmp_path, _issue(2, "validated", blocked_reason="crash: no new commit and no result"))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 2)
    assert issue.status == "committed"
    assert issue.blocked_reason == ""


def test_worker_transient_crash_requeues_under_cap(tmp_path: Path, monkeypatch, capsys):
    # A configured provider limit message in the worker's log classifies a no-result crash
    # as transient, so it returns to validated and records the bounded retry.
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "session_limit")
    _setup(tmp_path, _issue(3, "validated"))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 3)
    assert issue.status == "validated"
    assert issue.crash_retries == 1
    assert "classified transient" in capsys.readouterr().err


def test_worker_transient_crash_at_cap_blocks(tmp_path: Path, monkeypatch):
    # Already at the default cap (2): a matching transient crash blocks loudly with the
    # classification and retry count instead of looping forever.
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "session_limit")
    _setup(tmp_path, _issue(3, "validated", crash_retries=2))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 3)
    assert issue.status == "blocked"
    assert "transient error" in issue.blocked_reason
    assert "2/2" in issue.blocked_reason


def test_worker_nonmatching_crash_blocks_unchanged(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "crash")
    _setup(tmp_path, _issue(3, "validated"))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 3)
    assert issue.status == "blocked"
    assert issue.crash_retries == 0
    assert issue.blocked_reason == "crash: no new commit and no result"


def test_worker_plain_crash_after_transient_crash_blocks(tmp_path: Path, monkeypatch):
    """Only the current worker attempt's log may classify a crash as transient."""
    _setup(tmp_path, _issue(3, "validated"))
    cfg = load_config(tmp_path / "config.yaml")

    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "session_limit")
    dispatch(tmp_path, cfg, started="first")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    assert _status(tmp_path, 3) == "validated"

    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "crash")
    dispatch(tmp_path, cfg, started="second")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 3)
    assert issue.status == "blocked"
    assert issue.blocked_reason == "crash: no new commit and no result"


def test_worker_rework_transient_crash_requeues_to_validated(tmp_path: Path, monkeypatch):
    # A classified transient rework crash returns to validated for another worker attempt.
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "session_limit")
    _setup(tmp_path, _issue(3, "needs_rework", retries=1))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 3)
    assert issue.status == "validated"
    assert issue.crash_retries == 1


def test_worker_selfblock_stays_blocked(tmp_path: Path, monkeypatch):
    # a self-reported block writes a result — it is a real verdict, never crash-retried.
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "blocked")
    _setup(tmp_path, _issue(3, "validated"))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 3)
    assert issue.status == "blocked"
    assert issue.crash_retries == 0
    assert "stuck: fake" in issue.blocked_reason


def test_verifier_accept_sets_awaiting_review(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "accept")
    _setup(tmp_path, _issue(4, "committed"))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    assert _status(tmp_path, 4) == "awaiting_review"


def test_verifier_accept_autoapproves_when_enabled(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "accept")
    _setup(tmp_path, _issue(4, "committed"), config_text=CONFIG + "review:\n  autoapprove: true\n")
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    assert find_issue(read_queue(tmp_path / "queue" / "wf.md"), 4) is None
    assert _archived_status(tmp_path, 4) == "archived"


def test_verifier_reject_under_cap_sets_needs_rework(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "reject")
    _setup(tmp_path, _issue(5, "committed", retries=0))  # cap is 1
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 5)
    assert issue.status == "needs_rework"
    assert issue.retries == 1
    assert "fake complaint" in issue.verifier_feedback


def test_verifier_reject_at_cap_escalates(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "reject")
    _setup(tmp_path, _issue(6, "committed", retries=1))  # already at cap=1
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    assert _status(tmp_path, 6) == "awaiting_review"


def test_verifier_reject_at_cap_autoapproves_when_enabled(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "reject")
    _setup(tmp_path, _issue(6, "committed", retries=1), config_text=CONFIG + "review:\n  autoapprove: true\n")
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    assert find_issue(read_queue(tmp_path / "queue" / "wf.md"), 6) is None
    assert _archived_status(tmp_path, 6) == "archived"


def test_live_worker_stamped_in_progress(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "sleep")
    _setup(tmp_path, _issue(7, "validated"))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    reconcile(tmp_path, cfg)  # worker still sleeping
    assert _status(tmp_path, 7) == "in_progress"
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)  # now exited with a commit
    assert _status(tmp_path, 7) == "committed"


def test_completion_marker_beats_invisible_pid(tmp_path: Path):
    """A worker launched in another PID namespace stays active until its wrapper marks exit."""
    from orchestra.registry import WorkerHandle, save_registry

    _setup(tmp_path, _issue(8, "validated"))
    cfg = load_config(tmp_path / "config.yaml")
    done = tmp_path / ".orchestra" / "results" / "wf#008.exit.json"
    key = issue_key("wf", 8)
    save_registry(tmp_path / ".orchestra" / "workers.json", {
        key: WorkerHandle(
            project="wf", number=8, role="worker", branch="issue/008-x",
            worktree=str(tmp_path), pid=2_000_000_000, log=str(tmp_path / "l.log"),
            result_file=str(tmp_path / ".orchestra" / "results" / "wf#008.json"),
            started="t", start_sha="", proc_start="", completion_file=str(done),
            stop_file=str(done.with_suffix(".stop")),
        )
    })

    reconcile(tmp_path, cfg)

    assert _status(tmp_path, 8) == "in_progress"
    assert key in load_registry(tmp_path / ".orchestra" / "workers.json")


def test_rework_crash_not_classified_committed(tmp_path: Path, monkeypatch):
    """Regression: a rework worker that crashes (no NEW commit) must NOT be classified as
    committed just because the prior round's commit is on the branch. It crash-retries to
    needs_rework instead."""
    # Tick 1: worker commits → committed
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "commit")
    _setup(tmp_path, _issue(9, "validated"))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t1")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    assert _status(tmp_path, 9) == "committed"

    # Tick 2: verifier rejects → needs_rework (retries=1, still under cap=1)
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "reject")
    dispatch(tmp_path, cfg, started="t2")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 9)
    assert issue.status == "needs_rework"
    assert issue.retries == 1

    # Tick 3: a session-limit crash (no new commit) → validated, NOT committed.
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "session_limit")
    dispatch(tmp_path, cfg, started="t3")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 9)
    assert issue.status == "validated"  # prior commit must not fool reconcile into committed
    assert issue.crash_retries == 1


def test_verifier_crash_requeues_to_committed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "crash")
    _setup(tmp_path, _issue(4, "committed"))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 4)
    assert issue.status == "committed"  # re-verify the intact committed diff
    assert issue.crash_retries == 1


def test_verifier_crash_at_cap_blocks(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "crash")
    _setup(tmp_path, _issue(4, "committed", crash_retries=2))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 4)
    assert issue.status == "blocked"
    assert "crash: verifier produced no result" in issue.blocked_reason


def test_verifier_reject_resets_crash_retries(tmp_path: Path, monkeypatch):
    # a real verdict (reject) resets the crash counter — the cap bounds a crash loop only.
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "reject")
    _setup(tmp_path, _issue(5, "committed", retries=0, crash_retries=1))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 5)
    assert issue.status == "needs_rework"
    assert issue.crash_retries == 0


def test_validator_crash_requeues_to_open(tmp_path: Path):
    # A validator handle whose pid is dead with no result = crash → re-queue to open.
    from orchestra.registry import WorkerHandle, save_registry
    _setup(tmp_path, _issue(8, "open"))
    cfg = load_config(tmp_path / "config.yaml")
    key = issue_key("wf", 8)
    save_registry(tmp_path / ".orchestra" / "workers.json", {
        key: WorkerHandle(
            project="wf", number=8, role="validator",
            branch="issue/008-x", worktree=str(tmp_path),
            pid=os.getpid(), log=str(tmp_path / "l.log"),
            result_file=str(tmp_path / ".orchestra" / "results" / "wf#008.json"),
            started="t", start_sha="", proc_start="0",  # recycled pid → treated as exited
        )
    })
    reconcile(tmp_path, cfg)
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 8)
    assert issue.status == "open"
    assert issue.crash_retries == 1


def test_network_issue_validates_by_default_via_validator(tmp_path: Path, monkeypatch):
    # Network metadata is advisory by default: semantic acceptance is dispatchable.
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "validated")
    _setup(tmp_path, _issue(1, "open", network=True))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    assert _status(tmp_path, 1) == "validated"


def test_network_issue_validates_by_default_deterministic(tmp_path: Path):
    # Deterministic validation also treats Network as advisory by default.
    _setup(tmp_path, _issue(1, "open", network=True))
    p = tmp_path / "config.yaml"
    p.write_text(p.read_text().replace("semantic: true", "semantic: false"))
    cfg = load_config(p)
    assert dispatch(tmp_path, cfg, started="t") == []
    reconcile(tmp_path, cfg)
    assert _status(tmp_path, 1) == "validated"


def test_network_worker_crash_requeues_by_default(tmp_path: Path, monkeypatch):
    # A transient worker crash requeues normally when Network is advisory.
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "session_limit")
    _setup(tmp_path, _issue(1, "validated", network=True))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    assert _status(tmp_path, 1) == "validated"


def test_network_hold_remains_available_as_opt_in(tmp_path: Path):
    config = CONFIG.replace("semantic: true", "semantic: false")
    _setup(
        tmp_path,
        _issue(1, "open", network=True),
        config_text=config + "hold_network_issues: true\n",
    )
    cfg = load_config(tmp_path / "config.yaml")
    reconcile(tmp_path, cfg)
    assert _status(tmp_path, 1) == "held"
    assert dispatch(tmp_path, cfg, started="t2") == []


def test_network_hold_applies_to_semantic_validator_acceptance(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "validated")
    _setup(
        tmp_path,
        _issue(1, "open", network=True),
        config_text=CONFIG + "hold_network_issues: true\n",
    )
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    assert _status(tmp_path, 1) == "held"


def test_network_hold_applies_to_transient_worker_retry(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_FAKE_MODE", "session_limit")
    _setup(
        tmp_path,
        _issue(1, "validated", network=True),
        config_text=CONFIG + "hold_network_issues: true\n",
    )
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)
    reconcile(tmp_path, cfg)
    assert _status(tmp_path, 1) == "held"


def test_disabling_network_hold_releases_existing_held_issue(tmp_path: Path):
    _setup(tmp_path, _issue(1, "held", network=True))
    cfg = load_config(tmp_path / "config.yaml")
    transitions = reconcile(tmp_path, cfg)
    assert _status(tmp_path, 1) == "validated"
    assert transitions == [("wf#001", "validated")]


def test_reused_pid_is_treated_as_exited(tmp_path: Path):
    import os
    from orchestra.registry import WorkerHandle, issue_key, save_registry
    _setup(tmp_path, _issue(8, "open"))
    cfg = load_config(tmp_path / "config.yaml")
    # A handle whose pid is alive (this test process) but whose recorded
    # proc_start does NOT match -> a recycled pid; reconcile must treat it as
    # exited. role=validator with no result file = crash -> crash-retry to open.
    key = issue_key("wf", 8)
    save_registry(tmp_path / ".orchestra" / "workers.json", {
        key: WorkerHandle(
            project="wf", number=8, role="validator",
            branch="issue/008-x", worktree=str(tmp_path),
            pid=os.getpid(), log=str(tmp_path / "l.log"),
            result_file=str(tmp_path / ".orchestra" / "results" / "wf#008.json"),
            started="t", start_sha="", proc_start="0",  # deliberately wrong
        )
    })
    reconcile(tmp_path, cfg)
    assert _status(tmp_path, 8) == "open"  # crash-retry re-queues the validator
    assert load_registry(tmp_path / ".orchestra" / "workers.json") == {}


def test_reconcile_blocks_structurally_invalid_open(tmp_path: Path):
    issue = _issue(1, "open").replace("Spec: docs/specs/x.md", "Spec: docs/specs/missing.md")
    _setup(tmp_path, issue)
    cfg = load_config(tmp_path / "config.yaml")
    reconcile(tmp_path, cfg)
    assert _status(tmp_path, 1) == "blocked"


def test_reconcile_blocks_unknown_workflow(tmp_path: Path):
    """Config defines workflows; project uses an unknown workflow -> blocked with reason."""
    config_text = f"""\
slots: 5
retries_cap: 1
workflows:
  python:
    test: uv run pytest
roles:
  validator: {{ provider: fake, model: m, prompt: prompts/validator.md }}
  worker:    {{ provider: fake, model: m, prompt: prompts/worker.md }}
  verifier:  {{ provider: fake, model: m, prompt: prompts/verify-review.md }}
providers:
  fake:
    argv: ["{sys.executable}", "{FAKE}", "--role", "{{role}}", "--result-file", "{{result_file}}"]
    prompt: stdin
sandbox: {{ enabled: true, argv_prefix: [] }}
validate:
  semantic: true
"""
    projects_text = """\
# Projects

## wf
- Path: projects/wf
- Branch: main
- Purpose: test
- Queue: queue/wf.md
- Focus: none
- Workflow: bogus
"""
    issue_text = (
        "## #001 wf: t\nStatus: open\nPriority: 5\n"
        "Plan: null\nSpec: null\nDepends On: null\n"
        "Retries: 0\nWorker: null\nAcceptance:\n- [ ] do it\n"
        "### Decisions\n### Blocked Reason\n"
    )
    (tmp_path / "queue").mkdir(parents=True)
    (tmp_path / "queue" / "wf.md").write_text(issue_text)
    (tmp_path / "PROJECTS.md").write_text(projects_text)
    (tmp_path / "config.yaml").write_text(config_text)
    (tmp_path / "prompts").mkdir()
    for name in ("validator.md", "worker.md", "verify-review.md"):
        (tmp_path / "prompts" / name).write_text("do {issue}\n")
    (tmp_path / "projects" / "wf").mkdir(parents=True)

    cfg = load_config(tmp_path / "config.yaml")
    reconcile(tmp_path, cfg)

    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 1)
    assert issue.status == "blocked"
    assert "unknown workflow 'bogus'" in issue.blocked_reason


def test_deterministic_promotion_no_agent(tmp_path: Path):
    """semantic off (default): reconcile promotes a valid open issue to validated with
    NO validator agent launched."""
    _setup(tmp_path, _issue(1, "open"))
    p = tmp_path / "config.yaml"
    p.write_text(p.read_text().replace("semantic: true", "semantic: false"))
    cfg = load_config(p)
    launched = dispatch(tmp_path, cfg, started="t")
    assert launched == []  # no validator agent for open issues
    assert load_registry(tmp_path / ".orchestra" / "workers.json") == {}
    reconcile(tmp_path, cfg)
    assert _status(tmp_path, 1) == "validated"


def test_deterministic_blocks_spec_not_in_base_branch(tmp_path: Path):
    """semantic off: an issue whose Spec isn't committed in the base branch is blocked
    (even though _setup leaves it on disk) — the base-branch hardening."""
    issue = _issue(1, "open").replace("Spec: docs/specs/x.md", "Spec: docs/specs/missing.md")
    _setup(tmp_path, issue)
    p = tmp_path / "config.yaml"
    p.write_text(p.read_text().replace("semantic: true", "semantic: false"))
    cfg = load_config(p)
    reconcile(tmp_path, cfg)
    it = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 1)
    assert it.status == "blocked"
    assert "base branch" in it.blocked_reason


def test_dependency_cycle_blocks_both(tmp_path: Path):
    """Two open issues that depend on each other can never satisfy their deps, so they would
    sit at `validated` forever (never dispatchable). reconcile must block both, naming the
    cycle in the reason instead of silently stalling."""
    i1 = _issue(1, "open").replace("Depends On: null", "Depends On: 2")
    i2 = _issue(2, "open").replace("Depends On: null", "Depends On: 1")
    _setup(tmp_path, i1 + i2)
    cfg = load_config(tmp_path / "config.yaml")
    reconcile(tmp_path, cfg)
    for n in (1, 2):
        it = find_issue(read_queue(tmp_path / "queue" / "wf.md"), n)
        assert it.status == "blocked"
        assert "dependency cycle" in it.blocked_reason


def test_self_dependency_blocks_at_reconcile(tmp_path: Path):
    """A self-dependency (#1 depends on #1) is the degenerate cycle — blocked, not stuck."""
    _setup(tmp_path, _issue(1, "open").replace("Depends On: null", "Depends On: 1"))
    cfg = load_config(tmp_path / "config.yaml")
    reconcile(tmp_path, cfg)
    it = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 1)
    assert it.status == "blocked"
    assert "self-dependency: #1 depends on itself" in it.blocked_reason


AUTOAPPROVE = CONFIG + "review:\n  autoapprove: true\n"


def test_autoapprove_merge_failure_blocks_with_reason_and_retries(tmp_path: Path, monkeypatch):
    """issue #006: a merge failure must leave the issue blocked with a NON-EMPTY reason
    (never a silent/blank block), and must be retried once before giving up. Uses OSError —
    the actual tmpfs-quota failure type — to prove a non-CalledProcessError is handled too."""
    import orchestra.reconcile as rec
    _setup(tmp_path, _issue(9, "awaiting_review"), config_text=AUTOAPPROVE)
    cfg = load_config(tmp_path / "config.yaml")
    calls: list = []

    def boom(root, project, number, **kw):
        calls.append(kw.get("tmpdir"))
        raise OSError("could not write to /tmp/orchestra-merge-x: Disk quota exceeded")

    monkeypatch.setattr(rec, "merge_and_archive", boom)
    reconcile(tmp_path, cfg)

    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 9)
    assert issue.status == "blocked"
    assert issue.blocked_reason.strip() != ""  # empty-reason blocking is impossible
    assert "Disk quota exceeded" in issue.blocked_reason
    assert len(calls) == 2  # first attempt + one retry, then block
    assert calls == [None, None]  # no merge.tmpdir configured -> default (repo parent)


def test_autoapprove_merge_transient_failure_recovers_on_retry(tmp_path: Path, monkeypatch):
    """A merge that fails once then succeeds is NOT blocked — the retry recovers it."""
    import orchestra.reconcile as rec
    _setup(tmp_path, _issue(10, "awaiting_review"), config_text=AUTOAPPROVE)
    cfg = load_config(tmp_path / "config.yaml")
    n = {"c": 0}

    def flaky(root, project, number, **kw):
        n["c"] += 1
        if n["c"] == 1:
            raise OSError("Disk quota exceeded")  # transient
        return "archived"

    monkeypatch.setattr(rec, "merge_and_archive", flaky)
    transitions = reconcile(tmp_path, cfg)

    assert n["c"] == 2
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 10)
    assert issue.status == "awaiting_review"  # not blocked
    assert (issue_key("wf", 10), "archived") in transitions


def test_autoapprove_passes_configured_merge_tmpdir(tmp_path: Path, monkeypatch):
    """A configured merge.tmpdir is resolved against root and threaded into the merge."""
    import orchestra.reconcile as rec
    cfg_text = AUTOAPPROVE + "merge:\n  tmpdir: mergetmp\n"
    _setup(tmp_path, _issue(11, "awaiting_review"), config_text=cfg_text)
    cfg = load_config(tmp_path / "config.yaml")
    seen: list = []

    def capture(root, project, number, **kw):
        seen.append(kw.get("tmpdir"))
        return "archived"

    monkeypatch.setattr(rec, "merge_and_archive", capture)
    reconcile(tmp_path, cfg)

    assert seen == [tmp_path / "mergetmp"]
