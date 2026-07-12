# tests/test_tools_worktree_create.py
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "worktree-create"

ISSUE = """\
## #001 wf: thing
Status: validated
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

PROJECTS = """\
# Projects

## wf
- Path: projects/wf
- Branch: main
- Purpose: test
- Queue: queue/wf.md
- Focus: none
"""


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _setup(root: Path):
    (root / "queue").mkdir(parents=True)
    (root / "queue" / "wf.md").write_text(ISSUE)
    (root / "PROJECTS.md").write_text(PROJECTS)
    repo = root / "projects" / "wf"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("x\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")


def test_worktree_create(tmp_path: Path):
    _setup(tmp_path)
    result = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path), "wf", "1"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    wt = tmp_path / ".orchestra" / "worktrees" / "wf-001"
    assert wt.exists()
    assert str(wt) in result.stdout


# Fix D: second worktree-create for same issue (branch already exists) → exit 1, no traceback
def test_worktree_create_duplicate_exits_1_no_traceback(tmp_path: Path):
    _setup(tmp_path)
    # first call should succeed
    r1 = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path), "wf", "1"],
        capture_output=True, text=True,
    )
    assert r1.returncode == 0, r1.stderr
    # second call: branch already exists → git error → exit 1
    r2 = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path), "wf", "1"],
        capture_output=True, text=True,
    )
    assert r2.returncode == 1, f"expected 1, got {r2.returncode}\nstderr: {r2.stderr}"
    assert "Traceback" not in r2.stderr, f"unexpected traceback:\n{r2.stderr}"
    assert r2.stderr.strip() != "", "expected an error message on stderr"


def test_worktree_create_nested_repo_project(tmp_path: Path):
    """A project that is its own git repo nested inside another repo's tree
    (e.g. monorepo/service) resolves to the INNER repo, isolated."""
    (tmp_path / "queue").mkdir(parents=True)
    (tmp_path / "queue" / "service.md").write_text(
        ISSUE.replace("wf: thing", "service: thing").replace("Spec: docs/specs/x.md", "Spec: null")
    )
    (tmp_path / "PROJECTS.md").write_text(
        "# Projects\n\n## service\n- Path: projects/monorepo/service\n"
        "- Branch: main\n- Purpose: t\n- Queue: queue/service.md\n- Focus: none\n"
    )
    # outer monorepo with README "top"
    outer = tmp_path / "projects" / "monorepo"
    outer.mkdir(parents=True)
    _git(outer, "init", "-b", "main")
    _git(outer, "config", "user.email", "t@t.com")
    _git(outer, "config", "user.name", "t")
    (outer / "README.md").write_text("top\n")
    _git(outer, "add", "README.md")
    _git(outer, "commit", "-m", "init")
    # inner repo nested inside monorepo's tree, README "ob"
    inner = outer / "service"
    inner.mkdir()
    _git(inner, "init", "-b", "main")
    _git(inner, "config", "user.email", "t@t.com")
    _git(inner, "config", "user.name", "t")
    (inner / "README.md").write_text("ob\n")
    _git(inner, "add", "README.md")
    _git(inner, "commit", "-m", "init")

    result = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path), "service", "1"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    wt = tmp_path / ".orchestra" / "worktrees" / "service-001"
    assert wt.exists()
    assert (wt / "README.md").read_text() == "ob\n"  # inner repo, not "top"
