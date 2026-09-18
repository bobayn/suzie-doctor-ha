from __future__ import annotations

import os
from typing import Any

import aiohttp


class SupervisorClient:
    def __init__(self) -> None:
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise RuntimeError("SUPERVISOR_TOKEN is not available")
        self.base = "http://supervisor"
        self.headers = {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as session:
            async with session.request(method, self.base + path, **kwargs) as response:
                response.raise_for_status()
                ctype = response.headers.get("Content-Type", "")
                if response.status == 204:
                    return None
                if "json" not in ctype:
                    return await response.text()
                payload = await response.json()
                if isinstance(payload, dict) and "data" in payload:
                    return payload.get("data")
                return payload

    async def info(self) -> dict[str, Any]:
        return await self._request("GET", "/info") or {}

    async def supervisor_info(self) -> dict[str, Any]:
        return await self._request("GET", "/supervisor/info") or {}

    async def host_info(self) -> dict[str, Any]:
        return await self._request("GET", "/host/info") or {}

    async def hardware_info(self) -> dict[str, Any]:
        return await self._request("GET", "/hardware/info") or {}

    async def network_info(self) -> dict[str, Any]:
        return await self._request("GET", "/network/info") or {}

    async def core_info(self) -> dict[str, Any]:
        return await self._request("GET", "/core/info") or {}

    async def core_stats(self) -> dict[str, Any]:
        return await self._request("GET", "/core/stats") or {}

    async def backups_info(self) -> dict[str, Any]:
        return await self._request("GET", "/backups/info") or {}

    async def addons(self) -> dict[str, Any]:
        return await self._request("GET", "/addons") or {}

    async def addon_logs(self, slug: str) -> str:
        data = await self._request("GET", f"/addons/{slug}/logs")
        return data if isinstance(data, str) else ""

    async def restart_core(self) -> Any:
        return await self._request("POST", "/core/restart", json={})

    async def reboot_host(self) -> Any:
        return await self._request("POST", "/host/reboot", json={})

    async def list_discovery(self) -> dict[str, Any]:
        return await self._request("GET", "/discovery") or {}

    async def create_discovery(self, service: str, config: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/discovery", json={"service": service, "config": config}) or {}
