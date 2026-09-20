from __future__ import annotations

from copy import deepcopy
from typing import Any


class CapabilityRegistryError(RuntimeError):
    pass


_ALLOWED_RISKS = {
    "read_only",
    "structured_write",
    "signed_treatment",
    "high_risk",
}
_ALLOWED_TARGET_POLICIES = {
    "none",
    "exact_argument",
    "signed_execution_package",
    "structured_backend",
}
_ALLOWED_CONFIRMATION_CLASSES = {
    "none",
    "transport_verified",
    "protocol_defined",
    "human_required",
    "autonomous_risk_review",
}


class CapabilityRegistry:
    """Validated, deterministic adapter/capability registry for Connector Core."""

    def __init__(self, contract: dict[str, Any]) -> None:
        if not isinstance(contract, dict):
            raise CapabilityRegistryError("Connector contract must be an object")
        self._adapters: dict[str, dict[str, Any]] = {}
        self._capabilities: dict[str, dict[str, Any]] = {}
        self._tool_order: list[str] = []

        adapters = contract.get("adapters")
        if not isinstance(adapters, list) or not adapters:
            raise CapabilityRegistryError("Connector contract has no adapters")
        for item in adapters:
            self.register_adapter(item)

        tools = contract.get("tools")
        if not isinstance(tools, list) or not tools:
            raise CapabilityRegistryError("Connector contract has no tools")
        for item in tools:
            self.register_capability(item)

    @staticmethod
    def _require_text(item: dict[str, Any], key: str) -> str:
        value = str(item.get(key) or "").strip()
        if not value:
            raise CapabilityRegistryError(f"{key} must be non-empty")
        return value

    def register_adapter(self, item: dict[str, Any]) -> None:
        if not isinstance(item, dict):
            raise CapabilityRegistryError("adapter record must be an object")
        adapter_id = self._require_text(item, "adapter_id")
        if adapter_id in self._adapters:
            raise CapabilityRegistryError(f"duplicate adapter_id: {adapter_id}")

        family = self._require_text(item, "family")
        version = self._require_text(item, "adapter_version")
        health = self._require_text(item, "health")
        permissions = self._require_text(item, "permissions")
        confirmation_class = self._require_text(item, "confirmation_class")
        if confirmation_class not in _ALLOWED_CONFIRMATION_CLASSES:
            raise CapabilityRegistryError(
                f"adapter {adapter_id} has invalid confirmation_class"
            )

        compatibility = item.get("compatibility")
        if not isinstance(compatibility, dict):
            raise CapabilityRegistryError(
                f"adapter {adapter_id} compatibility must be an object"
            )

        for key in ("read_operations", "write_operations", "dangerous_operations"):
            value = item.get(key)
            if not isinstance(value, list) or any(not str(x).strip() for x in value):
                raise CapabilityRegistryError(
                    f"adapter {adapter_id} {key} must be a list of names"
                )

        normalized = deepcopy(item)
        normalized.update(
            {
                "adapter_id": adapter_id,
                "family": family,
                "adapter_version": version,
                "available": bool(item.get("available")),
                "health": health,
                "permissions": permissions,
                "confirmation_class": confirmation_class,
                "checkpoint_support": item.get("checkpoint_support", False),
                "rollback_support": item.get("rollback_support", False),
            }
        )
        reason = normalized.get("reason")
        if not normalized["available"] and not str(reason or "").strip():
            raise CapabilityRegistryError(
                f"unavailable adapter {adapter_id} must provide reason"
            )
        self._adapters[adapter_id] = normalized

    def register_capability(self, item: dict[str, Any]) -> None:
        if not isinstance(item, dict):
            raise CapabilityRegistryError("capability record must be an object")
        name = self._require_text(item, "name")
        if name in self._capabilities:
            raise CapabilityRegistryError(f"duplicate capability/tool: {name}")

        adapter_id = self._require_text(item, "adapter_id")
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            raise CapabilityRegistryError(
                f"capability {name} references unknown adapter {adapter_id}"
            )
        family = self._require_text(item, "family")
        if family != adapter.get("family"):
            raise CapabilityRegistryError(
                f"capability {name} family does not match adapter {adapter_id}"
            )

        risk = self._require_text(item, "risk")
        if risk not in _ALLOWED_RISKS:
            raise CapabilityRegistryError(f"capability {name} has invalid risk")

        target_policy = self._require_text(item, "target_policy")
        if target_policy not in _ALLOWED_TARGET_POLICIES:
            raise CapabilityRegistryError(
                f"capability {name} has invalid target_policy"
            )
        target_fields = item.get("target_fields", [])
        if not isinstance(target_fields, list):
            raise CapabilityRegistryError(
                f"capability {name} target_fields must be a list"
            )
        if target_policy == "exact_argument" and not target_fields:
            raise CapabilityRegistryError(
                f"capability {name} exact_argument requires target_fields"
            )
        if risk != "read_only" and target_policy == "none":
            raise CapabilityRegistryError(
                f"mutating capability {name} must define target policy"
            )

        input_schema = item.get("input_schema")
        if not isinstance(input_schema, dict):
            raise CapabilityRegistryError(
                f"capability {name} input_schema must be an object"
            )

        normalized = deepcopy(item)
        normalized.update(
            {
                "name": name,
                "adapter_id": adapter_id,
                "family": family,
                "risk": risk,
                "target_policy": target_policy,
                "target_fields": [str(x) for x in target_fields],
                "checkpoint_support": item.get("checkpoint_support", False),
                "rollback_support": item.get("rollback_support", False),
                "confirmation_class": str(
                    item.get("confirmation_class")
                    or adapter.get("confirmation_class")
                    or "none"
                ),
            }
        )
        if normalized["confirmation_class"] not in _ALLOWED_CONFIRMATION_CLASSES:
            raise CapabilityRegistryError(
                f"capability {name} has invalid confirmation_class"
            )
        self._capabilities[name] = normalized
        self._tool_order.append(name)

    def tool_catalog(self) -> list[dict[str, Any]]:
        return [deepcopy(self._capabilities[name]) for name in self._tool_order]

    def adapter(self, adapter_id: str) -> dict[str, Any]:
        item = self._adapters.get(str(adapter_id))
        if item is None:
            raise CapabilityRegistryError(f"unknown adapter: {adapter_id}")
        return deepcopy(item)

    def capability(self, name: str) -> dict[str, Any]:
        item = self._capabilities.get(str(name))
        if item is None:
            raise CapabilityRegistryError(f"unknown capability/tool: {name}")
        return deepcopy(item)

    def resolve_tool(self, name: str) -> dict[str, Any]:
        capability = self.capability(name)
        adapter = self.adapter(str(capability.get("adapter_id") or ""))
        if not bool(adapter.get("available")):
            reason = str(adapter.get("reason") or "adapter_unavailable")
            raise CapabilityRegistryError(
                f"adapter unavailable for {name}: {reason}"
            )
        return capability

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> None:
        capability = self.capability(name)
        if not isinstance(arguments, dict):
            raise CapabilityRegistryError("tool arguments must be an object")
        if capability.get("target_policy") == "exact_argument":
            missing = [
                field
                for field in capability.get("target_fields") or []
                if not str(arguments.get(str(field)) or "").strip()
            ]
            if missing:
                raise CapabilityRegistryError(
                    f"{name} requires exact target fields: {','.join(missing)}"
                )

    def snapshot(self) -> dict[str, Any]:
        adapters = [
            deepcopy(self._adapters[key])
            for key in sorted(self._adapters)
        ]
        capabilities = [
            deepcopy(self._capabilities[key])
            for key in sorted(self._capabilities)
        ]
        families: dict[str, dict[str, Any]] = {}
        for adapter in adapters:
            family = str(adapter.get("family") or "")
            entry = families.setdefault(
                family,
                {
                    "available": False,
                    "health": "unsupported",
                    "adapters": [],
                    "reasons": [],
                },
            )
            entry["adapters"].append(str(adapter.get("adapter_id") or ""))
            if bool(adapter.get("available")):
                entry["available"] = True
                entry["health"] = str(adapter.get("health") or "unknown")
            elif str(adapter.get("reason") or ""):
                entry["reasons"].append(str(adapter.get("reason")))
        for entry in families.values():
            entry["adapters"] = sorted(entry["adapters"])
            entry["reasons"] = sorted(set(entry["reasons"]))

        return {
            "adapters": adapters,
            "capabilities": capabilities,
            "families": {
                key: families[key]
                for key in sorted(families)
            },
        }


