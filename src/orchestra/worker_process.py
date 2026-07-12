from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def run(completion_path: Path, stop_path: Path | None, argv: list[str]) -> int:
    completion_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(argv, stdin=sys.stdin, start_new_session=True)
    stopped = False
    try:
        while proc.poll() is None:
            if stop_path is not None and stop_path.exists():
                stopped = True
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            time.sleep(0.2)
        returncode = proc.returncode
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            returncode = proc.wait()
        tmp = completion_path.with_suffix(completion_path.suffix + ".tmp")
        tmp.write_text(json.dumps({"returncode": returncode, "stopped": stopped}))
        os.replace(tmp, completion_path)
    return returncode


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: worker_process COMPLETION STOP ARGV_JSON")
    completion = Path(sys.argv[1])
    stop = Path(sys.argv[2]) if sys.argv[2] else None
    argv = json.loads(sys.argv[3])
    if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
        raise SystemExit("ARGV_JSON must be a list of strings")
    return run(completion, stop, argv)


if __name__ == "__main__":
    raise SystemExit(main())
