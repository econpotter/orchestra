import os
import subprocess
from pathlib import Path

from orchestra.cli import main
from orchestra.queue import find_issue, read_queue
from orchestra.registry import WorkerHandle, issue_key, load_registry, save_registry


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _setup(root: Path, status="awaiting_review"):
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
    _git(repo, "checkout", "-b", "issue/001-thing")
    (repo / "f.txt").write_text("done\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", "work")
    _git(repo, "checkout", "main")
    return repo


def test_approve(tmp_path, capsys):
    repo = _setup(tmp_path)
    rc = main(["--root", str(tmp_path), "approve", "wf", "1"])
    assert rc == 0
    assert "done" in subprocess.run(
        ["git", "-C", str(repo), "show", "main:f.txt"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "Status: archived" in (tmp_path / "queue" / "archive" / "wf.md").read_text()


def test_approve_refused_not_awaiting(tmp_path, capsys):
    _setup(tmp_path, status="committed")
    rc = main(["--root", str(tmp_path), "approve", "wf", "1"])
    assert rc == 3


def test_reject_awaiting_to_rework(tmp_path, capsys):
    _setup(tmp_path)
    rc = main(["--root", str(tmp_path), "reject", "wf", "1", "--note", "missing test"])
    assert rc == 0
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 1)
    assert issue.status == "needs_rework"
    assert "missing test" in issue.verifier_feedback


def test_kill(tmp_path, capsys):
    _setup(tmp_path, status="in_progress")
    # a live pid (this process) with a handle; kill should SIGTERM-attempt + mark blocked
    save_registry(tmp_path / ".orchestra" / "workers.json", {
        issue_key("wf", 1): WorkerHandle(
            project="wf", number=1, role="worker", branch="issue/001-thing",
            worktree=str(tmp_path), pid=os.getpid(), log=str(tmp_path / "l.log"),
            result_file=str(tmp_path / "r.json"), started="t", start_sha="", proc_start="",
        )
    })
    # don't actually kill the test process: use a sleeping child instead
    proc = subprocess.Popen(["sleep", "30"])
    reg = load_registry(tmp_path / ".orchestra" / "workers.json")
    reg[issue_key("wf", 1)].pid = proc.pid
    save_registry(tmp_path / ".orchestra" / "workers.json", reg)

    rc = main(["--root", str(tmp_path), "kill", "wf", "1"])
    assert rc == 0
    assert find_issue(read_queue(tmp_path / "queue" / "wf.md"), 1).status == "blocked"
    assert load_registry(tmp_path / ".orchestra" / "workers.json") == {}
    proc.wait(timeout=5)


def test_retry_merge_recovers_blocked_committed_issue(tmp_path, capsys):
    # issue #006 recovery path: a blocked issue whose worker committed re-drives to merge
    # WITHOUT re-running the worker.
    repo = _setup(tmp_path, status="blocked")
    rc = main(["--root", str(tmp_path), "retry-merge", "wf", "1"])
    assert rc == 0
    assert "done" in subprocess.run(
        ["git", "-C", str(repo), "show", "main:f.txt"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "Status: archived" in (tmp_path / "queue" / "archive" / "wf.md").read_text()


def test_retry_merge_refuses_without_committed_work(tmp_path, capsys):
    repo = _setup(tmp_path, status="blocked")
    _git(repo, "branch", "-D", "issue/001-thing")  # no committed work to merge
    rc = main(["--root", str(tmp_path), "retry-merge", "wf", "1"])
    assert rc == 3
    # unchanged: still blocked, never silently flipped to awaiting_review
    assert find_issue(read_queue(tmp_path / "queue" / "wf.md"), 1).status == "blocked"


def test_retry_merge_refuses_when_not_blocked(tmp_path, capsys):
    _setup(tmp_path, status="awaiting_review")
    rc = main(["--root", str(tmp_path), "retry-merge", "wf", "1"])
    assert rc == 3


def test_retry_merge_reblocks_on_non_calledprocess_error(tmp_path, monkeypatch, capsys):
    # issue #007: a non-CalledProcessError failure after the status flip must re-block
    # loudly, never strand the issue in awaiting_review nor crash the command.
    _setup(tmp_path, status="blocked")

    def boom(*a, **k):
        raise OSError("tmpfs quota exceeded")

    monkeypatch.setattr("orchestra.cli.merge_and_archive", boom)
    rc = main(["--root", str(tmp_path), "retry-merge", "wf", "1"])
    assert rc == 1
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 1)
    assert issue.status == "blocked"
    assert "tmpfs quota exceeded" in issue.blocked_reason
    assert issue.blocked_reason.strip()


def test_retry_merge_reblocks_on_calledprocess_error_without_stderr(tmp_path, monkeypatch, capsys):
    # issue #007: a CalledProcessError whose stderr is None must not AttributeError; it
    # still re-blocks with a non-empty reason via the message/repr fallback.
    _setup(tmp_path, status="blocked")

    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, ["git", "merge"], stderr=None)

    monkeypatch.setattr("orchestra.cli.merge_and_archive", boom)
    rc = main(["--root", str(tmp_path), "retry-merge", "wf", "1"])
    assert rc == 1
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 1)
    assert issue.status == "blocked"
    assert issue.blocked_reason.strip()
