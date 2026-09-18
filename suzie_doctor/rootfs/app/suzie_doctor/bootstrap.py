from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from . import BRIDGE_VERSION
from .ha_api import HomeAssistantClient
from .supervisor import SupervisorClient

BUNDLED = Path("/app/bridge_payload/suzie_doctor")
TARGET = Path("/homeassistant/custom_components/suzie_doctor")
STATE = Path("/data/bridge_bootstrap.json")


def _tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(str(file.relative_to(path)).encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def install_bridge() -> tuple[bool, str]:
    if not BUNDLED.exists():
        return False, "bundle_missing"
    source_hash = _tree_hash(BUNDLED)
    target_hash = _tree_hash(TARGET) if TARGET.exists() else None
    if source_hash == target_hash:
        return False, source_hash
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(BUNDLED, TARGET)
    return True, source_hash


def read_state() -> dict[str, Any]:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_state(data: dict[str, Any]) -> None:
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def ensure_discovery(supervisor: SupervisorClient, state: dict[str, Any]) -> tuple[bool, str | None]:
    # Apps may POST /discovery, but listing discovery is reserved for Home Assistant.
    # Persist the returned UUID locally so we announce only once per bridge hash.
    saved_uuid = state.get("discovery_uuid")
    saved_hash = state.get("discovery_bridge_hash")
    if saved_uuid and saved_hash == state.get("bridge_hash"):
        return True, str(saved_uuid)

    try:
        response = await supervisor.create_discovery(
            "suzie_doctor",
            {"bridge_version": BRIDGE_VERSION, "purpose": "suzie_doctor_bridge"},
        )
        uuid = response.get("uuid") if isinstance(response, dict) else None
        if not uuid:
            return False, None
        state["discovery_uuid"] = str(uuid)
        state["discovery_bridge_hash"] = state.get("bridge_hash")
        write_state(state)
        return True, str(uuid)
    except Exception as exc:
        state["discovery_error"] = f"{type(exc).__name__}: {exc}"
        write_state(state)
        return False, None


async def bootstrap_bridge(
    supervisor: SupervisorClient,
    ha: HomeAssistantClient,
    *,
    auto_install: bool,
    auto_restart_once: bool,
) -> dict[str, Any]:
    # DEV rule: bridge bootstrap must NEVER restart Home Assistant by itself.
    # auto_restart_once is accepted only for backward-compatible options parsing
    # and is intentionally ignored.
    result: dict[str, Any] = {
        "enabled": auto_install,
        "installed": False,
        "restart_requested": False,
        "automatic_core_restart_disabled": True,
    }
    if not auto_install:
        return result

    changed, digest = install_bridge()
    result["installed"] = True
    result["changed"] = changed
    state = read_state()
    state["bridge_version"] = BRIDGE_VERSION
    state["bridge_hash"] = digest
    write_state(state)

    if changed:
        result["restart_required"] = True
        try:
            await ha.persistent_notification(
                "Suzie Doctor",
                "Bridge обновлён. Для загрузки новой версии bridge потребуется один обычный перезапуск Home Assistant Core в удобное время. Suzie Doctor сам перезапуск не выполняет.",
                "suzie_doctor_bridge_restart_required",
            )
        except Exception:
            pass
    else:
        result["restart_required"] = False

    core_ready = False
    discovery_ready = False
    for _ in range(12):
        try:
            await ha.get_config()
            core_ready = True
            discovery_ready, discovery_uuid = await ensure_discovery(supervisor, state)
            if discovery_ready:
                result["discovery_uuid"] = discovery_uuid
                break
        except Exception:
            core_ready = False
        await asyncio.sleep(5)

    result["core_ready"] = core_ready
    result["discovery_registered"] = discovery_ready
    if not discovery_ready:
        result["warning"] = "bridge_files_installed_but_discovery_not_registered"
        if state.get("discovery_error"):
            result["discovery_error"] = state["discovery_error"]
    return result