def registry_selftest(contract: dict[str, Any]) -> dict[str, Any]:
    """Pure registry regression suite; no Home Assistant calls are made."""

    cases: list[dict[str, Any]] = []

    def add(case_id: str, passed: bool, detail: Any = None) -> None:
        item: dict[str, Any] = {"id": case_id, "passed": bool(passed)}
        if detail is not None:
            item["detail"] = detail
        cases.append(item)

    registry = CapabilityRegistry(contract)
    snapshot_a = registry.snapshot()
    snapshot_b = registry.snapshot()
    add("registry_loads", bool(snapshot_a.get("adapters")))
    add("capability_discovery_deterministic", snapshot_a == snapshot_b)

    metadata_valid = all(
        bool(item.get("adapter_id"))
        and bool(item.get("family"))
        and item.get("risk") in _ALLOWED_RISKS
        and bool(item.get("target_policy"))
        and "checkpoint_support" in item
        and "rollback_support" in item
        for item in snapshot_a.get("capabilities") or []
    )
    add("capability_metadata_valid", metadata_valid)

    duplicate_adapter_ok = False
    try:
        duplicate = CapabilityRegistry(contract)
        duplicate.register_adapter((contract.get("adapters") or [])[0])
    except CapabilityRegistryError:
        duplicate_adapter_ok = True
    add("duplicate_adapter_rejected", duplicate_adapter_ok)

    duplicate_capability_ok = False
    try:
        duplicate = CapabilityRegistry(contract)
        duplicate.register_capability((contract.get("tools") or [])[0])
    except CapabilityRegistryError:
        duplicate_capability_ok = True
    add("duplicate_capability_rejected", duplicate_capability_ok)

    unknown_fail_closed = False
    try:
        registry.resolve_tool("does.not.exist")
    except CapabilityRegistryError:
        unknown_fail_closed = True
    add("unsupported_operation_fail_closed", unknown_fail_closed)

    unavailable = [
        item
        for item in snapshot_a.get("adapters") or []
        if not bool(item.get("available"))
    ]
    add(
        "unavailable_adapter_reports_unavailable",
        bool(unavailable)
        and all(bool(str(item.get("reason") or "").strip()) for item in unavailable),
        [item.get("adapter_id") for item in unavailable],
    )

    target_contract = {
        "adapters": [
            {
                "adapter_id": "fake.adapter",
                "adapter_version": "1",
                "family": "fake",
                "available": True,
                "health": "ready",
                "reason": "",
                "permissions": "test",
                "read_operations": [],
                "write_operations": ["fake.write"],
                "dangerous_operations": [],
                "confirmation_class": "none",
                "checkpoint_support": True,
                "rollback_support": True,
                "compatibility": {"interface_version": 1},
            }
        ],
        "tools": [
            {
                "name": "fake.write",
                "adapter_id": "fake.adapter",
                "family": "fake",
                "risk": "structured_write",
                "target_policy": "exact_argument",
                "target_fields": ["entity_id"],
                "checkpoint_support": True,
                "rollback_support": True,
                "confirmation_class": "none",
                "input_schema": {
                    "type": "object",
                    "properties": {"entity_id": {"type": "string"}},
                    "required": ["entity_id"],
                    "additionalProperties": False,
                },
            }
        ],
    }
    target_registry = CapabilityRegistry(target_contract)
    exact_target_ok = False
    try:
        target_registry.validate_arguments("fake.write", {})
    except CapabilityRegistryError:
        exact_target_ok = True
    add("exact_target_enforced", exact_target_ok)
    capability = target_registry.capability("fake.write")
    add(
        "checkpoint_rollback_metadata_preserved",
        capability.get("checkpoint_support") is True
        and capability.get("rollback_support") is True,
    )

    return {
        "result": "PASS" if all(x["passed"] for x in cases) else "FAIL",
        "cases": cases,
    }
