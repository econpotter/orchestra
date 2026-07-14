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


@dataclass
class AttemptLimits:
    wall_seconds: int = 0
    idle_seconds: int = 0
    active_tool_seconds: int = 0
    grace_seconds: int = 10


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
        )
        for name, rc in (data.get("roles") or {}).items()
    }
    harnesses = {}
    for name, hc in (data.get("harnesses") or {}).items():
        raw_limits = hc.get("limits") or {}
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


def validate_config(config: Config) -> None:
    for role in _REQUIRED_ROLES:
        if role not in config.roles:
            raise ValueError(f"config: required role {role!r} is missing from roles")
    for name, role_cfg in config.roles.items():
        if role_cfg.harness not in config.harnesses:
            raise ValueError(
                f"config: role {name!r} uses harness {role_cfg.harness!r} "
                f"which is not defined in harnesses"
            )
        harness = config.harnesses[role_cfg.harness]
        if harness.kind not in {"codex", "claude"}:
            raise ValueError(f"config: harness {role_cfg.harness!r} has unsupported kind {harness.kind!r}")
        from orchestra.harness import adapter_for
        capabilities = adapter_for(harness.kind).capabilities
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
