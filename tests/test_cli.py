import json

import pytest

from orchestra.attempt import AttemptStore
from orchestra.cli import main


def test_guide_prints_integration_doc(capsys):
    rc = main(["guide"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "orchestra issue add" in out
    assert "awaiting_review -> needs_rework" in out
    assert "blocked -> open" in out
    assert "Do not run `orchestra tick`" in out
    assert "host scheduler" in out
    assert "transient user systemd service" in out


def test_root_defaults_to_none(monkeypatch):
    from orchestra.cli import build_parser
    monkeypatch.setenv("ORCHESTRA_ROOT", "/tmp/some-root")
    args = build_parser().parse_args(["guide"])
    assert args.root is None


def test_workspace_show_uses_upward_discovery(tmp_path, monkeypatch, capsys):
    root = tmp_path / "workspace"
    nested = root / "projects" / "demo"
    nested.mkdir(parents=True)
    (root / "config.yaml").write_text("slots: 1\n")
    (root / "PROJECTS.md").write_text("# Projects\n")
    monkeypatch.chdir(nested)
    monkeypatch.delenv("ORCHESTRA_ROOT", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))

    assert main(["workspace", "show"]) == 0
    assert capsys.readouterr().out.strip() == str(root.resolve())


def test_root_after_subcommand(tmp_path, capsys):
    """--root must work AFTER the subcommand too (systemd/docs write `orchestra tick --root X`)."""
    from orchestra.cli import main
    (tmp_path / "queue").mkdir()
    (tmp_path / "PROJECTS.md").write_text("# Projects\n")
    (tmp_path / "config.yaml").write_text("slots: 1\n")
    # status reads the root; --root after the subcommand must be honored
    rc = main(["status", "--root", str(tmp_path), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"counts"' in out  # ran against tmp_path, not cwd


def test_root_equals_form_after_subcommand(tmp_path):
    from orchestra.cli import main
    (tmp_path / "queue").mkdir()
    (tmp_path / "PROJECTS.md").write_text("# Projects\n")
    (tmp_path / "config.yaml").write_text("slots: 1\n")
    assert main(["status", f"--root={tmp_path}", "--json"]) == 0


def test_issue_list_surfaces_blocked_dependency(tmp_path, capsys):
    from orchestra.cli import main
    (tmp_path / "queue").mkdir()
    (tmp_path / "PROJECTS.md").write_text(
        "# Projects\n\n## wf\n- Path: projects/wf\n- Branch: main\n"
        "- Purpose: t\n- Queue: queue/wf.md\n- Focus: none\n"
    )
    (tmp_path / "config.yaml").write_text("slots: 1\n")

    def _issue(num, status, deps="null"):
        return (
            f"## #{num:03d} wf: t{num}\nStatus: {status}\nPriority: 1\nPlan: null\nSpec: null\n"
            f"Depends On: {deps}\nRetries: 0\nWorker: null\nAcceptance:\n- [ ] x\n"
            f"### Decisions\n### Blocked Reason\n"
        )
    (tmp_path / "queue" / "wf.md").write_text(_issue(1, "blocked") + "\n" + _issue(2, "validated", "1"))

    rc = main(["issue", "list", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    # #002 depends on the blocked #001 -> surfaced
    line2 = [ln for ln in out.splitlines() if "#002" in ln][0]
    assert "blocked dep #1" in line2


def _write_harness_config(
    root, *, kind="codex", policy="isolated", instructions_file=None,
):
    (root / "PROJECTS.md").write_text("# Projects\n")
    (root / "config.yaml").write_text(
        "slots: 0\n"
        "roles: {}\n"
        "harnesses:\n"
        "  automation:\n"
        f"    kind: {kind}\n"
        f"    executable: {kind}\n"
        "    environment:\n"
        f"      policy: {policy}\n"
        "      state_dir: .orchestra/homes/codex\n"
        + (f"      instructions_file: {instructions_file}\n" if instructions_file else "")
    )


def test_harness_setup_creates_private_codex_home_without_copying_auth(tmp_path, capsys):
    import stat

    _write_harness_config(tmp_path)

    assert main(["--root", str(tmp_path), "harness", "setup", "automation"]) == 0

    state_dir = tmp_path / ".orchestra" / "homes" / "codex"
    assert state_dir.is_dir()
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
    assert not (state_dir / "auth.json").exists()
    assert capsys.readouterr().out.strip() == f"CODEX_HOME={state_dir} codex login"


def test_harness_setup_installs_configured_automation_instructions(tmp_path, capsys):
    import stat

    source = tmp_path / "automation" / "AGENTS.md"
    source.parent.mkdir()
    source.write_text("automation standards\n")
    _write_harness_config(tmp_path, instructions_file="automation/AGENTS.md")

    assert main(["--root", str(tmp_path), "harness", "setup", "automation"]) == 0
    installed = tmp_path / ".orchestra" / "homes" / "codex" / "AGENTS.md"
    assert installed.read_text() == source.read_text()
    assert stat.S_IMODE(installed.stat().st_mode) == 0o600


def test_harness_setup_refuses_codex_override_that_would_shadow_instructions(
    tmp_path, capsys,
):
    source = tmp_path / "automation" / "AGENTS.md"
    source.parent.mkdir()
    source.write_text("automation standards\n")
    _write_harness_config(tmp_path, instructions_file="automation/AGENTS.md")
    state_dir = tmp_path / ".orchestra" / "homes" / "codex"
    state_dir.mkdir(parents=True)
    override = state_dir / "AGENTS.override.md"
    override.write_text("unexpected override\n")

    assert main(["--root", str(tmp_path), "harness", "setup", "automation"]) == 1
    assert override.read_text() == "unexpected override\n"
    assert "AGENTS.override.md" in capsys.readouterr().err


def test_harness_setup_creates_isolated_claude_home(tmp_path, capsys):
    _write_harness_config(tmp_path, kind="claude")

    assert main(["--root", str(tmp_path), "harness", "setup", "automation"]) == 0
    state_dir = tmp_path / ".orchestra" / "homes" / "codex"
    assert state_dir.is_dir()
    assert capsys.readouterr().out.strip() == (
        f"CLAUDE_CONFIG_DIR={state_dir} claude auth login"
    )


def test_harness_doctor_json_checks_preflight_and_isolated_login(
    tmp_path, monkeypatch, capsys,
):
    import json
    import subprocess

    import orchestra.cli as cli

    _write_harness_config(tmp_path)
    state_dir = tmp_path / ".orchestra" / "homes" / "codex"
    state_dir.mkdir(parents=True)
    state_dir.chmod(0o700)
    monkeypatch.setattr(cli, "preflight_harness", lambda kind, executable: "codex-cli 9.9")
    monkeypatch.setattr(cli.shutil, "which", lambda executable: "/usr/bin/codex")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "Logged in", "")

    monkeypatch.setattr(cli.subprocess, "run", run)

    assert main([
        "--root", str(tmp_path), "harness", "doctor", "automation", "--json",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "name": "automation",
        "kind": "codex",
        "policy": "isolated",
        "state_dir": str(state_dir),
        "state_dir_exists": True,
        "state_dir_writable": True,
        "state_dir_private": True,
        "executable": "/usr/bin/codex",
        "version": "codex-cli 9.9",
        "preflight": "passed",
        "login": "authenticated",
        "access_token_expires_at": None,
        "access_token_expires_in_days": None,
        "refresh_token_expires_at": None,
        "refresh_token_expires_in_days": None,
        "refresh_token_warning": False,
        "probe": "not_applicable",
        "instructions": "not_configured",
        "ready": True,
    }
    assert calls[0][0] == ["codex", "login", "status"]
    assert calls[0][1]["env"]["CODEX_HOME"] == str(state_dir)


def test_harness_doctor_is_nonzero_when_isolated_home_is_not_ready(
    tmp_path, monkeypatch, capsys,
):
    import json

    import orchestra.cli as cli

    _write_harness_config(tmp_path)
    monkeypatch.setattr(cli, "preflight_harness", lambda kind, executable: "codex-cli 9.9")
    monkeypatch.setattr(cli.shutil, "which", lambda executable: "/usr/bin/codex")

    assert main([
        "--root", str(tmp_path), "harness", "doctor", "automation", "--json",
    ]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["state_dir_exists"] is False
    assert report["login"] == "not_checked"
    assert report["ready"] is False


def test_harness_doctor_checks_claude_login_under_the_credential_lock(
    tmp_path, monkeypatch, capsys,
):
    """Doctor's Claude auth check IS the refresh trigger, so it must hold the same lock
    the dispatch refresher holds — otherwise it can rotate the shared token out from
    under a dispatch that is seeding it."""
    import fcntl
    import json

    from orchestra import auth
    import orchestra.cli as cli

    _write_harness_config(tmp_path, kind="claude")
    state_dir = tmp_path / ".orchestra" / "homes" / "codex"
    state_dir.mkdir(parents=True)
    state_dir.chmod(0o700)
    monkeypatch.setattr(cli, "preflight_harness", lambda kind, executable: "claude 9.9")
    monkeypatch.setattr(cli.shutil, "which", lambda executable: "/usr/bin/claude")
    observed = {}

    def fake(command, environment):
        contender = open(auth._lock_path(state_dir), "w")
        try:
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            observed["locked"] = False
        except BlockingIOError:
            observed["locked"] = True
        finally:
            contender.close()
        observed["command"] = command
        observed["home"] = environment["CLAUDE_CONFIG_DIR"]
        return 0

    monkeypatch.setattr(auth, "run_auth_status_command", fake)

    assert main([
        "--root", str(tmp_path), "harness", "doctor", "automation", "--json",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["login"] == "authenticated"
    assert observed["command"] == ["claude", "auth", "status", "--json"]
    assert observed["home"] == str(state_dir)
    assert observed["locked"] is True


def test_harness_doctor_reports_not_authenticated_when_the_check_fails(
    tmp_path, monkeypatch, capsys,
):
    import json

    from orchestra import auth
    import orchestra.cli as cli

    _write_harness_config(tmp_path, kind="claude")
    state_dir = tmp_path / ".orchestra" / "homes" / "codex"
    state_dir.mkdir(parents=True)
    state_dir.chmod(0o700)
    monkeypatch.setattr(cli, "preflight_harness", lambda kind, executable: "claude 9.9")
    monkeypatch.setattr(cli.shutil, "which", lambda executable: "/usr/bin/claude")
    monkeypatch.setattr(auth, "run_auth_status_command", lambda command, env: 1)

    assert main([
        "--root", str(tmp_path), "harness", "doctor", "automation", "--json",
    ]) == 1
    assert json.loads(capsys.readouterr().out)["login"] == "not_authenticated"


def test_harness_setup_rejects_unsupported_environment(tmp_path, capsys):
    _write_harness_config(tmp_path, policy="ambient")

    assert main(["--root", str(tmp_path), "harness", "setup", "automation"]) == 2
    assert "isolated Codex or Claude" in capsys.readouterr().err


def test_engine_provenance_compare_reports_mismatch(tmp_path, capsys):
    package = tmp_path / "src" / "orchestra"
    package.mkdir(parents=True)
    (package / "module.py").write_text("different\n")

    assert main(["engine", "provenance", "--compare", str(tmp_path), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["matches"] is False
    assert report["comparison_root"] == str(package)


def test_attempt_explain_surfaces_provenance_and_terminal_evidence(tmp_path, capsys):
    (tmp_path / "PROJECTS.md").write_text("# Projects\n")
    attempt = AttemptStore(tmp_path).create(
        attempt_id="a1", project="demo", number=1, role="worker", harness="codex",
        model="m", worktree=tmp_path, branch="issue/1", start_commit="abc",
        prompt="do it", instruction_bundle="rules", configuration={}, capabilities={},
        parent_attempt=None,
    )
    AttemptStore(tmp_path).update(
        attempt, state="completed", terminal_outcome="turn_failed",
        failure_category="authentication_failure", failure_evidence="not logged in",
        instruction_policy="native_project", delegation_policy="disabled",
        execution_envelope_sha256="e" * 64,
    )

    assert main([
        "--root", str(tmp_path), "attempt", "explain", "a1", "--json",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["attempt_id"] == "a1"
    assert report["failure_category"] == "authentication_failure"
    assert report["instruction_policy"] == "native_project"
    assert report["artifacts"]["manifest"]["exists"] is True


def test_status_prints_a_held_or_failed_central_refresh(tmp_path, capsys):
    import json

    (tmp_path / "queue").mkdir()
    (tmp_path / "PROJECTS.md").write_text("# Projects\n")
    (tmp_path / "config.yaml").write_text("slots: 1\n")
    orchestra_dir = tmp_path / ".orchestra"
    orchestra_dir.mkdir()
    (orchestra_dir / "auth-refresh.json").write_text(json.dumps({
        "claude": {"outcome": "failed", "detail": "auth status exited 1",
                   "at": "2026-07-30T00:00:00+00:00"},
        "codex": {"outcome": "refreshed", "detail": "", "at": "2026-07-30T00:00:00+00:00"},
    }))

    assert main(["status", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "auth: claude refresh failed" in out
    assert "auth status exited 1" in out
    assert "codex" not in out          # a healthy refresh is not an operator concern


CLAUDE_DOCTOR_CONFIG = """\
slots: 1
refresh_margin_seconds: 18000
roles:
  validator: {harness: automation, model: m, prompt: p.md}
  worker: {harness: automation, model: m, prompt: p.md,
           instruction_policy: explicit_bundle}
  verifier: {harness: automation, model: m, prompt: p.md}
harnesses:
  automation:
    kind: claude
    executable: claude
    environment: {policy: isolated, state_dir: .orchestra/homes/claude}
"""


def _doctor_setup(root, monkeypatch, *, expires_in, refresh_expires_in=30 * 86400):
    """An isolated Claude harness with a synthetic credential `expires_in` seconds out
    (access token) and `refresh_expires_in` seconds out (refresh token)."""
    import json
    import time

    import orchestra.cli as cli

    (root / "PROJECTS.md").write_text("# Projects\n")
    (root / "config.yaml").write_text(CLAUDE_DOCTOR_CONFIG)
    state_dir = root / ".orchestra" / "homes" / "claude"
    state_dir.mkdir(parents=True)
    state_dir.chmod(0o700)
    (state_dir / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "a", "refreshToken": "r",
        "expiresAt": int((time.time() + expires_in) * 1000),
        "refreshTokenExpiresAt": int((time.time() + refresh_expires_in) * 1000),
    }}))
    monkeypatch.setattr(cli, "preflight_harness", lambda kind, executable: "claude 9.9")
    monkeypatch.setattr(cli.shutil, "which", lambda executable: "/usr/bin/claude")
    return state_dir


def _live_worker_registry(root):
    """One live worker of the `automation` harness (the `worker` role uses it)."""
    import json
    import os

    from orchestra.selection import process_start_time

    pid = os.getpid()
    (root / ".orchestra").mkdir(parents=True, exist_ok=True)
    (root / ".orchestra" / "workers.json").write_text(json.dumps({"wf#001": {
        "project": "wf", "number": 1, "role": "worker", "branch": "issue/001-x",
        "worktree": "/tmp/wt", "pid": pid, "attempt_id": "a1",
        "manifest": "/tmp/a1/manifest.json", "stdout": "/tmp/a1/stdout.jsonl",
        "stderr": "/tmp/a1/stderr.log", "started": "2026-07-30T00:00:00+00:00",
        "start_sha": "abc", "proc_start": process_start_time(pid) or "",
        "supervisor_unit": "",
    }}))


def test_harness_doctor_refuses_to_rotate_while_workers_are_active(
    tmp_path, monkeypatch, capsys,
):
    """Doctor's Claude auth check IS the refresh trigger: near expiry it rotates, revoking
    the token every in-flight worker is holding. The operator flow this protects is the
    obvious one — status reports a held refresh, the operator runs doctor to find out why."""
    import json

    from orchestra import auth

    state_dir = _doctor_setup(tmp_path, monkeypatch, expires_in=600)
    _live_worker_registry(tmp_path)
    calls = []
    monkeypatch.setattr(
        auth, "run_auth_status_command",
        lambda command, environment: calls.append(command) or 0,
    )

    def _refuse(home):
        raise AssertionError("the probe must not run in the workers-active refusal path")

    monkeypatch.setattr(auth, "probe_shared_credential", _refuse)

    assert main([
        "--root", str(tmp_path), "harness", "doctor", "automation", "--json",
    ]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["login"] == "not_checked_workers_active"
    assert report["probe"] == "skipped_workers_active"
    assert report["ready"] is False
    assert calls == []
    # The credential is left exactly as it was.
    assert json.loads((state_dir / ".credentials.json").read_text())[
        "claudeAiOauth"]["accessToken"] == "a"


def test_harness_doctor_checks_login_once_the_harness_has_drained(
    tmp_path, monkeypatch, capsys,
):
    import json

    from orchestra import auth

    _doctor_setup(tmp_path, monkeypatch, expires_in=600)   # stale, but nothing is running
    calls = []
    monkeypatch.setattr(
        auth, "run_auth_status_command",
        lambda command, environment: calls.append(command) or 0,
    )

    # Doctor's authenticated-login path also runs the revocation probe by default; --no-probe
    # keeps this test (which is about the drain/login gating, not the probe) off the network.
    assert main([
        "--root", str(tmp_path), "harness", "doctor", "automation", "--json", "--no-probe",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["login"] == "authenticated"
    assert report["probe"] == "disabled"
    assert calls == [["claude", "auth", "status", "--json"]]


def test_harness_doctor_stays_usable_while_the_token_has_life_left(
    tmp_path, monkeypatch, capsys,
):
    """Outside the refresh margin the trigger has nothing to rotate, so doctor must keep
    working during ordinary operation — which is exactly when an operator reaches for it."""
    import json

    from orchestra import auth

    _doctor_setup(tmp_path, monkeypatch, expires_in=40000)
    _live_worker_registry(tmp_path)
    calls = []
    monkeypatch.setattr(
        auth, "run_auth_status_command",
        lambda command, environment: calls.append(command) or 0,
    )

    assert main([
        "--root", str(tmp_path), "harness", "doctor", "automation", "--json", "--no-probe",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["login"] == "authenticated"
    assert calls == [["claude", "auth", "status", "--json"]]


# --- expiry readout and revocation probe -------------------------------------------


def test_harness_doctor_reports_expiry_readout_with_days_remaining(
    tmp_path, monkeypatch, capsys,
):
    from orchestra import auth

    _doctor_setup(tmp_path, monkeypatch, expires_in=40000, refresh_expires_in=20 * 86400)
    monkeypatch.setattr(auth, "run_auth_status_command", lambda command, environment: 0)
    monkeypatch.setattr(auth, "probe_shared_credential", lambda home: auth.VALID)

    assert main([
        "--root", str(tmp_path), "harness", "doctor", "automation", "--json",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["access_token_expires_at"] is not None
    assert report["access_token_expires_in_days"] == pytest.approx(40000 / 86400, abs=0.01)
    assert report["refresh_token_expires_at"] is not None
    assert report["refresh_token_expires_in_days"] == pytest.approx(20.0, abs=0.01)
    assert report["refresh_token_warning"] is False
    assert report["probe"] == "valid"


def test_harness_doctor_warns_when_the_refresh_horizon_is_close(
    tmp_path, monkeypatch, capsys,
):
    from orchestra import auth

    _doctor_setup(tmp_path, monkeypatch, expires_in=40000, refresh_expires_in=3 * 86400)
    monkeypatch.setattr(auth, "run_auth_status_command", lambda command, environment: 0)
    monkeypatch.setattr(auth, "probe_shared_credential", lambda home: auth.VALID)

    assert main([
        "--root", str(tmp_path), "harness", "doctor", "automation", "--json",
    ]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["refresh_token_warning"] is True
    assert "WARNING" in captured.err
    assert "refresh token expires" in captured.err


def test_harness_doctor_reports_revoked_and_the_exit_code_is_nonzero(
    tmp_path, monkeypatch, capsys,
):
    from orchestra import auth

    _doctor_setup(tmp_path, monkeypatch, expires_in=40000)
    monkeypatch.setattr(auth, "run_auth_status_command", lambda command, environment: 0)
    monkeypatch.setattr(auth, "probe_shared_credential", lambda home: auth.REVOKED)

    assert main([
        "--root", str(tmp_path), "harness", "doctor", "automation", "--json",
    ]) == 1
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["probe"] == "revoked"
    assert report["ready"] is False
    assert "orchestra harness login claude" in captured.err


def test_harness_doctor_probe_unreachable_is_a_warning_not_a_failure(
    tmp_path, monkeypatch, capsys,
):
    from orchestra import auth

    _doctor_setup(tmp_path, monkeypatch, expires_in=40000)
    monkeypatch.setattr(auth, "run_auth_status_command", lambda command, environment: 0)
    monkeypatch.setattr(auth, "probe_shared_credential", lambda home: auth.UNREACHABLE)

    assert main([
        "--root", str(tmp_path), "harness", "doctor", "automation", "--json",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["probe"] == "unreachable"
    assert report["ready"] is True


def test_harness_doctor_no_probe_skips_the_network_entirely(
    tmp_path, monkeypatch, capsys,
):
    from orchestra import auth

    _doctor_setup(tmp_path, monkeypatch, expires_in=40000)
    monkeypatch.setattr(auth, "run_auth_status_command", lambda command, environment: 0)

    def _refuse(home):
        raise AssertionError("--no-probe must not touch the network")

    monkeypatch.setattr(auth, "probe_shared_credential", _refuse)

    assert main([
        "--root", str(tmp_path), "harness", "doctor", "automation", "--json", "--no-probe",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["probe"] == "disabled"


def test_harness_doctor_probe_is_not_checked_when_the_auth_status_path_never_ran(
    tmp_path, monkeypatch, capsys,
):
    """A Claude harness whose executable can't be found never enters the auth-status branch
    at all (`login` stays "not_checked"); the probe must not run either — there is no
    meaningfully current token to say anything about."""
    import orchestra.cli as cli
    from orchestra import auth

    _doctor_setup(tmp_path, monkeypatch, expires_in=40000)
    monkeypatch.setattr(cli.shutil, "which", lambda executable: None)

    def _refuse(home):
        raise AssertionError("the probe must not run when the auth-status path never ran")

    monkeypatch.setattr(auth, "probe_shared_credential", _refuse)

    assert main([
        "--root", str(tmp_path), "harness", "doctor", "automation", "--json",
    ]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["login"] == "not_checked"
    assert report["probe"] == "not_checked"


def test_harness_doctor_probe_never_prints_the_access_token_value(
    tmp_path, monkeypatch, capsys,
):
    """Faking at the `_open_probe` seam (rather than `probe_shared_credential`) exercises
    doctor's full read-credential-and-probe path, so this actually proves the token value
    read from disk never reaches stdout or stderr."""
    import time as _time
    import urllib.error

    from orchestra import auth

    state_dir = _doctor_setup(tmp_path, monkeypatch, expires_in=40000)
    (state_dir / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "very-secret-access-token-value",
        "refreshToken": "r",
        "expiresAt": int((_time.time() + 40000) * 1000),
        "refreshTokenExpiresAt": int((_time.time() + 30 * 86400) * 1000),
    }}))
    monkeypatch.setattr(auth, "run_auth_status_command", lambda command, environment: 0)

    def fake_open_probe(token, timeout):
        assert token == "very-secret-access-token-value"
        raise urllib.error.HTTPError(auth.PROBE_URL, 401, "unauthorized", {}, None)

    monkeypatch.setattr(auth, "_open_probe", fake_open_probe)

    assert main([
        "--root", str(tmp_path), "harness", "doctor", "automation", "--json",
    ]) == 1
    out = capsys.readouterr()
    assert "very-secret-access-token-value" not in out.out
    assert "very-secret-access-token-value" not in out.err
    assert json.loads(out.out)["probe"] == "revoked"
