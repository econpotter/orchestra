from pathlib import Path

import pytest

from orchestra.config import load_config

CONFIG = """\
slots: 5
roles:
  validator: { harness: claude, model: claude-haiku-4-5, prompt: prompts/validator.md }
  worker:    { harness: claude, model: claude-opus-4-8,  prompt: prompts/worker.md }
  verifier:  { harness: claude, model: claude-opus-4-8,  prompt: prompts/verify-review.md }
validate:
  semantic: true
stall:
  idle_minutes: 0
"""


def test_load_config(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(CONFIG)
    cfg = load_config(p)
    assert cfg.slots == 5
    assert cfg.roles["worker"].model == "claude-opus-4-8"
    assert cfg.roles["validator"].prompt == "prompts/validator.md"
    assert cfg.validate_semantic is True
    assert cfg.autoapprove is False
    assert cfg.hold_network_issues is False


def test_config_defaults(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("slots: 3\nroles: {}\n")
    cfg = load_config(p)
    assert cfg.validate_semantic is False  # opt-in: deterministic validation by default
    assert cfg.autoapprove is False
    assert cfg.hold_network_issues is False


def test_network_hold_is_configurable(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("slots: 1\nroles: {}\nhold_network_issues: true\n")
    assert load_config(p).hold_network_issues is True


def test_network_hold_rejects_non_boolean_values(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text('slots: 1\nroles: {}\nhold_network_issues: "false"\n')
    with pytest.raises(ValueError, match="hold_network_issues must be a boolean"):
        load_config(p)


HARNESSES_CONFIG = """\
slots: 5
retries_cap: 3
roles:
  worker: { harness: claude, model: claude-opus-4-8, prompt: prompts/worker.md }
harnesses:
  claude:
    kind: claude
    executable: claude
    attempts_cap: 4
sandbox:
  enabled: false
  kind: systemd
  executable: systemd-run
"""


def test_load_harnesses_and_sandbox(tmp_path):
    from orchestra.config import load_config
    p = tmp_path / "config.yaml"
    p.write_text(HARNESSES_CONFIG)
    cfg = load_config(p)
    assert cfg.retries_cap == 3
    assert cfg.harnesses["claude"].executable == "claude"
    assert cfg.harnesses["claude"].attempts_cap == 4
    assert cfg.sandbox.enabled is False
    assert cfg.sandbox.kind == "systemd"


def test_config_harness_defaults(tmp_path):
    from orchestra.config import load_config
    p = tmp_path / "config.yaml"
    p.write_text("slots: 2\nroles: {}\n")
    cfg = load_config(p)
    assert cfg.harnesses == {}
    assert cfg.sandbox.enabled is False
    assert cfg.sandbox.executable == "systemd-run"
    assert cfg.sandbox.filesystem_executable == "bwrap"
    assert cfg.sandbox.tmpfs_paths == ("~/.cache",)
    assert cfg.retries_cap == 2
    assert cfg.roles["worker"].prompt if cfg.roles else True  # roles untouched


def test_harness_limits_are_configurable(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("slots: 1\nroles: {}\nharnesses:\n  c:\n    kind: codex\n"
                 "    executable: codex\n    limits: {wall_seconds: 99}\n")
    assert load_config(p).harnesses["c"].limits.wall_seconds == 99


def test_role_and_harness_environment_policies_are_configurable(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "slots: 1\n"
        "roles:\n"
        "  worker:\n"
        "    harness: c\n"
        "    model: m\n"
        "    prompt: prompts/worker.md\n"
        "    instruction_policy: explicit_bundle\n"
        "    delegation: required\n"
        "harnesses:\n"
        "  c:\n"
        "    kind: codex\n"
        "    executable: codex\n"
        "    environment: {policy: isolated, state_dir: .orchestra/homes/codex, "
        "instructions_file: automation/AGENTS.md, "
        "verified_capabilities: [isolates_user_skills]}\n"
    )

    cfg = load_config(p)

    assert cfg.roles["worker"].instruction_policy == "explicit_bundle"
    assert cfg.roles["worker"].delegation == "required"
    assert cfg.harnesses["c"].environment.policy == "isolated"
    assert cfg.harnesses["c"].environment.state_dir == ".orchestra/homes/codex"
    assert cfg.harnesses["c"].environment.instructions_file == "automation/AGENTS.md"
    assert cfg.harnesses["c"].environment.verified_capabilities == (
        "isolates_user_skills",
    )


def test_role_and_harness_environment_policies_have_safe_defaults(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "slots: 1\n"
        "roles:\n"
        "  worker: {harness: c, model: m, prompt: prompts/worker.md}\n"
        "harnesses:\n"
        "  c: {kind: codex, executable: codex}\n"
    )

    cfg = load_config(p)

    assert cfg.roles["worker"].instruction_policy == "native_project"
    assert cfg.roles["worker"].delegation == "disabled"
    assert cfg.harnesses["c"].environment.policy == "ambient"
    assert cfg.harnesses["c"].environment.state_dir is None
    assert cfg.harnesses["c"].environment.instructions_file is None
    assert cfg.harnesses["c"].environment.verified_capabilities == ()


def test_validate_config_ok(tmp_path):
    from orchestra.config import load_config, validate_config
    p = tmp_path / "config.yaml"
    p.write_text(
        "slots: 2\n"
        "roles:\n"
        "  validator: { harness: claude, model: m, prompt: prompts/validator.md }\n"
        "  worker:    { harness: claude, model: m, prompt: prompts/worker.md }\n"
        "  verifier:  { harness: claude, model: m, prompt: prompts/verify-review.md }\n"
        "harnesses:\n"
        "  claude: { kind: claude, executable: claude }\n"
    )
    validate_config(load_config(p))  # no raise


def test_validate_config_unknown_harness(tmp_path):
    import pytest
    from orchestra.config import load_config, validate_config
    p = tmp_path / "config.yaml"
    p.write_text(
        "slots: 2\n"
        "roles:\n"
        "  validator: { harness: claude, model: m, prompt: prompts/validator.md }\n"
        "  worker:    { harness: codex,  model: m, prompt: prompts/worker.md }\n"
        "  verifier:  { harness: claude, model: m, prompt: prompts/verify-review.md }\n"
        "harnesses:\n"
        "  claude: { kind: claude, executable: claude }\n"
    )
    with pytest.raises(ValueError, match="codex"):
        validate_config(load_config(p))


def test_validate_config_missing_role(tmp_path):
    import pytest
    from orchestra.config import load_config, validate_config
    p = tmp_path / "config.yaml"
    p.write_text(
        "slots: 2\n"
        "roles:\n"
        "  validator: { harness: claude, model: m, prompt: prompts/validator.md }\n"
        "harnesses:\n"
        "  claude: { kind: claude, executable: claude }\n"
    )
    with pytest.raises(ValueError, match="worker"):
        validate_config(load_config(p))


WORKFLOWS_CONFIG = """\
slots: 3
roles: {}
workflows:
  python:
    lint: "uv run ruff check"
    test: "uv run pytest"
    typecheck: "uv run mypy src"
verify:
  rerun_checks: true
review:
  autoapprove: true
"""


def test_load_workflows_and_verify(tmp_path):
    from orchestra.config import load_config
    p = tmp_path / "config.yaml"
    p.write_text(WORKFLOWS_CONFIG)
    cfg = load_config(p)
    assert cfg.workflows["python"]["test"] == "uv run pytest"
    assert cfg.verify_rerun_checks is True
    assert cfg.autoapprove is True


def test_workflow_verify_defaults(tmp_path):
    from orchestra.config import load_config
    p = tmp_path / "config.yaml"
    p.write_text("slots: 1\nroles: {}\n")
    cfg = load_config(p)
    assert cfg.workflows == {}
    assert cfg.verify_rerun_checks is False


def test_template_path_and_r_workflow(tmp_path):
    from orchestra.config import load_config
    p = tmp_path / "config.yaml"
    p.write_text(
        "slots: 1\nroles: {}\n"
        "template_path: projects/project-template\n"
        "workflows:\n  r:\n    lint: \"Rscript -e 'lintr::lint_dir()'\"\n"
        "    test: \"Rscript -e 'devtools::test()'\"\n"
    )
    cfg = load_config(p)
    assert cfg.template_path == "projects/project-template"
    assert cfg.workflows["r"]["test"] == "Rscript -e 'devtools::test()'"


def test_template_path_default(tmp_path):
    from orchestra.config import load_config
    p = tmp_path / "config.yaml"
    p.write_text("slots: 1\nroles: {}\n")
    assert load_config(p).template_path == "projects/project-template"


def test_semantic_requires_sandbox(tmp_path):
    from orchestra.config import Config, HarnessConfig, RoleConfig, Sandbox, validate_config
    import pytest
    roles = {r: RoleConfig(harness="claude", model="m", prompt="p")
             for r in ("validator", "worker", "verifier")}
    harnesses = {"claude": HarnessConfig(kind="claude", executable="claude")}
    base = dict(slots=1, roles=roles, harnesses=harnesses,
                retries_cap=2, workflows={}, verify_rerun_checks=False, autoapprove=False,
                template_path="projects/project-template")
    bad = Config(validate_semantic=True, sandbox=Sandbox(enabled=False), **base)
    with pytest.raises(ValueError, match="sandbox"):
        validate_config(bad)
    ok = Config(validate_semantic=True, sandbox=Sandbox(enabled=True), **base)
    validate_config(ok)  # sandbox on → allowed
    off = Config(validate_semantic=False, sandbox=Sandbox(enabled=False), **base)
    validate_config(off)  # semantic off (default) → allowed


def test_isolated_harness_requires_outer_sandbox(tmp_path: Path):
    from orchestra.config import load_config, validate_config

    p = tmp_path / "config.yaml"
    p.write_text(
        "slots: 1\n"
        "roles:\n"
        "  validator: {harness: codex, model: m, prompt: p}\n"
        "  worker: {harness: codex, model: m, prompt: p}\n"
        "  verifier: {harness: codex, model: m, prompt: p}\n"
        "harnesses:\n"
        "  codex:\n"
        "    kind: codex\n"
        "    executable: codex\n"
        "    environment: {policy: isolated, "
        "verified_capabilities: [isolates_user_skills]}\n"
        "sandbox: {enabled: false}\n"
    )
    with pytest.raises(ValueError, match="isolation requires sandbox"):
        validate_config(load_config(p))


def test_required_isolation_capability_uses_effective_envelope(tmp_path: Path):
    from orchestra.config import load_config, validate_config

    p = tmp_path / "config.yaml"
    p.write_text(
        "slots: 1\n"
        "roles:\n"
        "  validator: {harness: codex, model: m, prompt: p}\n"
        "  worker:\n"
        "    harness: codex\n"
        "    model: m\n"
        "    prompt: p\n"
        "    required_capabilities: [isolates_user_skills]\n"
        "  verifier: {harness: codex, model: m, prompt: p}\n"
        "harnesses:\n"
        "  codex:\n"
        "    kind: codex\n"
        "    executable: codex\n"
        "    environment: {policy: isolated, "
        "verified_capabilities: [isolates_user_skills]}\n"
        "sandbox: {enabled: true}\n"
    )
    validate_config(load_config(p))


def test_codex_rejects_explicit_bundle_because_native_discovery_cannot_be_disabled(
    tmp_path: Path,
):
    from orchestra.config import load_config, validate_config

    p = tmp_path / "config.yaml"
    p.write_text(
        "slots: 1\n"
        "roles:\n"
        "  validator: {harness: codex, model: m, prompt: p}\n"
        "  worker: {harness: codex, model: m, prompt: p, instruction_policy: explicit_bundle}\n"
        "  verifier: {harness: codex, model: m, prompt: p}\n"
        "harnesses:\n  codex: {kind: codex, executable: codex}\n"
    )
    with pytest.raises(ValueError, match="native_project"):
        validate_config(load_config(p))


def _policy_config():
    from orchestra.config import Config, HarnessConfig, RoleConfig, Sandbox

    roles = {
        role: RoleConfig(harness="codex", model="m", prompt="p")
        for role in ("validator", "worker", "verifier")
    }
    return Config(
        slots=1,
        roles=roles,
        validate_semantic=False,
        harnesses={"codex": HarnessConfig(kind="codex", executable="codex")},
        sandbox=Sandbox(enabled=False),
        retries_cap=2,
        workflows={},
        verify_rerun_checks=False,
        autoapprove=False,
        template_path="projects/project-template",
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("instruction_policy", "ambient", "instruction_policy"),
        ("delegation", "sometimes", "delegation"),
    ],
)
def test_validate_config_rejects_invalid_role_policy(field, value, message):
    from orchestra.config import validate_config

    config = _policy_config()
    setattr(config.roles["worker"], field, value)

    with pytest.raises(ValueError, match=message):
        validate_config(config)


def test_validate_config_rejects_invalid_harness_environment_policy():
    from orchestra.config import HarnessEnvironment, validate_config

    config = _policy_config()
    config.harnesses["codex"].environment = HarnessEnvironment(policy="private")

    with pytest.raises(ValueError, match="environment policy"):
        validate_config(config)


def test_validate_config_rejects_invalid_policy_on_unused_harness():
    from orchestra.config import HarnessConfig, HarnessEnvironment, validate_config

    config = _policy_config()
    config.harnesses["unused"] = HarnessConfig(
        kind="claude",
        executable="claude",
        environment=HarnessEnvironment(policy="private"),
    )

    with pytest.raises(ValueError, match="unused.*environment policy"):
        validate_config(config)


def test_validate_config_rejects_instructions_file_without_isolated_codex():
    from orchestra.config import validate_config

    config = _policy_config()
    config.harnesses["codex"].environment.instructions_file = "automation/AGENTS.md"
    config.harnesses["codex"].environment.policy = "ambient"

    with pytest.raises(ValueError, match="instructions_file.*isolated Codex"):
        validate_config(config)


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--enable", "multi_agent"],
        ["--disable=multi_agent"],
        ["--config", "features.multi_agent=false"],
    ],
)
def test_validate_config_rejects_multi_agent_extra_args(extra_args):
    from orchestra.config import validate_config

    config = _policy_config()
    config.harnesses["codex"].extra_args = extra_args

    with pytest.raises(ValueError, match="delegation.*multi_agent"):
        validate_config(config)
