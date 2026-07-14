import subprocess
import sys
import time
import json

import pytest
from pathlib import Path

from orchestra.config import load_config
from orchestra.dispatch import dispatch
from orchestra.registry import issue_key, load_registry
from orchestra.selection import pid_alive

REPO_ROOT = Path(__file__).resolve().parent.parent
FAKE = REPO_ROOT / "tests" / "fake_agent.py"


@pytest.fixture(autouse=True)
def _launch_supervisors_without_host_systemd(monkeypatch):
    """Dispatch unit tests do not depend on a user systemd manager."""
    import orchestra.dispatch as dispatch_module

    def launch(root, attempt, _config):
        process = subprocess.Popen(
            [sys.executable, "-m", "orchestra.supervisor", str(attempt.path)],
            cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, text=True,
        )
        return process.pid, ""

    monkeypatch.setattr(dispatch_module, "_start_supervisor", launch)

CONFIG = f"""\
slots: 5
retries_cap: 2
roles:
  validator: {{ harness: fake, model: m, prompt: prompts/validator.md }}
  worker:    {{ harness: fake, model: m, prompt: prompts/worker.md }}
  verifier:  {{ harness: fake, model: m, prompt: prompts/verify-review.md }}
harnesses:
  fake:
    kind: codex
    executable: "{FAKE}"
    preflight: false
sandbox:
  enabled: true
  kind: systemd
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


def _issue(num, status, priority=5):
    return (
        f"## #{num:03d} wf: t\nStatus: {status}\nPriority: {priority}\n"
        f"Plan: null\nSpec: docs/specs/x.md\nDepends On: null\n"
        f"Retries: 0\nWorker: null\nAcceptance:\n- [ ] do it\n"
        f"### Decisions\n### Blocked Reason\n"
    )


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _setup(root: Path, issues_text: str):
    (root / "queue").mkdir(parents=True)
    (root / "queue" / "wf.md").write_text(issues_text)
    (root / "PROJECTS.md").write_text(PROJECTS)
    (root / "config.yaml").write_text(CONFIG)
    # minimal prompt files referenced by config
    (root / "prompts").mkdir()
    for name in ("validator.md", "worker.md", "verify-review.md"):
        (root / "prompts" / name).write_text("do {issue} in {workdir}\n")
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


def test_dispatch_open_issue_launches_validator(tmp_path: Path):
    _setup(tmp_path, _issue(1, "open"))
    cfg = load_config(tmp_path / "config.yaml")
    launched = dispatch(tmp_path, cfg, started="2026-06-26T00:00:00Z")
    assert launched == [issue_key("wf", 1)]
    reg = load_registry(tmp_path / ".orchestra" / "workers.json")
    assert reg[issue_key("wf", 1)].role == "validator"
    _wait_all_dead(tmp_path)


def test_dispatch_validated_issue_creates_worktree_and_worker(tmp_path: Path):
    _setup(tmp_path, _issue(2, "validated"))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="2026-06-26T00:00:00Z")
    reg = load_registry(tmp_path / ".orchestra" / "workers.json")
    h = reg[issue_key("wf", 2)]
    assert h.role == "worker"
    assert (tmp_path / ".orchestra" / "worktrees" / "wf-002").exists()
    _wait_all_dead(tmp_path)


def test_dispatch_does_not_add_network_gate_to_attempt(tmp_path: Path):
    _setup(tmp_path, _issue(3, "validated"))
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="2026-06-26T00:00:00Z")

    handle = load_registry(tmp_path / ".orchestra" / "workers.json")[issue_key("wf", 3)]
    manifest = json.loads(Path(handle.manifest).read_text())
    assert "network" not in manifest["configuration"]
    _wait_all_dead(tmp_path)


def test_dispatch_records_project_ro_seeds_in_attempt(tmp_path: Path):
    _setup(tmp_path, _issue(3, "validated"))
    projects = (tmp_path / "PROJECTS.md").read_text().replace(
        "- Focus: none\n", "- Worktree-Seed: data/raw:ro-link\n- Focus: none\n"
    )
    (tmp_path / "PROJECTS.md").write_text(projects)
    raw = tmp_path / "projects" / "wf" / "data" / "raw"
    raw.mkdir(parents=True)

    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="2026-06-26T00:00:00Z")

    handle = load_registry(tmp_path / ".orchestra" / "workers.json")[issue_key("wf", 3)]
    manifest = json.loads(Path(handle.manifest).read_text())
    assert manifest["configuration"]["read_only_binds"] == [
        [str(raw.resolve()), str(raw.resolve())]
    ]
    _wait_all_dead(tmp_path)


def test_dispatch_respects_slots(tmp_path: Path):
    _setup(tmp_path, _issue(1, "open") + "\n" + _issue(2, "open") + "\n" + _issue(3, "open"))
    cfg = load_config(tmp_path / "config.yaml")
    cfg.slots = 2
    launched = dispatch(tmp_path, cfg, started="2026-06-26T00:00:00Z")
    assert len(launched) == 2
    _wait_all_dead(tmp_path)


def test_dispatch_does_not_write_queue(tmp_path: Path):
    _setup(tmp_path, _issue(1, "open"))
    before = (tmp_path / "queue" / "wf.md").read_text()
    cfg = load_config(tmp_path / "config.yaml")
    dispatch(tmp_path, cfg, started="2026-06-26T00:00:00Z")
    assert (tmp_path / "queue" / "wf.md").read_text() == before
    _wait_all_dead(tmp_path)


DISPATCH_TOOL = REPO_ROOT / "tools" / "dispatch"

BAD_CONFIG = """\
slots: 2
roles:
  validator: { harness: claude, model: m, prompt: prompts/validator.md }
  worker:    { harness: nonexistent, model: m, prompt: prompts/worker.md }
  verifier:  { harness: claude, model: m, prompt: prompts/verify-review.md }
