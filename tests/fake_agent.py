#!/usr/bin/env python3
# tests/fake_agent.py — simulated agent for deterministic engine tests.
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_MODE = {"worker": "commit", "validator": "validated", "verifier": "accept"}


def _write(result_file: str, result: str, decisions: str = "", blocked_reason: str = "") -> None:
    p = Path(result_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"result": result, "decisions": decisions, "blocked_reason": blocked_reason}
    ))


def _commit(cwd: Path) -> None:
    fname = cwd / "fake_work.txt"
    fname.write_text("work\n")
    subprocess.run(["git", "-C", str(cwd), "add", "fake_work.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(cwd), "commit", "-m", "fake work"], check=True, capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", required=True)
    ap.add_argument("--result-file", required=True)
    args = ap.parse_args()
    sys.stdin.read()  # drain the piped prompt

    mode = os.environ.get("ORCHESTRA_FAKE_MODE") or DEFAULT_MODE[args.role]
    if mode == "sleep":
        time.sleep(3)
        mode = DEFAULT_MODE[args.role]

    cwd = Path.cwd()
    if args.role == "worker":
        if mode == "commit":
            _commit(cwd)
            _write(args.result_file, "committed", decisions="fake decision")
        elif mode == "blocked":
            _write(args.result_file, "blocked", blocked_reason="stuck: fake")
        elif mode == "crash":
            return 1
        elif mode == "session_limit":
            print("API session limit reached", file=sys.stderr)
            return 1
    elif args.role == "validator":
        if mode == "validated":
            _write(args.result_file, "validated")
        elif mode == "blocked":
            _write(args.result_file, "blocked", blocked_reason="invalid: fake")
        else:
            raise ValueError(f"unknown mode {mode!r} for role validator")
    elif args.role == "verifier":
        if mode == "accept":
            _write(args.result_file, "accept")
        elif mode == "reject":
            _write(args.result_file, "reject", decisions="retry: fake complaint")
        elif mode == "crash":
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
