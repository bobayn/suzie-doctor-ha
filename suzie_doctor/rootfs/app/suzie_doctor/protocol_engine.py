from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any

import yaml

from .db import Database
if TYPE_CHECKING:
    from .ha_api import HomeAssistantClient
    from .supervisor import SupervisorClient


class ProtocolError(RuntimeError):
    pass


class UnsupportedPrimitive(ProtocolError):
    pass


class ProtocolEngine:
    """Deterministic disease-protocol executor for the MVP."""

    SUPPORTED_PRIMITIVES = {
        "addon_info",
        "config_entry_info",
        "config_entry_set_enabled",
        "config_entry_state",
        "confirmed_disease",
        "context_value",
        "context_list",
        "core_memory_stability",
        "entity_registry_info",
        "ensure_update_current",
        "check_config",
        "create_backup",
        "google_assistant_set_exposed",
        "install_hacs_supported",
        "install_update",
        "mqtt_probe",
        "network_primary_info",
        "network_set_primary_dns",
        "notify_user",
        "read_host_metrics",
        "reload_config_entry",
        "reload_config_entry_verified",
        "reload_subsystem",
        "restart_addon",
        "restart_core",
        "set_entity_device_class",
        "set_entity_enabled",
        "update_state",
        "verify_recorder_write",
        "wait",
    }
    SUPPORTED_TRIGGER_TYPES = {
        "config_entry_state",
        "disease_confirmed",
        "repair_issue",
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
        compatibility_guard: Any | None = None,
    ) -> None:
        self.db = db
        self.supervisor = supervisor
        self.ha = ha
        self.app_version = app_version
        self.bridge_version = bridge_version
        self.pack_version = pack_version
        self.pack_root = Path(pack_root)
        self.compatibility_guard = compatibility_guard

    def load_pack(self) -> dict[str, Any]:
        pack_file = self.pack_root / "pack.yaml"
        if not pack_file.exists():
            raise ProtocolError(f"Protocol pack metadata not found: {pack_file}")
        pack = yaml.safe_load(pack_file.read_text(encoding="utf-8")) or {}
        if not isinstance(pack, dict):
            raise ProtocolError("Protocol pack metadata must be a mapping")

        cards: list[dict[str, Any]] = []
        seen_protocols: set[str] = set()
        cards_dir = self.pack_root / "cards"
        for path in sorted(cards_dir.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            card = self._validate_card(raw, source=str(path))
            protocol_id = str(card["protocol"]["id"])
            if protocol_id in seen_protocols:
                raise ProtocolError(f"Duplicate protocol id: {protocol_id}")
            # One Disease may legitimately have multiple environment/version
            # specific protocols. protocol_id remains globally unique.
            seen_protocols.add(protocol_id)
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
                    "severity": card.get("severity"),
                    "scan": card.get("scan"),
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

    @staticmethod
    def _normalized_database_family(value: Any) -> str:
        text = str(value or "").strip().lower()
        if "maria" in text:
            return "mariadb"
        if "mysql" in text:
            return "mysql"
        if "postgres" in text:
            return "postgresql"
        if "sqlite" in text:
            return "sqlite"
        return text

    def card_scan_applicability(
        self,
        card: dict[str, Any],
        *,
        mode: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Fail-closed scan gating. Unsupported or unknown conditions never run a card."""
        context = context or {}
        scan = card.get("scan")
        if not isinstance(scan, dict):
            return False, "scan_missing"

        modes = scan.get("modes")
        if not isinstance(modes, list) or mode not in {str(item) for item in modes}:
            return False, f"mode_{mode}_not_enabled"

        status = str((card.get("protocol") or {}).get("status") or "")
        if status not in {"ACTIVE", "WATCH", "MANUAL"}:
            return False, f"protocol_status_{status.lower() or 'unknown'}"

        applicability = scan.get("applicability") or {}
        if not isinstance(applicability, dict):
            return False, "applicability_invalid"

        for key, rule in applicability.items():
            if key == "database_family":
                actual = self._normalized_database_family(context.get("database_family"))
                if isinstance(rule, dict):
                    allowed = rule.get("any_of")
                else:
                    allowed = rule
                if isinstance(allowed, (list, tuple, set)):
                    expected = {self._normalized_database_family(item) for item in allowed}
                else:
                    expected = {self._normalized_database_family(allowed)}
                if not actual or actual not in expected:
                    return False, f"database_family:{actual or 'unknown'}"
                continue

            if key == "supervisor_app":
                apps = {str(item) for item in (context.get("supervisor_apps") or [])}
                if isinstance(rule, dict):
                    expected_raw = rule.get("any_of")
                else:
                    expected_raw = rule
                if isinstance(expected_raw, (list, tuple, set)):
                    expected = {str(item) for item in expected_raw}
                else:
                    expected = {str(expected_raw)}
                if not apps.intersection(expected):
                    return False, "supervisor_app_missing"
                continue

            if key == "recorder_present":
                if bool(context.get("recorder_present")) != bool(rule):
                    return False, "recorder_not_applicable"
                continue

            if key == "target_categories":
                if mode != "targeted":
                    continue
                allowed = rule if isinstance(rule, (list, tuple, set)) else [rule]
                if str(context.get("target_category") or "") not in {str(item) for item in allowed}:
                    return False, "target_category_not_applicable"
                continue

            return False, f"unsupported_applicability:{key}"

        return True, "applicable"

    def card_trigger_match(
        self,
        card: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        context = context or {}
        triggers = card.get("triggers")
        if not isinstance(triggers, dict):
            return False, "triggers_invalid"
        rules = triggers.get("any")
        if not isinstance(rules, list) or not rules:
            return False, "triggers_missing"

        events = context.get("trigger_events")
        if not isinstance(events, list) or not events:
            return False, "trigger_context_missing"

        normalized_events = {
            (str(event.get("type") or ""), str(event.get("match") or ""))
            for event in events
            if isinstance(event, dict)
            and str(event.get("type") or "") in self.SUPPORTED_TRIGGER_TYPES
            and str(event.get("match") or "")
        }
        if not normalized_events:
            return False, "trigger_context_invalid"

        for rule in rules:
            if not isinstance(rule, dict):
                continue
            trigger_type = str(rule.get("type") or "")
            trigger_match = str(rule.get("match") or "")
            if trigger_type not in self.SUPPORTED_TRIGGER_TYPES or not trigger_match:
                continue
            if (trigger_type, trigger_match) in normalized_events:
                return True, f"trigger_match:{trigger_type}:{trigger_match}"

        return False, "trigger_no_match"

    async def scan_cards(
        self,
        *,
        mode: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        loaded = self.load_pack()
        context = dict(context or {})
        items: list[dict[str, Any]] = []

        for card in loaded["cards"]:
            applies, applicability_reason = self.card_scan_applicability(
                card, mode=mode, context=context
            )
            scan_modes = [
                str(item)
                for item in ((card.get("scan") or {}).get("modes") or [])
            ]
            trigger_matched: bool | None = None
            trigger_reason: str | None = None
            should_run = applies
            if applies and mode == "triggered":
                trigger_matched, trigger_reason = self.card_trigger_match(
                    card, context=context
                )
                should_run = trigger_matched

            item: dict[str, Any] = {
                "disease_id": card["disease_id"],
                "title": card["title"],
                "component": card["component"],
                "severity": str(card.get("severity") or "PROBLEM"),
                "protocol_id": card["protocol"]["id"],
                "protocol_version": card["protocol"]["version"],
                "protocol_status": card["protocol"]["status"],
                "source_file": card.get("_source_file"),
                "scan_modes": scan_modes,
                "applicable": applies,
                "applicability_reason": applicability_reason,
                "trigger_matched": trigger_matched,
                "trigger_reason": trigger_reason,
            }
            if not should_run:
                item["result"] = "SKIPPED"
                item["diagnosis_confirmed"] = False
                items.append(item)
                continue

            diagnosis = await self.diagnose_card(card, context=context)
            item.update(
                {
                    "result": diagnosis.get("result"),
                    "diagnosis_confirmed": bool(diagnosis.get("diagnosis_confirmed")),
                    "diagnostics": diagnosis.get("diagnostics", []),
                    "error": diagnosis.get("error"),
                }
            )
            items.append(item)

        error_results = {
            "PRECONDITION_FAILED",
            "FAILED",
            "PROTOCOL_ERROR",
            "UNSUPPORTED_PRIMITIVE",
        }
        return {
            "mode": mode,
            "pack": loaded["pack"],
            "evaluated": sum(1 for item in items if item.get("result") != "SKIPPED"),
            "confirmed": sum(1 for item in items if item.get("diagnosis_confirmed")),
            "errors": [item for item in items if item.get("result") in error_results],
            "items": items,
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
            "EXPERIMENTAL",
            "FIELD_ONE_SHOT",
            "WATCH",
            "MANUAL",
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
        severity = raw.get("severity")
        if severity is not None and severity not in {"PROBLEM", "DEGRADED", "CRITICAL"}:
            raise ProtocolError(f"{source}: unsupported severity {severity!r}")
        scan = raw.get("scan")
        if scan is not None:
            if not isinstance(scan, dict):
                raise ProtocolError(f"{source}: scan must be a mapping")
            modes = scan.get("modes")
            if not isinstance(modes, list) or not modes:
                raise ProtocolError(f"{source}: scan.modes must be a non-empty list")
            unsupported_modes = {str(item) for item in modes} - {"daily", "targeted", "triggered"}
            if unsupported_modes:
                raise ProtocolError(
                    f"{source}: unsupported scan modes {sorted(unsupported_modes)!r}"
                )
            applicability = scan.get("applicability", {})
            if not isinstance(applicability, dict):
                raise ProtocolError(f"{source}: scan.applicability must be a mapping")
            if "triggered" in {str(item) for item in modes}:
                triggers = raw.get("triggers")
                if not isinstance(triggers, dict):
                    raise ProtocolError(f"{source}: triggered card requires triggers mapping")
                rules = triggers.get("any")
                if not isinstance(rules, list) or not rules:
                    raise ProtocolError(f"{source}: triggered card requires non-empty triggers.any")
                for index, rule in enumerate(rules):
                    if not isinstance(rule, dict):
                        raise ProtocolError(f"{source}: triggers.any[{index}] must be a mapping")
                    trigger_type = str(rule.get("type") or "")
                    trigger_match = str(rule.get("match") or "")
                    if trigger_type not in self.SUPPORTED_TRIGGER_TYPES:
                        raise ProtocolError(f"{source}: unsupported trigger type {trigger_type!r}")
                    if not trigger_match:
                        raise ProtocolError(f"{source}: triggers.any[{index}].match must be non-empty")
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

    @staticmethod
    def classify_filesystem_readonly_logs(logs: str) -> bool:
        """Classify host logs without treating normal HAOS immutable EROFS as failure."""
        benign_patterns = (
            r"etc-hosts\.mount:.*?/etc/hosts.*?read-only file system",
            r"etc-hostname\.mount:.*?/etc/hostname.*?read-only file system",
            r"vfs:\s+mounted root \(erofs filesystem\) readonly",
            r"mkfs\.erofs availability",
            r"loading plugin.*?\berofs\b",
        )
        strong_patterns = (
            r"\bremount(?:ing|ed)?\b.{0,120}\bfilesystem\b.{0,120}\bread[- ]only\b",
            r"\b(?:ext[234]-fs|xfs|btrfs|f2fs)\b.{0,200}\b(?:forced|forcing|remount(?:ing|ed)?)\b.{0,120}\bread[- ]only\b",
            r"\bfilesystem\b.{0,120}\b(?:forced|forcing)\b.{0,80}\bread[- ]only\b",
        )
        mutable_paths = (
            "/data",
            "/mnt/data",
            "/homeassistant",
            "/config",
        )

        for line in logs.splitlines()[-5000:]:
            lowered = line.lower()
            if any(
                re.search(pattern, lowered, re.IGNORECASE) is not None
                for pattern in benign_patterns
            ):
                continue
            if any(
                re.search(pattern, lowered, re.IGNORECASE) is not None
                for pattern in strong_patterns
            ):
                return True
            if (
                "read-only file system" in lowered
                and any(path in lowered for path in mutable_paths)
            ):
                return True
        return False

    async def _run_primitive(
        self, name: str, args: dict[str, Any], env: dict[str, Any]
    ) -> Any:
        if name not in self.SUPPORTED_PRIMITIVES:
            raise UnsupportedPrimitive(name)
        resolved = self._resolve_value(args or {}, env)

        if name == "confirmed_disease":
            expected = str(resolved.get("disease_id") or "").strip()
            actual = str(env.get("disease_id") or "").strip()
            confirmed = bool(env.get("disease_confirmed"))
            if expected and actual and expected != actual:
                return False
            return confirmed

        if name == "context_value":
            key = str(resolved.get("key") or "").strip()
            allowed = {
                "entity_id",
                "config_entry_id",
                "issue_domain",
                "issue_id",
                "addon_slug",
                "update_entity_id",
                "device_class",
            }
            if key not in allowed:
                raise ProtocolError(f"Unsupported context key: {key!r}")
            value = env.get(key)
            if value is None and isinstance(env.get("context"), dict):
                value = env["context"].get(key)
            text = str(value or "").strip()
            return {"found": bool(text), "value": text}

        if name == "context_list":
            key = str(resolved.get("key") or "").strip()
            allowed = {"dns_servers", "entity_ids"}
            if key not in allowed:
                raise ProtocolError(f"Unsupported context list key: {key!r}")
            value = env.get(key)
            if value is None and isinstance(env.get("context"), dict):
                value = env["context"].get(key)
            if isinstance(value, str):
                values = [part.strip() for part in value.split(",") if part.strip()]
            elif isinstance(value, list):
                values = [str(part).strip() for part in value if str(part).strip()]
            else:
                values = []
            return {"found": bool(values), "value": values}

        if name == "network_primary_info":
            info = await self.supervisor.network_info()
            interfaces = info.get("interfaces", []) if isinstance(info, dict) else []
            primary = next(
                (
                    item for item in interfaces
                    if isinstance(item, dict) and bool(item.get("primary"))
                ),
                None,
            )
            if primary is None:
                return {
                    "found": False,
                    "interface": "",
                    "method": "",
                    "nameservers": [],
                    "host_internet": bool(info.get("host_internet")) if isinstance(info, dict) else False,
                }
            ipv4 = primary.get("ipv4") if isinstance(primary.get("ipv4"), dict) else {}
            return {
                "found": True,
                "interface": str(primary.get("interface") or ""),
                "method": str(ipv4.get("method") or ""),
                "nameservers": [str(x) for x in (ipv4.get("nameservers") or [])],
                "host_internet": bool(info.get("host_internet")) if isinstance(info, dict) else False,
                "supervisor_internet": bool(info.get("supervisor_internet")) if isinstance(info, dict) else False,
            }

        if name == "network_set_primary_dns":
            nameservers = resolved.get("nameservers")
            if not isinstance(nameservers, list):
                raise ProtocolError("network_set_primary_dns requires nameservers list")
            result = await self.supervisor.set_primary_auto_dns(
                [str(x) for x in nameservers]
            )
            deadline = monotonic() + max(
                15,
                min(120, int(resolved.get("timeout_seconds", 60))),
            )
            desired = [str(x) for x in nameservers]
            while monotonic() < deadline:
                info = await self.supervisor.network_info()
                interfaces = info.get("interfaces", []) if isinstance(info, dict) else []
                primary = next(
                    (
                        item for item in interfaces
                        if isinstance(item, dict) and bool(item.get("primary"))
                    ),
                    None,
                )
                ipv4 = primary.get("ipv4") if isinstance(primary, dict) and isinstance(primary.get("ipv4"), dict) else {}
                current = [str(x) for x in (ipv4.get("nameservers") or [])]
                if current == desired and bool(info.get("host_internet")):
                    return {"ok": True, **result}
                await asyncio.sleep(3)
            return False

        if name == "entity_registry_info":
            entity_id = str(resolved.get("entity_id") or "").strip()
            if "." not in entity_id:
                return {
                    "found": False,
                    "entity_id": entity_id,
                    "disabled": False,
                    "disabled_by": None,
                    "config_entry_id": None,
                }
            data = await self.ha.ws_command("config/entity_registry/list")
            entries = data if isinstance(data, list) else []
            matches = [
                item for item in entries
                if isinstance(item, dict)
                and str(item.get("entity_id") or "") == entity_id
            ]
            if len(matches) != 1:
                return {
                    "found": False,
                    "entity_id": entity_id,
                    "disabled": False,
                    "disabled_by": None,
                    "config_entry_id": None,
                }
            item = matches[0]
            disabled_by = item.get("disabled_by")
            return {
                "found": True,
                "entity_id": entity_id,
                "disabled": disabled_by is not None,
                "disabled_by": disabled_by,
                "config_entry_id": item.get("config_entry_id"),
                "platform": item.get("platform"),
                "device_class": item.get("device_class"),
            }

        if name == "set_entity_device_class":
            entity_id = str(resolved.get("entity_id") or "").strip()
            if "." not in entity_id:
                raise ProtocolError(
                    "set_entity_device_class requires exact entity_id"
                )
            if "device_class" not in resolved:
                raise ProtocolError(
                    "set_entity_device_class requires device_class"
                )
            raw_device_class = resolved.get("device_class")
            device_class = (
                None
                if raw_device_class is None
                else str(raw_device_class).strip() or None
            )
            await self.ha.ws_command(
                "config/entity_registry/update",
                entity_id=entity_id,
                device_class=device_class,
            )
            registry = await self.ha.ws_command(
                "config/entity_registry/list"
            )
            rows = registry if isinstance(registry, list) else []
            current = next(
                (
                    item for item in rows
                    if isinstance(item, dict)
                    and str(item.get("entity_id") or "") == entity_id
                ),
                None,
            )
            return bool(
                current is not None
                and current.get("device_class") == device_class
            )

        if name == "set_entity_enabled":
            entity_id = str(resolved.get("entity_id") or "").strip()
            enabled = bool(resolved.get("enabled"))
            if "." not in entity_id:
                raise ProtocolError("set_entity_enabled requires exact entity_id")
            result = await self.ha.ws_command(
                "config/entity_registry/update",
                entity_id=entity_id,
                disabled_by=None if enabled else "user",
            )
            entity_entry = (
                result.get("entity_entry")
                if isinstance(result, dict)
                and isinstance(result.get("entity_entry"), dict)
                else {}
            )
            config_entry_id = str(
                entity_entry.get("config_entry_id") or ""
            ).strip()
            if enabled and isinstance(result, dict) and result.get("require_restart"):
                # Entity registry says a full HA restart is required. Do not hide
                # that inside an entity-enable primitive; a separate confirmed
                # Core restart Protocol step is required.
                return False
            if config_entry_id:
                await asyncio.sleep(1)
                if not await self.ha.bridge_reload_entry(config_entry_id):
                    return False

            deadline = monotonic() + max(
                10,
                min(120, int(resolved.get("timeout_seconds", 60))),
            )
            while monotonic() < deadline:
                registry = await self.ha.ws_command(
                    "config/entity_registry/list"
                )
                rows = registry if isinstance(registry, list) else []
                current = next(
                    (
                        item for item in rows
                        if isinstance(item, dict)
                        and str(item.get("entity_id") or "") == entity_id
                    ),
                    None,
                )
                if current is None:
                    await asyncio.sleep(2)
                    continue
                disabled_by = current.get("disabled_by")
                if enabled and disabled_by is not None:
                    await asyncio.sleep(2)
                    continue
                if not enabled and disabled_by is None:
                    await asyncio.sleep(2)
                    continue
                if not enabled:
                    return True
                try:
                    states = await self.ha.get_states()
                except Exception:
                    await asyncio.sleep(2)
                    continue
                state = next(
                    (
                        item for item in states
                        if isinstance(item, dict)
                        and str(item.get("entity_id") or "") == entity_id
                    ),
                    None,
                )
                if state is not None and str(
                    state.get("state") or ""
                ).lower() not in {"unavailable", "unknown", ""}:
                    return True
                await asyncio.sleep(2)
            return False

        if name == "google_assistant_set_exposed":
            entity_id = str(resolved.get("entity_id") or "").strip()
            exposed = bool(resolved.get("exposed"))
            if "." not in entity_id:
                raise ProtocolError(
                    "google_assistant_set_exposed requires exact entity_id"
                )
            await self.ha.ws_command(
                "homeassistant/expose_entity",
                assistants=["cloud.google_assistant"],
                entity_ids=[entity_id],
                should_expose=exposed,
            )
            await self.ha.call_service(
                "google_assistant",
                "request_sync",
                {},
            )
            listing = await self.ha.ws_command(
                "homeassistant/expose_entity/list"
            )
            exposed_entities = (
                listing.get("exposed_entities", {})
                if isinstance(listing, dict)
                else {}
            )
            current = exposed_entities.get(entity_id, {})
            is_exposed = bool(
                isinstance(current, dict)
                and current.get("cloud.google_assistant")
            )
            return is_exposed is exposed

        if name == "ensure_update_current":
            target = str(resolved.get("target") or "").strip().lower()
            aliases = {
                "core": "update.home_assistant_core_update",
                "supervisor": "update.home_assistant_supervisor_update",
                "os": "update.home_assistant_operating_system_update",
                "haos": "update.home_assistant_operating_system_update",
            }
            entity_id = aliases.get(target)
            if not entity_id:
                raise ProtocolError(
                    "ensure_update_current supports only core/supervisor/os"
                )
            states = await self.ha.get_states()
            current = next(
                (
                    item for item in states
                    if isinstance(item, dict)
                    and str(item.get("entity_id") or "") == entity_id
                ),
                None,
            )
            if current is None:
                return False
            attrs = (
                current.get("attributes")
                if isinstance(current.get("attributes"), dict)
                else {}
            )
            installed = attrs.get("installed_version")
            latest = attrs.get("latest_version")
            available = (
                str(current.get("state") or "").lower() == "on"
                and not bool(attrs.get("in_progress"))
                and not (
                    installed is not None
                    and latest is not None
                    and str(installed) == str(latest)
                )
            )
            if not available:
                return True
            await self.ha.install_update(entity_id, backup=True)
            deadline = monotonic() + max(
                60,
                min(900, int(resolved.get("timeout_seconds", 600))),
            )
            while monotonic() < deadline:
                await asyncio.sleep(5)
                try:
                    states = await self.ha.get_states()
                except Exception:
                    continue
                current = next(
                    (
                        item for item in states
                        if isinstance(item, dict)
                        and str(item.get("entity_id") or "") == entity_id
                    ),
                    None,
                )
                if current is None:
                    continue
                attrs = (
                    current.get("attributes")
                    if isinstance(current.get("attributes"), dict)
                    else {}
                )
                if attrs.get("in_progress"):
                    continue
                installed = attrs.get("installed_version")
                latest = attrs.get("latest_version")
                if (
                    str(current.get("state") or "").lower() != "on"
                    or (
                        installed is not None
                        and latest is not None
                        and str(installed) == str(latest)
                    )
                ):
                    return True
            return False

        if name == "install_hacs_supported":
            repo_url = "https://github.com/hacs/addons"
            manifest = Path(
                "/homeassistant/custom_components/hacs/manifest.json"
            )
            if manifest.exists():
                return True

            store = await self.supervisor.store_info()
            repos = (
                store.get("repositories", [])
                if isinstance(store, dict)
                else []
            )
            present = any(
                isinstance(item, dict)
                and str(item.get("source") or item.get("url") or "")
                == repo_url
                for item in repos
            )
            if not present:
                if not await self.supervisor.add_store_repository(repo_url):
                    return False
                if not await self.supervisor.reload_store():
                    return False
                await asyncio.sleep(3)
                store = await self.supervisor.store_info()

            addons = (
                store.get("addons", [])
                if isinstance(store, dict)
                else []
            )
            matches = [
                item
                for item in addons
                if isinstance(item, dict)
                and str(item.get("name") or "") == "Get HACS"
                and "hacs/addons" in str(item.get("url") or "")
            ]
            if len(matches) != 1:
                return False
            addon = matches[0]
            slug = str(addon.get("slug") or "").strip()
            if not slug:
                return False
            installed = addon.get("installed")
            if installed in (None, False, "", "none", "None"):
                if not await self.supervisor.install_store_addon(slug):
                    return False
            if not await self.supervisor.start_addon(slug):
                return False

            deadline = monotonic() + max(
                30,
                min(240, int(resolved.get("timeout_seconds", 180))),
            )
            while monotonic() < deadline:
                if manifest.exists():
                    return True
                await asyncio.sleep(3)
            return False

        if name == "update_state":
            target = str(resolved.get("target") or "").strip().lower()
            exact_entity = str(resolved.get("entity_id") or "").strip()
            states = await self.ha.get_states()
            candidates: list[dict[str, Any]] = []
            aliases = {
                "core": (
                    "update.home_assistant_core_update",
                    "home assistant core",
                ),
                "supervisor": (
                    "update.home_assistant_supervisor_update",
                    "home assistant supervisor",
                ),
                "os": (
                    "update.home_assistant_operating_system_update",
                    "home assistant operating system",
                ),
                "haos": (
                    "update.home_assistant_operating_system_update",
                    "home assistant operating system",
                ),
            }
            for state in states:
                if not isinstance(state, dict):
                    continue
                entity_id = str(state.get("entity_id") or "")
                if not entity_id.startswith("update."):
                    continue
                attrs = (
                    state.get("attributes")
                    if isinstance(state.get("attributes"), dict)
                    else {}
                )
                title = str(
                    attrs.get("title")
                    or attrs.get("friendly_name")
                    or ""
                )
                haystack = f"{entity_id} {title}".lower()
                matched = False
                if exact_entity:
                    matched = entity_id == exact_entity
                elif target in aliases:
                    entity_hint, title_hint = aliases[target]
                    matched = (
                        entity_id == entity_hint
                        or title_hint in haystack
                    )
                elif target:
                    token = re.sub(r"[^a-z0-9]+", "", target)
                    normalized = re.sub(
                        r"[^a-z0-9]+", "", haystack
                    )
                    matched = bool(token and token in normalized)
                if matched:
                    candidates.append(state)
            if len(candidates) != 1:
                return {
                    "found": False,
                    "ambiguous": len(candidates) > 1,
                    "match_count": len(candidates),
                    "entity_id": "",
                    "available": False,
                    "in_progress": False,
                }
            state = candidates[0]
            attrs = (
                state.get("attributes")
                if isinstance(state.get("attributes"), dict)
                else {}
            )
            installed = attrs.get("installed_version")
            latest = attrs.get("latest_version")
            available = (
                str(state.get("state") or "").lower() == "on"
                and not bool(attrs.get("in_progress"))
                and not (
                    installed is not None
                    and latest is not None
                    and str(installed) == str(latest)
                )
            )
            return {
                "found": True,
                "ambiguous": False,
                "match_count": 1,
                "entity_id": str(state.get("entity_id") or ""),
                "available": available,
                "in_progress": bool(attrs.get("in_progress")),
                "installed_version": installed,
                "latest_version": latest,
                "title": attrs.get("title") or attrs.get("friendly_name"),
            }

        if name == "install_update":
            entity_id = str(resolved.get("entity_id") or "").strip()
            if not entity_id.startswith("update."):
                raise ProtocolError("install_update requires update entity_id")
            await self.ha.install_update(entity_id, backup=True)
            timeout_seconds = max(
                30,
                min(900, int(resolved.get("timeout_seconds", 300))),
            )
            deadline = monotonic() + timeout_seconds
            while monotonic() < deadline:
                await asyncio.sleep(5)
                try:
                    states = await self.ha.get_states()
                except Exception:
                    continue
                current = next(
                    (
                        item for item in states
                        if isinstance(item, dict)
                        and str(item.get("entity_id") or "") == entity_id
                    ),
                    None,
                )
                if current is None:
                    continue
                attrs = (
                    current.get("attributes")
                    if isinstance(current.get("attributes"), dict)
                    else {}
                )
                if attrs.get("in_progress"):
                    continue
                installed = attrs.get("installed_version")
                latest = attrs.get("latest_version")
                if (
                    str(current.get("state") or "").lower() != "on"
                    or (
                        installed is not None
                        and latest is not None
                        and str(installed) == str(latest)
                    )
                ):
                    return True
            return False

        if name == "core_memory_stability":
            duration_seconds = max(
                30,
                min(180, int(resolved.get("duration_seconds", 60))),
            )
            interval_seconds = max(
                5,
                min(30, int(resolved.get("interval_seconds", 10))),
            )
            max_increase = max(
                1.0,
                min(
                    25.0,
                    float(resolved.get("max_increase_percent", 8.0)),
                ),
            )
            max_percent = max(
                50.0,
                min(98.0, float(resolved.get("max_percent", 90.0))),
            )
            samples: list[float] = []
            deadline = monotonic() + duration_seconds
            while True:
                stats = await self.supervisor.core_stats()
                value = stats.get("memory_percent")
                if not isinstance(value, (int, float)):
                    return False
                current = float(value)
                samples.append(current)
                if current >= max_percent:
                    return False
                if current - samples[0] >= max_increase:
                    return False
                if monotonic() >= deadline:
                    break
                await asyncio.sleep(interval_seconds)
            return True

        if name == "config_entry_set_enabled":
            entry_id = str(resolved.get("entry_id") or "").strip()
            enabled = bool(resolved.get("enabled"))
            if not entry_id:
                raise ProtocolError(
                    "config_entry_set_enabled requires exact entry_id"
                )
            result = await self.ha.ws_command(
                "config_entries/disable",
                entry_id=entry_id,
                disabled_by=None if enabled else "user",
            )
            if (
                isinstance(result, dict)
                and bool(result.get("require_restart"))
            ):
                return False
            deadline = monotonic() + max(
                10,
                min(120, int(resolved.get("timeout_seconds", 60))),
            )
            while monotonic() < deadline:
                snapshot = await self.ha.bridge_snapshot()
                entries = (
                    snapshot.get("config_entries", [])
                    if isinstance(snapshot, dict)
                    else []
                )
                current = next(
                    (
                        item for item in entries
                        if isinstance(item, dict)
                        and str(item.get("entry_id") or "") == entry_id
                    ),
                    None,
                )
                if current is not None:
                    disabled_by = current.get("disabled_by")
                    if enabled and disabled_by is None:
                        return {"ok": True, "require_restart": False}
                    if not enabled and disabled_by is not None:
                        return {"ok": True, "require_restart": False}
                await asyncio.sleep(2)
            return False

        if name == "config_entry_info":
            entry_id = str(resolved.get("entry_id") or "").strip()
            domain = str(resolved.get("domain") or "").strip().lower()
            snapshot = await self.ha.bridge_snapshot()
            entries = (
                snapshot.get("config_entries", [])
                if isinstance(snapshot, dict)
                else []
            )
            matches = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if entry_id and str(entry.get("entry_id") or "") == entry_id:
                    matches.append(entry)
                elif (
                    not entry_id
                    and domain
                    and str(entry.get("domain") or "").lower() == domain
                ):
                    matches.append(entry)
            if len(matches) != 1:
                return {
                    "found": False,
                    "ambiguous": len(matches) > 1,
                    "match_count": len(matches),
                    "entry_id": "",
                    "state": None,
                }
            entry = matches[0]
            return {
                "found": True,
                "ambiguous": False,
                "match_count": 1,
                "entry_id": str(entry.get("entry_id") or ""),
                "domain": str(entry.get("domain") or ""),
                "state": str(entry.get("state") or ""),
                "disabled_by": entry.get("disabled_by"),
            }

        if name == "addon_info":
            selector = str(resolved.get("selector") or "").strip().lower()
            exact_slug = str(resolved.get("slug") or "").strip()
            payload = await self.supervisor.addons()
            addons = (
                payload.get("addons", [])
                if isinstance(payload, dict)
                else []
            )
            matches = []
            token = re.sub(r"[^a-z0-9]+", "", selector)
            for addon in addons:
                if not isinstance(addon, dict):
                    continue
                slug = str(addon.get("slug") or "")
                name_text = str(addon.get("name") or "")
                if exact_slug:
                    matched = slug == exact_slug
                else:
                    haystack = re.sub(
                        r"[^a-z0-9]+",
                        "",
                        f"{slug} {name_text}".lower(),
                    )
                    matched = bool(token and token in haystack)
                if matched:
                    matches.append(addon)
            if len(matches) != 1:
                return {
                    "found": False,
                    "ambiguous": len(matches) > 1,
                    "match_count": len(matches),
                    "slug": "",
                    "state": None,
                }
            addon = matches[0]
            return {
                "found": True,
                "ambiguous": False,
                "match_count": 1,
                "slug": str(addon.get("slug") or ""),
                "name": str(addon.get("name") or ""),
                "state": str(addon.get("state") or ""),
                "version": addon.get("version"),
                "version_latest": addon.get("version_latest"),
            }

        if name == "restart_addon":
            slug = str(resolved.get("slug") or "").strip()
            if not slug:
                raise ProtocolError("restart_addon requires slug")
            if not await self.supervisor.restart_addon(slug):
                return False
            deadline = monotonic() + max(
                20,
                min(180, int(resolved.get("timeout_seconds", 90))),
            )
            while monotonic() < deadline:
                await asyncio.sleep(2)
                payload = await self.supervisor.addons()
                addons = (
                    payload.get("addons", [])
                    if isinstance(payload, dict)
                    else []
                )
                current = next(
                    (
                        item for item in addons
                        if isinstance(item, dict)
                        and str(item.get("slug") or "") == slug
                    ),
                    None,
                )
                if current is not None and str(
                    current.get("state") or ""
                ).lower() == "started":
                    return True
            return False

        if name == "reload_subsystem":
            subsystem = str(resolved.get("subsystem") or "").strip().lower()
            allowed = {
                "automation": ("automation", "reload"),
                "script": ("script", "reload"),
                "scene": ("scene", "reload"),
                "group": ("group", "reload"),
                "template": ("template", "reload"),
            }
            call = allowed.get(subsystem)
            if call is None:
                raise ProtocolError(
                    f"Unsupported reload subsystem: {subsystem!r}"
                )
            await self.ha.call_service(call[0], call[1], {})
            return True

        if name == "check_config":
            await self.ha.call_service("homeassistant", "check_config", {})
            return True

        if name == "restart_core":
            await self.supervisor.restart_core()
            timeout_seconds = max(
                30,
                min(300, int(resolved.get("timeout_seconds", 180))),
            )
            await asyncio.sleep(8)
            deadline = monotonic() + timeout_seconds
            while monotonic() < deadline:
                try:
                    config = await self.ha.get_config()
                    if isinstance(config, dict) and config:
                        return True
                except Exception:
                    pass
                await asyncio.sleep(5)
            return False

        if name == "create_backup":
            before = await self.supervisor.backups_info()
            before_items = (
                before.get("backups", [])
                if isinstance(before, dict)
                else []
            )
            before_slugs = {
                str(item.get("slug") or "")
                for item in before_items
                if isinstance(item, dict)
            }
            name_text = str(
                resolved.get("name")
                or "Suzie Doctor protocol checkpoint"
            )[:120]
            await self.ha.call_service(
                "hassio",
                "backup_full",
                {"name": name_text, "compressed": True},
            )
            deadline = monotonic() + max(
                30,
                min(600, int(resolved.get("timeout_seconds", 300))),
            )
            while monotonic() < deadline:
                await asyncio.sleep(5)
                after = await self.supervisor.backups_info()
                after_items = (
                    after.get("backups", [])
                    if isinstance(after, dict)
                    else []
                )
                after_slugs = {
                    str(item.get("slug") or "")
                    for item in after_items
                    if isinstance(item, dict)
                }
                if after_slugs - before_slugs:
                    return True
            return False

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

            return self.classify_filesystem_readonly_logs(logs)

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

        if name == "reload_config_entry_verified":
            entry_id = str(resolved.get("entry_id") or "")
            if not entry_id:
                raise ProtocolError(
                    "reload_config_entry_verified requires entry_id"
                )
            if not await self.ha.bridge_reload_entry(entry_id):
                return False
            deadline = monotonic() + max(
                15,
                min(180, int(resolved.get("timeout_seconds", 90))),
            )
            while monotonic() < deadline:
                await asyncio.sleep(2)
                snapshot = await self.ha.bridge_snapshot()
                entries = (
                    snapshot.get("config_entries", [])
                    if isinstance(snapshot, dict)
                    else []
                )
                current = next(
                    (
                        entry
                        for entry in entries
                        if isinstance(entry, dict)
                        and str(entry.get("entry_id") or "") == entry_id
                    ),
                    None,
                )
                if current is not None and str(
                    current.get("state") or ""
                ) == "loaded":
                    return True
            return False

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
        explicit_confirmation: bool = False,
        risk_assessment: dict[str, Any] | None = None,
        execution_actor: str = "field_suzie",
        developer_override: bool,
    ) -> tuple[bool, str]:
        status = str(card["protocol"]["status"])
        automation_class = str(card["automation_class"])

        if self.compatibility_guard is not None:
            try:
                compatible, reason = self.compatibility_guard()
            except Exception:
                return False, "suite_compatibility_error"
            if not compatible:
                return False, str(reason or "suite_incompatible")

        if developer_override:
            return True, "developer_override"
        actor = str(execution_actor or "field_suzie").strip().lower()
        if actor not in {"field_suzie", "family_doctor"}:
            return False, "unknown_execution_actor"
        if status == "EXPERIMENTAL":
            if actor != "field_suzie":
                return False, "experimental_field_only"
        elif status == "FIELD_ONE_SHOT":
            if actor != "field_suzie":
                return False, "field_one_shot_field_only"
        elif status != "ACTIVE":
            return False, f"protocol_status_{status.lower()}"
        if automation_class == "DIAGNOSTIC_ONLY":
            return False, "diagnostic_only"
        if trust_mode == "manual":
            return False, "manual_trust_mode"

        # Family Doctor may execute only already-published ACTIVE deterministic
        # treatment.  This avoids waking strong AI for routine medicine while
        # preserving the user's trust-mode boundary.  AUTO_SAFE is allowed in
        # safe_auto/full_trust; legacy CONFIRM_REQUIRED is allowed without AI
        # only in full_trust.  All preconditions/checkpoint/verify/rollback
        # gates below still apply.
        if actor == "family_doctor":
            if automation_class == "AUTO_SAFE":
                return True, "family_doctor_approved_active_auto_safe"
            if automation_class == "CONFIRM_REQUIRED":
                if trust_mode == "full_trust":
                    return True, "family_doctor_approved_active_full_trust"
                return False, "family_doctor_field_review_required"
            return False, "family_doctor_automation_class_blocked"

        risk = dict(risk_assessment or {})
        if not risk:
            return False, "doctor_risk_assessment_required"
        probability = str(risk.get("harm_probability") or "").upper()
        irreversibility = str(risk.get("irreversibility") or "").upper()
        magnitude = str(risk.get("harm_magnitude") or "").upper()
        decision = str(risk.get("decision") or "").upper()
        rationale = str(risk.get("rationale") or "").strip()

        valid = (
            probability in {"LOW", "MEDIUM", "HIGH"}
            and irreversibility in {
                "REVERSIBLE",
                "PARTIALLY_REVERSIBLE",
                "IRREVERSIBLE",
            }
            and magnitude in {"LOW", "MODERATE", "SUBSTANTIAL", "CATASTROPHIC"}
            and decision in {"PROCEED", "AVOID"}
            and bool(rationale)
        )
        if not valid:
            return False, "doctor_risk_assessment_invalid"
        if decision != "PROCEED":
            return False, "doctor_decision_avoid"
        if (
            irreversibility == "IRREVERSIBLE"
            and probability == "HIGH"
            and magnitude in {"SUBSTANTIAL", "CATASTROPHIC"}
        ):
            return False, "doctor_assessed_unacceptable_irreversible_risk"

        # explicit_confirmation is intentionally ignored. Human confirmation is not
        # a substitute for Suzie Doctor's autonomous risk judgment.
        return True, "doctor_autonomous_risk_accepted"

    async def diagnose_card(
        self,
        card: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        card = self._validate_card(dict(card))
        env: dict[str, Any] = dict(context or {})
        response: dict[str, Any] = {
            "disease_id": card["disease_id"],
            "protocol_id": card["protocol"]["id"],
            "protocol_version": card["protocol"]["version"],
            "protocol_status": card["protocol"]["status"],
            "automation_class": card["automation_class"],
        }
        if str(card["protocol"]["status"]) == "MANUAL":
            response["manual_guidance"] = card.get("manual") or {}
        try:
            await self._prepare_named_preconditions(card, env)
            if not self._eval_conditions(
                card.get("preconditions"), env, default=True
            ):
                response["result"] = "PRECONDITION_FAILED"
                response["diagnosis_confirmed"] = False
                return response

            response["diagnostics"] = await self._run_diagnostics(
                card, env
            )
            confirmed = self._eval_conditions(
                card.get("confirm"), env, default=False
            )
            if confirmed and self._eval_conditions(
                card.get("exclude"), env, default=False
            ):
                response["result"] = "EXCLUDED"
                response["diagnosis_confirmed"] = False
                return response

            response["diagnosis_confirmed"] = confirmed
            response["result"] = (
                "CONFIRMED" if confirmed else "NOT_CONFIRMED"
            )
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

    async def execute_card(
        self,
        card: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        incident_id: str | None = None,
        trust_mode: str = "safe_auto",
        explicit_confirmation: bool = False,
        risk_assessment: dict[str, Any] | None = None,
        execution_actor: str = "field_suzie",
        simulated: bool = False,
        developer_override: bool = False,
    ) -> dict[str, Any]:
        card = self._validate_card(dict(card))
        field_one_shot = str(card["protocol"]["status"]) == "FIELD_ONE_SHOT"
        env: dict[str, Any] = dict(context or {})
        started = monotonic()
        response: dict[str, Any] = {
            "disease_id": card["disease_id"],
            "protocol_id": card["protocol"]["id"],
            "protocol_version": card["protocol"]["version"],
            "protocol_status": card["protocol"]["status"],
            "automation_class": card["automation_class"],
            "execution_actor": str(execution_actor or "field_suzie"),
            "simulated": simulated,
            "execution_kind": "FIELD_ONE_SHOT" if field_one_shot else "PROTOCOL",
        }
        if isinstance(risk_assessment, dict):
            response["doctor_risk_assessment"] = dict(risk_assessment)
        if str(card["protocol"]["status"]) == "MANUAL":
            response["manual_guidance"] = card.get("manual") or {}

        try:
            await self._prepare_named_preconditions(card, env)
            if not self._eval_conditions(
                card.get("preconditions"), env, default=True
            ):
                response["result"] = "PRECONDITION_FAILED"
                return response

            if field_one_shot:
                response["diagnostics"] = []
                confirmed = bool(env.get("field_action_authorized") is True)
            else:
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
                risk_assessment=risk_assessment,
                execution_actor=execution_actor,
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
                verify_performed = False
                if not treatment_failed:
                    verify = card.get("verify") or {}
                    if (
                        isinstance(verify, dict)
                        and verify.get("rerun_diagnostics")
                    ):
                        verify_performed = True
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
                        elif success_when == "conditions":
                            success = self._eval_conditions(
                                verify.get("conditions"),
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
                response["verify_performed"] = verify_performed
                response["verify_passed"] = bool(success) if verify_performed else None

                rollback_results: list[dict[str, Any]] = []
                if not success and (card.get("rollback") or []):
                    for rollback_step in card.get("rollback") or []:
                        if not isinstance(rollback_step, dict):
                            continue
                        rollback_primitive = str(
                            rollback_step.get("primitive") or ""
                        )
                        if not rollback_primitive:
                            continue
                        max_attempts = max(
                            1,
                            min(
                                3,
                                int(
                                    rollback_step.get(
                                        "max_attempts", 1
                                    )
                                ),
                            ),
                        )
                        rollback_ok = False
                        rollback_value: Any = None
                        rollback_error: str | None = None
                        rollback_attempts = 0
                        for rollback_attempt in range(
                            1, max_attempts + 1
                        ):
                            rollback_attempts = rollback_attempt
                            try:
                                rollback_value = await self._run_primitive(
                                    rollback_primitive,
                                    rollback_step.get("args") or {},
                                    env,
                                )
                                rollback_ok = bool(
                                    rollback_value is not False
                                )
                                rollback_error = None
                            except Exception as exc:
                                rollback_error = (
                                    f"{type(exc).__name__}: {exc}"
                                )
                                rollback_ok = False
                            if rollback_ok:
                                break
                        rollback_results.append(
                            {
                                "step": rollback_step.get("step"),
                                "primitive": rollback_primitive,
                                "attempts": rollback_attempts,
                                "ok": rollback_ok,
                                "value": rollback_value,
                                "error": rollback_error,
                            }
                        )
                if rollback_results:
                    response["rollback"] = rollback_results
                    response["rollback_succeeded"] = all(
                        bool(item.get("ok"))
                        for item in rollback_results
                    )

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
            if field_one_shot:
                # One-shot Field work is Case/Wilson evidence, not published
                # Protocol fleet-effectiveness telemetry.
                response["duration_ms"] = duration_ms
                response["new_protocol_evidence"] = bool(
                    response.get("result") == "SUCCESS"
                    and response.get("verify_performed") is True
                    and response.get("verify_passed") is True
                )
                return response

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
