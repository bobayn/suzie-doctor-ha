from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROLE_SLOTS = (
    ("field-1", "FIELD_SUZIE", 0),
    ("field-2", "FIELD_SUZIE", 0),
    ("field-3", "FIELD_SUZIE", 0),
    ("field-4", "FIELD_SUZIE", 0),
    ("house-1", "HOUSE", 1),
    ("wilson-1", "WILSON", 1),
)

HOUSE_PROJECT_ID = "g-p-6ab184e457cc819182e5230e09fdfc18"
HOUSE_PROJECT_URL = "https://chatgpt.com/g/g-p-6ab184e457cc819182e5230e09fdfc18-doktor-khaus"
WILSON_PROJECT_ID = "g-p-6ab1850655508191afad64d3cc104b3a"
WILSON_PROJECT_URL = "https://chatgpt.com/g/g-p-6ab1850655508191afad64d3cc104b3a-doktor-vilson"


class DoctorV2StateError(RuntimeError):
    pass


@dataclass(frozen=True)
class DialogAdvance:
    dialog_id: str
    ordinal: int
    rotate_after: bool


class DoctorV2Store:
    def __init__(self, db_path: str | Path, schema_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.schema_path = Path(schema_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")

    def close(self) -> None:
        self.conn.close()

    def install_schema(self) -> None:
        self.conn.executescript(self.schema_path.read_text(encoding="utf-8"))
        field_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(doctor_v2_field_queue)")
        }
        field_migrations = {
            "house_directive": "ALTER TABLE doctor_v2_field_queue ADD COLUMN house_directive TEXT",
            "experimental_protocol_id": "ALTER TABLE doctor_v2_field_queue ADD COLUMN experimental_protocol_id TEXT",
            "validation_stage": "ALTER TABLE doctor_v2_field_queue ADD COLUMN validation_stage TEXT",
        }
        for name, ddl in field_migrations.items():
            if name not in field_columns:
                self.conn.execute(ddl)
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO doctor_v2_role_slots(slot_id, role, reserved)
                VALUES(?,?,?)
                ON CONFLICT(slot_id) DO UPDATE SET
                    role=excluded.role,
                    reserved=excluded.reserved
                """,
                ROLE_SLOTS,
            )
            self.conn.execute(
                """
                INSERT INTO doctor_v2_role_targets(role, project_id, project_url)
                VALUES('HOUSE', ?, ?)
                ON CONFLICT(role) DO UPDATE SET
                    project_id=excluded.project_id,
                    project_url=excluded.project_url,
                    enabled=1,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (HOUSE_PROJECT_ID, HOUSE_PROJECT_URL),
            )
            self.conn.execute(
                """
                INSERT INTO doctor_v2_role_targets(role, project_id, project_url)
                VALUES('WILSON', ?, ?)
                ON CONFLICT(role) DO UPDATE SET
                    project_id=excluded.project_id,
                    project_url=excluded.project_url,
                    enabled=1,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (WILSON_PROJECT_ID, WILSON_PROJECT_URL),
            )
            meta = {
                "architecture_version": "2",
                "session_max_minutes": "10",
                "dialog_max_sessions": "10",
                "field_suzie_max": "4",
                "house_reserved": "1",
                "wilson_reserved": "1",
                "new_protocol_internal_confirmations_required": "3",
            }
            self.conn.executemany(
                """
                INSERT INTO doctor_v2_meta(key,value)
                VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=CURRENT_TIMESTAMP
                """,
                list(meta.items()),
            )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def ensure_patient(self, patient_id: str, state: dict[str, Any] | None = None) -> int:
        patient_id = str(patient_id).strip()
        if not patient_id:
            raise DoctorV2StateError("patient_id required")
        payload = self._json(state or {})
        with self.conn:
            row = self.conn.execute(
                "SELECT card_version FROM doctor_v2_patient_cards WHERE patient_id=?",
                (patient_id,),
            ).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO doctor_v2_patient_cards(patient_id,state_json) VALUES(?,?)",
                    (patient_id, payload),
                )
                return 1
            version = int(row["card_version"]) + 1
            self.conn.execute(
                """
                UPDATE doctor_v2_patient_cards
                SET card_version=?, state_json=?, updated_at=CURRENT_TIMESTAMP
                WHERE patient_id=?
                """,
                (version, payload, patient_id),
            )
            return version

    def append_event(
        self,
        *,
        patient_id: str,
        event_type: str,
        source: str,
        payload: dict[str, Any],
        severity: str | None = None,
        fingerprint: str | None = None,
        create_house_job: bool = False,
        priority: int = 50,
    ) -> int:
        if event_type not in {"EVENT", "OBSERVATION", "INCIDENT", "CASE_EVENT"}:
            raise DoctorV2StateError("invalid event_type")
        with self.conn:
            row = self.conn.execute(
                "SELECT card_version FROM doctor_v2_patient_cards WHERE patient_id=?",
                (patient_id,),
            ).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO doctor_v2_patient_cards(patient_id) VALUES(?)", (patient_id,)
                )
                card_version = 1
            else:
                card_version = int(row["card_version"])
            cur = self.conn.execute(
                """
                INSERT INTO doctor_v2_patient_events(
                    patient_id,event_type,source,severity,fingerprint,payload_json
                ) VALUES(?,?,?,?,?,?)
                """,
                (patient_id,event_type,source,severity,fingerprint,self._json(payload)),
            )
            event_id = int(cur.lastrowid)
            if create_house_job:
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO doctor_v2_house_jobs(
                        patient_id,trigger_event_id,card_version,priority
                    ) VALUES(?,?,?,?)
                    """,
                    (patient_id,event_id,card_version,int(priority)),
                )
            return event_id

    def claim_house_job(self, dialog_id: str) -> dict[str, Any] | None:
        with self.conn:
            row = self.conn.execute(
                """
                SELECT * FROM doctor_v2_house_jobs
                WHERE status='WAITING'
                ORDER BY priority DESC, house_job_id ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            changed = self.conn.execute(
                """
                UPDATE doctor_v2_house_jobs
                SET status='CLAIMED', claimed_dialog_id=?, claimed_at=CURRENT_TIMESTAMP
                WHERE house_job_id=? AND status='WAITING'
                """,
                (dialog_id, int(row["house_job_id"])),
            ).rowcount
            if changed != 1:
                return None
            return dict(row)

    def submit_house_decision(self, house_job_id: int, result: dict[str, Any]) -> int:
        decision = str(result.get("decision") or "")
        finding_class = str(result.get("finding_class") or "OBSERVATION")
        significance = str(result.get("significance") or "LOW")
        field_priority = str(result.get("field_priority") or "NORMAL")
        with self.conn:
            job = self.conn.execute(
                "SELECT * FROM doctor_v2_house_jobs WHERE house_job_id=?",
                (int(house_job_id),),
            ).fetchone()
            if job is None or job["status"] != "CLAIMED":
                raise DoctorV2StateError("House job is not claimed")
            cur = self.conn.execute(
                """
                INSERT INTO doctor_v2_house_decisions(
                    house_job_id,patient_id,card_version,finding_class,significance,
                    decision,field_priority,result_json
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    int(house_job_id), job["patient_id"], int(job["card_version"]),
                    finding_class, significance, decision, field_priority, self._json(result),
                ),
            )
            decision_id = int(cur.lastrowid)
            self.conn.execute(
                "UPDATE doctor_v2_house_jobs SET status='DONE', completed_at=CURRENT_TIMESTAMP WHERE house_job_id=?",
                (int(house_job_id),),
            )
            if decision == "DISPATCH_SUZIE":
                priority_map = {"LOW":25,"NORMAL":50,"HIGH":75,"URGENT":100}
                self.conn.execute(
                    """
                    INSERT INTO doctor_v2_field_queue(
                        patient_id,source_house_decision_id,priority,house_directive,
                        experimental_protocol_id,validation_stage
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        job["patient_id"], decision_id, priority_map.get(field_priority,50),
                        result.get("house_directive"),
                        result.get("experimental_protocol_id"),
                        result.get("validation_stage"),
                    ),
                )
            return decision_id

    def acquire_role_slot(self, role: str, assignment_id: str) -> str | None:
        if role not in {"FIELD_SUZIE", "HOUSE", "WILSON"}:
            raise DoctorV2StateError("invalid role")
        with self.conn:
            row = self.conn.execute(
                """
                SELECT slot_id FROM doctor_v2_role_slots
                WHERE role=? AND state='FREE'
                ORDER BY slot_id LIMIT 1
                """,
                (role,),
            ).fetchone()
            if row is None:
                return None
            slot_id = str(row["slot_id"])
            changed = self.conn.execute(
                """
                UPDATE doctor_v2_role_slots
                SET state='BUSY', assignment_id=?, updated_at=CURRENT_TIMESTAMP
                WHERE slot_id=? AND state='FREE'
                """,
                (assignment_id, slot_id),
            ).rowcount
            return slot_id if changed == 1 else None

    def release_role_slot(self, slot_id: str, assignment_id: str) -> bool:
        with self.conn:
            changed = self.conn.execute(
                """
                UPDATE doctor_v2_role_slots
                SET state='FREE', assignment_id=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE slot_id=? AND assignment_id=? AND state='BUSY'
                """,
                (slot_id, assignment_id),
            ).rowcount
            return changed == 1

    def open_dialog(self, *, dialog_id: str, role: str, assignment_id: str, project_id: str | None, generation: int = 1) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO doctor_v2_web_dialogs(
                    dialog_id,role,assignment_id,project_id,generation
                ) VALUES(?,?,?,?,?)
                """,
                (dialog_id,role,assignment_id,project_id,int(generation)),
            )

    def start_session(self, dialog_id: str, wake_payload: dict[str, Any] | None = None) -> DialogAdvance:
        with self.conn:
            row = self.conn.execute(
                "SELECT state,session_count FROM doctor_v2_web_dialogs WHERE dialog_id=?",
                (dialog_id,),
            ).fetchone()
            if row is None or row["state"] != "OPEN":
                raise DoctorV2StateError("dialog is not open")
            ordinal = int(row["session_count"]) + 1
            if ordinal > 10:
                raise DoctorV2StateError("dialog session limit reached")
            self.conn.execute(
                """
                INSERT INTO doctor_v2_web_sessions(dialog_id,ordinal,server_wake_payload_json)
                VALUES(?,?,?)
                """,
                (dialog_id,ordinal,self._json(wake_payload or {})),
            )
            self.conn.execute(
                "UPDATE doctor_v2_web_dialogs SET session_count=? WHERE dialog_id=?",
                (ordinal,dialog_id),
            )
            return DialogAdvance(dialog_id=dialog_id, ordinal=ordinal, rotate_after=(ordinal >= 10))

    def end_session(self, dialog_id: str, ordinal: int, reason: str) -> dict[str, Any]:
        if reason not in {"NATURAL","WATCHDOG_10M","ERROR","DIALOG_LIMIT"}:
            raise DoctorV2StateError("invalid session end reason")
        with self.conn:
            changed = self.conn.execute(
                """
                UPDATE doctor_v2_web_sessions SET ended_at=CURRENT_TIMESTAMP,end_reason=?
                WHERE dialog_id=? AND ordinal=? AND ended_at IS NULL
                """,
                (reason,dialog_id,int(ordinal)),
            ).rowcount
            if changed != 1:
                raise DoctorV2StateError("session not open")
            row = self.conn.execute(
                "SELECT session_count FROM doctor_v2_web_dialogs WHERE dialog_id=?",
                (dialog_id,),
            ).fetchone()
            session_count = int(row["session_count"])
            if reason == "NATURAL":
                self.conn.execute(
                    "UPDATE doctor_v2_web_dialogs SET state='CLOSED_NATURAL',closed_at=CURRENT_TIMESTAMP WHERE dialog_id=?",
                    (dialog_id,),
                )
                return {"dialog_closed": True, "rotate": False, "continue_same_dialog": False}
            if reason in {"DIALOG_LIMIT","WATCHDOG_10M"} and session_count >= 10:
                self.conn.execute(
                    "UPDATE doctor_v2_web_dialogs SET state='CLOSED_LIMIT',closed_at=CURRENT_TIMESTAMP WHERE dialog_id=?",
                    (dialog_id,),
                )
                return {"dialog_closed": True, "rotate": True, "continue_same_dialog": False}
            return {"dialog_closed": False, "rotate": False, "continue_same_dialog": reason == "WATCHDOG_10M"}

    def save_checkpoint(self, *, dialog_id: str, assignment_id: str, checkpoint: dict[str, Any]) -> int:
        with self.conn:
            row = self.conn.execute(
                "SELECT COALESCE(MAX(version),0) AS v FROM doctor_v2_session_checkpoints WHERE assignment_id=?",
                (assignment_id,),
            ).fetchone()
            version = int(row["v"]) + 1
            self.conn.execute(
                """
                INSERT INTO doctor_v2_session_checkpoints(dialog_id,assignment_id,version,checkpoint_json)
                VALUES(?,?,?,?)
                """,
                (dialog_id,assignment_id,version,self._json(checkpoint)),
            )
            self.conn.execute(
                "UPDATE doctor_v2_web_dialogs SET last_checkpoint_version=? WHERE dialog_id=?",
                (version,dialog_id),
            )
            return version

    def record_protocol_validation(
        self,
        *,
        protocol_id: str,
        episode_key: str,
        success: bool,
        verified: bool,
        evidence: dict[str, Any],
        installation_id_hash: str | None = None,
        patient_id: str | None = None,
        field_case_id: str | None = None,
    ) -> dict[str, Any]:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO doctor_v2_protocol_validation_episodes(
                    protocol_id,episode_key,installation_id_hash,patient_id,field_case_id,
                    success,verified,evidence_json
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(protocol_id,episode_key) DO NOTHING
                """,
                (
                    protocol_id,episode_key,installation_id_hash,patient_id,field_case_id,
                    1 if success else 0,1 if verified else 0,self._json(evidence),
                ),
            )
            row = self.conn.execute(
                """
                SELECT
                    SUM(CASE WHEN success=1 AND verified=1 THEN 1 ELSE 0 END) AS successes,
                    SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS negatives
                FROM doctor_v2_protocol_validation_episodes
                WHERE protocol_id=?
                """,
                (protocol_id,),
            ).fetchone()
            n = int(row["successes"] or 0)
            negatives = int(row["negatives"] or 0)
            state = "FIELD_TESTING"
            if n == 1: state = "VALIDATED_1_3"
            elif n == 2: state = "VALIDATED_2_3"
            elif n >= 3: state = "VALIDATED_3_3"
            self.conn.execute(
                "UPDATE doctor_v2_protocol_candidates SET state=?,updated_at=CURRENT_TIMESTAMP WHERE protocol_id=? AND state!='APPROVED_ACTIVE'",
                (state,protocol_id),
            )
            return {
                "verified_successes": n,
                "negative_episodes": negatives,
                "state": state,
                "validation_stage": f"{min(n,3)}/3",
                "publication_ready": state == "VALIDATED_3_3",
            }

    def upsert_protocol_candidate(
        self,
        *,
        protocol_id: str,
        origin: str,
        disease_id: str | None,
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        origin=str(origin or "INTERNAL_FIELD").upper()
        if origin not in {"INTERNAL_FIELD","EXTERNAL_WILSON","LEGACY"}:
            raise DoctorV2StateError("invalid candidate origin")
        initial_state="CANDIDATE" if origin in {"EXTERNAL_WILSON","LEGACY"} else "FIELD_TESTING"
        with self.conn:
            self.conn.execute(
                """INSERT INTO doctor_v2_protocol_candidates(
                       protocol_id,origin,state,disease_id,candidate_json
                   ) VALUES(?,?,?,?,?)
                   ON CONFLICT(protocol_id) DO UPDATE SET
                     disease_id=COALESCE(excluded.disease_id,doctor_v2_protocol_candidates.disease_id),
                     candidate_json=excluded.candidate_json,
                     updated_at=CURRENT_TIMESTAMP""",
                (
                    str(protocol_id),origin,initial_state,
                    str(disease_id) if disease_id else None,
                    self._json(candidate),
                ),
            )
        item=self.protocol_candidate(str(protocol_id))
        if item is None:
            raise DoctorV2StateError("candidate upsert failed")
        return item

    def protocol_candidate(self, protocol_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM doctor_v2_protocol_candidates WHERE protocol_id=?",
            (str(protocol_id),),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        try:
            item["candidate"] = json.loads(item.pop("candidate_json"))
        except Exception:
            item["candidate"] = {}
            item.pop("candidate_json", None)
        counts = self.conn.execute(
            """SELECT
                   SUM(CASE WHEN success=1 AND verified=1 THEN 1 ELSE 0 END) AS successes,
                   SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS negatives
               FROM doctor_v2_protocol_validation_episodes WHERE protocol_id=?""",
            (str(protocol_id),),
        ).fetchone()
        successes = int(counts["successes"] or 0)
        item["verified_successes"] = successes
        item["negative_episodes"] = int(counts["negatives"] or 0)
        item["validation_stage"] = f"{min(successes,3)}/3"
        return item

    def enqueue_publication_review(
        self, protocol_id: str, wilson_job_id: int, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        candidate = self.protocol_candidate(protocol_id)
        if not candidate or str(candidate.get("state")) != "VALIDATED_3_3":
            raise DoctorV2StateError("publication requires VALIDATED_3_3")
        with self.conn:
            self.conn.execute(
                """INSERT INTO doctor_v2_protocol_publication_queue(
                       protocol_id,status,requested_by_wilson_job_id,snapshot_json
                   ) VALUES(?, 'WAITING', ?, ?)
                   ON CONFLICT(protocol_id) DO UPDATE SET
                     requested_by_wilson_job_id=excluded.requested_by_wilson_job_id,
                     snapshot_json=excluded.snapshot_json,
                     updated_at=CURRENT_TIMESTAMP
                """,
                (str(protocol_id), int(wilson_job_id), self._json(snapshot)),
            )
        row = self.conn.execute(
            "SELECT * FROM doctor_v2_protocol_publication_queue WHERE protocol_id=?",
            (str(protocol_id),),
        ).fetchone()
        return dict(row) if row else {}
