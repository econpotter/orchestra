from pathlib import Path

from orchestra.planparse import propose_issues_from_plan


def test_propose_from_headings(tmp_path: Path):
    p = tmp_path / "plan.md"
    p.write_text(
        "# My Plan\n\n## Global Constraints\nstuff\n\n"
        "## Task 1: add retry\nbody\n\n## Task 2: add cache\nbody\n"
    )
    out = propose_issues_from_plan(p, "wf")
    titles = [o["title"] for o in out]
    assert titles == ["Task 1: add retry", "Task 2: add cache"]  # H1 + Global Constraints skipped
    assert out[0]["plan"].endswith("plan.md#task-1-add-retry")
