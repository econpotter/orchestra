import json
from pathlib import Path

from orchestra.cli import main


def _setup(root: Path):
    (root / "queue").mkdir(parents=True)
    (root / "queue" / "wf.md").write_text(
        "## #001 wf: alpha\nStatus: open\nPriority: 1\nPlan: null\nSpec: null\n"
        "Depends On: null\nRetries: 0\nWorker: null\nAcceptance:\n- [ ] do x\n"
        "### Decisions\nchose y\n### Blocked Reason\n"
    )
    (root / "PROJECTS.md").write_text(
        "# Projects\n\n## wf\n- Path: projects/wf\n- Branch: main\n"
        "- Purpose: t\n- Queue: queue/wf.md\n- Focus: none\n"
    )


def test_issue_list_json(tmp_path, capsys):
    _setup(tmp_path)
    rc = main(["--root", str(tmp_path), "issue", "list", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data[0]["number"] == 1 and data[0]["status"] == "open"


def test_issue_list_filter_status(tmp_path, capsys):
    _setup(tmp_path)
    rc = main(["--root", str(tmp_path), "issue", "list", "--status", "blocked", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_issue_show_includes_decisions(tmp_path, capsys):
    _setup(tmp_path)
    rc = main(["--root", str(tmp_path), "issue", "show", "wf", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "chose y" in out and "issue/001-alpha" in out


def test_issue_show_json_includes_network_approval(tmp_path, capsys):
    _setup(tmp_path)
    queue = tmp_path / "queue" / "wf.md"
    queue.write_text(queue.read_text().replace(
        "Depends On: null\n", "Depends On: null\nNetwork: true\nNetwork-Approved: true\n"
    ))

    assert main(["--root", str(tmp_path), "issue", "show", "wf", "1", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["network"] is True
    assert info["network_approved"] is True


def test_issue_show_includes_project_worktree_seeds(tmp_path, capsys):
    _setup(tmp_path)
    projects_path = tmp_path / "PROJECTS.md"
    projects_path.write_text(
        projects_path.read_text().replace(
            "- Focus: none\n",
            "- Worktree-Seed: data/raw:ro-link, fixtures:copy\n- Focus: none\n",
        )
    )

    rc = main(["--root", str(tmp_path), "issue", "show", "wf", "1", "--json"])

    assert rc == 0
    info = json.loads(capsys.readouterr().out)
    assert info["worktree_seed"] == [
        {"path": "data/raw", "mode": "ro-link"},
        {"path": "fixtures", "mode": "copy"},
    ]


def test_status_json(tmp_path, capsys):
    _setup(tmp_path)
    rc = main(["--root", str(tmp_path), "status", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["counts"]["open"] == 1
    assert data["issues"] == [
        {
            "project": "wf",
            "number": 1,
            "status": "open",
            "priority": 1,
            "title": "alpha",
            "blocked_deps": [],
        }
    ]


def test_status_text_includes_issue_rows(tmp_path, capsys):
    _setup(tmp_path)
    rc = main(["--root", str(tmp_path), "status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "slots used: 0" in out
    assert "counts: open=1" in out
    assert "wf#001  open" in out
