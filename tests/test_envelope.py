import json
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


def test_reseed_wipes_stale_files_from_an_aborted_prior_copy(tmp_path: Path):
    # #013: a prior seed that aborted mid-copy could leave a truncated credential behind; a
    # retry must not inherit it. Reseeding starts from an empty target, so stale files are gone.
    source = managed_auth_home(tmp_path, "claude", ".orchestra/homes/claude")
    source.mkdir(parents=True)
    (source / ".credentials.json").write_text('{"token": "good"}')
    session = session_state_home(tmp_path, "claude", "attempt-reseed")
    session.mkdir(parents=True)
    (session / ".credentials.json").write_text('{"token": "TRUNCA')  # aborted partial copy
    (session / "stale-marker").write_text("left over from a failed seed")

    seed_session_home(source, session)

    assert (session / ".credentials.json").read_text() == '{"token": "good"}'
    assert not (session / "stale-marker").exists()


def test_seed_preserves_symlinks_rather_than_dereferencing(tmp_path: Path):
    # #013: symlinks copy as links (symlinks=True), so a dangling link does not crash the seed
    # and an out-of-tree target is not pulled in wholesale.
    source = managed_auth_home(tmp_path, "claude", ".orchestra/homes/claude")
    source.mkdir(parents=True)
    (source / "dangling").symlink_to(tmp_path / "nowhere")  # target does not exist
    session = session_state_home(tmp_path, "claude", "attempt-symlink")

    seed_session_home(source, session)

    assert (session / "dangling").is_symlink()


def test_session_key_traversal_fails_with_a_clear_error(tmp_path: Path):
    # #013: a traversal-shaped session_key must be rejected explicitly, naming the offending
    # key — not with a bare relative_to "is not in the subpath" exception.
    with pytest.raises(ValueError, match="must not escape the managed homes tree"):
        session_state_home(tmp_path, "claude", "../../../../../../etc/passwd")


def test_unauthenticated_source_seeds_unauthenticated_launch_home(tmp_path: Path):
    # A genuinely unauthenticated harness (no credentials in the source) seeds an empty home,
    # so preflight_authentication still fails loud at dispatch (#010 criterion 3).
    session = session_state_home(tmp_path, "claude", "attempt-x")
    seed_session_home(tmp_path / ".orchestra" / "homes" / "claude", session)
    assert session.is_dir()
    assert not (session / ".credentials.json").exists()


def _credential_json(refresh_token: str = "operator-refresh-token") -> dict:
    # Shape confirmed by the Task 1 spike note
    # (docs/notes/2026-07-30-refresh-trigger-spike.md): keys only, no real values.
    return {
        "claudeAiOauth": {
            "accessToken": "operator-access-token",
            "refreshToken": refresh_token,
            "expiresAt": 1785449770979,
            "refreshTokenExpiresAt": 1787988703979,
            "scopes": ["user:profile", "user:inference"],
            "subscriptionType": "max",
            "rateLimitTier": "default_claude_max_20x",
        }
    }


def test_seed_strips_refresh_token_but_preserves_sibling_fields_and_files(tmp_path: Path):
    # #014-2: a worker refreshing from a seeded refresh token rotates and revokes the
    # fleet's shared token server-side. The per-launch copy must not carry one.
    source = managed_auth_home(tmp_path, "claude", ".orchestra/homes/claude")
    source.mkdir(parents=True)
    (source / ".credentials.json").write_text(json.dumps(_credential_json()))
    (source / "shell-snapshots").mkdir()
    (source / "shell-snapshots" / "snap.sh").write_text("#!/bin/sh\necho hi\n")
    session = session_state_home(tmp_path, "claude", "attempt-strip")

    seed_session_home(source, session)

    seeded = json.loads((session / ".credentials.json").read_text())
    oauth = seeded["claudeAiOauth"]
    assert "refreshToken" not in oauth
    assert oauth["accessToken"] == "operator-access-token"
    assert oauth["expiresAt"] == 1785449770979
    assert oauth["refreshTokenExpiresAt"] == 1787988703979
    assert oauth["scopes"] == ["user:profile", "user:inference"]
    assert oauth["subscriptionType"] == "max"
    assert oauth["rateLimitTier"] == "default_claude_max_20x"
    # A sibling file untouched by the strip is copied byte-identical.
    assert (session / "shell-snapshots" / "snap.sh").read_text() == "#!/bin/sh\necho hi\n"


