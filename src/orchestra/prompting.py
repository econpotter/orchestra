from __future__ import annotations

from importlib import resources
from pathlib import Path


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def render(template_text: str, context: dict) -> str:
    return template_text.format_map(_SafeDict(context))


def render_file(path: str | Path, context: dict) -> str:
    return render(Path(path).read_text(), context)


def render_prompt(root: str | Path, configured_path: str | Path, context: dict) -> str:
    """Render a workspace override or the installed default prompt."""
    configured = Path(configured_path)
    workspace_path = Path(root) / configured
    if workspace_path.is_file():
        return render_file(workspace_path, context)
    if configured.parent != Path("prompts"):
        raise FileNotFoundError(f"prompt not found: {workspace_path}")
    resource = resources.files("orchestra").joinpath("defaults", "prompts", configured.name)
    if not resource.is_file():
        raise FileNotFoundError(
            f"prompt not found in workspace or installed defaults: {configured}"
        )
    return render(resource.read_text(), context)
