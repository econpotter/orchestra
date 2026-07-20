from __future__ import annotations

from dataclasses import dataclass

from orchestra.harness import RoleResult


@dataclass(frozen=True)
class AttemptEvidence:
    role: str
    new_commit: bool
    result: RoleResult | None
    terminal: str
    failure_category: str
    session_id: str
    resume_capable: bool
    attempts_used: int
    attempts_cap: int
    # Provider-overload (HTTP 529) retries are budgeted SEPARATELY from genuine failures:
    # `attempts_used`/`attempts_cap` count only non-overload attempts, while these count the
    # overload retries in the chain against their own (larger) cap. A brief overload window
    # therefore cannot exhaust the genuine attempt budget and block the issue (orchestra#011).
    overload_attempts: int = 0
    overload_cap: int = 0


@dataclass(frozen=True)
class AttemptDecision:
    action: str
    reason: str = ""


def decide_attempt(evidence: AttemptEvidence) -> AttemptDecision:
    result = evidence.result

    def recovery(category: str, reason: str) -> AttemptDecision:
        if category in {"needs_human", "acceptance_failure", "cancelled",
                        "authentication_failure"}:
            return AttemptDecision("blocked", reason)
        if category == "overloaded":
            # Provider overload (HTTP 529) is transient and self-clearing. Requeue a fresh
            # attempt — deferred to the next scheduler tick, which spaces the retries apart —
            # under a dedicated overload budget, so a brief overload window cannot burn the
            # small genuine-failure attempt cap and block the issue (orchestra#011: two 529s in
            # 3.5 min). Overload retries never count toward attempts_cap.
            if evidence.overload_attempts >= evidence.overload_cap:
                return AttemptDecision("blocked", f"overload retries exhausted: {reason}")
            return AttemptDecision("fresh_attempt", reason)
        if evidence.attempts_used >= evidence.attempts_cap:
            return AttemptDecision("blocked", f"attempt cap exhausted: {reason}")
        if category in {"time_limit", "quota_failure", "upstream_failure"} \
                and evidence.resume_capable and evidence.session_id:
            return AttemptDecision("resume", reason)
        if category in {"quota_failure", "upstream_failure", "harness_failure",
                        "protocol_failure", "tool_observation_failure",
                        "environment_failure", "authentication_expired"}:
            # authentication_expired: a mid-run stale/rotated token (#010). A fresh attempt
            # re-seeds a fresh authenticated per-launch home rather than blocking a human.
            return AttemptDecision("fresh_attempt", reason)
        return AttemptDecision("blocked", reason or f"unrecoverable {category}")

    if evidence.terminal != "success":
        if (evidence.role == "worker" and evidence.new_commit
                and evidence.resume_capable and evidence.session_id
                and evidence.attempts_used < evidence.attempts_cap):
            return AttemptDecision("resume", "finalize committed work after interrupted turn")
        if evidence.role == "worker" and not evidence.new_commit and result is None:
            # Genuine crash: nothing committed beyond the issue base and no result. Recovery
            # still owns the action (resume/fresh_attempt while retries remain, block when
            # exhausted or unrecoverable) — the reason just names the condition when it blocks.
            return recovery(evidence.failure_category, "crash: no new commit and no result")
        return recovery(evidence.failure_category, evidence.terminal)

    if evidence.role == "worker":
        if evidence.new_commit:
            if result is None:
                if (evidence.resume_capable and evidence.session_id
                        and evidence.attempts_used < evidence.attempts_cap):
                    return AttemptDecision("resume", "commit without valid result; finalize")
                return AttemptDecision("blocked", "commit without valid result")
            if result.outcome == "committed":
                return AttemptDecision("committed")
            return AttemptDecision("blocked", "partial commit with blocked result")
        if result and result.outcome == "committed":
            return AttemptDecision("contract_failure", "committed result without commit")
        if result and result.outcome == "blocked":
            return AttemptDecision("blocked", result.evidence)
        return recovery(evidence.failure_category, evidence.terminal)
    if evidence.new_commit:
        return AttemptDecision("contract_failure", f"{evidence.role} changed branch")
    if result is None:
        return recovery(evidence.failure_category, evidence.terminal)
    if result.outcome == "blocked":
        return AttemptDecision("blocked", result.evidence)
    return AttemptDecision(result.outcome)
