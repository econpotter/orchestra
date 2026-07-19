from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

from orchestra.config import HarnessConfig


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
    home.relative_to(managed_root)  # structural guarantee: never escapes managed homes
    return home


def seed_session_home(source_home: Path, session_home: Path) -> None:
    """Copy the operator-authenticated source home into a private per-launch home.

    A fresh copy per launch is what makes concurrent launches safe against mutual auth
    invalidation: the harness's OAuth refresh only ever rewrites this launch's private copy.
    A missing or empty source seeds an empty (unauthenticated) home, so `preflight_authentication`
    still fails loud for a genuinely unauthenticated harness.
    """
    session_home.mkdir(parents=True, mode=0o700, exist_ok=True)
    if source_home.is_dir():
        for entry in source_home.iterdir():
            target = session_home / entry.name
            if entry.is_dir():
                shutil.copytree(entry, target, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, target)
    session_home.chmod(0o700)


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
