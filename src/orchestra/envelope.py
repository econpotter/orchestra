from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

from orchestra.config import HarnessConfig


_CREDENTIAL_PATHS = (Path(".credentials.json"), Path(".claude") / ".credentials.json")


ISOLATION_CAPABILITIES = (
    "isolates_user_config",
    "isolates_user_instructions",
    "isolates_user_skills",
    "isolates_user_integrations",
    "isolates_session_state",
    "supports_dedicated_auth_home",
)


@dataclass(frozen=True)
class ExecutionEnvelope:
    environment: tuple[tuple[str, str], ...]
    read_write_paths: tuple[str, ...]
    inaccessible_paths: tuple[str, ...]
    effective_capabilities: dict[str, bool]


def execution_envelope_fingerprint(envelope: ExecutionEnvelope) -> str:
    encoded = json.dumps(asdict(envelope), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _managed_root(root: Path) -> Path:
    return (root / ".orchestra" / "homes").resolve()


def managed_auth_home(root: Path, harness_name: str, configured: str | None) -> Path:
    """The operator-authenticated source home for a harness, keyed by harness name.

    This is where `orchestra harness setup` logs in and `doctor` reports authentication. It
    is shared across launches only as a *read* seed: each launch copies it into a private
    per-launch home (see `session_state_home`) so a concurrent OAuth token refresh in one
    launch never rewrites — and thereby invalidates — a sibling launch's credentials.
    """
    managed_root = _managed_root(root)
    state_dir = Path(configured).expanduser() if configured else managed_root / harness_name
    if not state_dir.is_absolute():
        state_dir = root / state_dir
    state_dir = state_dir.resolve()
    try:
        state_dir.relative_to(managed_root)
    except ValueError:
        raise ValueError(
            f"isolated harness state_dir must be inside {managed_root}: {state_dir}"
        ) from None
    if state_dir == managed_root:
        raise ValueError("isolated harness state_dir must name a directory below homes")
    return state_dir


def session_state_home(root: Path, harness_name: str, session_key: str) -> Path:
    """A private per-launch harness home under the managed homes tree.

    Keyed by (harness, session_key) so two concurrent launches of the same harness get
    distinct writable homes: the harness CLI's own token refresh (which rewrites and rotates
    the OAuth token file) can only touch this launch's copy, never a sibling's. Kept under a
    `.sessions/` sibling of the source homes so seeding a copy never recurses into itself.
    """
    managed_root = _managed_root(root)
    home = (managed_root / ".sessions" / harness_name / session_key).resolve()
    # Validate escape explicitly: a traversal-shaped session_key (e.g. "../../etc") must fail
    # with a clear, actionable message, not a bare `relative_to` "is not in the subpath"
    # exception whose text names resolved absolute paths rather than the offending key.
    try:
        home.relative_to(managed_root)
    except ValueError:
        raise ValueError(
            "session_key must not escape the managed homes tree "
            f"({managed_root}): {session_key!r}"
        ) from None
    return home


def _strip_refresh_token(credential_file: Path) -> None:
    """Remove the OAuth refresh token from a seeded credential file, in place.

    A per-launch home must never carry a refresh token: the harness's native OAuth
    refresh rotates and revokes the prior token server-side, so a worker refreshing
    from its private copy would kill the token still sitting in the shared home
    (see `docs/plans/2026-07-30-worker-auth-central-refresh.md`). With no refresh
    token in the copy, a worker physically cannot rotate or revoke anything; its
    worst case is a clean 401 at true access-token expiry. Every other field,
    including unknown ones, is preserved. A missing file, a file with no
    `claudeAiOauth.refreshToken`, valid-but-non-dict JSON (e.g. a bare list or
    scalar), or malformed JSON are all left untouched: the credential schema is
    the harness's own external data, and a worker seeded from a genuinely corrupt
    credential will simply fail auth on its own, which is a more visible failure
    than crashing the launch here.
    """
    if not credential_file.is_file():
        return
    try:
        data = json.loads(credential_file.read_text())
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict) or "refreshToken" not in oauth:
        return
    del oauth["refreshToken"]
    credential_file.write_text(json.dumps(data))


