from pathlib import Path

from orchestra.result import Result, read_result, write_result


def test_write_then_read(tmp_path: Path):
    p = tmp_path / "nested" / "r.json"
    write_result(p, Result(result="committed", decisions="chose backoff=2s"))
    got = read_result(p)
    assert got == Result(result="committed", decisions="chose backoff=2s", blocked_reason="")


def test_read_missing_returns_none(tmp_path: Path):
    assert read_result(tmp_path / "absent.json") is None


def test_read_garbage_returns_none(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    assert read_result(p) is None
