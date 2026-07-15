from pathlib import Path

from orchestra.cli import main
from orchestra.queue import read_queue


def _setup(root: Path):
    (root / "queue").mkdir(parents=True)
    (root / "queue" / "wf.md").write_text("")
    (root / "PROJECTS.md").write_text(
        "# Projects\n\n## wf\n- Path: projects/wf\n- Branch: main\n"
        "- Purpose: t\n- Queue: queue/wf.md\n- Focus: none\n"
    )
    plan = root / "plan.md"
    plan.write_text("# P\n\n## Task 1: a\nx\n\n## Task 2: b\ny\n")
    return plan


def test_from_plan_dry_run_writes_nothing(tmp_path, capsys):
    plan = _setup(tmp_path)
    rc = main(["--root", str(tmp_path), "issue", "add", "wf", "--from-plan", str(plan)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Task 1: a" in out and "Task 2: b" in out
    assert read_queue(tmp_path / "queue" / "wf.md") == []   # dry-run: nothing written


def test_from_plan_apply_writes_issues(tmp_path, capsys):
    plan = _setup(tmp_path)
    rc = main(["--root", str(tmp_path), "issue", "add", "wf", "--from-plan", str(plan), "--apply", "--force"])
    assert rc == 0
    issues = read_queue(tmp_path / "queue" / "wf.md")
    assert [i.title for i in issues] == ["Task 1: a", "Task 2: b"]
    assert all(i.status == "open" for i in issues)


def test_from_plan_apply_supports_held_network_issues(tmp_path, capsys):
    plan = _setup(tmp_path)
    rc = main([
        "--root", str(tmp_path), "issue", "add", "wf",
        "--from-plan", str(plan), "--apply", "--force", "--held", "--network",
    ])
    assert rc == 0
    issues = read_queue(tmp_path / "queue" / "wf.md")
    assert all(issue.status == "held" for issue in issues)
    assert all(issue.network is True for issue in issues)
    assert all(issue.network_approved is False for issue in issues)