harnesses:
  claude: { kind: claude, executable: claude }
"""


def test_dispatch_cli_clean_error_on_bad_config(tmp_path: Path):
    (tmp_path / "config.yaml").write_text(BAD_CONFIG)
    result = subprocess.run(
        [sys.executable, str(DISPATCH_TOOL), "--root", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "config error" in result.stderr
    assert "Traceback" not in result.stderr


def test_dispatch_paused_launches_nothing(tmp_path: Path):
    _setup(tmp_path, _issue(1, "open"))
    (tmp_path / ".orchestra").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".orchestra" / "paused").write_text("")
    cfg = load_config(tmp_path / "config.yaml")
    assert dispatch(tmp_path, cfg, started="t") == []


def test_build_context_includes_title_and_acceptance():
    from orchestra.dispatch import build_context
    from orchestra.projects import Project
    from orchestra.issue import parse_issue
    from orchestra.config import Config
    from pathlib import Path
    cfg = Config(
        slots=1, roles={}, validate_semantic=True,
        harnesses={}, sandbox=None, retries_cap=2,
        workflows={}, verify_rerun_checks=False, autoapprove=False,
        template_path="projects/project-template",
    )
    issue = parse_issue(
        "## #007 wf: add retry\nStatus: validated\nPriority: 1\nPlan: null\nSpec: null\n"
        "Depends On: null\nRetries: 0\nWorker: null\nAcceptance:\n"
        "- [ ] retries 5xx\n- [x] tests green\n### Decisions\n### Blocked Reason\n"
    )
    project = Project(name="wf", path="projects/wf", branch="main", queue="queue/wf.md",
                      purpose="", focus="", workflow="python")
    ctx = build_context(".", project, issue, "worker",
                        workdir=Path("/wt"), model="m", config=cfg)
    assert ctx["title"] == "add retry"
    assert "- [ ] retries 5xx" in ctx["acceptance"]
    assert "- [x] tests green" in ctx["acceptance"]


def test_build_context_workflow_decisions_and_role_paths():
    from orchestra.dispatch import build_context
    from orchestra.projects import Project
    from orchestra.issue import parse_issue
    from orchestra.config import Config
    from pathlib import Path
    cfg = Config(
        slots=1, roles={}, validate_semantic=True,
        harnesses={}, sandbox=None, retries_cap=2,
        workflows={"python": {"test": "uv run pytest"}}, verify_rerun_checks=False,
        autoapprove=False, template_path="projects/project-template",
    )
    issue = parse_issue(
        "## #007 wf: add retry\nStatus: validated\nPriority: 1\n"
        "Plan: docs/plans/x.md\nSpec: docs/specs/y.md\nDepends On: null\nRetries: 0\n"
        "Worker: null\nAcceptance:\n- [ ] do it\n### Decisions\nchose y\n"
        "### Blocked Reason\n"
    )
    project = Project(name="wf", path="projects/wf", branch="main",
                      queue="queue/wf.md", purpose="", focus="", workflow="python")
    worker_ctx = build_context(".", project, issue, "worker", workdir=Path("/wt"),
                               model="m", config=cfg)
    val_ctx = build_context(".", project, issue, "validator", workdir=Path("/wt"),
                            model="m", config=cfg)
    assert worker_ctx["plan"] == "docs/plans/x.md"            # as-is for worker
    assert val_ctx["plan"] == "projects/wf/docs/plans/x.md"   # prefixed for validator
    assert worker_ctx["spec"] == "docs/specs/y.md"            # as-is for worker
    assert val_ctx["spec"] == "projects/wf/docs/specs/y.md"   # prefixed for validator
    assert "test: uv run pytest" in worker_ctx["workflow"]
    assert worker_ctx["decisions"] == "chose y"


def test_dispatch_skips_structurally_invalid_open(tmp_path: Path):
    # open issue references a Spec that does not exist -> structurally invalid
    issue = _issue(1, "open").replace("Spec: docs/specs/x.md", "Spec: docs/specs/missing.md")
    _setup(tmp_path, issue)
    cfg = load_config(tmp_path / "config.yaml")
    launched = dispatch(tmp_path, cfg, started="t")
    assert launched == []   # not launched (skipped)


def test_dispatch_skips_unknown_workflow(tmp_path: Path):
    """Config defines workflows; project uses an unknown workflow -> dispatch skips it."""
    config_text = f"""\
