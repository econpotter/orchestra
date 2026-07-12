"""Queue-time plan/spec-on-base validation (Fix 4) and the `release` command (Fix 3)."""
import subprocess
from pathlib import Path

from orchestra.cli import main
from orchestra.queue import find_issue, read_queue, write_queue
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


def test_release_promotes_held_to_validated(tmp_path, capsys):
    _setup(tmp_path, commit_plan=True)
    qf = _held_issue(tmp_path)
    rc = main(["--root", str(tmp_path), "release", "wf", "1"])
    assert rc == 0
    assert find_issue(read_queue(qf), 1).status == "validated"


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
