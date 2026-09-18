from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

import yaml

from .db import Database
from .ha_api import HomeAssistantClient
from .supervisor import SupervisorClient


class ProtocolError(RuntimeError):
    pass


class UnsupportedPrimitive(ProtocolError):
    pass


class ProtocolEngine:
    """Deterministic disease-protocol executor for the MVP."""

    SUPPORTED_PRIMITIVES = {
        "config_entry_state",
        "mqtt_probe",
        "notify_user",
        "read_host_metrics",
        "reload_config_entry",
        "verify_recorder_write",
        "wait",
    }

    def __init__(
        self,
        db: Database,
        supervisor: SupervisorClient,
        ha: HomeAssistantClient,
        *,
        app_version: str,
        bridge_version: str,
        pack_version: str,
        pack_root: str | Path = "/app/protocol_pack",
    ) -> None:
        self.db = db
        self.supervisor = supervisor
        self.ha = ha
        self.app_version = app_version
        self.bridge_version = bridge_version
        self.pack_version = pack_version
        self.pack_root = Path(pack_root)

    def load_pack(self) -> dict[str, Any]:
        pack_file = self.pack_root / "pack.yaml"
        if not pack_file.exists():
            raise ProtocolError(f"Protocol pack metadata not found: {pack_file}")
        pack = yaml.safe_load(pack_file.read_text(encoding="utf-8")) or {}
        if not isinstance(pack, dict):
            raise ProtocolError("Protocol pack metadata must be a mapping")

        cards: list[dict[str, Any]] = []
        seen_protocols: set[str] = set()
        seen_diseases: set[str] = set()
        cards_dir = self.pack_root / "cards"
        for path in sorted(cards_dir.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            card = self._validate_card(raw, source=str(path))
            protocol_id = str(card["protocol"]["id"])
            disease_id = str(card["disease_id"])
            if protocol_id in seen_protocols:
                raise ProtocolError(f"Duplicate protocol id: {protocol_id}")
            if disease_id in seen_diseases:
                raise ProtocolError(f"Duplicate disease id: {disease_id}")
            seen_protocols.add(protocol_id)
            seen_diseases.add(disease_id)
            card["_source_file"] = path.name
            cards.append(card)

        return {"pack": pack, "cards": cards}

    def inventory(self) -> dict[str, Any]:
        loaded = self.load_pack()
        cards = []
        for card in loaded["cards"]:
            primitives = self._card_primitives(card)
            cards.append(
                {
                    "disease_id": card["disease_id"],
                    "title": card["title"],
                    "component": card["component"],
                    "protocol_id": card["protocol"]["id"],
                    "protocol_version": card["protocol"]["version"],
                    "status": card["protocol"]["status"],
                    "automation_class": card["automation_class"],
                    "source_file": card.get("_source_file"),
                    "primitives": sorted(primitives),
                    "unsupported_primitives": sorted(
                        primitives - self.SUPPORTED_PRIMITIVES
                    ),
                }
            )
        return {
            "pack": loaded["pack"],
            "supported_primitives": sorted(self.SUPPORTED_PRIMITIVES),
            "cards": cards,
        }

    def _validate_card(
        self, raw: Any, *, source: str = "<memory>"
    ) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ProtocolError(f"{source}: card must be a mapping")
        required = [
            "schema_version",
            "disease_id",
            "title",
            "component",
            "protocol",
            "diagnostics",
            "confirm",
            "treatment",
            "verify",
            "automation_class",
        ]
        missing = [key for key in required if key not in raw]
        if missing:
            raise ProtocolError(
                f"{source}: missing required keys: {', '.join(missing)}"
            )
        protocol = raw.get("protocol")
        if not isinstance(protocol, dict):
            raise ProtocolError(f"{source}: protocol must be a mapping")
        for key in ("id", "version", "status"):
            if not protocol.get(key):
                raise ProtocolError(f"{source}: protocol.{key} is required")
        if protocol["status"] not in {
            "ACTIVE",
            "WATCH",
            "SUSPENDED",
            "RETIRED",
        }:
            raise ProtocolError(
                f"{source}: unsupported protocol status {protocol['status']!r}"
            )
        if raw["automation_class"] not in {
            "AUTO_SAFE",
            "CONFIRM_REQUIRED",
            "DIAGNOSTIC_ONLY",
        }:
            raise ProtocolError(
                f"{source}: unsupported automation_class "
                f"{raw['automation_class']!r}"
            )
        if not isinstance(raw.get("diagnostics"), list):
            raise ProtocolError(f"{source}: diagnostics must be a list")
        if not isinstance(raw.get("treatment"), list):
            raise ProtocolError(f"{source}: treatment must be a list")
        return raw

    def _card_primitives(self, card: dict[str, Any]) -> set[str]:
        names: set[str] = set()
        for section in ("diagnostics", "treatment", "fallback", "rollback"):
            for item in card.get(section, []) or []:
                if isinstance(item, dict) and item.get("primitive"):
                    names.add(str(item["primitive"]))
        checkpoint = card.get("checkpoint")
        if isinstance(checkpoint, dict) and checkpoint.get("primitive"):
            names.add(str(checkpoint["primitive"]))
        return names

    def _lookup(self, env: dict[str, Any], path: str) -> Any:
        value: Any = env
        for part in path.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                raise ProtocolError(f"Unknown protocol variable: {path}")
        return value

    def _resolve_value(self, value: Any, env: dict[str, Any]) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            return self._lookup(env, value[1:])
        if isinstance(value, dict):
            return {
                key: self._resolve_value(item, env)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._resolve_value(item, env) for item in value]
        return value

    def _parse_literal(self, text: str) -> Any:
        text = text.strip()
        lowered = text.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if lowered in {"null", "none"}:
            return None
        if (
            len(text) >= 2
            and text[0] == text[-1]
            and text[0] in {"'", '"'}
        ):
            return text[1:-1]
        try:
            if "." in text:
                return float(text)
            return int(text)
        except ValueError:
            return text

    def _eval_expr(self, expr: str, env: dict[str, Any]) -> bool:
        match = re.fullmatch(
            r"\s*([A-Za-z_][A-Za-z0-9_.]*)\s*"
            r"(==|!=|>=|<=|>|<)\s*(.+?)\s*",
            expr,
        )
        if not match:
            raise ProtocolError(
                f"Unsupported condition expression: {expr!r}"
            )
        lhs_path, op, rhs_text = match.groups()
        lhs = self._lookup(env, lhs_path)
        rhs = self._parse_literal(rhs_text)
        try:
            if op == "==":
                return lhs == rhs
            if op == "!=":
                return lhs != rhs
            if op == ">=":
                return lhs >= rhs
            if op == "<=":
                return lhs <= rhs
            if op == ">":
                return lhs > rhs
            if op == "<":
                return lhs < rhs
        except TypeError as exc:
            raise ProtocolError(
                f"Incompatible values in condition {expr!r}: {exc}"
            ) from exc
        raise ProtocolError(f"Unsupported operator: {op}")

    def _eval_condition_item(
        self, item: Any, env: dict[str, Any]
    ) -> bool:
        if isinstance(item, dict) and "expr" in item:
            return self._eval_expr(str(item["expr"]), env)
        if isinstance(item, str):
            if item in env:
                return bool(env[item])
            raise ProtocolError(
                f"Unsupported named precondition/exclusion: {item}"
            )
        raise ProtocolError(f"Unsupported condition item: {item!r}")

    def _eval_conditions(
        self, block: Any, env: dict[str, Any], *, default: bool
    ) -> bool:
        if block in (None, {}, []):
            return default
        if isinstance(block, dict):
            if "all" in block:
                return all(
                    self._eval_condition_item(item, env)
                    for item in (block.get("all") or [])
                )
            if "any" in block:
                return any(
                    self._eval_condition_item(item, env)
                    for item in (block.get("any") or [])
                )
        if isinstance(block, list):
            return all(
                self._eval_condition_item(item, env) for item in block
            )
        return self._eval_condition_item(block, env)

    async def _prepare_named_preconditions(
        self, card: dict[str, Any], env: dict[str, Any]
    ) -> None:
        for item in card.get("preconditions", []) or []:
            if item == "broker_logs_readable":
                try:
                    logs = await self.supervisor.addon_logs("core_mosquitto")
                    env["broker_logs_readable"] = bool(logs)
                    env["_mosquitto_logs"] = logs
                except Exception:
                    env["broker_logs_readable"] = False

    async def _run_primitive(
        self, name: str, args: dict[str, Any], env: dict[str, Any]
    ) -> Any:
        if name not in self.SUPPORTED_PRIMITIVES:
            raise UnsupportedPrimitive(name)
        resolved = self._resolve_value(args or {}, env)

        if name == "read_host_metrics":
            metric = str(resolved.get("metric") or "")
            if metric != "filesystem_readonly":
                raise ProtocolError(
                    f"Unsupported read_host_metrics metric: {metric!r}"
                )
            logs = await self.supervisor.host_logs_current(
                int(resolved.get("lines", 5000))
            )
            if not logs:
                return None
            readonly_patterns = (
                r"\bread-only file system\b",
                r"\bremount(?:ing|ed)?\b.{0,120}\bread-only\b",
                r"\bfilesystem\b.{0,120}\bread-only\b",
                r"\bforced?\b.{0,80}\bread-only\b",
            )
            lowered = logs.lower()
            return any(
                re.search(pattern, lowered, re.IGNORECASE | re.DOTALL)
                is not None
                for pattern in readonly_patterns
            )

        if name == "mqtt_probe":
            mode = str(resolved.get("mode") or "")
            if mode != "duplicate_client_id":
                raise ProtocolError(f"Unsupported mqtt_probe mode: {mode!r}")
            logs = env.get("_mosquitto_logs")
            if not isinstance(logs, str):
                logs = await self.supervisor.addon_logs("core_mosquitto")
            max_lines = max(100, min(5000, int(resolved.get("max_lines", 2000))))
            lines = logs.splitlines()[-max_lines:]
            pattern = re.compile(
                r"Client\s+(.+?)\s+already connected, closing old connection\.",
                re.IGNORECASE,
            )
            matches = []
            for line in lines:
                match = pattern.search(line)
                if match:
                    matches.append(match.group(1).strip())
            return {
                "count": len(matches),
                "unique_count": len(set(matches)),
                "client_ids": sorted(set(matches)),
                "lines_scanned": len(lines),
            }

        if name == "verify_recorder_write":
            return await self.ha.recorder_write_probe()

        if name == "config_entry_state":
            entry_id = str(resolved.get("entry_id") or "")
            domain = str(resolved.get("domain") or "")
            snapshot = await self.ha.bridge_snapshot()
            entries = (
                snapshot.get("config_entries", [])
                if isinstance(snapshot, dict)
                else []
            )
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if (
                    entry_id
                    and str(entry.get("entry_id") or "") != entry_id
                ):
                    continue
                if (
                    domain
                    and str(entry.get("domain") or "") != domain
                ):
                    continue
                return str(entry.get("state") or "")
            return None

        if name == "reload_config_entry":
            entry_id = str(resolved.get("entry_id") or "")
            if not entry_id:
                raise ProtocolError(
                    "reload_config_entry requires entry_id"
                )
            return await self.ha.bridge_reload_entry(entry_id)

        if name == "wait":
            seconds = float(resolved.get("seconds", 0))
            if seconds < 0 or seconds > 60:
                raise ProtocolError(
                    "wait seconds must be between 0 and 60"
                )
            await asyncio.sleep(seconds)
            return True

        if name == "notify_user":
            message = str(resolved.get("message") or "").strip()
            if not message:
                raise ProtocolError("notify_user requires message")
            notification_id = str(
                resolved.get("notification_id")
                or "suzie_doctor_protocol"
            )
            await self.ha.persistent_notification(
                "Suzie Doctor", message, notification_id
            )
            return True

        raise UnsupportedPrimitive(name)

    async def _run_diagnostics(
        self, card: dict[str, Any], env: dict[str, Any]
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for diag in card.get("diagnostics", []) or []:
            if not isinstance(diag, dict):
                raise ProtocolError(
                    "Diagnostic step must be a mapping"
                )
            diag_id = str(diag.get("id") or "")
            primitive = str(diag.get("primitive") or "")
            if not diag_id or not primitive:
                raise ProtocolError(
                    "Diagnostic step requires id and primitive"
                )
            value = await self._run_primitive(
                primitive, diag.get("args") or {}, env
            )
            save_as = str(diag.get("save_as") or diag_id)
            env[save_as] = value
            results.append(
                {
                    "id": diag_id,
                    "primitive": primitive,
                    "save_as": save_as,
                    "value": value,
                }
            )
        return results

    def _treatment_allowed(
        self,
        card: dict[str, Any],
        *,
        trust_mode: str,
        explicit_confirmation: bool,
        developer_override: bool,
    ) -> tuple[bool, str]:
        status = str(card["protocol"]["status"])
        automation_class = str(card["automation_class"])

        if developer_override:
            return True, "developer_override"
        if status != "ACTIVE":
            return False, f"protocol_status_{status.lower()}"
        if automation_class == "DIAGNOSTIC_ONLY":
            return False, "diagnostic_only"
        if trust_mode == "manual":
            return False, "manual_trust_mode"
        if automation_class == "CONFIRM_REQUIRED" and not (
            trust_mode == "full_trust" or explicit_confirmation
        ):
            return False, "confirmation_required"
        return True, "allowed"

    async def execute_card(
        self,
        card: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        incident_id: str | None = None,
        trust_mode: str = "safe_auto",
        explicit_confirmation: bool = False,
        simulated: bool = False,
        developer_override: bool = False,
    ) -> dict[str, Any]:
        card = self._validate_card(dict(card))
        env: dict[str, Any] = dict(context or {})
        started = monotonic()
        response: dict[str, Any] = {
            "disease_id": card["disease_id"],
            "protocol_id": card["protocol"]["id"],
            "protocol_version": card["protocol"]["version"],
            "protocol_status": card["protocol"]["status"],
            "automation_class": card["automation_class"],
            "simulated": simulated,
        }

        try:
            await self._prepare_named_preconditions(card, env)
            if not self._eval_conditions(
                card.get("preconditions"), env, default=True
            ):
                response["result"] = "PRECONDITION_FAILED"
                return response

            response["diagnostics"] = await self._run_diagnostics(
                card, env
            )
            confirmed = self._eval_conditions(
                card.get("confirm"), env, default=False
            )
            response["diagnosis_confirmed"] = confirmed
            if not confirmed:
                response["result"] = "NOT_CONFIRMED"
                return response

            if self._eval_conditions(
                card.get("exclude"), env, default=False
            ):
                response["result"] = "EXCLUDED"
                return response

            allowed, allow_reason = self._treatment_allowed(
                card,
                trust_mode=trust_mode,
                explicit_confirmation=explicit_confirmation,
                developer_override=developer_override,
            )
            response["treatment_allowed"] = allowed
            response["treatment_gate"] = allow_reason
            if not allowed:
                response["result"] = "DIAGNOSIS_ONLY"
                return response

            versions = await self._versions()
            run_id = self.db.begin_protocol_run(
                incident_id=incident_id,
                disease_id=str(card["disease_id"]),
                protocol_id=str(card["protocol"]["id"]),
                protocol_version=str(card["protocol"]["version"]),
                protocol_pack_version=self.pack_version,
                simulated=simulated,
                versions=versions,
            )
            response["protocol_run_id"] = run_id

            attempts_total = 0
            treatment_results: list[dict[str, Any]] = []
            treatment_failed = False
            try:
                checkpoint = card.get("checkpoint") or {}
                if (
                    isinstance(checkpoint, dict)
                    and checkpoint.get("required")
                ):
                    primitive = checkpoint.get("primitive")
                    if not primitive:
                        raise ProtocolError(
                            "Required checkpoint has no primitive"
                        )
                    await self._run_primitive(
                        str(primitive),
                        checkpoint.get("args") or {},
                        env,
                    )

                for step in card.get("treatment", []) or []:
                    if not isinstance(step, dict):
                        raise ProtocolError(
                            "Treatment step must be a mapping"
                        )
                    primitive = str(step.get("primitive") or "")
                    if not primitive:
                        raise ProtocolError(
                            "Treatment step requires primitive"
                        )
                    max_attempts = max(
                        1, min(3, int(step.get("max_attempts", 1)))
                    )
                    step_ok = False
                    last_value: Any = None
                    last_error: str | None = None
                    used_attempts = 0
                    for attempt in range(1, max_attempts + 1):
                        used_attempts = attempt
                        attempts_total += 1
                        try:
                            last_value = await self._run_primitive(
                                primitive,
                                step.get("args") or {},
                                env,
                            )
                            step_ok = bool(last_value is not False)
                            last_error = None
                        except Exception as exc:
                            last_error = (
                                f"{type(exc).__name__}: {exc}"
                            )
                            step_ok = False
                        if step_ok:
                            break
                    treatment_results.append(
                        {
                            "step": step.get("step"),
                            "primitive": primitive,
                            "attempts": used_attempts,
                            "ok": step_ok,
                            "value": last_value,
                            "error": last_error,
                        }
                    )
                    if not step_ok:
                        treatment_failed = True
                        break

                response["treatment"] = treatment_results
                verify_results: list[dict[str, Any]] = []
                success = False
                if not treatment_failed:
                    verify = card.get("verify") or {}
                    if (
                        isinstance(verify, dict)
                        and verify.get("rerun_diagnostics")
                    ):
                        verify_env = dict(context or {})
                        verify_results = await self._run_diagnostics(
                            card, verify_env
                        )
                        success_when = str(
                            verify.get("success_when") or ""
                        )
                        if success_when == "no_original_symptoms":
                            success = not self._eval_conditions(
                                card.get("confirm"),
                                verify_env,
                                default=False,
                            )
                        else:
                            raise ProtocolError(
                                "Unsupported verify.success_when: "
                                f"{success_when!r}"
                            )
                    else:
                        success = not treatment_failed
                response["verify"] = verify_results
                response["result"] = (
                    "SUCCESS" if success else "FAILED"
                )
            except Exception as exc:
                response["result"] = "FAILED"
                response["error"] = (
                    f"{type(exc).__name__}: {exc}"
                )

            versions = await self._versions()
            duration_ms = int((monotonic() - started) * 1000)
            self.db.finish_protocol_run(
                run_id,
                result=str(response["result"]),
                attempt_count=max(1, attempts_total),
                restart_level_used="none",
                versions=versions,
            )
            telemetry = {
                "telemetry_schema_version": 1,
                "anonymous_installation_id":
                    self.db.get_or_create_meta_uuid(
                        "anonymous_telemetry_id"
                    ),
                "occurred_at_utc": datetime.now(UTC).isoformat(),
                "protocol_id": str(card["protocol"]["id"]),
                "protocol_version":
                    str(card["protocol"]["version"]),
                "disease_id": str(card["disease_id"]),
                "result": str(response["result"]),
                "duration_ms": duration_ms,
                "attempt_count": max(1, attempts_total),
                "restart_level_used": "none",
                "doctor_app_version": self.app_version,
                "doctor_integration_version": self.bridge_version,
                "protocol_pack_version": self.pack_version,
                "ha_core_version":
                    versions.get("ha_core_version", "unknown"),
                "ha_install_type":
                    versions.get("ha_install_type", "unknown"),
                "architecture":
                    versions.get("architecture", "unknown"),
                "database_family":
                    versions.get("database_family", "unknown"),
                "affected_component_version":
                    versions.get(
                        "affected_component_version", "unknown"
                    ),
                "simulated": bool(simulated),
            }
            response["telemetry_seq"] = self.db.enqueue_telemetry(
                telemetry
            )
            response["duration_ms"] = duration_ms
            return response

        except UnsupportedPrimitive as exc:
            response["result"] = "UNSUPPORTED_PRIMITIVE"
            response["error"] = str(exc)
            return response
        except ProtocolError as exc:
            response["result"] = "PROTOCOL_ERROR"
            response["error"] = str(exc)
            return response
        except Exception as exc:
            response["result"] = "FAILED"
            response["error"] = f"{type(exc).__name__}: {exc}"
            return response

    async def _versions(self) -> dict[str, Any]:
        try:
            system = await self.supervisor.info()
        except Exception:
            system = {}
        return {
            "ha_core_version":
                str(system.get("homeassistant") or "unknown"),
            "ha_install_type":
                str(system.get("operating_system") or "unknown"),
            "architecture": str(system.get("arch") or "unknown"),
            "database_family": "unknown",
            "affected_component_version": "unknown",
        }
