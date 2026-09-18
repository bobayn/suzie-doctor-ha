from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from .db import Database
from .ha_api import HomeAssistantClient
from .supervisor import SupervisorClient

PROBLEM_ENTRY_STATES = {"setup_error", "setup_retry", "migration_error", "failed_unload"}


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
    def __init__(self, db: Database, supervisor: SupervisorClient, ha: HomeAssistantClient) -> None:
        self.db = db
        self.supervisor = supervisor
        self.ha = ha

    async def run(self, audit_type: str, reason: str | None = None, allow_generic_recovery: bool = True) -> dict[str, Any]:
        audit_id = self.db.begin_audit(audit_type, reason)
        results = await asyncio.gather(
            _safe(self.supervisor.info),
            _safe(self.supervisor.supervisor_info),
            _safe(self.supervisor.host_info),
            _safe(self.supervisor.core_info),
            _safe(self.supervisor.core_stats),
            _safe(self.supervisor.backups_info),
            _safe(self.supervisor.network_info),
            _safe(self.ha.get_config),
            _safe(self.ha.bridge_snapshot),
        )
        names = ["system", "supervisor", "host", "core", "core_stats", "backups", "network", "ha_config", "bridge"]
        data = {name: result[0] for name, result in zip(names, results)}
        errors = {name: result[1] for name, result in zip(names, results) if result[1]}

        current_problem_keys: set[str] = set()
        findings: list[dict[str, Any]] = []

        host = data.get("host") or {}
        total = host.get("disk_total")
        used = host.get("disk_used")
        if isinstance(total, (int, float)) and total > 0 and isinstance(used, (int, float)):
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

        if isinstance(bridge, dict):
            for old_key in self.db.open_problem_keys(("repair:", "config_entry:")):
                if old_key not in current_problem_keys:
                    self.db.resolve_problem(old_key)

        observations = repair_observations if isinstance(bridge, dict) else []
        result = "INCIDENTS_FOUND" if findings else ("OBSERVE" if observations or errors or not isinstance(bridge, dict) else "HEALTHY")
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
        }
        self.db.finish_audit(audit_id, result, len(findings), payload)
        return {"audit_id": audit_id, "result": result, **payload}
