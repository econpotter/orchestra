from pathlib import Path

from orchestra.cli import main


def _fixture_template(tmp: Path) -> Path:
    """Self-contained minimal python template + init.sh (no cross-test import)."""
    t = tmp / "tmpl"
    (t / "variants" / "python" / "src" / "PROJECT_SLUG").mkdir(parents=True)
    (t / "AGENTS.md").write_text("# PROJECT_NAME\nLifecycle stage: PROJECT_STAGE\n")
    (t / "variants" / "python" / "pyproject.toml").write_text('[project]\nname = "PROJECT_SLUG"\n')
    (t / "variants" / "python" / "src" / "PROJECT_SLUG" / "__init__.py").write_text("")
    (t / ".gitignore").write_text("# base\n")
    (t / "init.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'lang="$1"; name="$2"; stage="${3:-alpha}"\n'
        '[ -d "variants/$lang" ] || { echo "unknown lang: $lang" >&2; exit 1; }\n'
        'slug=$(printf "%s" "$name" | tr "[:upper:]" "[:lower:]" | sed "s/[^a-z0-9]/_/g")\n'
        'shopt -s dotglob; mv "variants/$lang"/* .; shopt -u dotglob\n'
        'rm -rf variants\n'
        '[ -d src/PROJECT_SLUG ] && mv src/PROJECT_SLUG "src/$slug" || true\n'
        'grep -rl PROJECT_ . | while read -r f; do '
        'sed -i "s/PROJECT_NAME/$name/g; s/PROJECT_SLUG/$slug/g; s/PROJECT_STAGE/$stage/g" "$f"; done\n'
    )
    (t / "init.sh").chmod(0o755)
    return t


def _setup_root(tmp: Path, tmpl: Path):
    (tmp / "queue").mkdir(parents=True)
    (tmp / "PROJECTS.md").write_text("# Projects\n")
    (tmp / "projects").mkdir()
    (tmp / "config.yaml").write_text(
        "slots: 1\nroles: {}\n"
        f"template_path: {tmpl}\n"
        "workflows:\n  python: { test: \"uv run pytest\" }\n  r: { test: \"x\" }\n"
    )


def test_cli_new_project(tmp_path, capsys):
    tmpl = _fixture_template(tmp_path)
    _setup_root(tmp_path, tmpl)
    rc = main(["--root", str(tmp_path), "new-project", "demo", "--lang", "python"])
    out = capsys.readouterr().out
    assert rc == 0
    assert (tmp_path / "projects" / "demo" / "pyproject.toml").exists()
    assert "Workflow: python" in (tmp_path / "PROJECTS.md").read_text()
    assert "projects/demo" in out


def test_cli_new_project_existing_exits_2(tmp_path, capsys):
    tmpl = _fixture_template(tmp_path)
    _setup_root(tmp_path, tmpl)
    (tmp_path / "projects" / "demo").mkdir()
    rc = main(["--root", str(tmp_path), "new-project", "demo", "--lang", "python"])
    assert rc == 2


def test_cli_new_project_script_fails_exits_1(tmp_path, capsys):
    tmpl = _fixture_template(tmp_path)
    (tmpl / "init.sh").write_text("#!/usr/bin/env bash\nexit 1\n")
    (tmpl / "init.sh").chmod(0o755)
    _setup_root(tmp_path, tmpl)
    rc = main(["--root", str(tmp_path), "new-project", "demo", "--lang", "python"])
    assert rc == 1
    assert "scaffold failed" in capsys.readouterr().err


def test_cli_new_project_missing_template_exits_1(tmp_path, capsys):
    tmpl = _fixture_template(tmp_path)
    _setup_root(tmp_path, tmpl)
    # point config at a template path that does not exist
    (tmp_path / "config.yaml").write_text(
        "slots: 1\nroles: {}\n"
        "template_path: projects/does-not-exist\n"
        "workflows:\n  python: { test: \"uv run pytest\" }\n  r: { test: \"x\" }\n"
    )
    rc = main(["--root", str(tmp_path), "new-project", "demo", "--lang", "python"])
    assert rc == 1
    assert "template not found" in capsys.readouterr().err
