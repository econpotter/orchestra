import subprocess
import sys
import time
from pathlib import Path

from orchestra.config import load_config
from orchestra.dispatch import dispatch
from orchestra.queue import find_issue, read_queue
from orchestra.reconcile import reconcile
from orchestra.registry import load_registry
from orchestra.selection import pid_alive

REPO_ROOT = Path(__file__).resolve().parent.parent
FAKE = REPO_ROOT / "tests" / "fake_agent.py"
MERGE_TOOL = REPO_ROOT / "tools" / "merge-and-archive"

CONFIG = f"""\
slots: 5
retries_cap: 2
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

PROJECTS = "# Projects\n\n## wf\n- Path: projects/wf\n- Branch: main\n- Purpose: t\n- Queue: queue/wf.md\n- Focus: none\n"

ISSUE = (
    "## #001 wf: t\nStatus: open\nPriority: 1\nPlan: null\nSpec: docs/specs/x.md\n"
    "Depends On: null\nRetries: 0\nWorker: null\nAcceptance:\n- [ ] do it\n"
    "### Decisions\n### Blocked Reason\n"
)


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _setup(root: Path):
    (root / "queue").mkdir(parents=True)
    (root / "queue" / "wf.md").write_text(ISSUE)
    (root / "PROJECTS.md").write_text(PROJECTS)
    (root / "config.yaml").write_text(CONFIG)
    (root / "prompts").mkdir()
    for name in ("validator.md", "worker.md", "verify-review.md"):
        (root / "prompts" / name).write_text("do {issue} -> {result_file}\n")
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


def _wait(root, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        reg = load_registry(root / ".orchestra" / "workers.json")
        if all(not pid_alive(h.pid) for h in reg.values()):
            return
        time.sleep(0.1)
    raise AssertionError("agents still alive")


def _status(root, num):
    return find_issue(read_queue(root / "queue" / "wf.md"), num).status


def _tick(root, cfg):
    dispatch(root, cfg, started="t")
    _wait(root)
    reconcile(root, cfg)


def test_full_loop_open_to_awaiting_review_then_merge(tmp_path: Path):
    _setup(tmp_path)
    cfg = load_config(tmp_path / "config.yaml")

    _tick(tmp_path, cfg)  # validator: open -> validated
    assert _status(tmp_path, 1) == "validated"
    _tick(tmp_path, cfg)  # worker: validated -> committed (real commit on branch)
    assert _status(tmp_path, 1) == "committed"
    _tick(tmp_path, cfg)  # verifier: committed -> awaiting_review
    assert _status(tmp_path, 1) == "awaiting_review"

    # human approves -> merge-and-archive (Phase A tool)
    r = subprocess.run(
        [sys.executable, str(MERGE_TOOL), "--root", str(tmp_path), "wf", "1"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    # merge advances main's ref (detached temp worktree); assert the file is in main's tree
    assert subprocess.run(
        ["git", "-C", str(tmp_path / "projects" / "wf"), "cat-file", "-e", "main:fake_work.txt"],
    ).returncode == 0  # merged to main
    archive = (tmp_path / "queue" / "archive" / "wf.md").read_text()
    assert "Status: archived" in archive
