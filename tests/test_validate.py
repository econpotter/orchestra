# tests/test_validate.py

from orchestra.issue import parse_issue
from orchestra.validate import validate_structural

GOOD = """\
## #042 wf: add retry
Status: open
Priority: 3
Plan: null
Spec: docs/specs/x.md
Depends On: null
Retries: 0
Worker: null
Acceptance:
- [ ] retries 5xx
### Decisions
### Blocked Reason
"""


def _issue(text):
    return parse_issue(text)


def test_valid_issue(tmp_path):
    (tmp_path / "projects" / "wf" / "docs" / "specs").mkdir(parents=True)
    (tmp_path / "projects" / "wf" / "docs" / "specs" / "x.md").write_text("spec")
    res = validate_structural(_issue(GOOD), project_path="projects/wf",
                              orchestra_root=tmp_path, known_ids=set())
    assert res.ok is True


def test_missing_spec_file(tmp_path):
    res = validate_structural(_issue(GOOD), project_path="projects/wf",
                              orchestra_root=tmp_path, known_ids=set())
    assert res.ok is False
    assert any("docs/specs/x.md" in r for r in res.reasons)


def test_inline_task_no_plan_or_spec_is_valid(tmp_path):
    text = GOOD.replace("Spec: docs/specs/x.md", "Spec: null")
    res = validate_structural(_issue(text), project_path="projects/wf",
                              orchestra_root=tmp_path, known_ids=set())
    assert res.ok is True


def test_no_acceptance(tmp_path):
    text = GOOD.replace("- [ ] retries 5xx\n", "")
    (tmp_path / "projects" / "wf" / "docs" / "specs").mkdir(parents=True)
    (tmp_path / "projects" / "wf" / "docs" / "specs" / "x.md").write_text("spec")
    res = validate_structural(_issue(text), project_path="projects/wf",
                              orchestra_root=tmp_path, known_ids=set())
    assert res.ok is False
    assert any("Acceptance" in r for r in res.reasons)


def test_unknown_dependency(tmp_path):
    text = GOOD.replace("Depends On: null", "Depends On: 7")
    (tmp_path / "projects" / "wf" / "docs" / "specs").mkdir(parents=True)
    (tmp_path / "projects" / "wf" / "docs" / "specs" / "x.md").write_text("spec")
    res = validate_structural(_issue(text), project_path="projects/wf",
                              orchestra_root=tmp_path, known_ids={1, 2})
    assert res.ok is False
    assert any("#7" in r for r in res.reasons)


def test_dependency_on_archived_issue_is_valid(tmp_path):
    """Regression (issue #003): a Depends On pointing at an issue that has left the live
    queue for the archive must resolve — otherwise validate blocks it while dispatch (which
    checks done_numbers) passes it, producing a re-block loop."""
    text = GOOD.replace("Depends On: null", "Depends On: 7")
    (tmp_path / "projects" / "wf" / "docs" / "specs").mkdir(parents=True)
    (tmp_path / "projects" / "wf" / "docs" / "specs" / "x.md").write_text("spec")
    res = validate_structural(_issue(text), project_path="projects/wf",
                              orchestra_root=tmp_path, known_ids={1, 2}, archived_ids={7})
    assert res.ok is True


def test_unknown_dependency_still_blocks_with_archived(tmp_path):
    """A truly unknown number — absent from both live queue and archive — still blocks."""
    text = GOOD.replace("Depends On: null", "Depends On: 9")
    (tmp_path / "projects" / "wf" / "docs" / "specs").mkdir(parents=True)
    (tmp_path / "projects" / "wf" / "docs" / "specs" / "x.md").write_text("spec")
    res = validate_structural(_issue(text), project_path="projects/wf",
                              orchestra_root=tmp_path, known_ids={1, 2}, archived_ids={7})
    assert res.ok is False
    assert any("#9" in r for r in res.reasons)


