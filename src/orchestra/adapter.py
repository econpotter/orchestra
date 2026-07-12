from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

from orchestra.config import ProviderConfig, Sandbox
from orchestra.prompting import render

ReadOnlyBind = tuple[Path, Path]


def build_argv(
    provider: ProviderConfig,
    sandbox: Sandbox,
    context: dict,
    *,
    read_only_binds: list[ReadOnlyBind] | None = None,
) -> list[str]:
    # When enabled, the sandbox is a FILESYSTEM confinement (see config.yaml): bwrap
    # ro-binds the rootfs and gives the agent a writable workdir/tmp/results_dir. Network is
    # shared — the agent must reach its model API to run at all — so `Network: false` is a
    # dispatch gate + advisory, not a run-time network jail (that would need an egress
    # allowlist, out of scope). Project ro-link seeds add nested read-only binds below.
    argv = [render(tok, context) for tok in provider.argv]
    binds = read_only_binds or []
    bind_argv = [part for src, dst in binds for part in ("--ro-bind", str(src), str(dst))]
    if sandbox.enabled:
        prefix = [render(tok, context) for tok in sandbox.argv_prefix]
        return prefix + bind_argv + argv
    if binds:
        return ["bwrap", "--bind", "/", "/", "--dev-bind", "/dev", "/dev"] + bind_argv + argv
    return argv


def launch(
    provider: ProviderConfig,
    sandbox: Sandbox,
    context: dict,
    *,
    prompt_text: str,
    cwd: Path,
    log_path: Path,
    completion_path: Path | None = None,
    stop_path: Path | None = None,
    read_only_binds: list[ReadOnlyBind] | None = None,
) -> int:
    argv = build_argv(provider, sandbox, context, read_only_binds=read_only_binds)
    if provider.prompt == "arg":
        argv = argv + [prompt_text]
    if completion_path is not None:
        argv = [
            sys.executable, "-m", "orchestra.worker_process",
            str(completion_path), str(stop_path or ""), json.dumps(argv),
        ]
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    # A worker log is also crash-classification evidence. Start every launch with a fresh
    # file so a prior attempt's provider error cannot reclassify a later plain crash.
    log = open(log_path, "w")
    stdin = subprocess.PIPE if provider.prompt == "stdin" else None
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdin=stdin,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
        )
        if provider.prompt == "stdin":
            assert proc.stdin is not None
            proc.stdin.write(prompt_text + "\n")
            proc.stdin.flush()
            proc.stdin.close()
    finally:
        log.close()
    # Reap the detached child so its pid is freed (not left a zombie),
    # keeping selection.pid_alive accurate for long-lived callers (tests,
    # or a future single-process driver). Inert under short-lived cron ticks.
    threading.Thread(target=proc.wait, daemon=True).start()
    return proc.pid
