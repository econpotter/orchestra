from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# The harness's own credential file, at the ROOT of the config dir. `CLAUDE_CONFIG_DIR`
# makes this file authoritative: the CLI reads and rewrites it directly, never the nested
# `.claude/.credentials.json` copy some homes also carry (that copy is a leftover of
# populating a managed home by hand — see docs/notes/2026-07-30-refresh-trigger-spike.md).
CREDENTIAL_FILE = ".credentials.json"

AUTH_STATUS_TIMEOUT = 15

# `harness doctor`'s expiry readout warns once the refresh-token horizon (the ~30-day point
# past which no refresh is possible at all, requiring `orchestra harness login`) is this
# close.
REFRESH_WARNING_DAYS = 5

_HOME_VARIABLE = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}

_AUTH_STATUS_ARGUMENTS = {
    "codex": ("login", "status"),
    "claude": ("auth", "status", "--json"),
}

# Refresh outcomes. `HELD` is produced here only for a non-blocking (`blocking=False`) lock
# acquisition that hits contention with another writer (`harness doctor`/`harness login`);
# the dispatch layer also records `HELD` itself when a needed refresh cannot run yet because
# workers of that harness are still in flight.
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
def credential_lock(home: Path, *, blocking: bool = True) -> Iterator[bool]:
    """Exclusive lock over one harness home's credential. Yields whether it was acquired.

    Serializes every writer that can rewrite the shared credential: `orchestra harness
    doctor` and `orchestra harness login` (operator-driven, bounded by the operator's own
    terminal — these use the default `blocking=True` and always yield `True`, waiting as
    long as it takes) and the dispatch tick's refresh gate and seed-time copy (`blocking=
    False` — an engine tick must never stall behind a slow or abandoned interactive login
    holding this lock; on contention it yields `False` immediately instead of waiting, and
    the caller defers that harness's work to the next tick). Mirrors `enginelock.engine_lock`
    's non-blocking defer idiom.
    """
    path = _lock_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "w")
    try:
        if not blocking:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            yield True
            return
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield True
    finally:
        handle.close()  # closing the descriptor releases the flock


def _oauth_field(home: Path, field: str) -> object | None:
    """The raw `claudeAiOauth.<field>` value from `home`'s credential file, or None.

    None means "cannot determine": no credential file, an unreadable one, malformed JSON,
    or a missing field. The credential schema is the harness's own external data, so each
    of those is a loud warning, never a crash.
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
    if not isinstance(oauth, dict):
        _warn(f"{path} has no usable claudeAiOauth object; cannot determine token expiry")
        return None
    return oauth.get(field)


def _oauth_expiry(home: Path, field: str) -> float | None:
    """Epoch SECONDS for a `claudeAiOauth.<field>` expiry, or None if undeterminable.

    The stored value is epoch MILLIseconds.
    """
    value = _oauth_field(home, field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        path = credential_path(home)
        _warn(f"{path} has no usable claudeAiOauth.{field}; cannot determine token expiry")
        return None
    return value / 1000.0


def access_expiry(home: Path) -> float | None:
    """Epoch SECONDS at which this home's OAuth access token expires, or None (see
    `_oauth_expiry`)."""
    return _oauth_expiry(home, "expiresAt")


def refresh_expiry(home: Path) -> float | None:
    """Epoch SECONDS at which this home's OAuth refresh token expires, or None (see
    `_oauth_expiry`). This is the ~30-day horizon past which no refresh is possible at all
    and the operator must re-authenticate (`orchestra harness login`)."""
    return _oauth_expiry(home, "refreshTokenExpiresAt")


def access_token(home: Path) -> str | None:
    """The raw OAuth access token VALUE from `home`'s credential file, or None.

    Read only so `harness doctor` can send it once as the revocation probe's Authorization
    header over HTTPS. Never log, print, or persist this value — callers must not either.
    Silent (no `_warn`) on any parse failure: `access_expiry`/`refresh_expiry` already warn
    about a broken credential file when doctor reads it for the expiry readout.
    """
    value = _oauth_field(home, "accessToken")
    return value if isinstance(value, str) and value else None


def describe_expiry(
    expiry: float | None, *, now: float | None = None
) -> tuple[str | None, float | None]:
    """(ISO 8601 UTC timestamp, days-remaining) for an epoch-seconds expiry, or (None, None)
    when the expiry itself is undeterminable. Used by `harness doctor`'s expiry readout."""
    if expiry is None:
        return None, None
    reference = time.time() if now is None else now
    at = datetime.fromtimestamp(expiry, tz=timezone.utc).isoformat()
    return at, (expiry - reference) / 86400.0


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