slots: 5
retries_cap: 2
workflows:
  python:
    test: uv run pytest
roles:
  validator: {{ harness: fake, model: m, prompt: prompts/validator.md }}
  worker:    {{ harness: fake, model: m, prompt: prompts/worker.md }}
  verifier:  {{ harness: fake, model: m, prompt: prompts/verify-review.md }}
harnesses:
  fake:
    kind: codex
    executable: "{FAKE}"
    preflight: false
sandbox:
  enabled: true
  kind: systemd
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
        (tmp_path / "prompts" / name).write_text("do {issue} in {workdir}\n")
    (tmp_path / "projects" / "wf").mkdir(parents=True)
    _git(tmp_path / "projects" / "wf", "init", "-b", "main")
    _git(tmp_path / "projects" / "wf", "config", "user.email", "t@t.com")
    _git(tmp_path / "projects" / "wf", "config", "user.name", "t")
    (tmp_path / "projects" / "wf" / "README.md").write_text("x\n")
    _git(tmp_path / "projects" / "wf", "add", "README.md")
    _git(tmp_path / "projects" / "wf", "commit", "-m", "init")

    cfg = load_config(tmp_path / "config.yaml")
    launched = dispatch(tmp_path, cfg, started="t")
    assert launched == []


def test_dispatch_worktree_db_creates_clone_after_seed(tmp_path, monkeypatch):
    """A Worktree-DB project triggers create_worktree_db after the worktree is seeded,
    passing the project .env, the worktree .env, and the issue number."""
    import orchestra.dispatch as d

    _setup(tmp_path, _issue(2, "validated"))
    projects = (tmp_path / "PROJECTS.md").read_text().replace(
        "- Focus: none\n", "- Worktree-DB: postgres\n- Focus: none\n"
    )
    (tmp_path / "PROJECTS.md").write_text(projects)

    calls = []
    monkeypatch.setattr(d, "create_worktree_db",
                        lambda repo_env, wt_env, number: calls.append((repo_env, wt_env, number)))

    cfg = load_config(tmp_path / "config.yaml")
    d.dispatch(tmp_path, cfg, started="t")
    _wait_all_dead(tmp_path)

    assert len(calls) == 1
    repo_env, wt_env, number = calls[0]
    assert repo_env == tmp_path / "projects" / "wf" / ".env"
    assert wt_env == tmp_path / ".orchestra" / "worktrees" / "wf-002" / ".env"
    assert number == 2


def test_dispatch_db_create_failure_isolates_that_issue(tmp_path, monkeypatch):
    """A DB-create failure blocks only that issue's launch (skipped), never the loop."""
    import orchestra.dispatch as d

    _setup(tmp_path, _issue(2, "validated") + "\n" + _issue(3, "validated"))
    projects = (tmp_path / "PROJECTS.md").read_text().replace(
        "- Focus: none\n", "- Worktree-DB: postgres\n- Focus: none\n"
    )
    (tmp_path / "PROJECTS.md").write_text(projects)

    def boom(repo_env, wt_env, number):
        if number == 2:
            raise ValueError("simulated DB-create failure")

    monkeypatch.setattr(d, "create_worktree_db", boom)

    cfg = load_config(tmp_path / "config.yaml")
    launched = d.dispatch(tmp_path, cfg, started="t")  # must NOT raise
    _wait_all_dead(tmp_path)

    assert launched == [issue_key("wf", 3)]  # #2 skipped, #3 still launched


def test_dispatch_isolates_per_issue_launch_failure(tmp_path, monkeypatch):
    """One issue's launch failure must not crash dispatch or orphan the others:
    the good issue is still launched + recorded; the failure is skipped + logged."""
    from orchestra.config import load_config
    from orchestra.registry import load_registry
    import orchestra.dispatch as d

    _setup(tmp_path, _issue(1, "validated") + "\n" + _issue(2, "validated"))
    cfg = load_config(tmp_path / "config.yaml")

    calls = {"n": 0}
    real_launch = d._start_supervisor

    def flaky_launch(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FileNotFoundError("simulated supervisor launch failure")
        return real_launch(*a, **k)

    monkeypatch.setattr(d, "_start_supervisor", flaky_launch)

    launched = d.dispatch(tmp_path, cfg, started="t")  # must NOT raise

    assert len(launched) == 1  # one failed, the other went through
    reg = load_registry(tmp_path / ".orchestra" / "workers.json")
    assert len(reg) == 2  # failed attempt is also durable so reconcile can classify it
    assert any(handle.pid == 0 for handle in reg.values())
    _wait_all_dead(tmp_path)
