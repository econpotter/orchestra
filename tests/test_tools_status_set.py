import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "status-set"

ISSUE = """\
## #001 wf: thing
Status: open
Priority: 1
Plan: null
Spec: docs/specs/x.md
Depends On: null
Retries: 0
Worker: null
Acceptance:
- [ ] do it
### Decisions
### Blocked Reason
"""


def _setup(root: Path):
    (root / "queue").mkdir(parents=True)
    (root / "queue" / "wf.md").write_text(ISSUE)


def test_status_set_changes_status(tmp_path: Path):
    _setup(tmp_path)
    result = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path), "wf", "1",
         "blocked", "--reason", "stuck: missing token", "--retries", "2"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "queue" / "wf.md").read_text()
    assert "Status: blocked" in text
    assert "Retries: 2" in text
    assert "stuck: missing token" in text


def test_status_set_missing_issue(tmp_path: Path):
    _setup(tmp_path)
    result = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path), "wf", "99", "blocked"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
