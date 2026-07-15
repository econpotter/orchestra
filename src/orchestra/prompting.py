from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

CODEX_INSTRUCTION_MAX_BYTES = 32768


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


@dataclass(frozen=True)
class InstructionSource:
    path: str
    sha256: str


@dataclass(frozen=True)
class InstructionBundle:
    text: str
    sources: tuple[InstructionSource, ...]


def resolve_configured_instruction(
    root: str | Path, configured_path: str | Path
) -> tuple[str, InstructionSource]:
    path = Path(configured_path).expanduser()
    if not path.is_absolute():
        path = Path(root) / path
    path = path.resolve()
    content = path.read_text()
    return content, InstructionSource(
        path=str(path), sha256=hashlib.sha256(content.encode()).hexdigest()
    )


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


def _project_boundary(root: Path, permitted_boundary: Path) -> Path:
    """Find the nearest repository boundary without escaping the permitted tree."""
    current = root
    while True:
        if (current / ".git").exists():
            return current
        if current == permitted_boundary:
            return permitted_boundary
        current = current.parent


def resolve_instruction_provenance(
    workdir: str | Path, *, boundary: str | Path | None = None,
    harness_kind: str | None = None,
) -> InstructionBundle:
    """Capture repository-owned instructions and immutable source provenance."""
    root = Path(workdir).resolve()
    stop = Path(boundary).resolve() if boundary is not None else root
    try:
        root.relative_to(stop)
    except ValueError:
        raise ValueError(f"instruction workdir {root} is outside boundary {stop}") from None
    project_root = _project_boundary(root, stop)
    directories = [root, *root.parents]
    directories = list(reversed(directories[:directories.index(project_root) + 1]))
    parts: list[str] = []
    sources: list[InstructionSource] = []
    seen: set[Path] = set()
    content_bytes = 0
    for directory in directories:
        names = ("AGENTS.override.md", "AGENTS.md") if harness_kind == "codex" \
            else ("AGENTS.md", "CLAUDE.md")
        for name in names:
            path = directory / name
            if not path.is_file():
                continue
            content = path.read_text()
            if harness_kind == "codex" and not content.strip():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            try:
                resolved.relative_to(project_root)
            except ValueError:
                raise ValueError(
                    f"instruction source {path} resolves outside boundary {project_root}"
                ) from None
            seen.add(resolved)
            label = path.relative_to(project_root)
            content_bytes += len(content.encode())
            if harness_kind == "codex" and content_bytes > CODEX_INSTRUCTION_MAX_BYTES:
                raise ValueError(
                    "Codex project instructions exceed the default 32768-byte discovery limit"
                )
            parts.append(f"# {label}\n\n{content.rstrip()}")
            sources.append(InstructionSource(
                path=str(label),
                sha256=hashlib.sha256(content.encode()).hexdigest(),
            ))
            if harness_kind == "codex":
                break
    text = "\n\n".join(parts) + ("\n" if parts else "")
    return InstructionBundle(text=text, sources=tuple(sources))


def resolve_instruction_bundle(workdir: str | Path, *, boundary: str | Path | None = None) -> str:
    """Compatibility wrapper returning only the captured instruction text."""
    return resolve_instruction_provenance(workdir, boundary=boundary).text
