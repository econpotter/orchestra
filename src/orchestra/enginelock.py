from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def engine_lock(root: Path):
    """Non-blocking exclusive lock serializing engine operations (dispatch/reconcile).

    Yields True if acquired, False if another engine op already holds it — in which case
    the caller should skip (the in-flight run will handle this tick). Prevents a manual
    dispatch/tick run alongside the timer from double-launching the same issue. Closing the
    file descriptor releases the flock.
    """
    (root / ".orchestra").mkdir(parents=True, exist_ok=True)
    fh = open(root / ".orchestra" / "engine.lock", "w")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        fh.close()
