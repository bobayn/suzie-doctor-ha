from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import aiohttp


class HomeAssistantClient:
    def __init__(self) -> None:
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise RuntimeError("SUPERVISOR_TOKEN is not available")
        self.token = token
        self.base = "http://supervisor/core/api"
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self._simulated_entry_states_once: dict[str, tuple[str, int]] = {}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as session:
            async with session.request(method, self.base + path, **kwargs) as response:
                response.raise_for_status()
                if response.status == 204:
                    return None
                return await response.json()

    async def get_config(self) -> dict[str, Any]:
        data = await self._request("GET", "/config")
        return data if isinstance(data, dict) else {}

    async def get_states(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/states")
        return data if isinstance(data, list) else []

    async def call_service(self, domain: str, service: str, data: dict[str, Any] | None = None) -> Any:
        return await self._request("POST", f"/services/{domain}/{service}", json=data or {})

    async def set_state(self, entity_id: str, state: str, attributes: dict[str, Any] | None = None) -> Any:
        return await self._request(
            "POST",
            f"/states/{entity_id}",
            json={"state": state, "attributes": attributes or {}},
        )

    async def delete_state(self, entity_id: str) -> None:
        try:
            await self._request("DELETE", f"/states/{entity_id}")
        except aiohttp.ClientResponseError as err:
            if err.status != 404:
                raise

    async def history(
        self,
        entity_id: str,
        start: datetime,
        end: datetime,
    ) -> list[Any]:
        start_text = quote(start.astimezone(UTC).isoformat(), safe="")
        params = {
            "filter_entity_id": entity_id,
            "end_time": end.astimezone(UTC).isoformat(),
            "minimal_response": "",
            "no_attributes": "",
        }
        data = await self._request(
            "GET",
            f"/history/period/{start_text}",
            params=params,
        )
        return data if isinstance(data, list) else []

    async def recorder_write_probe(self) -> bool:
        entity_id = "sensor.suzie_doctor_recorder_probe"
        marker = f"doctor-{uuid4()}"
        started = datetime.now(UTC) - timedelta(seconds=1)
        try:
            await self.set_state(
                entity_id,
                marker,
                {"friendly_name": "Suzie Doctor Recorder Probe"},
            )
            for _ in range(5):
                await asyncio.sleep(1)
                history = await self.history(
                    entity_id,
                    started,
                    datetime.now(UTC) + timedelta(seconds=1),
                )
                for series in history:
                    if not isinstance(series, list):
                        continue
                    for item in series:
                        if isinstance(item, dict) and str(item.get("state") or "") == marker:
                            return True
            return False
        finally:
            await self.delete_state(entity_id)

    async def ws_command(self, command_type: str, **data: Any) -> Any:
        """Run one authenticated one-shot Home Assistant WebSocket command."""
        timeout = aiohttp.ClientTimeout(total=20)
        ws_headers = {"Authorization": f"Bearer {self.token}"}
        async with aiohttp.ClientSession(timeout=timeout, headers=ws_headers) as session:
            async with session.ws_connect("ws://supervisor/core/websocket", heartbeat=10) as ws:
                first = await asyncio.wait_for(ws.receive_json(), timeout=5)
                if not isinstance(first, dict) or first.get("type") != "auth_required":
                    raise RuntimeError("Unexpected Home Assistant WebSocket greeting")
                await ws.send_json({"type": "auth", "access_token": self.token})
                auth = await asyncio.wait_for(ws.receive_json(), timeout=5)
                if not isinstance(auth, dict) or auth.get("type") != "auth_ok":
                    raise RuntimeError("Home Assistant WebSocket authentication failed")
                payload = {"id": 1, "type": str(command_type)}
                payload.update(data)
                await ws.send_json(payload)
                for _ in range(20):
                    message = await asyncio.wait_for(ws.receive_json(), timeout=5)
                    if not isinstance(message, dict) or message.get("id") != 1:
                        continue
                    if message.get("type") != "result":
                        continue
                    if not message.get("success", False):
                        raise RuntimeError(
                            f"{command_type} failed: {message.get('error')}"
                        )
                    return message.get("result")
                raise RuntimeError(f"No result received for {command_type}")

    async def list_repairs(self) -> list[dict[str, Any]]:
        data = await self.ws_command("repairs/list_issues")
        if isinstance(data, dict):
            issues = data.get("issues")
            return [x for x in issues if isinstance(x, dict)] if isinstance(issues, list) else []
        return []

    async def list_persistent_notifications(self) -> list[dict[str, Any]]:
        data = await self.ws_command("persistent_notification/get")
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []

    async def start_repair_flow(self, domain: str, issue_id: str) -> dict[str, Any]:
        data = await self._request(
            "POST",
            "/repairs/issues/fix",
            json={"handler": str(domain), "issue_id": str(issue_id)},
        )
        return data if isinstance(data, dict) else {}

    async def repair_flow_step(
        self,
        flow_id: str,
        user_input: dict[str, Any],
    ) -> dict[str, Any]:
        data = await self._request(
            "POST",
            f"/repairs/issues/fix/{quote(str(flow_id), safe='')}",
            json=user_input,
        )
        return data if isinstance(data, dict) else {}

    async def get_repair_flow(self, flow_id: str) -> dict[str, Any]:
        data = await self._request(
            "GET",
            f"/repairs/issues/fix/{quote(str(flow_id), safe='')}",
        )
        return data if isinstance(data, dict) else {}

    async def list_config_entries(self, domain: str = "") -> list[dict[str, Any]]:
        payload: dict[str, Any] = {}
        if domain:
            payload["domain"] = str(domain)
        data = await self.ws_command("config_entries/get", **payload)
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []

    async def set_config_entry_enabled(
        self,
        entry_id: str,
        *,
        enabled: bool,
    ) -> dict[str, Any]:
        data = await self.ws_command(
            "config_entries/disable",
            entry_id=str(entry_id),
            disabled_by=None if enabled else "user",
        )
        return data if isinstance(data, dict) else {}

    async def get_entity_registry_entry(self, entity_id: str) -> dict[str, Any] | None:
        data = await self.ws_command(
            "config/entity_registry/get_entries",
            entity_ids=[str(entity_id)],
        )
        if not isinstance(data, dict):
            return None
        item = data.get(str(entity_id))
        return item if isinstance(item, dict) else None

    async def set_entity_registry_enabled(
        self,
        entity_id: str,
        *,
        enabled: bool,
    ) -> dict[str, Any]:
        data = await self.ws_command(
            "config/entity_registry/update",
            entity_id=str(entity_id),
            disabled_by=None if enabled else "user",
        )
        return data if isinstance(data, dict) else {}

    async def install_update(self, entity_id: str, *, backup: bool = True) -> Any:
        safe_entity_id = str(entity_id).strip()
        if not safe_entity_id.startswith("update."):
            raise ValueError("Only update entities may be installed")
        return await self.call_service(
            "update",
            "install",
            {"entity_id": safe_entity_id, "backup": bool(backup)},
        )

    async def system_health_info(self) -> dict[str, Any]:
        """Read the initial Home Assistant system-health snapshot over the official WS API."""
        timeout = aiohttp.ClientTimeout(total=15)
        ws_headers = {"Authorization": f"Bearer {self.token}"}
        async with aiohttp.ClientSession(timeout=timeout, headers=ws_headers) as session:
            async with session.ws_connect("ws://supervisor/core/websocket", heartbeat=10) as ws:
                first = await asyncio.wait_for(ws.receive_json(), timeout=5)
                if not isinstance(first, dict) or first.get("type") != "auth_required":
                    raise RuntimeError("Unexpected Home Assistant WebSocket greeting")

                await ws.send_json({"type": "auth", "access_token": self.token})
                auth = await asyncio.wait_for(ws.receive_json(), timeout=5)
                if not isinstance(auth, dict) or auth.get("type") != "auth_ok":
                    raise RuntimeError("Home Assistant WebSocket authentication failed")

                command_id = 1
                await ws.send_json({"id": command_id, "type": "system_health/info"})
                for _ in range(12):
                    message = await asyncio.wait_for(ws.receive_json(), timeout=5)
                    if not isinstance(message, dict) or message.get("id") != command_id:
                        continue

                    if message.get("type") == "result":
                        if not message.get("success", False):
                            raise RuntimeError(f"system_health/info failed: {message.get('error')}")
                        result = message.get("result")
                        if isinstance(result, dict):
                            if result.get("type") == "initial" and isinstance(result.get("data"), dict):
                                return result["data"]
                            if isinstance(result.get("data"), dict):
                                return result["data"]
                            return result

                    if message.get("type") == "event":
                        event = message.get("event")
                        if isinstance(event, dict):
                            if event.get("type") == "initial" and isinstance(event.get("data"), dict):
                                return event["data"]
                            if isinstance(event.get("data"), dict):
                                return event["data"]

                raise RuntimeError("No initial system-health payload received")

    async def persistent_notification(self, title: str, message: str, notification_id: str) -> None:
        await self.call_service(
            "persistent_notification",
            "create",
            {"title": title, "message": message, "notification_id": notification_id},
        )

    def simulate_entry_state_reads(self, entry_id: str, state: str, reads: int = 1) -> None:
        """Developer-only bounded overlay used by controlled treatment tests."""
        self._simulated_entry_states_once[str(entry_id)] = (str(state), max(1, int(reads)))

    def simulate_entry_state_once(self, entry_id: str, state: str) -> None:
        self.simulate_entry_state_reads(entry_id, state, 1)

    async def bridge_snapshot(self) -> dict[str, Any] | None:
        try:
            data = await self._request("GET", "/suzie_doctor/snapshot")
            if not isinstance(data, dict):
                return None

            if self._simulated_entry_states_once:
                entries = data.get("config_entries")
                if isinstance(entries, list):
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        entry_id = str(entry.get("entry_id") or "")
                        simulated = self._simulated_entry_states_once.get(entry_id)
                        if simulated is None:
                            continue
                        state, remaining_reads = simulated
                        entry["state"] = state
                        entry["_doctor_simulated_state"] = True
                        if remaining_reads <= 1:
                            self._simulated_entry_states_once.pop(entry_id, None)
                        else:
                            self._simulated_entry_states_once[entry_id] = (
                                state,
                                remaining_reads - 1,
                            )

            return data
        except aiohttp.ClientResponseError as err:
            if err.status in (404, 503):
                return None
            raise

    async def bridge_reload_entry(self, entry_id: str) -> bool:
        try:
            data = await self._request("POST", "/suzie_doctor/reload_entry", json={"entry_id": entry_id})
            return bool(isinstance(data, dict) and data.get("ok"))
        except aiohttp.ClientResponseError:
            return False
