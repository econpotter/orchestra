from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import yaml  # type: ignore[import-untyped]

# PROJECTS.md is the workspace identity marker. Some read-only commands do not need engine
# config, so resolution must not reject a valid registry-only workspace prematurely.
WORKSPACE_MARKERS = ("PROJECTS.md",)


class WorkspaceError(ValueError):
    pass


def default_settings_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    if configured := env.get("ORCHESTRA_SETTINGS"):
        return Path(configured).expanduser()
    config_home = Path(env.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return config_home / "orchestra" / "settings.yaml"


def _validate_workspace(path: Path, source: str) -> Path:
    resolved = path.expanduser().resolve()
    missing = [marker for marker in WORKSPACE_MARKERS if not (resolved / marker).is_file()]
    if missing:
        raise WorkspaceError(
            f"workspace from {source} is invalid: {resolved} missing {', '.join(missing)}"
        )
    return resolved


def _configured_workspace(settings_path: Path) -> Path | None:
    if not settings_path.exists():
        return None
    data = yaml.safe_load(settings_path.read_text()) or {}
    if not isinstance(data, dict):
        raise WorkspaceError(f"settings file must contain a mapping: {settings_path}")
    value = data.get("workspace")
    if value in (None, ""):
        return None
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else settings_path.parent / path


def resolve_workspace(
    explicit: str | Path | None,
    *,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    settings_path: Path | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    current = (cwd or Path.cwd()).resolve()
    if explicit is not None:
        path = Path(explicit)
        if not path.is_absolute():
            path = current / path
        return _validate_workspace(path, "--root")
    if value := env.get("ORCHESTRA_ROOT"):
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = current / path
        return _validate_workspace(path, "ORCHESTRA_ROOT")

    settings = settings_path or default_settings_path(env)
    if configured := _configured_workspace(settings):
        return _validate_workspace(configured, f"settings {settings}")

    for candidate in (current, *current.parents):
        if all((candidate / marker).is_file() for marker in WORKSPACE_MARKERS):
            return candidate
    raise WorkspaceError(
        "orchestra workspace not found; pass --root, set ORCHESTRA_ROOT, or run "
        "'orchestra workspace set PATH'"
    )


def save_workspace_setting(
    workspace: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
    settings_path: Path | None = None,
) -> Path:
    settings = settings_path or default_settings_path(environ)
    resolved = _validate_workspace(Path(workspace), "workspace set")
    settings.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings.with_suffix(settings.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump({"workspace": str(resolved)}, sort_keys=False))
    os.replace(tmp, settings)
    return resolved
