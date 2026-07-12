import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "validate"

VALID = """\
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

INVALID = VALID.replace("Spec: docs/specs/x.md", "Spec: docs/specs/missing.md")


def _setup(root: Path, content: str):
    (root / "queue").mkdir(parents=True)
    (root / "queue" / "wf.md").write_text(content)
    (root / "projects" / "wf" / "docs" / "specs").mkdir(parents=True)
    (root / "projects" / "wf" / "docs" / "specs" / "x.md").write_text("spec")
    (root / "PROJECTS.md").write_text("## wf\n- Path: projects/wf")


def test_validate_ok(tmp_path: Path):
    _setup(tmp_path, VALID)
    result = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path), "wf", "1"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_validate_reports_reasons(tmp_path: Path):
    _setup(tmp_path, INVALID)
    result = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path), "wf", "1"],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "missing.md" in result.stdout


# Fix A: malformed Priority (non-numeric) → exit 2 (malformed queue)
MALFORMED_PRIORITY = VALID.replace("Priority: 1", "Priority: not-a-number")


def test_validate_malformed_priority_exits_2(tmp_path: Path):
    _setup(tmp_path, MALFORMED_PRIORITY)
    result = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path), "wf", "1"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2, f"expected 2, got {result.returncode}\n{result.stderr}"


# Fix A: missing Priority field entirely → exit 2 (malformed queue)
MISSING_PRIORITY = """\
## #001 wf: thing
Status: open
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


def test_validate_missing_priority_exits_2(tmp_path: Path):
    _setup(tmp_path, MISSING_PRIORITY)
    result = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path), "wf", "1"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2, f"expected 2, got {result.returncode}\n{result.stderr}"
