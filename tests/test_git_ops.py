import subprocess
from pathlib import Path

from orchestra import git_ops


def _run(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_create_worktree_and_commit_detection(git_repo: Path, tmp_path: Path):
    wt = tmp_path / "wt"
    git_ops.create_worktree(git_repo, wt, "issue/001-x", "main")
    assert wt.exists()
    assert git_ops.commit_exists_on_branch(git_repo, "issue/001-x", "main") is False

    (wt / "feature.txt").write_text("work\n")
    _run(wt, "add", "feature.txt")
    _run(wt, "commit", "-m", "do work")
    assert git_ops.commit_exists_on_branch(git_repo, "issue/001-x", "main") is True


def test_branch_head(git_repo: Path, tmp_path: Path):
    wt = tmp_path / "wt"
    git_ops.create_worktree(git_repo, wt, "issue/004-h", "main")
    sha_before = git_ops.branch_head(git_repo, "issue/004-h")
    assert len(sha_before) == 40

    (wt / "newfile.txt").write_text("x\n")
    _run(wt, "add", "newfile.txt")
    _run(wt, "commit", "-m", "add newfile")
    sha_after = git_ops.branch_head(git_repo, "issue/004-h")
    assert sha_after != sha_before
    assert len(sha_after) == 40


def test_merge_branch(git_repo: Path, tmp_path: Path):
    wt = tmp_path / "wt"
    git_ops.create_worktree(git_repo, wt, "issue/002-y", "main")
    (wt / "feature.txt").write_text("work\n")
    _run(wt, "add", "feature.txt")
    _run(wt, "commit", "-m", "do work")

    git_ops.merge_branch(git_repo, "issue/002-y", "main")
    # Checkout is on main and clean → merge happens IN the checkout, so the merged file
    # is present on disk (not just in the ref) — the working tree reflects the merge.
    assert git_ops.commit_exists_on_branch(git_repo, "issue/002-y", "main") is False
    assert (git_repo / "feature.txt").read_text() == "work\n"


def test_remove_worktree(git_repo: Path, tmp_path: Path):
    wt = tmp_path / "wt"
    git_ops.create_worktree(git_repo, wt, "issue/003-z", "main")
    git_ops.remove_worktree(git_repo, wt)
    assert not wt.exists()


def test_create_worktree_existing_branch(git_repo: Path, tmp_path: Path):
    """create_worktree for a branch that already exists must attach, not fail."""
    wtA = tmp_path / "wtA"
    wtB = tmp_path / "wtB"

    # First: create worktree on a new branch, commit a file, then remove worktree.
    git_ops.create_worktree(git_repo, wtA, "issue/010-x", "main")
    (wtA / "canary.txt").write_text("canary\n")
    _run(wtA, "add", "canary.txt")
    _run(wtA, "commit", "-m", "add canary")
    git_ops.remove_worktree(git_repo, wtA)

    # Branch "issue/010-x" still exists; worktree is gone.
    assert not wtA.exists()

    # Second: create_worktree for the SAME branch into a different path — must not raise.
    git_ops.create_worktree(git_repo, wtB, "issue/010-x", "main")
    assert wtB.exists()
    # Attached to existing branch, so the previously-committed file must be present.
    assert (wtB / "canary.txt").exists()


def test_create_worktree_replaces_clean_foreign_worktree(git_repo: Path, tmp_path: Path):
    foreign = tmp_path / "foreign"
    canonical = tmp_path / "canonical"
    git_ops.create_worktree(git_repo, foreign, "issue/011-x", "main")

    git_ops.create_worktree(git_repo, canonical, "issue/011-x", "main")

    assert canonical.exists()
    assert not foreign.exists()


def test_create_worktree_refuses_dirty_foreign_worktree(git_repo: Path, tmp_path: Path):
    foreign = tmp_path / "foreign"
    canonical = tmp_path / "canonical"
    git_ops.create_worktree(git_repo, foreign, "issue/012-x", "main")
    (foreign / "local.txt").write_text("keep me\n")

    try:
        git_ops.create_worktree(git_repo, canonical, "issue/012-x", "main")
    except RuntimeError as exc:
        assert "dirty worktree" in str(exc)
        assert str(foreign) in str(exc)
    else:
        raise AssertionError("dirty foreign worktree was removed")

    assert foreign.exists()
    assert (foreign / "local.txt").read_text() == "keep me\n"


def test_merge_branch_robust_to_dirty_main_checkout(git_repo: Path, tmp_path: Path):
    """Merge must succeed even when the main checkout has an untracked file that
    collides with a file the branch adds (the smoke-test failure). The merge runs
    in a detached temp worktree, so the main checkout is never touched."""
    # issue branch adds a tracked lock.txt
    wt = tmp_path / "wt"
    git_ops.create_worktree(git_repo, wt, "issue/010-x", "main")
    (wt / "lock.txt").write_text("from-branch\n")
    _run(wt, "add", "lock.txt")
    _run(wt, "commit", "-m", "add lock")
    git_ops.remove_worktree(git_repo, wt)

    # main checkout has an UNTRACKED lock.txt that would collide on an in-checkout merge
    (git_repo / "lock.txt").write_text("untracked-local\n")

    git_ops.merge_branch(git_repo, "issue/010-x", "main")

    # main ref advanced to include the branch's commit (no longer ahead)
    assert git_ops.commit_exists_on_branch(git_repo, "issue/010-x", "main") is False
    # branch's tracked file is now in main's tree
    shown = subprocess.run(
        ["git", "-C", str(git_repo), "show", "main:lock.txt"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "from-branch" in shown
    # the main checkout's untracked working-tree file is untouched
    assert (git_repo / "lock.txt").read_text() == "untracked-local\n"


def test_merge_branch_syncs_primary_worktree_for_nonconflicting_files(
    git_repo: Path, tmp_path: Path
):
    """The dirty-checkout fallback must SYNC merged files that have no local edit into the
    primary working tree — else the checkout stays stale and merged code never takes
    effect (the recurring 'merge advanced ref, engine ran old code' bug)."""
    wt = tmp_path / "wt"
    git_ops.create_worktree(git_repo, wt, "issue/020-e", "main")
    (wt / "engine.py").write_text("CRASH_RETRY = True\n")  # new file
    (wt / "README.md").write_text("hello\nmerged-line\n")  # modify tracked
    _run(wt, "add", "-A")
    _run(wt, "commit", "-m", "engine work")
    git_ops.remove_worktree(git_repo, wt)

    # DIRTY the main checkout on an UNRELATED file to force the fallback path
    (git_repo / "notes.txt").write_text("local scratch\n")

    git_ops.merge_branch(git_repo, "issue/020-e", "main")

    # merged files with no local edit are now materialized in the WORKING TREE
    assert (git_repo / "engine.py").read_text() == "CRASH_RETRY = True\n"
    assert "merged-line" in (git_repo / "README.md").read_text()
    # unrelated local work preserved
    assert (git_repo / "notes.txt").read_text() == "local scratch\n"
    # no phantom-revert: working tree matches HEAD for the merged files
    diff = subprocess.run(
        ["git", "-C", str(git_repo), "diff", "--name-only", "HEAD", "--",
         "engine.py", "README.md"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert diff == ""


def test_merge_branch_preserves_locally_edited_merged_file(git_repo: Path, tmp_path: Path):
    """A merged file that ALSO has a local uncommitted edit is left as-is (local wins),
    not clobbered — the merge still landed in the ref."""
    wt = tmp_path / "wt"
    git_ops.create_worktree(git_repo, wt, "issue/021-c", "main")
    (wt / "README.md").write_text("hello\nfrom-branch\n")
    _run(wt, "add", "-A")
    _run(wt, "commit", "-m", "edit readme")
    git_ops.remove_worktree(git_repo, wt)

    (git_repo / "README.md").write_text("hello\nLOCAL-EDIT\n")  # dirty the same file

    git_ops.merge_branch(git_repo, "issue/021-c", "main")

    # local edit preserved in the working tree
    assert "LOCAL-EDIT" in (git_repo / "README.md").read_text()
    # but the merge landed in the committed tree
    shown = subprocess.run(
        ["git", "-C", str(git_repo), "show", "main:README.md"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "from-branch" in shown
    # the index must point at the MERGED version, not the pre-merge one — otherwise
    # `git status` shows a staged diff that silently REVERTS the merge if committed
    staged = subprocess.run(
        ["git", "-C", str(git_repo), "diff", "--cached", "--name-only"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert staged == ""


def test_merge_branch_three_way_merges_nonconflicting_local_edit(
    git_repo: Path, tmp_path: Path
):
    """A merged file with a local edit in a DIFFERENT region gets a content-level 3-way
    merge: the working tree ends up with BOTH the merged change and the local edit."""
    (git_repo / "config.txt").write_text("alpha\nbeta\ngamma\n")
    _run(git_repo, "add", "config.txt")
    _run(git_repo, "commit", "-m", "add config")

    wt = tmp_path / "wt"
    git_ops.create_worktree(git_repo, wt, "issue/022-t", "main")
    (wt / "config.txt").write_text("alpha-from-branch\nbeta\ngamma\n")  # edit line 1
    _run(wt, "add", "-A")
    _run(wt, "commit", "-m", "branch edits line 1")
    git_ops.remove_worktree(git_repo, wt)

    (git_repo / "config.txt").write_text("alpha\nbeta\ngamma-local\n")  # edit line 3

    git_ops.merge_branch(git_repo, "issue/022-t", "main")

    merged = (git_repo / "config.txt").read_text()
    assert "alpha-from-branch" in merged  # merged change took effect
    assert "gamma-local" in merged  # local edit preserved
    assert "<<<<<<<" not in merged
    # index at the merged HEAD version: local edit shows as unstaged, nothing staged
    staged = subprocess.run(
        ["git", "-C", str(git_repo), "diff", "--cached", "--name-only"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert staged == ""


def test_merge_branch_deleted_upstream_but_locally_edited_keeps_local_file(
    git_repo: Path, tmp_path: Path
):
    """Merge deletes a file the checkout has locally edited: keep the local file on disk
    (as untracked), but drop it from the index so nothing staged resurrects it silently."""
    (git_repo / "old.txt").write_text("v1\n")
    _run(git_repo, "add", "old.txt")
    _run(git_repo, "commit", "-m", "add old")

    wt = tmp_path / "wt"
    git_ops.create_worktree(git_repo, wt, "issue/023-d", "main")
    _run(wt, "rm", "old.txt")
    _run(wt, "commit", "-m", "delete old")
    git_ops.remove_worktree(git_repo, wt)

    (git_repo / "old.txt").write_text("v1\nlocal-edit\n")  # dirty the doomed file
    (git_repo / "scratch.txt").write_text("x\n")  # keep checkout dirty regardless

    git_ops.merge_branch(git_repo, "issue/023-d", "main")

    assert (git_repo / "old.txt").read_text() == "v1\nlocal-edit\n"
    staged = subprocess.run(
        ["git", "-C", str(git_repo), "diff", "--cached", "--name-only"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert staged == ""
    status = subprocess.run(
        ["git", "-C", str(git_repo), "status", "--porcelain", "--", "old.txt"],
        capture_output=True, text=True,
    ).stdout
    assert status.startswith("??")  # local content survives as untracked


def test_file_in_branch(git_repo: Path):
    # README committed on main by the fixture; a stray file is not
    assert git_ops.file_in_branch(git_repo, "main", "README.md") is True
    assert git_ops.file_in_branch(git_repo, "main", "docs/nope.md") is False


def _dirty_fallback_branch(git_repo: Path, tmp_path: Path, branch: str) -> None:
    """Commit a file on `branch` then dirty the main checkout so merge_branch takes its
    detached-temp-worktree fallback (the path that creates the merge tmpdir)."""
    wt = tmp_path / "wt"
    git_ops.create_worktree(git_repo, wt, branch, "main")
    (wt / "feature.txt").write_text("work\n")
    _run(wt, "add", "feature.txt")
    _run(wt, "commit", "-m", "do work")
    git_ops.remove_worktree(git_repo, wt)
    (git_repo / "scratch.txt").write_text("local\n")  # dirty -> forces fallback


def test_merge_branch_tmpdir_default_is_repo_parent_not_tmpfs(
    git_repo: Path, tmp_path: Path, monkeypatch
):
    """The fallback merge worktree must be created on a disk-backed path (the repo's
    parent), NOT under /tmp — issue #006 saw a merge die with 'Disk quota exceeded' on a
    tmpfs /tmp. Default tmpdir=None => dir=repo.parent."""
    _dirty_fallback_branch(git_repo, tmp_path, "issue/030-a")
    seen: dict[str, str] = {}
    real_mkdtemp = git_ops.tempfile.mkdtemp

    def spy(*a, **kw):
        seen["dir"] = kw.get("dir")
        return real_mkdtemp(*a, **kw)

    monkeypatch.setattr(git_ops.tempfile, "mkdtemp", spy)

    git_ops.merge_branch(git_repo, "issue/030-a", "main")

    assert seen["dir"] == str(git_repo.parent)
    assert git_ops.commit_exists_on_branch(git_repo, "issue/030-a", "main") is False


def test_merge_branch_honors_configured_tmpdir(git_repo: Path, tmp_path: Path, monkeypatch):
    """An explicit tmpdir overrides the default and is created if absent."""
    _dirty_fallback_branch(git_repo, tmp_path, "issue/031-b")
    custom = tmp_path / "merge-tmp"  # does not exist yet
    seen: dict[str, str] = {}
    real_mkdtemp = git_ops.tempfile.mkdtemp

    def spy(*a, **kw):
        seen["dir"] = kw.get("dir")
        return real_mkdtemp(*a, **kw)

    monkeypatch.setattr(git_ops.tempfile, "mkdtemp", spy)

    git_ops.merge_branch(git_repo, "issue/031-b", "main", tmpdir=custom)

    assert seen["dir"] == str(custom)
    assert custom.is_dir()  # created on demand
    assert git_ops.commit_exists_on_branch(git_repo, "issue/031-b", "main") is False


def test_merge_conflicts(git_repo: Path):
    # branch 'a' and main both change README differently -> conflict
    _run(git_repo, "checkout", "-b", "a")
    (git_repo / "README.md").write_text("A\n")
    _run(git_repo, "add", "-A")
    _run(git_repo, "commit", "-m", "a")
    _run(git_repo, "checkout", "main")
    (git_repo / "README.md").write_text("B\n")
    _run(git_repo, "add", "-A")
    _run(git_repo, "commit", "-m", "b")
    assert git_ops.merge_conflicts(git_repo, "main", "a") is True

    # branch 'c' adds an unrelated file -> no conflict
    _run(git_repo, "checkout", "-b", "c", "main")
    (git_repo / "new.txt").write_text("n\n")
    _run(git_repo, "add", "-A")
    _run(git_repo, "commit", "-m", "c")
    _run(git_repo, "checkout", "main")
    assert git_ops.merge_conflicts(git_repo, "main", "c") is False
