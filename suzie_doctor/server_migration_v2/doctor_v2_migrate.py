from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from doctor_v2_store import DoctorV2Store

SERVER_ROOT = Path("/opt/suzie-doctor-server")
CONFIG_PATH = Path("/etc/suzie-doctor-server/config.json")
STATE_ROOT = Path("/var/lib/suzie-doctor-server")
DB_PATH = STATE_ROOT / "server.sqlite3"
SERVICE = "suzie-doctor-server.service"


def ts() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def inspect() -> dict:
    out = {
        "server_root": {"path": str(SERVER_ROOT), "exists": SERVER_ROOT.exists()},
        "config": {"path": str(CONFIG_PATH), "exists": CONFIG_PATH.exists()},
        "state_root": {"path": str(STATE_ROOT), "exists": STATE_ROOT.exists()},
        "db": {"path": str(DB_PATH), "exists": DB_PATH.exists()},
        "python_files": [],
        "db_tables": [],
        "config_keys": [],
    }
    if SERVER_ROOT.exists():
        out["python_files"] = [
            str(p.relative_to(SERVER_ROOT)) for p in sorted(SERVER_ROOT.rglob("*.py"))
        ]
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(cfg, dict):
                out["config_keys"] = sorted(str(k) for k in cfg.keys())
        except Exception as exc:
            out["config_error"] = f"{type(exc).__name__}: {exc}"
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            try:
                out["db_tables"] = [
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    )
                ]
                out["db_integrity"] = conn.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                conn.close()
        except Exception as exc:
            out["db_error"] = f"{type(exc).__name__}: {exc}"
    return out


def backup_db() -> Path:
    if not DB_PATH.exists():
        raise RuntimeError(f"DB not found: {DB_PATH}")
    backup_dir = STATE_ROOT / "migration-backups"
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest = backup_dir / f"server.sqlite3.before-v2-{ts()}.bak"
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
        check = dst.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"backup quick_check={check}")
    finally:
        dst.close()
        src.close()
    return dest


def apply_db(schema_path: Path) -> dict:
    pre = inspect()
    if not DB_PATH.exists():
        raise RuntimeError("live Doctor Server DB not found")
    if pre.get("db_integrity") != "ok":
        raise RuntimeError(f"live DB quick_check failed: {pre.get('db_integrity')}")
    backup = backup_db()
    store = DoctorV2Store(DB_PATH, schema_path)
    try:
        store.install_schema()
        quick = store.conn.execute("PRAGMA quick_check").fetchone()[0]
        slots = {
            r["role"]: r["n"]
            for r in store.conn.execute(
                "SELECT role,COUNT(*) n FROM doctor_v2_role_slots GROUP BY role"
            )
        }
        targets = {
            r["role"]: r["project_id"]
            for r in store.conn.execute(
                "SELECT role,project_id FROM doctor_v2_role_targets"
            )
        }
        meta = {
            r["key"]: r["value"]
            for r in store.conn.execute("SELECT key,value FROM doctor_v2_meta")
        }
        if quick != "ok":
            raise RuntimeError(f"post-migration quick_check={quick}")
        if slots != {"FIELD_SUZIE": 4, "HOUSE": 1, "WILSON": 1}:
            raise RuntimeError(f"role slot mismatch: {slots}")
        if (
            meta.get("session_max_minutes") != "10"
            or meta.get("dialog_max_sessions") != "10"
        ):
            raise RuntimeError(f"10/10 metadata mismatch: {meta}")
        return {
            "result": "DB_MIGRATION_PASS",
            "backup": str(backup),
            "quick_check": quick,
            "role_slots": slots,
            "role_targets": targets,
            "meta": meta,
        }
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Suzie Doctor Server architecture-v2 safe migration helper"
    )
    parser.add_argument(
        "--apply-db",
        action="store_true",
        help="backup live SQLite DB and add doctor_v2_* state tables",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).with_name("doctor_v2_schema.sql"),
    )
    args = parser.parse_args()
    if args.apply_db:
        print(json.dumps(apply_db(args.schema), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(inspect(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
