# tests/test_scaffold.py
import shutil
import subprocess
from pathlib import Path

import pytest

from orchestra.scaffold import new_project


def _fixture_template(tmp: Path) -> Path:
    """A minimal but faithful template: shared AGENTS.md + variants + init.sh."""
    t = tmp / "tmpl"
    (t / "variants" / "python" / "src" / "PROJECT_SLUG").mkdir(parents=True)
    (t / "variants" / "r").mkdir(parents=True)
    (t / "AGENTS.md").write_text(
        "# AGENTS.md — PROJECT_NAME\nLifecycle stage: PROJECT_STAGE\n"
        "If working with orchestra, run `orchestra guide`.\n"
    )
    (t / "variants" / "python" / "pyproject.toml").write_text('[project]\nname = "PROJECT_SLUG"\n')
    (t / "variants" / "python" / "src" / "PROJECT_SLUG" / "__init__.py").write_text("")
    (t / "variants" / "python" / ".gitignore.append").write_text(".venv/\n")
    (t / "variants" / "r" / "DESCRIPTION").write_text("Package: PROJECT_SLUG\n")
    (t / "variants" / "r" / ".gitignore.append").write_text("renv/library/\n")
    (t / ".gitignore").write_text("# base\n")
    # init.sh: hoist variant, append gitignore, drop variants/, substitute, rename pkg dir
    (t / "init.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'lang="$1"; name="$2"; stage="${3:-alpha}"\n'
        'slug=$(echo "$name" | tr "[:upper:]" "[:lower:]" | sed "s/[^a-z0-9]/_/g")\n'
        'shopt -s dotglob\n'
        'mv variants/"$lang"/* .\n'
        'shopt -u dotglob\n'
        'if [ -f .gitignore.append ]; then cat .gitignore.append >> .gitignore; rm .gitignore.append; fi\n'
        'rm -rf variants\n'
        '[ -d src/PROJECT_SLUG ] && mv src/PROJECT_SLUG "src/$slug" || true\n'
        'grep -rl PROJECT_ . 2>/dev/null | while read -r f; do '
        'sed -i "s/PROJECT_NAME/$name/g; s/PROJECT_SLUG/$slug/g; s/PROJECT_STAGE/$stage/g" "$f"; done\n'
    )
    (t / "init.sh").chmod(0o755)
    return t


def _setup_root(tmp: Path):
    (tmp / "queue").mkdir(parents=True)
    (tmp / "PROJECTS.md").write_text("# Projects\n")
    (tmp / "projects").mkdir()


def test_new_project_python(tmp_path: Path):
    tmpl = _fixture_template(tmp_path)
    _setup_root(tmp_path)
    dest = new_project(tmp_path, "Demo Service", lang="python", stage="alpha",
                       template_path=tmpl)
    assert dest == tmp_path / "projects" / "Demo Service"
    # variant materialized + placeholders substituted + pkg dir renamed
    assert (dest / "pyproject.toml").read_text() == '[project]\nname = "demo_service"\n'
    assert (dest / "src" / "demo_service" / "__init__.py").exists()
    assert not (dest / "variants").exists()
    assert "Lifecycle stage: alpha" in (dest / "AGENTS.md").read_text()
    assert "orchestra guide" in (dest / "AGENTS.md").read_text()
    assert ".venv/" in (dest / ".gitignore").read_text()
    # git repo created
    assert (dest / ".git").exists()
    # registered + queue created
    projects = (tmp_path / "PROJECTS.md").read_text()
    assert "## Demo Service" in projects and "Workflow: python" in projects \
        and "projects/Demo Service" in projects
    assert (tmp_path / "queue" / "Demo Service.md").exists()


def test_new_project_r(tmp_path: Path):
    tmpl = _fixture_template(tmp_path)
    _setup_root(tmp_path)
    dest = new_project(tmp_path, "myr", lang="r", stage="beta", template_path=tmpl)
    assert (dest / "DESCRIPTION").read_text() == "Package: myr\n"
    assert "Workflow: r" in (tmp_path / "PROJECTS.md").read_text()
    assert "Lifecycle stage: beta" in (dest / "AGENTS.md").read_text()


def test_new_project_refuses_existing(tmp_path: Path):
    tmpl = _fixture_template(tmp_path)
    _setup_root(tmp_path)
    (tmp_path / "projects" / "dup").mkdir()
    with pytest.raises(FileExistsError):
        new_project(tmp_path, "dup", lang="python", stage="alpha", template_path=tmpl)


def test_new_project_unknown_lang_raises(tmp_path: Path):
    tmpl = _fixture_template(tmp_path)
    _setup_root(tmp_path)
    with pytest.raises(subprocess.CalledProcessError):
        new_project(tmp_path, "x", lang="cobol", stage="alpha", template_path=tmpl)
    # dest was cleaned up on failure, so the run is retryable
    assert not (tmp_path / "projects" / "x").exists()


def test_new_project_relative_template_resolves_against_root(tmp_path: Path, monkeypatch, tmp_path_factory):
    # build the template UNDER the root at projects/project-template
    tmpl = _fixture_template(tmp_path)
    _setup_root(tmp_path)
    rel = tmp_path / "projects" / "project-template"
    shutil.move(str(tmpl), str(rel))
    # run from an unrelated cwd to prove the relative path resolves against root, not cwd
    other = tmp_path_factory.mktemp("elsewhere")
    monkeypatch.chdir(other)
    dest = new_project(tmp_path, "demo", lang="python", stage="alpha",
                       template_path="projects/project-template")
    assert dest == tmp_path / "projects" / "demo"
    assert (dest / "pyproject.toml").read_text() == '[project]\nname = "demo"\n'
    assert (dest / "src" / "demo" / "__init__.py").exists()
    assert "Workflow: python" in (tmp_path / "PROJECTS.md").read_text()
