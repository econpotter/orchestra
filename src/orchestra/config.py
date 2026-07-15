from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml  # type: ignore[import-untyped]


@dataclass
class RoleConfig:
    harness: str
    model: str
    prompt: str
    required_capabilities: tuple[str, ...] = ()
    instruction_policy: str = "native_project"
    delegation: str = "disabled"


@dataclass
class AttemptLimits:
    wall_seconds: int = 0
    idle_seconds: int = 0
    active_tool_seconds: int = 0
    grace_seconds: int = 10


@dataclass
class HarnessEnvironment:
    policy: str = "ambient"
    state_dir: str | None = None
    instructions_file: str | None = None
    verified_capabilities: tuple[str, ...] = ()


@dataclass
class HarnessConfig:
    kind: str
    executable: str
    reasoning_effort: str = "high"
    sandbox: str = "workspace-write"
    extra_args: list[str] = field(default_factory=list)
    attempts_cap: int = 3
    limits: AttemptLimits = field(default_factory=AttemptLimits)
    preflight: bool = True
    environment: HarnessEnvironment = field(default_factory=HarnessEnvironment)


@dataclass
class Sandbox:
    enabled: bool
    kind: str = "systemd"
    executable: str = "systemd-run"


@dataclass
class Config:
    slots: int
    roles: dict[str, RoleConfig]
    validate_semantic: bool
    harnesses: dict[str, HarnessConfig]
    sandbox: Sandbox
    retries_cap: int
    workflows: dict[str, dict[str, str]]
    verify_rerun_checks: bool
    autoapprove: bool
    template_path: str
    merge_tmpdir: str = ""
    hold_network_issues: bool = False


def _optional_bool(data: dict[str, object], key: str, *, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"config: {key} must be a boolean")
    return value


def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text()) or {}
    roles = {
        name: RoleConfig(
            harness=rc["harness"], model=rc["model"], prompt=rc["prompt"],
            required_capabilities=tuple(rc.get("required_capabilities", ())),
            instruction_policy=str(rc.get("instruction_policy", "native_project")),
            delegation=str(rc.get("delegation", "disabled")),
        )
        for name, rc in (data.get("roles") or {}).items()
    }
    harnesses = {}
    for name, hc in (data.get("harnesses") or {}).items():
        raw_limits = hc.get("limits") or {}
        raw_environment = hc.get("environment") or {}
        state_dir = raw_environment.get("state_dir")
        instructions_file = raw_environment.get("instructions_file")
        verified_capabilities = raw_environment.get("verified_capabilities") or ()
        harnesses[name] = HarnessConfig(
            kind=str(hc["kind"]), executable=str(hc["executable"]),
            reasoning_effort=str(hc.get("reasoning_effort", "high")),
            sandbox=str(hc.get("sandbox", "workspace-write")),
            extra_args=[str(arg) for arg in hc.get("extra_args", ())],
            attempts_cap=int(hc.get("attempts_cap", 3)),
            limits=AttemptLimits(**{
                key: int(raw_limits.get(key, default)) for key, default in {
                    "wall_seconds": 0, "idle_seconds": 0,
                    "active_tool_seconds": 0, "grace_seconds": 10,
                }.items()
            }),
            preflight=_optional_bool(hc, "preflight", default=True),
            environment=HarnessEnvironment(
                policy=str(raw_environment.get("policy", "ambient")),
                state_dir=None if state_dir is None else str(state_dir),
                instructions_file=(
                    None if instructions_file is None else str(instructions_file)
                ),
                verified_capabilities=tuple(str(item) for item in verified_capabilities),
            ),
        )
    sb = data.get("sandbox") or {}
    sandbox = Sandbox(
        enabled=bool(sb.get("enabled", False)),
        kind=str(sb.get("kind", "systemd")),
        executable=str(sb.get("executable", "systemd-run")),
    )
    workflows = {
        name: {str(k): str(v) for k, v in (block or {}).items()}
        for name, block in (data.get("workflows") or {}).items()
    }
    verify_rerun_checks = bool((data.get("verify") or {}).get("rerun_checks", False))
    autoapprove = bool((data.get("review") or {}).get("autoapprove", False))
    merge_tmpdir = str((data.get("merge") or {}).get("tmpdir", ""))
    template_path = str(data.get("template_path", "projects/project-template"))
    return Config(
        slots=int(data.get("slots", 0)),
        roles=roles,
        validate_semantic=bool((data.get("validate") or {}).get("semantic", False)),
        harnesses=harnesses,
        sandbox=sandbox,
        retries_cap=int(data.get("retries_cap", 2)),
        workflows=workflows,
        verify_rerun_checks=verify_rerun_checks,
        autoapprove=autoapprove,
        template_path=template_path,
        merge_tmpdir=merge_tmpdir,
        hold_network_issues=_optional_bool(data, "hold_network_issues", default=False),
    )


