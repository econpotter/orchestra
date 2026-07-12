"""Per-worktree Postgres database create/drop for `Worktree-DB` projects.

A DB-backed project (declares `Worktree-DB: postgres` in PROJECTS.md) needs each
worktree pointed at its own database clone, so concurrent workers and the verifier
never collide through shared fixtures, upserts, or unmerged migrations. Orchestra
owns the database lifecycle (create on dispatch, drop on archive); the project owns
its schema — the worker migrates its own blank clone from its own branch.

DB ops shell out to `psql` (no new Python dependency; present wherever a local
Postgres workflow exists). The password is passed via `PGPASSWORD` in the subprocess
environment, never on argv. Identifiers interpolated into DDL are validated against
`^[a-z_][a-z0-9_]*$` first — quoting is not a substitute for validation here.

One-time per-project prerequisite (documented, not automated): `DB_USER` needs
`CREATEDB` (`ALTER ROLE <user> CREATEDB`).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
# Connection components read from the project's `.env`.
_DB_COMPONENTS = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME")


def _parse_env(env_file: Path) -> dict[str, str]:
    """Return `KEY=value` pairs from a `.env` file, first occurrence winning (matching
    common `os.environ.setdefault` conventions). Skips blank/comment lines,
    strips an optional `export ` prefix and surrounding quotes."""
    values: dict[str, str] = {}
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, val = line.partition("=")
        if not sep:
            continue
        values.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    return values


def _db_config(env_file: Path) -> dict[str, str]:
    """DB_* connection components from `.env`. Missing any component — or an empty
    value for anything but the password — is a loud error (per the fail-loud rule)."""
    if not env_file.exists():
        raise ValueError(
            f"Worktree-DB: no .env at {env_file} to read DB_* connection from"
        )
    env = _parse_env(env_file)
    missing = [k for k in _DB_COMPONENTS if k not in env]
    if missing:
        raise ValueError(
            f"Worktree-DB: {env_file} missing DB component(s): {', '.join(missing)}"
        )
    empty = [k for k in _DB_COMPONENTS if k != "DB_PASSWORD" and not env[k]]
    if empty:
        raise ValueError(
            f"Worktree-DB: {env_file} has empty DB component(s): {', '.join(empty)}"
        )
    return {k: env[k] for k in _DB_COMPONENTS}


def _require_ident(name: str, label: str) -> str:
    """Return `name` if it is a valid Postgres identifier, else raise loudly. Names
    are interpolated into SQL run via psql; validation, not quoting, is the guard."""
    if not _IDENT_RE.match(name):
        raise ValueError(
            f"Worktree-DB: {label} {name!r} is not a valid Postgres identifier "
            f"(must match ^[a-z_][a-z0-9_]*$)"
        )
    return name


def clone_name(db_name: str, number: int) -> str:
    """Derive the per-worktree clone name. `DB_NAME` already namespaces the project,
    so the issue number alone disambiguates: e.g. `demo` + 4 -> `demo_wt_004`."""
    return f"{db_name}_wt_{number:03d}"


def _psql(cfg: dict[str, str], dbname: str, sql: str) -> subprocess.CompletedProcess[str]:
    """Run one SQL statement via psql against `dbname`. `PGPASSWORD` goes in the
    environment, never on argv. Raises `CalledProcessError` on a nonzero exit."""
    env = dict(os.environ)
    env["PGPASSWORD"] = cfg["DB_PASSWORD"]
    argv = [
        "psql",
        "-h", cfg["DB_HOST"],
        "-p", cfg["DB_PORT"],
        "-U", cfg["DB_USER"],
        "-d", dbname,
        "-v", "ON_ERROR_STOP=1",
        "-tAqc", sql,
    ]
    return subprocess.run(argv, env=env, check=True, capture_output=True, text=True)


def _set_env_var(env_file: Path, key: str, value: str) -> None:
    """Rewrite the `KEY=...` line in `.env` in place (first occurrence). Never appends:
    Some test setups apply `.env` first-occurrence-wins, so an appended override is
    silently ignored. A missing key line is a loud error."""
    lines = env_file.read_text().splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        exported = stripped.startswith("export ")
        body = stripped[len("export "):] if exported else stripped
        if body.startswith(f"{key}="):
            indent = line[: len(line) - len(line.lstrip())]
            lines[i] = f"{indent}{'export ' if exported else ''}{key}={value}"
            env_file.write_text("\n".join(lines) + "\n")
            return
    raise ValueError(f"Worktree-DB: no {key} line in {env_file} to rewrite")


def create_worktree_db(repo_env: Path, worktree_env: Path, number: int) -> None:
    """Ensure `{DB_NAME}_wt_{number}` exists (blank, owned by DB_USER), then rewrite
    DB_NAME in the worktree `.env` to the clone.

    The base `DB_NAME` is read from the project's own `.env` (`repo_env`) — the
    canonical source that is never rewritten — so this is idempotent across crash-
    retries: an existing clone is reused as-is (exactly right for the verifier
    sharing the worker's DB state), and the derivation never compounds. No `TEMPLATE`
    clause: the clone is blank and the worker migrates it from its own branch.

    Raises loudly on missing/invalid config so the caller can isolate this issue's
    launch (log + skip), never crashing the dispatch loop.
    """
    cfg = _db_config(repo_env)
    base = _require_ident(cfg["DB_NAME"], "DB_NAME")
    owner = _require_ident(cfg["DB_USER"], "DB_USER")
    clone = _require_ident(clone_name(base, number), "clone name")

    present = _psql(
        cfg, base, f"SELECT 1 FROM pg_database WHERE datname = '{clone}'"
    ).stdout.strip()
    if present != "1":
        _psql(cfg, base, f'CREATE DATABASE "{clone}" OWNER "{owner}"')
    _set_env_var(worktree_env, "DB_NAME", clone)


def drop_worktree_db(repo_env: Path, number: int) -> None:
    """Drop `{DB_NAME}_wt_{number}` WITH (FORCE) (Postgres 13+; terminates lingering
    connections). Best-effort: any failure warns with a manual-cleanup pointer and
    returns, never blocking archive."""
    try:
        cfg = _db_config(repo_env)
        base = _require_ident(cfg["DB_NAME"], "DB_NAME")
        clone = _require_ident(clone_name(base, number), "clone name")
        _psql(cfg, base, f'DROP DATABASE IF EXISTS "{clone}" WITH (FORCE)')
    except (subprocess.CalledProcessError, ValueError, OSError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        print(
            f"warning: Worktree-DB drop failed for issue #{number:03d} "
            f"(from {repo_env}) — clean up manually: "
            f"DROP DATABASE IF EXISTS <DB_NAME>_wt_{number:03d} WITH (FORCE). "
            f"Detail: {str(detail).strip()}",
            file=sys.stderr,
        )
