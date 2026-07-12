from pathlib import Path

from orchestra.cli import main
from orchestra.queue import find_issue, read_queue


def _setup(root: Path):
    (root / "queue").mkdir(parents=True)
    (root / "queue" / "wf.md").write_text("")
    (root / "PROJECTS.md").write_text(
        "# Projects\n\n## wf\n- Path: projects/wf\n- Branch: main\n"
        "- Purpose: t\n- Queue: queue/wf.md\n- Focus: none\n"
    )


def test_issue_add_writes_open_issue(tmp_path, capsys):
    _setup(tmp_path)
    rc = main(["--root", str(tmp_path), "issue", "add", "wf", "--force",
               "--title", "add retry", "--plan", "docs/plans/x.md",
               "--priority", "3", "--accept", "retries 5xx", "--accept", "tests green"])
    out = capsys.readouterr().out
    assert rc == 0 and "wf#001" in out
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 1)
    assert issue.status == "open"
    assert issue.title == "add retry"
    assert issue.priority == 3
    assert issue.plan == "docs/plans/x.md"
    assert [a.text for a in issue.acceptance] == ["retries 5xx", "tests green"]


def test_issue_add_depends_on(tmp_path, capsys):
    _setup(tmp_path)
    main(["--root", str(tmp_path), "issue", "add", "wf", "--force",
          "--title", "base", "--accept", "x"])
    rc = main(["--root", str(tmp_path), "issue", "add", "wf", "--force",
               "--title", "dependent", "--accept", "y", "--depends-on", "1"])
    out = capsys.readouterr().out
    assert rc == 0 and "wf#002" in out
    issue = find_issue(read_queue(tmp_path / "queue" / "wf.md"), 2)
    assert issue.depends_on == [1]


def test_issue_add_malformed_depends_on_non_numeric(tmp_path, capsys):
    # A non-numeric segment must produce a clear error and exit 2 — not a raw
    # `ValueError: invalid literal for int()` traceback — and write nothing.
    _setup(tmp_path)
    rc = main(["--root", str(tmp_path), "issue", "add", "wf", "--force",
               "--title", "bad", "--accept", "y", "--depends-on", "1,foo"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "--depends-on" in err and "'foo'" in err
    assert "Traceback" not in err
    assert read_queue(tmp_path / "queue" / "wf.md") == []


def test_issue_add_malformed_depends_on_empty_segment(tmp_path, capsys):
    # An empty segment (stray/trailing comma) is rejected too — not silently dropped.
    _setup(tmp_path)
    rc = main(["--root", str(tmp_path), "issue", "add", "wf", "--force",
               "--title", "bad", "--accept", "y", "--depends-on", "1,,2"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "--depends-on" in err and "empty segment" in err
    assert read_queue(tmp_path / "queue" / "wf.md") == []


def test_issue_add_increments_number(tmp_path, capsys):
    _setup(tmp_path)
    main(["--root", str(tmp_path), "issue", "add", "wf", "--title", "one", "--accept", "x"])
    main(["--root", str(tmp_path), "issue", "add", "wf", "--title", "two", "--accept", "y"])
    nums = [i.number for i in read_queue(tmp_path / "queue" / "wf.md")]
    assert nums == [1, 2]


def test_project_add(tmp_path, capsys):
    _setup(tmp_path)
    rc = main(["--root", str(tmp_path), "project", "add", "newproj",
               "--path", "projects/newproj", "--branch", "dev"])
    assert rc == 0
    text = (tmp_path / "PROJECTS.md").read_text()
    assert "## newproj" in text and "projects/newproj" in text and "dev" in text


def test_issue_add_unregistered_project(tmp_path, capsys):
    _setup(tmp_path)  # registers 'wf' only
    rc = main(["--root", str(tmp_path), "issue", "add", "nonesuch",
               "--title", "x", "--accept", "y"])
    assert rc == 2
    assert not (tmp_path / "queue" / "nonesuch.md").exists()
