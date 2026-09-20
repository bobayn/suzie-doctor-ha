from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from .db import Database
from .ha_api import HomeAssistantClient
from .protocol_engine import ProtocolEngine
from .supervisor import SupervisorClient

PROBLEM_ENTRY_STATES = {"setup_error", "setup_retry", "migration_error", "failed_unload"}
MOUNT_FAILED_TRANSLATION_KEYS = {"issue_mount_mount_failed", "mount_mount_failed"}


def _inactive_mounts(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("mounts")
    if not isinstance(rows, list):
        return []
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("name") or "").strip()
        and str(row.get("state") or "").lower() == "inactive"
    ]


def _mount_state(payload: Any, name: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    rows = payload.get("mounts")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and str(row.get("name") or "") == name:
            state = str(row.get("state") or "").lower()
            return state or None
    return None


def _bridge_trigger_events(bridge: Any) -> list[dict[str, str]]:
    if not isinstance(bridge, dict):
        return []

    events: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    issues = bridge.get("issues")
    if isinstance(issues, list):
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            if not bool(issue.get("active", False)):
                continue
            if issue.get("dismissed_version"):
                continue
            match = str(
                issue.get("translation_key")
                or issue.get("issue_id")
                or ""
            ).strip()
            if not match:
                continue
            key = ("repair_issue", match)
            if key not in seen:
                seen.add(key)
                events.append({"type": key[0], "match": key[1]})

    entries = bridge.get("config_entries")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            state = str(entry.get("state") or "").strip()
            if state not in PROBLEM_ENTRY_STATES:
                continue
            domain = str(entry.get("domain") or "unknown").strip() or "unknown"
            match = f"{domain}:{state}"
            key = ("config_entry_state", match)
            if key not in seen:
                seen.add(key)
                events.append({"type": key[0], "match": key[1]})

    return events


def _iso_age_hours(value: str | None) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (datetime.now(UTC) - dt.astimezone(UTC)).total_seconds() / 3600
    except Exception:
        return None


async def _safe(call: Callable[[], Awaitable[Any]]) -> tuple[Any | None, str | None]:
    try:
        return await call(), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


class Auditor:
    def __init__(
        self,
        db: Database,
        supervisor: SupervisorClient,
        ha: HomeAssistantClient,
        protocol_engine: ProtocolEngine,
    ) -> None:
        self.db = db
        self.supervisor = supervisor
        self.ha = ha
        self.protocol_engine = protocol_engine

    async def _protocol_scan_context(
        self, mode: str, target_category: str | None
    ) -> tuple[dict[str, Any], dict[str, str]]:
        context: dict[str, Any] = {"target_category": target_category}
        errors: dict[str, str] = {}

        if mode != "daily":
            return context, errors

        addons_result, health_result = await asyncio.gather(
            _safe(self.supervisor.addons),
            _safe(self.ha.system_health_info),
        )
        addons, addons_error = addons_result
        health, health_error = health_result

        if addons_error:
            errors["protocol_supervisor_apps"] = addons_error
        addon_rows = addons.get("addons", []) if isinstance(addons, dict) else []
        context["supervisor_apps"] = [
            str(item.get("slug"))
            for item in addon_rows
            if isinstance(item, dict) and item.get("slug")
        ]

        if health_error:
            errors["protocol_system_health"] = health_error
        recorder = health.get("recorder") if isinstance(health, dict) else None
        recorder_info = recorder.get("info") if isinstance(recorder, dict) else None
        context["recorder_present"] = isinstance(recorder_info, dict)
        if isinstance(recorder_info, dict):
            context["database_family"] = str(
                recorder_info.get("database_engine") or ""
            ).lower()

        return context, errors

    async def _run_disease_scan(
        self,
        *,
        mode: str,
        target_category: str | None,
        simulated: bool,
        trigger_events: list[dict[str, str]] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]:
        if simulated:
            return (
                {"mode": mode, "skipped": "simulated_audit", "items": []},
                [],
                {},
            )

        context, errors = await self._protocol_scan_context(mode, target_category)
        trigger_events = [
            {"type": str(item.get("type") or ""), "match": str(item.get("match") or "")}
            for item in (trigger_events or [])
            if isinstance(item, dict)
            and str(item.get("type") or "")
            and str(item.get("match") or "")
        ]
        context["trigger_events"] = list(trigger_events)
        try:
            scan = await self.protocol_engine.scan_cards(mode=mode, context=context)
        except Exception as exc:
            errors["protocol_pack"] = f"{type(exc).__name__}: {exc}"
            return ({"mode": mode, "failed": True, "items": []}, [], errors)

        if mode == "daily":
            seen_triggers = {
                (item["type"], item["match"])
                for item in trigger_events
            }
            for item in scan.get("items", []):
                if (
                    isinstance(item, dict)
                    and item.get("result") == "CONFIRMED"
                    and item.get("diagnosis_confirmed")
                    and item.get("disease_id")
                ):
                    event = {
                        "type": "disease_confirmed",
                        "match": str(item.get("disease_id") or ""),
                    }
                    key = (event["type"], event["match"])
                    if key not in seen_triggers:
                        seen_triggers.add(key)
                        trigger_events.append(event)
            triggered_context = dict(context)
            triggered_context["trigger_events"] = trigger_events
            try:
                triggered_scan = await self.protocol_engine.scan_cards(
                    mode="triggered",
                    context=triggered_context,
                )
            except Exception as exc:
                errors["protocol_pack_triggered"] = f"{type(exc).__name__}: {exc}"
            else:
                triggered_items = [
                    item
                    for item in triggered_scan.get("items", [])
                    if isinstance(item, dict)
                    and "triggered" in (item.get("scan_modes") or [])
                ]
                if triggered_items:
                    replacement_by_disease = {
                        str(item.get("disease_id") or ""): item
                        for item in triggered_items
                        if item.get("disease_id")
                    }
                    merged_items = []
                    seen: set[str] = set()
                    for item in scan.get("items", []):
                        if not isinstance(item, dict):
                            continue
                        disease_id = str(item.get("disease_id") or "")
                        merged_items.append(replacement_by_disease.get(disease_id, item))
                        seen.add(disease_id)
                    for disease_id, item in replacement_by_disease.items():
                        if disease_id not in seen:
                            merged_items.append(item)

                    scan["items"] = merged_items
                    scan["evaluated"] = sum(
                        1 for item in merged_items if item.get("result") != "SKIPPED"
                    )
                    scan["confirmed"] = sum(
                        1 for item in merged_items if item.get("diagnosis_confirmed")
                    )
                    error_results = {
                        "PRECONDITION_FAILED",
                        "FAILED",
                        "PROTOCOL_ERROR",
                        "UNSUPPORTED_PRIMITIVE",
                    }
                    scan["errors"] = [
                        item for item in merged_items if item.get("result") in error_results
                    ]
                    scan["triggered_evaluated"] = triggered_scan.get("evaluated", 0)
                    scan["triggered_confirmed"] = triggered_scan.get("confirmed", 0)
        scan["trigger_events"] = trigger_events

        findings: list[dict[str, Any]] = []
        summary_items: list[dict[str, Any]] = []
        uncertain_results = {
            "PRECONDITION_FAILED",
            "FAILED",
            "PROTOCOL_ERROR",
            "UNSUPPORTED_PRIMITIVE",
        }

        for item in scan.get("items", []):
            if not isinstance(item, dict):
                continue
            disease_id = str(item.get("disease_id") or "")
            if not disease_id:
                continue
            result = str(item.get("result") or "")
            problem_key = f"disease:{disease_id}"
            lifecycle: str | None = None
            incident_id: str | None = None

            if result == "CONFIRMED" and item.get("diagnosis_confirmed"):
                incident_id = self.db.upsert_incident(
                    problem_key=problem_key,
                    incident_type="disease",
                    severity=str(item.get("severity") or "PROBLEM"),
                    title=str(item.get("title") or disease_id),
                    detail=f"Protocol {item.get('protocol_id')} confirmed this disease.",
                    disease_id=disease_id,
                )
                lifecycle = "OPEN_OR_UPDATE"
                findings.append(
                    {
                        "problem_key": problem_key,
                        "incident_id": incident_id,
                        "kind": "disease",
                        "disease_id": disease_id,
                        "protocol_id": item.get("protocol_id"),
                        "severity": item.get("severity"),
                    }
                )
            elif result in {"NOT_CONFIRMED", "EXCLUDED"}:
                if self.db.resolve_problem(
                    problem_key,
                    f"Protocol {item.get('protocol_id')} no longer confirms the disease.",
                ):
                    lifecycle = "RESOLVED"
            elif result in uncertain_results:
                lifecycle = "UNCHANGED_UNCERTAIN"

            summary_items.append(
                {
                    "disease_id": disease_id,
                    "protocol_id": item.get("protocol_id"),
                    "severity": item.get("severity"),
                    "result": result,
                    "applicable": item.get("applicable"),
                    "applicability_reason": item.get("applicability_reason"),
                    "trigger_matched": item.get("trigger_matched"),
                    "trigger_reason": item.get("trigger_reason"),
                    "lifecycle": lifecycle,
                    "incident_id": incident_id,
                }
            )

        scan_errors = scan.get("errors", [])
        if isinstance(scan_errors, list) and scan_errors:
            compact = [
                f"{item.get('disease_id')}:{item.get('result')}"
                for item in scan_errors
                if isinstance(item, dict)
            ]
            errors["protocol_pack"] = "; ".join(compact) or "diagnostic uncertainty"

        safe_context = {
            "database_family": context.get("database_family"),
            "recorder_present": context.get("recorder_present"),
            "local_mosquitto": "core_mosquitto" in set(context.get("supervisor_apps") or []),
            "target_category": context.get("target_category"),
            "trigger_event_count": len(scan.get("trigger_events") or []),
        }
        summary = {
            "mode": mode,
            "evaluated": scan.get("evaluated", 0),
            "confirmed": scan.get("confirmed", 0),
            "triggered_evaluated": scan.get("triggered_evaluated", 0),
            "triggered_confirmed": scan.get("triggered_confirmed", 0),
            "context": safe_context,
            "items": summary_items,
        }
        return summary, findings, errors

    async def run(
        self,
        audit_type: str,
        reason: str | None = None,
        allow_generic_recovery: bool = True,
        target_context: dict[str, Any] | None = None,
        simulated: bool = False,
    ) -> dict[str, Any]:
        audit_id = self.db.begin_audit(audit_type, reason)
        targeted_category = None
        if audit_type == "targeted" and reason and reason.startswith("health_guard:"):
            targeted_category = reason.split(":", 1)[1]
        elif audit_type == "targeted" and reason == "ha_error_event":
            targeted_category = "ha_runtime_error"

        if targeted_category is not None:
            if targeted_category == "ha_runtime_error":
                calls = [
                    ("ha_config", self.ha.get_config),
                    ("bridge", self.ha.bridge_snapshot),
                ]
            else:
                calls = [
                    ("host", self.supervisor.host_info),
                    ("core_stats", self.supervisor.core_stats),
                ]
                if targeted_category == "storage":
                    calls.append(("backups", self.supervisor.backups_info))
            results = await asyncio.gather(*(_safe(call) for _, call in calls))
            names = [name for name, _ in calls]
        else:
            results = await asyncio.gather(
                _safe(self.supervisor.info),
                _safe(self.supervisor.supervisor_info),
                _safe(self.supervisor.host_info),
                _safe(self.supervisor.core_info),
                _safe(self.supervisor.core_stats),
                _safe(self.supervisor.backups_info),
                _safe(self.supervisor.mounts_info),
                _safe(self.supervisor.network_info),
                _safe(self.ha.get_config),
                _safe(self.ha.bridge_snapshot),
            )
            names = ["system", "supervisor", "host", "core", "core_stats", "backups", "mounts", "network", "ha_config", "bridge"]

        data = {name: result[0] for name, result in zip(names, results)}
        errors = {name: result[1] for name, result in zip(names, results) if result[1]}

        current_problem_keys: set[str] = set()
        findings: list[dict[str, Any]] = []

        if targeted_category == "ha_runtime_error":
            event = dict(target_context or {})
            level = str(event.get("level") or "ERROR").upper()
            fingerprint = str(event.get("fingerprint") or "").strip()[:64]
            logger_name = str(event.get("logger") or "homeassistant").strip()[:500]
            message = str(event.get("message") or "Home Assistant runtime error").strip()[:4000]
            source = str(event.get("source") or "").strip()[:500]
            exception = str(event.get("exception") or "").strip()[:6000]
            if not fingerprint:
                raw_fingerprint = f"{logger_name}|{source}|{message}"
                fingerprint = hashlib.sha256(
                    raw_fingerprint.encode("utf-8", "replace")
                ).hexdigest()[:24]
            problem_key = f"ha_error:{fingerprint}"
            current_problem_keys.add(problem_key)
            incident_id = self.db.upsert_incident(
                problem_key=problem_key,
                incident_type="ha_runtime_error",
                severity="CRITICAL" if level == "CRITICAL" else "PROBLEM",
                title=f"Home Assistant runtime error: {logger_name}",
                detail=f"{message} | source={source}",
                simulated=simulated,
            )
            findings.append(
                {
                    "problem_key": problem_key,
                    "incident_id": incident_id,
                    "kind": "ha_runtime_error",
                    "severity": "CRITICAL" if level == "CRITICAL" else "PROBLEM",
                    "error_level": level,
                    "logger": logger_name,
                    "message": message,
                    "source": source,
                    "exception": exception,
                    "fingerprint": fingerprint,
                    "event_timestamp": event.get("timestamp"),
                    "bridge_version": event.get("bridge_version"),
                    "simulated": simulated,
                }
            )

        target_health_checks = {
            "thermal": ("cpu_temperature_c", 80.0),
            "performance": ("host_cpu_percent", 90.0),
            "memory": ("host_memory_percent", 90.0),
            "storage": ("storage_used_percent", 90.0),
        }
        if targeted_category in target_health_checks:
            metric, threshold = target_health_checks[targeted_category]
            value = (target_context or {}).get(metric)
            problem_key = (
                f"health:{targeted_category}:simulated"
                if simulated
                else f"health:{targeted_category}"
            )
            if isinstance(value, (int, float)):
                if float(value) >= threshold:
                    current_problem_keys.add(problem_key)
                    incident_id = self.db.upsert_incident(
                        problem_key=problem_key,
                        incident_type="health_guard",
                        severity="DEGRADED" if targeted_category in {"thermal", "storage"} else "PROBLEM",
                        title=f"Health Guard: {targeted_category}",
                        detail=f"{metric}={value}; threshold={threshold}",
                        simulated=simulated,
                    )
                    findings.append(
                        {
                            "problem_key": problem_key,
                            "incident_id": incident_id,
                            "kind": "health_guard_targeted",
                            "category": targeted_category,
                            "metric": metric,
                            "value": value,
                            "threshold": threshold,
                            "simulated": simulated,
                        }
                    )
                else:
                    self.db.resolve_problem(
                        problem_key,
                        f"Health metric recovered: {metric}={value} < {threshold}.",
                    )
            else:
                errors["target_context"] = f"Missing numeric target metric: {metric}"

        host = data.get("host") or {}
        total = host.get("disk_total")
        used = host.get("disk_used")
        if targeted_category is None and isinstance(total, (int, float)) and total > 0 and isinstance(used, (int, float)):
            disk_pct = round(float(used) / float(total) * 100.0, 1)
            data["disk_used_percent"] = disk_pct
            if disk_pct >= 90:
                key = "health:storage"
                current_problem_keys.add(key)
                self.db.upsert_incident(
                    problem_key=key,
                    incident_type="resource",
                    severity="DEGRADED" if disk_pct >= 95 else "PROBLEM",
                    title="Системный диск почти заполнен",
                    detail=f"Использовано {disk_pct}% системного диска.",
                )
                findings.append({"problem_key": key, "kind": "storage", "value": disk_pct})

        backup_info = data.get("backups") or {}
        backups = backup_info.get("backups", []) if isinstance(backup_info, dict) else []
        backup_summary: dict[str, Any] = {"count": len(backups) if isinstance(backups, list) else 0}
        if isinstance(backups, list) and backups:
            latest = max(backups, key=lambda b: str(b.get("date", "")))
            backup_summary["latest"] = latest.get("date")
            backup_summary["latest_age_hours"] = _iso_age_hours(latest.get("date"))
        data["backup_summary"] = backup_summary

        mounts_payload = data.get("mounts")
        for mount in _inactive_mounts(mounts_payload):
            mount_name = str(mount.get("name") or "")
            key = f"supervisor_mount:{mount_name}"
            current_problem_keys.add(key)
            incident_id = self.db.upsert_incident(
                problem_key=key,
                incident_type="supervisor_mount",
                severity="PROBLEM",
                title=f"Supervisor mount недоступен: {mount_name}",
                detail=(
                    f"Supervisor reports mount state=inactive; "
                    f"type={mount.get('type')}; usage={mount.get('usage')}."
                ),
                simulated=simulated,
            )
            finding = {
                "problem_key": key,
                "kind": "supervisor_mount",
                "incident_id": incident_id,
                "mount_name": mount_name,
                "state": "inactive",
                "usage": mount.get("usage"),
                "mount_type": mount.get("type"),
                "simulated": simulated,
            }
            findings.append(finding)

            recovery_allowed = allow_generic_recovery and not simulated
            if recovery_allowed:
                attempts = self.db.incident_event_count(incident_id, "MOUNT_RELOAD_ATTEMPT")
                finding["reload_attempts_before"] = attempts
                if attempts >= 1:
                    finding["reload_skipped"] = "already_attempted_this_episode"
                    recovery_allowed = False

            if recovery_allowed:
                self.db.add_incident_event(
                    incident_id,
                    "MOUNT_RELOAD_ATTEMPT",
                    {"mount_name": mount_name, "state": "inactive"},
                )
                reloaded = await self.supervisor.reload_mount(mount_name)
                finding["reload_attempted"] = True
                finding["reload_accepted"] = reloaded
                if reloaded:
                    await asyncio.sleep(1.0)
                    verify_mounts, verify_error = await _safe(self.supervisor.mounts_info)
                    if verify_error:
                        finding["repeat_diagnosis_error"] = verify_error
                        finding["treatment_result"] = "FAILED"
                    else:
                        verify_state = _mount_state(verify_mounts, mount_name)
                        finding["repeat_diagnosis_state"] = verify_state
                        if verify_state == "active":
                            resolved = self.db.resolve_problem(
                                key,
                                "Supervisor mount reload succeeded; repeat diagnosis state=active.",
                            )
                            finding["treatment_result"] = "SUCCESS"
                            finding["incident_resolved"] = resolved
                            self.db.add_incident_event(
                                incident_id,
                                "MOUNT_RELOAD_SUCCESS",
                                {"repeat_diagnosis_state": verify_state},
                            )
                            current_problem_keys.discard(key)
                        else:
                            finding["treatment_result"] = "FAILED"
                            self.db.add_incident_event(
                                incident_id,
                                "MOUNT_RELOAD_FAILED",
                                {"repeat_diagnosis_state": verify_state},
                            )
                else:
                    finding["treatment_result"] = "FAILED"
                    self.db.add_incident_event(
                        incident_id,
                        "MOUNT_RELOAD_FAILED",
                        {"reason": "reload_not_accepted"},
                    )

        bridge = data.get("bridge")
        if isinstance(bridge, dict):
            issues = bridge.get("issues", [])
            repair_observations: list[dict[str, Any]] = []
            repair_ignored: dict[str, int] = {"inactive": 0, "dismissed": 0}
            if isinstance(issues, list):
                for issue in issues:
                    if not isinstance(issue, dict):
                        continue
                    domain = str(issue.get("domain") or "unknown")
                    issue_id = str(issue.get("issue_id") or "unknown")
                    key = f"repair:{domain}:{issue_id}"
                    title = str(issue.get("translation_key") or issue_id)
                    active = bool(issue.get("active", False))
                    dismissed = issue.get("dismissed_version")
                    severity = str(issue.get("severity") or "").lower()

                    # The HA issue registry retains non-persistent historical records
                    # as active=False after restart. They are not current faults.
                    if not active:
                        repair_ignored["inactive"] += 1
                        self.db.discard_problem(key, "HA Repair is inactive; historical/non-current issue.")
                        continue

                    # If the HA user has dismissed the issue for this HA version,
                    # Doctor must not resurrect it as a fault.
                    if dismissed:
                        repair_ignored["dismissed"] += 1
                        self.db.discard_problem(key, f"HA Repair dismissed in version {dismissed}.")
                        continue

                    # Mount failures are handled from authoritative Supervisor mount state,
                    # so a lagging HA Repair must not create a duplicate observation/incident.
                    if (
                        title in MOUNT_FAILED_TRANSLATION_KEYS
                        and isinstance(mounts_payload, dict)
                    ):
                        self.db.discard_problem(
                            key,
                            "Supervisor mount state handled by native mount recovery.",
                        )
                        continue

                    # A Repair WARNING is a real advisory from HA but not, by itself,
                    # evidence of functional failure. Keep it as OBSERVE only.
                    if severity not in {"error", "critical"}:
                        observation = {
                            "problem_key": key,
                            "kind": "repair_warning",
                            "domain": domain,
                            "issue_id": issue_id,
                            "severity": severity or "warning",
                            "breaks_in_ha_version": issue.get("breaks_in_ha_version"),
                            "is_fixable": issue.get("is_fixable"),
                        }
                        repair_observations.append(observation)
                        self.db.add_observation(key, "repair_warning", observation)
                        self.db.discard_problem(key, "HA Repair warning reclassified to OBSERVE.")
                        continue

                    # Active ERROR/CRITICAL Repairs are confirmed HA problems.
                    current_problem_keys.add(key)
                    doctor_severity = "CRITICAL" if severity == "critical" else "PROBLEM"
                    self.db.upsert_incident(
                        problem_key=key,
                        incident_type="repair_issue",
                        severity=doctor_severity,
                        title=f"Home Assistant Repair: {title}",
                        detail=f"Источник: {domain}; issue: {issue_id}; HA severity: {severity}",
                    )
                    findings.append({
                        "problem_key": key,
                        "kind": "repair",
                        "domain": domain,
                        "issue_id": issue_id,
                        "ha_severity": severity,
                    })

            entries = bridge.get("config_entries", [])
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    state = str(entry.get("state") or "")
                    if state not in PROBLEM_ENTRY_STATES:
                        continue
                    entry_id = str(entry.get("entry_id") or "")
                    domain = str(entry.get("domain") or "unknown")
                    key = f"config_entry:{entry_id}"
                    current_problem_keys.add(key)
                    entry_simulated = bool(entry.get("_doctor_simulated_state"))
                    incident_id = self.db.upsert_incident(
                        problem_key=key,
                        incident_type="config_entry",
                        severity="DEGRADED" if state in {"setup_error", "migration_error"} else "PROBLEM",
                        title=f"Интеграция {domain}: {state}",
                        detail=f"Config entry {entry_id} находится в состоянии {state}.",
                        simulated=entry_simulated,
                    )
                    incident = self.db.incident_by_id(incident_id) or {}
                    recurrence_count = int(incident.get("recurrence_count") or 0)
                    finding = {
                        "problem_key": key,
                        "kind": "config_entry",
                        "incident_id": incident_id,
                        "entry_id": entry_id,
                        "domain": domain,
                        "state": state,
                        "simulated": entry_simulated,
                        "recurrence_count": recurrence_count,
                    }
                    findings.append(finding)

                    recovery_allowed = allow_generic_recovery and state in {"setup_retry", "setup_error"}
                    if recovery_allowed and not entry_simulated and recurrence_count > 0:
                        finding["generic_reload_skipped"] = "recurrence_requires_disease_diagnosis"
                        finding["requires_disease_diagnosis"] = True
                        recovery_allowed = False

                    if recovery_allowed and not entry_simulated:
                        attempts = self.db.incident_event_count(incident_id, "GENERIC_RELOAD_ATTEMPT")
                        finding["generic_reload_attempts_before"] = attempts
                        if attempts >= 1:
                            finding["generic_reload_skipped"] = "already_attempted_this_episode"
                            recovery_allowed = False

                    if recovery_allowed:
                        if not entry_simulated:
                            self.db.add_incident_event(
                                incident_id,
                                "GENERIC_RELOAD_ATTEMPT",
                                {"entry_id": entry_id, "state": state},
                            )
                        reloaded = await self.ha.bridge_reload_entry(entry_id)
                        finding["generic_reload_attempted"] = True
                        finding["generic_reload_accepted"] = reloaded

                        if reloaded:
                            await asyncio.sleep(1.0)
                            verify_snapshot, verify_error = await _safe(self.ha.bridge_snapshot)
                            if verify_error:
                                finding["repeat_diagnosis_error"] = verify_error
                                finding["treatment_result"] = "FAILED"
                            else:
                                verify_entries = (
                                    verify_snapshot.get("config_entries", [])
                                    if isinstance(verify_snapshot, dict)
                                    else []
                                )
                                verify_entry = next(
                                    (
                                        item
                                        for item in verify_entries
                                        if isinstance(item, dict)
                                        and str(item.get("entry_id") or "") == entry_id
                                    ),
                                    None,
                                )
                                verify_state = (
                                    str(verify_entry.get("state") or "")
                                    if isinstance(verify_entry, dict)
                                    else ""
                                )
                                finding["repeat_diagnosis_state"] = verify_state or None
                                if verify_state and verify_state not in PROBLEM_ENTRY_STATES:
                                    resolved = self.db.resolve_problem(
                                        key,
                                        f"Generic reload succeeded; repeat diagnosis state={verify_state}.",
                                    )
                                    finding["treatment_result"] = "SUCCESS"
                                    finding["incident_resolved"] = resolved
                                    if not entry_simulated:
                                        self.db.add_incident_event(
                                            incident_id,
                                            "GENERIC_RELOAD_SUCCESS",
                                            {"repeat_diagnosis_state": verify_state},
                                        )
                                    current_problem_keys.discard(key)
                                else:
                                    finding["treatment_result"] = "FAILED"
                                    if not entry_simulated:
                                        self.db.add_incident_event(
                                            incident_id,
                                            "GENERIC_RELOAD_FAILED",
                                            {"repeat_diagnosis_state": verify_state or None},
                                        )
                        else:
                            finding["treatment_result"] = "FAILED"
                            if not entry_simulated:
                                self.db.add_incident_event(
                                    incident_id,
                                    "GENERIC_RELOAD_FAILED",
                                    {"reason": "reload_not_accepted"},
                                )

        if isinstance(mounts_payload, dict):
            for old_key in self.db.open_problem_keys(("supervisor_mount:",)):
                if old_key not in current_problem_keys:
                    self.db.resolve_problem(old_key)

        if isinstance(bridge, dict):
            for old_key in self.db.open_problem_keys(("repair:", "config_entry:")):
                if old_key not in current_problem_keys:
                    self.db.resolve_problem(old_key)

        disease_scan: dict[str, Any] | None = None
        scan_mode = "daily" if audit_type == "daily" else ("targeted" if audit_type == "targeted" else None)
        if scan_mode is not None:
            disease_scan, disease_findings, disease_errors = await self._run_disease_scan(
                mode=scan_mode,
                target_category=targeted_category,
                simulated=simulated,
                trigger_events=_bridge_trigger_events(bridge),
            )
            findings.extend(disease_findings)
            errors.update(disease_errors)

        observations = repair_observations if isinstance(bridge, dict) else []
        bridge_required = targeted_category is None
        result = "INCIDENTS_FOUND" if findings else (
            "OBSERVE"
            if observations or errors or (bridge_required and not isinstance(bridge, dict))
            else "HEALTHY"
        )
        payload = {
            "reason": reason,
            "findings": findings,
            "observations": observations,
            "observation_count": len(observations),
            "repair_ignored": repair_ignored if isinstance(bridge, dict) else {},
            "provider_errors": errors,
            "bridge_available": isinstance(bridge, dict),
            "system": data.get("system"),
            "supervisor": data.get("supervisor"),
            "host": data.get("host"),
            "core": data.get("core"),
            "core_stats": data.get("core_stats"),
            "backup_summary": data.get("backup_summary"),
            "disk_used_percent": data.get("disk_used_percent"),
            "disease_scan": disease_scan,
            "targeted": {
                "category": targeted_category,
                "collected_sources": names,
                "target_context": target_context or {},
            } if targeted_category is not None else None,
        }
        self.db.finish_audit(audit_id, result, len(findings), payload)
        return {"audit_id": audit_id, "result": result, **payload}
