from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import (
    APP_VERSION,
    BRIDGE_VERSION,
    CONNECTOR_INTERFACE_VERSION,
    CONNECTOR_SCHEMA_VERSION,
    CONNECTOR_VERSION,
    DOCTOR_SERVER_API_VERSION,
    PROTOCOL_CARD_SCHEMA_VERSION,
    PROTOCOL_PACK_VERSION,
    PROTOCOL_PRIMITIVE_SET_VERSION,
    SKILL_SCHEMA_VERSION,
    SKILL_VERSION,
    SUITE_VERSION,
)
from .skill import SkillCore


class SuiteError(RuntimeError):
    pass


class SuiteRuntime:
    def __init__(
        self,
        manifest_path: str | Path = "/app/suite/manifest.json",
        skill_root: str | Path = "/app/suzie_doctor_skill",
    ) -> None:
        self.manifest_path = Path(manifest_path)
        self.skill = SkillCore(skill_root)
        self.manifest = self._load_manifest()
        self.errors = self._compatibility_errors()
        self.compatible = not self.errors

    def _load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            raise SuiteError(f"Suite manifest not found: {self.manifest_path}")
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SuiteError(f"Suite manifest is invalid: {exc}") from exc
        if not isinstance(data, dict):
            raise SuiteError("Suite manifest must be an object")
        return data

    def _compatibility_errors(
        self, manifest: dict[str, Any] | None = None
    ) -> list[str]:
        errors: list[str] = []
        manifest = self.manifest if manifest is None else manifest

        def expect(label: str, actual: Any, expected: Any) -> None:
            if actual != expected:
                errors.append(f"{label}:{actual!r}!={expected!r}")

        connector = manifest.get("connector") or {}
        skill = manifest.get("skill") or {}
        protocol = manifest.get("protocol") or {}
        surfaces = manifest.get("surfaces") or {}
        policy = manifest.get("policy") or {}

        expect("suite_version", manifest.get("suite_version"), SUITE_VERSION)
        expect("app_version", manifest.get("app_version"), APP_VERSION)
        expect("connector.version", connector.get("version"), CONNECTOR_VERSION)
        expect(
            "connector.interface_version",
            connector.get("interface_version"),
            CONNECTOR_INTERFACE_VERSION,
        )
        expect(
            "connector.schema_version",
            connector.get("schema_version"),
            CONNECTOR_SCHEMA_VERSION,
        )
        expect("skill.version", skill.get("version"), SKILL_VERSION)
        expect("skill.schema_version", skill.get("schema_version"), SKILL_SCHEMA_VERSION)
        expect(
            "skill.connector_interface_version",
            skill.get("connector_interface_version"),
            CONNECTOR_INTERFACE_VERSION,
        )
        expect("skill.runtime_version", self.skill.version, SKILL_VERSION)
        expect("skill.runtime_schema", self.skill.schema_version, SKILL_SCHEMA_VERSION)
        expect(
            "skill.runtime_connector_interface",
            self.skill.connector_interface_version,
            CONNECTOR_INTERFACE_VERSION,
        )
        expect(
            "protocol.card_schema_version",
            protocol.get("card_schema_version"),
            PROTOCOL_CARD_SCHEMA_VERSION,
        )
        expect(
            "protocol.primitive_set_version",
            protocol.get("primitive_set_version"),
            PROTOCOL_PRIMITIVE_SET_VERSION,
        )
        expect(
            "doctor_server_api",
            manifest.get("doctor_server_api"),
            DOCTOR_SERVER_API_VERSION,
        )
        expect("bridge_version", manifest.get("bridge_version"), BRIDGE_VERSION)
        expect(
            "emergency_pack_version",
            manifest.get("emergency_pack_version"),
            PROTOCOL_PACK_VERSION,
        )
        expect(
            "surface.web.adapter_contract",
            (surfaces.get("web") or {}).get("adapter_contract"),
            CONNECTOR_INTERFACE_VERSION,
        )
        expect(
            "surface.api.adapter_contract",
            (surfaces.get("api") or {}).get("adapter_contract"),
            CONNECTOR_INTERFACE_VERSION,
        )
        if policy.get("treatment_fail_closed_on_incompatibility") is not True:
            errors.append("policy:treatment_fail_closed_on_incompatibility")
        if policy.get("master_knowledge_base_local") is not False:
            errors.append("policy:master_knowledge_base_local")
        if policy.get("arbitrary_shell") is not False:
            errors.append("policy:arbitrary_shell")
        if policy.get("arbitrary_eval") is not False:
            errors.append("policy:arbitrary_eval")
        if connector.get("canonical_core") is not True:
            errors.append("connector:not_canonical")
        if skill.get("canonical_core") is not True:
            errors.append("skill:not_canonical")
        return errors

    def compatibility_errors_for(
        self, manifest: dict[str, Any]
    ) -> list[str]:
        return self._compatibility_errors(manifest)

    def treatment_gate(self) -> tuple[bool, str]:
        if self.compatible:
            return True, "suite_compatible"
        return False, "suite_incompatible"

    def status(self) -> dict[str, Any]:
        return {
            "suite_version": SUITE_VERSION,
            "app_version": APP_VERSION,
            "connector_version": CONNECTOR_VERSION,
            "connector_interface_version": CONNECTOR_INTERFACE_VERSION,
            "connector_schema_version": CONNECTOR_SCHEMA_VERSION,
            "skill_version": SKILL_VERSION,
            "skill_schema_version": SKILL_SCHEMA_VERSION,
            "protocol_card_schema_version": PROTOCOL_CARD_SCHEMA_VERSION,
            "protocol_primitive_set_version": PROTOCOL_PRIMITIVE_SET_VERSION,
            "doctor_server_api": DOCTOR_SERVER_API_VERSION,
            "bridge_version": BRIDGE_VERSION,
            "emergency_pack_version": PROTOCOL_PACK_VERSION,
            "compatible": self.compatible,
            "diagnosis_allowed": True,
            "treatment_allowed": self.compatible,
            "compatibility_errors": list(self.errors),
            "skill_sha256": self.skill.sha256,
            "surfaces": ["web", "api"],
        }
