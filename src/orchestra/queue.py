from __future__ import annotations

import fcntl
import re
from pathlib import Path

from orchestra.issue import Issue, parse_issue, render_issue


def read_queue(path: str | Path) -> list[Issue]:
    text = Path(path).read_text()
    blocks = re.split(r"(?m)^(?=##\s+#\d+)", text)
    return [parse_issue(b.strip()) for b in blocks if b.strip().startswith("## #")]


def write_queue(path: str | Path, issues: list[Issue]) -> None:
    path = Path(path)
    lock_path = path.with_suffix(path.suffix + ".lock")
    body = "\n\n".join(render_issue(i) for i in issues)
    with open(lock_path, "w") as _lock:
        fcntl.flock(_lock, fcntl.LOCK_EX)
        path.write_text(body + "\n" if body else "")


def find_issue(issues: list[Issue], number: int) -> Issue | None:
    for issue in issues:
        if issue.number == number:
            return issue
    return None


def next_number(active: list[Issue], archived: list[Issue]) -> int:
    nums = [i.number for i in active] + [i.number for i in archived]
    return (max(nums) + 1) if nums else 1
