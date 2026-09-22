from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import ssl
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

DATA_DIR = Path(os.environ.get("SUZIE_DOCTOR_DATA", "/data"))
IDENTITY_PATH = DATA_DIR / "doctor_server_client.json"
PRIVATE_KEY_PATH = DATA_DIR / "doctor_server_client_private.pem"
DEFAULT_TLS_CERT = Path("/app/server_trust/server_tls_cert.pem")
DEFAULT_SERVER_KEY = Path("/app/server_trust/server_ed25519_public.b64")


class DoctorServerError(RuntimeError):
    pass


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


class DoctorServerClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: int = 10,
        tls_cert_path: str | Path = DEFAULT_TLS_CERT,
        server_key_path: str | Path = DEFAULT_SERVER_KEY,
        app_version: str,
    ) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.timeout_seconds = max(2, min(60, int(timeout_seconds)))
        self.tls_cert_path = Path(tls_cert_path)
        self.server_key_path = Path(server_key_path)
        self.app_version = app_version
        self._private_key: Ed25519PrivateKey | None = None
        self._client_id: str | None = None
        self._server_key: Ed25519PublicKey | None = None
        self.last_status: dict[str, Any] = {"state": "not_connected"}

    @property
    def client_id(self) -> str:
        self._ensure_identity()
        assert self._client_id is not None
        return self._client_id

    def _ensure_identity(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if IDENTITY_PATH.exists() and PRIVATE_KEY_PATH.exists():
            raw = json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
            client_id = str(raw.get("client_id") or "")
            private = serialization.load_pem_private_key(
                PRIVATE_KEY_PATH.read_bytes(),
                password=None,
            )
            if not isinstance(private, Ed25519PrivateKey) or not client_id:
                raise DoctorServerError("stored Doctor Server identity is invalid")
            self._client_id = client_id
            self._private_key = private
            return

        private = Ed25519PrivateKey.generate()
        client_id = f"ha-{uuid4()}"
        temp_key = PRIVATE_KEY_PATH.with_suffix(".pem.tmp")
        temp_id = IDENTITY_PATH.with_suffix(".json.tmp")
        temp_key.write_bytes(
            private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        os.chmod(temp_key, 0o600)
        temp_id.write_text(
            json.dumps(
                {
                    "client_id": client_id,
                    "created_at": datetime.now(UTC).isoformat(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(temp_id, 0o600)
        temp_key.replace(PRIVATE_KEY_PATH)
        temp_id.replace(IDENTITY_PATH)
        self._client_id = client_id
        self._private_key = private

    def _private(self) -> Ed25519PrivateKey:
        self._ensure_identity()
        assert self._private_key is not None
        return self._private_key

    def _server_public(self) -> Ed25519PublicKey:
        if self._server_key is None:
            raw = b64d(self.server_key_path.read_text(encoding="utf-8").strip())
            self._server_key = Ed25519PublicKey.from_public_bytes(raw)
        return self._server_key

    def _ssl_context(self) -> ssl.SSLContext:
        if not self.tls_cert_path.exists():
            raise DoctorServerError("Doctor Server TLS trust certificate missing")
        context = ssl.create_default_context(cafile=str(self.tls_cert_path))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    def _timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=self.timeout_seconds)

    def _verify_envelope(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise DoctorServerError("Doctor Server response is not an object")
        if raw.get("algorithm") != "ed25519":
            raise DoctorServerError("unsupported Doctor Server signature algorithm")
        payload = raw.get("payload")
        signature_text = str(raw.get("signature") or "")
        if not isinstance(payload, dict) or not signature_text:
            raise DoctorServerError("signed Doctor Server envelope is incomplete")
        try:
            self._server_public().verify(
                b64d(signature_text),
                canonical_json(payload),
            )
        except Exception as exc:
            raise DoctorServerError("Doctor Server signature verification failed") from exc
        return payload

    async def health(self) -> dict[str, Any]:
        url = f"{self.base_url}/health"
        try:
            async with aiohttp.ClientSession(
                timeout=self._timeout(),
            ) as session:
                async with session.get(
                    url,
                    ssl=self._ssl_context(),
                ) as response:
                    text = await response.text()
                    if response.status != 200:
                        raise DoctorServerError(
                            f"health HTTP {response.status}: {text[:300]}"
                        )
                    payload = json.loads(text)
                    if not isinstance(payload, dict):
                        raise DoctorServerError("health response invalid")
                    self.last_status = {
                        "state": "ok",
                        "checked_at": datetime.now(UTC).isoformat(),
                        "server_version": payload.get("version"),
                        "knowledge": payload.get("knowledge"),
                    }
                    return payload
        except Exception as exc:
            self.last_status = {
                "state": "error",
                "checked_at": datetime.now(UTC).isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
            }
            raise

    async def enroll(self) -> dict[str, Any]:
        private = self._private()
        public_raw = private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        body = {
            "client_id": self.client_id,
            "public_key_b64": b64e(public_raw),
            "label": "Home Assistant Suzie Doctor",
            "metadata": {
                "app_version": self.app_version,
                "environment": "home_assistant",
            },
        }
        url = f"{self.base_url}/v1/enroll"
        async with aiohttp.ClientSession(timeout=self._timeout()) as session:
            async with session.post(
                url,
                json=body,
                ssl=self._ssl_context(),
            ) as response:
                text = await response.text()
                if response.status != 200:
                    raise DoctorServerError(
                        f"enroll HTTP {response.status}: {text[:300]}"
                    )
                payload = self._verify_envelope(json.loads(text))
                self.last_status = {
                    "state": "enrolled",
                    "checked_at": datetime.now(UTC).isoformat(),
                    "client_id": self.client_id,
                    "license": payload.get("license"),
                    "server_version": payload.get("server_version"),
                }
                return payload

    async def _signed_post(
        self,
        path: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        private = self._private()
        raw_body = canonical_json(body)
        timestamp = int(time.time())
        nonce = secrets.token_urlsafe(24)
        digest = hashlib.sha256(raw_body).hexdigest()
        message = f"{timestamp}\n{nonce}\n{digest}".encode("utf-8")
        signature = private.sign(message)
        headers = {
            "Content-Type": "application/json",
            "X-Suzie-Client-ID": self.client_id,
            "X-Suzie-Timestamp": str(timestamp),
            "X-Suzie-Nonce": nonce,
            "X-Suzie-Signature": b64e(signature),
        }
        url = f"{self.base_url}{path}"
        async with aiohttp.ClientSession(timeout=self._timeout()) as session:
            async with session.post(
                url,
                data=raw_body,
                headers=headers,
                ssl=self._ssl_context(),
            ) as response:
                text = await response.text()
                if response.status == 401:
                    raise DoctorServerError("Doctor Server client is not enrolled")
                if response.status != 200:
                    raise DoctorServerError(
                        f"{path} HTTP {response.status}: {text[:300]}"
                    )
                return self._verify_envelope(json.loads(text))

    async def ensure_enrolled(self) -> dict[str, Any]:
        try:
            return await self.license_status()
        except DoctorServerError:
            return await self.enroll()

    async def license_status(self) -> dict[str, Any]:
        return await self._signed_post("/v1/license", {})

    async def diagnose(self, evidence: dict[str, Any]) -> dict[str, Any]:
        return await self._signed_post("/v1/diagnose", evidence)

    async def customer_feed(self, limit: int = 80) -> dict[str, Any]:
        return await self._signed_post(
            "/v1/customer-feed",
            {"limit": max(1, min(200, int(limit)))},
        )

    async def poll_command(self) -> dict[str, Any]:
        """Poll exactly this enrolled client for one server-routed command."""
        return await self._signed_post("/v1/client/commands/poll", {})

    async def submit_command_result(
        self,
        *,
        command_id: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "command_id": str(command_id),
            "result": dict(result or {}),
        }
        if error:
            payload["error"] = str(error)[:2000]
        return await self._signed_post(
            "/v1/client/commands/result",
            payload,
        )

    def validate_execution_package(
        self,
        package: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(package, dict):
            raise DoctorServerError("execution package must be an object")
        if str(package.get("client_id") or "") != self.client_id:
            raise DoctorServerError("execution package is bound to another client")
        expires_text = str(package.get("expires_at") or "")
        try:
            expires = datetime.fromisoformat(expires_text)
        except Exception as exc:
            raise DoctorServerError("execution package expiry is invalid") from exc
        if expires <= datetime.now(UTC):
            raise DoctorServerError("execution package has expired")
        card = package.get("card")
        if not isinstance(card, dict):
            raise DoctorServerError("execution package has no protocol card")
        disease_id = str(package.get("disease_id") or "")
        protocol_id = str(package.get("protocol_id") or "")
        card_protocol = card.get("protocol") if isinstance(card.get("protocol"), dict) else {}
        if str(card.get("disease_id") or "") != disease_id:
            raise DoctorServerError("execution package Disease mismatch")
        if str(card_protocol.get("id") or "") != protocol_id:
            raise DoctorServerError("execution package Protocol mismatch")
        return card
