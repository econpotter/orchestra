from pathlib import Path

from orchestra.adapter import build_argv
from orchestra.config import Sandbox, load_config

ROOT = Path(__file__).resolve().parent.parent


def test_example_config_has_codex_provider():
    cfg = load_config(ROOT / "config.example.yaml")
    assert set(cfg.providers) == {"codex"}


def test_codex_argv_renders():
    cfg = load_config(ROOT / "config.example.yaml")
    argv = build_argv(cfg.providers["codex"], Sandbox(False, []),
                      {"model": "gpt-5-codex", "workdir": "/wt"})
    assert argv[:2] == ["codex", "exec"]
    assert "gpt-5-codex" in argv
    assert "/wt" in argv
    assert cfg.providers["codex"].prompt == "stdin"
