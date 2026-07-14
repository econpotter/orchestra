#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path


def _value(flag: str) -> str:
    return sys.argv[sys.argv.index(flag) + 1]


mode = os.environ.get("ORCHESTRA_FAKE_STRUCTURED_MODE", "success")
sys.stdin.read()
print("fake diagnostic", file=sys.stderr, flush=True)
if mode == "malformed":
    print("not-json", flush=True)
    raise SystemExit(0)
if mode == "sleep":
    time.sleep(30)
print(json.dumps({"type": "thread.started", "thread_id": "fake-thread"}), flush=True)
print(json.dumps({"type": "turn.started"}), flush=True)
if mode in {"dangling_tool", "tool_sleep"}:
    tool = {"type": "command_execution", "command": "slow command"}
    print(json.dumps({"type": "item.started", "item": tool}), flush=True)
    if mode == "tool_sleep":
        time.sleep(2)
        print(json.dumps({"type": "item.completed", "item": tool}), flush=True)
if mode == "error":
    print(json.dumps({"type": "turn.failed", "error": "upstream unavailable"}), flush=True)
    raise SystemExit(1)
result = {
    "schema_version": 1, "outcome": "committed", "decisions": "",
    "failure_category": "", "evidence": "fake evidence", "requires_human": False,
}
Path(_value("--output-last-message")).write_text(json.dumps(result))
print(json.dumps({"type": "item.completed", "item": {
    "type": "agent_message", "text": json.dumps(result),
}}), flush=True)
print(json.dumps({"type": "turn.completed", "usage": {"output_tokens": 1}}), flush=True)
