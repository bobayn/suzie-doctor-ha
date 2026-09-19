"""Versioned structured facade over the existing ProtocolEngine primitives.

No public primitive executor: writes enter only through the signed-package engine.
Capability availability describes implemented access, never authorization to treat.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any

CONNECTOR_VERSION = "0.2.0"
CAPABILITY_SCHEMA_VERSION = 1


class ConnectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class Capability:
    capability_id: str
    adapter_id: str
    primitive: str
    operation: str = "read"
    risk: str = "low"
    confirmation_class: str = "none"
    target_field: str | None = None
    fixed_target: str | None = None
    checkpoint_support: str = "not_required"
    rollback_support: str = "not_applicable"
    backend_methods: tuple[str, ...] = ()


@dataclass(frozen=True)
class Adapter:
    adapter_id: str
    adapter_version: str = CONNECTOR_VERSION
    access_level: str = "none"
    implemented: bool = False
    reason: str = "No supported safe backend in this release"
    compatibility: str = "capability_schema=1"


class CapabilityRegistry:
    def __init__(self) -> None:
        self.adapters: dict[str, Adapter] = {}
        self.capabilities: dict[str, Capability] = {}
        self.primitives: dict[str, Capability] = {}

    def register_adapter(self, adapter: Adapter) -> None:
        if adapter.adapter_id in self.adapters:
            raise ConnectorError("duplicate_adapter")
        self.adapters[adapter.adapter_id] = adapter

    def register(self, capability: Capability) -> None:
        if capability.adapter_id not in self.adapters:
            raise ConnectorError("unknown_adapter")
        if capability.capability_id in self.capabilities or capability.primitive in self.primitives:
            raise ConnectorError("duplicate_capability")
        if capability.operation not in {"read", "write", "probe", "notify"}:
            raise ConnectorError("invalid_operation")
        if capability.risk not in {"low", "medium", "high"}:
            raise ConnectorError("invalid_risk")
        if capability.operation == "write" and (not capability.confirmation_class or
                not (capability.target_field or capability.fixed_target)):
            raise ConnectorError("write_metadata_required")
        self.capabilities[capability.capability_id] = capability
        self.primitives[capability.primitive] = capability


def build_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for name, access in (("ha", "supervisor_ha_api"), ("supervisor", "supervisor_manager"),
                         ("network", "supervisor_primary_ipv4_auto_only"),
                         ("mqtt", "supervisor_mosquitto_logs_only"),
                         ("recorder", "ha_functional_probe_only"), ("workflow", "local")):
        registry.register_adapter(Adapter(name, access_level=access, implemented=True, reason=""))
    for name in ("docker", "frigate", "z2m", "zwave", "esphome", "nodered", "storage",
                 "auth_handoff", "human_action"):
        registry.register_adapter(Adapter(name))
    # Explicit method dependencies make fake and real runtimes use the same contract.
    reads = {
        "confirmed_disease": ("workflow", ()), "context_value": ("workflow", ()),
        "context_list": ("workflow", ()), "wait": ("workflow", ()),
        "addon_info": ("supervisor", ("supervisor.addons",)),
        "config_entry_info": ("ha", ("ha.bridge_snapshot",)),
        "config_entry_state": ("ha", ("ha.bridge_snapshot",)),
        "entity_registry_info": ("ha", ("ha.ws_command",)),
        "update_state": ("ha", ("ha.get_states",)),
        "core_memory_stability": ("supervisor", ("supervisor.core_stats",)),
        "read_host_metrics": ("supervisor", ("supervisor.host_logs_current",)),
        "network_primary_info": ("network", ("supervisor.network_info",)),
        "mqtt_probe": ("mqtt", ("supervisor.addon_logs",)),
        "check_config": ("ha", ("ha.call_service",)),
    }
    for primitive, (adapter, methods) in reads.items():
        registry.register(Capability(f"{adapter}.{primitive}", adapter, primitive, backend_methods=methods))
    writes = {
        "config_entry_set_enabled": ("ha", "entry_id", None, ("ha.ws_command", "ha.bridge_snapshot")),
        "reload_config_entry": ("ha", "entry_id", None, ("ha.bridge_reload_entry",)),
        "reload_config_entry_verified": ("ha", "entry_id", None, ("ha.bridge_reload_entry", "ha.bridge_snapshot")),
        "set_entity_device_class": ("ha", "entity_id", None, ("ha.ws_command",)),
        "set_entity_enabled": ("ha", "entity_id", None, ("ha.ws_command", "ha.bridge_reload_entry", "ha.get_states")),
        "google_assistant_set_exposed": ("ha", "entity_id", None, ("ha.ws_command", "ha.call_service")),
        "install_update": ("ha", "entity_id", None, ("ha.install_update", "ha.get_states")),
        "ensure_update_current": ("ha", "target", None, ("ha.install_update", "ha.get_states")),
        "reload_subsystem": ("ha", "subsystem", None, ("ha.call_service",)),
        "restart_addon": ("supervisor", "slug", None, ("supervisor.restart_addon", "supervisor.addons")),
        "restart_core": ("supervisor", None, "home_assistant_core", ("supervisor.restart_core", "ha.get_config")),
        "create_backup": ("supervisor", None, "home_assistant_installation", ("supervisor.backups_info", "ha.call_service")),
        "install_hacs_supported": ("supervisor", None, "hacs_official_get_hacs", ("supervisor.store_info", "supervisor.add_store_repository", "supervisor.reload_store", "supervisor.install_store_addon", "supervisor.start_addon")),
        "network_set_primary_dns": ("network", None, "unique_primary_ipv4_auto", ("supervisor.network_info", "supervisor.set_primary_auto_dns")),
    }
    for primitive, (adapter, target, fixed, methods) in writes.items():
        high = primitive in {"restart_core", "network_set_primary_dns", "install_hacs_supported", "ensure_update_current", "install_update"}
        registry.register(Capability(f"{adapter}.{primitive}", adapter, primitive, "write",
            "high" if high else "medium", "protocol_gate", target, fixed,
            "native_backup_true" if primitive in {"install_update", "ensure_update_current"} else "protocol_checkpoint",
            "protocol_defined" if primitive in {"network_set_primary_dns", "config_entry_set_enabled", "set_entity_device_class", "set_entity_enabled", "google_assistant_set_exposed"} else "no_automatic_undo",
            methods))
    registry.register(Capability("recorder.verify_recorder_write", "recorder", "verify_recorder_write",
        "probe", "low", "diagnostic_probe", fixed_target="sensor.suzie_doctor_recorder_probe",
        backend_methods=("ha.recorder_write_probe",)))
    registry.register(Capability("ha.notify_user", "ha", "notify_user", "notify", "low", "protocol_gate",
        fixed_target="local_ha_persistent_notification", backend_methods=("ha.persistent_notification",)))
    return registry


class DoctorConnector:
    def __init__(self, ha: Any, supervisor: Any) -> None:
        self.ha, self.supervisor = ha, supervisor
        self.registry = build_registry()
        self.health: dict[str, str] = {}

    def _available(self, cap: Capability) -> tuple[bool, str]:
        if not self.registry.adapters[cap.adapter_id].implemented:
            return False, "adapter_unsupported"
        for path in cap.backend_methods:
            owner, method = path.split(".")
            if not callable(getattr(getattr(self, owner), method, None)):
                return False, f"backend_missing:{path}"
            if self.health.get(owner) == "unavailable":
                return False, f"backend_unavailable:{owner}"
        return True, "implemented; execution still requires protocol permission"

    async def refresh_health(self) -> None:
        async def probe(owner: str, method: str) -> tuple[str, str]:
            try:
                value = await asyncio.wait_for(getattr(getattr(self, owner), method)(), 10)
                return owner, "ok" if isinstance(value, dict) and value else "unknown"
            except Exception:
                return owner, "unavailable"
        self.health.update(await asyncio.gather(probe("ha", "get_config"), probe("supervisor", "info")))

    def discovery(self) -> dict[str, Any]:
        adapters = []
        for adapter_id, adapter in sorted(self.registry.adapters.items()):
            capabilities = []
            for _, cap in sorted(self.registry.capabilities.items()):
                if cap.adapter_id != adapter_id:
                    continue
                available, reason = self._available(cap)
                capabilities.append({**asdict(cap), "available": available, "reason": reason})
            owners = {p.split(".")[0] for c in capabilities for p in c["backend_methods"]}
            statuses = {self.health.get(o, "unknown") for o in owners}
            health = "unsupported" if not adapter.implemented else (
                "unavailable" if "unavailable" in statuses else "unknown" if "unknown" in statuses else "ok")
            adapters.append({**asdict(adapter), "available": any(c["available"] for c in capabilities),
                "health": health, "capabilities": capabilities,
                "read_operations": [c["capability_id"] for c in capabilities if c["operation"] == "read"],
                "write_operations": [c["capability_id"] for c in capabilities if c["operation"] != "read"],
                "high_risk_operations": [c["capability_id"] for c in capabilities if c["risk"] == "high"]})
        return {"connector_version": CONNECTOR_VERSION, "capability_schema_version": CAPABILITY_SCHEMA_VERSION,
                "availability_is_permission": False, "adapters": adapters}

    def required_capabilities(self, primitives: set[str]) -> list[str]:
        return [self.registry.primitives[p].capability_id if p in self.registry.primitives else f"unsupported:{p}"
                for p in sorted(primitives)]

    def check(self, primitives: set[str]) -> list[str]:
        errors = []
        for name in sorted(primitives):
            cap = self.registry.primitives.get(name)
            if cap is None:
                errors.append(f"unsupported_primitive:{name}")
            elif not self._available(cap)[0]:
                errors.append(f"unavailable:{cap.capability_id}")
        return errors

    async def dispatch(self, name: str, args: dict[str, Any], env: dict[str, Any], implementation: Any) -> Any:
        errors = self.check({name})
        if errors:
            raise ConnectorError(errors[0])
        cap = self.registry.primitives[name]
        if cap.operation == "write" and cap.target_field:
            value = args.get(cap.target_field)
            targets = value if isinstance(value, list) else [value]
            if not targets or any(not isinstance(v, str) or not v.strip() or
                    any(x in v for x in ("*", "/", "\x00", "$")) for v in targets):
                raise ConnectorError(f"exact_target_required:{cap.target_field}")
        # Preserve original exception/false semantics. Never turn adapter errors into success.
        return await implementation(name, args, env)
