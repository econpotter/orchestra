"""Seed a freshly-created worktree with gitignored files the tracked tree lacks.

A worker runs in an isolated git worktree, which contains only committed files.
Gitignored assets — the project's `.env` (API keys, config) and large data
directories — are therefore absent, so a worker re-authenticates from scratch or
needlessly redownloads data. `seed_worktree` materializes those into the new
worktree per the project's `Worktree-Seed` declaration (see `projects.py`):

  * `.env` is always copied when present (small; copied, not linked, so a worker
    can never clobber the real one).
  * each declared path is `copy`ed (isolated duplicate), `link`ed (writable
    symlink), or `ro-link`ed (a symlink or bind mount whose source is read-only
    in the worker's mount namespace).

Missing `copy` and `link` sources warn so a worker may create/download them.
Missing `ro-link` sources are fatal because the declared read-only input cannot
be supplied safely.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ReadOnlyBind = tuple[Path, Path]


def _seed_source(repo: Path, rel: str) -> Path:
    repo = repo.resolve()
    src = (repo / rel).resolve()
    try:
        src.relative_to(repo)
    except ValueError:
        raise ValueError(f"Worktree-Seed path {rel!r} resolves outside project {repo}") from None
    return src


def prepare_read_only_binds(
    repo: Path, worktree_path: Path, seed: list[tuple[str, str]]
) -> list[ReadOnlyBind]:
    """Prepare `ro-link` paths and return source/destination bind pairs."""
    binds: list[ReadOnlyBind] = []
    for rel, mode in seed:
        if mode != "ro-link":
            continue
        src = _seed_source(repo, rel)
        if not src.exists():
            raise FileNotFoundError(f"Worktree-Seed ro-link source {rel!r} not found at {src}")
        dst = worktree_path / rel
        if dst.is_symlink():
            if dst.resolve() != src:
                raise ValueError(
                    f"Worktree-Seed ro-link destination {dst} points to a different source"
                )
            binds.append((src, src))
            continue
        if dst.exists():
            if dst.resolve() == src:
                binds.append((src, src))
                continue
            if src.is_dir() != dst.is_dir():
                raise ValueError(
                    f"Worktree-Seed ro-link source and destination types differ: {src}, {dst}"
                )
            binds.append((src, dst.resolve()))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.symlink_to(src)
        binds.append((src, src))
    return binds


def seed_worktree(
    repo: Path, worktree_path: Path, seed: list[tuple[str, str]]
) -> list[ReadOnlyBind]:
    """Copy `.env` (if present) and each seed path into `worktree_path`.

    `seed` is a list of `(relative_path, mode)`. Never clobbers a path already
    present in the worktree. Returns bind pairs for `ro-link` entries.
    """
    env = repo / ".env"
    if env.exists():
        shutil.copy2(env, worktree_path / ".env")

    for rel, mode in seed:
        if mode not in ("copy", "link", "ro-link"):
            raise ValueError(
                f"seed_worktree: bad mode {mode!r} for {rel!r} (use copy|link|ro-link)"
            )
        if mode == "ro-link":
            continue
        src = _seed_source(repo, rel)
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
    return prepare_read_only_binds(repo, worktree_path, seed)
