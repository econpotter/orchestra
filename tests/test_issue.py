import pytest

from orchestra.issue import block_issue, branch_name, parse_issue, render_issue

BLOCK = """\
## #042 weather-api: add API retry
Status: open
Priority: 3
Plan: projects/weather-api/docs/plans/api-resilience.md#retry
Spec: projects/weather-api/docs/specs/2026-06-api-resilience.md
Depends On: 12, 15
Retries: 0
Worker: null
Acceptance:
- [ ] client retries 5xx with exponential backoff, max 3
- [x] covered by tests; existing suite green
### Decisions
chose backoff base=2s
### Blocked Reason
"""


def test_parse_issue():
    issue = parse_issue(BLOCK)
    assert issue.number == 42
    assert issue.project == "weather-api"
    assert issue.title == "add API retry"
    assert issue.status == "open"
    assert issue.priority == 3
    assert issue.plan.endswith("api-resilience.md#retry")
    assert issue.depends_on == [12, 15]
    assert issue.worker is None
    assert len(issue.acceptance) == 2
    assert issue.acceptance[0].checked is False
    assert issue.acceptance[1].checked is True
    assert issue.decisions == "chose backoff base=2s"
    assert issue.blocked_reason == ""


def test_round_trip():
    issue = parse_issue(BLOCK)
    reparsed = parse_issue(render_issue(issue))
    assert reparsed == issue


def test_null_optionals():
    issue = parse_issue(BLOCK)
    issue.plan = None
    issue.spec = None
    issue.depends_on = []
    rendered = render_issue(issue)
    assert "Plan: null" in rendered
    assert "Depends On: null" in rendered
    assert parse_issue(rendered).depends_on == []


def test_branch_name():
    assert branch_name(parse_issue(BLOCK)) == "issue/042-add-api-retry"


# Fix A: missing Priority raises ValueError
def test_parse_issue_missing_priority_raises():
    block_no_priority = """\
## #007 wf: missing priority
Status: open
Plan: null
Spec: null
Depends On: null
Retries: 0
Worker: null
Acceptance:
### Decisions
### Blocked Reason
"""
    with pytest.raises(ValueError):
        parse_issue(block_no_priority)


# Fix C: round-trip with non-empty blocked_reason
def test_round_trip_blocked_reason():
    block = """\
## #010 wf: blocked issue
Status: blocked
Priority: 2
Plan: null
Spec: docs/specs/z.md
Depends On: null
Retries: 1
Worker: agent-1
Acceptance:
- [ ] unblock first
### Decisions
### Blocked Reason
waiting for upstream API to stabilize
"""
    issue = parse_issue(block)
    assert issue.blocked_reason == "waiting for upstream API to stabilize"
    reparsed = parse_issue(render_issue(issue))
    assert reparsed == issue


# Fix C: round-trip with multi-line decisions body
def test_round_trip_multiline_decisions():
    block = """\
## #011 wf: decisions issue
Status: open
Priority: 1
Plan: null
Spec: null
Depends On: null
Retries: 0
Worker: null
Acceptance:
- [x] reviewed
### Decisions
chose option A because it was faster
also rejected option B due to cost
### Blocked Reason
"""
    issue = parse_issue(block)
    assert "chose option A" in issue.decisions
    assert "also rejected option B" in issue.decisions
    reparsed = parse_issue(render_issue(issue))
    assert reparsed == issue


# Fix C: round-trip with zero acceptance items
def test_round_trip_zero_acceptance():
    block = """\
## #012 wf: no acceptance
Status: open
Priority: 1
Plan: null
Spec: null
Depends On: null
Retries: 0
Worker: null
Acceptance:
### Decisions
### Blocked Reason
"""
    issue = parse_issue(block)
    assert issue.acceptance == []
    reparsed = parse_issue(render_issue(issue))
    assert reparsed == issue


def test_verifier_feedback_round_trip():
    from orchestra.issue import parse_issue, render_issue
    block = """\
## #050 wf: thing
Status: needs_rework
Priority: 2
Plan: null
Spec: docs/specs/x.md
Depends On: null
Retries: 1
Worker: null
Acceptance:
- [ ] do it
### Decisions
### Blocked Reason
### Verifier Feedback
retry: missing test for the 5xx path
"""
    issue = parse_issue(block)
    assert issue.verifier_feedback == "retry: missing test for the 5xx path"
    assert parse_issue(render_issue(issue)) == issue


def test_crash_retries_and_network_round_trip():
    block = """\
## #060 wf: netjob
Status: open
Priority: 2
Plan: null
Spec: null
Depends On: null
Network: true
Retries: 0
Crash-Retries: 1
Worker: null
Acceptance:
- [ ] fetch data
### Decisions
### Blocked Reason
"""
    issue = parse_issue(block)
    assert issue.network is True
    assert issue.crash_retries == 1
    rendered = render_issue(issue)
    assert "Network: true" in rendered
    assert "Crash-Retries: 1" in rendered
    assert parse_issue(rendered) == issue


def test_legacy_block_defaults_new_fields():
    # A legacy block without Network/Crash-Retries parses to safe defaults and round-trips.
    issue = parse_issue(BLOCK)
    assert issue.network is False
    assert issue.crash_retries == 0
    assert parse_issue(render_issue(issue)) == issue


def test_verifier_feedback_defaults_empty():
    from orchestra.issue import parse_issue
    block = """\
## #051 wf: thing
Status: open
Priority: 2
Plan: null
Spec: docs/specs/x.md
Depends On: null
Retries: 0
Worker: null
Acceptance:
- [ ] do it
### Decisions
### Blocked Reason
"""
    assert parse_issue(block).verifier_feedback == ""


def _mk(status="awaiting_review", blocked_reason=""):
    issue = parse_issue(BLOCK)
    issue.status = status
    issue.blocked_reason = blocked_reason
    return issue


def test_block_issue_records_reason():
    issue = _mk()
    block_issue(issue, "  autoapprove: merge failed after retry: quota  ")
    assert issue.status == "blocked"
    assert issue.blocked_reason == "autoapprove: merge failed after retry: quota"  # trimmed


def test_block_issue_empty_reason_is_impossible_by_construction(capsys):
    # issue #006: a block with no reason is a bug — fail loud, never store a blank.
    issue = _mk()
    block_issue(issue, "")
    assert issue.status == "blocked"
    assert issue.blocked_reason.strip() != ""
    assert "fail-loud fallback" in issue.blocked_reason
    assert "empty reason" in capsys.readouterr().err


def test_block_issue_whitespace_reason_is_treated_as_empty():
    issue = _mk()
    block_issue(issue, "   \n  ")
    assert issue.status == "blocked"
    assert issue.blocked_reason.strip() != ""
