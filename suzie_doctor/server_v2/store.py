from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class DoctorV2Store:
    """Additive persistence for House/Wilson/session state.

    It deliberately does not replace the existing Case/claim/command tables.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")

    @contextmanager
    def tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def migrate_text(self, sql: str) -> None:
        self.conn.executescript(sql)

    def ensure_patient(self, client_id: str, label: str | None = None) -> str:
        row = self.conn.execute(
            "SELECT patient_id FROM doctor_patients WHERE client_id=?",
            (client_id,),
        ).fetchone()
        if row:
            return str(row["patient_id"])
        patient_id = "PAT-" + uuid.uuid4().hex[:16].upper()
        now = utcnow()
        with self.tx():
            self.conn.execute(
                """
                INSERT INTO doctor_patients(
                  patient_id, client_id, label, created_at, updated_at
                ) VALUES(?,?,?,?,?)
                """,
                (patient_id, client_id, label, now, now),
            )
        return patient_id

    def append_event(
        self,
        patient_id: str,
        event_type: str,
        source: str,
        payload: dict[str, Any],
        significance: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO doctor_patient_events(
              patient_id,event_type,source,significance,payload_json,created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                patient_id,
                event_type,
                source,
                significance,
                dumps(payload),
                utcnow(),
            ),
        )
        return int(cur.lastrowid)

    def put_card(self, patient_id: str, state: dict[str, Any]) -> int:
        now = utcnow()
        with self.tx():
            row = self.conn.execute(
                "SELECT version FROM doctor_patient_cards WHERE patient_id=?",
                (patient_id,),
            ).fetchone()
            if row:
                version = int(row["version"]) + 1
                self.conn.execute(
                    """
                    UPDATE doctor_patient_cards
                    SET version=?, state_json=?, updated_at=?
                    WHERE patient_id=?
                    """,
                    (version, dumps(state), now, patient_id),
                )
            else:
                version = 1
                self.conn.execute(
                    """
                    INSERT INTO doctor_patient_cards(
                      patient_id,version,state_json,updated_at
                    ) VALUES(?,?,?,?)
                    """,
                    (patient_id, version, dumps(state), now),
                )
        return version

    def enqueue_house(
        self,
        patient_id: str,
        card_version: int,
        trigger_event_id: int | None,
        *,
        priority: str = "NORMAL",
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO doctor_house_jobs(
              patient_id,card_version,trigger_event_id,status,priority,created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                patient_id,
                card_version,
                trigger_event_id,
                "WAITING",
                priority,
                utcnow(),
            ),
        )
        return int(cur.lastrowid)

    def claim_house(self) -> dict[str, Any] | None:
        with self.tx():
            row = self.conn.execute(
                """
                SELECT * FROM doctor_house_jobs
                WHERE status='WAITING'
                ORDER BY CASE priority
                  WHEN 'URGENT' THEN 0
                  WHEN 'HIGH' THEN 1
                  WHEN 'NORMAL' THEN 2
                  ELSE 3
                END, job_id
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            cur = self.conn.execute(
                """
                UPDATE doctor_house_jobs
                SET status='CLAIMED', claimed_at=?
                WHERE job_id=? AND status='WAITING'
                """,
                (utcnow(), int(row["job_id"])),
            )
            if cur.rowcount != 1:
                return None
            claimed = self.conn.execute(
                "SELECT * FROM doctor_house_jobs WHERE job_id=?",
                (int(row["job_id"]),),
            ).fetchone()
            return dict(claimed)

    def allocate_slot(
        self,
        role_type: str,
        assignment_ref: str,
        *,
        lease_expires_at: str | None = None,
    ) -> dict[str, Any] | None:
        with self.tx():
            row = self.conn.execute(
                """
                SELECT role_type,slot_no FROM doctor_role_slots
                WHERE role_type=? AND status='FREE'
                ORDER BY slot_no LIMIT 1
                """,
                (role_type,),
            ).fetchone()
            if not row:
                return None
            cur = self.conn.execute(
                """
                UPDATE doctor_role_slots
                SET status='BUSY',
                    assignment_ref=?,
                    lease_expires_at=?,
                    updated_at=?
                WHERE role_type=? AND slot_no=? AND status='FREE'
                """,
                (
                    assignment_ref,
                    lease_expires_at,
                    utcnow(),
                    role_type,
                    int(row["slot_no"]),
                ),
            )
            if cur.rowcount != 1:
                return None
            claimed = self.conn.execute(
                """
                SELECT * FROM doctor_role_slots
                WHERE role_type=? AND slot_no=?
                """,
                (role_type, int(row["slot_no"])),
            ).fetchone()
            return dict(claimed)

    def release_slot(
        self,
        role_type: str,
        slot_no: int,
        *,
        assignment_ref: str | None = None,
    ) -> bool:
        with self.tx():
            if assignment_ref is None:
                cur = self.conn.execute(
                    """
                    UPDATE doctor_role_slots
                    SET status='FREE',
                        assignment_ref=NULL,
                        lease_expires_at=NULL,
                        updated_at=?
                    WHERE role_type=? AND slot_no=?
                    """,
                    (utcnow(), role_type, slot_no),
                )
            else:
                cur = self.conn.execute(
                    """
                    UPDATE doctor_role_slots
                    SET status='FREE',
                        assignment_ref=NULL,
                        lease_expires_at=NULL,
                        updated_at=?
                    WHERE role_type=? AND slot_no=? AND assignment_ref=?
                    """,
                    (utcnow(), role_type, slot_no, assignment_ref),
                )
            return cur.rowcount == 1

    def open_dialog(
        self,
        *,
        role_type: str,
        job_type: str,
        job_id: str,
        project_id: str,
        generation: int,
    ) -> str:
        dialog_id = "DLG-" + uuid.uuid4().hex
        self.conn.execute(
            """
            INSERT INTO doctor_web_dialogs(
              dialog_id,role_type,logical_job_type,logical_job_id,
              project_id,generation,session_count,status,opened_at
            ) VALUES(?,?,?,?,?,?,0,'OPEN',?)
            """,
            (
                dialog_id,
                role_type,
                job_type,
                job_id,
                project_id,
                generation,
                utcnow(),
            ),
        )
        return dialog_id

    def start_session(
        self,
        dialog_id: str,
        *,
        watchdog_at: str,
    ) -> dict[str, Any]:
        with self.tx():
            row = self.conn.execute(
                """
                SELECT session_count,status
                FROM doctor_web_dialogs
                WHERE dialog_id=?
                """,
                (dialog_id,),
            ).fetchone()
            if not row or str(row["status"]) != "OPEN":
                raise ValueError("dialog_not_open")
            session_no = int(row["session_count"]) + 1
            if session_no > 10:
                raise ValueError("dialog_session_limit")
            session_id = "SES-" + uuid.uuid4().hex
            self.conn.execute(
                """
                INSERT INTO doctor_web_sessions(
                  session_id,dialog_id,session_no,started_at,watchdog_at
                ) VALUES(?,?,?,?,?)
                """,
                (
                    session_id,
                    dialog_id,
                    session_no,
                    utcnow(),
                    watchdog_at,
                ),
            )
            self.conn.execute(
                """
                UPDATE doctor_web_dialogs
                SET session_count=?
                WHERE dialog_id=?
                """,
                (session_no, dialog_id),
            )
            return {
                "session_id": session_id,
                "session_no": session_no,
            }

    def record_validation(
        self,
        *,
        episode_id: str,
        protocol_id: str,
        client_id: str,
        independent_group: str,
        outcome: str,
        verify: dict[str, Any],
        case_id: int | None = None,
        disease_id: str | None = None,
        counted: bool = False,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO doctor_protocol_validation_episodes(
              episode_id,protocol_id,disease_id,client_id,case_id,
              independent_group,outcome,verify_json,
              counted_confirmation,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                episode_id,
                protocol_id,
                disease_id,
                client_id,
                case_id,
                independent_group,
                outcome,
                dumps(verify),
                1 if counted else 0,
                utcnow(),
            ),
        )

    def confirmation_count(self, protocol_id: str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(DISTINCT independent_group) AS n
            FROM doctor_protocol_validation_episodes
            WHERE protocol_id=?
              AND counted_confirmation=1
              AND outcome='SUCCESS'
            """,
            (protocol_id,),
        ).fetchone()
        return int(row["n"])
