"""Queue-time plan/spec-on-base validation (Fix 4) and the `release` command (Fix 3)."""
import os
import subprocess
from pathlib import Path

import pytest

from orchestra.cli import main
from orchestra.config import load_config
from orchestra.queue import find_issue, read_queue, write_queue
from orchestra.reconcile import reconcile
from orchestra.registry import WorkerHandle, save_registry
from orchestra.issue import AcceptanceItem, Issue


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _setup(root: Path, *, commit_plan: bool):
    (root / "queue").mkdir(parents=True)
    (root / "queue" / "wf.md").write_text("")
    (root / "PROJECTS.md").write_text(
        "# Projects\n\n## wf\n- Path: projects/wf\n- Branch: main\n"
        "- Purpose: t\n- Queue: queue/wf.md\n- Focus: none\n"
    )
    repo = root / "projects" / "wf"
    (repo / "docs" / "plans").mkdir(parents=True)
    (repo / "docs" / "plans" / "p.md").write_text("# plan\n")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    if commit_plan:
        _git(repo, "add", "-A")
    else:
        (repo / "README.md").write_text("x\n")
        _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")


def test_add_refuses_plan_absent_from_base(tmp_path, capsys):
    _setup(tmp_path, commit_plan=False)
    rc = main(["--root", str(tmp_path), "issue", "add", "wf",
               "--title", "t", "--plan", "docs/plans/p.md", "--accept", "x"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "base branch" in err
    assert read_queue(tmp_path / "queue" / "wf.md") == []  # not added


def test_add_force_warns_and_adds(tmp_path, capsys):
    _setup(tmp_path, commit_plan=False)
    rc = main(["--root", str(tmp_path), "issue", "add", "wf", "--force",
               "--title", "t", "--plan", "docs/plans/p.md", "--accept", "x"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "warning" in err.lower()
    assert len(read_queue(tmp_path / "queue" / "wf.md")) == 1


def test_add_clean_when_plan_committed(tmp_path, capsys):
    _setup(tmp_path, commit_plan=True)
    rc = main(["--root", str(tmp_path), "issue", "add", "wf",
               "--title", "t", "--plan", "docs/plans/p.md#anchor", "--accept", "x"])
    assert rc == 0
    assert len(read_queue(tmp_path / "queue" / "wf.md")) == 1


def _held_issue(root: Path):
    qf = root / "queue" / "wf.md"
    issue = Issue(
        number=1, project="wf", title="netjob", status="held", priority=5,
        plan=None, spec=None, depends_on=[], retries=0, worker=None,
        acceptance=[AcceptanceItem(checked=False, text="fetch")], decisions="",
        blocked_reason="", verifier_feedback="", network=True,
    )
    write_queue(qf, [issue])
    return qf


def test_release_reopens_held_and_approves_network(tmp_path, capsys):
    _setup(tmp_path, commit_plan=True)
    qf = _held_issue(tmp_path)
    rc = main(["--root", str(tmp_path), "release", "wf", "1"])
    assert rc == 0
    issue = find_issue(read_queue(qf), 1)
    assert issue.status == "open"
    assert issue.network_approved is True


def test_release_refuses_non_held(tmp_path, capsys):
    _setup(tmp_path, commit_plan=True)
    qf = _held_issue(tmp_path)
    issue = find_issue(read_queue(qf), 1)
    issue.status = "validated"
    write_queue(qf, [issue])
    rc = main(["--root", str(tmp_path), "release", "wf", "1"])
    err = capsys.readouterr().err
    assert rc == 3
    assert "held" in err
    assert find_issue(read_queue(qf), 1).status == "validated"  # unchanged


def test_release_under_network_policy_validates_without_reholding(tmp_path, capsys):
    _setup(tmp_path, commit_plan=True)
    qf = _held_issue(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "slots: 0\nroles: {}\nhold_network_issues: true\n"
    )

    assert main(["--root", str(tmp_path), "release", "wf", "1"]) == 0
    reconcile(tmp_path, load_config(tmp_path / "config.yaml"))

    issue = find_issue(read_queue(qf), 1)
    assert issue.status == "validated"
    assert issue.network_approved is True


def test_add_supports_held_and_network_flags(tmp_path, capsys):
    _setup(tmp_path, commit_plan=True)
    assert main([
        "--root", str(tmp_path), "issue", "add", "wf", "--title", "held netjob",
        "--accept", "fetch", "--held", "--network",
    ]) == 0
    issue = read_queue(tmp_path / "queue" / "wf.md")[0]
    assert issue.status == "held"
    assert issue.network is True
    assert issue.network_approved is False


@pytest.mark.parametrize("status", ["open", "validated", "needs_rework", "blocked"])
def test_hold_accepts_inactive_preworker_states(tmp_path, capsys, status):
    _setup(tmp_path, commit_plan=True)
    qf = _held_issue(tmp_path)
    issue = find_issue(read_queue(qf), 1)
    issue.status = status
    issue.network_approved = True
    issue.blocked_reason = "prior failure" if status == "blocked" else ""
    write_queue(qf, [issue])

    assert main(["--root", str(tmp_path), "hold", "wf", "1"]) == 0
    held = find_issue(read_queue(qf), 1)
    assert held.status == "held"
    assert held.network_approved is False
    assert held.blocked_reason == ""
    if status == "blocked":
        assert "prior failure" in held.decisions


@pytest.mark.parametrize(
    "status", ["in_progress", "committed", "awaiting_review", "archived"]
)
def test_hold_refuses_active_or_completed_states(tmp_path, capsys, status):
    _setup(tmp_path, commit_plan=True)
    qf = _held_issue(tmp_path)
    issue = find_issue(read_queue(qf), 1)
    issue.status = status
    write_queue(qf, [issue])

    assert main(["--root", str(tmp_path), "hold", "wf", "1"]) == 3
    assert find_issue(read_queue(qf), 1).status == status


def test_hold_refuses_issue_with_registered_attempt(tmp_path, capsys):
    _setup(tmp_path, commit_plan=True)
    qf = _held_issue(tmp_path)
    issue = find_issue(read_queue(qf), 1)
    issue.status = "open"
    write_queue(qf, [issue])
    save_registry(tmp_path / ".orchestra" / "workers.json", {
        "wf#001": WorkerHandle(
            project="wf", number=1, role="validator", branch="", worktree="",
            pid=os.getpid(), attempt_id="attempt", manifest="", stdout="", stderr="",
            started="now", start_sha="", proc_start="",
        ),
    })

    assert main(["--root", str(tmp_path), "hold", "wf", "1"]) == 3
    assert "kill and reconcile" in capsys.readouterr().err
    assert find_issue(read_queue(qf), 1).status == "open"
