from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from orchestra import layout
from orchestra.projects import read_projects
from orchestra.queue import read_queue
from orchestra.registry import load_registry
from orchestra.selection import worker_alive


def summarize(root: str | Path) -> dict:
    root = Path(root)
    counts: Counter[str] = Counter()
    projects_file = root / "PROJECTS.md"
    if projects_file.exists():
        for project in read_projects(projects_file):
            qf = layout.queue_file(root, project.name)
            if qf.exists():
                for issue in read_queue(qf):
                    counts[issue.status] += 1

    reg = load_registry(root / ".orchestra" / "workers.json")
    running = [
        {
            "key": key,
            "project": h.project,
            "number": h.number,
            "role": h.role,
            "pid": h.pid,
            "alive": worker_alive(h),
            "started": h.started,
        }
        for key, h in reg.items()
    ]
    return {
        "slots_used": len(reg),
        "running": running,
        "counts": dict(counts),
        "auth_refresh": _auth_refresh(root),
    }


def _auth_refresh(root: Path) -> dict:
    """The last central-refresh event per harness, as dispatch recorded it.

    A held or failed refresh is why launches of that harness stalled, so it belongs in
    status rather than only in a dispatch log line the operator never sees.
    """
    path = root / ".orchestra" / "auth-refresh.json"
    try:
        records = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return records if isinstance(records, dict) else {}
