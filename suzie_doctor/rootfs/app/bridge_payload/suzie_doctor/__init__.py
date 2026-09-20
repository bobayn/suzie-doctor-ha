from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import logging
from pathlib import Path
import threading
import time
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import entity_registry as er

from .const import BRIDGE_VERSION, DOMAIN, PLATFORMS

EVENT_DOCTOR_ERROR = "suzie_doctor_error"


class DoctorErrorHandler(logging.Handler):
    """Forward HA ERROR/CRITICAL records onto the internal HA event bus."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(level=logging.ERROR)
        self.hass = hass
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno < logging.ERROR:
                return
            logger_name = str(record.name or "")
            if "suzie_doctor" in logger_name.lower():
                return
            message = str(record.getMessage() or "")[:4000]
            source = f"{Path(str(record.pathname or '')).name}:{int(record.lineno or 0)}"
            raw = f"{logger_name}|{record.levelname}|{source}|{message}"
            fingerprint = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:24]
            now = time.monotonic()
            with self._lock:
                previous = self._seen.get(fingerprint, 0.0)
                if now - previous < 5.0:
                    return
                self._seen[fingerprint] = now
                if len(self._seen) > 512:
                    cutoff = now - 3600.0
                    self._seen = {k: v for k, v in self._seen.items() if v >= cutoff}
            exception = ""
            if record.exc_info:
                try:
                    exception = logging.Formatter().formatException(record.exc_info)[:6000]
                except Exception:
                    exception = ""
            payload = {
                "level": str(record.levelname or "ERROR").upper(),
                "logger": logger_name[:500],
                "message": message,
                "source": source[:500],
                "exception": exception,
                "fingerprint": fingerprint,
                "bridge_version": BRIDGE_VERSION,
                "timestamp": time.time(),
            }
            self.hass.loop.call_soon_threadsafe(
                self.hass.bus.async_fire,
                EVENT_DOCTOR_ERROR,
                payload,
            )
        except Exception:
            # A logging handler must never break Home Assistant logging.
            return


def _serialize_issue(domain: str, issue_id: str, issue: Any) -> dict[str, Any]:
    data: dict[str, Any]
    if is_dataclass(issue):
        data = asdict(issue)
    else:
        data = {}
    data["domain"] = domain
    data["issue_id"] = issue_id
    for key, value in list(data.items()):
        if hasattr(value, "value"):
            data[key] = value.value
        elif value is not None and not isinstance(value, (str, int, float, bool, list, dict)):
            data[key] = str(value)
    return data


def _snapshot(hass: HomeAssistant) -> dict[str, Any]:
    registry = ir.async_get(hass)
    issues = []
    for key, issue in registry.issues.items():
        try:
            domain, issue_id = key
        except Exception:
            domain = getattr(issue, "domain", "unknown")
            issue_id = getattr(issue, "issue_id", str(key))
        issues.append(_serialize_issue(str(domain), str(issue_id), issue))

    entity_registry = er.async_get(hass)
    backup_entities = []
    for entity in entity_registry.entities.values():
        if entity.platform != "backup":
            continue
        state = hass.states.get(entity.entity_id)
        backup_entities.append({
            "entity_id": entity.entity_id,
            "state": None if state is None else state.state,
            "attributes": {} if state is None else dict(state.attributes),
        })

    entries = []
    for entry in hass.config_entries.async_entries():
        entries.append(
            {
                "entry_id": entry.entry_id,
                "domain": entry.domain,
                "title": entry.title,
                "state": entry.state.value,
                "disabled_by": None if entry.disabled_by is None else str(entry.disabled_by),
            }
        )
    return {
        "bridge_version": BRIDGE_VERSION,
        "ha_state": hass.state.value,
        "issues": issues,
        "config_entries": entries,
        "backup_entities": backup_entities,
    }


class SnapshotView(HomeAssistantView):
    url = "/api/suzie_doctor/snapshot"
    name = "api:suzie_doctor:snapshot"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        return self.json(_snapshot(hass))


class ReloadEntryView(HomeAssistantView):
    url = "/api/suzie_doctor/reload_entry"
    name = "api:suzie_doctor:reload_entry"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        data = await request.json()
        entry_id = str(data.get("entry_id", ""))
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            return self.json({"ok": False, "error": "not_found"}, status_code=404)
        ok = await hass.config_entries.async_reload(entry_id)
        return self.json({"ok": bool(ok)})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    if not hass.data[DOMAIN].get("views_registered"):
        hass.http.register_view(SnapshotView)
        hass.http.register_view(ReloadEntryView)
        hass.data[DOMAIN]["views_registered"] = True
    if not hass.data[DOMAIN].get("error_handler"):
        handler = DoctorErrorHandler(hass)
        logging.getLogger().addHandler(handler)
        hass.data[DOMAIN]["error_handler"] = handler
    hass.data[DOMAIN][entry.entry_id] = {"ready": True}
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        domain_data = hass.data.get(DOMAIN, {})
        domain_data.pop(entry.entry_id, None)
        handler = domain_data.pop("error_handler", None)
        if isinstance(handler, logging.Handler):
            logging.getLogger().removeHandler(handler)
    return unloaded
