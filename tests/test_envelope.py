from pathlib import Path

import pytest

from orchestra.config import HarnessConfig, HarnessEnvironment
from orchestra.envelope import (
    build_execution_envelope,
    execution_envelope_fingerprint,
    managed_auth_home,
    seed_session_home,
    session_state_home,
)


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


def _isolated_claude(state_dir: str = ".orchestra/homes/claude") -> HarnessConfig:
    return HarnessConfig(
        kind="claude", executable="claude",
        environment=HarnessEnvironment(
            policy="isolated", state_dir=state_dir,
            verified_capabilities=("supports_dedicated_auth_home",),
        ),
    )


def test_session_key_routes_live_home_to_private_per_launch_copy(tmp_path: Path):
    harness = _isolated_claude()
    shared = build_execution_envelope(
        tmp_path, "claude", harness, {}, home=tmp_path / "home",
        instruction_policy="explicit_bundle",
    )
    launch = build_execution_envelope(
        tmp_path, "claude", harness, {}, home=tmp_path / "home",
        instruction_policy="explicit_bundle", session_key="attempt-abc",
    )
    source = tmp_path / ".orchestra" / "homes" / "claude"
    session = tmp_path / ".orchestra" / "homes" / ".sessions" / "claude" / "attempt-abc"
    # Without a session_key the setup/doctor path still resolves the shared source home.
    assert dict(shared.environment)["CLAUDE_CONFIG_DIR"] == str(source)
    # A launch's live home is a private copy that never aliases the shared source.
    assert dict(launch.environment)["CLAUDE_CONFIG_DIR"] == str(session)
    assert launch.read_write_paths == (str(session),)


def test_concurrent_launches_do_not_clobber_each_others_auth(tmp_path: Path):
    """Two concurrent launches of one harness must each refresh auth in isolation (#010)."""
    harness = _isolated_claude()
    source = managed_auth_home(tmp_path, "claude", harness.environment.state_dir)
    source.mkdir(parents=True)
    (source / ".credentials.json").write_text('{"token": "operator-seed"}')

    homes = []
    for session_key in ("worker-1", "verifier-2"):
        envelope = build_execution_envelope(
            tmp_path, "claude", harness, {}, home=tmp_path / "home",
            instruction_policy="explicit_bundle", session_key=session_key,
        )
        home = Path(dict(envelope.environment)["CLAUDE_CONFIG_DIR"])
        seed_session_home(source, home)
        homes.append(home)

    first, second = homes
    assert first != second
    # Both launches start from the same authenticated seed...
    assert (first / ".credentials.json").read_text() == '{"token": "operator-seed"}'
    assert (second / ".credentials.json").read_text() == '{"token": "operator-seed"}'
    # ...but a token refresh in one launch must not touch the other's credentials.
    (first / ".credentials.json").write_text('{"token": "refreshed-in-worker"}')
    assert (second / ".credentials.json").read_text() == '{"token": "operator-seed"}'
    # ...and neither rewrites the shared operator source.
    assert (source / ".credentials.json").read_text() == '{"token": "operator-seed"}'


def test_unauthenticated_source_seeds_unauthenticated_launch_home(tmp_path: Path):
    # A genuinely unauthenticated harness (no credentials in the source) seeds an empty home,
    # so preflight_authentication still fails loud at dispatch (#010 criterion 3).
    session = session_state_home(tmp_path, "claude", "attempt-x")
    seed_session_home(tmp_path / ".orchestra" / "homes" / "claude", session)
    assert session.is_dir()
    assert not (session / ".credentials.json").exists()


def test_isolated_state_directory_must_be_workspace_managed(tmp_path: Path):
    harness = HarnessConfig(
        kind="codex", executable="codex",
        environment=HarnessEnvironment(policy="isolated", state_dir=str(tmp_path.parent)),
    )
    with pytest.raises(ValueError, match="must be inside"):
        build_execution_envelope(
            tmp_path, "codex", harness, {}, home=tmp_path / "home"
        )
