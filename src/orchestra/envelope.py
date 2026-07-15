from __future__ import annotations

import hashlib
import json
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


def _managed_state_dir(root: Path, harness_name: str, configured: str | None) -> Path:
    managed_root = (root / ".orchestra" / "homes").resolve()
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


def build_execution_envelope(
    root: str | Path,
    harness_name: str,
    harness: HarnessConfig,
    supported_capabilities: dict[str, bool],
    *,
    home: str | Path,
    instruction_policy: str = "native_project",
) -> ExecutionEnvelope:
    """Resolve the effective, auditable process boundary for one harness launch."""
    capabilities = dict(supported_capabilities)
    capabilities.update({name: False for name in ISOLATION_CAPABILITIES})
    if harness.environment.policy == "ambient":
        return ExecutionEnvelope((), (), (), capabilities)
    if harness.environment.policy != "isolated":
        raise ValueError(f"unsupported environment policy: {harness.environment.policy}")

    root = Path(root).resolve()
    home = Path(home).resolve()
    if harness.kind == "codex":
        state_dir = _managed_state_dir(root, harness_name, harness.environment.state_dir)
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
        state_dir = _managed_state_dir(root, harness_name, harness.environment.state_dir)
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
