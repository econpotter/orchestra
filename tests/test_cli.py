import json

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
