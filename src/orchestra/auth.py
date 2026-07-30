from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

# The harness's own credential file, at the ROOT of the config dir. `CLAUDE_CONFIG_DIR`
# makes this file authoritative: the CLI reads and rewrites it directly, never the nested
# `.claude/.credentials.json` copy some homes also carry (that copy is a leftover of
# populating a managed home by hand — see docs/notes/2026-07-30-refresh-trigger-spike.md).
CREDENTIAL_FILE = ".credentials.json"

AUTH_STATUS_TIMEOUT = 15

_HOME_VARIABLE = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}

_AUTH_STATUS_ARGUMENTS = {
    "codex": ("login", "status"),
    "claude": ("auth", "status", "--json"),
}

# Refresh outcomes. `HELD` is not produced here — the dispatch layer records it when a
# needed refresh cannot run yet because workers of that harness are still in flight.
NOT_NEEDED = "not_needed"
REFRESHED = "refreshed"
FAILED = "failed"
HELD = "held"


@dataclass(frozen=True)
class RefreshOutcome:
    action: str
    detail: str = ""


def _warn(message: str) -> None:
    print(f"auth: {message}", file=sys.stderr)


def auth_status_command(kind: str, executable: str) -> list[str] | None:
    """The harness's non-interactive auth-status command, or None if it has none.

    For Claude this command is also the *refresh trigger*: run against a config dir whose
    access token is past or near expiry, the CLI performs its native OAuth refresh and
    persists the rotated credential in place before printing status. It is free — no model
    call, nothing metered (docs/notes/2026-07-30-refresh-trigger-spike.md). Because it
    rewrites the credential, every engine-side invocation against a *shared* home must hold
    `credential_lock`; the per-launch copies dispatch seeds are private and need no lock.
    """
    arguments = _AUTH_STATUS_ARGUMENTS.get(kind)
    if arguments is None:
        return None
    return [executable, *arguments]


def credential_path(home: Path) -> Path:
    return home / CREDENTIAL_FILE


def _lock_path(home: Path) -> Path:
    # A sibling of the home rather than a file inside it: `seed_session_home` copies the
    # home wholesale into every per-launch copy, and a lock file has no business travelling
    # with the seed.
    return home.parent / f".{home.name}.credentials.lock"


@contextmanager
def credential_lock(home: Path) -> Iterator[None]:
    """Blocking exclusive lock over one harness home's credential.

    Serializes every engine-side invocation that can rewrite the shared credential: the
    dispatch refresher and `orchestra harness doctor` both run the same refresh-and-persist
    command against the shared home. Blocking rather than try-lock, because neither caller
    may proceed on a credential another writer is in the middle of rotating; the critical
    section is a single CLI call bounded by `AUTH_STATUS_TIMEOUT`.
    """
    path = _lock_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        handle.close()  # closing the descriptor releases the flock


def access_expiry(home: Path) -> float | None:
    """Epoch SECONDS at which this home's OAuth access token expires, or None.

    None means "cannot determine": no credential file, an unreadable one, malformed JSON,
    or a missing/non-numeric `claudeAiOauth.expiresAt`. The credential schema is the
    harness's own external data, so each of those is a loud warning and a skipped refresh
    decision, never a crash. The stored value is epoch MILLIseconds.
    """
    path = credential_path(home)
    try:
        raw = path.read_text()
    except FileNotFoundError:
        _warn(f"{path} does not exist; cannot determine token expiry")
        return None
    except OSError as exc:
        _warn(f"cannot read {path}: {exc}")
        return None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        _warn(f"{path} is not valid JSON: {exc}")
        return None
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    expires_at = oauth.get("expiresAt") if isinstance(oauth, dict) else None
    if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)):
        _warn(f"{path} has no usable claudeAiOauth.expiresAt; cannot determine token expiry")
        return None
    return expires_at / 1000.0


def _stale(expiry: float, margin_seconds: int, now: float | None) -> bool:
    return expiry - (time.time() if now is None else now) < margin_seconds


def is_stale(home: Path, margin_seconds: int, *, now: float | None = None) -> bool:
    """True when the home's access token has less than `margin_seconds` of life left.

    An undeterminable expiry is False: refresh gating is skipped (with the warning
    `access_expiry` already emitted) rather than guessed at, because a wrong "stale"
    verdict rotates — and thereby revokes — a live fleet token for no reason.
    """
    expiry = access_expiry(home)
    if expiry is None:
        return False
    return _stale(expiry, margin_seconds, now)


def shared_home_environment(kind: str, home: Path) -> dict[str, str]:
    """The process environment that points a harness CLI at `home` as its config dir."""
    environment = os.environ.copy()
    variable = _HOME_VARIABLE.get(kind)
    if variable is not None:
        environment[variable] = str(home)
    return environment


def run_auth_status_command(command: list[str], environment: dict[str, str]) -> int:
    """Run an auth-status command and return its exit code (non-zero on invocation error).

    The single seam every engine-side auth-status invocation goes through, so tests can
    replace it and never run a real `claude auth` command against a real credential.
    """
    try:
        result = subprocess.run(
            command, text=True, capture_output=True,
            timeout=AUTH_STATUS_TIMEOUT, check=False, env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _warn(f"auth status invocation failed: {exc}")
        return 1
    return result.returncode


def run_auth_status(
    kind: str, executable: str, home: Path, environment: dict[str, str]
) -> bool:
    """Locked auth-status check against a harness home; True when it reports success.

    Used by `harness doctor`, whose check against the shared home is the very same
    refresh-and-persist command the dispatch refresher runs — so it takes the same lock.
    """
    command = auth_status_command(kind, executable)
    if command is None:
        return False
    with credential_lock(home):
        return run_auth_status_command(command, environment) == 0


def refresh_shared_credential(
    kind: str, executable: str, home: Path, *,
    margin_seconds: int, now: float | None = None,
) -> RefreshOutcome:
    """Refresh a shared home's credential in place, as the engine's single writer.

    Staleness is re-checked *inside* the lock, so two engine paths racing on the same home
    perform one refresh rather than two: rotation revokes the prior access token, so a
    second, redundant rotation is not merely wasted work.

    Success is not "the command exited 0". With an unusable refresh token the CLI still
    exits 0 and persists the *failure* — zeroing the tokens in place (spike section 1). The
    only honest success signal is the credential's own expiry moving forward, so that is
    what is checked. The refreshed credential is written by the harness itself; orchestra
    never rewrites the shared credential file.
    """
    command = auth_status_command(kind, executable)
    if command is None:
        return RefreshOutcome(NOT_NEEDED, f"{kind} exposes no auth-status command")
    environment = shared_home_environment(kind, home)
    with credential_lock(home):
        before = access_expiry(home)
        if before is None or not _stale(before, margin_seconds, now):
            return RefreshOutcome(NOT_NEEDED)
        code = run_auth_status_command(command, environment)
        if code != 0:
            return RefreshOutcome(FAILED, f"{' '.join(command[1:])} exited {code}")
        after = access_expiry(home)
        if after is None or after <= before:
            return RefreshOutcome(FAILED, "access token expiry did not move forward")
        return RefreshOutcome(REFRESHED)