def test_seed_strips_refresh_token_from_stale_nested_claude_credentials_copy(tmp_path: Path):
    # The spike found a stale duplicate at .claude/.credentials.json inside the shared
    # home; if a seed carries that duplicate forward it must be stripped too.
    source = managed_auth_home(tmp_path, "claude", ".orchestra/homes/claude")
    (source / ".claude").mkdir(parents=True)
    (source / ".credentials.json").write_text(json.dumps(_credential_json("top-level-refresh")))
    (source / ".claude" / ".credentials.json").write_text(
        json.dumps(_credential_json("nested-stale-refresh"))
    )
    session = session_state_home(tmp_path, "claude", "attempt-nested")

    seed_session_home(source, session)

    top = json.loads((session / ".credentials.json").read_text())
    nested = json.loads((session / ".claude" / ".credentials.json").read_text())
    assert "refreshToken" not in top["claudeAiOauth"]
    assert "refreshToken" not in nested["claudeAiOauth"]
    assert top["claudeAiOauth"]["accessToken"] == "operator-access-token"
    assert nested["claudeAiOauth"]["accessToken"] == "operator-access-token"


def test_seed_leaves_malformed_credential_json_untouched(tmp_path: Path):
    # Malformed JSON must not crash the launch; the worker will fail auth on its own
    # and that failure is visible, which is a better failure mode than a crashed seed.
    source = managed_auth_home(tmp_path, "claude", ".orchestra/homes/claude")
    source.mkdir(parents=True)
    (source / ".credentials.json").write_text("not valid json{")
    session = session_state_home(tmp_path, "claude", "attempt-malformed")

    seed_session_home(source, session)

    assert (session / ".credentials.json").read_text() == "not valid json{"


def test_seed_leaves_non_dict_top_level_json_untouched(tmp_path: Path):
    # Regression: valid JSON whose top level is not an object (list, string, number,
    # bool, null) must not crash the seed via AttributeError on `.get` — it is
    # "malformed" from the credential schema's point of view and must be left alone.
    source = managed_auth_home(tmp_path, "claude", ".orchestra/homes/claude")
    source.mkdir(parents=True)
    (source / ".credentials.json").write_text("[1, 2, 3]")
    session = session_state_home(tmp_path, "claude", "attempt-non-dict")

    seed_session_home(source, session)

    assert (session / ".credentials.json").read_text() == "[1, 2, 3]"


def test_seed_preserves_unknown_fields_at_top_level_and_inside_oauth(tmp_path: Path):
    # The brief requires preserving unknown keys, not just the documented schema
    # fields: one at the top level (sibling of claudeAiOauth) and one nested inside
    # claudeAiOauth itself must both survive the strip untouched.
    source = managed_auth_home(tmp_path, "claude", ".orchestra/homes/claude")
    source.mkdir(parents=True)
    credential = _credential_json()
    credential["claudeAiOauth"]["futureOauthField"] = "unknown-oauth-value"
    credential["futureTopLevelField"] = "unknown-top-level-value"
    (source / ".credentials.json").write_text(json.dumps(credential))
    session = session_state_home(tmp_path, "claude", "attempt-unknown-fields")

    seed_session_home(source, session)

    seeded = json.loads((session / ".credentials.json").read_text())
    assert "refreshToken" not in seeded["claudeAiOauth"]
    assert seeded["claudeAiOauth"]["futureOauthField"] == "unknown-oauth-value"
    assert seeded["futureTopLevelField"] == "unknown-top-level-value"


def test_seed_without_credential_file_still_seeds_other_files(tmp_path: Path):
    # A home without a credential file must seed exactly as before this change: no
    # crash, no file conjured up, other content copied normally.
    source = managed_auth_home(tmp_path, "claude", ".orchestra/homes/claude")
    source.mkdir(parents=True)
    (source / "settings.json").write_text('{"theme": "dark"}')
    session = session_state_home(tmp_path, "claude", "attempt-no-credential")

    seed_session_home(source, session)

    assert not (session / ".credentials.json").exists()
    assert (session / "settings.json").read_text() == '{"theme": "dark"}'


def test_isolated_state_directory_must_be_workspace_managed(tmp_path: Path):
    harness = HarnessConfig(
        kind="codex", executable="codex",
        environment=HarnessEnvironment(policy="isolated", state_dir=str(tmp_path.parent)),
    )
    with pytest.raises(ValueError, match="must be inside"):
        build_execution_envelope(
            tmp_path, "codex", harness, {}, home=tmp_path / "home"
        )
