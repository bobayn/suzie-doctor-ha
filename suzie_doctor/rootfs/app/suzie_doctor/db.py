from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = 2

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
        simulated: bool = False,
    ) -> str:
        now = utcnow()
        row = self.conn.execute(
            "SELECT id FROM incidents WHERE problem_key=? AND resolved_at IS NULL",
            (problem_key,),
        ).fetchone()
        if row:
            incident_id = str(row["id"])
            self.conn.execute(
                "UPDATE incidents SET severity=?, status='OPEN', title=?, detail=?, updated_at=? WHERE id=?",
                (severity, title, detail, now, incident_id),
            )
        else:
            incident_id = str(uuid4())
            self.conn.execute(
                """INSERT INTO incidents
                   (id,problem_key,incident_type,severity,status,title,detail,opened_at,updated_at,simulated)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (incident_id, problem_key, incident_type, severity, "OPEN", title, detail, now, now, int(simulated)),
            )
        self.conn.commit()
        return incident_id

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

    def cleanup(self, retention_days: int) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=max(30, retention_days))).isoformat()
        self.conn.execute("DELETE FROM health_samples WHERE sampled_at < ?", (cutoff,))
        self.conn.execute("DELETE FROM audit_runs WHERE started_at < ?", (cutoff,))
        self.conn.execute("DELETE FROM incidents WHERE resolved_at IS NOT NULL AND resolved_at < ?", (cutoff,))
        self.conn.commit()

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
