import subprocess
from pathlib import Path

import pytest


def _run(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-b", "main")
    _run(repo, "config", "user.email", "t@t.com")
    _run(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("hello\n")
    _run(repo, "add", "README.md")
    _run(repo, "commit", "-m", "init")
    return repo
