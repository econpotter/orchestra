from __future__ import annotations

import fcntl
import re
import sys
from pathlib import Path

from orchestra.issue import Issue, parse_issue, render_issue


def read_queue(path: str | Path) -> list[Issue]:
    """Parse every issue block in `path`, skipping (loudly, on stderr) any block that fails
    to parse — a single malformed block (e.g. a hand-edited issue with a bad/missing field)
    must not crash dispatch/reconcile/status for the entire workspace; every other project's
    issues still need to be read and acted on."""
    text = Path(path).read_text()
    blocks = re.split(r"(?m)^(?=##\s+#\d+)", text)
    issues: list[Issue] = []
    for raw in blocks:
        block = raw.strip()
        if not block.startswith("## #"):
            continue
        try:
            issues.append(parse_issue(block))
        except ValueError as exc:
            print(f"warning: skipping unparseable issue block in {path}: {exc}", file=sys.stderr)
    return issues


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