def test_referenced_spec_still_must_exist(tmp_path):
    res = validate_structural(_issue(GOOD), project_path="projects/wf",
                              orchestra_root=tmp_path, known_ids=set())
    assert res.ok is False
    assert any("docs/specs/x.md" in r for r in res.reasons)


def _dep_issue(number: int, depends: str) -> str:
    """A minimal, spec-free issue with a given number and Depends On value."""
    return (
        f"## #{number} wf: t\n"
        "Status: open\nPriority: 3\nPlan: null\nSpec: null\n"
        f"Depends On: {depends}\n"
        "Retries: 0\nWorker: null\nAcceptance:\n- [ ] do x\n"
        "### Decisions\n### Blocked Reason\n"
    )


def test_self_dependency_blocks(tmp_path):
    issue = _issue(_dep_issue(42, "42"))
    res = validate_structural(
        issue, project_path="projects/wf", orchestra_root=tmp_path,
        known_ids={42}, dep_graph={42: [42]},
    )
    assert res.ok is False
    assert any("self-dependency" in r and "#42" in r for r in res.reasons)


def test_two_cycle_blocks(tmp_path):
    graph = {1: [2], 2: [1]}
    res = validate_structural(
        _issue(_dep_issue(1, "2")), project_path="projects/wf", orchestra_root=tmp_path,
        known_ids={1, 2}, dep_graph=graph,
    )
    assert res.ok is False
    assert any("dependency cycle: #1 -> #2 -> #1" in r for r in res.reasons)


def test_longer_cycle_blocks(tmp_path):
    # 1 -> 2 -> 3 -> 1
    graph = {1: [2], 2: [3], 3: [1]}
    res = validate_structural(
        _issue(_dep_issue(1, "2")), project_path="projects/wf", orchestra_root=tmp_path,
        known_ids={1, 2, 3}, dep_graph=graph,
    )
    assert res.ok is False
    assert any("dependency cycle: #1 -> #2 -> #3 -> #1" in r for r in res.reasons)


def test_acyclic_chain_is_valid(tmp_path):
    # 1 -> 2, 2 -> nothing: a real dependency, no cycle.
    graph = {1: [2], 2: []}
    res = validate_structural(
        _issue(_dep_issue(1, "2")), project_path="projects/wf", orchestra_root=tmp_path,
        known_ids={1, 2}, dep_graph=graph,
    )
    assert res.ok is True


def test_cycle_check_skipped_without_dep_graph(tmp_path):
    # Back-compat: callers that don't pass a graph get the old behavior (no cycle check).
    res = validate_structural(
        _issue(_dep_issue(1, "1")), project_path="projects/wf", orchestra_root=tmp_path,
        known_ids={1},
    )
    assert res.ok is True


def _git(repo, *a):
    import subprocess
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _init_repo(repo):
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")


def test_base_branch_spec_committed_ok(tmp_path):
    repo = tmp_path / "projects" / "wf"
    _init_repo(repo)
    (repo / "docs" / "specs").mkdir(parents=True)
    (repo / "docs" / "specs" / "x.md").write_text("spec")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    res = validate_structural(_issue(GOOD), project_path="projects/wf",
                              orchestra_root=tmp_path, known_ids=set(), base_branch="main")
    assert res.ok is True


def test_base_branch_spec_on_disk_but_uncommitted_blocks(tmp_path):
    """The exact failure mode: file on disk in the root checkout, NOT in the base branch
    a worker branches off — must be flagged (was silently passing the on-disk check)."""
    repo = tmp_path / "projects" / "wf"
    _init_repo(repo)
    (repo / "README.md").write_text("x\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    # spec exists on disk but is never committed to main
    (repo / "docs" / "specs").mkdir(parents=True)
    (repo / "docs" / "specs" / "x.md").write_text("spec")
    res = validate_structural(_issue(GOOD), project_path="projects/wf",
                              orchestra_root=tmp_path, known_ids=set(), base_branch="main")
    assert res.ok is False
    assert any("base branch 'main'" in r and "x.md" in r for r in res.reasons)
