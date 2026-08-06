import subprocess
from pathlib import Path

import pytest

from orchestra.manual_archive import cascade_dropped, manual_archive
from orchestra.projects import find_project, read_projects
from orchestra.queue import find_issue, read_queue, write_queue
from orchestra.registry import WorkerHandle, save_registry


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _setup(root: Path, issues_text: str):
    (root / "queue").mkdir(parents=True)
    (root / "queue" / "wf.md").write_text(issues_text)
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
    return repo


def _issue_block(number: int, status: str, depends_on: str = "null") -> str:
    return (
        f"## #{number:03d} wf: thing {number}\nStatus: {status}\nPriority: 1\n"
        f"Plan: null\nSpec: null\nDepends On: {depends_on}\nRetries: 0\nWorker: null\n"
        f"Acceptance:\n- [x] x\n### Decisions\n### Blocked Reason\n"
    )


def _branch_with_commit(repo, name):
    _git(repo, "checkout", "-b", name)
    (repo / "f.txt").write_text("done\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", "work")
    _git(repo, "checkout", "main")


def test_archive_writes_status_and_reason_and_removes_from_live(tmp_path):
    _setup(tmp_path, _issue_block(1, "awaiting_review"))
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")

    manual_archive(tmp_path, project, 1, status="archived", reason="landed by hand in 8c25da7")

    assert read_queue(tmp_path / "queue" / "wf.md") == []
    archived = read_queue(tmp_path / "queue" / "archive" / "wf.md")
    assert archived[0].status == "archived"
    assert archived[0].archive_reason == "landed by hand in 8c25da7"


def test_drop_writes_status_and_reason(tmp_path):
    _setup(tmp_path, _issue_block(1, "open"))
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")

    manual_archive(tmp_path, project, 1, status="dropped", reason="superseded by #112")

    archived = read_queue(tmp_path / "queue" / "archive" / "wf.md")
    assert archived[0].status == "dropped"
    assert archived[0].archive_reason == "superseded by #112"


def test_branch_survives_and_reports_unmerged_count(tmp_path):
    repo = _setup(tmp_path, _issue_block(1, "awaiting_review"))
    _branch_with_commit(repo, "issue/001-thing-1")
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")

    outcome = manual_archive(tmp_path, project, 1, status="archived", reason="r")

    assert outcome.branch == "issue/001-thing-1"
    assert outcome.unmerged_commits == 1
    # left in place, not deleted
    out = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", "issue/001-thing-1"],
        capture_output=True, text=True,
    ).stdout
    assert "issue/001-thing-1" in out


def test_branch_never_cut_reports_zero_unmerged(tmp_path):
    _setup(tmp_path, _issue_block(1, "open"))
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")

    outcome = manual_archive(tmp_path, project, 1, status="dropped", reason="moot")

    assert outcome.unmerged_commits == 0


def test_worktree_db_dropped_when_configured(tmp_path, monkeypatch):
    import orchestra.manual_archive as m
    repo = _setup(tmp_path, _issue_block(1, "awaiting_review"))
    pf = tmp_path / "PROJECTS.md"
    pf.write_text(pf.read_text().replace("- Focus: none\n", "- Worktree-DB: postgres\n- Focus: none\n"))
    calls = []
    monkeypatch.setattr(m, "drop_worktree_db", lambda env, number: calls.append((env, number)))
    project = find_project(read_projects(pf), "wf")

    manual_archive(tmp_path, project, 1, status="archived", reason="r")

    assert calls == [(repo / ".env", 1)]


def test_worktree_removal_failure_warns_and_still_completes(tmp_path, capsys):
    _setup(tmp_path, _issue_block(1, "awaiting_review"))
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")

    # No worktree was ever created for this issue, so `git worktree remove` fails —
    # must warn, not raise, and the archive must still have completed.
    manual_archive(tmp_path, project, 1, status="archived", reason="r")

    assert "warning: worktree removal failed" in capsys.readouterr().err
    assert read_queue(tmp_path / "queue" / "wf.md") == []


def test_refuses_unknown_number(tmp_path):
    _setup(tmp_path, _issue_block(1, "open"))
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")

    with pytest.raises(ValueError, match=r"#7.*not found"):
        manual_archive(tmp_path, project, 7, status="archived", reason="r")


def test_refuses_live_handle(tmp_path):
    _setup(tmp_path, _issue_block(1, "open"))
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")
    save_registry(tmp_path / ".orchestra" / "workers.json", {
        "wf#001": WorkerHandle(
            project="wf", number=1, role="validator", branch="", worktree="",
            pid=1, attempt_id="a", manifest="", stdout="", stderr="",
            started="now", start_sha="", proc_start="",
        ),
    })

    with pytest.raises(ValueError, match="kill and reconcile"):
        manual_archive(tmp_path, project, 1, status="archived", reason="r")
    # refused before any write — issue is untouched
    assert find_issue(read_queue(tmp_path / "queue" / "wf.md"), 1).status == "open"


@pytest.mark.parametrize("status", ["archived", "dropped"])
def test_refuses_already_terminal(tmp_path, status):
    _setup(tmp_path, _issue_block(1, status))
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")

    with pytest.raises(ValueError, match=f"already {status}"):
        manual_archive(tmp_path, project, 1, status="dropped", reason="r")


def test_archive_write_precedes_live_removal_crash_safety(tmp_path, monkeypatch):
    """A crash between the two writes must leave the issue recoverable: the archive-file
    write happens first, so if the second (live-queue) write never runs, the issue is
    fully present in the archive and merely still lingering in the live queue too."""
    import orchestra.manual_archive as m

    _setup(tmp_path, _issue_block(1, "open"))
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")

    real_write_queue = write_queue
    calls = []

    def _crash_on_second_write(path, issues):
        calls.append(path)
        if len(calls) == 2:
            raise RuntimeError("simulated crash")  # crash BEFORE this write lands
        real_write_queue(path, issues)

    monkeypatch.setattr(m, "write_queue", _crash_on_second_write)

    with pytest.raises(RuntimeError, match="simulated crash"):
        manual_archive(tmp_path, project, 1, status="dropped", reason="r")

    # archive write landed (first call) before the crash on the second (live-queue) write
    archived = read_queue(tmp_path / "queue" / "archive" / "wf.md")
    assert archived and archived[0].number == 1
    assert archived[0].status == "dropped"
    # the live queue write is the one that crashed, so the issue is still sitting there too
    live = read_queue(tmp_path / "queue" / "wf.md")
    assert find_issue(live, 1) is not None


def test_cascade_dropped_blocks_dependents(tmp_path):
    text = _issue_block(1, "open") + "\n" + _issue_block(2, "validated", depends_on="1")
    _setup(tmp_path, text)
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")

    manual_archive(tmp_path, project, 1, status="dropped", reason="superseded by #9")
    blocked = cascade_dropped(tmp_path, project, 1, "superseded by #9")

    assert blocked == [2]
    issue2 = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 2)
    assert issue2.status == "blocked"
    assert "depends on dropped #1: superseded by #9" in issue2.blocked_reason


def test_cascade_dropped_ignores_non_dependents(tmp_path):
    text = _issue_block(1, "open") + "\n" + _issue_block(2, "validated")
    _setup(tmp_path, text)
    project = find_project(read_projects(tmp_path / "PROJECTS.md"), "wf")

    manual_archive(tmp_path, project, 1, status="dropped", reason="moot")
    blocked = cascade_dropped(tmp_path, project, 1, "moot")

    assert blocked == []
    issue2 = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 2)
    assert issue2.status == "validated"
