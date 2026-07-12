import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from orchestra.worktree_db import (
    clone_name,
    create_worktree_db,
    drop_worktree_db,
)

FAKE_PSQL = Path(__file__).resolve().parent / "fake_psql.py"

ENV_TEMPLATE = (
    "# project config\n"
    "OTHER=1\n"
    "DB_HOST=localhost\n"
    "DB_PORT=5432\n"
    "DB_USER=proj_user\n"
    "DB_PASSWORD=s3cret\n"
    "DB_NAME={db_name}\n"
    "TRAILING=x\n"
)


def _fake_psql_env(tmp_path: Path, monkeypatch, *, existing=(), fail=False) -> dict:
    """Put a fake `psql` on PATH and wire its state files. Returns paths for asserts."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "psql"
    script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_PSQL}" "$@"\n')
    script.chmod(0o755)

    dbs = tmp_path / "dbs.txt"
    dbs.write_text("\n".join(existing) + ("\n" if existing else ""))
    log = tmp_path / "log.txt"
    pw = tmp_path / "pgpassword.txt"

    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_PSQL_DBS", str(dbs))
    monkeypatch.setenv("FAKE_PSQL_LOG", str(log))
    monkeypatch.setenv("FAKE_PSQL_PGPASSWORD", str(pw))
    if fail:
        monkeypatch.setenv("FAKE_PSQL_FAIL", "1")
    return {"dbs": dbs, "log": log, "pw": pw}


def _write_envs(tmp_path: Path, db_name="proj") -> tuple[Path, Path]:
    repo_env = tmp_path / "repo.env"
    wt_env = tmp_path / "wt.env"
    repo_env.write_text(ENV_TEMPLATE.format(db_name=db_name))
    wt_env.write_text(ENV_TEMPLATE.format(db_name=db_name))  # seed copies the project .env
    return repo_env, wt_env


def test_clone_name_zero_padded():
    assert clone_name("demo", 4) == "demo_wt_004"
    assert clone_name("demo", 123) == "demo_wt_123"


def test_create_makes_db_and_rewrites_env(tmp_path, monkeypatch):
    state = _fake_psql_env(tmp_path, monkeypatch)
    repo_env, wt_env = _write_envs(tmp_path)

    create_worktree_db(repo_env, wt_env, 4)

    log = state["log"].read_text()
    assert 'CREATE DATABASE "proj_wt_004" OWNER "proj_user"' in log
    assert "proj_wt_004" in state["dbs"].read_text()
    # worktree .env DB_NAME rewritten (not appended), exactly once
    wt_lines = wt_env.read_text().splitlines()
    assert wt_lines.count("DB_NAME=proj_wt_004") == 1
    assert not any(line == "DB_NAME=proj" for line in wt_lines)
    # other lines untouched, order preserved
    assert wt_lines[0] == "# project config"
    assert wt_lines[-1] == "TRAILING=x"
    # project .env is the canonical source — never rewritten
    assert "DB_NAME=proj\n" in repo_env.read_text()
    # password went through the environment, never argv
    assert state["pw"].read_text() == "s3cret"


def test_create_is_idempotent_when_clone_exists(tmp_path, monkeypatch):
    state = _fake_psql_env(tmp_path, monkeypatch, existing=["proj_wt_004"])
    repo_env, wt_env = _write_envs(tmp_path)

    create_worktree_db(repo_env, wt_env, 4)

    log = state["log"].read_text()
    assert "CREATE DATABASE" not in log  # reused as-is
    assert "DB_NAME=proj_wt_004" in wt_env.read_text()  # still points at the clone


def test_create_missing_component_fails_loud(tmp_path, monkeypatch):
    _fake_psql_env(tmp_path, monkeypatch)
    repo_env = tmp_path / "repo.env"
    wt_env = tmp_path / "wt.env"
    repo_env.write_text("DB_HOST=localhost\nDB_PORT=5432\nDB_USER=u\nDB_PASSWORD=p\n")  # no DB_NAME
    wt_env.write_text(repo_env.read_text())
    with pytest.raises(ValueError, match="DB_NAME"):
        create_worktree_db(repo_env, wt_env, 4)


def test_create_invalid_identifier_fails_loud(tmp_path, monkeypatch):
    _fake_psql_env(tmp_path, monkeypatch)
    repo_env, wt_env = _write_envs(tmp_path, db_name="Bad-Name")
    with pytest.raises(ValueError, match="valid Postgres identifier"):
        create_worktree_db(repo_env, wt_env, 4)


def test_create_empty_password_allowed(tmp_path, monkeypatch):
    state = _fake_psql_env(tmp_path, monkeypatch)
    repo_env = tmp_path / "repo.env"
    wt_env = tmp_path / "wt.env"
    body = (
        "DB_HOST=localhost\nDB_PORT=5432\nDB_USER=u\nDB_PASSWORD=\nDB_NAME=proj\n"
    )
    repo_env.write_text(body)
    wt_env.write_text(body)
    create_worktree_db(repo_env, wt_env, 1)  # trust auth: empty password is valid
    assert 'CREATE DATABASE "proj_wt_001"' in state["log"].read_text()


def test_drop_issues_force_drop(tmp_path, monkeypatch):
    state = _fake_psql_env(tmp_path, monkeypatch, existing=["proj_wt_004"])
    repo_env, _ = _write_envs(tmp_path)

    drop_worktree_db(repo_env, 4)

    assert 'DROP DATABASE IF EXISTS "proj_wt_004" WITH (FORCE)' in state["log"].read_text()
    assert "proj_wt_004" not in state["dbs"].read_text()


def test_drop_failure_warns_and_returns(tmp_path, monkeypatch, capsys):
    _fake_psql_env(tmp_path, monkeypatch, existing=["proj_wt_004"], fail=True)
    repo_env, _ = _write_envs(tmp_path)

    drop_worktree_db(repo_env, 4)  # must NOT raise

    err = capsys.readouterr().err
    assert "Worktree-DB drop failed" in err
    assert "manually" in err  # manual-cleanup pointer


def test_drop_missing_component_warns_not_raises(tmp_path, monkeypatch, capsys):
    _fake_psql_env(tmp_path, monkeypatch)
    repo_env = tmp_path / "repo.env"
    repo_env.write_text("DB_HOST=localhost\n")  # incomplete
    drop_worktree_db(repo_env, 4)  # must NOT raise
    assert "Worktree-DB drop failed" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Live test against a throwaway Postgres cluster (real psql CREATE/DROP).
# Skips cleanly when the Postgres server binaries are unavailable.
# ---------------------------------------------------------------------------

def _pg_bindir() -> Path | None:
    if shutil.which("initdb") and shutil.which("pg_ctl"):
        return Path(shutil.which("initdb")).parent
    for cand in sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True):
        if (cand / "initdb").exists() and (cand / "pg_ctl").exists():
            return cand
    return None


@pytest.mark.skipif(_pg_bindir() is None, reason="Postgres server binaries not installed")
def test_live_create_and_drop(tmp_path):
    bindir = _pg_bindir()
    assert bindir is not None
    datadir = tmp_path / "pgdata"
    sockdir = tmp_path / "sock"
    sockdir.mkdir()
    user = os.environ.get("USER") or "postgres"

    # Bring up a throwaway cluster. In a sandbox the binaries can be present but unable
    # to actually initdb/start (blocked syscalls, no fsync, restricted exec) — bound each
    # step and skip cleanly rather than hang the suite. `-w start` is capped by
    # PGCTLTIMEOUT so pg_ctl can't wait forever on a server that never binds its socket.
    started = False
    try:
        subprocess.run(
            [str(bindir / "initdb"), "-D", str(datadir), "-U", user, "-A", "trust"],
            check=True, capture_output=True, text=True, timeout=60,
        )
        # Unix-socket only (listen_addresses= disables TCP, avoiding port collisions with
        # any real local server). Empty value, no quotes — the -o string is split on spaces.
        subprocess.run(
            [str(bindir / "pg_ctl"), "-D", str(datadir), "-o",
             f"-p 55432 -k {sockdir} -c listen_addresses=", "-w", "start"],
            check=True, capture_output=True, text=True, timeout=60,
            env={**os.environ, "PGCTLTIMEOUT": "30"},
        )
        started = True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        if started:  # server is up but a later step failed — tear it down before skipping
            subprocess.run(
                [str(bindir / "pg_ctl"), "-D", str(datadir), "-m", "immediate", "-w", "stop"],
                capture_output=True, text=True, timeout=30,
            )
        pytest.skip(f"local Postgres cluster could not be started here: {exc}")
    try:
        # give the socket a beat in case -w returned just before it bound
        for _ in range(20):
            if list(sockdir.glob(".s.PGSQL.*")):
                break
            time.sleep(0.1)
        env = dict(os.environ)
        env["PGHOST"] = str(sockdir)
        env["PGPORT"] = "55432"
        subprocess.run([str(bindir / "createdb"), "-U", user, "proj"],
                       check=True, capture_output=True, text=True, env=env, timeout=30)

        body = (
            f"DB_HOST={sockdir}\nDB_PORT=55432\nDB_USER={user}\n"
            f"DB_PASSWORD=\nDB_NAME=proj\n"
        )
        repo_env = tmp_path / "repo.env"
        wt_env = tmp_path / "wt.env"
        repo_env.write_text(body)
        wt_env.write_text(body)

        def _db_exists(name: str) -> bool:
            out = subprocess.run(
                [str(bindir / "psql"), "-U", user, "-d", "proj", "-tAc",
                 f"SELECT 1 FROM pg_database WHERE datname = '{name}'"],
                capture_output=True, text=True, env=env, timeout=30,
            ).stdout.strip()
            return out == "1"

        create_worktree_db(repo_env, wt_env, 7)
        assert _db_exists("proj_wt_007")
        assert "DB_NAME=proj_wt_007" in wt_env.read_text()

        # idempotent: a second create does not error on the existing clone
        create_worktree_db(repo_env, wt_env, 7)
        assert _db_exists("proj_wt_007")

        drop_worktree_db(repo_env, 7)
        assert not _db_exists("proj_wt_007")
    finally:
        subprocess.run(
            [str(bindir / "pg_ctl"), "-D", str(datadir), "-m", "immediate", "-w", "stop"],
            capture_output=True, text=True, timeout=30,
        )
