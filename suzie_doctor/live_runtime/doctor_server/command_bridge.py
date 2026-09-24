from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


class CommandBridgeError(RuntimeError):
    pass


class ClientCommandBridge:
    """
    Exact-client, fail-closed command transport.

    Commands are queued by Doctor Server for a specific enrolled client_id.
    The HA Doctor App pulls commands using its existing Ed25519-authenticated
    outbound channel. A command is never reassigned to another client.

    Claimed commands are NOT automatically requeued after lease expiry:
    duplicate write execution is considered worse than a stuck command.
    """

    TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.conn = sqlite3.connect(
            self.path,
            timeout=10,
            check_same_thread=False,
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.initialize()

    def initialize(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS doctor_client_commands (
                command_id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                case_id INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL DEFAULT '{}',
                trusted_context_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                claimed_at TEXT,
                lease_expires TEXT,
                completed_at TEXT,
                result_json TEXT NOT NULL DEFAULT '{}',
                error TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_doctor_client_commands_queue
                ON doctor_client_commands(client_id,status,created_at);

            CREATE INDEX IF NOT EXISTS idx_doctor_client_commands_case
                ON doctor_client_commands(case_id,created_at);
            """
        )
        columns = {str(r["name"]) for r in self.conn.execute("PRAGMA table_info(doctor_client_commands)")}
        migrations = {
            "execution_state": "ALTER TABLE doctor_client_commands ADD COLUMN execution_state TEXT",
            "state_updated_at": "ALTER TABLE doctor_client_commands ADD COLUMN state_updated_at TEXT",
            "action_key": "ALTER TABLE doctor_client_commands ADD COLUMN action_key TEXT",
            "package_json": "ALTER TABLE doctor_client_commands ADD COLUMN package_json TEXT",
        }
        for name, ddl in migrations.items():
            if name not in columns:
                self.conn.execute(ddl)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_doctor_client_commands_action ON doctor_client_commands(client_id,action_key,created_at)")
        self.conn.commit()

    def _public(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["arguments"] = _loads(out.pop("arguments_json", "{}"), {})
        out["trusted_context"] = _loads(
            out.pop("trusted_context_json", "{}"), {}
        )
        out["result"] = _loads(out.pop("result_json", "{}"), {})
        out["signed_package"] = _loads(out.pop("package_json", "{}"), {})
        return out

    def enqueue(
        self,
        *,
        client_id: str,
        case_id: int,
        tool_name: str,
        arguments: dict[str, Any],
        trusted_context: dict[str, Any],
    ) -> dict[str, Any]:
        command_id = f"CMD-{uuid4()}"
        now = iso()
        action_key = None
        if str(tool_name) == "doctor.action.request":
            action = arguments.get("action") if isinstance(arguments.get("action"), dict) else {}
            target = arguments.get("exact_target") if isinstance(arguments.get("exact_target"), dict) else {}
            action_key = _json({"action": action, "exact_target": target})
        self.conn.execute(
            """
            INSERT INTO doctor_client_commands(
                command_id,client_id,case_id,tool_name,arguments_json,
                trusted_context_json,status,created_at,execution_state,state_updated_at,action_key
            ) VALUES(?,?,?,?,?,?,'QUEUED',?,'REQUESTED',?,?)
            """,
            (
                command_id,
                client_id,
                int(case_id),
                tool_name,
                _json(arguments),
                _json(trusted_context),
                now,
                now,
                action_key,
            ),
        )
        self.conn.commit()
        return self.get(command_id)

    def get(self, command_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM doctor_client_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
        if not row:
            raise CommandBridgeError("command not found")
        return self._public(row)

    def poll(
        self,
        *,
        client_id: str,
        lease_seconds: int = 120,
    ) -> dict[str, Any] | None:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            # Never run two commands concurrently inside one Doctor App.
            active = self.conn.execute(
                """
                SELECT command_id FROM doctor_client_commands
                WHERE client_id=? AND status='CLAIMED'
                LIMIT 1
                """,
                (client_id,),
            ).fetchone()
            if active:
                self.conn.commit()
                return None

            row = self.conn.execute(
                """
                SELECT * FROM doctor_client_commands
                WHERE client_id=? AND status='QUEUED'
                ORDER BY created_at, command_id
                LIMIT 1
                """,
                (client_id,),
            ).fetchone()
            if not row:
                self.conn.commit()
                return None

            now_dt = utcnow()
            now = iso(now_dt)
            lease_expires = iso(
                now_dt + timedelta(seconds=max(30, int(lease_seconds)))
            )
            cur = self.conn.execute(
                """
                UPDATE doctor_client_commands
                SET status='CLAIMED',claimed_at=?,lease_expires=?
                WHERE command_id=? AND client_id=? AND status='QUEUED'
                """,
                (
                    now,
                    lease_expires,
                    str(row["command_id"]),
                    client_id,
                ),
            )
            if cur.rowcount != 1:
                self.conn.rollback()
                return None
            self.conn.commit()
            return self.get(str(row["command_id"]))
        except Exception:
            self.conn.rollback()
            raise

    def store_signed_package(
        self, *, client_id: str, command_id: str, package: dict[str, Any]
    ) -> dict[str, Any]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row=self.conn.execute("SELECT status FROM doctor_client_commands WHERE command_id=? AND client_id=?",(command_id,client_id)).fetchone()
            if not row or str(row["status"])!="CLAIMED":
                raise CommandBridgeError("signed package requires claimed command")
            self.conn.execute("UPDATE doctor_client_commands SET package_json=?,state_updated_at=? WHERE command_id=? AND client_id=?",(_json(package),iso(),command_id,client_id))
            self.conn.commit(); return self.get(command_id)
        except Exception:
            self.conn.rollback(); raise

    def set_execution_state(
        self, *, client_id: str, command_id: str, state: str
    ) -> dict[str, Any]:
        allowed = {
            "REQUESTED","SIGNED","EXECUTING","CONNECTION_LOST_EXPECTED",
            "EXECUTED","VERIFY_PENDING","VERIFIED_PASS","VERIFIED_FAIL",
        }
        state = str(state or "").upper()
        if state not in allowed:
            raise CommandBridgeError("invalid execution state")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT status FROM doctor_client_commands WHERE command_id=? AND client_id=?",
                (command_id, client_id),
            ).fetchone()
            if not row:
                raise CommandBridgeError("command/client mismatch")
            if str(row["status"]) not in {"QUEUED","CLAIMED","COMPLETED","FAILED"}:
                raise CommandBridgeError("command state cannot be updated")
            self.conn.execute(
                "UPDATE doctor_client_commands SET execution_state=?,state_updated_at=? WHERE command_id=? AND client_id=?",
                (state, iso(), command_id, client_id),
            )
            self.conn.commit()
            return self.get(command_id)
        except Exception:
            self.conn.rollback()
            raise

    def recent_action(
        self, *, client_id: str, action_key: str, since: datetime
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            """SELECT * FROM doctor_client_commands
               WHERE client_id=? AND action_key=? AND created_at>=?
               ORDER BY created_at DESC LIMIT 1""",
            (client_id, action_key, iso(since)),
        ).fetchone()
        return self._public(row) if row else None

    def finish(
        self,
        *,
        client_id: str,
        command_id: str,
        result: dict[str, Any] | None,
        error: str | None,
    ) -> dict[str, Any]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                """
                SELECT * FROM doctor_client_commands
                WHERE command_id=? AND client_id=?
                """,
                (command_id, client_id),
            ).fetchone()
            if not row:
                raise CommandBridgeError("command/client mismatch")
            if str(row["status"]) != "CLAIMED":
                raise CommandBridgeError(
                    f"command is {row['status']}, not CLAIMED"
                )
            status = "FAILED" if error else "COMPLETED"
            now = iso()
            self.conn.execute(
                """
                UPDATE doctor_client_commands
                SET status=?,completed_at=?,result_json=?,error=?,
                    lease_expires=NULL
                WHERE command_id=? AND client_id=?
                """,
                (
                    status,
                    now,
                    _json(result or {}),
                    str(error or "")[:2000] or None,
                    command_id,
                    client_id,
                ),
            )
            self.conn.commit()
            return self.get(command_id)
        except Exception:
            self.conn.rollback()
            raise
