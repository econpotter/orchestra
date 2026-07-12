from pathlib import Path

from orchestra.projects import find_project, read_projects

PROJECTS = """\
# Projects

## weather-api
- Path: projects/weather-api
- Branch: main
- Purpose: forecasting reservoir levels
- Queue: queue/weather-api.md
- Focus: DuckDB migration

## task-engine
- Path: projects/task-engine
- Branch: develop
- Purpose: core engine
- Queue: queue/task-engine.md
- Focus: none
"""


def test_read_projects(tmp_path: Path):
    p = tmp_path / "PROJECTS.md"
    p.write_text(PROJECTS)
    projects = read_projects(p)
    assert [pr.name for pr in projects] == ["weather-api", "task-engine"]
    wf = find_project(projects, "weather-api")
    assert wf.path == "projects/weather-api"
    assert wf.branch == "main"
    assert wf.queue == "queue/weather-api.md"
    assert find_project(projects, "task-engine").branch == "develop"


def test_find_project_missing(tmp_path: Path):
    p = tmp_path / "PROJECTS.md"
    p.write_text(PROJECTS)
    assert find_project(read_projects(p), "nope") is None


def test_worktree_seed_parsing(tmp_path):
    from orchestra.projects import find_project, read_projects
    p = tmp_path / "PROJECTS.md"
    p.write_text(
        "# Projects\n\n"
        "## alpha\n- Path: projects/alpha\n- Branch: main\n- Purpose: t\n"
        "- Queue: queue/alpha.md\n- Worktree-Seed: data:link\n- Focus: none\n\n"
        "## mixed\n- Path: projects/mixed\n- Branch: main\n- Purpose: t\n"
        "- Queue: queue/mixed.md\n- Worktree-Seed: fixtures, cache:symlink\n- Focus: none\n\n"
        "## bare\n- Path: projects/bare\n- Branch: main\n- Purpose: t\n"
        "- Queue: queue/bare.md\n- Focus: none\n"
    )
    projects = read_projects(p)
    assert find_project(projects, "alpha").worktree_seed == [("data", "link")]
    # default mode is copy; `symlink` normalizes to `link`
    assert find_project(projects, "mixed").worktree_seed == [
        ("fixtures", "copy"),
        ("cache", "link"),
    ]
    assert find_project(projects, "bare").worktree_seed == []


def test_worktree_seed_bad_mode(tmp_path):
    import pytest

    from orchestra.projects import read_projects
    p = tmp_path / "PROJECTS.md"
    p.write_text(
        "# Projects\n\n## x\n- Path: projects/x\n- Branch: main\n- Purpose: t\n"
        "- Queue: queue/x.md\n- Worktree-Seed: data:move\n- Focus: none\n"
    )
    with pytest.raises(ValueError):
        read_projects(p)


def test_worktree_db_parsing(tmp_path):
    from orchestra.projects import find_project, read_projects
    p = tmp_path / "PROJECTS.md"
    p.write_text(
        "# Projects\n\n"
        "## db\n- Path: projects/db\n- Branch: main\n- Purpose: t\n"
        "- Queue: queue/db.md\n- Worktree-DB: postgres\n- Focus: none\n\n"
        "## nodb\n- Path: projects/nodb\n- Branch: main\n- Purpose: t\n"
        "- Queue: queue/nodb.md\n- Focus: none\n"
    )
    projects = read_projects(p)
    assert find_project(projects, "db").worktree_db == "postgres"
    assert find_project(projects, "nodb").worktree_db == ""  # absent = zero behavior


def test_worktree_db_bad_value(tmp_path):
    import pytest

    from orchestra.projects import read_projects
    p = tmp_path / "PROJECTS.md"
    p.write_text(
        "# Projects\n\n## x\n- Path: projects/x\n- Branch: main\n- Purpose: t\n"
        "- Queue: queue/x.md\n- Worktree-DB: mysql\n- Focus: none\n"
    )
    with pytest.raises(ValueError):
        read_projects(p)


def test_workflow_field(tmp_path):
    from orchestra.projects import read_projects, find_project
    p = tmp_path / "PROJECTS.md"
    p.write_text(
        "# Projects\n\n## wf\n- Path: projects/wf\n- Branch: main\n"
        "- Purpose: t\n- Queue: queue/wf.md\n- Workflow: python\n- Focus: none\n\n"
        "## other\n- Path: projects/other\n- Branch: main\n- Purpose: t\n"
        "- Queue: queue/other.md\n- Focus: none\n"
    )
    projects = read_projects(p)
    assert find_project(projects, "wf").workflow == "python"
    assert find_project(projects, "other").workflow == "python"  # default when absent
