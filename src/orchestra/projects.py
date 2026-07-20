from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path


class DuplicateProjectError(ValueError):
    """A project name is already present in the registry (or would collide)."""


class DuplicateProjectWarning(UserWarning):
    """A loaded registry contains more than one entry for the same name."""

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
    _warn_duplicate_names(projects)
    return projects


def _warn_duplicate_names(projects: list[Project]) -> None:
    """Surface duplicate registry entries instead of silently first-matching.

    A hand-edited PROJECTS.md can register the same name twice (see commit
    b3f9d65); left unchecked every lookup silently resolves to the first block.
    """
    seen: set[str] = set()
    dupes: list[str] = []
    for project in projects:
        if project.name in seen and project.name not in dupes:
            dupes.append(project.name)
        seen.add(project.name)
    if dupes:
        names = ", ".join(repr(d) for d in dupes)
        warnings.warn(
            f"PROJECTS.md registers duplicate project name(s): {names}; "
            "every lookup resolves to the first match",
            DuplicateProjectWarning,
            stacklevel=3,
        )


def registered_names(path: str | Path) -> list[str]:
    """Names present in the registry file (empty if the file does not exist)."""
    p = Path(path)
    if not p.exists():
        return []
    return [project.name for project in read_projects(p)]


def ensure_name_available(path: str | Path, name: str) -> None:
    """Raise if `name` is already registered — shared add/scaffold guard."""
    if name in registered_names(path):
        raise DuplicateProjectError(
            f"project {name!r} is already registered in {Path(path).name}"
        )


def find_project(projects: list[Project], name: str) -> Project | None:
    for project in projects:
        if project.name == name:
            return project
    return None
