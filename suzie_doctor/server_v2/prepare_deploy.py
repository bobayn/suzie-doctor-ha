#!/usr/bin/env python3
"""Prepare/apply Doctor Server v2 additive DB migration safely.

Default mode is read-only/preflight + backup-copy migration test.
Use --apply only after reviewing the live server integration patch.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SERVER_ROOT = Path("/opt/suzie-doctor-server")
CONFIG = Path("/etc/suzie-doctor-server/config.json")
STATE_ROOT = Path("/var/lib/suzie-doctor-server")
DB = STATE_ROOT / "server.sqlite3"
SERVICE = "suzie-doctor-server.service"
HERE = Path(__file__).resolve().parent
MIGRATION = HERE / "migrations" / "001_house_wilson.sql"


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def integrity(path: Path) -> str:
    con = sqlite3.connect(path)
    try:
        return str(con.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--backup-root",
        default="/var/backups/suzie-doctor-server-v2",
    )
    args = ap.parse_args()

    for path in (SERVER_ROOT, CONFIG, STATE_ROOT, DB, MIGRATION):
        if not path.exists():
            raise SystemExit(f"missing required path: {path}")

    if integrity(DB) != "ok":
        raise SystemExit("live DB integrity_check failed; refusing migration")

    backup_dir = Path(args.backup_root) / stamp()
    backup_dir.mkdir(parents=True, exist_ok=False)

    shutil.copy2(DB, backup_dir / "server.sqlite3.before")
    shutil.copy2(CONFIG, backup_dir / "config.json.before")
    shutil.copytree(
        SERVER_ROOT,
        backup_dir / "server-code.before",
        symlinks=True,
    )

    test_db = backup_dir / "server.sqlite3.migration-test"
    shutil.copy2(DB, test_db)
    sql = MIGRATION.read_text(encoding="utf-8")
    con = sqlite3.connect(test_db)
    try:
        con.executescript(sql)
        result = str(con.execute("PRAGMA integrity_check").fetchone()[0])
        if result != "ok":
            raise SystemExit(
                "migration-copy integrity_check failed: " + result
            )
    finally:
        con.close()

    print(f"PREPARED backup={backup_dir}")
    print("PASS migration on backup copy")
    if not args.apply:
        print("LIVE DB NOT CHANGED")
        return 0

    # Apply only the additive schema.  Source-code integration must already be
    # installed/reviewed separately before the service is restarted.
    con = sqlite3.connect(DB)
    try:
        con.executescript(sql)
        result = str(con.execute("PRAGMA integrity_check").fetchone()[0])
        if result != "ok":
            raise SystemExit("live DB post-migration integrity failed: " + result)
    finally:
        con.close()

    subprocess.run(
        ["systemctl", "restart", SERVICE],
        check=True,
    )
    subprocess.run(
        ["systemctl", "is-active", "--quiet", SERVICE],
        check=True,
    )
    print("APPLIED DB migration and restarted Doctor Server only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
