# src/orchestra/scaffold.py
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from orchestra import layout


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def new_project(
    root: str | Path,
    name: str,
    *,
    lang: str,
    stage: str,
    template_path: str | Path,
) -> Path:
    root = Path(root)
    dest = root / "projects" / name
    if dest.exists():
        raise FileExistsError(f"project dir already exists: {dest}")

    # resolve template_path against root (pathlib keeps absolute paths absolute)
    src = root / Path(template_path)

    # copy the template (without its own git history) into the destination
    shutil.copytree(
        src, dest, ignore=shutil.ignore_patterns(".git")
    )

    # everything after the copy must be all-or-nothing: if any step fails,
    # remove the half-materialized dest so the run is retryable.
    try:
        # materialize the chosen variant + substitute placeholders (single source: init.sh)
        subprocess.run(
            ["bash", str(dest / "init.sh"), lang, name, stage],
            cwd=str(dest), check=True, capture_output=True,
        )

        # fresh git history for the new project
        _git(dest, "init", "-b", "main")
        _git(dest, "add", "-A")
        subprocess.run(
            ["git", "-C", str(dest),
             "-c", "user.email=orchestra@local", "-c", "user.name=orchestra",
             "commit", "-m", f"init {name} ({lang}, {stage})"],
            check=True, capture_output=True,
        )

        # register in PROJECTS.md
        pf = root / "PROJECTS.md"
        block = (
            f"\n## {name}\n- Path: projects/{name}\n- Branch: main\n"
            f"- Purpose: TODO\n- Queue: queue/{name}.md\n- Workflow: {lang}\n- Focus: none\n"
        )
        existing = pf.read_text() if pf.exists() else "# Projects\n"
        pf.write_text(existing.rstrip() + "\n" + block)

        # empty active queue
        qf = layout.queue_file(root, name)
        qf.parent.mkdir(parents=True, exist_ok=True)
        qf.write_text("")
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest
