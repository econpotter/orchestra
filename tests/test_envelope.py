from pathlib import Path

import pytest

from orchestra.config import HarnessConfig, HarnessEnvironment
from orchestra.envelope import build_execution_envelope, execution_envelope_fingerprint


def test_ambient_envelope_does_not_claim_isolation(tmp_path: Path):
    harness = HarnessConfig(kind="codex", executable="codex")
    envelope = build_execution_envelope(
        tmp_path, "codex", harness, {"structured_events": True},
        home=tmp_path / "home",
    )
    assert envelope.environment == ()
    assert envelope.read_write_paths == ()
    assert envelope.inaccessible_paths == ()
    assert envelope.effective_capabilities["isolates_user_skills"] is False
    assert execution_envelope_fingerprint(envelope) == execution_envelope_fingerprint(envelope)


def test_isolated_codex_envelope_preserves_home_and_masks_personal_skills(tmp_path: Path):
    home = tmp_path / "home"
    harness = HarnessConfig(
        kind="codex", executable="codex",
        environment=HarnessEnvironment(
            policy="isolated", state_dir=".orchestra/homes/codex",
            verified_capabilities=(
                "isolates_user_config", "isolates_user_instructions",
                "isolates_user_skills", "isolates_user_integrations",
                "isolates_session_state", "supports_dedicated_auth_home",
            ),
        ),
    )
    envelope = build_execution_envelope(
        tmp_path, "codex", harness, {"structured_events": True}, home=home,
    )
    state_dir = tmp_path / ".orchestra" / "homes" / "codex"
    assert dict(envelope.environment) == {"CODEX_HOME": str(state_dir)}
    assert "HOME" not in dict(envelope.environment)
    assert envelope.read_write_paths == (str(state_dir),)
    assert envelope.inaccessible_paths == (f"-{home / '.agents'}",)
    assert envelope.effective_capabilities["isolates_user_config"] is True
    assert envelope.effective_capabilities["isolates_user_instructions"] is True
    assert envelope.effective_capabilities["isolates_user_skills"] is True
    assert envelope.effective_capabilities["isolates_user_integrations"] is True
    assert envelope.effective_capabilities["isolates_session_state"] is True
    assert envelope.effective_capabilities["supports_dedicated_auth_home"] is True


def test_isolated_claude_requires_explicit_bundle(tmp_path: Path):
    harness = HarnessConfig(
        kind="claude", executable="claude",
        environment=HarnessEnvironment(policy="isolated"),
    )
    try:
        build_execution_envelope(
            tmp_path, "claude", harness, {}, home=tmp_path / "home",
            instruction_policy="native_project",
        )
    except ValueError as exc:
        assert "explicit_bundle" in str(exc)
    else:
        raise AssertionError("isolated Claude must reject native instruction discovery")


def test_isolated_claude_uses_dedicated_config_and_masks_personal_state(tmp_path: Path):
    home = tmp_path / "home"
    harness = HarnessConfig(
        kind="claude", executable="claude",
        environment=HarnessEnvironment(
            policy="isolated", state_dir=".orchestra/homes/claude",
            verified_capabilities=(
                "isolates_user_config", "isolates_user_instructions",
                "isolates_user_skills", "isolates_user_integrations",
                "isolates_session_state", "supports_dedicated_auth_home",
            ),
        ),
    )
    envelope = build_execution_envelope(
        tmp_path, "claude", harness, {}, home=home,
        instruction_policy="explicit_bundle",
    )
    state_dir = tmp_path / ".orchestra" / "homes" / "claude"
    assert dict(envelope.environment) == {"CLAUDE_CONFIG_DIR": str(state_dir)}
    assert envelope.read_write_paths == (str(state_dir),)
    assert envelope.inaccessible_paths == (f"-{home / '.claude'}",)
    assert all(envelope.effective_capabilities[name] for name in (
        "isolates_user_config", "isolates_user_instructions", "isolates_user_skills",
        "isolates_user_integrations", "isolates_session_state",
        "supports_dedicated_auth_home",
    ))


def test_isolated_state_directory_must_be_workspace_managed(tmp_path: Path):
    harness = HarnessConfig(
        kind="codex", executable="codex",
        environment=HarnessEnvironment(policy="isolated", state_dir=str(tmp_path.parent)),
    )
    with pytest.raises(ValueError, match="must be inside"):
        build_execution_envelope(
            tmp_path, "codex", harness, {}, home=tmp_path / "home"
        )
