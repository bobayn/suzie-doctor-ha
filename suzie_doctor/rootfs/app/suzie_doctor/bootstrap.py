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


async def ensure_discovery(supervisor: SupervisorClient) -> None:
    try:
        current = await supervisor.list_discovery()
        items = current.get("discovery", []) if isinstance(current, dict) else []
        if any(isinstance(item, dict) and item.get("service") == "suzie_doctor" for item in items):
            return
        await supervisor.create_discovery(
            "suzie_doctor",
            {"bridge_version": BRIDGE_VERSION, "purpose": "suzie_doctor_bridge"},
        )
    except Exception:
        return


async def bootstrap_bridge(
    supervisor: SupervisorClient,
    ha: HomeAssistantClient,
    *,
    auto_install: bool,
    auto_restart_once: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {"enabled": auto_install, "installed": False, "restart_requested": False}
    if not auto_install:
        return result

    changed, digest = install_bridge()
    result["installed"] = True
    result["changed"] = changed
    state = read_state()
    state["bridge_version"] = BRIDGE_VERSION
    state["bridge_hash"] = digest

    if changed and auto_restart_once and state.get("core_restart_for_hash") != digest:
        state["core_restart_for_hash"] = digest
        write_state(state)
        try:
            await ha.persistent_notification(
                "Suzie Doctor",
                "Bridge установлен. Home Assistant Core сейчас один раз перезапустится, после чего Suzie Doctor продолжит настройку.",
                "suzie_doctor_bridge_install",
            )
        except Exception:
            pass
        await asyncio.sleep(8)
        await supervisor.restart_core()
        result["restart_requested"] = True
        await asyncio.sleep(20)
    else:
        write_state(state)

    for _ in range(12):
        try:
            await ha.get_config()
            await ensure_discovery(supervisor)
            break
        except Exception:
            await asyncio.sleep(5)
    return result
