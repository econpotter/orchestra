from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class Attempt:
    attempt_id: str
    directory: Path
    data: dict[str, Any]

    @property
    def path(self) -> Path: return self.directory / "manifest.json"
    @property
    def prompt_path(self) -> Path: return self.directory / "prompt.md"
    @property
    def instructions_path(self) -> Path: return self.directory / "instructions.md"
    @property
    def schema_path(self) -> Path: return self.directory / "result.schema.json"
    @property
    def stdout_path(self) -> Path: return self.directory / "stdout.jsonl"
    @property
    def stderr_path(self) -> Path: return self.directory / "stderr.log"
    @property
    def events_path(self) -> Path: return self.directory / "events.jsonl"
    @property
    def provider_output_path(self) -> Path: return self.directory / "provider-result.json"
    @property
    def canonical_result_path(self) -> Path: return self.directory / "result.json"
    @property
    def process_path(self) -> Path: return self.directory / "process.json"
    @property
    def stop_path(self) -> Path: return self.directory / "stop"


class AttemptStore:
    def __init__(self, root: str | Path):
        self.root = Path(root) / ".orchestra" / "attempts"

    def create(self, *, attempt_id: str, project: str, number: int, role: str,
               harness: str, model: str, worktree: Path, branch: str, start_commit: str,
               prompt: str, instruction_bundle: str, configuration: dict[str, Any],
               capabilities: dict[str, bool], parent_attempt: str | None) -> Attempt:
        directory = self.root / attempt_id
        directory.mkdir(parents=True, exist_ok=False)
        data = {
            "schema_version": 1, "attempt_id": attempt_id, "state": "created",
            "project": project, "number": number, "role": role, "harness": harness,
            "model": model, "harness_version": "", "adapter_version": 1,
            "capabilities": capabilities, "parent_attempt": parent_attempt,
            "session_id": "", "worktree": str(worktree), "branch": branch,
            "start_commit": start_commit, "terminal_commit": "", "pid": 0,
            "proc_start": "", "process_exit": None, "started_at": "",
            "completed_at": "", "terminal_outcome": "", "failure_category": "",
            "failure_evidence": "", "process_signal": None,
            "latest_event": "", "active_tool": "", "active_tool_started_at": "",
            "prompt_sha256": _sha(prompt), "instruction_sha256": _sha(instruction_bundle),
            "effective_prompt_sha256": "", "instruction_policy": "",
            "instruction_sources": [], "delegation_policy": "",
            "execution_envelope_sha256": "", "supervisor_launch_sha256": "",
            "harness_launch_sha256": "", "engine_provenance": {},
            "configuration_sha256": _sha(json.dumps(configuration, sort_keys=True)),
            "configuration": configuration,
            "artifacts": {
                "prompt": "prompt.md", "instructions": "instructions.md",
                "result_schema": "result.schema.json", "stdout": "stdout.jsonl",
                "stderr": "stderr.log", "events": "events.jsonl",
                "provider_output": "provider-result.json", "canonical_result": "result.json",
                "process": "process.json",
            },
        }
        attempt = Attempt(attempt_id, directory, data)
        attempt.prompt_path.write_text(prompt)
        attempt.instructions_path.write_text(instruction_bundle)
        self._save(attempt)
        return attempt

    def load(self, attempt_id: str) -> Attempt:
        directory = self.root / attempt_id
        return Attempt(attempt_id, directory, json.loads((directory / "manifest.json").read_text()))

    def update(self, attempt: Attempt, **changes: Any) -> None:
        attempt.data.update(changes)
        self._save(attempt)

    def append_event(self, attempt: Attempt, event: dict[str, Any]) -> None:
        with attempt.events_path.open("a") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")

    def latest(self, project: str, number: int, role: str) -> Attempt | None:
        found = []
        if not self.root.exists():
            return None
        for path in self.root.glob("*/manifest.json"):
            data = json.loads(path.read_text())
            if (data["project"], data["number"], data["role"]) == (project, number, role):
                found.append(Attempt(data["attempt_id"], path.parent, data))
        return max(found, key=lambda item: (item.data.get("started_at", ""), item.attempt_id),
                   default=None)

    def _save(self, attempt: Attempt) -> None:
        tmp = attempt.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(attempt.data, indent=2, sort_keys=True))
        os.replace(tmp, attempt.path)
