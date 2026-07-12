from pathlib import Path

import pytest

from orchestra.workspace import WorkspaceError, resolve_workspace, save_workspace_setting


def _workspace(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "config.yaml").write_text("slots: 1\n")
    (path / "PROJECTS.md").write_text("# Projects\n")
    return path


def test_explicit_workspace_wins(tmp_path: Path):
    explicit = _workspace(tmp_path / "explicit")
    env_root = _workspace(tmp_path / "env")
    assert resolve_workspace(
        explicit, environ={"ORCHESTRA_ROOT": str(env_root)}, cwd=tmp_path,
        settings_path=tmp_path / "missing.yaml",
    ) == explicit.resolve()


def test_environment_workspace_wins_over_setting(tmp_path: Path):
    env_root = _workspace(tmp_path / "env")
    configured = _workspace(tmp_path / "configured")
    settings = tmp_path / "settings.yaml"
    settings.write_text(f"workspace: {configured}\n")
    assert resolve_workspace(
        None, environ={"ORCHESTRA_ROOT": str(env_root)}, cwd=tmp_path,
        settings_path=settings,
    ) == env_root.resolve()


def test_configured_workspace_is_default(tmp_path: Path):
    configured = _workspace(tmp_path / "configured")
    settings = tmp_path / "settings.yaml"
    save_workspace_setting(configured, settings_path=settings)
    assert resolve_workspace(
        None, environ={}, cwd=tmp_path / "elsewhere", settings_path=settings,
    ) == configured.resolve()


def test_discovers_workspace_from_nested_directory(tmp_path: Path):
    root = _workspace(tmp_path / "workspace")
    nested = root / "projects" / "demo"
    nested.mkdir(parents=True)
    assert resolve_workspace(
        None, environ={}, cwd=nested, settings_path=tmp_path / "missing.yaml",
    ) == root.resolve()


def test_invalid_configured_workspace_fails_loud(tmp_path: Path):
    settings = tmp_path / "settings.yaml"
    settings.write_text(f"workspace: {tmp_path / 'missing'}\n")
    with pytest.raises(WorkspaceError, match="settings.*missing PROJECTS.md"):
        resolve_workspace(None, environ={}, cwd=tmp_path, settings_path=settings)
