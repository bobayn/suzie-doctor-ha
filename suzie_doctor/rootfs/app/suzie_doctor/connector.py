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


def normalize_doctor_risk_assessment(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ConnectorError(
            "state-changing Doctor action requires Suzie Doctor risk_assessment"
        )
    allowed = {
        "harm_probability": {"LOW", "MEDIUM", "HIGH"},
        "irreversibility": {"REVERSIBLE", "PARTIALLY_REVERSIBLE", "IRREVERSIBLE"},
        "harm_magnitude": {"LOW", "MODERATE", "SUBSTANTIAL", "CATASTROPHIC"},
        "decision": {"PROCEED", "AVOID"},
    }
    normalized: dict[str, str] = {}
    for key, choices in allowed.items():
        item = str(value.get(key) or "").strip().upper()
        if item not in choices:
            raise ConnectorError(f"risk_assessment.{key} is invalid")
        normalized[key] = item
    rationale = str(value.get("rationale") or "").strip()
    if not rationale:
        raise ConnectorError("risk_assessment.rationale is required")
    normalized["rationale"] = rationale[:2000]
    return normalized


DiagnoseCallback = Callable[..., Awaitable[dict[str, Any]]]
FieldActionCallback = Callable[..., Awaitable[dict[str, Any]]]


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
        field_action_callback: FieldActionCallback | None = None,
        contract_path: str | Path = "/app/suite/connector_contract.json",
    ) -> None:
        self.suite = suite
        self.skill = skill
        self.ha = ha
        self.supervisor = supervisor
        self.protocol_engine = protocol_engine
        self.diagnose_callback = diagnose_callback
        self.field_action_callback = field_action_callback
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
            "field_actions": list(self.contract.get("field_actions") or []),
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

        if tool_name == "doctor.action.request":
            actor = str(trusted.get("execution_actor") or "").strip().lower()
            case_id = int(trusted.get("case_id") or 0)
            if actor != "field_suzie" or case_id <= 0:
                raise ConnectorError("doctor.action.request is Field-Suzie Case only")
            if self.field_action_callback is None:
                raise ConnectorError("Field action callback is unavailable")
            action = args.get("action")
            exact_target = args.get("exact_target")
            evidence = args.get("evidence")
            verify_criterion = args.get("verify_criterion")
            if not isinstance(action, dict) or not (str(action.get("name") or "").strip() or str(action.get("primitive") or "").strip()):
                raise ConnectorError("doctor.action.request requires action.name or action.primitive")
            if not isinstance(exact_target, dict) or not exact_target:
                raise ConnectorError("doctor.action.request requires structured exact_target")
            if not isinstance(evidence, dict):
                raise ConnectorError("doctor.action.request requires evidence object")
            if not isinstance(verify_criterion, dict):
                raise ConnectorError("doctor.action.request requires verify_criterion object")
            risk_assessment = normalize_doctor_risk_assessment(args.get("risk_assessment"))
            if not self.suite.compatible:
                return {
                    "result": "TREATMENT_BLOCKED",
                    "reason": "suite_incompatible",
                    "suite": self.suite.status(),
                }
            command_id = str(trusted.get("command_id") or "").strip()
            if not command_id:
                raise ConnectorError("doctor.action.request requires trusted command_id")
            return await self.field_action_callback(
                {
                    "command_id": command_id,
                    "field_case_id": case_id,
                    "action": dict(action),
                    "exact_target": dict(exact_target),
                    "reason": str(args.get("reason") or ""),
                    "evidence": dict(evidence),
                    "risk_assessment": risk_assessment,
                    "expected_result": str(args.get("expected_result") or ""),
                    "verify_criterion": dict(verify_criterion),
                    "checkpoint": args.get("checkpoint"),
                    "rollback": args.get("rollback"),
                    "fallback": args.get("fallback"),
                    "routing_intent": "FIELD_ACTION_REQUEST",
                },
                risk_assessment=risk_assessment,
            )

        if tool_name == "doctor.diagnose":
            evidence = args.get("evidence")
            if not isinstance(evidence, dict):
                raise ConnectorError("doctor.diagnose requires evidence object")
            execute = bool(args.get("execute", False))
            if "explicit_confirmation" in args:
                raise ConnectorError(
                    "explicit_confirmation is obsolete; Suzie Doctor must supply "
                    "risk_assessment instead"
                )
            actor = str(trusted.get("execution_actor") or "field_suzie").strip().lower()
            if actor not in {"field_suzie", "family_doctor"}:
                raise ConnectorError("trusted execution_actor is invalid")
            risk_raw = args.get("risk_assessment")
            if actor == "field_suzie":
                risk_assessment = (
                    normalize_doctor_risk_assessment(risk_raw)
                    if execute or risk_raw is not None
                    else None
                )
            else:
                # Family Doctor receives execution authority only from trusted
                # server/internal context.  It cannot be selected by tool args.
                risk_assessment = (
                    normalize_doctor_risk_assessment(risk_raw)
                    if risk_raw is not None
                    else None
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
                risk_assessment=risk_assessment,
                execution_actor=actor,
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
