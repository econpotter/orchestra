#!/usr/bin/env python3
"""Codex-protocol fake used by dispatch/reconcile integration tests."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _commit(cwd: Path) -> None:
    (cwd / "fake_work.txt").write_text("work\n")
    subprocess.run(["git", "add", "fake_work.txt"], cwd=cwd, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "fake work"], cwd=cwd, check=True,
                   capture_output=True)


def _role(schema_path: str) -> str:
    outcomes = json.loads(Path(schema_path).read_text())["properties"]["outcome"]["enum"]
    if "committed" in outcomes:
        return "worker"
    if "validated" in outcomes:
        return "validator"
    return "verifier"


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output-schema", required=True)
    parser.add_argument("--output-last-message", required=True)
    args, _ = parser.parse_known_args()
    sys.stdin.read()
    role = _role(args.output_schema)
    defaults = {"worker": "commit", "validator": "validated", "verifier": "accept"}
    mode = os.environ.get("ORCHESTRA_FAKE_MODE", defaults[role])
    if mode == "sleep":
        time.sleep(3)
        mode = defaults[role]

    print(json.dumps({"type": "thread.started", "thread_id": "fake-thread"}), flush=True)
    print(json.dumps({"type": "turn.started"}), flush=True)
    if mode in {"crash", "session_limit"}:
        message = "API usage limit reached" if mode == "session_limit" else "fake crash"
        print(json.dumps({"type": "turn.failed", "error": message}), flush=True)
        return 1

    if role == "worker" and mode == "commit":
        _commit(Path.cwd())
        outcome, decisions = "committed", "fake decision"
    elif role == "validator" and mode == "validated":
        outcome, decisions = "validated", ""
    elif role == "verifier" and mode == "accept":
        outcome, decisions = "accept", ""
    elif role == "verifier" and mode == "reject":
        outcome, decisions = "reject", "retry: fake complaint"
    else:
        outcome, decisions = "blocked", ""
    failed = outcome == "blocked"
    result = {
        "schema_version": 1, "outcome": outcome, "decisions": decisions,
        "failure_category": "needs_human" if failed else "",
        "evidence": "stuck: fake" if failed else "fake evidence",
        "requires_human": failed,
    }
    Path(args.output_last_message).write_text(json.dumps(result))
    print(json.dumps({"type": "item.completed", "item": {
        "type": "agent_message", "text": json.dumps(result),
    }}), flush=True)
    print(json.dumps({"type": "turn.completed", "usage": {"output_tokens": 1}}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
