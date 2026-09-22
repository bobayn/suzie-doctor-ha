from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import ipaddress
import json
import re
import secrets
import sqlite3
import ssl
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from urllib.parse import unquote

import yaml
from aiohttp import ClientSession, ClientTimeout, web
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from case_journal import (
    CaseJournal,
    JournalAuthError,
    JournalConflict,
    JournalNotFound,
)
from command_bridge import ClientCommandBridge, CommandBridgeError
from doctor_v2_extension import V2Extension

SERVER_VERSION = "0.2.0-v2-dev"
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
ALLOWED_NETWORKS = [
    ipaddress.ip_network("192.168.0.0/24"),
    ipaddress.ip_network("10.8.0.0/24"),
]
MAX_BODY = 256 * 1024

CLIENT_COMMAND_TOOLS = {
    "doctor.capabilities",
    "doctor.suite",
    "doctor.skill",
    "doctor.diagnose",
    "ha.config.read",
    "ha.repairs.list",
    "ha.notifications.list",
    "ha.config_entries.list",
    "supervisor.info",
    "supervisor.host.info",
    "supervisor.core.info",
    "supervisor.network.info",
    "supervisor.addons.list",
    "supervisor.mounts.list",
    "supervisor.backups.list",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).isoformat()


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def b64e(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def b64d(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def clean_text(value: Any, limit: int = 1000) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    return text[:limit]


def safe_structured(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return clean_text(value, 1000)
    if isinstance(value, list):
        return [safe_structured(x, depth + 1) for x in value[:100]]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:100]:
            name = clean_text(key, 80)
            if name.lower() in {
                "raw_log", "raw_logs", "audio", "image", "video",
                "document", "user_content", "conversation",
            }:
                continue
            out[name] = safe_structured(item, depth + 1)
        return out
    return clean_text(value, 1000)


def token_set(value: Any) -> set[str]:
    text = clean_text(value, 5000).lower()
    return {
        token
        for token in re.findall(r"[a-z0-9_./:+-]{3,}", text)
        if token not in {
            "the", "and", "for", "with", "from", "this", "that",
            "was", "were", "not", "but", "into", "are", "is",
        }
    }


class ServerDB:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def initialize(self) -> None:
        self.conn.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS clients (
            client_id TEXT PRIMARY KEY,
            public_key_b64 TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            trial_expires TEXT,
            license_expires TEXT,
            created_at TEXT NOT NULL,
            last_seen TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS request_nonces (
            client_id TEXT NOT NULL,
            nonce TEXT NOT NULL,
            seen_at INTEGER NOT NULL,
            PRIMARY KEY(client_id, nonce)
        );
        CREATE INDEX IF NOT EXISTS idx_nonce_seen
            ON request_nonces(seen_at);
        CREATE TABLE IF NOT EXISTS server_events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            client_id TEXT,
            detail_json TEXT NOT NULL DEFAULT '{}'
        );
        """)
        self.conn.commit()

    def _row(self, client_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM clients WHERE client_id=?", (client_id,)
        ).fetchone()
        return dict(row) if row else None

    def enroll(
        self,
        *,
        client_id: str,
        public_key_b64: str,
        label: str,
        metadata: dict[str, Any],
        trial_days: int,
        auto_trial: bool,
    ) -> dict[str, Any]:
        current = self._row(client_id)
        now = utcnow()
        if current:
            if current["public_key_b64"] != public_key_b64:
                raise web.HTTPConflict(text="client_id already enrolled with another key")
            self.conn.execute(
                "UPDATE clients SET last_seen=?, label=?, metadata_json=? WHERE client_id=?",
                (iso(now), label, json.dumps(metadata), client_id),
            )
            self.conn.commit()
            return self._row(client_id) or current

        status = "trial" if auto_trial else "free"
        trial_expires = iso(now + timedelta(days=trial_days)) if auto_trial else None
        self.conn.execute(
            """INSERT INTO clients(
                client_id,public_key_b64,label,status,trial_expires,
                license_expires,created_at,last_seen,metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                client_id, public_key_b64, label, status, trial_expires,
                None, iso(now), iso(now), json.dumps(metadata),
            ),
        )
        self.conn.commit()
        self.event("client_enrolled", client_id, {"status": status})
        return self._row(client_id) or {}

    def client(self, client_id: str) -> dict[str, Any] | None:
        return self._row(client_id)

    def touch(self, client_id: str) -> None:
        self.conn.execute(
            "UPDATE clients SET last_seen=? WHERE client_id=?",
            (iso(), client_id),
        )
        self.conn.commit()

    def consume_nonce(self, client_id: str, nonce: str, now_epoch: int) -> bool:
        self.conn.execute(
            "DELETE FROM request_nonces WHERE seen_at < ?",
            (now_epoch - 900,),
        )
        try:
            self.conn.execute(
                "INSERT INTO request_nonces(client_id,nonce,seen_at) VALUES(?,?,?)",
                (client_id, nonce, now_epoch),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def event(
        self, event_type: str, client_id: str | None, detail: dict[str, Any]
    ) -> None:
        self.conn.execute(
            "INSERT INTO server_events(created_at,event_type,client_id,detail_json) VALUES(?,?,?,?)",
            (iso(), event_type, client_id, json.dumps(detail)),
        )
        self.conn.commit()

    def license_state(self, client: dict[str, Any]) -> dict[str, Any]:
        now = utcnow()
        status = str(client.get("status") or "free")
        trial_expires = client.get("trial_expires")
        license_expires = client.get("license_expires")
        active = False
        mode = "free"
        expires_at = None

        if status == "blocked":
            mode = "blocked"
        elif status == "licensed":
            if not license_expires:
                active = True
                mode = "licensed"
            else:
                expires = datetime.fromisoformat(str(license_expires))
                active = expires > now
                mode = "licensed" if active else "free"
                expires_at = str(license_expires)
        elif status == "trial" and trial_expires:
            expires = datetime.fromisoformat(str(trial_expires))
            active = expires > now
            mode = "trial" if active else "free"
            expires_at = str(trial_expires)

        return {
            "active": active,
            "mode": mode,
            "expires_at": expires_at,
        }

    def count_clients(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM clients").fetchone()
        return int(row["n"] if row else 0)


class KnowledgeStore:
    def __init__(
        self,
        compiled_path: str | Path,
        pack_root: str | Path,
        master_path: str | Path | None = None,
        generated_protocol_path: str | Path | None = None,
    ) -> None:
        self.compiled_path = Path(compiled_path)
        self.pack_root = Path(pack_root)
        self.master_path = Path(master_path) if master_path else None
        self.generated_protocol_path = (
            Path(generated_protocol_path)
            if generated_protocol_path
            else None
        )
        self.data: dict[str, Any] = {}
        self.master: dict[str, Any] = {}
        self.diseases: dict[str, dict[str, Any]] = {}
        self.protocols: dict[str, list[dict[str, Any]]] = {}
        self.generated_protocol_stats: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        self.data = json.loads(self.compiled_path.read_text(encoding="utf-8"))
        if self.master_path and self.master_path.exists():
            self.master = json.loads(self.master_path.read_text(encoding="utf-8"))
        else:
            self.master = {}
        self.diseases = {
            str(item["disease_id"]): item
            for item in self.data.get("diseases", [])
            if isinstance(item, dict) and item.get("disease_id")
        }
        self.aliases = {
            str(old): str(new)
            for old, new in (self.data.get("disease_aliases") or {}).items()
        }
        protocols: dict[str, list[dict[str, Any]]] = {}
        cards_dir = self.pack_root / "cards"
        for path in sorted(cards_dir.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict) or not raw.get("disease_id"):
                continue
            raw.pop("_source_file", None)
            raw["source_evidence"] = []
            protocols.setdefault(str(raw["disease_id"]), []).append(raw)

        self.generated_protocol_stats = {}
        if (
            self.generated_protocol_path
            and self.generated_protocol_path.exists()
        ):
            generated = json.loads(
                self.generated_protocol_path.read_text(encoding="utf-8")
            )
            self.generated_protocol_stats = dict(
                generated.get("stats") or {}
            )
            seen_ids = {
                str((card.get("protocol") or {}).get("id"))
                for cards in protocols.values()
                for card in cards
                if isinstance(card, dict)
            }
            for raw in generated.get("protocols") or []:
                if not isinstance(raw, dict) or not raw.get("disease_id"):
                    continue
                generated_status = str(
                    (raw.get("protocol") or {}).get("status") or ""
                )
                if generated_status not in {"ACTIVE", "WATCH", "MANUAL"}:
                    continue
                protocol_id = str(
                    (raw.get("protocol") or {}).get("id") or ""
                )
                if not protocol_id or protocol_id in seen_ids:
                    continue
                seen_ids.add(protocol_id)
                card = dict(raw)
                card["source_evidence"] = []
                protocols.setdefault(
                    str(card["disease_id"]), []
                ).append(card)
        self.protocols = protocols

    def disease(self, disease_id: str) -> dict[str, Any] | None:
        resolved = self.aliases.get(disease_id, disease_id)
        return self.diseases.get(resolved)

    def match(self, request: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
        component = clean_text(request.get("component"), 80).lower()
        query = set()
        query |= token_set(request.get("symptoms"))
        query |= token_set(request.get("fingerprints"))
        query |= token_set(request.get("evidence"))
        scored: list[tuple[float, dict[str, Any]]] = []
        for disease in self.diseases.values():
            disease_components = {
                clean_text(disease.get("component"), 80).lower(),
                *{
                    clean_text(value, 80).lower()
                    for value in (disease.get("component_aliases") or [])
                },
            }
            if component and component not in disease_components:
                continue
            target = set()
            target |= token_set(disease.get("title"))
            target |= token_set(disease.get("root_cause"))
            target |= token_set(disease.get("fingerprints"))
            if not query or not target:
                score = 0.0
            else:
                score = len(query & target) / max(1, len(query))
            if score >= 0.20:
                scored.append((score, disease))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "disease_id": item["disease_id"],
                "title": item.get("title"),
                "component": item.get("component"),
                "diagnosis_status": item.get("diagnosis_status"),
                "score": round(score, 4),
            }
            for score, item in scored[:limit]
        ]

    def _rank_text_items(
        self,
        items: list[dict[str, Any]],
        query: set[str],
        *,
        text_fields: tuple[str, ...],
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        ranked: list[tuple[float, dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            tokens: set[str] = set()
            for field in text_fields:
                tokens |= token_set(item.get(field))
            if not query or not tokens:
                continue
            score = len(query & tokens) / max(1, len(query))
            if score >= 0.08:
                ranked.append((score, item))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in ranked[:limit]]

    def auxiliary_guidance(self, disease: dict[str, Any]) -> dict[str, Any]:
        query = set()
        query |= token_set(disease.get("title"))
        query |= token_set(disease.get("root_cause"))
        component = clean_text(disease.get("component"), 80).lower()
        component_tokens = token_set(component)

        def component_scoped(
            items: list[dict[str, Any]],
            fields: tuple[str, ...],
            limit: int = 3,
        ) -> list[dict[str, Any]]:
            ranked: list[tuple[float, dict[str, Any]]] = []
            for item in items:
                item_tokens: set[str] = set()
                for field in fields:
                    item_tokens |= token_set(item.get(field))
                if component_tokens and not (component_tokens & item_tokens):
                    continue
                score = len(query & item_tokens) / max(1, len(query))
                if score >= 0.08:
                    ranked.append((score, item))
            ranked.sort(key=lambda pair: pair[0], reverse=True)
            return [item for _, item in ranked[:limit]]

        rules = component_scoped(
            list(self.master.get("diagnostic_rules") or []),
            ("rule",),
        )
        dont_do = component_scoped(
            list(self.master.get("dont_do") or []),
            ("rule",),
        )

        wanted_patterns = set(disease.get("pattern_refs") or [])
        patterns = [
            item
            for item in (self.master.get("recurring_patterns") or [])
            if isinstance(item, dict)
            and str(item.get("id") or "") in wanted_patterns
        ][:5]

        return {
            "diagnostic_rules": [
                {"id": item.get("id"), "rule": item.get("rule")}
                for item in rules
            ],
            "dont_do": [
                {"id": item.get("id"), "rule": item.get("rule")}
                for item in dont_do
            ],
            "related_patterns": [
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "lesson": item.get("lesson"),
                }
                for item in patterns
            ],
        }

    def recommendations(self, disease: dict[str, Any]) -> dict[str, Any]:
        candidates = disease.get("protocol_candidates") or []
        checks: list[str] = []
        actions: list[str] = []
        for candidate in candidates[:3]:
            for check in candidate.get("checks") or []:
                text = clean_text(check, 500)
                if text and text not in checks:
                    checks.append(text)
            action = clean_text(candidate.get("action"), 1000)
            if action and action not in actions:
                actions.append(action)
        return {
            "diagnosis": clean_text(disease.get("title"), 500),
            "root_cause": clean_text(disease.get("root_cause"), 1000),
            "recommended_checks": checks[:8],
            "possible_actions": actions[:3],
            "auxiliary": self.auxiliary_guidance(disease),
            "note": "Written guidance only; no executable protocol was issued.",
        }

    def protocol_cards(self, disease_id: str) -> list[dict[str, Any]]:
        resolved = self.aliases.get(disease_id, disease_id)
        return list(self.protocols.get(resolved, []))


class DoctorServer:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.db = ServerDB(config["database_path"])
        self.db.initialize()
        self.journal = CaseJournal(config["database_path"])
        self.command_bridge = ClientCommandBridge(config["database_path"])
        self.journal_gate = asyncio.Lock()
        self.v2_ext = V2Extension(
            self,
            config["database_path"],
            str(Path(__file__).with_name("doctor_v2_schema.sql")),
        )
        self._dispatch_tasks: set[asyncio.Task[Any]] = set()
        self._http: ClientSession | None = None
        self.knowledge = KnowledgeStore(
            config["knowledge_path"],
            config["protocol_pack_root"],
            config.get("master_knowledge_path"),
            config.get("generated_protocol_path"),
        )
        self.signing_key = serialization.load_pem_private_key(
            Path(config["signing_private_key"]).read_bytes(),
            password=None,
        )
        self._knowledge_mtime_ns = Path(
            config["knowledge_path"]
        ).stat().st_mtime_ns
        if not isinstance(self.signing_key, Ed25519PrivateKey):
            raise RuntimeError("server signing key must be Ed25519")

    def signed(self, payload: dict[str, Any]) -> web.Response:
        body = safe_structured(payload)
        signature = self.signing_key.sign(canonical_json(body))
        return web.json_response({
            "algorithm": "ed25519",
            "key_id": "server-v1",
            "payload": body,
            "signature": b64e(signature),
        })

    def remote_allowed(self, request: web.Request) -> bool:
        remote = request.remote or ""
        try:
            addr = ipaddress.ip_address(remote)
        except ValueError:
            return False
        return any(addr in network for network in ALLOWED_NETWORKS)

    async def require_local(self, request: web.Request) -> None:
        if not self.remote_allowed(request):
            raise web.HTTPForbidden(text="network not allowed")

    async def authenticated_body(
        self, request: web.Request
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        await self.require_local(request)
        raw = await request.read()
        if len(raw) > MAX_BODY:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY, actual_size=len(raw))

        client_id = clean_text(request.headers.get("X-Suzie-Client-ID"), 128)
        timestamp_text = clean_text(request.headers.get("X-Suzie-Timestamp"), 32)
        nonce = clean_text(request.headers.get("X-Suzie-Nonce"), 128)
        signature_b64 = clean_text(request.headers.get("X-Suzie-Signature"), 256)
        if not CLIENT_ID_RE.fullmatch(client_id):
            raise web.HTTPUnauthorized(text="invalid client id")
        try:
            timestamp = int(timestamp_text)
        except ValueError as exc:
            raise web.HTTPUnauthorized(text="invalid timestamp") from exc
        now_epoch = int(time.time())
        skew = int(self.config["max_clock_skew_seconds"])
        if abs(now_epoch - timestamp) > skew:
            raise web.HTTPUnauthorized(text="request timestamp outside allowed skew")
        if len(nonce) < 16:
            raise web.HTTPUnauthorized(text="invalid nonce")

        client = self.db.client(client_id)
        if not client:
            raise web.HTTPUnauthorized(text="client not enrolled")
        if not self.db.consume_nonce(client_id, nonce, now_epoch):
            raise web.HTTPUnauthorized(text="replayed nonce")

        try:
            public = Ed25519PublicKey.from_public_bytes(
                b64d(str(client["public_key_b64"]))
            )
            signature = b64d(signature_b64)
            message = (
                f"{timestamp}\n{nonce}\n{hashlib.sha256(raw).hexdigest()}"
            ).encode("utf-8")
            public.verify(signature, message)
        except Exception as exc:
            raise web.HTTPUnauthorized(text="invalid request signature") from exc

        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as exc:
            raise web.HTTPBadRequest(text="invalid JSON") from exc
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="JSON body must be an object")
        self.db.touch(client_id)
        return safe_structured(body), client

    async def require_doctor_operator(self, request: web.Request) -> None:
        await self.require_local(request)
        token_path = Path(str(self.config["doctor_operator_token_path"]))
        expected = token_path.read_text(encoding="utf-8").strip()
        auth = str(request.headers.get("Authorization") or "")
        supplied = auth[7:].strip() if auth.startswith("Bearer ") else ""
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise web.HTTPUnauthorized(text="doctor operator authorization required")

    async def doctor_json_body(self, request: web.Request) -> dict[str, Any]:
        await self.require_doctor_operator(request)
        try:
            body = await request.json()
        except Exception as exc:
            raise web.HTTPBadRequest(text="invalid JSON body") from exc
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="JSON body must be an object")
        return body

    @staticmethod
    def _journal_http_error(exc: Exception) -> web.HTTPException:
        if isinstance(exc, JournalNotFound):
            return web.HTTPNotFound(text=str(exc))
        if isinstance(exc, JournalAuthError):
            return web.HTTPUnauthorized(text=str(exc))
        if isinstance(exc, JournalConflict):
            return web.HTTPConflict(text=str(exc))
        return web.HTTPInternalServerError(text=f"{type(exc).__name__}: {exc}")

    def _case_source_key(self, client_id: str, body: dict[str, Any]) -> str:
        problem_key = clean_text(body.get("problem_key"), 512)
        evidence = body.get("evidence")
        if not problem_key and isinstance(evidence, dict):
            problem_key = clean_text(evidence.get("problem_key"), 512)
        if problem_key:
            return f"problem:{problem_key}"
        request_id = clean_text(body.get("request_id"), 128)
        if request_id:
            return f"request:{request_id}"
        stable = safe_structured(
            {
                "client_id": client_id,
                "component": body.get("component"),
                "symptoms": body.get("symptoms"),
                "fingerprints": body.get("fingerprints"),
                "evidence": body.get("evidence"),
                "disease_id": body.get("disease_id"),
                "confirmed_disease_id": body.get("confirmed_disease_id"),
            }
        )
        return "fingerprint:" + hashlib.sha256(canonical_json(stable)).hexdigest()

    async def escalate_to_suzie(
        self,
        *,
        client_id: str,
        body: dict[str, Any],
        diagnosis_payload: dict[str, Any],
        reason: str,
        disease_id: str | None = None,
        actor: str = "doctor_server",
    ) -> dict[str, Any]:
        problem = safe_structured(
            {
                "reason": reason,
                "diagnostic_request": body,
                "diagnosis": diagnosis_payload,
            }
        )
        component = clean_text(body.get("component"), 80) or "unknown"
        title = ""
        if isinstance(diagnosis_payload.get("disease"), dict):
            title = clean_text(diagnosis_payload["disease"].get("title"), 180)
        summary = f"{component}: {title or reason}"
        source_request_id = clean_text(body.get("request_id"), 128) or None
        source_key = self._case_source_key(client_id, body)
        async with self.journal_gate:
            case, created = self.journal.escalate(
                client_id=client_id,
                source_key=source_key,
                source_request_id=source_request_id,
                summary=summary,
                problem=problem if isinstance(problem, dict) else {},
                disease_id=disease_id,
                priority=50,
                actor=actor,
            )
            stats = self.journal.stats()
        self.db.event(
            "suzie_case_created" if created else "suzie_case_refreshed",
            client_id,
            {
                "case_id": case["case_id"],
                "case_ref": case["case_ref"],
                "reason": reason,
            },
        )
        return {"created": created, "case": case, "queue": stats}

    def _dialog_id_from_url(self, url: str) -> str | None:
        match = re.search(r"/c/([^/?#]+)", str(url or ""))
        if not match:
            return None
        value = unquote(match.group(1))
        if not value or value.startswith("local-chatgpt:"):
            return None
        return value

    async def _lookup_final_dialog(
        self, tab_id: str
    ) -> tuple[str, str] | None:
        if not self._http:
            return None
        try:
            async with self._http.get(
                str(self.config["cdp_list_url"]),
                timeout=ClientTimeout(total=5),
            ) as response:
                if response.status != 200:
                    return None
                pages = await response.json()
        except Exception:
            return None
        for page in pages if isinstance(pages, list) else []:
            if str(page.get("id") or "") != str(tab_id):
                continue
            url = str(page.get("url") or "")
            dialog_id = self._dialog_id_from_url(url)
            if dialog_id:
                return dialog_id, url
        return None

    async def _wait_final_dialog(
        self, tab_id: str, timeout_seconds: float = 25.0
    ) -> tuple[str, str] | None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            found = await self._lookup_final_dialog(tab_id)
            if found:
                return found
            await asyncio.sleep(0.35)
        return None

    async def _close_web_dialog_later(
        self,
        dialog_id: str,
        delay_seconds: float = 12.0,
    ) -> None:
        """Close one resolved Web Doctor tab after tool response can return."""
        if not dialog_id:
            return
        await asyncio.sleep(max(1.0, float(delay_seconds)))
        if not self._http:
            return
        try:
            async with self._http.get(
                str(self.config["cdp_list_url"]),
                timeout=ClientTimeout(total=5),
            ) as response:
                if response.status != 200:
                    return
                pages = await response.json()
            page_id = None
            for page in pages if isinstance(pages, list) else []:
                if page.get("type") != "page":
                    continue
                url = str(page.get("url") or "")
                if f"/c/{dialog_id}" in url:
                    page_id = str(page.get("id") or "")
                    break
            if not page_id:
                return
            close_url = str(self.config["cdp_list_url"]).replace(
                "/json/list",
                f"/json/close/{page_id}",
            )
            async with self._http.get(
                close_url,
                timeout=ClientTimeout(total=5),
            ) as response:
                await response.read()
            self.db.event(
                "web_doctor_dialog_closed",
                None,
                {"dialog_id": dialog_id, "tab_id": page_id},
            )
        except Exception as exc:
            self.db.event(
                "web_doctor_dialog_close_error",
                None,
                {
                    "dialog_id": dialog_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    async def _call_lab_run(
        self, *, method: str, text: str, target: str = "project",
        target_url: str = ""
    ) -> dict[str, Any]:
        if not self._http:
            raise RuntimeError("dispatcher HTTP session unavailable")
        url = str(self.config["call_lab_url"]).rstrip("/") + "/api/run"
        body = {"method": method, "target": target, "text": text}
        if target_url:
            body["target_url"] = target_url
        async with self._http.post(
            url,
            json=body,
            timeout=ClientTimeout(total=10),
        ) as response:
            data = await response.json()
            if response.status not in (200, 202) or not data.get("ok"):
                raise RuntimeError(f"call lab rejected dispatch: {data}")
            return dict(data.get("job") or {})

    async def _call_lab_job(
        self, job_id: str
    ) -> dict[str, Any] | None:
        if not self._http:
            return None
        url = str(self.config["call_lab_url"]).rstrip("/") + "/api/status"
        try:
            async with self._http.get(
                url,
                timeout=ClientTimeout(total=8),
            ) as response:
                data = await response.json()
        except Exception:
            return None
        for job in data.get("jobs") or []:
            if str(job.get("job_id") or "") == str(job_id):
                return dict(job)
        return None

    async def _dispatch_reserved_web(
        self, reservation: dict[str, Any]
    ) -> None:
        case = reservation["case"]
        case_id = int(case["case_id"])
        session_id = str(reservation["session_id"])
        ui_sent = False
        v2_field_slot = self.v2_ext.reserve_field(case_id)
        if not v2_field_slot:
            async with self.journal_gate:
                self.journal.fail_dispatch(
                    case_id=case_id,
                    session_id=session_id,
                    reason="Doctor v2 FIELD_SUZIE capacity unavailable",
                )
            return
        try:
            job = await self._call_lab_run(
                method="cdp",
                text=f"CASE #{case_id}",
            )
            job_id = str(job.get("job_id") or "")
            if not job_id:
                raise RuntimeError("call lab returned no job_id")

            deadline = time.monotonic() + 100
            last_job: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                current = await self._call_lab_job(job_id)
                if current:
                    last_job = current
                    state = str(current.get("state") or "")
                    if state == "failed":
                        raise RuntimeError(
                            f"CDP dispatch failed: {current.get('detail')}"
                        )
                    if state == "submitted":
                        detail = current.get("detail") or {}
                        tab_id = str(detail.get("tab_id") or "")
                        transient_url = str(detail.get("url") or "")
                        if not tab_id:
                            raise RuntimeError("submitted job has no tab_id")
                        async with self.journal_gate:
                            self.journal.mark_dispatch_progress(
                                case_id=case_id,
                                session_id=session_id,
                                dispatch_job_id=job_id,
                                tab_id=tab_id,
                                transient_url=transient_url,
                            )
                        ui_sent = True

                        final = await self._wait_final_dialog(tab_id, 30)
                        if not final:
                            self.db.event(
                                "web_dialog_id_pending",
                                case.get("client_id"),
                                {
                                    "case_id": case_id,
                                    "session_id": session_id,
                                    "job_id": job_id,
                                    "tab_id": tab_id,
                                },
                            )
                            return

                        dialog_id, conversation_url = final
                        async with self.journal_gate:
                            self.journal.finish_dispatch(
                                case_id=case_id,
                                session_id=session_id,
                                dispatch_job_id=job_id,
                                dialog_id=dialog_id,
                                conversation_url=conversation_url,
                            )
                        self.v2_ext.bind_field_dialog(case_id, dialog_id)
                        self.db.event(
                            "web_doctor_dispatched",
                            case.get("client_id"),
                            {
                                "case_id": case_id,
                                "session_id": session_id,
                                "dialog_id": dialog_id,
                                "doctor_v2_field_slot": v2_field_slot,
                            },
                        )
                        return
                await asyncio.sleep(0.5)

            raise TimeoutError(f"dispatch timeout; last_job={last_job}")
        except Exception as exc:
            if ui_sent:
                # Never requeue after the message was already sent: duplicate
                # treatment is worse than a stuck case. Reconciliation will bind
                # the final dialog ID later.
                self.db.event(
                    "web_dispatch_post_send_error",
                    case.get("client_id"),
                    {
                        "case_id": case_id,
                        "session_id": session_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                return
            self.v2_ext.release_field(case_id)
            async with self.journal_gate:
                self.journal.fail_dispatch(
                    case_id=case_id,
                    session_id=session_id,
                    reason=f"{type(exc).__name__}: {exc}",
                )

    async def _reconcile_starting_dialogs(self) -> None:
        async with self.journal_gate:
            starting = self.journal.starting_sessions()
        for session in starting:
            detail = session.get("detail") or {}
            if not detail.get("ui_sent"):
                continue
            tab_id = str(detail.get("tab_id") or "")
            current_case_id = session.get("current_case_id")
            if not tab_id or not current_case_id:
                continue
            final = await self._lookup_final_dialog(tab_id)
            if not final:
                continue
            dialog_id, conversation_url = final
            try:
                async with self.journal_gate:
                    self.journal.finish_dispatch(
                        case_id=int(current_case_id),
                        session_id=str(session["session_id"]),
                        dispatch_job_id=str(session.get("dispatch_job_id") or ""),
                        dialog_id=dialog_id,
                        conversation_url=conversation_url,
                    )
                self.v2_ext.reserve_field(int(current_case_id))
                self.v2_ext.bind_field_dialog(int(current_case_id), dialog_id)
            except JournalConflict:
                continue

    async def doctor_dispatch_loop(self) -> None:
        while True:
            try:
                async with self.journal_gate:
                    self.journal.reap_stale_sessions(
                        max_requeues=int(self.config.get("stale_session_max_requeues", 2)),
                        dispatch_timeout_seconds=int(self.config.get("dispatch_stale_seconds", 300)),
                        assigned_timeout_seconds=int(self.config.get("assigned_stale_seconds", 1800)),
                    )
                await self._reconcile_starting_dialogs()
                if bool(self.config.get("web_dispatch_enabled", False)):
                    while True:
                        async with self.journal_gate:
                            reservation = self.journal.reserve_web_dispatch(
                                max_doctors=int(
                                    self.config.get("max_ai_doctors", 5)
                                )
                            )
                        if not reservation:
                            break
                        task = asyncio.create_task(
                            self._dispatch_reserved_web(reservation),
                            name=f"doctor_web_dispatch_{reservation['case']['case_id']}",
                        )
                        self._dispatch_tasks.add(task)
                        task.add_done_callback(self._dispatch_tasks.discard)
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.db.event(
                    "doctor_dispatch_loop_error",
                    None,
                    {"error": f"{type(exc).__name__}: {exc}"},
                )
                await asyncio.sleep(2)

    async def doctor_capabilities(self, request: web.Request) -> web.Response:
        await self.require_doctor_operator(request)
        return web.json_response(
            {
                "ok": True,
                "connector_contract": "Suzie Doctor Connector Core",
                "journal_gate": "single_fair_async_gate",
                "max_ai_doctors": int(self.config.get("max_ai_doctors", 5)),
                "web": {
                    "enabled": bool(self.config.get("web_dispatch_enabled", False)),
                    "primary": True,
                    "method": "cdp",
                    "project_dispatch": True,
                },
                "api": {
                    "enabled": bool(self.config.get("api_transport_enabled", False)),
                    "ready_for_adapter": True,
                },
                "case_actions": [
                    "queue",
                    "case.get",
                    "case.claim",
                    "case.heartbeat",
                    "case.stage",
                    "case.complete_next",
                ],
                "doctor_v2": {
                    "enabled": True,
                    "roles": {"FIELD_SUZIE": 4, "HOUSE": 1, "WILSON": 1},
                    "web_policy": {
                        "session_max_minutes": 10,
                        "dialog_max_sessions": 10,
                        "natural_completion_closes_dialog": True,
                    },
                    "role_actions": [
                        "v2.state",
                        "house.job.get",
                        "house.decision",
                        "wilson.job.get",
                        "wilson.complete",
                    ],
                },
            }
        )

    async def doctor_queue(self, request: web.Request) -> web.Response:
        await self.require_doctor_operator(request)
        async with self.journal_gate:
            payload = {
                "ok": True,
                "stats": self.journal.stats(),
                "cases": self.journal.list_active(),
                "sessions": self.journal.active_sessions(),
            }
        return web.json_response(payload)

    async def doctor_case_get(self, request: web.Request) -> web.Response:
        await self.require_doctor_operator(request)
        try:
            case_id = int(request.match_info["case_id"])
            async with self.journal_gate:
                case = self.journal.get_case(case_id)
            return web.json_response({"ok": True, "case": case})
        except Exception as exc:
            raise self._journal_http_error(exc)

    async def doctor_case_escalate(self, request: web.Request) -> web.Response:
        body = await self.doctor_json_body(request)
        client_id = clean_text(body.get("client_id"), 128)
        if not self.db.client(client_id):
            raise web.HTTPBadRequest(text="unknown client_id")
        source_key = clean_text(body.get("source_key"), 200)
        if not source_key:
            raise web.HTTPBadRequest(text="source_key required")
        problem = safe_structured(body.get("problem") or {})
        if not isinstance(problem, dict):
            problem = {}
        try:
            async with self.journal_gate:
                case, created = self.journal.escalate(
                    client_id=client_id,
                    source_key=source_key,
                    source_request_id=clean_text(
                        body.get("source_request_id"), 128
                    ) or None,
                    summary=clean_text(body.get("summary"), 240),
                    problem=problem,
                    disease_id=clean_text(body.get("disease_id"), 160) or None,
                    priority=int(body.get("priority") or 50),
                    actor="operator",
                )
                stats = self.journal.stats()
            return web.json_response(
                {"ok": True, "created": created, "case": case, "queue": stats}
            )
        except Exception as exc:
            raise self._journal_http_error(exc)

    async def doctor_case_claim(self, request: web.Request) -> web.Response:
        body = await self.doctor_json_body(request)
        try:
            case_id = int(request.match_info["case_id"])
            async with self.journal_gate:
                result = self.journal.claim(
                    case_id=case_id,
                    actor="doctor_suzie",
                    lease_seconds=int(
                        self.config.get("claim_lease_seconds", 900)
                    ),
                )
            return web.json_response({"ok": True, "case": result})
        except Exception as exc:
            raise self._journal_http_error(exc)

    async def doctor_case_heartbeat(self, request: web.Request) -> web.Response:
        body = await self.doctor_json_body(request)
        try:
            case_id = int(request.match_info["case_id"])
            async with self.journal_gate:
                result = self.journal.heartbeat(
                    case_id=case_id,
                    claim_token=str(body.get("claim_token") or ""),
                    lease_seconds=int(
                        self.config.get("claim_lease_seconds", 900)
                    ),
                    actor="doctor_suzie",
                )
            return web.json_response({"ok": True, **result})
        except Exception as exc:
            raise self._journal_http_error(exc)

    async def doctor_case_stage(self, request: web.Request) -> web.Response:
        body = await self.doctor_json_body(request)
        try:
            case_id = int(request.match_info["case_id"])
            async with self.journal_gate:
                result = self.journal.stage(
                    case_id=case_id,
                    claim_token=str(body.get("claim_token") or ""),
                    stage=str(body.get("stage") or ""),
                    actor="doctor_suzie",
                )
            return web.json_response({"ok": True, "case": result})
        except Exception as exc:
            raise self._journal_http_error(exc)

    async def doctor_case_tool(self, request: web.Request) -> web.Response:
        body = await self.doctor_json_body(request)
        try:
            case_id = int(request.match_info["case_id"])
            claim_token = str(body.get("claim_token") or "")
            tool_name = clean_text(body.get("tool_name"), 120)
            arguments = safe_structured(body.get("arguments") or {})
            if not isinstance(arguments, dict):
                raise JournalConflict("arguments must be an object")
            if tool_name not in CLIENT_COMMAND_TOOLS:
                raise JournalConflict(
                    f"tool not allowed on client command bridge: {tool_name}"
                )
            # Human confirmation is never accepted from model arguments.
            if (
                tool_name == "doctor.diagnose"
                and "explicit_confirmation" in arguments
            ):
                raise JournalConflict(
                    "explicit_confirmation is transport-owned"
                )

            async with self.journal_gate:
                case = self.journal.authorize_claim(
                    case_id=case_id,
                    claim_token=claim_token,
                )
                command = self.command_bridge.enqueue(
                    client_id=str(case["client_id"]),
                    case_id=case_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    trusted_context={
                        "source": "doctor_ai",
                        "case_id": case_id,
                        "human_confirmation_verified": False,
                    },
                )
                self.db.event(
                    "doctor_client_command_queued",
                    str(case["client_id"]),
                    {
                        "case_id": case_id,
                        "command_id": command["command_id"],
                        "tool_name": tool_name,
                    },
                )
            return web.json_response(
                {
                    "ok": True,
                    "command": command,
                },
                status=202,
            )
        except Exception as exc:
            if isinstance(exc, CommandBridgeError):
                raise web.HTTPConflict(text=str(exc))
            raise self._journal_http_error(exc)

    async def doctor_command_get(self, request: web.Request) -> web.Response:
        await self.require_doctor_operator(request)
        command_id = clean_text(request.match_info["command_id"], 128)
        try:
            command = self.command_bridge.get(command_id)
            return web.json_response({"ok": True, "command": command})
        except CommandBridgeError as exc:
            raise web.HTTPNotFound(text=str(exc))

    async def client_command_poll(self, request: web.Request) -> web.Response:
        _, client = await self.authenticated_body(request)
        client_id = str(client["client_id"])
        try:
            command = self.command_bridge.poll(
                client_id=client_id,
                lease_seconds=120,
            )
        except Exception as exc:
            self.db.event(
                "client_command_poll_error",
                client_id,
                {"error": f"{type(exc).__name__}: {exc}"},
            )
            raise web.HTTPInternalServerError(text="command poll failed")
        return self.signed(
            {
                "result": "COMMAND" if command else "NO_COMMAND",
                "client_id": client_id,
                "command": command,
            }
        )

    async def client_command_result(self, request: web.Request) -> web.Response:
        body, client = await self.authenticated_body(request)
        client_id = str(client["client_id"])
        command_id = clean_text(body.get("command_id"), 128)
        result = safe_structured(body.get("result") or {})
        if not isinstance(result, dict):
            result = {"value": result}
        error = clean_text(body.get("error"), 2000) or None
        if not command_id:
            raise web.HTTPBadRequest(text="command_id required")
        try:
            command = self.command_bridge.finish(
                client_id=client_id,
                command_id=command_id,
                result=result,
                error=error,
            )
        except CommandBridgeError as exc:
            raise web.HTTPConflict(text=str(exc))
        self.db.event(
            "doctor_client_command_finished",
            client_id,
            {
                "case_id": command.get("case_id"),
                "command_id": command_id,
                "tool_name": command.get("tool_name"),
                "status": command.get("status"),
            },
        )
        return self.signed(
            {
                "result": "RECORDED",
                "client_id": client_id,
                "command_id": command_id,
                "status": command.get("status"),
            }
        )

    async def doctor_case_complete_next(
        self, request: web.Request
    ) -> web.Response:
        body = await self.doctor_json_body(request)
        result_payload = safe_structured(body.get("result") or {})
        if not isinstance(result_payload, dict):
            result_payload = {}
        try:
            case_id = int(request.match_info["case_id"])
            async with self.journal_gate:
                before = self.journal.get_case(case_id)
                result = self.journal.complete_and_next(
                    case_id=case_id,
                    claim_token=str(body.get("claim_token") or ""),
                    outcome=str(body.get("outcome") or ""),
                    result=result_payload,
                    actor="doctor_suzie",
                    allow_handoff=False,
                )
            await self.v2_ext.field_finished(
                case_id,
                str(before.get("client_id") or ""),
                str(body.get("outcome") or ""),
                result_payload,
            )
            if result.get("close_dialog") and result.get("dialog_id"):
                task = asyncio.create_task(
                    self._close_web_dialog_later(
                        str(result["dialog_id"]),
                    ),
                    name=f"doctor_close_dialog_{case_id}",
                )
                self._dispatch_tasks.add(task)
                task.add_done_callback(self._dispatch_tasks.discard)
            return web.json_response({"ok": True, **result})
        except Exception as exc:
            raise self._journal_http_error(exc)

    async def health(self, request: web.Request) -> web.Response:
        await self.require_local(request)
        stats = self.knowledge.data.get("stats") or {}
        async with self.journal_gate:
            journal_stats = self.journal.stats()
        return web.json_response({
            "status": "ok",
            "version": SERVER_VERSION,
            "knowledge": {
                "incidents": stats.get("incidents"),
                "diseases": stats.get("diseases"),
                "protocol_candidates": stats.get("protocol_candidates"),
                "executable_protocols": stats.get("executable_protocols"),
                "families": stats.get("families"),
                "multi_incident_diseases": stats.get("multi_incident_diseases"),
                "unclassified_incidents": stats.get("unclassified_incidents"),
                "new_protocols_promotable": stats.get("new_protocols_promotable"),
                "confirmed_diseases": stats.get("confirmed_diseases"),
                "diseases_with_protocol_candidates": stats.get(
                    "diseases_with_protocol_candidates"
                ),
                "diseases_with_executable_protocols": stats.get(
                    "diseases_with_executable_protocols"
                ),
                "confirmed_without_treatment_knowledge": stats.get(
                    "confirmed_without_treatment_knowledge"
                ),
                "diagnostic_rules": len(
                    self.knowledge.master.get("diagnostic_rules") or []
                ),
                "dont_do": len(self.knowledge.master.get("dont_do") or []),
                "recurring_patterns": len(
                    self.knowledge.master.get("recurring_patterns") or []
                ),
                "quarantined_recipes": len(
                    self.knowledge.master.get("quarantined_recipes") or []
                ),
                "curation": self.knowledge.data.get("curation_summary") or {},
                "protocol_factory": self.knowledge.generated_protocol_stats,
            },
            "clients": self.db.count_clients(),
            "doctor_journal": {
                **journal_stats,
                "max_ai_doctors": int(self.config.get("max_ai_doctors", 5)),
                "web_dispatch_enabled": bool(
                    self.config.get("web_dispatch_enabled", False)
                ),
                "auto_escalate_to_suzie": bool(
                    self.config.get("auto_escalate_to_suzie", False)
                ),
                "api_transport_enabled": bool(
                    self.config.get("api_transport_enabled", False)
                ),
            },
            "doctor_v2": {
                **self.v2_ext.runtime.snapshot(),
                "dispatch_enabled": bool(
                    self.config.get("doctor_v2_dispatch_enabled", False)
                ),
            },
        })

    async def server_key(self, request: web.Request) -> web.Response:
        await self.require_local(request)
        public = self.signing_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return web.json_response({
            "algorithm": "ed25519",
            "key_id": "server-v1",
            "public_key_b64": b64e(public),
        })

    async def enroll(self, request: web.Request) -> web.Response:
        await self.require_local(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="JSON body must be an object")
        client_id = clean_text(body.get("client_id"), 128)
        public_key_b64 = clean_text(body.get("public_key_b64"), 128)
        label = clean_text(body.get("label"), 120)
        metadata = safe_structured(body.get("metadata") or {})
        if not CLIENT_ID_RE.fullmatch(client_id):
            raise web.HTTPBadRequest(text="invalid client_id")
        try:
            public_raw = b64d(public_key_b64)
            Ed25519PublicKey.from_public_bytes(public_raw)
        except Exception as exc:
            raise web.HTTPBadRequest(text="invalid Ed25519 public key") from exc

        client = self.db.enroll(
            client_id=client_id,
            public_key_b64=public_key_b64,
            label=label,
            metadata=metadata if isinstance(metadata, dict) else {},
            trial_days=int(self.config["trial_days"]),
            auto_trial=bool(self.config["auto_trial_enroll"]),
        )
        license_state = self.db.license_state(client)
        return self.signed({
            "result": "ENROLLED",
            "client_id": client_id,
            "license": license_state,
            "server_version": SERVER_VERSION,
        })

    async def license_status(self, request: web.Request) -> web.Response:
        _, client = await self.authenticated_body(request)
        return self.signed({
            "client_id": client["client_id"],
            "license": self.db.license_state(client),
        })

    def package_for(
        self,
        *,
        client_id: str,
        disease_id: str,
        card: dict[str, Any],
        execution_actor: str | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        ttl = int(self.config["execution_package_ttl_seconds"])
        protocol = card.get("protocol") or {}
        sanitized_card = {
            key: value
            for key, value in card.items()
            if key not in {"source_evidence", "_source_file"}
        }
        sanitized_card["source_evidence"] = []
        return {
            "package_id": str(uuid4()),
            "issued_at": iso(now),
            "expires_at": iso(now + timedelta(seconds=ttl)),
            "client_id": client_id,
            "disease_id": disease_id,
            "protocol_id": protocol.get("id"),
            "protocol_version": protocol.get("version"),
            "protocol_status": protocol.get("status"),
            "automation_class": card.get("automation_class"),
            "execution_actor": execution_actor,
            "card": sanitized_card,
        }

    async def diagnose(self, request: web.Request) -> web.Response:
        body, client = await self.authenticated_body(request)
        client_id = str(client["client_id"])
        license_state = self.db.license_state(client)
        confirmed_id = clean_text(body.get("confirmed_disease_id"), 160)
        requested_id = clean_text(body.get("disease_id"), 160)

        disease: dict[str, Any] | None = None
        confirmed = False
        if confirmed_id:
            disease = self.knowledge.disease(confirmed_id)
            confirmed = disease is not None
            if disease is None:
                raise web.HTTPBadRequest(text="confirmed_disease_id not found")
        elif requested_id:
            disease = self.knowledge.disease(requested_id)

        candidates = self.knowledge.match(body)
        candidate_selected = False
        if disease is None and candidates:
            best_id = str(candidates[0]["disease_id"])
            disease = self.knowledge.disease(best_id)
            candidate_selected = disease is not None

        payload: dict[str, Any] = {
            "result": "NO_MATCH",
            "request_id": clean_text(body.get("request_id"), 128) or str(uuid4()),
            "license": license_state,
            "confirmed": confirmed,
            "candidates": candidates,
            "execution_packages": [],
        }
        routing_intent = clean_text(body.get("routing_intent"), 80)
        v2_route = routing_intent in {
            "FAMILY_DOCTOR_PROTOCOL_OR_PATIENT_JOURNAL",
            "PATIENT_JOURNAL_HOUSE_REVIEW",
        }

        if disease is None:
            payload["message"] = (
                "No known Disease matched strongly enough. "
                "Collect more structured evidence and retry."
            )
            if v2_route:
                journal = self.v2_ext.runtime.journal_to_house(
                    client_id,
                    "diagnose",
                    {
                        "request_id": payload["request_id"],
                        "routing_intent": routing_intent,
                        "diagnosis_result": "NO_MATCH",
                        "evidence": safe_structured(body.get("evidence") or body),
                    },
                    event_type="OBSERVATION",
                    fingerprint=clean_text(body.get("fingerprint"), 200) or None,
                )
                payload["result"] = "PATIENT_JOURNAL_HOUSE"
                payload["patient_journal"] = journal
                self.db.event("doctor_v2_patient_journal", client_id, journal)
                return self.signed(payload)
            if (
                bool(self.config.get("auto_escalate_to_suzie", False))
                and bool(license_state["active"])
            ):
                escalation = await self.escalate_to_suzie(
                    client_id=client_id,
                    body=body,
                    diagnosis_payload=payload,
                    reason="NO_MATCH",
                )
                payload["suzie_case"] = {
                    "case_id": escalation["case"]["case_id"],
                    "case_ref": escalation["case"]["case_ref"],
                    "state": escalation["case"]["state"],
                    "created": escalation["created"],
                }
            self.db.event("diagnosis_no_match", client_id, {})
            return self.signed(payload)

        disease_id = str(disease["disease_id"])
        payload.update({
            "result": (
                "DIAGNOSIS_CONFIRMED"
                if confirmed
                else ("CANDIDATE_MATCH" if candidate_selected else "DIAGNOSIS_LOOKUP")
            ),
            "disease": {
                "disease_id": disease_id,
                "title": disease.get("title"),
                "component": disease.get("component"),
                "diagnosis_status": disease.get("diagnosis_status"),
                "confidence": disease.get("confidence"),
            },
            "recommendations": self.knowledge.recommendations(disease),
        })

        cards = self.knowledge.protocol_cards(disease_id)
        eligible_cards = cards
        if v2_route:
            eligible_cards = [
                card for card in cards
                if str((card.get("protocol") or {}).get("status") or "").upper()
                in {"ACTIVE", "APPROVED_ACTIVE"}
            ]
        if license_state["active"] and confirmed and eligible_cards:
            payload["execution_packages"] = [
                self.package_for(
                    client_id=client_id,
                    disease_id=disease_id,
                    card=card,
                    execution_actor=("family_doctor" if v2_route else None),
                )
                for card in eligible_cards
            ]
            payload["result"] = "PROTOCOL_AVAILABLE"
            if v2_route:
                payload["routing"] = "FAMILY_DOCTOR"
        elif cards and not confirmed:
            payload["message"] = (
                "A protocol exists, but the Disease is not yet confirmed. "
                "No executable package was issued."
            )
        elif cards and not license_state["active"]:
            payload["message"] = (
                "Diagnosis is available. An active license or trial is required "
                "for an executable protocol."
            )
        else:
            payload["message"] = (
                "Disease recognized, but no executable protocol is published yet."
            )

        if v2_route and payload.get("result") != "PROTOCOL_AVAILABLE":
            journal = self.v2_ext.runtime.journal_to_house(
                client_id,
                "diagnose",
                {
                    "request_id": payload["request_id"],
                    "routing_intent": routing_intent,
                    "diagnosis_result": payload.get("result"),
                    "disease_id": disease_id,
                    "confirmed": confirmed,
                    "recommendations": payload.get("recommendations"),
                    "evidence": safe_structured(body.get("evidence") or body),
                },
                event_type="OBSERVATION",
                fingerprint=clean_text(body.get("fingerprint"), 200) or None,
            )
            payload["result"] = "PATIENT_JOURNAL_HOUSE"
            payload["patient_journal"] = journal
            self.db.event("doctor_v2_patient_journal", client_id, journal)
            return self.signed(payload)

        suzie_review_required = bool(body.get("suzie_review_required", False))
        if (
            (payload.get("result") != "PROTOCOL_AVAILABLE" or suzie_review_required)
            and bool(self.config.get("auto_escalate_to_suzie", False))
            and bool(license_state["active"])
        ):
            reason = (
                "AUTONOMOUS_RISK_REVIEW"
                if payload.get("result") == "PROTOCOL_AVAILABLE"
                and suzie_review_required
                else str(payload.get("result") or "UNRESOLVED")
            )
            escalation = await self.escalate_to_suzie(
                client_id=client_id,
                body=body,
                diagnosis_payload=payload,
                reason=reason,
                disease_id=disease_id,
            )
            payload["suzie_case"] = {
                "case_id": escalation["case"]["case_id"],
                "case_ref": escalation["case"]["case_ref"],
                "state": escalation["case"]["state"],
                "created": escalation["created"],
            }

        self.db.event(
            "diagnosis",
            client_id,
            {
                "disease_id": disease_id,
                "confirmed": confirmed,
                "licensed": bool(license_state["active"]),
                "packages": len(payload["execution_packages"]),
            },
        )
        return self.signed(payload)

    async def reload_knowledge(self, request: web.Request) -> web.Response:
        body, client = await self.authenticated_body(request)
        if str(client.get("status")) != "licensed":
            raise web.HTTPForbidden(text="licensed client required")
        self.knowledge.reload()
        return self.signed({
            "result": "RELOADED",
            "stats": self.knowledge.data.get("stats") or {},
        })

    async def knowledge_watch_loop(self) -> None:
        path = Path(self.config["knowledge_path"])
        while True:
            try:
                mtime = path.stat().st_mtime_ns
                if mtime != self._knowledge_mtime_ns:
                    self.knowledge.reload()
                    self._knowledge_mtime_ns = mtime
                    self.db.event(
                        "knowledge_reloaded",
                        None,
                        {"stats": self.knowledge.data.get("stats") or {}},
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.db.event(
                    "knowledge_reload_error",
                    None,
                    {"error": f"{type(exc).__name__}: {exc}"},
                )
            await asyncio.sleep(30)

    async def on_startup(self, app: web.Application) -> None:
        self._http = ClientSession(timeout=ClientTimeout(total=20))
        app["knowledge_watch_task"] = asyncio.create_task(
            self.knowledge_watch_loop(),
            name="knowledge_watch",
        )
        app["doctor_dispatch_task"] = asyncio.create_task(
            self.doctor_dispatch_loop(),
            name="doctor_dispatch",
        )
        app["doctor_v2_task"] = asyncio.create_task(
            self.v2_ext.run(),
            name="doctor_v2_dispatch",
        )

    async def on_cleanup(self, app: web.Application) -> None:
        tasks = [
            app.get("knowledge_watch_task"),
            app.get("doctor_dispatch_task"),
            app.get("doctor_v2_task"),
            *list(self._dispatch_tasks),
        ]
        for task in tasks:
            if task:
                task.cancel()
        await asyncio.gather(
            *(task for task in tasks if task),
            return_exceptions=True,
        )
        if self._http:
            await self._http.close()
            self._http = None
        await self.v2_ext.close()

    def build_app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY)
        app.router.add_get("/health", self.health)
        app.router.add_get("/v1/server-key", self.server_key)
        app.router.add_post("/v1/enroll", self.enroll)
        app.router.add_post("/v1/license", self.license_status)
        app.router.add_post("/v1/diagnose", self.diagnose)
        app.router.add_post("/v1/knowledge/reload", self.reload_knowledge)

        # Suzie Doctor Connector-facing journal contract.
        app.router.add_get("/v1/doctor/capabilities", self.doctor_capabilities)
        app.router.add_get("/v1/doctor/queue", self.doctor_queue)
        app.router.add_post("/v1/doctor/case/escalate", self.doctor_case_escalate)
        app.router.add_get(
            "/v1/doctor/case/{case_id:\\d+}",
            self.doctor_case_get,
        )
        app.router.add_post(
            "/v1/doctor/case/{case_id:\\d+}/claim",
            self.doctor_case_claim,
        )
        app.router.add_post(
            "/v1/doctor/case/{case_id:\\d+}/heartbeat",
            self.doctor_case_heartbeat,
        )
        app.router.add_post(
            "/v1/doctor/case/{case_id:\\d+}/stage",
            self.doctor_case_stage,
        )
        app.router.add_post(
            "/v1/doctor/case/{case_id:\\d+}/tool",
            self.doctor_case_tool,
        )
        app.router.add_get(
            "/v1/doctor/command/{command_id}",
            self.doctor_command_get,
        )
        app.router.add_post(
            "/v1/doctor/case/{case_id:\\d+}/complete-next",
            self.doctor_case_complete_next,
        )

        # Exact-client pull bridge used by the installed Doctor App.
        app.router.add_post(
            "/v1/client/commands/poll",
            self.client_command_poll,
        )
        app.router.add_post(
            "/v1/client/commands/result",
            self.client_command_result,
        )

        # Doctor architecture v2 internal/operator contract.
        app.router.add_get("/v2/state", self.v2_ext.state)
        app.router.add_get("/internal/house/jobs/{job_id:\\d+}", self.v2_ext.house_get)
        app.router.add_post("/internal/house/jobs/{job_id:\\d+}/decision", self.v2_ext.house_decision)
        app.router.add_get("/internal/wilson/jobs/{job_id:\\d+}", self.v2_ext.wilson_get)
        app.router.add_post("/internal/wilson/jobs/{job_id:\\d+}/complete", self.v2_ext.wilson_complete)

        app.on_startup.append(self.on_startup)
        app.on_cleanup.append(self.on_cleanup)
        return app


def load_config(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("config root must be an object")
    return data


def make_ssl_context(config: dict[str, Any]) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config["tls_cert"], config["tls_key"])
    return context


def run_selftest(config: dict[str, Any]) -> int:
    import tempfile

    cases: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="suzie_doctor_server_test_") as tmp:
        test_cfg = dict(config)
        test_cfg["database_path"] = str(Path(tmp) / "test.sqlite3")
        server = DoctorServer(test_cfg)

        client_private = Ed25519PrivateKey.generate()
        client_public_raw = client_private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        client = server.db.enroll(
            client_id="selftest-client-001",
            public_key_b64=b64e(client_public_raw),
            label="selftest",
            metadata={"app": "selftest"},
            trial_days=30,
            auto_trial=bool(test_cfg["auto_trial_enroll"]),
        )
        initial_state = server.db.license_state(client)
        expected_active = bool(test_cfg["auto_trial_enroll"])
        cases.append({
            "id": "enrollment_license_policy",
            "pass": (
                initial_state["active"] is expected_active
                and initial_state["mode"]
                == ("trial" if expected_active else "free")
            ),
        })

        trial_client = dict(client)
        trial_client["status"] = "trial"
        trial_client["trial_expires"] = iso(
            utcnow() + timedelta(days=30)
        )
        cases.append({
            "id": "server_granted_trial_gate",
            "pass": server.db.license_state(trial_client)["active"] is True,
        })

        disease_ids = sorted(server.knowledge.diseases)
        cases.append({
            "id": "knowledge_loaded",
            "pass": (
                len(server.knowledge.diseases) > 0
                and int(server.knowledge.data.get("stats", {}).get("incidents") or 0)
                == 411
            ),
        })

        protocol_disease = next(
            (
                did for did in disease_ids
                if server.knowledge.protocol_cards(did)
            ),
            None,
        )
        cases.append({
            "id": "protocol_catalog_loaded",
            "pass": protocol_disease is not None,
        })

        generated_path = test_cfg.get("generated_protocol_path")
        generated = (
            json.loads(Path(generated_path).read_text(encoding="utf-8"))
            if generated_path and Path(generated_path).exists()
            else {}
        )
        generated_cards = [
            item
            for item in (generated.get("protocols") or [])
            if isinstance(item, dict)
        ]
        normalized_candidate_count = int(
            (server.knowledge.data.get("stats") or {}).get(
                "protocol_candidates"
            )
            or 0
        )
        cases.append({
            "id": "generated_protocol_catalog_complete",
            "pass": (
                int(generated.get("source_protocol_candidates") or 0)
                == normalized_candidate_count
                == len(generated_cards)
                and normalized_candidate_count > 0
            ),
        })
        publishable_ids = {
            str((item.get("protocol") or {}).get("id") or "")
            for item in generated_cards
            if str((item.get("protocol") or {}).get("status") or "")
            in {"ACTIVE", "WATCH", "MANUAL"}
        }
        loaded_ids = {
            str((card.get("protocol") or {}).get("id") or "")
            for cards in server.knowledge.protocols.values()
            for card in cards
            if isinstance(card.get("factory"), dict)
        }
        cases.append({
            "id": "generated_publish_filter_exact",
            "pass": loaded_ids == publishable_ids,
        })
        cases.append({
            "id": "generated_active_protocols_bounded",
            "pass": all(
                bool(item.get("treatment"))
                and str(item.get("automation_class") or "")
                in {"AUTO_SAFE", "CONFIRM_REQUIRED"}
                and bool((item.get("factory") or {}).get("complete_mapping"))
                for item in generated_cards
                if str((item.get("protocol") or {}).get("status") or "")
                == "ACTIVE"
            ),
        })
        generated_active = next(
            (
                item for item in generated_cards
                if str((item.get("protocol") or {}).get("status") or "")
                == "ACTIVE"
            ),
            None,
        )
        sanitized_generated = (
            safe_structured({"card": generated_active})
            if generated_active
            else {}
        )
        sanitized_card = (
            sanitized_generated.get("card")
            if isinstance(sanitized_generated, dict)
            else None
        )
        sanitized_diags = (
            sanitized_card.get("diagnostics")
            if isinstance(sanitized_card, dict)
            else []
        )
        sanitized_treatment = (
            sanitized_card.get("treatment")
            if isinstance(sanitized_card, dict)
            else []
        )
        cases.append({
            "id": "generated_nested_protocol_survives_sanitizer",
            "pass": bool(
                sanitized_diags
                and isinstance(sanitized_diags[0], dict)
                and sanitized_diags[0].get("primitive")
                and sanitized_treatment
                and isinstance(sanitized_treatment[0], dict)
                and sanitized_treatment[0].get("primitive")
            ),
        })

        if protocol_disease:
            card = server.knowledge.protocol_cards(protocol_disease)[0]
            package = server.package_for(
                client_id="selftest-client-001",
                disease_id=protocol_disease,
                card=card,
            )
            signed_payload = {
                "execution_packages": [package],
                "license": server.db.license_state(client),
            }
            signature = server.signing_key.sign(canonical_json(signed_payload))
            try:
                server.signing_key.public_key().verify(
                    signature, canonical_json(signed_payload)
                )
                signature_ok = True
            except Exception:
                signature_ok = False
            cases.append({
                "id": "server_signature_verifies",
                "pass": signature_ok,
            })
            cases.append({
                "id": "package_is_client_bound",
                "pass": package["client_id"] == "selftest-client-001",
            })
            cases.append({
                "id": "source_evidence_not_delivered",
                "pass": package["card"].get("source_evidence") == [],
            })

        free_client = dict(client)
        free_client["status"] = "free"
        cases.append({
            "id": "free_license_gate",
            "pass": server.db.license_state(free_client)["active"] is False,
        })

        # Doctor journal / dispatcher concurrency regression.
        journal = server.journal
        journal_case_ids = []
        for idx in range(1, 7):
            item, created = journal.escalate(
                client_id="selftest-client-001",
                source_key=f"journal-selftest-{idx}",
                source_request_id=f"journal-selftest-{idx}",
                summary=f"journal selftest {idx}",
                problem={"selftest": True, "index": idx},
                priority=50,
                actor="selftest",
            )
            if created:
                journal_case_ids.append(int(item["case_id"]))

        reservations = []
        for idx in range(5):
            reservation = journal.reserve_web_dispatch(max_doctors=5)
            if reservation is None:
                break
            reservations.append(reservation)
            case_id = int(reservation["case"]["case_id"])
            session_id = str(reservation["session_id"])
            journal.mark_dispatch_progress(
                case_id=case_id,
                session_id=session_id,
                dispatch_job_id=f"selftest-job-{idx+1}",
                tab_id=f"selftest-tab-{idx+1}",
                transient_url="https://chatgpt.com/project",
                actor="selftest",
            )
            journal.finish_dispatch(
                case_id=case_id,
                session_id=session_id,
                dispatch_job_id=f"selftest-job-{idx+1}",
                dialog_id=f"selftest-dialog-{idx+1}",
                conversation_url=(
                    "https://chatgpt.com/project/c/"
                    f"selftest-dialog-{idx+1}"
                ),
                actor="selftest",
            )

        sixth_blocked = journal.reserve_web_dispatch(max_doctors=5) is None
        stats_at_limit = journal.stats()
        cases.append({
            "id": "doctor_journal_max_five",
            "pass": (
                len(reservations) == 5
                and sixth_blocked
                and stats_at_limit["active_doctors"] == 5
                and stats_at_limit["waiting_for_suzie"] == 1
            ),
        })

        first_case_id = int(reservations[0]["case"]["case_id"])
        first_claim = journal.claim(
            case_id=first_case_id,
            actor="selftest",
        )
        first_token = str(first_claim["claim_token"])

        second_case_id = int(reservations[1]["case"]["case_id"])
        second_claim = journal.claim(
            case_id=second_case_id,
            actor="selftest",
        )
        cases.append({
            "id": "doctor_journal_same_client_parallel_claim",
            "pass": (
                int(second_claim.get("case_id") or 0) == second_case_id
                and str(second_claim.get("state") or "") == "CLAIMED"
                and str(second_claim.get("client_id") or "")
                    == str(first_claim.get("client_id") or "")
            ),
        })

        double_claim_blocked = False
        try:
            journal.claim(case_id=first_case_id, actor="selftest")
        except JournalConflict:
            double_claim_blocked = True
        cases.append({
            "id": "doctor_journal_double_claim_blocked",
            "pass": double_claim_blocked,
        })

        journal.stage(
            case_id=first_case_id,
            claim_token=first_token,
            stage="TREATING",
            actor="selftest",
        )
        journal.stage(
            case_id=first_case_id,
            claim_token=first_token,
            stage="VERIFYING",
            actor="selftest",
        )
        handoff = journal.complete_and_next(
            case_id=first_case_id,
            claim_token=first_token,
            outcome="SUCCESS",
            result={"verified": True},
            actor="selftest",
        )
        next_case = handoff.get("next_case") or {}
        cases.append({
            "id": "doctor_journal_same_dialog_handoff",
            "pass": (
                int(next_case.get("case_id") or 0) == journal_case_ids[5]
                and handoff.get("dialog_id") == "selftest-dialog-1"
                and handoff.get("dialog_ref") == "selftest-dialog-1-2"
                and handoff.get("close_dialog") is False
            ),
        })

        # Continuous HUMAN_REQUIRED problems must not create a fresh Case on
        # every audit cycle. A fresh Case is allowed after the cooldown.
        human_journal = CaseJournal(Path(tmp) / "human_dedupe_test.sqlite3")
        human_case, created = human_journal.escalate(
            client_id="selftest-client-human",
            source_key="human-dedupe-selftest",
            source_request_id="human-1",
            summary="human dedupe selftest",
            problem={"selftest": True, "n": 1},
            actor="selftest",
        )
        human_id = int(human_case["case_id"])
        hr = human_journal.reserve_web_dispatch(max_doctors=1)
        assert hr is not None
        human_journal.mark_dispatch_progress(
            case_id=human_id, session_id=str(hr["session_id"]),
            dispatch_job_id="human-job", tab_id="human-tab",
            transient_url="https://chatgpt.com/project", actor="selftest",
        )
        human_journal.finish_dispatch(
            case_id=human_id, session_id=str(hr["session_id"]),
            dispatch_job_id="human-job", dialog_id="human-dialog",
            conversation_url="https://chatgpt.com/project/c/human", actor="selftest",
        )
        hc = human_journal.claim(case_id=human_id, actor="selftest")
        human_journal.complete_and_next(
            case_id=human_id, claim_token=str(hc["claim_token"]),
            outcome="HUMAN_REQUIRED", result={"reason":"external"}, actor="selftest",
        )
        same, created_again = human_journal.escalate(
            client_id="selftest-client-human", source_key="human-dedupe-selftest",
            source_request_id="human-2", summary="still broken",
            problem={"selftest": True, "n": 2}, actor="selftest",
            human_required_cooldown_seconds=86400,
        )
        human_journal.conn.execute(
            "UPDATE doctor_cases SET closed_at=? WHERE case_id=?",
            (iso(utcnow() - timedelta(days=2)), human_id),
        )
        human_journal.conn.commit()
        later, created_later = human_journal.escalate(
            client_id="selftest-client-human", source_key="human-dedupe-selftest",
            source_request_id="human-3", summary="recurrence later",
            problem={"selftest": True, "n": 3}, actor="selftest",
            human_required_cooldown_seconds=86400,
        )
        cases.append({
            "id": "doctor_human_required_continuous_problem_dedupe",
            "pass": (
                created is True
                and created_again is False
                and int(same.get("case_id") or 0) == human_id
                and created_later is True
                and int(later.get("case_id") or 0) != human_id
            ),
        })

        # Expired leases must release the Doctor slot, invalidate the old
        # claim token, and requeue only a bounded number of times.
        stale_journal = CaseJournal(Path(tmp) / "stale_test.sqlite3")
        stale_case, _ = stale_journal.escalate(
            client_id="selftest-client-lease",
            source_key="stale-lease-selftest",
            source_request_id="stale-lease-selftest",
            summary="stale lease selftest",
            problem={"selftest": True},
            actor="selftest",
        )
        stale_case_id = int(stale_case["case_id"])
        stale_old_sessions = []
        stale_old_tokens = []
        bounded_ok = True
        for attempt in range(1, 4):
            reservation = stale_journal.reserve_web_dispatch(max_doctors=1)
            if reservation is None:
                bounded_ok = False
                break
            stale_old_sessions.append(str(reservation["session_id"]))
            stale_journal.mark_dispatch_progress(
                case_id=stale_case_id,
                session_id=str(reservation["session_id"]),
                dispatch_job_id=f"stale-job-{attempt}",
                tab_id=f"stale-tab-{attempt}",
                transient_url="https://chatgpt.com/project",
                actor="selftest",
            )
            stale_journal.finish_dispatch(
                case_id=stale_case_id,
                session_id=str(reservation["session_id"]),
                dispatch_job_id=f"stale-job-{attempt}",
                dialog_id=f"stale-dialog-{attempt}",
                conversation_url=f"https://chatgpt.com/project/c/stale-{attempt}",
                actor="selftest",
            )
            claimed = stale_journal.claim(case_id=stale_case_id, actor="selftest")
            token = str(claimed["claim_token"])
            stale_old_tokens.append(token)
            stale_journal.conn.execute(
                "UPDATE doctor_cases SET lease_expires=? WHERE case_id=?",
                (iso(utcnow() - timedelta(seconds=1)), stale_case_id),
            )
            stale_journal.conn.commit()
            token_blocked = False
            try:
                stale_journal.authorize_claim(
                    case_id=stale_case_id, claim_token=token
                )
            except JournalAuthError:
                token_blocked = True
            if not token_blocked:
                bounded_ok = False
            actions = stale_journal.reap_stale_sessions(
                max_requeues=2, actor="selftest"
            )
            expected_action = "REQUEUED" if attempt < 3 else "HUMAN_REQUIRED"
            if not actions or actions[0].get("action") != expected_action:
                bounded_ok = False
            current = stale_journal.get_case(stale_case_id)
            expected_state = "FOR_SUZIE" if attempt < 3 else "HUMAN_REQUIRED"
            if current.get("state") != expected_state:
                bounded_ok = False
            session = stale_journal._session_public(
                stale_journal._session_row(stale_old_sessions[-1])
            )
            if session.get("status") != "EXPIRED":
                bounded_ok = False

        cases.append({
            "id": "doctor_stale_lease_reaper_bounded",
            "pass": bounded_ok,
        })

    passed = all(item["pass"] for item in cases)
    print(json.dumps({
        "result": "PASS" if passed else "FAIL",
        "cases": cases,
    }, indent=2))
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default="/etc/suzie-doctor-server/config.json",
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)

    if args.selftest:
        return run_selftest(config)

    server = DoctorServer(config)
    web.run_app(
        server.build_app(),
        host=str(config["bind"]),
        port=int(config["port"]),
        ssl_context=make_ssl_context(config),
        print=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
