"""Central refresh of the shared harness credential.

Every test here runs against a SYNTHETIC credential file in a tmp dir and replaces
`auth.run_auth_status_command`, so no real `claude auth` command and no real credential is
ever touched (a stray real refresh revokes the operator's live fleet token).
"""

from __future__ import annotations

import fcntl
import json
import threading
import time
from pathlib import Path

import pytest

from orchestra import auth


def _write_credential(home: Path, *, expires_in: float, refresh_in: float = 30 * 86400,
                      now: float | None = None) -> Path:
    now = time.time() if now is None else now
    home.mkdir(parents=True, exist_ok=True)
    path = auth.credential_path(home)
    path.write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": "synthetic-access",
            "refreshToken": "synthetic-refresh",
            "expiresAt": int((now + expires_in) * 1000),
            "refreshTokenExpiresAt": int((now + refresh_in) * 1000),
            "scopes": ["user:profile"],
            "subscriptionType": "max",
        }
    }))
    return path


def _set_expiry(home: Path, *, expires_in: float) -> None:
    path = auth.credential_path(home)
    data = json.loads(path.read_text())
    data["claudeAiOauth"]["expiresAt"] = int((time.time() + expires_in) * 1000)
    path.write_text(json.dumps(data))


# --- expiry parsing ---------------------------------------------------------------


def test_access_expiry_reads_epoch_milliseconds(tmp_path: Path):
    home = tmp_path / "homes" / "claude"
    _write_credential(home, expires_in=3600, now=1_000_000.0)
    assert auth.access_expiry(home) == pytest.approx(1_003_600.0)


@pytest.mark.parametrize("payload", [
    None,                                                    # no file at all
    "{not json",                                             # malformed
    json.dumps(["claudeAiOauth"]),                           # valid JSON, not an object
    json.dumps({}),                                          # no oauth block
    json.dumps({"claudeAiOauth": "nope"}),                   # oauth block not an object
    json.dumps({"claudeAiOauth": {"accessToken": "x"}}),     # no expiresAt
    json.dumps({"claudeAiOauth": {"expiresAt": "soon"}}),    # non-numeric expiresAt
    json.dumps({"claudeAiOauth": {"expiresAt": True}}),      # bool is not an expiry
])
def test_access_expiry_is_undeterminable_and_warns(tmp_path: Path, capsys, payload):
    home = tmp_path / "homes" / "claude"
    home.mkdir(parents=True)
    if payload is not None:
        auth.credential_path(home).write_text(payload)

    assert auth.access_expiry(home) is None
    assert "auth:" in capsys.readouterr().err


# --- margin arithmetic ------------------------------------------------------------


def test_is_stale_compares_remaining_lifetime_against_the_margin(tmp_path: Path):
    home = tmp_path / "homes" / "claude"
    _write_credential(home, expires_in=3600, now=1_000_000.0)

    assert auth.is_stale(home, 3599, now=1_000_000.0) is False   # 3600s left > margin
    assert auth.is_stale(home, 3600, now=1_000_000.0) is False   # exactly the margin
    assert auth.is_stale(home, 3601, now=1_000_000.0) is True     # inside the margin
    assert auth.is_stale(home, 7200, now=1_000_000.0) is True     # well inside the margin
    # An already-expired token is stale for any margin.
    assert auth.is_stale(home, 60, now=1_010_000.0) is True


def test_is_stale_is_false_when_expiry_cannot_be_determined(tmp_path: Path):
    home = tmp_path / "homes" / "claude"
    home.mkdir(parents=True)
    assert auth.is_stale(home, 99999) is False


# --- lock -------------------------------------------------------------------------


