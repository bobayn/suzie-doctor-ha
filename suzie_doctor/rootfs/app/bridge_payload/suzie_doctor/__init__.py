from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import entity_registry as er

from .const import BRIDGE_VERSION, DOMAIN, PLATFORMS


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
    hass.data[DOMAIN][entry.entry_id] = {"ready": True}
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded
