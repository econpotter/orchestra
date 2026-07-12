import subprocess
from pathlib import Path

import pytest

from orchestra.archive import merge_and_archive
from orchestra.projects import find_project, read_projects
from orchestra.queue import read_queue


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _setup(root: Path, status: str):
    (root / "queue").mkdir(parents=True)
    (root / "queue" / "wf.md").write_text(
        f"## #001 wf: thing\nStatus: {status}\nPriority: 1\nPlan: null\nSpec: null\n"
        f"Depends On: null\nRetries: 0\nWorker: null\nAcceptance:\n- [x] x\n"
        f"### Decisions\n### Blocked Reason\n"
    )
    (root / "PROJECTS.md").write_text(
        "# Projects\n\n## wf\n- Path: projects/wf\n- Branch: main\n"
        "- Purpose: t\n- Queue: queue/wf.md\n- Focus: none\n"
    )
    repo = root / "projects" / "wf"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("x\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    # issue branch with a commit (no worktree needed for this test)
    _git(repo, "checkout", "-b", "issue/001-thing")
    (repo / "f.txt").write_text("done\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", "work")
    _git(repo, "checkout", "main")
    return repo


def test_merge_and_archive_happy(tmp_path):
    repo = _setup(tmp_path, "awaiting_review")
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")
    merge_and_archive(tmp_path, project, 1)
    # merge advances main's ref (runs in a detached temp worktree); assert via the tree
    assert "done" in subprocess.run(
        ["git", "-C", str(repo), "show", "main:f.txt"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert read_queue(tmp_path / "queue" / "wf.md") == []
    assert "Status: archived" in (tmp_path / "queue" / "archive" / "wf.md").read_text()


def test_merge_and_archive_precondition(tmp_path):
    _setup(tmp_path, "committed")
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")
    with pytest.raises(ValueError, match="awaiting_review"):
        merge_and_archive(tmp_path, project, 1)


def test_merge_and_archive_conflict_reworks(tmp_path):
    repo = _setup(tmp_path, "awaiting_review")
    # main now adds f.txt with different content than the issue branch -> merge conflict
    (repo / "f.txt").write_text("other\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", "main adds f.txt")
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")

    result = merge_and_archive(tmp_path, project, 1)

    assert result == "reworked"
    # back to needs_rework with a rebase note, NOT archived
    issues = read_queue(tmp_path / "queue" / "wf.md")
    assert issues[0].status == "needs_rework"
    assert "rebase" in issues[0].verifier_feedback
    # stale issue branch discarded so dispatch cuts a fresh one off the base
    out = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", "issue/001-thing"],
        capture_output=True, text=True,
    ).stdout
    assert out.strip() == ""
    # no merge landed: main has main's f.txt, not the issue's
    shown = subprocess.run(
        ["git", "-C", str(repo), "show", "main:f.txt"], capture_output=True, text=True
    ).stdout
    assert "done" not in shown


def _setup_db(root: Path, status: str):
    repo = _setup(root, status)
    pf = root / "PROJECTS.md"
    pf.write_text(pf.read_text().replace("- Focus: none\n", "- Worktree-DB: postgres\n- Focus: none\n"))
    return repo


def test_merge_and_archive_drops_clone(tmp_path, monkeypatch):
    import orchestra.archive as a
    repo = _setup_db(tmp_path, "awaiting_review")
    calls = []
    monkeypatch.setattr(a, "drop_worktree_db", lambda env, number: calls.append((env, number)))
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")
    a.merge_and_archive(tmp_path, project, 1)
    assert calls == [(repo / ".env", 1)]  # happy-path remove_worktree site drops the clone


def test_rework_drops_clone(tmp_path, monkeypatch):
    import orchestra.archive as a
    repo = _setup_db(tmp_path, "awaiting_review")
    # force a conflict so the rework (stale-worktree removal) path runs
    (repo / "f.txt").write_text("other\n")
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "conflict"], check=True, capture_output=True)
    calls = []
    monkeypatch.setattr(a, "drop_worktree_db", lambda env, number: calls.append((env, number)))
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")
    assert a.merge_and_archive(tmp_path, project, 1) == "reworked"
    assert calls == [(repo / ".env", 1)]  # rework remove_worktree site drops the clone


def test_no_worktree_db_no_drop(tmp_path, monkeypatch):
    import orchestra.archive as a
    _setup(tmp_path, "awaiting_review")  # no Worktree-DB field
    calls = []
    monkeypatch.setattr(a, "drop_worktree_db", lambda env, number: calls.append((env, number)))
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")
    a.merge_and_archive(tmp_path, project, 1)
    assert calls == []  # absent field = zero DB behavior


def test_merge_and_archive_rebase_cap_blocks(tmp_path):
    repo = _setup(tmp_path, "awaiting_review")
    (repo / "f.txt").write_text("other\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", "conflict on main")
    qf = tmp_path / "queue" / "wf.md"
    qf.write_text(qf.read_text().replace("Retries: 0", "Retries: 2"))  # at cap
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")

    result = merge_and_archive(tmp_path, project, 1, rebase_cap=2)

    assert result == "blocked"
    issues = read_queue(qf)
    assert issues[0].status == "blocked"
    assert "manual merge" in issues[0].blocked_reason
