from pathlib import Path

import hashlib
import pytest

from orchestra.prompting import (
    InstructionBundle,
    InstructionSource,
    render,
    render_file,
    render_prompt,
    resolve_instruction_bundle,
    resolve_instruction_provenance,
)


def test_render_substitutes_known_keys():
    out = render("issue {issue} in {workdir}", {"issue": "042", "workdir": "/wt"})
    assert out == "issue 042 in /wt"


def test_render_missing_key_is_empty():
    assert render("a {missing} b", {}) == "a  b"


def test_render_file(tmp_path: Path):
    p = tmp_path / "t.md"
    p.write_text("model={model}")
    assert render_file(p, {"model": "claude-opus-4-8"}) == "model=claude-opus-4-8"


def test_render_prompt_prefers_workspace_override(tmp_path: Path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "worker.md").write_text("workspace {issue}")
    assert render_prompt(tmp_path, "prompts/worker.md", {"issue": "007"}) == "workspace 007"


def test_render_prompt_missing_custom_path_fails_loud(tmp_path: Path):
    import pytest

    with pytest.raises(FileNotFoundError, match="prompt not found"):
        render_prompt(tmp_path, "custom/private.md", {})


def test_worker_prompt_requires_polling_yielded_commands():
    prompt = (Path(__file__).parents[1] / "prompts" / "worker.md").read_text()
    assert "session ID" in prompt
    assert "poll" in prompt.lower()
    assert "does not mean the process was terminated" in prompt


def test_instruction_bundle_stops_at_worktree_boundary(tmp_path):
    (tmp_path / "AGENTS.md").write_text("workspace rules")
    worktree = tmp_path / ".orchestra" / "worktrees" / "p-001"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: /tmp/common/worktrees/p-001\n")
    (worktree / "AGENTS.md").write_text("project rules")

    (worktree / "CLAUDE.md").symlink_to(worktree / "AGENTS.md")
    bundle = resolve_instruction_bundle(worktree, boundary=tmp_path)

    assert "workspace rules" not in bundle
    assert bundle.count("project rules") == 1


def test_instruction_provenance_records_immutable_sources_and_hashes(tmp_path):
    (tmp_path / ".git").mkdir()
    agents = tmp_path / "AGENTS.md"
    agents.write_text("project rules\n")

    bundle = resolve_instruction_provenance(tmp_path, boundary=tmp_path)

    assert isinstance(bundle, InstructionBundle)
    assert bundle.sources == (
        InstructionSource(
            path="AGENTS.md",
            sha256=hashlib.sha256(agents.read_bytes()).hexdigest(),
        ),
    )
    assert bundle.text == "# AGENTS.md\n\nproject rules\n"


def test_instruction_provenance_orders_ancestors_then_names_deterministically(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "CLAUDE.md").write_text("root claude")
    (tmp_path / "AGENTS.md").write_text("root agents")
    nested = tmp_path / "src" / "feature"
    nested.mkdir(parents=True)
    (nested / "CLAUDE.md").write_text("nested claude")
    (nested / "AGENTS.md").write_text("nested agents")

    first = resolve_instruction_provenance(nested, boundary=tmp_path)
    second = resolve_instruction_provenance(nested, boundary=tmp_path)

    assert first == second
    assert [source.path for source in first.sources] == [
        "AGENTS.md",
        "CLAUDE.md",
        "src/feature/AGENTS.md",
        "src/feature/CLAUDE.md",
    ]


def test_instruction_provenance_deduplicates_symlinked_agent_files(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("same rules")
    (tmp_path / "CLAUDE.md").symlink_to(tmp_path / "AGENTS.md")

    bundle = resolve_instruction_provenance(tmp_path, boundary=tmp_path)

    assert [source.path for source in bundle.sources] == ["AGENTS.md"]
    assert bundle.text.count("same rules") == 1


def test_codex_instruction_provenance_uses_override_precedence_and_ignores_claude(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("base rules")
    (tmp_path / "AGENTS.override.md").write_text("override rules")
    (tmp_path / "CLAUDE.md").write_text("claude-only rules")
    nested = tmp_path / "src"
    nested.mkdir()
    (nested / "AGENTS.override.md").write_text("")
    (nested / "AGENTS.md").write_text("nested rules")

    bundle = resolve_instruction_provenance(
        nested, boundary=tmp_path, harness_kind="codex"
    )

    assert [source.path for source in bundle.sources] == [
        "AGENTS.override.md", "src/AGENTS.md",
    ]
    assert "override rules" in bundle.text
    assert "base rules" not in bundle.text
    assert "claude-only rules" not in bundle.text


def test_codex_instruction_provenance_fails_loud_at_default_size_limit(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("x" * 32769)

    with pytest.raises(ValueError, match="32768-byte"):
        resolve_instruction_provenance(tmp_path, boundary=tmp_path, harness_kind="codex")
