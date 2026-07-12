from pathlib import Path

from orchestra.config import load_config

CONFIG = """\
slots: 5
roles:
  validator: { provider: claude, model: claude-haiku-4-5, prompt: prompts/validator.md }
  worker:    { provider: claude, model: claude-opus-4-8,  prompt: prompts/worker.md }
  verifier:  { provider: claude, model: claude-opus-4-8,  prompt: prompts/verify-review.md }
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
    assert cfg.stall_idle_minutes == 0
    assert cfg.autoapprove is False


def test_config_defaults(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("slots: 3\nroles: {}\n")
    cfg = load_config(p)
    assert cfg.validate_semantic is False  # opt-in: deterministic validation by default
    assert cfg.stall_idle_minutes == 0
    assert cfg.autoapprove is False


PROVIDERS_CONFIG = """\
slots: 5
retries_cap: 3
roles:
  worker: { provider: claude, model: claude-opus-4-8, prompt: prompts/worker.md }
providers:
  claude:
    argv: ["claude", "-p", "--model", "{model}", "--dangerously-skip-permissions"]
    prompt: stdin
sandbox:
  enabled: false
  argv_prefix: ["bwrap", "--ro-bind", "/", "/", "--bind", "{workdir}", "{workdir}"]
"""


def test_load_providers_and_sandbox(tmp_path):
    from orchestra.config import load_config
    p = tmp_path / "config.yaml"
    p.write_text(PROVIDERS_CONFIG)
    cfg = load_config(p)
    assert cfg.retries_cap == 3
    assert cfg.providers["claude"].argv[0] == "claude"
    assert cfg.providers["claude"].prompt == "stdin"
    assert cfg.sandbox.enabled is False
    assert cfg.sandbox.argv_prefix[0] == "bwrap"


def test_config_provider_defaults(tmp_path):
    from orchestra.config import load_config
    p = tmp_path / "config.yaml"
    p.write_text("slots: 2\nroles: {}\n")
    cfg = load_config(p)
    assert cfg.providers == {}
    assert cfg.sandbox.enabled is False
    assert cfg.sandbox.argv_prefix == []
    assert cfg.retries_cap == 2
    assert cfg.roles["worker"].prompt if cfg.roles else True  # roles untouched


def test_crash_retries_cap(tmp_path):
    from orchestra.config import load_config
    p = tmp_path / "config.yaml"
    p.write_text("slots: 1\nroles: {}\n")
    assert load_config(p).crash_retries_cap == 2  # default
    p.write_text("slots: 1\nroles: {}\ncrash_retries_cap: 5\n")
    assert load_config(p).crash_retries_cap == 5


def test_crash_transient_error_patterns_are_configurable(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("slots: 1\nroles: {}\ncrash_transient_error_patterns: ['provider overloaded']\n")
    assert load_config(p).crash_transient_error_patterns == ["provider overloaded"]


def test_validate_config_ok(tmp_path):
    from orchestra.config import load_config, validate_config
    p = tmp_path / "config.yaml"
    p.write_text(
        "slots: 2\n"
        "roles:\n"
        "  validator: { provider: claude, model: m, prompt: prompts/validator.md }\n"
        "  worker:    { provider: claude, model: m, prompt: prompts/worker.md }\n"
        "  verifier:  { provider: claude, model: m, prompt: prompts/verify-review.md }\n"
        "providers:\n"
        "  claude: { argv: [\"claude\"], prompt: stdin }\n"
    )
    validate_config(load_config(p))  # no raise


def test_validate_config_unknown_provider(tmp_path):
    import pytest
    from orchestra.config import load_config, validate_config
    p = tmp_path / "config.yaml"
    p.write_text(
        "slots: 2\n"
        "roles:\n"
        "  validator: { provider: claude, model: m, prompt: prompts/validator.md }\n"
        "  worker:    { provider: codex,  model: m, prompt: prompts/worker.md }\n"
        "  verifier:  { provider: claude, model: m, prompt: prompts/verify-review.md }\n"
        "providers:\n"
        "  claude: { argv: [\"claude\"], prompt: stdin }\n"
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
        "  validator: { provider: claude, model: m, prompt: prompts/validator.md }\n"
        "providers:\n"
        "  claude: { argv: [\"claude\"], prompt: stdin }\n"
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
    from orchestra.config import Config, ProviderConfig, RoleConfig, Sandbox, validate_config
    import pytest
    roles = {r: RoleConfig(provider="claude", model="m", prompt="p")
             for r in ("validator", "worker", "verifier")}
    providers = {"claude": ProviderConfig(argv=["claude"], prompt="stdin")}
    base = dict(slots=1, roles=roles, stall_idle_minutes=0, providers=providers,
                retries_cap=2, workflows={}, verify_rerun_checks=False, autoapprove=False,
                template_path="projects/project-template")
    bad = Config(validate_semantic=True, sandbox=Sandbox(enabled=False, argv_prefix=[]), **base)
    with pytest.raises(ValueError, match="sandbox"):
        validate_config(bad)
    ok = Config(validate_semantic=True, sandbox=Sandbox(enabled=True, argv_prefix=[]), **base)
    validate_config(ok)  # sandbox on → allowed
    off = Config(validate_semantic=False, sandbox=Sandbox(enabled=False, argv_prefix=[]), **base)
    validate_config(off)  # semantic off (default) → allowed
