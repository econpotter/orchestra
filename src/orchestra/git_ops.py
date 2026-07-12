from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def commit_exists_on_branch(repo: Path, branch: str, base: str) -> bool:
    out = _git(repo, "rev-list", "--count", f"{base}..{branch}").stdout.strip()
    return int(out) > 0


def file_in_branch(repo: Path, branch: str, path: str) -> bool:
    """True if `path` exists in `branch`'s committed tree (not just on disk). Used to
    validate that a worker — which branches off `branch` — will actually have the file."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{branch}:{path}"],
        capture_output=True,
    )
    return proc.returncode == 0


def branch_head(repo: Path, branch: str) -> str:
    return _git(repo, "rev-parse", f"refs/heads/{branch}").stdout.strip()


def _worktree_for_branch(repo: Path, branch: str) -> Path | None:
    current: Path | None = None
    for line in _git(repo, "worktree", "list", "--porcelain").stdout.splitlines():
        if line.startswith("worktree "):
            current = Path(line.removeprefix("worktree "))
        elif line == f"branch refs/heads/{branch}":
            return current
    return None


def _retire_clean_foreign_worktree(
    repo: Path, worktree_path: Path, branch: str
) -> None:
    attached = _worktree_for_branch(repo, branch)
    if attached is None or attached.resolve() == worktree_path.resolve():
        return
    dirty = subprocess.run(
        ["git", "-C", str(attached), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError(
            f"branch {branch} is attached to dirty worktree {attached}; "
            "preserve or clean it before dispatch"
        )
    _git(repo, "worktree", "remove", "--force", str(attached))


def create_worktree(repo: Path, worktree_path: Path, branch: str, base: str) -> None:
    # Check if branch already exists without raising on failure (cannot use _git here
    # because _git uses check=True and would raise when the branch is absent).
    probe = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True,
    )
    if probe.returncode == 0:
        _retire_clean_foreign_worktree(repo, worktree_path, branch)
        # Branch exists — attach a new worktree to it (no -b, no base).
        _git(repo, "worktree", "add", str(worktree_path), branch)
    else:
        # Branch does not exist — create it from base.
        _git(repo, "worktree", "add", "-b", branch, str(worktree_path), base)


def remove_worktree(repo: Path, worktree_path: Path) -> None:
    _git(repo, "worktree", "remove", "--force", str(worktree_path))


def merge_conflicts(repo: Path, base: str, branch: str) -> bool:
    """True if merging `branch` into `base` would conflict. Computed via a side-effect-free
    `git merge-tree` (the same `ort` merge machinery as the real merge), so the verdict
    matches what `merge_branch` will do. Deterministic; no working tree, no commit."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "merge-tree", "--write-tree", base, branch],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return False  # clean
    if proc.returncode == 1:
        return True  # conflicts
    raise RuntimeError(
        "git merge-tree --write-tree failed (needs git >= 2.38 for conflict detection): "
        f"{(proc.stderr or proc.stdout).strip()}"
    )


def delete_branch(repo: Path, branch: str) -> None:
    """Best-effort delete of a local branch (so a fresh worktree can be cut from base)."""
    subprocess.run(["git", "-C", str(repo), "branch", "-D", branch], capture_output=True)


def branch_exists(repo: Path, branch: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True,
    ).returncode == 0


def _dirty_paths(repo: Path) -> set[str]:
    """Paths with uncommitted working-tree/index changes (porcelain). For renames, the
    destination path."""
    out = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True
    ).stdout.splitlines()
    paths: set[str] = set()
    for line in out:
        p = line[3:]
        if " -> " in p:  # rename: "old -> new" — the new path is what's dirty
            p = p.split(" -> ", 1)[1]
        paths.add(p.strip().strip('"'))
    return paths


