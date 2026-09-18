from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = 3

DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    problem_key TEXT NOT NULL,
    incident_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    disease_id TEXT,
    opened_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    recurrence_of TEXT,
    recurrence_count INTEGER NOT NULL DEFAULT 0,
    simulated INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_incident_open_problem
ON incidents(problem_key) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
CREATE TABLE IF NOT EXISTS incident_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(incident_id) REFERENCES incidents(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS observations (
    observation_key TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_runs (
    id TEXT PRIMARY KEY,
    audit_type TEXT NOT NULL,
    reason TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT,
    found_count INTEGER NOT NULL DEFAULT 0,
    resolved_count INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS health_samples (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    sampled_at TEXT NOT NULL,
    metric TEXT NOT NULL,
    value_num REAL,
    value_text TEXT,
    source TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_health_metric_time ON health_samples(metric, sampled_at);
CREATE TABLE IF NOT EXISTS protocol_runs (
    id TEXT PRIMARY KEY,
    incident_id TEXT,
    disease_id TEXT NOT NULL,
    protocol_id TEXT NOT NULL,
    protocol_version TEXT NOT NULL,
    protocol_pack_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    restart_level_used TEXT NOT NULL DEFAULT 'none',
    simulated INTEGER NOT NULL DEFAULT 0,
    versions_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS telemetry_queue (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def initialize(self) -> None:
        self.conn.executescript(DDL)

        # SQLite CREATE TABLE IF NOT EXISTS does not add columns to an existing
        # table. Keep migrations explicit and idempotent so App upgrades preserve
        # /data without requiring a database rebuild.
        incident_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(incidents)").fetchall()
        }
        if "disease_id" not in incident_columns:
            self.conn.execute("ALTER TABLE incidents ADD COLUMN disease_id TEXT")

        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
        self.conn.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return default if row is None else str(row["value"])

    def begin_audit(self, audit_type: str, reason: str | None = None) -> str:
        audit_id = str(uuid4())
        self.conn.execute(
            "INSERT INTO audit_runs(id,audit_type,reason,started_at) VALUES(?,?,?,?)",
            (audit_id, audit_type, reason, utcnow()),
        )
        self.conn.commit()
        return audit_id

    def finish_audit(self, audit_id: str, result: str, found_count: int, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE audit_runs SET finished_at=?, result=?, found_count=?, payload_json=? WHERE id=?",
            (utcnow(), result, found_count, json.dumps(payload, ensure_ascii=False), audit_id),
        )
        self.conn.commit()

    def upsert_incident(
        self,
        *,
        problem_key: str,
        incident_type: str,
        severity: str,
        title: str,
        detail: str,
        disease_id: str | None = None,
        simulated: bool = False,
    ) -> str:
        now = utcnow()
        row = self.conn.execute(
            "SELECT id, simulated FROM incidents WHERE problem_key=? AND resolved_at IS NULL",
            (problem_key,),
        ).fetchone()
        if row:
            incident_id = str(row["id"])
            # A real occurrence must never remain hidden just because an earlier
            # developer simulation used the same problem key.
            combined_simulated = int(bool(row["simulated"]) and simulated)
            self.conn.execute(
                """UPDATE incidents
                   SET incident_type=?, severity=?, status='OPEN', title=?, detail=?,
                       disease_id=COALESCE(?, disease_id), updated_at=?, simulated=?
                   WHERE id=?""",
                (
                    incident_type,
                    severity,
                    title,
                    detail,
                    disease_id,
                    now,
                    combined_simulated,
                    incident_id,
                ),
            )
            self.conn.commit()
            return incident_id

        previous = self.conn.execute(
            """SELECT id,resolved_at,recurrence_count,simulated
               FROM incidents
               WHERE problem_key=? AND status='RESOLVED'
               ORDER BY resolved_at DESC
               LIMIT 1""",
            (problem_key,),
        ).fetchone()

        recurrence_of: str | None = None
        recurrence_count = 0

        if previous and previous["resolved_at"]:
            daily_boundary = self.conn.execute(
                """SELECT 1 FROM audit_runs
                   WHERE audit_type='daily'
                     AND finished_at IS NOT NULL
                     AND finished_at > ?
                   LIMIT 1""",
                (str(previous["resolved_at"]),),
            ).fetchone()

            if daily_boundary is None:
                # The symptom returned before the next daily boundary: this is
                # still the same incident episode, so reopen the same row.
                incident_id = str(previous["id"])
                combined_simulated = int(bool(previous["simulated"]) and simulated)
                self.conn.execute(
                    """UPDATE incidents
                       SET incident_type=?, severity=?, status='OPEN', title=?, detail=?,
                           disease_id=COALESCE(?, disease_id), updated_at=?,
                           resolved_at=NULL, simulated=?
                       WHERE id=?""",
                    (
                        incident_type,
                        severity,
                        title,
                        detail,
                        disease_id,
                        now,
                        combined_simulated,
                        incident_id,
                    ),
                )
                self.conn.execute(
                    """INSERT INTO incident_events
                       (incident_id,occurred_at,event_type,payload_json)
                       VALUES(?,?,?,?)""",
                    (
                        incident_id,
                        now,
                        "REOPENED_SAME_EPISODE",
                        json.dumps(
                            {"note": "Problem returned before next daily audit boundary."},
                            ensure_ascii=False,
                        ),
                    ),
                )
                self.conn.commit()
                return incident_id

            recurrence_of = str(previous["id"])
            recurrence_count = int(previous["recurrence_count"]) + 1

        incident_id = str(uuid4())
        self.conn.execute(
            """INSERT INTO incidents
               (id,problem_key,incident_type,severity,status,title,detail,disease_id,
                opened_at,updated_at,recurrence_of,recurrence_count,simulated)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                incident_id,
                problem_key,
                incident_type,
                severity,
                "OPEN",
                title,
                detail,
                disease_id,
                now,
                now,
                recurrence_of,
                recurrence_count,
                int(simulated),
            ),
        )
        if recurrence_of is not None:
            self.conn.execute(
                """INSERT INTO incident_events
                   (incident_id,occurred_at,event_type,payload_json)
                   VALUES(?,?,?,?)""",
                (
                    incident_id,
                    now,
                    "RECURRENCE_OPENED",
                    json.dumps(
                        {
                            "recurrence_of": recurrence_of,
                            "recurrence_count": recurrence_count,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        self.conn.commit()
        return incident_id

    def incident_by_id(self, incident_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        return None if row is None else dict(row)

    def add_incident_event(self, incident_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            "INSERT INTO incident_events(incident_id,occurred_at,event_type,payload_json) VALUES(?,?,?,?)",
            (
                incident_id,
                utcnow(),
                event_type,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def incident_event_count(self, incident_id: str, event_type: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM incident_events WHERE incident_id=? AND event_type=?",
            (incident_id, event_type),
        ).fetchone()
        return 0 if row is None else int(row["c"])

    def resolve_problem(self, problem_key: str, note: str = "Symptoms absent on repeat diagnostic") -> bool:
        row = self.conn.execute(
            "SELECT id FROM incidents WHERE problem_key=? AND resolved_at IS NULL", (problem_key,)
        ).fetchone()
        if not row:
            return False
        now = utcnow()
        self.conn.execute(
            "UPDATE incidents SET status='RESOLVED', resolved_at=?, updated_at=? WHERE id=?",
            (now, now, row["id"]),
        )
        self.conn.execute(
            "INSERT INTO incident_events(incident_id,occurred_at,event_type,payload_json) VALUES(?,?,?,?)",
            (row["id"], now, "RESOLVED", json.dumps({"note": note}, ensure_ascii=False)),
        )
        self.conn.commit()
        return True

    def discard_problem(self, problem_key: str, note: str = "Reclassified as non-incident") -> bool:
        row = self.conn.execute(
            "SELECT id FROM incidents WHERE problem_key=? AND resolved_at IS NULL", (problem_key,)
        ).fetchone()
        if not row:
            return False
        now = utcnow()
        self.conn.execute(
            "UPDATE incidents SET status='DISCARDED', resolved_at=?, updated_at=?, detail=? WHERE id=?",
            (now, now, note, row["id"]),
        )
        self.conn.execute(
            "INSERT INTO incident_events(incident_id,occurred_at,event_type,payload_json) VALUES(?,?,?,?)",
            (row["id"], now, "DISCARDED", json.dumps({"note": note}, ensure_ascii=False)),
        )
        self.conn.commit()
        return True

    def add_observation(self, key: str, category: str, payload: dict[str, Any]) -> int:
        now = utcnow()
        row = self.conn.execute("SELECT count FROM observations WHERE observation_key=?", (key,)).fetchone()
        if row:
            count = int(row["count"]) + 1
            self.conn.execute(
                "UPDATE observations SET last_seen=?, count=?, payload_json=? WHERE observation_key=?",
                (now, count, json.dumps(payload, ensure_ascii=False), key),
            )
        else:
            count = 1
            self.conn.execute(
                "INSERT INTO observations(observation_key,category,first_seen,last_seen,count,payload_json) VALUES(?,?,?,?,?,?)",
                (key, category, now, now, count, json.dumps(payload, ensure_ascii=False)),
            )
        self.conn.commit()
        return count

    def clear_observation(self, key: str) -> None:
        self.conn.execute("DELETE FROM observations WHERE observation_key=?", (key,))
        self.conn.commit()

    def store_health_samples(self, rows: list[tuple[str, str, float | None, str | None, str, int]]) -> None:
        self.conn.executemany(
            "INSERT INTO health_samples(sampled_at,metric,value_num,value_text,source,simulated) VALUES(?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()

    def get_or_create_meta_uuid(self, key: str) -> str:
        existing = self.get_meta(key)
        if existing:
            return existing
        value = str(uuid4())
        self.set_meta(key, value)
        return value

    def begin_protocol_run(
        self,
        *,
        incident_id: str | None,
        disease_id: str,
        protocol_id: str,
        protocol_version: str,
        protocol_pack_version: str,
        simulated: bool,
        versions: dict[str, Any],
    ) -> str:
        run_id = str(uuid4())
        self.conn.execute(
            """INSERT INTO protocol_runs
               (id,incident_id,disease_id,protocol_id,protocol_version,
                protocol_pack_version,started_at,simulated,versions_json)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                incident_id,
                disease_id,
                protocol_id,
                protocol_version,
                protocol_pack_version,
                utcnow(),
                int(simulated),
                json.dumps(versions, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return run_id

    def finish_protocol_run(
        self,
        run_id: str,
        *,
        result: str,
        attempt_count: int,
        restart_level_used: str,
        versions: dict[str, Any],
    ) -> None:
        self.conn.execute(
            """UPDATE protocol_runs
               SET finished_at=?, result=?, attempt_count=?,
                   restart_level_used=?, versions_json=?
               WHERE id=?""",
            (
                utcnow(),
                result,
                max(1, int(attempt_count)),
                restart_level_used,
                json.dumps(versions, ensure_ascii=False),
                run_id,
            ),
        )
        self.conn.commit()

    def enqueue_telemetry(self, payload: dict[str, Any]) -> int:
        cur = self.conn.execute(
            "INSERT INTO telemetry_queue(created_at,payload_json) VALUES(?,?)",
            (utcnow(), json.dumps(payload, ensure_ascii=False)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def protocol_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM protocol_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["versions"] = json.loads(item.pop("versions_json"))
            except Exception:
                item["versions"] = {}
            result.append(item)
        return result

    def telemetry_queue(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT seq,created_at,payload_json,sent_at
               FROM telemetry_queue
               ORDER BY seq DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.pop("payload_json"))
            except Exception:
                item["payload"] = {}
            result.append(item)
        return result

    def cleanup(self, retention_days: int) -> dict[str, int]:
        cutoff = (datetime.now(UTC) - timedelta(days=max(30, retention_days))).isoformat()
        statements = {
            "health_samples": (
                "DELETE FROM health_samples WHERE sampled_at < ?",
                (cutoff,),
            ),
            "audit_runs": (
                "DELETE FROM audit_runs WHERE started_at < ?",
                (cutoff,),
            ),
            "resolved_incidents": (
                "DELETE FROM incidents "
                "WHERE resolved_at IS NOT NULL AND resolved_at < ?",
                (cutoff,),
            ),
            "observations": (
                "DELETE FROM observations WHERE last_seen < ?",
                (cutoff,),
            ),
            "finished_protocol_runs": (
                "DELETE FROM protocol_runs "
                "WHERE finished_at IS NOT NULL AND finished_at < ?",
                (cutoff,),
            ),
            "telemetry_queue": (
                "DELETE FROM telemetry_queue WHERE created_at < ?",
                (cutoff,),
            ),
        }
        removed: dict[str, int] = {}
        for key, (sql, params) in statements.items():
            cursor = self.conn.execute(sql, params)
            removed[key] = max(0, int(cursor.rowcount or 0))
        self.conn.commit()
        return removed

    def dashboard(self) -> dict[str, Any]:
        open_count = int(self.conn.execute(
            "SELECT COUNT(*) c FROM incidents WHERE resolved_at IS NULL AND simulated=0"
        ).fetchone()["c"])
        since24 = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
        fixed24 = int(self.conn.execute(
            "SELECT COUNT(*) c FROM incidents WHERE resolved_at >= ? AND status != 'DISCARDED' AND simulated=0",
            (since24,),
        ).fetchone()["c"])
        found24 = int(self.conn.execute(
            "SELECT COUNT(*) c FROM incidents WHERE opened_at >= ? AND status != 'DISCARDED' AND simulated=0",
            (since24,),
        ).fetchone()["c"])
        last_audit = self.conn.execute(
            "SELECT audit_type,started_at,finished_at,result,found_count FROM audit_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return {
            "open_incidents": open_count,
            "found_24h": found24,
            "fixed_24h": fixed24,
            "last_audit": None if last_audit is None else dict(last_audit),
        }

    def incidents(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM incidents WHERE status != 'DISCARDED' AND simulated=0 ORDER BY opened_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def audits(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM audit_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            try:
                d["payload"] = json.loads(d.pop("payload_json"))
            except Exception:
                d["payload"] = {}
            result.append(d)
        return result

    def has_open_problem(self, problem_key: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM incidents WHERE problem_key=? AND resolved_at IS NULL", (problem_key,)
        ).fetchone() is not None

    def open_problem_keys(self, prefixes: tuple[str, ...] = ()) -> set[str]:
        rows = self.conn.execute("SELECT problem_key FROM incidents WHERE resolved_at IS NULL").fetchall()
        keys = {str(r["problem_key"]) for r in rows}
        if not prefixes:
            return keys
        return {k for k in keys if k.startswith(prefixes)}