def seed_session_home(source_home: Path, session_home: Path) -> None:
    """Copy the operator-authenticated source home into a private per-launch home.

    A fresh copy per launch is what makes concurrent launches safe against mutual auth
    invalidation: the harness's OAuth refresh only ever rewrites this launch's private copy.
    A missing or empty source seeds an empty (unauthenticated) home, so `preflight_authentication`
    still fails loud for a genuinely unauthenticated harness.

    The target is wiped before reseeding: a prior seed that aborted mid-copy (crash, disk
    quota) could otherwise leave a truncated credential file behind, which a retry would then
    inherit and read as a valid-but-corrupt authenticated home. Starting from an empty target
    guarantees the copy is all-or-nothing from the caller's perspective. Directories copy with
    `symlinks=True` (and files with `follow_symlinks=False`) so a symlink in the source is
    reproduced as a link rather than dereferenced — dereferencing crashes on a dangling link
    and can pull in large or out-of-tree content, while the credential files themselves are
    ordinary files, so per-launch auth isolation is unaffected.

    After copying, the refresh token is stripped from the seeded credential file(s) (see
    `_strip_refresh_token`): a worker never carries a refresh token, so it cannot rotate or
    revoke the shared home's live token.
    """
    shutil.rmtree(session_home, ignore_errors=True)
    session_home.mkdir(parents=True, mode=0o700, exist_ok=True)
    if source_home.is_dir():
        for entry in source_home.iterdir():
            target = session_home / entry.name
            if entry.is_dir():
                shutil.copytree(entry, target, symlinks=True, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, target, follow_symlinks=False)
    session_home.chmod(0o700)
    for relative in _CREDENTIAL_PATHS:
        _strip_refresh_token(session_home / relative)


def reseed_credentials(source_home: Path, session_home: Path) -> None:
    """Replace a seeded home's credential file(s) with the shared home's current ones.

    Only used when a launch is seeded from a parent launch's home (a resume, which must
    inherit the parent's session transcript). That parent copy carries no refresh token, so
    its access token can only *age* — a resumed long run would otherwise start on a token
    older than the one the engine has been keeping alive centrally. Copying the shared
    home's credential over it keeps the transcript and takes the fresh token; the refresh
    token is stripped again, exactly as an ordinary seed.

    A source without a credential file leaves the seeded one alone: the seeded copy failing
    authentication preflight is a clearer failure than an empty home.
    """
    for relative in _CREDENTIAL_PATHS:
        source = source_home / relative
        if not source.is_file():
            continue
        target = session_home / relative
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
        _strip_refresh_token(target)


def _launch_home(
    root: Path, harness_name: str, configured: str | None, session_key: str | None
) -> Path:
    # Always resolve (and validate) the configured source home; a per-launch session_key then
    # redirects the live home to a private copy so concurrent refreshes cannot collide.
    source = managed_auth_home(root, harness_name, configured)
    if session_key is None:
        return source
    return session_state_home(root, harness_name, session_key)


def build_execution_envelope(
    root: str | Path,
    harness_name: str,
    harness: HarnessConfig,
    supported_capabilities: dict[str, bool],
    *,
    home: str | Path,
    instruction_policy: str = "native_project",
    session_key: str | None = None,
) -> ExecutionEnvelope:
    """Resolve the effective, auditable process boundary for one harness launch.

    When `session_key` is given, an isolated harness's live state home is a private per-launch
    copy under `.orchestra/homes/.sessions/<harness>/<session_key>` instead of the shared
    source home, so concurrent launches cannot invalidate each other's auth on token refresh.
    """
    capabilities = dict(supported_capabilities)
    capabilities.update({name: False for name in ISOLATION_CAPABILITIES})
    if harness.environment.policy == "ambient":
        return ExecutionEnvelope((), (), (), capabilities)
    if harness.environment.policy != "isolated":
        raise ValueError(f"unsupported environment policy: {harness.environment.policy}")

    root = Path(root).resolve()
    home = Path(home).resolve()
    if harness.kind == "codex":
        state_dir = _launch_home(
            root, harness_name, harness.environment.state_dir, session_key
        )
        capabilities.update({
            name: name in harness.environment.verified_capabilities
            for name in ISOLATION_CAPABILITIES
        })
        return ExecutionEnvelope(
            environment=(("CODEX_HOME", str(state_dir)),),
            read_write_paths=(str(state_dir),),
            inaccessible_paths=(f"-{home / '.agents'}",),
            effective_capabilities=capabilities,
        )
    if harness.kind == "claude":
        if instruction_policy != "explicit_bundle":
            raise ValueError("isolated Claude requires instruction_policy: explicit_bundle")
        state_dir = _launch_home(
            root, harness_name, harness.environment.state_dir, session_key
        )
        capabilities.update({
            name: name in harness.environment.verified_capabilities
            for name in ISOLATION_CAPABILITIES
        })
        return ExecutionEnvelope(
            environment=(("CLAUDE_CONFIG_DIR", str(state_dir)),),
            read_write_paths=(str(state_dir),),
            inaccessible_paths=(f"-{home / '.claude'}",),
            effective_capabilities=capabilities,
        )
    raise ValueError(f"unsupported isolated harness kind: {harness.kind}")
