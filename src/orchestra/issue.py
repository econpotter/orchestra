from __future__ import annotations

import re
import sys
from dataclasses import dataclass

_HEADER_RE = re.compile(r"^##\s+#(\d+)\s+([A-Za-z0-9_-]+):\s+(.+?)\s*$")
_NULLS = {"", "null", "none"}
_TRUE = {"true", "yes", "1", "on"}


@dataclass
class AcceptanceItem:
    checked: bool
    text: str


@dataclass
class Issue:
    number: int
    project: str
    title: str
    status: str
    priority: int
    plan: str | None
    spec: str | None
    depends_on: list[int]
    retries: int
    worker: str | None
    acceptance: list[AcceptanceItem]
    decisions: str
    blocked_reason: str
    verifier_feedback: str
    crash_retries: int = 0
    network: bool = False
    network_approved: bool = False


def needs_network_approval(issue: Issue, hold_network_issues: bool) -> bool:
    """Return whether an open network issue must stop before validation."""
    return (
        hold_network_issues
        and issue.status == "open"
        and issue.network
        and not issue.network_approved
    )


def block_issue(issue: Issue, reason: str) -> None:
    """Transition an issue to blocked with a GUARANTEED non-empty reason.

    A blocked issue with no reason is undebuggable — the operator sees a stuck issue and no
    trace of why (issue #006: the autoapprove merge step died and parked issues `blocked`
    with an EMPTY Blocked Reason). Blocking with a blank reason is therefore a bug: rather
    than store it, we substitute a loud sentinel and warn on stderr, so empty-reason blocking
    is impossible by construction. Route EVERY block transition through here."""
    reason = (reason or "").strip()
    if not reason:
        reason = "blocked with no reason recorded — fail-loud fallback (please report as a bug)"
        print(
            f"warning: issue #{issue.number} blocked with an empty reason; "
            "recording fallback sentinel",
            file=sys.stderr,
        )
    issue.status = "blocked"
    issue.blocked_reason = reason


def exception_detail(exc: BaseException) -> str:
    """Never-empty, human-readable detail for a caught exception. Prefers a subprocess's
    stderr, then the exception message, then its repr — so a Blocked Reason always names the
    failing step even when stderr is None (a `CalledProcessError` with `stderr=None`) or the
    exception is not a subprocess error at all (issue #006/#007)."""
    stderr = getattr(exc, "stderr", None)
    if isinstance(stderr, str) and stderr.strip():
        return stderr.strip()
    if str(exc).strip():
        return str(exc).strip()
    return repr(exc)


def _opt(value: str) -> str | None:
    value = value.strip()
    return None if value.lower() in _NULLS else value


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in _TRUE


def _parse_depends(value: str) -> list[int]:
    value = value.strip()
    if value.lower() in _NULLS:
        return []
    result: list[int] = []
    for seg in value.split(","):
        seg = seg.strip()
        if not seg:
            raise ValueError(
                f"depends-on {value!r}: empty segment "
                f"(check for stray or trailing commas)"
            )
        try:
            result.append(int(seg))
        except ValueError:
            raise ValueError(
                f"depends-on {value!r}: {seg!r} is not an issue number "
                f"(expected comma-separated integers, e.g. '1,2,3')"
            ) from None
    return result


def parse_issue(block: str) -> Issue:
    lines = block.splitlines()
    m = _HEADER_RE.match(lines[0])
    if not m:
        raise ValueError(f"bad issue header: {lines[0]!r}")
    fields: dict[str, str] = {}
    acceptance: list[AcceptanceItem] = []
    decisions: list[str] = []
    blocked: list[str] = []
    verifier: list[str] = []
    section = "fields"
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "Acceptance:":
            section = "acceptance"
        elif stripped == "### Decisions":
            section = "decisions"
        elif stripped == "### Blocked Reason":
            section = "blocked"
        elif stripped == "### Verifier Feedback":
            section = "verifier"
        elif section == "acceptance" and line.lstrip().startswith("- ["):
            checked = line.lstrip()[3:4].lower() == "x"
            acceptance.append(
                AcceptanceItem(checked=checked, text=line.split("]", 1)[1].strip())
            )
        elif section == "decisions":
            decisions.append(line)
        elif section == "blocked":
            blocked.append(line)
        elif section == "verifier":
            verifier.append(line)
        elif section == "fields" and ":" in line:
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip()
    number = int(m.group(1))
    try:
        priority = int(fields["Priority"])
    except (KeyError, ValueError):
        raise ValueError(f"issue #{number}: missing/invalid Priority")
    try:
        retries = int(fields.get("Retries", "0"))
    except ValueError:
        raise ValueError(f"issue #{number}: invalid Retries")
    try:
        crash_retries = int(fields.get("Crash-Retries", "0"))
    except ValueError:
        raise ValueError(f"issue #{number}: invalid Crash-Retries")
    return Issue(
        number=number,
        project=m.group(2),
        title=m.group(3),
        status=fields.get("Status", "open"),
        priority=priority,
        plan=_opt(fields.get("Plan", "")),
        spec=_opt(fields.get("Spec", "")),
        depends_on=_parse_depends(fields.get("Depends On", "")),
        retries=retries,
        worker=_opt(fields.get("Worker", "")),
        acceptance=acceptance,
        decisions="\n".join(decisions).strip(),
        blocked_reason="\n".join(blocked).strip(),
        verifier_feedback="\n".join(verifier).strip(),
        crash_retries=crash_retries,
        network=_parse_bool(fields.get("Network", "")),
        network_approved=_parse_bool(fields.get("Network-Approved", "")),
    )


def _fmt_opt(value: str | None) -> str:
    return value if value is not None else "null"


def render_issue(issue: Issue) -> str:
    lines = [
        f"## #{issue.number:03d} {issue.project}: {issue.title}",
        f"Status: {issue.status}",
        f"Priority: {issue.priority}",
        f"Plan: {_fmt_opt(issue.plan)}",
        f"Spec: {_fmt_opt(issue.spec)}",
        f"Depends On: {', '.join(str(d) for d in issue.depends_on) if issue.depends_on else 'null'}",
        f"Network: {'true' if issue.network else 'false'}",
        f"Network-Approved: {'true' if issue.network_approved else 'false'}",
        f"Retries: {issue.retries}",
        f"Crash-Retries: {issue.crash_retries}",
        f"Worker: {_fmt_opt(issue.worker)}",
        "Acceptance:",
    ]
    for item in issue.acceptance:
        lines.append(f"- [{'x' if item.checked else ' '}] {item.text}")
    lines.append("### Decisions")
    if issue.decisions:
        lines.append(issue.decisions)
    lines.append("### Blocked Reason")
    if issue.blocked_reason:
        lines.append(issue.blocked_reason)
    lines.append("### Verifier Feedback")
    if issue.verifier_feedback:
        lines.append(issue.verifier_feedback)
    return "\n".join(lines)


def branch_name(issue: Issue) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", issue.title.lower()).strip("-")[:40]
    return f"issue/{issue.number:03d}-{slug}"
