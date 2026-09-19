from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import CONNECTOR_INTERFACE_VERSION


class SkillError(RuntimeError):
    pass


class SkillCore:
    def __init__(self, root: str | Path = "/app/suzie_doctor_skill") -> None:
        self.root = Path(root)
        self.skill_path = self.root / "SKILL.md"
        self.metadata_path = self.root / "metadata.json"
        self._metadata = self._load_metadata()
        self._text = self._load_text()
        self._sha256 = hashlib.sha256(self._text.encode("utf-8")).hexdigest()
        self._validate()

    def _load_metadata(self) -> dict[str, Any]:
        if not self.metadata_path.exists():
            raise SkillError(f"Skill metadata not found: {self.metadata_path}")
        try:
            data = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SkillError(f"Skill metadata is invalid: {exc}") from exc
        if not isinstance(data, dict):
            raise SkillError("Skill metadata must be an object")
        return data

    def _load_text(self) -> str:
        if not self.skill_path.exists():
            raise SkillError(f"Skill file not found: {self.skill_path}")
        text = self.skill_path.read_text(encoding="utf-8")
        if not text.strip():
            raise SkillError("Skill file is empty")
        return text

    def _validate(self) -> None:
        expected = str(self._metadata.get("sha256") or "")
        if not expected:
            raise SkillError("Skill metadata has no sha256")
        if expected != self._sha256:
            raise SkillError("Skill sha256 mismatch")
        if not bool(self._metadata.get("canonical")):
            raise SkillError("Skill is not marked canonical")
        surfaces = self._metadata.get("surfaces")
        if not isinstance(surfaces, list) or not {"web", "api"}.issubset(
            {str(item) for item in surfaces}
        ):
            raise SkillError("Skill must support both web and api surfaces")
        if int(self._metadata.get("connector_interface_version") or 0) != (
            CONNECTOR_INTERFACE_VERSION
        ):
            raise SkillError("Skill Connector interface version mismatch")
        if self._metadata.get("contains_master_kb") is not False:
            raise SkillError("Skill metadata must declare contains_master_kb=false")
        if self._metadata.get("contains_source_evidence") is not False:
            raise SkillError(
                "Skill metadata must declare contains_source_evidence=false"
            )
        forbidden_names = {
            "forum_knowledge_base.json",
            "compiled_knowledge.json",
            "normalized_knowledge.json",
            "generated_protocols.json",
            "curation_ledger.json",
        }
        bundled = {
            item.name
            for item in self.root.rglob("*")
            if item.is_file()
        }
        leaked = sorted(bundled & forbidden_names)
        if leaked:
            raise SkillError(
                "Skill bundle contains server-side knowledge files: "
                + ",".join(leaked)
            )

    @property
    def version(self) -> str:
        return str(self._metadata.get("version") or "")

    @property
    def schema_version(self) -> int:
        return int(self._metadata.get("schema_version") or 0)

    @property
    def sha256(self) -> str:
        return self._sha256

    @property
    def connector_interface_version(self) -> int:
        return int(self._metadata.get("connector_interface_version") or 0)

    @property
    def text(self) -> str:
        return self._text

    def descriptor(self, *, include_text: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": str(self._metadata.get("name") or "suzie-doctor-skill"),
            "version": self.version,
            "schema_version": self.schema_version,
            "sha256": self.sha256,
            "canonical": True,
            "surfaces": ["web", "api"],
            "connector_interface_version": self.connector_interface_version,
            "contains_master_kb": False,
            "contains_source_evidence": False,
        }
        if include_text:
            result["skill"] = self.text
        return result


class SkillSurfaceLoader:
    """Surface packaging around one canonical SkillCore."""

    def __init__(self, skill: SkillCore, *, surface: str) -> None:
        if surface not in {"web", "api"}:
            raise ValueError("surface must be web or api")
        self.skill = skill
        self.surface = surface

    def load(self, *, include_text: bool = True) -> dict[str, Any]:
        descriptor = self.skill.descriptor(include_text=include_text)
        return {
            "surface": self.surface,
            "canonical_skill": descriptor,
        }
