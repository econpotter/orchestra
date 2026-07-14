from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml  # type: ignore[import-untyped]


DEFAULT_CRASH_TRANSIENT_ERROR_PATTERNS = (
    r"(?i)(?:api )?(?:session(?:/usage)?|usage)[ -]?limit(?: reached| exceeded)?",
)


@dataclass
class RoleConfig:
    provider: str
    model: str
    prompt: str


@dataclass
class ProviderConfig:
    argv: list[str]
    prompt: str = "stdin"


@dataclass
class Sandbox:
    # Filesystem confinement for launched agents. When `enabled`, `argv_prefix` (a bwrap
    # invocation, see config.yaml) ro-binds the rootfs and grants a writable
    # workdir/tmp/results_dir, so a confined agent cannot write files outside its worktree.
    # Network is shared — the agent needs its model API — so this does not enforce the
    # `Network` flag at run time. The flag is advisory by default and can be made a dispatch
    # gate with Config.hold_network_issues; real egress isolation is out of scope.
    enabled: bool
    argv_prefix: list[str]


@dataclass
class Config:
    slots: int
    roles: dict[str, RoleConfig]
    validate_semantic: bool
    stall_idle_minutes: int
    providers: dict[str, ProviderConfig]
    sandbox: Sandbox
    retries_cap: int
    workflows: dict[str, dict[str, str]]
    verify_rerun_checks: bool
    autoapprove: bool
    template_path: str
    merge_tmpdir: str = ""
    crash_retries_cap: int = 2
    hold_network_issues: bool = False
    crash_transient_error_patterns: list[str] = field(
        default_factory=lambda: list(DEFAULT_CRASH_TRANSIENT_ERROR_PATTERNS)
    )


def _optional_bool(data: dict[str, object], key: str, *, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"config: {key} must be a boolean")
    return value


def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text()) or {}
    roles = {
        name: RoleConfig(
            provider=rc["provider"], model=rc["model"], prompt=rc["prompt"]
        )
        for name, rc in (data.get("roles") or {}).items()
    }
    providers = {
        name: ProviderConfig(argv=list(pc["argv"]), prompt=pc.get("prompt", "stdin"))
        for name, pc in (data.get("providers") or {}).items()
    }
    sb = data.get("sandbox") or {}
    sandbox = Sandbox(
        enabled=bool(sb.get("enabled", False)),
        argv_prefix=list(sb.get("argv_prefix", [])),
    )
    workflows = {
        name: {str(k): str(v) for k, v in (block or {}).items()}
        for name, block in (data.get("workflows") or {}).items()
    }
    verify_rerun_checks = bool((data.get("verify") or {}).get("rerun_checks", False))
    autoapprove = bool((data.get("review") or {}).get("autoapprove", False))
    merge_tmpdir = str((data.get("merge") or {}).get("tmpdir", ""))
    template_path = str(data.get("template_path", "projects/project-template"))
    transient_patterns = data.get(
        "crash_transient_error_patterns", DEFAULT_CRASH_TRANSIENT_ERROR_PATTERNS
    )
    if not isinstance(transient_patterns, (list, tuple)):
        raise ValueError("config: crash_transient_error_patterns must be a list")
    return Config(
        slots=int(data.get("slots", 0)),
        roles=roles,
        validate_semantic=bool((data.get("validate") or {}).get("semantic", False)),
        stall_idle_minutes=int((data.get("stall") or {}).get("idle_minutes", 0)),
        providers=providers,
        sandbox=sandbox,
        retries_cap=int(data.get("retries_cap", 2)),
        workflows=workflows,
        verify_rerun_checks=verify_rerun_checks,
        autoapprove=autoapprove,
        template_path=template_path,
        merge_tmpdir=merge_tmpdir,
        crash_retries_cap=int(data.get("crash_retries_cap", 2)),
        hold_network_issues=_optional_bool(data, "hold_network_issues", default=False),
        crash_transient_error_patterns=[str(pattern) for pattern in transient_patterns],
    )


_REQUIRED_ROLES = ("validator", "worker", "verifier")


def validate_config(config: Config) -> None:
    for role in _REQUIRED_ROLES:
        if role not in config.roles:
            raise ValueError(f"config: required role {role!r} is missing from roles")
    for name, role_cfg in config.roles.items():
        if role_cfg.provider not in config.providers:
            raise ValueError(
                f"config: role {name!r} uses provider {role_cfg.provider!r} "
                f"which is not defined in providers"
            )
    if config.crash_retries_cap < 0:
        raise ValueError("config: crash_retries_cap must be non-negative")
    for pattern in config.crash_transient_error_patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(
                f"config: invalid crash transient-error pattern {pattern!r}: {exc}"
            ) from None
    # The semantic validator agent runs at the repo root with skip-permissions; without the
    # sandbox it is unconfined and could write queue/ (violating the single-writer invariant).
    # Refuse the unsafe combination rather than launch it. (Default semantic is false → no
    # validator agent runs, so this never trips on the default config.)
    if config.validate_semantic and not config.sandbox.enabled:
        raise ValueError(
            "config: validate.semantic: true runs the validator agent unconfined at the repo "
            "root — enable sandbox.enabled before turning on semantic validation"
        )
