from pathlib import Path

from orchestra.registry import WorkerHandle, issue_key, load_registry, save_registry


def _handle(n: int) -> WorkerHandle:
    return WorkerHandle(
        project="wf", number=n, role="worker", branch=f"issue/{n:03d}-x",
        worktree=f".orchestra/worktrees/wf-{n:03d}", pid=1000 + n,
        log=f".orchestra/logs/wf#{n:03d}.log",
        result_file=f".orchestra/results/wf#{n:03d}.json", started="2026-06-26T00:00:00Z",
        start_sha="abc123",
        proc_start="999",
    )


def test_issue_key():
    assert issue_key("weather-api", 42) == "weather-api#042"


def test_load_missing_is_empty(tmp_path: Path):
    assert load_registry(tmp_path / "workers.json") == {}


def test_save_then_load_round_trip(tmp_path: Path):
    p = tmp_path / "sub" / "workers.json"
    handles = {issue_key("wf", 1): _handle(1), issue_key("wf", 2): _handle(2)}
    save_registry(p, handles)
    assert load_registry(p) == handles


def test_save_is_atomic_no_tmp_left(tmp_path):
    p = tmp_path / "workers.json"
    handles = {issue_key("wf", 1): _handle(1)}
    save_registry(p, handles)
    assert load_registry(p) == handles
    # no stray temp files left in the directory
    leftovers = [f.name for f in tmp_path.iterdir() if f.name.endswith(".tmp")]
    assert leftovers == []
