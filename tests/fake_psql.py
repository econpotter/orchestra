#!/usr/bin/env python3
"""Deterministic stand-in for `psql`, driven by env-var file paths.

Recognizes the three statements orchestra's worktree_db issues (existence probe,
CREATE DATABASE, DROP DATABASE) by inspecting the SQL passed as the final argv
element. State (which databases "exist") lives in FAKE_PSQL_DBS; every invocation
is appended to FAKE_PSQL_LOG for assertions. Setting FAKE_PSQL_FAIL makes it exit
nonzero (to exercise the drop-failure warning path).
"""
import os
import re
import sys
from pathlib import Path


def _dbs_file() -> Path:
    return Path(os.environ["FAKE_PSQL_DBS"])


def _read_dbs() -> list[str]:
    f = _dbs_file()
    if not f.exists():
        return []
    return [ln for ln in f.read_text().splitlines() if ln.strip()]


def main() -> int:
    sql = sys.argv[-1]
    # Log the full argv + whether the password arrived via env (never argv).
    log = Path(os.environ["FAKE_PSQL_LOG"])
    with log.open("a") as fh:
        fh.write(sql + "\n")
    Path(os.environ["FAKE_PSQL_PGPASSWORD"]).write_text(os.environ.get("PGPASSWORD", "\0"))

    if os.environ.get("FAKE_PSQL_FAIL"):
        sys.stderr.write("fake psql: forced failure\n")
        return 1

    if "pg_database WHERE datname" in sql:
        m = re.search(r"datname = '([^']+)'", sql)
        name = m.group(1) if m else ""
        if name in _read_dbs():
            sys.stdout.write("1\n")
        return 0
    if sql.startswith("CREATE DATABASE"):
        m = re.search(r'CREATE DATABASE "([^"]+)"', sql)
        if m:
            dbs = _read_dbs()
            dbs.append(m.group(1))
            _dbs_file().write_text("\n".join(dbs) + "\n")
        return 0
    if sql.startswith("DROP DATABASE"):
        m = re.search(r'DROP DATABASE IF EXISTS "([^"]+)"', sql)
        if m:
            dbs = [d for d in _read_dbs() if d != m.group(1)]
            _dbs_file().write_text("\n".join(dbs) + ("\n" if dbs else ""))
        return 0
    sys.stderr.write(f"fake psql: unrecognized SQL: {sql}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
