import subprocess
import sys
import time
from pathlib import Path

from orchestra.queue import find_issue, read_queue
from orchestra.registry import load_registry
from orchestra.selection import pid_alive

REPO_ROOT = Path(__file__).resolve().parent.parent
FAKE = REPO_ROOT / "tests" / "fake_agent.py"
TICK = REPO_ROOT / "tools" / "tick"

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
sandbox: {{ enabled: false, argv_prefix: [] }}
"""

PROJECTS = "# Projects\n\n## wf\n- Path: projects/wf\n- Branch: main\n- Purpose: t\n- Queue: queue/wf.md\n- Focus: none\n"
ISSUE = ("## #001 wf: t\nStatus: open\nPriority: 1\nPlan: null\nSpec: null\n"
         "Depends On: null\nRetries: 0\nWorker: null\nAcceptance:\n- [ ] do it\n"
         "### Decisions\n### Blocked Reason\n")


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _setup(root: Path):
    (root / "queue").mkdir(parents=True)
    (root / "queue" / "wf.md").write_text(ISSUE)
    (root / "PROJECTS.md").write_text(PROJECTS)
    (root / "config.yaml").write_text(CONFIG)
    (root / "prompts").mkdir()
    for n in ("validator.md", "worker.md", "verify-review.md"):
        (root / "prompts" / n).write_text("do {issue} -> {result_file}\n")
    repo = root / "projects" / "wf"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("x\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")


def _wait(root, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        reg = load_registry(root / ".orchestra" / "workers.json")
        if all(not pid_alive(h.pid) for h in reg.values()):
            return
        time.sleep(0.1)


def test_tick_drives_issue_forward(tmp_path: Path):
    _setup(tmp_path)
    # Each tick = dispatch + reconcile (no internal wait); loop with waits so the
    # fake agents finish between ticks. The issue should reach awaiting_review.
    final = None
    for _ in range(8):
        r = subprocess.run([sys.executable, str(TICK), "--root", str(tmp_path)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        _wait(tmp_path)
        final = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 1).status
        if final == "awaiting_review":
            break
    assert final == "awaiting_review"


def test_systemd_units_present_and_reference_tick():
    svc = (REPO_ROOT / "systemd" / "orchestra.service").read_text()
    tmr = (REPO_ROOT / "systemd" / "orchestra.timer").read_text()
    assert "ExecStart" in svc and "tick" in svc
    assert "--root %h/orchestra" not in svc
    assert "[Timer]" in tmr and "OnCalendar" in tmr
