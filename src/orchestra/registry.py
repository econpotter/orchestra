from __future__ import annotations

import fcntl
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def issue_key(project: str, number: int) -> str:
    return f"{project}#{number:03d}"


@dataclass
class WorkerHandle:
    project: str
    number: int
    role: str
    branch: str
    worktree: str
    pid: int
    log: str
    result_file: str
    started: str
    start_sha: str
    proc_start: str
    completion_file: str = ""
    stop_file: str = ""


def load_registry(path: str | Path) -> dict[str, WorkerHandle]:
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text())
    return {k: WorkerHandle(**v) for k, v in data.items()}


def save_registry(path: str | Path, handles: dict[str, WorkerHandle]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({k: asdict(v) for k, v in handles.items()}, indent=2)
    lock_path = p.with_suffix(p.suffix + ".lock")
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(payload)
        os.replace(tmp, p)
