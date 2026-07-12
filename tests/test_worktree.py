from pathlib import Path

import pytest

from orchestra.worktree import seed_worktree


def _make_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "big.parquet").write_text("payload")
    (repo / "fixtures").mkdir()
    (repo / "fixtures" / "sample.csv").write_text("a,b\n1,2\n")
    (repo / ".env").write_text("API_KEY=secret\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    return repo, wt


def test_env_always_copied(tmp_path):
    repo, wt = _make_repo(tmp_path)
    seed_worktree(repo, wt, [])
    assert (wt / ".env").read_text() == "API_KEY=secret\n"
    # copy, not symlink — worker edits must not touch the real .env
    assert not (wt / ".env").is_symlink()


def test_missing_env_is_fine(tmp_path):
    repo, wt = _make_repo(tmp_path)
    (repo / ".env").unlink()
    seed_worktree(repo, wt, [])  # no raise
    assert not (wt / ".env").exists()


def test_copy_mode_duplicates_dir(tmp_path):
    repo, wt = _make_repo(tmp_path)
    seed_worktree(repo, wt, [("fixtures", "copy")])
    assert (wt / "fixtures" / "sample.csv").read_text() == "a,b\n1,2\n"
    assert not (wt / "fixtures").is_symlink()


def test_link_mode_symlinks_dir(tmp_path):
    repo, wt = _make_repo(tmp_path)
    seed_worktree(repo, wt, [("data", "link")])
    assert (wt / "data").is_symlink()
    assert (wt / "data").resolve() == (repo / "data").resolve()
    # shared: worker sees the real data without a copy
    assert (wt / "data" / "big.parquet").read_text() == "payload"


def test_missing_seed_path_warns_not_fatal(tmp_path, capsys):
    repo, wt = _make_repo(tmp_path)
    seed_worktree(repo, wt, [("nonexistent", "link")])  # must not raise
    assert not (wt / "nonexistent").exists()
    err = capsys.readouterr().err
    assert "nonexistent" in err  # loud, not silent


def test_no_clobber_existing(tmp_path):
    repo, wt = _make_repo(tmp_path)
    (wt / "fixtures").mkdir()
    (wt / "fixtures" / "tracked.txt").write_text("keep")
    seed_worktree(repo, wt, [("fixtures", "copy")])
    assert (wt / "fixtures" / "tracked.txt").read_text() == "keep"


def test_bad_mode_raises(tmp_path):
    repo, wt = _make_repo(tmp_path)
    with pytest.raises(ValueError):
        seed_worktree(repo, wt, [("data", "move")])
