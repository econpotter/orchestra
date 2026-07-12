from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUSES = [
    "open", "validated", "in_progress", "committed",
    "needs_rework", "awaiting_review", "blocked", "merged", "archived",
]


def test_protocol_files_exist():
    for rel in [
        "protocol/ISSUE-GUIDE.md",
        "protocol/STATES.md",
        "protocol/OPERATIONS.md",
        "prompts/validator.md",
        "prompts/worker.md",
        "prompts/verify-review.md",
    ]:
        assert (ROOT / rel).exists(), f"missing {rel}"


def test_states_doc_lists_all_statuses():
    text = (ROOT / "protocol" / "STATES.md").read_text()
    for status in STATUSES:
        assert status in text, f"STATES.md missing status {status}"


def test_prompts_are_filled_not_stubs():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for name in ("validator.md", "worker.md", "verify-review.md"):
        text = (root / "prompts" / name).read_text()
        assert "Stub — filled in Phase B" not in text, f"{name} still a stub"
        assert "{result_file}" in text, f"{name} must reference its result file"


def test_operations_doc_describes_engine():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "protocol" / "OPERATIONS.md").read_text()
    assert "dispatch" in text and "reconcile" in text
    assert "workers.json" in text
    assert "sole writer" in text.lower()


def test_worker_prompt_decompose_and_lifecycle():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "prompts" / "worker.md").read_text()
    low = text.lower()
    assert "subagent" in low                 # decompose-or-direct
    assert "agents.md" in low                 # reads project lifecycle policy
    assert "{result_file}" in text
    assert "{verifier_feedback}" in text


def test_operations_doc_covers_scheduling_and_providers():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "protocol" / "OPERATIONS.md").read_text()
    low = text.lower()
    assert "tick" in low
    assert "systemd" in low or "cron" in low
    assert "codex" in low and "pi" in low


def test_operations_doc_notes_tick_latency():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "protocol" / "OPERATIONS.md").read_text()
    assert "latency" in text.lower()


def test_readme_and_cli_docs_present():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    readme = (root / "README.md").read_text().lower()
    assert "uv tool install" in readme            # install step
    assert "orchestra issue add" in readme        # workflow command
    ops = (root / "protocol" / "OPERATIONS.md").read_text().lower()
    assert "orchestra approve" in ops or "control surface" in ops


def test_prompts_reference_title_and_acceptance():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for name in ("validator.md", "worker.md", "verify-review.md"):
        text = (root / "prompts" / name).read_text()
        assert "{title}" in text, f"{name} missing {{title}}"
        assert "{acceptance}" in text, f"{name} missing {{acceptance}}"


def test_prompts_phase_f_content():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    validator = (root / "prompts" / "validator.md").read_text().lower()
    worker = (root / "prompts" / "worker.md").read_text()
    verifier = (root / "prompts" / "verify-review.md").read_text()
    assert "uncertain" in validator and "validated" in validator   # permissive
    assert "{workflow}" in worker and "issue #{issue}: {title}" in worker
    assert "{decisions}" in verifier


def test_new_project_documented():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    assert "new-project" in (root / "README.md").read_text()
    assert "new-project" in (root / "protocol" / "OPERATIONS.md").read_text()
