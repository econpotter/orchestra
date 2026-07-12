from pathlib import Path

from orchestra.prompting import render, render_file, render_prompt


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