_REQUIRED_ROLES = ("validator", "worker", "verifier")
_INSTRUCTION_POLICIES = {"native_project", "explicit_bundle"}
_DELEGATION_POLICIES = {"disabled", "allowed", "required"}
_ENVIRONMENT_POLICIES = {"ambient", "isolated"}


def validate_config(config: Config) -> None:
    for role in _REQUIRED_ROLES:
        if role not in config.roles:
            raise ValueError(f"config: required role {role!r} is missing from roles")
    for harness_name, harness in config.harnesses.items():
        if harness.environment.policy not in _ENVIRONMENT_POLICIES:
            raise ValueError(
                f"config: harness {harness_name!r} environment policy must be one of "
                f"{', '.join(sorted(_ENVIRONMENT_POLICIES))}"
            )
        if any("multi_agent" in arg for arg in harness.extra_args):
            raise ValueError(
                f"config: role delegation owns multi_agent; remove it from harness "
                f"{harness_name!r} extra_args"
            )
        if harness.environment.policy == "isolated" and not config.sandbox.enabled:
            raise ValueError(
                f"config: harness {harness_name!r} isolation requires sandbox.enabled"
            )
        if harness.environment.instructions_file and (
            harness.kind != "codex" or harness.environment.policy != "isolated"
        ):
            raise ValueError(
                f"config: harness {harness_name!r} instructions_file requires isolated Codex"
            )
        from orchestra.envelope import ISOLATION_CAPABILITIES
        unknown_verified = sorted(
            set(harness.environment.verified_capabilities) - set(ISOLATION_CAPABILITIES)
        )
        if unknown_verified:
            raise ValueError(
                f"config: harness {harness_name!r} has unknown verified isolation "
                f"capabilities: {', '.join(unknown_verified)}"
            )
        if harness.environment.verified_capabilities \
                and harness.environment.policy != "isolated":
            raise ValueError(
                f"config: harness {harness_name!r} verified capabilities require isolation"
            )
    for name, role_cfg in config.roles.items():
        if role_cfg.instruction_policy not in _INSTRUCTION_POLICIES:
            raise ValueError(
                f"config: role {name!r} instruction_policy must be one of "
                f"{', '.join(sorted(_INSTRUCTION_POLICIES))}"
            )
        if role_cfg.delegation not in _DELEGATION_POLICIES:
            raise ValueError(
                f"config: role {name!r} delegation must be one of "
                f"{', '.join(sorted(_DELEGATION_POLICIES))}"
            )
        if role_cfg.harness not in config.harnesses:
            raise ValueError(
                f"config: role {name!r} uses harness {role_cfg.harness!r} "
                f"which is not defined in harnesses"
            )
        harness = config.harnesses[role_cfg.harness]
        if harness.kind not in {"codex", "claude"}:
            raise ValueError(f"config: harness {role_cfg.harness!r} has unsupported kind {harness.kind!r}")
        from orchestra.harness import adapter_for
        adapter = adapter_for(harness.kind)
        from orchestra.envelope import build_execution_envelope
        capabilities = build_execution_envelope(
            Path("."), role_cfg.harness, harness, adapter.capabilities,
            home=Path.home(), instruction_policy=role_cfg.instruction_policy,
        ).effective_capabilities
        if harness.kind == "codex" and role_cfg.instruction_policy != "native_project":
            raise ValueError(
                f"config: Codex role {name!r} requires instruction_policy: native_project"
            )
        if harness.kind == "claude" and role_cfg.delegation == "required":
            raise ValueError(
                f"config: Claude role {name!r} does not support required delegation"
            )
        missing = [cap for cap in role_cfg.required_capabilities if not capabilities.get(cap)]
        if missing:
            raise ValueError(f"config: role {name!r} requires unsupported capabilities: {', '.join(missing)}")
        if harness.attempts_cap < 1:
            raise ValueError(f"config: harness {role_cfg.harness!r} attempts_cap must be positive")
        if harness.sandbox == "danger-full-access" and not config.sandbox.enabled:
            raise ValueError(
                f"config: harness {role_cfg.harness!r} bypasses its inner sandbox; "
                "enable the verified outer sandbox"
            )
    if config.sandbox.enabled and config.sandbox.kind != "systemd":
        raise ValueError("config: sandbox.kind must be 'systemd'")
    # The semantic validator agent runs at the repo root with skip-permissions; without the
    # sandbox it is unconfined and could write queue/ (violating the single-writer invariant).
    # Refuse the unsafe combination rather than launch it. (Default semantic is false → no
    # validator agent runs, so this never trips on the default config.)
    if config.validate_semantic and not config.sandbox.enabled:
        raise ValueError(
            "config: validate.semantic: true runs the validator agent unconfined at the repo "
            "root — enable sandbox.enabled before turning on semantic validation"
        )
