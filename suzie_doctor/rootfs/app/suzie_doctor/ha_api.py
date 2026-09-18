from __future__ import annotations

import os
from typing import Any

import aiohttp


class HomeAssistantClient:
    def __init__(self) -> None:
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise RuntimeError("SUPERVISOR_TOKEN is not available")
        self.base = "http://supervisor/core/api"
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self._simulated_entry_states_once: dict[str, str] = {}

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

    async def persistent_notification(self, title: str, message: str, notification_id: str) -> None:
        await self.call_service(
            "persistent_notification",
            "create",
            {"title": title, "message": message, "notification_id": notification_id},
        )

    def simulate_entry_state_once(self, entry_id: str, state: str) -> None:
        """Developer-only one-shot overlay used by controlled treatment tests."""
        self._simulated_entry_states_once[str(entry_id)] = str(state)

    async def bridge_snapshot(self) -> dict[str, Any] | None:
        try:
            data = await self._request("GET", "/suzie_doctor/snapshot")
            if not isinstance(data, dict):
                return None

            if self._simulated_entry_states_once:
                entries = data.get("config_entries")
                consumed: list[str] = []
                if isinstance(entries, list):
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        entry_id = str(entry.get("entry_id") or "")
                        if entry_id in self._simulated_entry_states_once:
                            entry["state"] = self._simulated_entry_states_once[entry_id]
                            entry["_doctor_simulated_state"] = True
                            consumed.append(entry_id)
                for entry_id in consumed:
                    self._simulated_entry_states_once.pop(entry_id, None)

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
