from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Field keys may contain hyphens (e.g. `Worktree-Seed`) as well as spaces.
_FIELD_RE = re.compile(r"^-\s*([A-Za-z -]+):\s*(.+?)\s*$")


def _parse_seed(value: str) -> list[tuple[str, str]]:
    """Parse a `Worktree-Seed` value into `(path, mode)` pairs.

    Format: comma-separated `path` (mode defaults to `copy`) or `path:mode`
    where mode is `copy`, `link`, `ro-link`, or `symlink` (an alias for `link`).
    """
    out: list[tuple[str, str]] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        path, _, mode = item.partition(":")
        path = path.strip()
        seed_path = Path(path)
        if not path or seed_path.is_absolute() or ".." in seed_path.parts:
            raise ValueError(
                f"Worktree-Seed: {path!r} must be a relative project path without '..'"
            )
        mode = (mode.strip().lower() or "copy")
        if mode == "symlink":
            mode = "link"
        if mode not in ("copy", "link", "ro-link"):
            raise ValueError(
                f"Worktree-Seed: bad mode {mode!r} for {path!r} "
                "(use copy|link|ro-link|symlink)"
            )
        out.append((path, mode))
    return out


def _parse_db(value: str) -> str:
    """Parse a `Worktree-DB` value. Only `postgres` is supported; absent is empty
    (zero behavior change). Any other value is a loud error."""
    value = value.strip().lower()
    if value and value != "postgres":
        raise ValueError(f"Worktree-DB: unsupported value {value!r} (only 'postgres')")
    return value


@dataclass
class Project:
    name: str
    path: str
    branch: str
    queue: str
    purpose: str
    focus: str
    workflow: str
    worktree_seed: list[tuple[str, str]] = field(default_factory=list)
    worktree_db: str = ""


def read_projects(path: str | Path) -> list[Project]:
    text = Path(path).read_text()
    blocks = re.split(r"(?m)^(?=##\s+)", text)
    projects: list[Project] = []
    for block in blocks:
        block = block.strip()
        if not block.startswith("## "):
            continue
        name = block.splitlines()[0][3:].strip()
        fields: dict[str, str] = {}
        for line in block.splitlines()[1:]:
            m = _FIELD_RE.match(line)
            if m:
                fields[m.group(1).strip().lower()] = m.group(2).strip()
        projects.append(
            Project(
                name=name,
                path=fields.get("path", ""),
                branch=fields.get("branch", "main"),
                queue=fields.get("queue", ""),
                purpose=fields.get("purpose", ""),
                focus=fields.get("focus", ""),
                workflow=fields.get("workflow", "python"),
                worktree_seed=_parse_seed(fields.get("worktree-seed", "")),
                worktree_db=_parse_db(fields.get("worktree-db", "")),
            )
        )
    return projects


def find_project(projects: list[Project], name: str) -> Project | None:
    for project in projects:
        if project.name == name:
            return project
    return None
