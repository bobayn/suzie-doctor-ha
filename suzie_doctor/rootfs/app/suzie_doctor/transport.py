"""Surface-neutral Doctor tools. Adapters decode envelopes, never decide treatment.

SessionContext must be constructed by a trusted host authentication/approval layer,
never from model arguments or request JSON. No auth server or public listener here.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from .connector import ConnectorError

CONTRACT_FIELDS = ("connector_core_version", "connector_interface_version",
                   "skill_core_version", "skill_schema_version")
ADAPTER_CONTRACT = {"connector_core_version": "0.2.0", "connector_interface_version": 1,
                    "skill_core_version": "0.2.0", "skill_schema_version": 1}
TOOLS = ("doctor.capabilities", "doctor.skill", "doctor.diagnose", "doctor.treat")


@dataclass(frozen=True)
class SessionContext:
    permissions: frozenset[str] = frozenset()
    explicit_confirmation: bool = False


class DoctorConnectorCore:
    def __init__(self, runtime: Any):
        self.runtime = runtime
        self.engine = runtime.protocol_engine

    def compatibility(self, declaration: Any) -> dict[str, Any]:
        status = self.engine.suite.status(known=set(self.engine.connector.registry.capabilities))
        errors = list(status["errors"])
        if not isinstance(declaration, dict):
            declaration = {}
        for key in CONTRACT_FIELDS:
            value = declaration.get(key)
            expected = status[key]
            if type(value) is not type(expected) or value != expected:
                errors.append(f"adapter_contract_mismatch:{key}")
        return {**status, "errors": sorted(set(errors)), "compatible": not errors,
                "treatment_allowed": not errors}

    async def call(self, name: str, arguments: dict[str, Any], *,
                   declaration: dict[str, Any], session: SessionContext) -> Any:
        if name not in TOOLS or not isinstance(arguments, dict):
            raise ConnectorError("invalid_tool_request")
        if "doctor.read" not in session.permissions:
            raise ConnectorError("permission_denied:doctor.read")
        compatibility = self.compatibility(declaration)
        if name in {"doctor.capabilities", "doctor.skill"} and arguments:
            raise ConnectorError("unexpected_tool_arguments")
        if name == "doctor.capabilities":
            await self.engine.connector.refresh_health()
            return {**self.engine.connector.discovery(), "tools": tool_contract(),
                    "compatibility": compatibility}
        if name == "doctor.skill":
            return {**self.engine.suite.skill_bundle(), "compatibility": compatibility}
        if set(arguments) != {"evidence"} or not isinstance(arguments["evidence"], dict):
            raise ConnectorError("evidence_object_required")
        evidence = arguments["evidence"]
        if any(k in evidence for k in ("execute", "explicit_confirmation", "trust_mode", "developer_override")):
            raise ConnectorError("session_policy_is_not_a_tool_argument")
        execute = name == "doctor.treat"
        if execute:
            if "doctor.treat" not in session.permissions:
                raise ConnectorError("permission_denied:doctor.treat")
            if not compatibility["treatment_allowed"]:
                return {"result": "ADAPTER_INCOMPATIBLE", "compatibility": compatibility}
        # Same signed packages, client binding, ProtocolEngine, verification, rollback,
        # and persistent audit for both surfaces. No API-only protocols or primitives.
        return await self.runtime.doctor_server_diagnose(
            evidence, execute=execute,
            explicit_confirmation=session.explicit_confirmation if execute else False)


class WebAdapter:
    adapter_id = "web"

    def __init__(self, core: DoctorConnectorCore, declaration: dict[str, Any] | None = None):
        self.core = core
        self.declaration = dict(ADAPTER_CONTRACT if declaration is None else declaration)

    async def call(self, envelope: dict[str, Any], session: SessionContext) -> Any:
        if not isinstance(envelope, dict) or set(envelope) != {"tool", "arguments"}:
            raise ConnectorError("invalid_web_envelope")
        return await self.core.call(envelope["tool"], envelope["arguments"],
                                    declaration=self.declaration, session=session)


class APIAdapter(WebAdapter):
    adapter_id = "api"

    async def call(self, envelope: dict[str, Any], session: SessionContext) -> Any:
        # Normalized tools/function-calling envelope; SDK/MCP glue only translates it.
        if not isinstance(envelope, dict) or set(envelope) != {"name", "arguments"}:
            raise ConnectorError("invalid_api_envelope")
        return await self.core.call(envelope["name"], envelope["arguments"],
                                    declaration=self.declaration, session=session)


def tool_contract() -> list[dict[str, Any]]:
    """Canonical tool schemas for Web/MCP and API loaders; no surface policy."""
    evidence = {"type": "object", "properties": {"evidence": {"type": "object"}},
                "required": ["evidence"], "additionalProperties": False}
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    return [{"name": name, "inputSchema": evidence if name in {"doctor.diagnose", "doctor.treat"} else empty,
             "description": {
                 "doctor.capabilities": "Discover environment capabilities and compatibility; availability is not permission.",
                 "doctor.skill": "Load the canonical versioned Skill Core and references.",
                 "doctor.diagnose": "Read-only signed server diagnosis; never execute treatment.",
                 "doctor.treat": "Run signed server protocols through shared safety, checkpoint, verify, rollback and audit gates."
             }[name]} for name in TOOLS]
