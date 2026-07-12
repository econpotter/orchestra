import os

from orchestra.issue import parse_issue
from orchestra.registry import issue_key
from orchestra.selection import (
    pid_alive,
    role_for_issue,
    select_dispatchable,
)


def _issue(num, status, priority=5, depends="null"):
    return parse_issue(
        f"## #{num:03d} wf: t\nStatus: {status}\nPriority: {priority}\n"
        f"Plan: null\nSpec: docs/specs/x.md\nDepends On: {depends}\n"
        f"Retries: 0\nWorker: null\nAcceptance:\n- [ ] do it\n"
        f"### Decisions\n### Blocked Reason\n"
    )


def test_role_routing():
    assert role_for_issue(_issue(1, "open"), set(), set()) == "validator"
    assert role_for_issue(_issue(1, "validated"), set(), set()) == "worker"
    assert role_for_issue(_issue(1, "needs_rework"), set(), set()) == "worker"
    assert role_for_issue(_issue(1, "committed"), set(), set()) == "verifier"


def test_non_routable_statuses_return_none():
    for status in ("in_progress", "awaiting_review", "blocked", "merged", "archived"):
        assert role_for_issue(_issue(1, status), set(), set()) is None


def test_active_handle_blocks_dispatch():
    issue = _issue(7, "validated")
    active = {issue_key("wf", 7)}
    assert role_for_issue(issue, active, set()) is None


def test_unmet_dependency_blocks_dispatch():
    issue = _issue(7, "validated", depends="3")
    assert role_for_issue(issue, set(), done_numbers=set()) is None
    assert role_for_issue(issue, set(), done_numbers={3}) == "worker"


def test_select_respects_slots_and_priority():
    cands = [
        ("wf", _issue(1, "open", priority=9), "validator"),
        ("wf", _issue(2, "validated", priority=1), "worker"),
        ("wf", _issue(3, "open", priority=5), "validator"),
    ]
    chosen = select_dispatchable(cands, free_slots=2)
    assert [i.number for _, i, _ in chosen] == [2, 3]  # priority 1, then 5


def test_select_zero_slots():
    cands = [("wf", _issue(1, "open"), "validator")]
    assert select_dispatchable(cands, free_slots=0) == []


def test_pid_alive_self_true_and_bogus_false():
    assert pid_alive(os.getpid()) is True
    assert pid_alive(2_000_000_000) is False


def test_process_start_time_self_and_bogus():
    from orchestra.selection import process_start_time
    mine = process_start_time(os.getpid())
    assert mine is not None and mine == process_start_time(os.getpid())  # stable
    assert process_start_time(2_000_000_000) is None
