from __future__ import annotations

import re
from pathlib import Path

_SKIP = {"global constraints", "final verification"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def propose_issues_from_plan(plan_path: str | Path, project: str) -> list[dict]:
    path = Path(plan_path)
    proposals = []
    for line in path.read_text().splitlines():
        m = re.match(r"^(#{2,3})\s+(.*\S)\s*$", line)  # ## or ### headings
        if not m:
            continue
        title = m.group(2).strip()
        if title.lower() in _SKIP:
            continue
        proposals.append({"title": title, "plan": f"{plan_path}#{_slug(title)}"})
    return proposals
