from pathlib import Path

from orchestra.cli import main


def _setup(root: Path):
    (root / "queue").mkdir(parents=True)
    (root / "queue" / "wf.md").write_text(
        "## #001 wf: thing\nStatus: awaiting_review\nPriority: 1\nPlan: null\nSpec: null\n"
        "Depends On: null\nRetries: 0\nWorker: null\nAcceptance:\n- [x] x\n"
        "### Decisions\n### Blocked Reason\n"
    )
    (root / "PROJECTS.md").write_text(
        "# Projects\n\n## wf\n- Path: projects/wf\n- Branch: main\n"
        "- Purpose: t\n- Queue: queue/wf.md\n- Focus: none\n"
    )


def test_pause_resume_sentinel(tmp_path, capsys):
    _setup(tmp_path)
    assert main(["--root", str(tmp_path), "pause"]) == 0
    assert (tmp_path / ".orchestra" / "paused").exists()
    assert main(["--root", str(tmp_path), "resume"]) == 0
    assert not (tmp_path / ".orchestra" / "paused").exists()


def test_logs_prints_file(tmp_path, capsys):
    _setup(tmp_path)
    log = tmp_path / ".orchestra" / "logs" / "wf#001.log"
    log.parent.mkdir(parents=True)
    log.write_text("hello-log\n")
    rc = main(["--root", str(tmp_path), "logs", "wf", "1"])
    assert rc == 0
    assert "hello-log" in capsys.readouterr().out
