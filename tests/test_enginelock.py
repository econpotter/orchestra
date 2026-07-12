from pathlib import Path

from orchestra.enginelock import engine_lock


def test_engine_lock_is_exclusive(tmp_path: Path):
    with engine_lock(tmp_path) as outer:
        assert outer is True
        with engine_lock(tmp_path) as inner:
            assert inner is False  # second acquirer is told to skip
    # released → acquirable again
    with engine_lock(tmp_path) as again:
        assert again is True
