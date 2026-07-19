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