# Revocation probe (docs/notes/2026-07-30-refresh-trigger-spike.md section 3): a purpose-built
# authenticated GET the Claude CLI itself uses to answer "is this token still good." Identified
# statically from the installed binary and never yet exercised, so every outcome other than an
# explicit 2xx/401/403 is treated as indeterminate, never as a definitive answer.
PROBE_URL = "https://api.anthropic.com/api/oauth/validate"
PROBE_TIMEOUT_SECONDS = 5.0

VALID = "valid"
REVOKED = "revoked"
UNREACHABLE = "unreachable"
NO_CREDENTIAL = "no_credential"


def _open_probe(access_token_value: str, timeout: float) -> Any:
    """The single seam every revocation-probe invocation goes through, so tests can replace
    it and never send a real HTTP request carrying a real access token."""
    request = urllib.request.Request(
        PROBE_URL, headers={"Authorization": f"Bearer {access_token_value}"},
    )
    return urllib.request.urlopen(request, timeout=timeout)  # fixed https:// URL, not user input


def probe_access_token(
    access_token_value: str, *, timeout: float = PROBE_TIMEOUT_SECONDS
) -> str:
    """valid / revoked / unreachable for one access token.

    A 2xx response is `valid`; an explicit 401 or 403 is `revoked`. Everything else —
    timeout, network error, malformed HTTP response, 5xx, or an unexpected 4xx — is
    `unreachable`: the endpoint's behavior was identified from binary strings only and
    never exercised live, so anything short of an unambiguous auth failure is treated as
    indeterminate, never as `revoked`. `unreachable` is a warning; it must never cause
    `harness doctor` to fail, and it must never crash it either — hence the blanket except
    below rather than an enumerated list of exception types (a flaky proxy or a truncated
    status line can raise `http.client.HTTPException` subclasses, which are not `OSError`s
    and so would otherwise slip past a narrower catch).

    The token value is used only as this one request's Authorization header over HTTPS —
    never logged, printed, or written anywhere.
    """
    try:
        with _open_probe(access_token_value, timeout):
            return VALID
    except urllib.error.HTTPError as exc:
        return REVOKED if exc.code in (401, 403) else UNREACHABLE
    except Exception:  # noqa: BLE001 — see docstring: any other failure is indeterminate,
        # never a hard failure and never "revoked".
        return UNREACHABLE


def probe_shared_credential(home: Path, *, timeout: float = PROBE_TIMEOUT_SECONDS) -> str:
    """valid / revoked / unreachable / no_credential for `home`'s current access token."""
    token = access_token(home)
    if token is None:
        return NO_CREDENTIAL
    return probe_access_token(token, timeout=timeout)


def refresh_shared_credential(
    kind: str, executable: str, home: Path, *, margin_seconds: int, blocking: bool = True
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

    `blocking=False` (the dispatch tick's use) never waits for a concurrent writer: an
    operator-driven `harness doctor`/`harness login` can hold this lock for as long as its
    terminal session takes, and a dispatch tick must not stall the whole engine behind that.
    Contention then yields `HELD` immediately — the same outcome the caller already uses for
    "workers still active" — so the caller defers this harness to the next tick instead.
    """
    command = auth_status_command(kind, executable)
    if command is None:
        return RefreshOutcome(NOT_NEEDED, f"{kind} exposes no auth-status command")
    environment = shared_home_environment(kind, home)
    with credential_lock(home, blocking=blocking) as acquired:
        if not acquired:
            return RefreshOutcome(
                HELD, "credential lock is held by another writer (doctor or login in progress)"
            )
        before = access_expiry(home)
        if before is None or not _stale(before, margin_seconds, None):
            return RefreshOutcome(NOT_NEEDED)
        code = run_auth_status_command(command, environment)
        if code != 0:
            return RefreshOutcome(FAILED, f"{' '.join(command[1:])} exited {code}")
        after = access_expiry(home)
        if after is None or after <= before:
            return RefreshOutcome(FAILED, "access token expiry did not move forward")
        return RefreshOutcome(REFRESHED)
