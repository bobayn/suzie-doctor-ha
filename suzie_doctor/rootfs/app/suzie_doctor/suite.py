"""Release-unit compatibility; diagnostics survive a failed treatment gate."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any
from .connector import CONNECTOR_VERSION, CAPABILITY_SCHEMA_VERSION

SUITE_VERSION = "0.2.36-dev"
SKILL_VERSION = "0.2.0"
SKILL_SCHEMA_VERSION = 1
CONNECTOR_INTERFACE_VERSION = 1
PROTOCOL_SCHEMA_VERSION = 1
SERVER_API_VERSION = 1
ROOT = Path(__file__).resolve().parent.parent


class SuiteCompatibilityGate:
    def __init__(self, app_version: str, bridge_version: str, pack_version: str, root: Path = ROOT):
        self.root = root
        self.actual = {"suite_version": SUITE_VERSION, "app_version": app_version,
            "connector_version": CONNECTOR_VERSION, "skill_version": SKILL_VERSION,
            "connector_core_version": CONNECTOR_VERSION,
            "connector_interface_version": CONNECTOR_INTERFACE_VERSION,
            "skill_core_version": SKILL_VERSION, "skill_schema_version": SKILL_SCHEMA_VERSION,
            "protocol_schema_version": PROTOCOL_SCHEMA_VERSION, "server_api_version": SERVER_API_VERSION,
            "bridge_version": bridge_version, "emergency_pack_version": pack_version,
            "capability_schema_version": CAPABILITY_SCHEMA_VERSION}
        self.manifest: dict[str, Any] = {}
        self.skill: dict[str, Any] = {}
        self.errors: list[str] = []
        try:
            self.manifest = json.loads((root / "suite_manifest.json").read_text())
            self.skill = json.loads((root / "skills/suzie-doctor/metadata.json").read_text())
            for key, expected in self.actual.items():
                if self.manifest.get(key) != expected:
                    self.errors.append(f"component_mismatch:{key}")
            if self.skill.get("skill_schema_version") != SKILL_SCHEMA_VERSION:
                self.errors.append("skill_schema_incompatible")
            if self.skill.get("skill_version") != SKILL_VERSION:
                self.errors.append("skill_version_mismatch")
            if self.skill.get("capability_schema_version") != CAPABILITY_SCHEMA_VERSION:
                self.errors.append("skill_capability_schema_incompatible")
            skill_bytes = (root / "skills/suzie-doctor/SKILL.md").read_bytes()
            if hashlib.sha256(skill_bytes).hexdigest() != self.manifest.get("skill_sha256"):
                self.errors.append("skill_integrity_mismatch")
        except (OSError, ValueError, TypeError):
            self.errors.append("suite_artifact_invalid_or_missing")

    def status(self, *, protocol_schema: Any = 1, server_api: Any = 1,
               required: list[str] | None = None, known: set[str] | None = None) -> dict[str, Any]:
        errors = list(self.errors)
        if type(protocol_schema) is not int or protocol_schema != PROTOCOL_SCHEMA_VERSION:
            errors.append("protocol_schema_incompatible")
        if type(server_api) is not int or server_api != SERVER_API_VERSION:
            errors.append("server_api_incompatible")
        if known is not None:
            for capability in self.skill.get("capabilities", []):
                if capability not in known:
                    errors.append(f"skill_capability_unknown:{capability}")
            for capability in required or []:
                if capability not in known:
                    errors.append(f"protocol_capability_unknown:{capability}")
        return {**self.actual, "compatible": not errors, "treatment_allowed": not errors,
            "diagnosis_allowed": True, "errors": sorted(set(errors))}

    def skill_text(self) -> str:
        return (self.root / "skills/suzie-doctor/SKILL.md").read_text(encoding="utf-8")

    def skill_bundle(self) -> dict[str, Any]:
        base = self.root / "skills/suzie-doctor"
        return {"metadata": self.skill, "text": self.skill_text(),
                "references": {str(p.relative_to(base)): p.read_text(encoding="utf-8")
                    for p in sorted((base / "references").rglob("*.md"))}}
