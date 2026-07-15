from __future__ import annotations

import hashlib
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def package_tree_digest(package_root: str | Path) -> str:
    """Fingerprint the executable Python and packaged prompt surface in stable path order."""
    root = Path(package_root).resolve()
    digest = hashlib.sha256()
    paths = sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts
    )
    logical_files = {
        path.relative_to(root).as_posix(): path.read_bytes() for path in paths
    }
    source_prompts = root.parents[1] / "prompts" if root.parent.name == "src" else None
    if source_prompts and source_prompts.is_dir():
        for path in sorted(source_prompts.glob("*.md")):
            logical_files[f"defaults/prompts/{path.name}"] = path.read_bytes()
    for name, content in sorted(logical_files.items()):
        relative = name.encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _git_value(package_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(package_root), *args], text=True, capture_output=True,
        timeout=5, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def runtime_provenance(package_root: str | Path | None = None) -> dict[str, object]:
    root = Path(package_root).resolve() if package_root else Path(__file__).resolve().parent
    try:
        package_version = version("orchestra")
    except PackageNotFoundError:
        package_version = "uninstalled"
    commit = _git_value(root, "rev-parse", "HEAD")
    dirty = bool(_git_value(root, "status", "--porcelain")) if commit else False
    return {
        "version": package_version,
        "package_root": str(root),
        "package_sha256": package_tree_digest(root),
        "git_commit": commit,
        "git_dirty": dirty,
    }