def test_credential_lock_is_exclusive_and_lives_outside_the_home(tmp_path: Path):
    home = tmp_path / "homes" / "claude"
    home.mkdir(parents=True)

    with auth.credential_lock(home):
        contender = open(auth._lock_path(home), "w")
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            contender.close()
        # The lock file must not sit inside the home: seeds copy the home wholesale.
        assert auth._lock_path(home).parent == home.parent
        assert list(home.iterdir()) == []

    # Released once the context exits.
    released = open(auth._lock_path(home), "w")
    try:
        fcntl.flock(released, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        released.close()


# --- refresh ----------------------------------------------------------------------


@pytest.fixture
def invocations(monkeypatch):
    """Replace the only seam that runs a harness auth command."""
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake(command, environment):
        calls.append((command, environment))
        return 0

    monkeypatch.setattr(auth, "run_auth_status_command", fake)
    return calls


def test_refresh_is_skipped_when_the_token_is_not_stale(tmp_path: Path, invocations):
    home = tmp_path / "homes" / "claude"
    _write_credential(home, expires_in=40000)

    outcome = auth.refresh_shared_credential("claude", "claude", home, margin_seconds=18000)

    assert outcome.action == auth.NOT_NEEDED
    assert invocations == []


def test_refresh_runs_the_trigger_against_the_shared_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "homes" / "claude"
    _write_credential(home, expires_in=600)
    seen: list[tuple[list[str], str]] = []

    def fake(command, environment):
        # The CLI persists the rotated credential itself; simulate that write.
        seen.append((command, environment["CLAUDE_CONFIG_DIR"]))
        _set_expiry(home, expires_in=40000)
        return 0

    monkeypatch.setattr(auth, "run_auth_status_command", fake)

    outcome = auth.refresh_shared_credential("claude", "claude", home, margin_seconds=18000)

    assert outcome.action == auth.REFRESHED
    assert seen == [(["claude", "auth", "status", "--json"], str(home))]
    assert auth.is_stale(home, 18000) is False


def test_refresh_holds_the_lock_across_the_invocation(tmp_path: Path, monkeypatch):
    home = tmp_path / "homes" / "claude"
    _write_credential(home, expires_in=600)
    locked: list[bool] = []

    def fake(command, environment):
        contender = open(auth._lock_path(home), "w")
        try:
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked.append(False)
        except BlockingIOError:
            locked.append(True)
        finally:
            contender.close()
        _set_expiry(home, expires_in=40000)
        return 0

    monkeypatch.setattr(auth, "run_auth_status_command", fake)
    auth.refresh_shared_credential("claude", "claude", home, margin_seconds=18000)

    assert locked == [True]


def test_concurrent_refresh_attempts_refresh_once(tmp_path: Path, monkeypatch):
    home = tmp_path / "homes" / "claude"
    _write_credential(home, expires_in=600)
    calls: list[str] = []

    def fake(command, environment):
        calls.append("refresh")
        time.sleep(0.05)          # widen the window the loser would race into
        _set_expiry(home, expires_in=40000)
        return 0

    monkeypatch.setattr(auth, "run_auth_status_command", fake)
    outcomes: list[auth.RefreshOutcome] = []

    def attempt():
        outcomes.append(auth.refresh_shared_credential(
            "claude", "claude", home, margin_seconds=18000
        ))

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert calls == ["refresh"]            # the loser re-checked inside the lock
    assert sorted(outcome.action for outcome in outcomes) == [auth.NOT_NEEDED, auth.REFRESHED]


def test_refresh_fails_loudly_when_the_trigger_exits_non_zero(tmp_path: Path, monkeypatch):
    home = tmp_path / "homes" / "claude"
    _write_credential(home, expires_in=600)
    monkeypatch.setattr(auth, "run_auth_status_command", lambda command, environment: 3)

    outcome = auth.refresh_shared_credential("claude", "claude", home, margin_seconds=18000)

    assert outcome.action == auth.FAILED
    assert "exited 3" in outcome.detail


def test_refresh_fails_when_the_credential_expiry_does_not_move(tmp_path: Path, monkeypatch):
    """The CLI exits 0 even when it persists a FAILED refresh (zeroed tokens, spike s1)."""
    home = tmp_path / "homes" / "claude"
    _write_credential(home, expires_in=600)

    def fake(command, environment):
        path = auth.credential_path(home)
        data = json.loads(path.read_text())
        data["claudeAiOauth"]["accessToken"] = ""
        data["claudeAiOauth"]["refreshToken"] = ""
        path.write_text(json.dumps(data))
        return 0

    monkeypatch.setattr(auth, "run_auth_status_command", fake)

    outcome = auth.refresh_shared_credential("claude", "claude", home, margin_seconds=18000)

    assert outcome.action == auth.FAILED
    assert "did not move forward" in outcome.detail


def test_refresh_is_a_no_op_for_a_harness_without_an_auth_status_command(
    tmp_path: Path, invocations,
):
    home = tmp_path / "homes" / "pi"
    _write_credential(home, expires_in=600)

    outcome = auth.refresh_shared_credential("pi", "pi", home, margin_seconds=18000)

    assert outcome.action == auth.NOT_NEEDED
    assert invocations == []
