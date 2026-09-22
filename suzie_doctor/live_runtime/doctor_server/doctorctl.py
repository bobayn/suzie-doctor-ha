from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

DB = Path("/var/lib/suzie-doctor-server/server.sqlite3")


def iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(UTC)).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Suzie Doctor Server license admin")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list")

    setp = sub.add_parser("set")
    setp.add_argument("client_id")
    setp.add_argument("status", choices=["trial", "licensed", "free", "blocked"])
    setp.add_argument("--days", type=int, default=0)

    args = parser.parse_args()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    if args.command == "list":
        rows = conn.execute(
            """SELECT client_id,label,status,trial_expires,license_expires,
                      created_at,last_seen
               FROM clients ORDER BY created_at"""
        ).fetchall()
        print(json.dumps([dict(row) for row in rows], indent=2))
        return 0

    row = conn.execute(
        "SELECT client_id FROM clients WHERE client_id=?", (args.client_id,)
    ).fetchone()
    if not row:
        raise SystemExit("client not found")

    trial_expires = None
    license_expires = None
    days = max(0, int(args.days))
    if args.status == "trial":
        if days <= 0:
            days = 30
        trial_expires = iso(datetime.now(UTC) + timedelta(days=days))
    elif args.status == "licensed" and days > 0:
        license_expires = iso(datetime.now(UTC) + timedelta(days=days))

    conn.execute(
        """UPDATE clients
           SET status=?, trial_expires=?, license_expires=?
           WHERE client_id=?""",
        (args.status, trial_expires, license_expires, args.client_id),
    )
    conn.commit()
    print(json.dumps({
        "result": "UPDATED",
        "client_id": args.client_id,
        "status": args.status,
        "trial_expires": trial_expires,
        "license_expires": license_expires,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
