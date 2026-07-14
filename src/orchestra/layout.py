from __future__ import annotations

from pathlib import Path


def queue_file(root: str | Path, project: str) -> Path:
    return Path(root) / "queue" / f"{project}.md"


def archive_file(root: str | Path, project: str) -> Path:
    return Path(root) / "queue" / "archive" / f"{project}.md"


def worktree_dir(root: str | Path, project: str, number: int) -> Path:
    return Path(root) / ".orchestra" / "worktrees" / f"{project}-{number:03d}"
