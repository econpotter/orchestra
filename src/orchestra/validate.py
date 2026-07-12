from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchestra import git_ops
from orchestra.issue import Issue


@dataclass
class ValidationResult:
    ok: bool
    reasons: list[str]


def _dependency_cycle(start: int, dep_graph: dict[int, list[int]]) -> list[int] | None:
    """Return the cycle path (issue numbers, closing back on `start`) that `start`
    participates in via its Depends On edges, or None if it is acyclic. A self-dependency
    (`start` in its own deps) is the length-1 case, returned as ``[start, start]``. Follows
    edges start -> dep -> ... looking for a return to `start`; unrelated cycles elsewhere in
    the graph are ignored (each issue is validated on its own reachability)."""
    path = [start]
    visited: set[int] = set()

    def walk(node: int) -> bool:
        for dep in dep_graph.get(node, []):
            path.append(dep)
            if dep == start:
                return True  # closed the loop back to the issue under validation
            if dep not in visited:
                visited.add(dep)
                if walk(dep):
                    return True
            path.pop()
        return False

    return path if walk(start) else None


def validate_structural(
    issue: Issue,
    *,
    project_path: str,
    orchestra_root: Path,
    known_ids: set[int],
    archived_ids: set[int] | None = None,
    base_branch: str | None = None,
    dep_graph: dict[int, list[int]] | None = None,
) -> ValidationResult:
    reasons: list[str] = []

    if not issue.title.strip():
        reasons.append("missing title")

    # A worker branches off `base_branch`, so a referenced Plan/Spec must exist in THAT
    # branch's committed tree — not merely on disk in the orchestra root checkout (which
    # may sit on a different / ahead branch). When base_branch is None, fall back to disk.
    repo = Path(orchestra_root) / project_path
    for label, ref in (("Plan", issue.plan), ("Spec", issue.spec)):
        if ref:
            file_part = ref.split("#", 1)[0]
            if base_branch is not None:
                present = git_ops.file_in_branch(repo, base_branch, file_part)
                where = f"in base branch '{base_branch}'"
            else:
                present = (repo / file_part).exists()
                where = "on disk"
            if not present:
                reasons.append(f"{label} path not found {where}: {file_part}")

    if not issue.acceptance:
        reasons.append("Acceptance needs >=1 checkbox")

    # Resolve deps against the live queue AND archived/merged numbers — an archived issue
    # has left the live queue but is still a satisfied dependency (matches selection.py's
    # role_for_issue, which gates on done_numbers). Without this, an issue depending on an
    # archived number blocks here while dispatch passes it → a re-block livelock.
    resolvable = known_ids if archived_ids is None else known_ids | archived_ids
    for dep in issue.depends_on:
        if dep not in resolvable:
            reasons.append(f"Depends On references unknown issue #{dep}")

    # A self-dependency or a dependency cycle can never be satisfied — every member waits on
    # a peer that waits on it — so the issue would sit at `validated` forever, never
    # dispatchable (selection.role_for_issue gates on all deps being done). Detect it here so
    # it goes to `blocked` with the offending cycle named, instead of silently stalling.
    if dep_graph is not None:
        if issue.number in issue.depends_on:
            reasons.append(f"self-dependency: #{issue.number} depends on itself")
        else:
            cycle = _dependency_cycle(issue.number, dep_graph)
            if cycle is not None:
                chain = " -> ".join(f"#{n}" for n in cycle)
                reasons.append(f"dependency cycle: {chain}")

    return ValidationResult(ok=not reasons, reasons=reasons)
