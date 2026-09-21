from __future__ import annotations

import os
import json
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

    async def network_reload(self) -> bool:
        try:
            await self._request("POST", "/network/reload", json={})
            return True
        except aiohttp.ClientResponseError:
            return False

    async def set_primary_auto_dns(self, nameservers: list[str]) -> dict[str, Any]:
        safe_nameservers = [str(x).strip() for x in nameservers if str(x).strip()]
        if not safe_nameservers or len(safe_nameservers) > 4:
            raise ValueError("nameservers must contain 1..4 addresses")
        info = await self.network_info()
        interfaces = info.get("interfaces", []) if isinstance(info, dict) else []
        primary = next(
            (
                item for item in interfaces
                if isinstance(item, dict) and bool(item.get("primary"))
            ),
            None,
        )
        if primary is None:
            raise RuntimeError("Primary network interface not found")
        ipv4 = primary.get("ipv4") if isinstance(primary.get("ipv4"), dict) else {}
        if str(ipv4.get("method") or "").lower() != "auto":
            raise RuntimeError("DNS write is restricted to IPv4 method=auto")
        interface = str(primary.get("interface") or "").strip()
        if not interface or "/" in interface or "\x00" in interface:
            raise RuntimeError("Invalid primary network interface")
        previous = [str(x) for x in (ipv4.get("nameservers") or [])]
        await self._request(
            "POST",
            f"/network/interface/{quote(interface, safe='')}/update",
            json={"ipv4": {"method": "auto", "nameservers": safe_nameservers}},
        )
        await self.network_reload()
        return {
            "interface": interface,
            "previous_nameservers": previous,
            "nameservers": safe_nameservers,
        }

    async def core_info(self) -> dict[str, Any]:
        return await self._request("GET", "/core/info") or {}

    async def core_stats(self) -> dict[str, Any]:
        return await self._request("GET", "/core/stats") or {}

    async def backups_info(self) -> dict[str, Any]:
        return await self._request("GET", "/backups/info") or {}

    async def mounts_info(self) -> dict[str, Any]:
        return await self._request("GET", "/mounts") or {}

    async def reload_mount_detailed(self, name: str) -> dict[str, Any]:
        safe_name = str(name).strip()
        if not safe_name or "/" in safe_name or "\x00" in safe_name:
            return {"ok": False, "error": "invalid_mount_name"}
        path = f"/mounts/{quote(safe_name, safe='')}/reload"
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as session:
                async with session.post(self.base + path, json={}) as response:
                    raw = await response.text()
                    detail = raw[:1000]
                    try:
                        payload = json.loads(raw) if raw else {}
                    except Exception:
                        payload = {}
                    if isinstance(payload, dict):
                        detail = str(payload.get("message") or payload.get("error") or detail)[:1000]
                    if 200 <= response.status < 300:
                        return {"ok": True, "status": response.status, "detail": detail}
                    return {
                        "ok": False,
                        "status": response.status,
                        "error": detail or response.reason or "mount_reload_rejected",
                    }
        except (aiohttp.ClientError, TimeoutError) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:1000]}

    async def reload_mount(self, name: str) -> bool:
        return bool((await self.reload_mount_detailed(name)).get("ok"))

    async def addons(self) -> dict[str, Any]:
        return await self._request("GET", "/addons") or {}

    async def store_info(self) -> dict[str, Any]:
        return await self._request("GET", "/store") or {}

    async def add_store_repository(self, repository: str) -> bool:
        value = str(repository).strip()
        if not value.startswith("https://github.com/"):
            return False
        try:
            await self._request(
                "POST",
                "/store/repositories",
                json={"repository": value},
            )
            return True
        except aiohttp.ClientResponseError:
            return False

    async def reload_store(self) -> bool:
        try:
            await self._request("POST", "/store/reload", json={})
            return True
        except aiohttp.ClientResponseError:
            return False

    async def install_store_addon(self, slug: str) -> bool:
        safe_slug = str(slug).strip()
        if not safe_slug or "/" in safe_slug or "\x00" in safe_slug:
            return False
        try:
            await self._request(
                "POST",
                f"/store/addons/{quote(safe_slug, safe='')}/install",
                json={"background": False},
            )
            return True
        except aiohttp.ClientResponseError:
            return False

    async def start_addon(self, slug: str) -> bool:
        safe_slug = str(slug).strip()
        if not safe_slug or "/" in safe_slug or "\x00" in safe_slug:
            return False
        try:
            await self._request(
                "POST",
                f"/addons/{quote(safe_slug, safe='')}/start",
                json={},
            )
            return True
        except aiohttp.ClientResponseError:
            return False

    async def addon_logs(self, slug: str) -> str:
        data = await self._request("GET", f"/addons/{slug}/logs")
        return data if isinstance(data, str) else ""

    async def restart_addon(self, slug: str) -> bool:
        safe_slug = str(slug).strip()
        if not safe_slug or "/" in safe_slug or "\x00" in safe_slug:
            return False
        try:
            await self._request(
                "POST",
                f"/addons/{quote(safe_slug, safe='')}/restart",
                json={},
            )
            return True
        except aiohttp.ClientResponseError:
            return False

    async def restart_core(self) -> Any:
        return await self._request("POST", "/core/restart", json={})

    async def reboot_host(self) -> Any:
        return await self._request("POST", "/host/reboot", json={})

    async def list_discovery(self) -> dict[str, Any]:
        return await self._request("GET", "/discovery") or {}

    async def create_discovery(self, service: str, config: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/discovery", json={"service": service, "config": config}) or {}
