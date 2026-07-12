from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Result:
    result: str
    decisions: str = ""
    blocked_reason: str = ""


def write_result(path: str | Path, result: Result) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(result)))


def read_result(path: str | Path) -> Result | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return Result(
            result=str(data["result"]),
            decisions=str(data.get("decisions", "")),
            blocked_reason=str(data.get("blocked_reason", "")),
        )
    except (ValueError, KeyError, TypeError):
        return None
