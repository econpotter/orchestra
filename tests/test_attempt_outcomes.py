import pytest

from orchestra.harness import RoleResult
from orchestra.outcome import AttemptEvidence, decide_attempt


@pytest.mark.parametrize(
    ("new_commit", "result", "action"),
    [
        (True, RoleResult(1, "committed", "", "", "", False), "committed"),
        (True, RoleResult(1, "blocked", "", "needs_human", "why", True), "blocked"),
        (True, None, "resume"),
        (False, RoleResult(1, "committed", "", "", "", False), "contract_failure"),
        (False, RoleResult(1, "blocked", "", "needs_human", "still running", True), "blocked"),
        (False, None, "fresh_attempt"),
    ],
)
def test_worker_truth_table(new_commit, result, action):
    evidence = AttemptEvidence(
        role="worker", new_commit=new_commit, result=result,
        terminal="success" if result else "turn_failed",
        failure_category="protocol_failure" if result is None else "",
        session_id="session-1", resume_capable=True, attempts_used=1, attempts_cap=2,
    )
    assert decide_attempt(evidence).action == action


def test_acceptance_and_human_failures_never_retry():
    for category in ("acceptance_failure", "needs_human", "cancelled"):
        result = RoleResult(1, "blocked", "", category, "evidence", True)
        evidence = AttemptEvidence("worker", False, result, "turn_failed", category,
                                   "session", True, 1, 3)
        assert decide_attempt(evidence).action == "blocked"


def test_resume_is_bounded_and_requires_session_capability():
    base = dict(role="worker", new_commit=False, result=None, terminal="turn_failed",
                failure_category="time_limit", session_id="s", resume_capable=True)
    assert decide_attempt(AttemptEvidence(attempts_used=1, attempts_cap=2, **base)).action == "resume"
    assert decide_attempt(AttemptEvidence(attempts_used=2, attempts_cap=2, **base)).action == "blocked"
    assert decide_attempt(AttemptEvidence(attempts_used=1, attempts_cap=2,
                                          **{**base, "session_id": ""})).action == "blocked"


def test_model_cannot_make_its_own_block_retryable():
    result = RoleResult(1, "blocked", "", "time_limit", "model says retry", False)
    evidence = AttemptEvidence("worker", False, result, "success", "", "s", True, 1, 3)
    assert decide_attempt(evidence).action == "blocked"
