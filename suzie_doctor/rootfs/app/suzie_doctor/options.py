from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

OPTIONS_PATH = Path("/data/options.json")


@dataclass(slots=True)
class Options:
    trust_mode: str = "safe_auto"
    daily_audit_time: str = "03:15"
    retention_days: int = 90
    language: str = "ru"
    developer_mode: bool = True
    auto_install_bridge: bool = True
    auto_restart_core_once: bool = False
    doctor_server_enabled: bool = True
    doctor_server_url: str = "https://192.168.0.105:8790"
    doctor_server_timeout_seconds: int = 10


def load_options(path: Path = OPTIONS_PATH) -> Options:
    if not path.exists():
        return Options()
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Options(
        trust_mode=str(raw.get("trust_mode", "safe_auto")),
        daily_audit_time=str(raw.get("daily_audit_time", "03:15")),
        retention_days=max(30, int(raw.get("retention_days", 90))),
        language=str(raw.get("language", "ru")),
        developer_mode=bool(raw.get("developer_mode", True)),
        auto_install_bridge=bool(raw.get("auto_install_bridge", True)),
        auto_restart_core_once=bool(raw.get("auto_restart_core_once", False)),
        doctor_server_enabled=bool(raw.get("doctor_server_enabled", True)),
        doctor_server_url=str(
            raw.get("doctor_server_url", "https://192.168.0.105:8790")
        ).rstrip("/"),
        doctor_server_timeout_seconds=max(
            2, min(60, int(raw.get("doctor_server_timeout_seconds", 10)))
        ),
    )
