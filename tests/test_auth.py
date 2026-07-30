"""Central refresh of the shared harness credential.

Every test here runs against a SYNTHETIC credential file in a tmp dir and replaces
`auth.run_auth_status_command`, so no real `claude auth` command and no real credential is
ever touched (a stray real refresh revokes the operator's live fleet token).
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from pathlib import Path

import pytest

from orchestra import auth
from orchestra.config import load_config
from orchestra.dispatch import _refresh_managed_credentials
from orchestra.registry import WorkerHandle
from orchestra.selection import process_start_time


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


# --- the dispatch-boundary gate ---------------------------------------------------

GATE_CONFIG = """\
slots: 2
refresh_margin_seconds: 18000
roles:
  validator: {harness: codex, model: m, prompt: prompts/validator.md}
  worker: {harness: claude, model: m, prompt: prompts/worker.md,
           instruction_policy: explicit_bundle}
  verifier: {harness: codex, model: m, prompt: prompts/verify-review.md}
harnesses:
  codex:
    kind: codex
    executable: codex
    environment: {policy: isolated, state_dir: .orchestra/homes/codex}
  claude:
    kind: claude
    executable: claude
    environment: {policy: isolated, state_dir: .orchestra/homes/claude}
sandbox: {enabled: true}
"""


def _gate_config(root: Path, extra: str = ""):
    path = root / "config.yaml"
    path.write_text(GATE_CONFIG + extra)
    return load_config(path)


def _handle(role: str, *, alive: bool) -> WorkerHandle:
    pid = os.getpid() if alive else 0
    return WorkerHandle(
        project="wf", number=1, role=role, branch="issue/001-x", worktree="/tmp/wt",
        pid=pid, attempt_id="a1", manifest="/tmp/a1/manifest.json",
        stdout="/tmp/a1/stdout.jsonl", stderr="/tmp/a1/stderr.log",
        started="2026-07-30T00:00:00+00:00", start_sha="abc",
        proc_start=(process_start_time(pid) or "") if alive else "",
    )


def _shared_home(root: Path) -> Path:
    return root / ".orchestra" / "homes" / "claude"


def _record(root: Path) -> dict:
    return json.loads((root / ".orchestra" / "auth-refresh.json").read_text())


@pytest.fixture
def refreshing_runner(monkeypatch):
    """A fake trigger that persists a rotated credential, as the real CLI would."""
    calls: list[list[str]] = []
    homes: list[Path] = []

    def fake(command, environment):
        calls.append(command)
        home = Path(environment["CLAUDE_CONFIG_DIR"])
        homes.append(home)
        _set_expiry(home, expires_in=40000)
        return 0

    monkeypatch.setattr(auth, "run_auth_status_command", fake)
    return calls


def test_gate_refreshes_a_stale_credential_when_the_harness_is_quiesced(
    tmp_path: Path, refreshing_runner,
):
    config = _gate_config(tmp_path)
    _write_credential(_shared_home(tmp_path), expires_in=600)

    held = _refresh_managed_credentials(
        tmp_path, config, {}, {"claude"}, started="2026-07-30T00:00:00+00:00"
    )

    assert held == set()
    assert refreshing_runner == [["claude", "auth", "status", "--json"]]
    assert auth.is_stale(_shared_home(tmp_path), config.refresh_margin_seconds) is False
    assert _record(tmp_path)["claude"]["outcome"] == auth.REFRESHED


def test_gate_holds_launches_while_workers_of_that_harness_are_active(
    tmp_path: Path, refreshing_runner, capsys,
):
    config = _gate_config(tmp_path)
    _write_credential(_shared_home(tmp_path), expires_in=600)
    reg = {"wf#001": _handle("worker", alive=True)}   # worker role runs on claude

    held = _refresh_managed_credentials(
        tmp_path, config, reg, {"claude"}, started="2026-07-30T00:00:00+00:00"
    )

    # Rotation revokes the token the live worker is holding, so it must not happen yet.
    assert held == {"claude"}
    assert refreshing_runner == []
    assert auth.is_stale(_shared_home(tmp_path), config.refresh_margin_seconds) is True
    assert _record(tmp_path)["claude"]["outcome"] == auth.HELD
    assert "holding claude launches" in capsys.readouterr().err


def test_gate_refreshes_once_the_harness_has_drained(tmp_path: Path, refreshing_runner):
    """The held tick refreshes nothing; the next tick, after the drain, does."""
    config = _gate_config(tmp_path)
    _write_credential(_shared_home(tmp_path), expires_in=600)
    reg = {"wf#001": _handle("worker", alive=True)}
    started = "2026-07-30T00:00:00+00:00"

    assert _refresh_managed_credentials(
        tmp_path, config, reg, {"claude"}, started=started
    ) == {"claude"}
    assert _refresh_managed_credentials(
        tmp_path, config, {}, {"claude"}, started=started
    ) == set()
    assert refreshing_runner == [["claude", "auth", "status", "--json"]]
    assert _record(tmp_path)["claude"]["outcome"] == auth.REFRESHED


def test_gate_ignores_live_workers_of_a_different_harness(
    tmp_path: Path, refreshing_runner,
):
    config = _gate_config(tmp_path)
    _write_credential(_shared_home(tmp_path), expires_in=600)
    reg = {"wf#002": _handle("validator", alive=True)}   # validator role runs on codex

    held = _refresh_managed_credentials(
        tmp_path, config, reg, {"claude"}, started="2026-07-30T00:00:00+00:00"
    )

    assert held == set()
    assert len(refreshing_runner) == 1


def test_gate_ignores_registry_rows_whose_supervisor_is_gone(
    tmp_path: Path, refreshing_runner,
):
    """A stale row must not block refresh forever — that would let the token die."""
    config = _gate_config(tmp_path)
    _write_credential(_shared_home(tmp_path), expires_in=600)
    reg = {"wf#001": _handle("worker", alive=False)}

    held = _refresh_managed_credentials(
        tmp_path, config, reg, {"claude"}, started="2026-07-30T00:00:00+00:00"
    )

    assert held == set()
    assert len(refreshing_runner) == 1


def test_gate_leaves_a_credential_with_life_left_alone(tmp_path: Path, invocations):
    config = _gate_config(tmp_path)
    _write_credential(_shared_home(tmp_path), expires_in=40000)

    held = _refresh_managed_credentials(
        tmp_path, config, {}, {"claude"}, started="2026-07-30T00:00:00+00:00"
    )

    assert held == set()
    assert invocations == []
    assert not (tmp_path / ".orchestra" / "auth-refresh.json").exists()


def test_gate_skips_harnesses_without_a_managed_claude_home(tmp_path: Path, invocations):
    config = _gate_config(tmp_path, extra="")
    _write_credential(tmp_path / ".orchestra" / "homes" / "codex", expires_in=600)

    held = _refresh_managed_credentials(
        tmp_path, config, {}, {"codex"}, started="2026-07-30T00:00:00+00:00"
    )

    assert held == set()
    assert invocations == []


def test_gate_skips_when_the_expiry_cannot_be_determined(
    tmp_path: Path, invocations, capsys,
):
    config = _gate_config(tmp_path)
    _shared_home(tmp_path).mkdir(parents=True)

    held = _refresh_managed_credentials(
        tmp_path, config, {}, {"claude"}, started="2026-07-30T00:00:00+00:00"
    )

    assert held == set()
    assert invocations == []
    assert "cannot determine token expiry" in capsys.readouterr().err


def test_gate_dispatches_degraded_when_the_refresh_fails(
    tmp_path: Path, monkeypatch, capsys,
):
    config = _gate_config(tmp_path)
    _write_credential(_shared_home(tmp_path), expires_in=600)
    monkeypatch.setattr(auth, "run_auth_status_command", lambda command, environment: 1)

    held = _refresh_managed_credentials(
        tmp_path, config, {}, {"claude"}, started="2026-07-30T00:00:00+00:00"
    )

    # Degraded, not deadlocked: the launch still goes out on the existing token.
    assert held == set()
    assert "WARNING shared claude credential refresh failed" in capsys.readouterr().err
    assert _record(tmp_path)["claude"]["outcome"] == auth.FAILED


def test_gate_records_are_atomic_and_keep_other_harnesses(tmp_path: Path, refreshing_runner):
    config = _gate_config(tmp_path)
    _write_credential(_shared_home(tmp_path), expires_in=600)
    orchestra_dir = tmp_path / ".orchestra"
    orchestra_dir.mkdir(parents=True, exist_ok=True)
    (orchestra_dir / "auth-refresh.json").write_text(json.dumps(
        {"other": {"outcome": "failed", "detail": "d", "at": "2026-07-29T00:00:00+00:00"}}
    ))

    _refresh_managed_credentials(
        tmp_path, config, {}, {"claude"}, started="2026-07-30T00:00:00+00:00"
    )

    records = _record(tmp_path)
    assert set(records) == {"other", "claude"}
    assert list(orchestra_dir.glob("auth-refresh.json.tmp")) == []
