from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4


ACTIVE_SESSION_STATES = ("STARTING", "ASSIGNED", "BUSY", "CHECKING")
FINAL_CASE_STATES = ("RESOLVED", "HUMAN_REQUIRED", "FAILED", "CANCELLED")


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class JournalConflict(RuntimeError):
    pass


class JournalNotFound(RuntimeError):
    pass


class JournalAuthError(RuntimeError):
    pass


class CaseJournal:
    """
    Central Suzie Doctor case journal.

    All public methods are synchronous and MUST be called while DoctorServer's
    single asyncio journal_gate is held. This gives fair, one-at-a-time journal
    entry for server dispatcher and all Doctor Suzie sessions. Mutations also
    use BEGIN IMMEDIATE so a future second process still fails safely.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.conn = sqlite3.connect(
            self.path,
            timeout=10,
            check_same_thread=False,
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.initialize()

    def initialize(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS doctor_cases (
                case_id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT NOT NULL,
                source_key TEXT NOT NULL,
                source_request_id TEXT,
                priority INTEGER NOT NULL DEFAULT 50,
                state TEXT NOT NULL,
                route TEXT NOT NULL DEFAULT 'SUZIE',
                summary TEXT NOT NULL DEFAULT '',
                problem_json TEXT NOT NULL DEFAULT '{}',
                disease_id TEXT,
                transport TEXT,
                doctor_session_id TEXT,
                dialog_id TEXT,
                dialog_ref TEXT,
                assignment_seq INTEGER NOT NULL DEFAULT 0,
                dispatch_token TEXT,
                dispatch_failures INTEGER NOT NULL DEFAULT 0,
                dispatch_retry_after TEXT,
                claim_token_hash TEXT,
                lease_expires TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                assigned_at TEXT,
                claimed_at TEXT,
                closed_at TEXT,
                outcome TEXT,
                result_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_doctor_cases_queue
                ON doctor_cases(state, priority DESC, case_id ASC);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_doctor_cases_active_source
                ON doctor_cases(client_id, source_key)
                WHERE state NOT IN ('RESOLVED','HUMAN_REQUIRED','FAILED','CANCELLED');

            CREATE TABLE IF NOT EXISTS doctor_sessions (
                session_id TEXT PRIMARY KEY,
                transport TEXT NOT NULL,
                status TEXT NOT NULL,
                slot_no INTEGER NOT NULL,
                dialog_id TEXT,
                current_case_id INTEGER,
                assignment_seq INTEGER NOT NULL DEFAULT 0,
                dispatch_job_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_seen TEXT,
                closed_at TEXT,
                detail_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_doctor_sessions_status
                ON doctor_sessions(status, slot_no);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_doctor_sessions_active_slot
                ON doctor_sessions(slot_no)
                WHERE status IN ('STARTING','ASSIGNED','BUSY','CHECKING');

            CREATE TABLE IF NOT EXISTS doctor_journal_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                case_id INTEGER,
                session_id TEXT,
                detail_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_doctor_journal_events_case
                ON doctor_journal_events(case_id, seq);
            """
        )
        case_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(doctor_cases)")
        }
        case_migrations = {
            "dispatch_failures": "ALTER TABLE doctor_cases ADD COLUMN dispatch_failures INTEGER NOT NULL DEFAULT 0",
            "dispatch_retry_after": "ALTER TABLE doctor_cases ADD COLUMN dispatch_retry_after TEXT",
        }
        for name, ddl in case_migrations.items():
            if name not in case_columns:
                self.conn.execute(ddl)
        self.conn.commit()

    def _begin(self) -> None:
        self.conn.execute("BEGIN IMMEDIATE")

    def _event(
        self,
        actor: str,
        action: str,
        *,
        case_id: int | None = None,
        session_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO doctor_journal_events(
                created_at,actor,action,case_id,session_id,detail_json
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                iso(),
                actor,
                action,
                case_id,
                session_id,
                _json(detail or {}),
            ),
        )

    def _case_row(self, case_id: int) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM doctor_cases WHERE case_id=?",
            (int(case_id),),
        ).fetchone()
        if not row:
            raise JournalNotFound(f"case {case_id} not found")
        return row

    def _session_row(self, session_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM doctor_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if not row:
            raise JournalNotFound(f"session {session_id} not found")
        return row

    @staticmethod
    def case_ref(case_id: int) -> str:
        return f"CASE #{int(case_id)}"

    def _case_public(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["case_ref"] = self.case_ref(int(row["case_id"]))
        out["problem"] = _loads(out.pop("problem_json", "{}"), {})
        out["result"] = _loads(out.pop("result_json", "{}"), {})
        out.pop("claim_token_hash", None)
        out.pop("dispatch_token", None)
        return out

    def _session_public(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["detail"] = _loads(out.pop("detail_json", "{}"), {})
        return out

    def stats(self) -> dict[str, Any]:
        states = {
            str(r["state"]): int(r["n"])
            for r in self.conn.execute(
                """
                SELECT state,COUNT(*) AS n
                FROM doctor_cases
                WHERE state NOT IN ('RESOLVED','HUMAN_REQUIRED','FAILED','CANCELLED')
                GROUP BY state
                """
            )
        }
        active = self.conn.execute(
            """
            SELECT COUNT(*) FROM doctor_sessions
            WHERE status IN ('STARTING','ASSIGNED','BUSY','CHECKING')
            """
        ).fetchone()[0]
        waiting = self.conn.execute(
            """
            SELECT COUNT(*) FROM doctor_cases
            WHERE state='FOR_SUZIE' AND doctor_session_id IS NULL
            """
        ).fetchone()[0]
        return {
            "open_cases": sum(states.values()),
            "waiting_for_suzie": int(waiting),
            "active_doctors": int(active),
            "states": states,
        }

    def list_active(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM doctor_cases
            WHERE state NOT IN ('RESOLVED','HUMAN_REQUIRED','FAILED','CANCELLED')
            ORDER BY priority DESC, case_id ASC
            LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        return [self._case_public(r) for r in rows]

    def get_case(self, case_id: int) -> dict[str, Any]:
        return self._case_public(self._case_row(case_id))

    def merge_house_evidence(
        self, case_id: int, update: dict[str, Any], *, actor: str = "doctor_house"
    ) -> dict[str, Any]:
        row = self._case_row(case_id)
        if str(row["state"]) in {"RESOLVED","HUMAN_REQUIRED","FAILED","CANCELLED"}:
            raise JournalConflict(f"case {case_id} is terminal")
        problem = _loads(row["problem_json"], {})
        if not isinstance(problem, dict):
            problem = {}
        updates = problem.get("related_house_updates")
        if not isinstance(updates, list):
            updates = []
        item = dict(update or {})
        item["merged_at"] = iso()
        updates.append(item)
        problem["related_house_updates"] = updates[-20:]
        incoming_caps = item.get("field_action_capabilities")
        if isinstance(incoming_caps, list):
            existing_caps = problem.get("field_action_capabilities")
            merged = {str(x) for x in (existing_caps if isinstance(existing_caps, list) else []) if str(x)}
            merged.update(str(x) for x in incoming_caps if str(x))
            problem["field_action_capabilities"] = sorted(merged)
        incoming_no_repeat = item.get("do_not_repeat")
        if isinstance(incoming_no_repeat, list):
            existing = problem.get("do_not_repeat")
            values = list(existing if isinstance(existing, list) else [])
            seen = {_json(x) for x in values if isinstance(x, dict)}
            for x in incoming_no_repeat:
                if isinstance(x, dict) and _json(x) not in seen:
                    values.append(x); seen.add(_json(x))
            problem["do_not_repeat"] = values[-50:]
        if isinstance(item.get("active_repair"), dict):
            if not problem.get("active_repair"):
                problem["active_repair"] = item["active_repair"]
            repair_fp=str(item["active_repair"].get("problem_key") or "").strip()
            if repair_fp:
                problem["canonical_resolution_fingerprint"] = repair_fp
            problem["terminal_resolution_required"] = True
            if not problem.get("domain"):
                problem["domain"] = str(item["active_repair"].get("domain") or "")
            if not problem.get("issue_id"):
                problem["issue_id"] = str(item["active_repair"].get("issue_id") or "")
        if not problem.get("original_functional_criterion") and isinstance(item.get("original_functional_criterion"), dict):
            problem["original_functional_criterion"] = item["original_functional_criterion"]
        with self.conn:
            self.conn.execute(
                "UPDATE doctor_cases SET problem_json=?,updated_at=? WHERE case_id=?",
                (_json(problem), iso(), int(case_id)),
            )
            self._event(actor,"HOUSE_EVIDENCE_MERGED",case_id=int(case_id),detail={"house_job_id":item.get("house_job_id"),"house_decision_id":item.get("house_decision_id")})
        return self.get_case(case_id)

    def active_sessions(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM doctor_sessions
            WHERE status IN ('STARTING','ASSIGNED','BUSY','CHECKING')
            ORDER BY slot_no
            """
        ).fetchall()
        return [self._session_public(r) for r in rows]

    def reap_stale_sessions(
        self,
        *,
        max_requeues: int = 2,
        dispatch_timeout_seconds: int = 300,
        assigned_timeout_seconds: int = 1800,
        actor: str = "doctor_server",
    ) -> list[dict[str, Any]]:
        """Expire abandoned Doctor sessions and release their Cases safely.

        Claimed/TREATING/VERIFYING cases are stale only after their explicit
        claim lease expires. DISPATCHING and unclaimed ASSIGNED cases use
        bounded inactivity timeouts because they do not yet have a claim lease.
        A stale Case is requeued with all old ownership/authentication cleared.
        Requeues are bounded; after the configured limit the Case becomes
        HUMAN_REQUIRED instead of looping forever.
        """
        max_requeues = max(0, int(max_requeues))
        dispatch_timeout_seconds = max(60, int(dispatch_timeout_seconds))
        assigned_timeout_seconds = max(60, int(assigned_timeout_seconds))
        now_dt = utcnow()
        now = iso(now_dt)
        actions: list[dict[str, Any]] = []

        self._begin()
        try:
            rows = self.conn.execute(
                """
                SELECT c.*,
                       s.status AS session_status,
                       s.last_seen AS session_last_seen,
                       s.updated_at AS session_updated_at,
                       s.detail_json AS session_detail_json
                FROM doctor_cases c
                JOIN doctor_sessions s
                  ON s.session_id=c.doctor_session_id
                WHERE c.state NOT IN ('RESOLVED','HUMAN_REQUIRED','FAILED','CANCELLED')
                  AND s.status IN ('STARTING','ASSIGNED','BUSY','CHECKING')
                ORDER BY c.case_id
                """
            ).fetchall()

            for case in rows:
                state = str(case["state"] or "")
                session_status = str(case["session_status"] or "")
                reason = ""

                if state in ("CLAIMED", "TREATING", "VERIFYING"):
                    expires = _parse_iso(case["lease_expires"])
                    if expires is not None and expires <= now_dt:
                        reason = "claim_lease_expired"
                elif state == "ASSIGNED" and session_status == "ASSIGNED":
                    stamp = (
                        _parse_iso(case["assigned_at"])
                        or _parse_iso(case["updated_at"])
                        or _parse_iso(case["session_last_seen"])
                    )
                    if stamp is not None and (now_dt - stamp).total_seconds() >= assigned_timeout_seconds:
                        reason = "assignment_timeout"
                elif state == "DISPATCHING" and session_status == "STARTING":
                    stamp = (
                        _parse_iso(case["session_updated_at"])
                        or _parse_iso(case["updated_at"])
                    )
                    if stamp is not None and (now_dt - stamp).total_seconds() >= dispatch_timeout_seconds:
                        reason = "dispatch_timeout"

                if not reason:
                    continue

                case_id = int(case["case_id"])
                session_id = str(case["doctor_session_id"] or "")
                previous_state = state
                requeues = int(self.conn.execute(
                    """
                    SELECT COUNT(*) FROM doctor_journal_events
                    WHERE case_id=? AND action='CASE_REQUEUED_STALE_SESSION'
                    """,
                    (case_id,),
                ).fetchone()[0])

                detail = _loads(case["session_detail_json"], {})
                detail["stale_reason"] = reason
                detail["stale_at"] = now
                detail["stale_case_state"] = previous_state
                self.conn.execute(
                    """
                    UPDATE doctor_sessions
                    SET status='EXPIRED',current_case_id=NULL,updated_at=?,
                        last_seen=?,closed_at=?,detail_json=?
                    WHERE session_id=?
                    """,
                    (now, now, now, _json(detail), session_id),
                )
                self._event(
                    actor,
                    "DOCTOR_SESSION_EXPIRED",
                    case_id=case_id,
                    session_id=session_id,
                    detail={
                        "reason": reason,
                        "previous_state": previous_state,
                        "requeues_before": requeues,
                    },
                )

                if requeues < max_requeues:
                    self.conn.execute(
                        """
                        UPDATE doctor_cases
                        SET state='FOR_SUZIE',transport=NULL,doctor_session_id=NULL,
                            dialog_id=NULL,dialog_ref=NULL,assignment_seq=0,
                            dispatch_token=NULL,claim_token_hash=NULL,lease_expires=NULL,
                            assigned_at=NULL,claimed_at=NULL,updated_at=?
                        WHERE case_id=?
                        """,
                        (now, case_id),
                    )
                    self._event(
                        actor,
                        "CASE_REQUEUED_STALE_SESSION",
                        case_id=case_id,
                        session_id=session_id,
                        detail={
                            "reason": reason,
                            "attempt": requeues + 1,
                            "max_requeues": max_requeues,
                        },
                    )
                    actions.append({
                        "case_id": case_id,
                        "session_id": session_id,
                        "action": "REQUEUED",
                        "reason": reason,
                        "attempt": requeues + 1,
                    })
                else:
                    result = {
                        "reason": "doctor_session_stale_limit",
                        "stale_reason": reason,
                        "max_requeues": max_requeues,
                        "previous_state": previous_state,
                    }
                    self.conn.execute(
                        """
                        UPDATE doctor_cases
                        SET state='FAILED',outcome='FAILED',
                            result_json=?,claim_token_hash=NULL,lease_expires=NULL,
                            closed_at=?,updated_at=?
                        WHERE case_id=?
                        """,
                        (_json(result), now, now, case_id),
                    )
                    self._event(
                        actor,
                        "CASE_STALE_REQUEUE_LIMIT_REACHED",
                        case_id=case_id,
                        session_id=session_id,
                        detail=result,
                    )
                    actions.append({
                        "case_id": case_id,
                        "session_id": session_id,
                        "action": "FAILED",
                        "reason": reason,
                        "result": result,
                        "attempt": requeues + 1,
                    })

            self.conn.commit()
            return actions
        except Exception:
            self.conn.rollback()
            raise

    def escalate(
        self,
        *,
        client_id: str,
        source_key: str,
        source_request_id: str | None,
        summary: str,
        problem: dict[str, Any],
        disease_id: str | None = None,
        priority: int = 50,
        actor: str = "doctor_server",
        human_required_cooldown_seconds: int = 86400,
    ) -> tuple[dict[str, Any], bool]:
        now_dt = utcnow()
        now = iso(now_dt)
        self._begin()
        try:
            existing = self.conn.execute(
                """
                SELECT * FROM doctor_cases
                WHERE client_id=? AND source_key=?
                  AND state NOT IN ('RESOLVED','HUMAN_REQUIRED','FAILED','CANCELLED')
                ORDER BY case_id DESC LIMIT 1
                """,
                (client_id, source_key),
            ).fetchone()
            if existing:
                self.conn.execute(
                    """
                    UPDATE doctor_cases
                    SET updated_at=?, problem_json=?, summary=?,
                        disease_id=COALESCE(?,disease_id)
                    WHERE case_id=?
                    """,
                    (
                        now,
                        _json(problem),
                        summary,
                        disease_id,
                        int(existing["case_id"]),
                    ),
                )
                self._event(
                    actor,
                    "CASE_REFRESHED",
                    case_id=int(existing["case_id"]),
                    detail={"source_key": source_key},
                )
                self.conn.commit()
                return self.get_case(int(existing["case_id"])), False

            # A HUMAN_REQUIRED result means a human/external dependency is still
            # pending. Repeated audits of the same continuous problem must not
            # spawn a new Web Doctor every cycle. Suppress re-escalation for a
            # bounded window; a later recurrence can create a fresh Case.
            recent_human = self.conn.execute(
                """
                SELECT * FROM doctor_cases
                WHERE client_id=? AND source_key=? AND state='HUMAN_REQUIRED'
                ORDER BY case_id DESC LIMIT 1
                """,
                (client_id, source_key),
            ).fetchone()
            if recent_human:
                closed = _parse_iso(recent_human["closed_at"])
                cooldown = max(60, int(human_required_cooldown_seconds))
                if closed is not None and (now_dt - closed).total_seconds() < cooldown:
                    self.conn.execute(
                        """
                        UPDATE doctor_cases
                        SET updated_at=?, problem_json=?, summary=?,
                            disease_id=COALESCE(?,disease_id)
                        WHERE case_id=?
                        """,
                        (
                            now,
                            _json(problem),
                            summary,
                            disease_id,
                            int(recent_human["case_id"]),
                        ),
                    )
                    self._event(
                        actor,
                        "CASE_REFRESHED_HUMAN_REQUIRED",
                        case_id=int(recent_human["case_id"]),
                        detail={
                            "source_key": source_key,
                            "cooldown_seconds": cooldown,
                        },
                    )
                    self.conn.commit()
                    return self.get_case(int(recent_human["case_id"])), False

            cur = self.conn.execute(
                """
                INSERT INTO doctor_cases(
                    client_id,source_key,source_request_id,priority,state,route,
                    summary,problem_json,disease_id,created_at,updated_at
                ) VALUES(?,?,?,?, 'FOR_SUZIE','SUZIE',?,?,?,?,?)
                """,
                (
                    client_id,
                    source_key,
                    source_request_id,
                    int(priority),
                    summary,
                    _json(problem),
                    disease_id,
                    now,
                    now,
                ),
            )
            case_id = int(cur.lastrowid)
            self._event(
                actor,
                "CASE_ESCALATED_TO_SUZIE",
                case_id=case_id,
                detail={"source_key": source_key, "priority": int(priority)},
            )
            self.conn.commit()
            return self.get_case(case_id), True
        except Exception:
            self.conn.rollback()
            raise

    def missing_dialog_candidates(
        self, live_dialog_ids: set[str], *, grace_seconds: int = 90
    ) -> list[dict[str, Any]]:
        now = utcnow()
        grace = max(30, int(grace_seconds))
        rows = self.conn.execute(
            """
            SELECT c.case_id,c.client_id,c.state,c.dialog_id,c.doctor_session_id,
                   c.updated_at AS case_updated,c.claimed_at,c.assigned_at,
                   s.status AS session_status,s.last_seen,s.updated_at AS session_updated
            FROM doctor_cases c JOIN doctor_sessions s ON s.session_id=c.doctor_session_id
            WHERE c.transport='WEB' AND c.dialog_id IS NOT NULL
              AND c.state IN ('ASSIGNED','CLAIMED','TREATING','VERIFYING')
              AND s.status IN ('ASSIGNED','BUSY','CHECKING')
            ORDER BY c.case_id
            """
        ).fetchall()
        out=[]
        for row in rows:
            dialog_id=str(row["dialog_id"] or "")
            if not dialog_id or dialog_id in live_dialog_ids:
                continue
            stamp=(
                _parse_iso(row["last_seen"])
                or _parse_iso(row["session_updated"])
                or _parse_iso(row["case_updated"])
                or _parse_iso(row["claimed_at"])
                or _parse_iso(row["assigned_at"])
            )
            if stamp is None or (now-stamp).total_seconds() < grace:
                continue
            out.append(dict(row))
        return out

    def recover_missing_dialog(
        self, *, case_id: int, session_id: str, max_requeues: int = 2,
        actor: str = "doctor_server"
    ) -> dict[str, Any]:
        max_requeues=max(0,int(max_requeues))
        self._begin()
        try:
            case=self._case_row(case_id)
            if str(case["doctor_session_id"] or "") != str(session_id):
                raise JournalConflict("case/session binding changed")
            if str(case["state"] or "") not in {"ASSIGNED","CLAIMED","TREATING","VERIFYING"}:
                raise JournalConflict("case no longer eligible for missing-dialog recovery")
            session=self._session_row(session_id)
            now_dt=utcnow(); now=iso(now_dt)
            requeues=int(self.conn.execute(
                "SELECT COUNT(*) FROM doctor_journal_events WHERE case_id=? AND action='CASE_REQUEUED_MISSING_DIALOG'",
                (int(case_id),),
            ).fetchone()[0])
            detail=_loads(session["detail_json"],{})
            detail.update({"stale_reason":"browser_dialog_missing","stale_at":now,"stale_case_state":str(case["state"] or "")})
            self.conn.execute(
                "UPDATE doctor_sessions SET status='EXPIRED',current_case_id=NULL,updated_at=?,last_seen=?,closed_at=?,detail_json=? WHERE session_id=?",
                (now,now,now,_json(detail),session_id),
            )
            if requeues < max_requeues:
                failures=int(case["dispatch_failures"] or 0)+1
                delay=min(300,15*(2**min(failures-1,4)))
                retry_after=iso(now_dt+timedelta(seconds=delay))
                self.conn.execute(
                    """UPDATE doctor_cases SET state='FOR_SUZIE',transport=NULL,doctor_session_id=NULL,
                       dialog_id=NULL,dialog_ref=NULL,assignment_seq=0,dispatch_token=NULL,
                       claim_token_hash=NULL,lease_expires=NULL,assigned_at=NULL,claimed_at=NULL,
                       dispatch_failures=?,dispatch_retry_after=?,updated_at=? WHERE case_id=?""",
                    (failures,retry_after,now,int(case_id)),
                )
                result={"action":"REQUEUED","case_id":int(case_id),"client_id":str(case["client_id"]),"session_id":session_id,"reason":"browser_dialog_missing","attempt":requeues+1,"retry_after":retry_after}
                self._event(actor,"CASE_REQUEUED_MISSING_DIALOG",case_id=int(case_id),session_id=session_id,detail=result)
            else:
                report={"reason":"web_transport_missing_dialog_limit","stale_reason":"browser_dialog_missing","max_requeues":max_requeues,"previous_state":str(case["state"] or "")}
                self.conn.execute(
                    """UPDATE doctor_cases SET state='FAILED',outcome='FAILED',result_json=?,
                       claim_token_hash=NULL,lease_expires=NULL,closed_at=?,updated_at=? WHERE case_id=?""",
                    (_json(report),now,now,int(case_id)),
                )
                result={"action":"FAILED","case_id":int(case_id),"client_id":str(case["client_id"]),"session_id":session_id,"reason":"browser_dialog_missing","attempt":requeues+1,"result":report}
                self._event(actor,"CASE_MISSING_DIALOG_LIMIT_REACHED",case_id=int(case_id),session_id=session_id,detail=result)
            self.conn.commit()
            return result
        except Exception:
            self.conn.rollback(); raise

    def reserve_web_dispatch(
        self,
        *,
        max_doctors: int,
        actor: str = "doctor_server",
    ) -> dict[str, Any] | None:
        self._begin()
        try:
            occupied = {
                int(r["slot_no"])
                for r in self.conn.execute(
                    """
                    SELECT slot_no FROM doctor_sessions
                    WHERE status IN ('STARTING','ASSIGNED','BUSY','CHECKING')
                    """
                )
            }
            free_slots = [n for n in range(1, int(max_doctors) + 1) if n not in occupied]
            if not free_slots:
                self.conn.commit()
                return None

            case = self.conn.execute(
                """
                SELECT * FROM doctor_cases
                WHERE state='FOR_SUZIE'
                  AND doctor_session_id IS NULL
                  AND dialog_id IS NULL
                  AND (dispatch_retry_after IS NULL OR datetime(dispatch_retry_after) <= datetime('now'))
                ORDER BY priority DESC, case_id ASC
                LIMIT 1
                """
            ).fetchone()
            if not case:
                self.conn.commit()
                return None

            case_id = int(case["case_id"])
            slot_no = free_slots[0]
            session_id = f"DS-{uuid4()}"
            dispatch_token = secrets.token_urlsafe(24)
            now = iso()

            self.conn.execute(
                """
                INSERT INTO doctor_sessions(
                    session_id,transport,status,slot_no,current_case_id,
                    assignment_seq,created_at,updated_at,last_seen,detail_json
                ) VALUES(?, 'WEB','STARTING',?,?,1,?,?,?,?)
                """,
                (
                    session_id,
                    slot_no,
                    case_id,
                    now,
                    now,
                    now,
                    _json({"dispatch_token": dispatch_token}),
                ),
            )
            cur = self.conn.execute(
                """
                UPDATE doctor_cases
                SET state='DISPATCHING',transport='WEB',
                    doctor_session_id=?,assignment_seq=1,
                    dispatch_token=?,updated_at=?
                WHERE case_id=? AND state='FOR_SUZIE'
                """,
                (session_id, dispatch_token, now, case_id),
            )
            if cur.rowcount != 1:
                raise JournalConflict("dispatch reservation lost")

            self._event(
                actor,
                "WEB_DISPATCH_RESERVED",
                case_id=case_id,
                session_id=session_id,
                detail={"slot_no": slot_no},
            )
            self.conn.commit()
            return {
                "case": self.get_case(case_id),
                "session_id": session_id,
                "slot_no": slot_no,
                "dispatch_token": dispatch_token,
            }
        except Exception:
            self.conn.rollback()
            raise

    def mark_dispatch_progress(
        self,
        *,
        case_id: int,
        session_id: str,
        dispatch_job_id: str,
        tab_id: str,
        transient_url: str,
        actor: str = "doctor_server",
    ) -> None:
        self._begin()
        try:
            case = self._case_row(case_id)
            session = self._session_row(session_id)
            if case["state"] != "DISPATCHING" or case["doctor_session_id"] != session_id:
                raise JournalConflict("dispatch reservation no longer owns case")
            detail = _loads(session["detail_json"], {})
            detail.update(
                {
                    "dispatch_job_id": dispatch_job_id,
                    "tab_id": tab_id,
                    "transient_url": transient_url,
                    "ui_sent": True,
                }
            )
            now = iso()
            self.conn.execute(
                """
                UPDATE doctor_sessions
                SET dispatch_job_id=?,updated_at=?,last_seen=?,detail_json=?
                WHERE session_id=?
                """,
                (dispatch_job_id, now, now, _json(detail), session_id),
            )
            self._event(
                actor,
                "WEB_UI_SENT_WAITING_DIALOG_ID",
                case_id=int(case_id),
                session_id=session_id,
                detail={
                    "dispatch_job_id": dispatch_job_id,
                    "tab_id": tab_id,
                    "transient_url": transient_url,
                },
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def starting_sessions(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM doctor_sessions
            WHERE status='STARTING'
            ORDER BY created_at
            """
        ).fetchall()
        return [self._session_public(r) for r in rows]

    def finish_dispatch(
        self,
        *,
        case_id: int,
        session_id: str,
        dispatch_job_id: str,
        dialog_id: str,
        conversation_url: str,
        actor: str = "doctor_server",
    ) -> dict[str, Any]:
        self._begin()
        try:
            case = self._case_row(case_id)
            session = self._session_row(session_id)
            if case["state"] != "DISPATCHING" or case["doctor_session_id"] != session_id:
                raise JournalConflict("case no longer belongs to dispatch reservation")
            if session["status"] != "STARTING":
                raise JournalConflict("doctor session is no longer STARTING")
            now = iso()
            self.conn.execute(
                """
                UPDATE doctor_sessions
                SET status='ASSIGNED',dialog_id=?,dispatch_job_id=?,
                    updated_at=?,last_seen=?,detail_json=?
                WHERE session_id=?
                """,
                (
                    dialog_id,
                    dispatch_job_id,
                    now,
                    now,
                    _json({"conversation_url": conversation_url}),
                    session_id,
                ),
            )
            self.conn.execute(
                """
                UPDATE doctor_cases
                SET state='ASSIGNED',dialog_id=?,dialog_ref=?,
                    assigned_at=?,updated_at=?,dispatch_token=NULL,
                    dispatch_failures=0,dispatch_retry_after=NULL
                WHERE case_id=?
                """,
                (dialog_id, dialog_id, now, now, int(case_id)),
            )
            self._event(
                actor,
                "WEB_DIALOG_ASSIGNED",
                case_id=int(case_id),
                session_id=session_id,
                detail={
                    "dialog_id": dialog_id,
                    "dispatch_job_id": dispatch_job_id,
                    "conversation_url": conversation_url,
                },
            )
            self.conn.commit()
            return self.get_case(case_id)
        except Exception:
            self.conn.rollback()
            raise

    def fail_dispatch(
        self,
        *,
        case_id: int,
        session_id: str,
        reason: str,
        actor: str = "doctor_server",
    ) -> None:
        self._begin()
        try:
            now_dt = utcnow()
            now = iso(now_dt)
            row = self.conn.execute(
                "SELECT dispatch_failures FROM doctor_cases WHERE case_id=?",
                (int(case_id),),
            ).fetchone()
            failures = int(row["dispatch_failures"] or 0) + 1 if row else 1
            delay_seconds = min(300, 15 * (2 ** min(failures - 1, 4)))
            retry_after = iso(now_dt + timedelta(seconds=delay_seconds))
            self.conn.execute(
                """
                UPDATE doctor_cases
                SET state='FOR_SUZIE',transport=NULL,doctor_session_id=NULL,
                    dialog_id=NULL,dialog_ref=NULL,assignment_seq=0,
                    dispatch_token=NULL,dispatch_failures=?,dispatch_retry_after=?,updated_at=?
                WHERE case_id=? AND state='DISPATCHING'
                  AND doctor_session_id=?
                """,
                (failures, retry_after, now, int(case_id), session_id),
            )
            self.conn.execute(
                """
                UPDATE doctor_sessions
                SET status='FAILED',updated_at=?,closed_at=?,detail_json=?
                WHERE session_id=?
                """,
                (now, now, _json({"error": reason}), session_id),
            )
            self._event(
                actor,
                "WEB_DISPATCH_FAILED",
                case_id=int(case_id),
                session_id=session_id,
                detail={
                    "error": reason,
                    "dispatch_failures": failures,
                    "retry_after": retry_after,
                    "retry_delay_seconds": delay_seconds,
                },
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def claim(
        self,
        *,
        case_id: int,
        actor: str = "web_suzie",
        lease_seconds: int = 900,
    ) -> dict[str, Any]:
        self._begin()
        try:
            case = self._case_row(case_id)
            if case["state"] != "ASSIGNED":
                raise JournalConflict(
                    f"{self.case_ref(case_id)} is {case['state']}, not ASSIGNED"
                )

            # Case ownership is exclusive per Case, not per client. Multiple
            # Cases for one exact client may be diagnosed in parallel by
            # different Doctor sessions. State-changing client commands remain
            # serialized by ClientCommandBridge.poll() inside the Doctor App.

            session_id = str(case["doctor_session_id"] or "")
            if not session_id:
                raise JournalConflict("case has no reserved doctor session")
            session = self._session_row(session_id)
            if session["current_case_id"] != int(case_id):
                raise JournalConflict("session/current case mismatch")

            raw_token = secrets.token_urlsafe(32)
            now_dt = utcnow()
            now = iso(now_dt)
            lease_expires = iso(now_dt + timedelta(seconds=max(60, int(lease_seconds))))
            self.conn.execute(
                """
                UPDATE doctor_cases
                SET state='CLAIMED',claim_token_hash=?,lease_expires=?,
                    claimed_at=?,updated_at=?
                WHERE case_id=?
                """,
                (_token_hash(raw_token), lease_expires, now, now, int(case_id)),
            )
            self.conn.execute(
                """
                UPDATE doctor_sessions
                SET status='BUSY',updated_at=?,last_seen=?
                WHERE session_id=?
                """,
                (now, now, session_id),
            )
            self._event(
                actor,
                "CASE_CLAIMED",
                case_id=int(case_id),
                session_id=session_id,
                detail={"lease_expires": lease_expires},
            )
            self.conn.commit()
            result = self.get_case(case_id)
            result["claim_token"] = raw_token
            result["doctor_session"] = self._session_public(self._session_row(session_id))
            return result
        except Exception:
            self.conn.rollback()
            raise

    def _verify_claim(self, case: sqlite3.Row, claim_token: str) -> str:
        expected = str(case["claim_token_hash"] or "")
        if not expected or not secrets.compare_digest(expected, _token_hash(claim_token)):
            raise JournalAuthError("invalid claim token")
        expires = _parse_iso(case["lease_expires"])
        if expires is None or expires <= utcnow():
            raise JournalAuthError("claim lease expired")
        session_id = str(case["doctor_session_id"] or "")
        if not session_id:
            raise JournalConflict("case has no doctor session")
        return session_id

    def authorize_claim(
        self,
        *,
        case_id: int,
        claim_token: str,
    ) -> dict[str, Any]:
        case = self._case_row(case_id)
        self._verify_claim(case, claim_token)
        if case["state"] not in ("CLAIMED", "TREATING", "VERIFYING"):
            raise JournalConflict(
                f"tool invocation not allowed in {case['state']}"
            )
        return self._case_public(case)

    def heartbeat(
        self,
        *,
        case_id: int,
        claim_token: str,
        lease_seconds: int = 900,
        actor: str = "web_suzie",
    ) -> dict[str, Any]:
        self._begin()
        try:
            case = self._case_row(case_id)
            session_id = self._verify_claim(case, claim_token)
            if case["state"] not in ("CLAIMED", "TREATING", "VERIFYING"):
                raise JournalConflict(f"heartbeat not allowed in {case['state']}")
            now_dt = utcnow()
            now = iso(now_dt)
            lease_expires = iso(now_dt + timedelta(seconds=max(60, int(lease_seconds))))
            self.conn.execute(
                "UPDATE doctor_cases SET lease_expires=?,updated_at=? WHERE case_id=?",
                (lease_expires, now, int(case_id)),
            )
            self.conn.execute(
                """
                UPDATE doctor_sessions SET last_seen=?,updated_at=?
                WHERE session_id=?
                """,
                (now, now, session_id),
            )
            self.conn.commit()
            return {"case_id": int(case_id), "lease_expires": lease_expires}
        except Exception:
            self.conn.rollback()
            raise

    def stage(
        self,
        *,
        case_id: int,
        claim_token: str,
        stage: str,
        actor: str = "web_suzie",
    ) -> dict[str, Any]:
        stage = str(stage).upper()
        if stage not in {"CLAIMED", "TREATING", "VERIFYING"}:
            raise JournalConflict("invalid treatment stage")
        self._begin()
        try:
            case = self._case_row(case_id)
            session_id = self._verify_claim(case, claim_token)
            if case["state"] not in ("CLAIMED", "TREATING", "VERIFYING"):
                raise JournalConflict(f"stage change not allowed in {case['state']}")
            now = iso()
            self.conn.execute(
                "UPDATE doctor_cases SET state=?,updated_at=? WHERE case_id=?",
                (stage, now, int(case_id)),
            )
            self.conn.execute(
                """
                UPDATE doctor_sessions SET status='BUSY',last_seen=?,updated_at=?
                WHERE session_id=?
                """,
                (now, now, session_id),
            )
            self._event(
                actor,
                "CASE_STAGE",
                case_id=int(case_id),
                session_id=session_id,
                detail={"stage": stage},
            )
            self.conn.commit()
            return self.get_case(case_id)
        except Exception:
            self.conn.rollback()
            raise

    def complete_and_next(
        self,
        *,
        case_id: int,
        claim_token: str,
        outcome: str,
        result: dict[str, Any],
        actor: str = "web_suzie",
        allow_handoff: bool = True,
    ) -> dict[str, Any]:
        outcome = str(outcome).upper()
        final_state = {
            "SUCCESS": "RESOLVED",
            "RESOLVED": "RESOLVED",
            "HUMAN_REQUIRED": "HUMAN_REQUIRED",
            "UNSAFE_TO_TREAT": "FAILED",
            "FAILED": "FAILED",
        }.get(outcome)
        if not final_state:
            raise JournalConflict("invalid outcome")

        self._begin()
        try:
            case = self._case_row(case_id)
            session_id = self._verify_claim(case, claim_token)
            if case["state"] not in ("CLAIMED", "TREATING", "VERIFYING"):
                raise JournalConflict(f"cannot finish case in {case['state']}")
            session = self._session_row(session_id)
            now = iso()

            self.conn.execute(
                """
                UPDATE doctor_cases
                SET state=?,outcome=?,result_json=?,claim_token_hash=NULL,
                    lease_expires=NULL,closed_at=?,updated_at=?
                WHERE case_id=?
                """,
                (final_state, outcome, _json(result), now, now, int(case_id)),
            )
            self.conn.execute(
                """
                UPDATE doctor_sessions
                SET status='CHECKING',current_case_id=NULL,
                    updated_at=?,last_seen=?
                WHERE session_id=?
                """,
                (now, now, session_id),
            )
            self._event(
                actor,
                "CASE_FINISHED",
                case_id=int(case_id),
                session_id=session_id,
                detail={"outcome": outcome},
            )

            next_case = (
                self.conn.execute(
                    """
                    SELECT * FROM doctor_cases
                    WHERE state='FOR_SUZIE'
                      AND doctor_session_id IS NULL
                      AND dialog_id IS NULL
                    ORDER BY priority DESC, case_id ASC
                    LIMIT 1
                    """
                ).fetchone()
                if allow_handoff
                else None
            )

            if next_case:
                next_id = int(next_case["case_id"])
                seq = int(session["assignment_seq"] or 0) + 1
                dialog_id = str(session["dialog_id"] or "")
                dialog_ref = dialog_id if seq == 1 else f"{dialog_id}-{seq}"
                self.conn.execute(
                    """
                    UPDATE doctor_sessions
                    SET status='ASSIGNED',current_case_id=?,assignment_seq=?,
                        updated_at=?,last_seen=?
                    WHERE session_id=?
                    """,
                    (next_id, seq, now, now, session_id),
                )
                self.conn.execute(
                    """
                    UPDATE doctor_cases
                    SET state='ASSIGNED',transport=?,doctor_session_id=?,
                        dialog_id=?,dialog_ref=?,assignment_seq=?,
                        assigned_at=?,updated_at=?
                    WHERE case_id=? AND state='FOR_SUZIE'
                      AND doctor_session_id IS NULL
                    """,
                    (
                        session["transport"],
                        session_id,
                        dialog_id,
                        dialog_ref,
                        seq,
                        now,
                        now,
                        next_id,
                    ),
                )
                self._event(
                    actor,
                    "NEXT_CASE_ASSIGNED_TO_EXISTING_DIALOG",
                    case_id=next_id,
                    session_id=session_id,
                    detail={"dialog_id": dialog_id, "dialog_ref": dialog_ref, "seq": seq},
                )
                self.conn.commit()
                return {
                    "finished_case": self.case_ref(case_id),
                    "next_case": self.get_case(next_id),
                    "doctor_session_id": session_id,
                    "dialog_id": dialog_id,
                    "dialog_ref": dialog_ref,
                    "close_dialog": False,
                }

            self.conn.execute(
                """
                UPDATE doctor_sessions
                SET status='CLOSED',updated_at=?,last_seen=?,closed_at=?
                WHERE session_id=?
                """,
                (now, now, now, session_id),
            )
            self._event(
                actor,
                "DOCTOR_SESSION_CLOSED_NO_WORK",
                case_id=int(case_id),
                session_id=session_id,
            )
            self.conn.commit()
            return {
                "finished_case": self.case_ref(case_id),
                "next_case": None,
                "doctor_session_id": session_id,
                "dialog_id": str(session["dialog_id"] or ""),
                "close_dialog": True,
            }
        except Exception:
            self.conn.rollback()
            raise
