import sys
import time
from pathlib import Path

from orchestra.cli import main
from orchestra.queue import find_issue, read_queue
from orchestra.registry import load_registry
from orchestra.selection import pid_alive

REPO_ROOT = Path(__file__).resolve().parent.parent
FAKE = REPO_ROOT / "tests" / "fake_agent.py"

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


def _setup(root: Path):
    import subprocess
    (root / "queue").mkdir(parents=True)
    (root / "queue" / "wf.md").write_text(
        "## #001 wf: t\nStatus: open\nPriority: 1\nPlan: null\nSpec: null\n"
        "Depends On: null\nRetries: 0\nWorker: null\nAcceptance:\n- [ ] x\n"
        "### Decisions\n### Blocked Reason\n"
    )
    (root / "PROJECTS.md").write_text(
        "# Projects\n\n## wf\n- Path: projects/wf\n- Branch: main\n"
        "- Purpose: t\n- Queue: queue/wf.md\n- Focus: none\n"
    )
    (root / "config.yaml").write_text(CONFIG)
    (root / "prompts").mkdir()
    for n in ("validator.md", "worker.md", "verify-review.md"):
        (root / "prompts" / n).write_text("do {issue} -> {result_file}\n")
    repo = root / "projects" / "wf"
    repo.mkdir(parents=True)
    for a in (["init", "-b", "main"], ["config", "user.email", "t@t.com"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)


def _wait(root, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        reg = load_registry(root / ".orchestra" / "workers.json")
        if all(not pid_alive(h.pid) for h in reg.values()):
            return
        time.sleep(0.1)


def test_cli_tick_advances(tmp_path):
    _setup(tmp_path)
    final = None
    for _ in range(8):
        assert main(["--root", str(tmp_path), "tick"]) == 0
        _wait(tmp_path)
        final = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 1).status
        if final == "awaiting_review":
            break
    assert final == "awaiting_review"
