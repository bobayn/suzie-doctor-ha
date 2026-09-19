from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from . import (
    CONNECTOR_INTERFACE_VERSION,
    CONNECTOR_SCHEMA_VERSION,
    CONNECTOR_VERSION,
)
from .connector_registry import CapabilityRegistry, CapabilityRegistryError
if TYPE_CHECKING:
    from .ha_api import HomeAssistantClient
    from .protocol_engine import ProtocolEngine
    from .supervisor import SupervisorClient
from .skill import SkillCore
from .suite import SuiteRuntime


class ConnectorError(RuntimeError):
    pass


DiagnoseCallback = Callable[..., Awaitable[dict[str, Any]]]


class ConnectorCore:
    def __init__(
        self,
        *,
        suite: SuiteRuntime,
        skill: SkillCore,
        ha: HomeAssistantClient,
        supervisor: SupervisorClient,
        protocol_engine: ProtocolEngine,
        diagnose_callback: DiagnoseCallback,
        contract_path: str | Path = "/app/suite/connector_contract.json",
    ) -> None:
        self.suite = suite
        self.skill = skill
        self.ha = ha
        self.supervisor = supervisor
        self.protocol_engine = protocol_engine
        self.diagnose_callback = diagnose_callback
        self.contract_path = Path(contract_path)
        self.contract = self._load_contract()
        try:
            self.registry = CapabilityRegistry(self.contract)
        except CapabilityRegistryError as exc:
            raise ConnectorError(f"Connector registry invalid: {exc}") from exc
        self._validate_contract()

    def _load_contract(self) -> dict[str, Any]:
        if not self.contract_path.exists():
            raise ConnectorError(f"Connector contract not found: {self.contract_path}")
        try:
            data = json.loads(self.contract_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ConnectorError(f"Connector contract is invalid: {exc}") from exc
        if not isinstance(data, dict):
            raise ConnectorError("Connector contract must be an object")
        return data

    def _validate_contract(self) -> None:
        if str(self.contract.get("version") or "") != CONNECTOR_VERSION:
            raise ConnectorError("Connector version mismatch")
        if int(self.contract.get("interface_version") or 0) != CONNECTOR_INTERFACE_VERSION:
            raise ConnectorError("Connector interface version mismatch")
        if int(self.contract.get("schema_version") or 0) != CONNECTOR_SCHEMA_VERSION:
            raise ConnectorError("Connector schema version mismatch")
        if self.contract.get("canonical_core") is not True:
            raise ConnectorError("Connector contract is not canonical")
        forbidden = ("shell", "eval", "exec", "python", "sql.write")
        for item in self.registry.tool_catalog():
            name = str(item.get("name") or "")
            lowered = name.lower()
            if any(token in lowered for token in forbidden):
                raise ConnectorError(f"Forbidden generic tool in Connector contract: {name}")

    def tool_catalog(self) -> list[dict[str, Any]]:
        return self.registry.tool_catalog()

    def capabilities(self) -> dict[str, Any]:
        registry = self.registry.snapshot()
        return {
            "connector": {
                "version": CONNECTOR_VERSION,
                "interface_version": CONNECTOR_INTERFACE_VERSION,
                "schema_version": CONNECTOR_SCHEMA_VERSION,
                "canonical_core": True,
                "registry": "adapter_capability_registry_v1",
            },
            "suite": self.suite.status(),
            "skill": self.skill.descriptor(include_text=False),
            "families": registry["families"],
            "adapters": registry["adapters"],
            "capabilities": registry["capabilities"],
            "tools": self.tool_catalog(),
            "protocol_engine_primitives": sorted(
                self.protocol_engine.SUPPORTED_PRIMITIVES
            ),
            "treatment_path": str(self.contract.get("treatment_path") or ""),
        }

    async def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        trusted_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        trusted = dict(trusted_context or {})
        try:
            self.registry.resolve_tool(tool_name)
            self.registry.validate_arguments(tool_name, args)
        except CapabilityRegistryError as exc:
            raise ConnectorError(str(exc)) from exc

        if tool_name == "doctor.capabilities":
            return self.capabilities()

        if tool_name == "doctor.suite":
            return self.suite.status()

        if tool_name == "doctor.skill":
            return self.skill.descriptor(
                include_text=bool(args.get("include_text", False))
            )

        if tool_name == "doctor.diagnose":
            evidence = args.get("evidence")
            if not isinstance(evidence, dict):
                raise ConnectorError("doctor.diagnose requires evidence object")
            execute = bool(args.get("execute", False))
            if "explicit_confirmation" in args:
                raise ConnectorError(
                    "explicit_confirmation is transport-owned and must not be supplied "
                    "as a model tool argument"
                )
            explicit_confirmation = bool(
                trusted.get("human_confirmation_verified", False)
            )
            if execute and not self.suite.compatible:
                return {
                    "result": "TREATMENT_BLOCKED",
                    "reason": "suite_incompatible",
                    "suite": self.suite.status(),
                }
            return await self.diagnose_callback(
                evidence,
                execute=execute,
                explicit_confirmation=explicit_confirmation,
            )

        if tool_name == "ha.config.read":
            return {"config": await self.ha.get_config()}
        if tool_name == "ha.repairs.list":
            return {"repairs": await self.ha.list_repairs()}
        if tool_name == "ha.notifications.list":
            return {"notifications": await self.ha.list_persistent_notifications()}
        if tool_name == "ha.config_entries.list":
            domain = str(args.get("domain") or "")
            return {"config_entries": await self.ha.list_config_entries(domain)}

        if tool_name == "supervisor.info":
            return {"supervisor": await self.supervisor.supervisor_info()}
        if tool_name == "supervisor.host.info":
            return {"host": await self.supervisor.host_info()}
        if tool_name == "supervisor.core.info":
            return {"core": await self.supervisor.core_info()}
        if tool_name == "supervisor.network.info":
            return {"network": await self.supervisor.network_info()}
        if tool_name == "supervisor.addons.list":
            return {"addons": await self.supervisor.addons()}
        if tool_name == "supervisor.mounts.list":
            return {"mounts": await self.supervisor.mounts_info()}
        if tool_name == "supervisor.backups.list":
            return {"backups": await self.supervisor.backups_info()}

        raise ConnectorError(f"Connector tool has no implementation: {tool_name}")
