from orchestra.cli import main


def test_guide_prints_integration_doc(capsys):
    rc = main(["guide"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "orchestra issue add" in out
    assert "awaiting_review -> needs_rework" in out
    assert "blocked -> open" in out


def test_root_defaults_to_none(monkeypatch):
    from orchestra.cli import build_parser
    monkeypatch.setenv("ORCHESTRA_ROOT", "/tmp/some-root")
    args = build_parser().parse_args(["guide"])
    assert args.root is None


def test_workspace_show_uses_upward_discovery(tmp_path, monkeypatch, capsys):
    root = tmp_path / "workspace"
    nested = root / "projects" / "demo"
    nested.mkdir(parents=True)
    (root / "config.yaml").write_text("slots: 1\n")
    (root / "PROJECTS.md").write_text("# Projects\n")
    monkeypatch.chdir(nested)
    monkeypatch.delenv("ORCHESTRA_ROOT", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))

    assert main(["workspace", "show"]) == 0
    assert capsys.readouterr().out.strip() == str(root.resolve())


def test_root_after_subcommand(tmp_path, capsys):
    """--root must work AFTER the subcommand too (systemd/docs write `orchestra tick --root X`)."""
    from orchestra.cli import main
    (tmp_path / "queue").mkdir()
    (tmp_path / "PROJECTS.md").write_text("# Projects\n")
    (tmp_path / "config.yaml").write_text("slots: 1\n")
    # status reads the root; --root after the subcommand must be honored
    rc = main(["status", "--root", str(tmp_path), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"counts"' in out  # ran against tmp_path, not cwd


def test_root_equals_form_after_subcommand(tmp_path):
    from orchestra.cli import main
    (tmp_path / "queue").mkdir()
    (tmp_path / "PROJECTS.md").write_text("# Projects\n")
    (tmp_path / "config.yaml").write_text("slots: 1\n")
    assert main(["status", f"--root={tmp_path}", "--json"]) == 0


def test_issue_list_surfaces_blocked_dependency(tmp_path, capsys):
    from orchestra.cli import main
    (tmp_path / "queue").mkdir()
    (tmp_path / "PROJECTS.md").write_text(
        "# Projects\n\n## wf\n- Path: projects/wf\n- Branch: main\n"
        "- Purpose: t\n- Queue: queue/wf.md\n- Focus: none\n"
    )
    (tmp_path / "config.yaml").write_text("slots: 1\n")

    def _issue(num, status, deps="null"):
        return (
            f"## #{num:03d} wf: t{num}\nStatus: {status}\nPriority: 1\nPlan: null\nSpec: null\n"
            f"Depends On: {deps}\nRetries: 0\nWorker: null\nAcceptance:\n- [ ] x\n"
            f"### Decisions\n### Blocked Reason\n"
        )
    (tmp_path / "queue" / "wf.md").write_text(_issue(1, "blocked") + "\n" + _issue(2, "validated", "1"))

    rc = main(["issue", "list", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    # #002 depends on the blocked #001 -> surfaced
    line2 = [ln for ln in out.splitlines() if "#002" in ln][0]
    assert "blocked dep #1" in line2
