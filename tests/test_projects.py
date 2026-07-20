from pathlib import Path

import pytest

from orchestra.projects import (
    DuplicateProjectError,
    DuplicateProjectWarning,
    ensure_name_available,
    find_project,
    read_projects,
)

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


def test_read_projects_duplicate_name_warns(tmp_path: Path):
    # A hand-introduced duplicate must be loud, not a silent first-match.
    p = tmp_path / "PROJECTS.md"
    p.write_text(
        "# Projects\n\n"
        "## openbrain\n- Path: projects/openbrain\n- Branch: main\n- Purpose: a\n"
        "- Queue: queue/openbrain.md\n- Focus: none\n\n"
        "## openbrain\n- Path: projects/openbrain\n- Branch: dev\n- Purpose: b\n"
        "- Queue: queue/openbrain.md\n- Focus: none\n"
    )
    with pytest.warns(DuplicateProjectWarning, match="openbrain"):
        projects = read_projects(p)
    # still returns both blocks; the point is the load is no longer silent
    assert [pr.name for pr in projects] == ["openbrain", "openbrain"]


def test_ensure_name_available_rejects_registered(tmp_path: Path):
    p = tmp_path / "PROJECTS.md"
    p.write_text(PROJECTS)
    with pytest.raises(DuplicateProjectError, match="task-engine"):
        ensure_name_available(p, "task-engine")
    # a fresh name and a missing registry are both fine
    ensure_name_available(p, "brand-new")
    ensure_name_available(tmp_path / "absent.md", "task-engine")


def test_worktree_seed_parsing(tmp_path):
    from orchestra.projects import find_project, read_projects
    p = tmp_path / "PROJECTS.md"
    p.write_text(
        "# Projects\n\n"
        "## alpha\n- Path: projects/alpha\n- Branch: main\n- Purpose: t\n"
        "- Queue: queue/alpha.md\n- Worktree-Seed: data:link\n- Focus: none\n\n"
        "## mixed\n- Path: projects/mixed\n- Branch: main\n- Purpose: t\n"
        "- Queue: queue/mixed.md\n"
        "- Worktree-Seed: fixtures, cache:symlink, data/raw:ro-link\n"
        "- Focus: none\n\n"
        "## bare\n- Path: projects/bare\n- Branch: main\n- Purpose: t\n"
        "- Queue: queue/bare.md\n- Focus: none\n"
    )
    projects = read_projects(p)
    assert find_project(projects, "alpha").worktree_seed == [("data", "link")]
    # default mode is copy; `symlink` normalizes to `link`
    assert find_project(projects, "mixed").worktree_seed == [
        ("fixtures", "copy"),
        ("cache", "link"),
        ("data/raw", "ro-link"),
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


@pytest.mark.parametrize("seed", ["/data:ro-link", "../data:ro-link", "data/../raw:ro-link"])
def test_worktree_seed_rejects_paths_outside_project(tmp_path, seed):
    p = tmp_path / "PROJECTS.md"
    p.write_text(
        "# Projects\n\n## x\n- Path: projects/x\n- Branch: main\n- Purpose: t\n"
        f"- Queue: queue/x.md\n- Worktree-Seed: {seed}\n- Focus: none\n"
    )
    with pytest.raises(ValueError, match="relative project path"):
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
