import json
from pathlib import Path

from orchestra.dashboard import summarize


def _setup(root: Path):
    (root / "queue").mkdir(parents=True)
    (root / "queue" / "wf.md").write_text(
        "## #001 wf: a\nStatus: open\nPriority: 1\nPlan: null\nSpec: null\n"
        "Depends On: null\nRetries: 0\nWorker: null\nAcceptance:\n- [ ] x\n"
        "### Decisions\n### Blocked Reason\n\n"
        "## #002 wf: b\nStatus: awaiting_review\nPriority: 1\nPlan: null\nSpec: null\n"
        "Depends On: null\nRetries: 0\nWorker: null\nAcceptance:\n- [ ] x\n"
        "### Decisions\n### Blocked Reason\n"
    )
    (root / "PROJECTS.md").write_text(
        "# Projects\n\n## wf\n- Path: projects/wf\n- Branch: main\n"
        "- Purpose: t\n- Queue: queue/wf.md\n- Focus: none\n"
    )


def test_summarize_counts_and_running(tmp_path: Path):
    _setup(tmp_path)
    s = summarize(tmp_path)
    assert s["counts"]["open"] == 1
    assert s["counts"]["awaiting_review"] == 1
    assert s["slots_used"] == 0           # no workers.json
    assert s["running"] == []


def test_summarize_running_has_started(tmp_path: Path):
    _setup(tmp_path)
    workers = {
        "wf#001": {
            "project": "wf",
            "number": 1,
            "role": "worker",
            "branch": "issue/001-a",
            "worktree": "/tmp/wt",
            "pid": 99999,
            "attempt_id": "a1",
            "manifest": "/tmp/a1/manifest.json",
            "stdout": "/tmp/a1/stdout.jsonl",
            "stderr": "/tmp/a1/stderr.log",
            "started": "2026-06-27T00:00:00+00:00",
            "start_sha": "abc",
            "proc_start": "abc",
        }
    }
    wf = tmp_path / ".orchestra"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "workers.json").write_text(json.dumps(workers))
    s = summarize(tmp_path)
    assert len(s["running"]) == 1
    assert "started" in s["running"][0]
    assert s["running"][0]["started"] == "2026-06-27T00:00:00+00:00"