def _show_blob(repo: Path, rev: str, path: str) -> bytes | None:
    """Contents of `path` at `rev`, or None if it does not exist there."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{rev}:{path}"], capture_output=True
    )
    return proc.stdout if proc.returncode == 0 else None


def _three_way_merge_local(repo: Path, path: str, old: str, head: str) -> bool:
    """Content-level 3-way merge of a locally edited file with its merged version.

    ours = the working-tree file (local edits on top of `old`), base = `old`:path,
    theirs = `head`:path. On a clean merge, writes the result to the working tree and
    returns True. On conflict (or a binary/unmergeable file), leaves the working tree
    untouched and returns False. Never writes conflict markers to the checkout — a live
    engine imports this tree."""
    base = _show_blob(repo, old, path)
    theirs = _show_blob(repo, head, path)
    ours_fp = repo / path
    if theirs is None or not ours_fp.is_file():
        return False
    tmp = Path(tempfile.mkdtemp(prefix="orchestra-3way-"))
    try:
        base_fp = tmp / "base"
        theirs_fp = tmp / "theirs"
        base_fp.write_bytes(base if base is not None else b"")
        theirs_fp.write_bytes(theirs)
        proc = subprocess.run(
            ["git", "-C", str(repo), "merge-file", "-p",
             str(ours_fp), str(base_fp), str(theirs_fp)],
            capture_output=True,
        )
        if proc.returncode != 0:  # >0 conflicts, <0 error
            return False
        ours_fp.write_bytes(proc.stdout)
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _sync_primary_worktree(repo: Path, old: str, head: str, protected: set[str]) -> None:
    """After a ref-only merge advanced `into`, bring the primary checkout's tree up to
    `head` for every merged file that has NO local uncommitted edit. A file with local
    edits gets a content-level 3-way merge (local edits on top of the merged version);
    if that conflicts, the local file is kept as-is and reported loudly. Either way the
    INDEX always moves to `head`'s version — a stale index reads as a staged diff that
    silently reverts the merge if committed."""
    changed = _git(repo, "diff", "--name-status", old, head).stdout.splitlines()
    merged_with_local: list[str] = []
    conflicted: list[str] = []
    for line in changed:
        parts = line.split("\t")
        status, path = parts[0], parts[-1]  # for renames name-status gives the dest last
        if path in protected:
            if status.startswith("D"):
                # merge deleted a locally edited file: keep the local content on disk
                # (becomes untracked), but drop it from the index to match HEAD
                subprocess.run(
                    ["git", "-C", str(repo), "restore", "--staged", "--source", head,
                     "--", path],
                    capture_output=True,
                )
                conflicted.append(path)
                continue
            if _three_way_merge_local(repo, path, old, head):
                merged_with_local.append(path)
            else:
                conflicted.append(path)
            # index -> head's version either way; local differences (if any) stay visible
            # as UNSTAGED modifications, never as a staged revert of the merge
            subprocess.run(
                ["git", "-C", str(repo), "restore", "--staged", "--source", head,
                 "--", path],
                capture_output=True,
            )
            continue
        if status.startswith("D"):
            fp = repo / path
            if fp.exists():
                fp.unlink()
            subprocess.run(
                ["git", "-C", str(repo), "rm", "--cached", "--quiet", "--", path],
                capture_output=True,
            )
        else:
            subprocess.run(
                ["git", "-C", str(repo), "checkout", head, "--", path], capture_output=True
            )
    if merged_with_local:
        print(
            "note: local uncommitted edits were 3-way merged with these merged files "
            f"(kept unstaged): {', '.join(merged_with_local)}",
            file=sys.stderr,
        )
    if conflicted:
        print(
            "warning: these merged files have local uncommitted edits that CONFLICT with "
            "the merge — local content kept on disk, index moved to the merged version; "
            f"reconcile manually: {', '.join(conflicted)}",
            file=sys.stderr,
        )


def _merge_tmp_root(repo: Path, tmpdir: str | Path | None) -> Path:
    """Directory to hold the throwaway detached merge worktree. Defaults to the repo's
    PARENT — a disk-backed path on the same filesystem as the checkout — NOT the system
    temp dir. `tempfile.mkdtemp()` without `dir=` lands under `$TMPDIR`/`/tmp`, which on the
    observed host was a small tmpfs; a merge there died with 'could not write to
    /tmp/orchestra-merge-*: Disk quota exceeded' (issue #006). A configured `tmpdir` (see
    config `merge.tmpdir`) overrides the default."""
    root = Path(tmpdir) if tmpdir else repo.parent
    root.mkdir(parents=True, exist_ok=True)
    return root


def merge_branch(
    repo: Path, branch: str, into: str, *, tmpdir: str | Path | None = None
) -> None:
    # Prefer an IN-CHECKOUT merge so the project's working tree reflects the merge — a
    # ref-only merge leaves the checkout stale, so a run imports OLD code and the merged
    # work silently never takes effect. Only safe when the checkout is on `into` and clean
    # (no uncommitted/untracked changes that a real `git merge` could clobber or trip on).
    on_into = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == into
    dirty = _dirty_paths(repo)
    if on_into and not dirty:
        _git(repo, "merge", "--no-ff", "-m", f"merge {branch}", branch)
        return

    # Fallback (checkout dirty or not on `into`): merge in a throwaway DETACHED worktree at
    # `into`'s commit and advance the `into` ref, never touching the dirty main tree. Then
    # sync the primary checkout to the new commit for merged files WITHOUT local edits, so
    # the merge takes effect (a live engine imports the working tree) while preserving any
    # uncommitted work. (Historically this fallback left the checkout stale.)
    old = _git(repo, "rev-parse", into).stdout.strip()
    tmp = tempfile.mkdtemp(prefix="orchestra-merge-", dir=str(_merge_tmp_root(repo, tmpdir)))
    head = old
    try:
        _git(repo, "worktree", "add", "--detach", tmp, into)
        _git(Path(tmp), "merge", "--no-ff", "-m", f"merge {branch}", branch)
        head = _git(Path(tmp), "rev-parse", "HEAD").stdout.strip()
        _git(repo, "update-ref", f"refs/heads/{into}", head)
    finally:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", tmp],
            capture_output=True,
        )
        shutil.rmtree(tmp, ignore_errors=True)

    if on_into:
        _sync_primary_worktree(repo, old, head, dirty)
    else:
        print(
            f"warning: '{into}' advanced by the merge but the primary checkout is on "
            f"'{_git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()}' — not synced; "
            f"check out '{into}' to pick up the merged work",
            file=sys.stderr,
        )
