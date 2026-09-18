from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

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

    async def host_logs_current(self, lines: int = 5000) -> str:
        safe_lines = max(100, min(10000, int(lines)))
        data = await self._request(
            "GET",
            "/host/logs/boots/0",
            params={"lines": safe_lines, "no_colors": ""},
        )
        return data if isinstance(data, str) else ""

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

    async def mounts_info(self) -> dict[str, Any]:
        return await self._request("GET", "/mounts") or {}

    async def reload_mount(self, name: str) -> bool:
        safe_name = str(name).strip()
        if not safe_name or "/" in safe_name or "\x00" in safe_name:
            return False
        try:
            await self._request("POST", f"/mounts/{quote(safe_name, safe='')}/reload", json={})
            return True
        except aiohttp.ClientResponseError:
            return False

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
