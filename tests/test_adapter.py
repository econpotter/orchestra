import subprocess
import sys
import time
from pathlib import Path

from orchestra.adapter import build_argv, launch
from orchestra.config import ProviderConfig, Sandbox
from orchestra.selection import pid_alive

REPO_ROOT = Path(__file__).resolve().parent.parent
FAKE = REPO_ROOT / "tests" / "fake_agent.py"


def _fake_provider() -> ProviderConfig:
    return ProviderConfig(
        argv=[sys.executable, str(FAKE), "--role", "{role}", "--result-file", "{result_file}"],
        prompt="stdin",
    )


def test_build_argv_substitutes_and_no_sandbox():
    p = ProviderConfig(argv=["claude", "--model", "{model}"], prompt="stdin")
    argv = build_argv(p, Sandbox(enabled=False, argv_prefix=["x"]), {"model": "m1"})
    assert argv == ["claude", "--model", "m1"]


def test_build_argv_prepends_sandbox_when_enabled():
    p = ProviderConfig(argv=["claude", "{model}"], prompt="stdin")
    sb = Sandbox(enabled=True, argv_prefix=["bwrap", "--bind", "{workdir}"])
    argv = build_argv(p, sb, {"model": "m1", "workdir": "/wt"})
    assert argv == ["bwrap", "--bind", "/wt", "claude", "m1"]


def _wait_dead(pid: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.1)
    raise AssertionError(f"pid {pid} still alive after {timeout}s")


def _init_repo(repo: Path) -> None:
    def g(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    repo.mkdir(parents=True)
    g("init", "-b", "main")
    g("config", "user.email", "t@t.com")
    g("config", "user.name", "t")
    (repo / "README.md").write_text("x\n")
    g("add", "README.md")
    g("commit", "-m", "init")


def test_launch_fake_worker_commits_and_writes_result(tmp_path: Path):
    from orchestra.result import read_result
    repo = tmp_path / "repo"
    _init_repo(repo)
    rf = tmp_path / "results" / "wf#001.json"
    log = tmp_path / "logs" / "wf#001.log"
    ctx = {"role": "worker", "result_file": str(rf)}
    pid = launch(
        _fake_provider(), Sandbox(False, []), ctx,
        prompt_text="do the work", cwd=repo, log_path=log,
    )
    assert pid > 0
    _wait_dead(pid)
    res = read_result(rf)
    assert res is not None and res.result == "committed"
    # the fake made a real commit on the current branch
    out = subprocess.run(["git", "-C", str(repo), "rev-list", "--count", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert int(out) == 2


def _shipped_sandbox_prefix() -> list[str]:
    """The real filesystem-confinement prefix from config.yaml — kept in sync so this test
    exercises what actually ships."""
    from orchestra.config import load_config
    cfg = load_config(REPO_ROOT / "config.yaml")
    return cfg.sandbox.argv_prefix


def test_launch_real_process_under_sandbox_prefix(tmp_path: Path):
    """Launch a REAL process under the shipped sandbox prefix — not just build argv. The
    #004 prefix bound no rootfs, so bwrap could not exec the agent at all (execvp: No such
    file or directory). This runs the fake worker for real inside the sandbox: it must exec,
    make a git commit, and write its result. Also asserts the sandbox confines writes — the
    rootfs is read-only, so a write outside the worktree fails."""
    import shutil

    import pytest

    from orchestra.result import read_result

    if shutil.which("bwrap") is None:
        pytest.skip("bwrap not installed")

    repo = tmp_path / "repo"
    _init_repo(repo)
    results = tmp_path / "results"
    results.mkdir()
    rf = results / "wf#001.json"
    log = tmp_path / "logs" / "wf#001.log"
    sb = Sandbox(enabled=True, argv_prefix=_shipped_sandbox_prefix())
    ctx = {
        "role": "worker", "result_file": str(rf),
        "workdir": str(repo), "results_dir": str(results),
    }
    pid = launch(
        _fake_provider(), sb, ctx,
        prompt_text="do the work", cwd=repo, log_path=log,
    )
    assert pid > 0
    _wait_dead(pid)
    res = read_result(rf)
    assert res is not None and res.result == "committed", (
        f"agent failed under sandbox; log:\n{log.read_text() if log.exists() else '(no log)'}"
    )
    # the commit really landed from inside the sandbox
    out = subprocess.run(["git", "-C", str(repo), "rev-list", "--count", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert int(out) == 2

    # confinement: the ro-bound rootfs rejects a write outside the writable worktree
    write_provider = ProviderConfig(
        argv=[sys.executable, "-c", "open('/etc/orchestra-nope', 'w')"], prompt="stdin"
    )
    denied = subprocess.run(
        build_argv(write_provider, sb, ctx), cwd=str(repo), capture_output=True, text=True,
    )
    assert denied.returncode != 0 and "Read-only file system" in denied.stderr


def test_launch_arg_mode_passes_prompt_as_last_argv(tmp_path):
    import time
    from orchestra.adapter import launch
    from orchestra.config import ProviderConfig, Sandbox
    from orchestra.selection import pid_alive

    out = tmp_path / "got.txt"
    # stub: write sys.argv[1] (the appended prompt) to a file
    script = f"import sys; open({str(out)!r}, 'w').write(sys.argv[1])"
    provider = ProviderConfig(argv=[sys.executable, "-c", script], prompt="arg")
    pid = launch(
        provider, Sandbox(False, []), {},
        prompt_text="HELLO-PROMPT", cwd=tmp_path, log_path=tmp_path / "l.log",
    )
    deadline = time.time() + 10
    while time.time() < deadline and pid_alive(pid):
        time.sleep(0.05)
    assert out.read_text() == "HELLO-PROMPT"
