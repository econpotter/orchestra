"""Seed a freshly-created worktree with gitignored files the tracked tree lacks.

A worker runs in an isolated git worktree, which contains only committed files.
Gitignored assets — the project's `.env` (API keys, config) and large data
directories — are therefore absent, so a worker re-authenticates from scratch or
needlessly redownloads data. `seed_worktree` materializes those into the new
worktree per the project's `Worktree-Seed` declaration (see `projects.py`):

  * `.env` is always copied when present (small; copied, not linked, so a worker
    can never clobber the real one).
  * each declared path is `copy`ed (isolated duplicate) or `link`ed (symlink to
    the project copy — for big, read-mostly data dirs that must not be duplicated).

A declared path that does not exist yet is warned about, not fatal: the worker
should proceed and create/download it rather than have dispatch skip the issue.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def seed_worktree(repo: Path, worktree_path: Path, seed: list[tuple[str, str]]) -> None:
    """Copy `.env` (if present) and each seed path into `worktree_path`.

    `seed` is a list of `(relative_path, mode)` where mode is `"copy"` or
    `"link"`. Never clobbers a path already present in the worktree.
    """
    env = repo / ".env"
    if env.exists():
        shutil.copy2(env, worktree_path / ".env")

    for rel, mode in seed:
        if mode not in ("copy", "link"):
            raise ValueError(f"seed_worktree: bad mode {mode!r} for {rel!r} (use copy|link)")
        src = repo / rel
        dst = worktree_path / rel
        if dst.exists() or dst.is_symlink():
            continue  # tracked file or already seeded — don't overwrite
        if not src.exists():
            # Loud, but non-fatal: absent data is a redownload, not a config error.
            print(
                f"seed_worktree: skipping {rel!r} — not found at {src}",
                file=sys.stderr,
            )
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if mode == "link":
            dst.symlink_to(src.resolve())
        elif src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
