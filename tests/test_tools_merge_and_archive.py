import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "merge-and-archive"
WT_TOOL = REPO_ROOT / "tools" / "worktree-create"

ISSUE = """\
## #001 wf: thing
Status: awaiting_review
Priority: 1
Plan: null
Spec: docs/specs/x.md
Depends On: null
Retries: 0
Worker: null
Acceptance:
- [x] do it
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
    return repo


def test_merge_and_archive(tmp_path: Path):
    repo = _setup(tmp_path)
    # create worktree + a commit on the issue branch
    subprocess.run(
        [sys.executable, str(WT_TOOL), "--root", str(tmp_path), "wf", "1"],
        check=True, capture_output=True,
    )
    wt = tmp_path / ".orchestra" / "worktrees" / "wf-001"
    (wt / "feature.txt").write_text("done\n")
    _git(wt, "add", "feature.txt")
    _git(wt, "commit", "-m", "work")

    result = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path), "wf", "1"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "done" in subprocess.run(                    # merged into main (via ref)
        ["git", "-C", str(repo), "show", "main:feature.txt"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert not wt.exists()                              # worktree removed
    assert (tmp_path / "queue" / "wf.md").read_text().strip() == ""  # removed from active
    archive = (tmp_path / "queue" / "archive" / "wf.md").read_text()
    assert "## #001 wf: thing" in archive
    assert "Status: archived" in archive


def test_worktree_removal_failure_still_archives(tmp_path: Path):
    repo = _setup(tmp_path)
    # Create the issue branch with a commit but NO registered worktree, so the
    # tool's remove_worktree step raises while merge + archive succeed.
    _git(repo, "checkout", "-b", "issue/001-thing")
    (repo / "feature.txt").write_text("done\n")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-m", "work")
    _git(repo, "checkout", "main")

    result = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path), "wf", "1"],
        capture_output=True, text=True,
    )
    # merge + archive succeeded despite the worktree-removal failure
    assert result.returncode == 0, result.stderr
    assert "warning" in result.stderr.lower()
    assert "done" in subprocess.run(                                    # merged into main (via ref)
        ["git", "-C", str(repo), "show", "main:feature.txt"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert (tmp_path / "queue" / "wf.md").read_text().strip() == ""     # removed from active
    assert "Status: archived" in (tmp_path / "queue" / "archive" / "wf.md").read_text()


def test_merge_refused_when_not_awaiting_review(tmp_path: Path):
    _setup(tmp_path)  # _setup seeds the issue; make a committed variant
    # Overwrite the queue with a committed (not awaiting_review) issue
    (tmp_path / "queue" / "wf.md").write_text(ISSUE.replace("Status: awaiting_review", "Status: committed"))
    result = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path), "wf", "1"],
        capture_output=True, text=True,
    )
    assert result.returncode == 3
    assert "awaiting_review" in (result.stdout + result.stderr)
    # nothing merged: feature file absent on main, issue still in active queue
    assert "## #001" in (tmp_path / "queue" / "wf.md").read_text()
