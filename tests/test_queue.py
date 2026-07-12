from pathlib import Path

from orchestra import layout
from orchestra.issue import parse_issue
from orchestra.queue import find_issue, read_queue, write_queue

TWO = """\
## #001 weather-api: first
Status: open
Priority: 1
Plan: null
Spec: docs/specs/x.md
Depends On: null
Retries: 0
Worker: null
Acceptance:
- [ ] does a thing
### Decisions
### Blocked Reason

## #002 weather-api: second
Status: validated
Priority: 2
Plan: null
Spec: docs/specs/y.md
Depends On: 1
Retries: 0
Worker: null
Acceptance:
- [ ] does another thing
### Decisions
### Blocked Reason
"""


def test_read_queue(tmp_path: Path):
    p = tmp_path / "q.md"
    p.write_text(TWO)
    issues = read_queue(p)
    assert [i.number for i in issues] == [1, 2]
    assert find_issue(issues, 2).status == "validated"
    assert find_issue(issues, 99) is None


def test_round_trip_queue(tmp_path: Path):
    p = tmp_path / "q.md"
    p.write_text(TWO)
    issues = read_queue(p)
    write_queue(p, issues)
    assert read_queue(p) == issues


# Fix B: write_queue creates a sibling .lock file and round-trip still holds
def test_write_queue_lockfile(tmp_path: Path):
    p = tmp_path / "q.md"
    p.write_text(TWO)
    issues = read_queue(p)
    write_queue(p, issues)
    lock = tmp_path / "q.md.lock"
    assert lock.exists(), "lockfile should exist after write_queue"
    # second sequential write also succeeds
    write_queue(p, issues)
    assert read_queue(p) == issues


def test_layout_paths(tmp_path: Path):
    root = tmp_path
    assert layout.queue_file(root, "wf") == root / "queue" / "wf.md"
    assert layout.archive_file(root, "wf") == root / "queue" / "archive" / "wf.md"
    assert (
        layout.worktree_dir(root, "wf", 42)
        == root / ".orchestra" / "worktrees" / "wf-042"
    )
    assert (
        layout.result_file(root, "wf", 42)
        == root / ".orchestra" / "results" / "wf#042.json"
    )


def test_next_number():
    from orchestra.queue import next_number

    def mk(n):
        return parse_issue(
            f"## #{n:03d} wf: t\nStatus: open\nPriority: 1\nPlan: null\nSpec: null\n"
            f"Depends On: null\nRetries: 0\nWorker: null\nAcceptance:\n- [ ] x\n"
            f"### Decisions\n### Blocked Reason\n"
        )
    assert next_number([], []) == 1
    assert next_number([mk(1), mk(3)], [mk(2)]) == 4
