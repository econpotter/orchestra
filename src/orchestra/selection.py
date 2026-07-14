from __future__ import annotations

import os
from typing import TypeVar

from orchestra.issue import Issue
from orchestra.registry import issue_key

ROLE_FOR_STATUS: dict[str, str] = {
    "open": "validator",
    "validated": "worker",
    "needs_rework": "worker",
    "committed": "verifier",
}

ProjectType = TypeVar("ProjectType")


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        stat = open(f"/proc/{pid}/stat").read()
        state = stat[stat.rindex(")") + 1:].split()[0]
    except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError, IndexError):
        return False
    return state != "Z"


def process_start_time(pid: int) -> str | None:
    """Process start time (jiffies since boot) from /proc/<pid>/stat field 22.

    Used to distinguish a still-running worker from a recycled pid. Returns
    None if the process is gone or /proc is unreadable.
    """
    try:
        stat = open(f"/proc/{pid}/stat").read()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    # The comm field (field 2) is parenthesized and may contain spaces/parens,
    # so split on the LAST ')'. Fields after it start at field 3 (state); the
    # start time is field 22 -> index 19 in the post-')' split.
    try:
        after = stat[stat.rindex(")") + 1:].split()
        return after[19]
    except (ValueError, IndexError):
        return None


def worker_alive(handle) -> bool:
    """Return supervisor liveness while guarding against PID reuse."""
    return pid_alive(handle.pid) and (
        handle.proc_start == "" or process_start_time(handle.pid) == handle.proc_start
    )


def role_for_issue(
    issue: Issue, active_keys: set[str], done_numbers: set[int]
) -> str | None:
    role = ROLE_FOR_STATUS.get(issue.status)
    if role is None:
        return None
    if issue_key(issue.project, issue.number) in active_keys:
        return None
    if any(dep not in done_numbers for dep in issue.depends_on):
        return None
    return role


def select_dispatchable(
    candidates: list[tuple[ProjectType, Issue, str]], free_slots: int
) -> list[tuple[ProjectType, Issue, str]]:
    if free_slots <= 0:
        return []
    ordered = sorted(candidates, key=lambda c: (c[1].priority, c[1].number))
    return ordered[:free_slots]
