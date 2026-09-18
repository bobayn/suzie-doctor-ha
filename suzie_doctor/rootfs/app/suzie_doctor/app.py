from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp import web

from . import APP_VERSION, BRIDGE_VERSION, PROTOCOL_PACK_VERSION
from .audit import Auditor, _bridge_trigger_events
from .bootstrap import bootstrap_bridge
from .db import Database
from .ha_api import HomeAssistantClient
from .health_guard import HealthGuard, MetricSample
from .local_metrics import LocalMetrics
from .options import Options, load_options
from .protocol_engine import ProtocolEngine
from .supervisor import SupervisorClient

DATA_DIR = Path(os.environ.get("SUZIE_DOCTOR_DATA", "/data"))
DB_PATH = DATA_DIR / "suzie_doctor.sqlite3"


class Runtime:
    def __init__(self) -> None:
        self.options: Options = load_options()
        self.db = Database(DB_PATH)
        self.db.initialize()
        self.supervisor = SupervisorClient()
        self.ha = HomeAssistantClient()
        self.protocol_engine = ProtocolEngine(
            self.db,
            self.supervisor,
            self.ha,
            app_version=APP_VERSION,
            bridge_version=BRIDGE_VERSION,
            pack_version=PROTOCOL_PACK_VERSION,
        )
        self.auditor = Auditor(
            self.db, self.supervisor, self.ha, self.protocol_engine
        )
        self.local_metrics = LocalMetrics()
        self.started_at = datetime.now(UTC).isoformat()
        self.last_health: dict[str, Any] = {}
        self.bootstrap_status: dict[str, Any] = {"status": "pending"}
        self.last_full_audit: dict[str, Any] | None = None
        self.background_status: dict[str, dict[str, Any]] = {}
        self._audit_lock = asyncio.Lock()

    def record_background_error(self, source: str, exc: BaseException) -> None:
        source = str(source or "unknown")
        previous = self.background_status.get(source) or {}
        count = int(previous.get("error_count") or 0) + 1
        message = str(exc).replace(chr(10), " ").strip()
        if len(message) > 300:
            message = message[:297] + "..."
        payload = {
            **previous,
            "state": "error",
            "error_count": count,
            "last_error_at": datetime.now(UTC).isoformat(),
            "error_type": type(exc).__name__,
            "error": message,
        }
        self.background_status[source] = payload
        print(
            f"Suzie Doctor background error | source={source} "
            f"count={count} type={payload['error_type']} error={message}",
            flush=True,
        )

    def record_background_ok(self, source: str) -> None:
        source = str(source or "unknown")
        previous = self.background_status.get(source) or {}
        was_error = previous.get("state") == "error"
        payload = {
            **previous,
            "state": "ok",
            "last_ok_at": datetime.now(UTC).isoformat(),
        }
        if was_error:
            payload["recovered_at"] = payload["last_ok_at"]
            print(
                f"Suzie Doctor background recovered | source={source}",
                flush=True,
            )
        self.background_status[source] = payload

    async def collect_fast(self) -> dict[str, Any]:
        local = self.local_metrics.snapshot()
        try:
            core = await self.supervisor.core_stats()
        except Exception as exc:
            self.record_background_error("collector:core_stats", exc)
            core = {}
        else:
            self.record_background_ok("collector:core_stats")
        values = {
            **local,
            "ha_core_cpu_percent": core.get("cpu_percent"),
            "ha_core_memory_percent": core.get("memory_percent"),
        }
        self.last_health.update({k: v for k, v in values.items() if v is not None})
        return values

    async def collect_normal(self) -> dict[str, Any]:
        try:
            host = await self.supervisor.host_info()
        except Exception as exc:
            self.record_background_error("collector:host_info", exc)
            host = {}
        else:
            self.record_background_ok("collector:host_info")
        total = host.get("disk_total")
        used = host.get("disk_used")
        pct = None
        if isinstance(total, (int, float)) and total > 0 and isinstance(used, (int, float)):
            pct = round(float(used) / float(total) * 100.0, 2)
        values = {
            "storage_total_gb": total,
            "storage_used_gb": used,
            "storage_free_gb": host.get("disk_free"),
            "storage_used_percent": pct,
            "disk_life_time_percent": host.get("disk_life_time"),
            "boot_timestamp": host.get("boot_timestamp"),
        }
        self.last_health.update({k: v for k, v in values.items() if v is not None})
        return values

    async def store_samples(self, samples: list[MetricSample]) -> None:
        rows = []
        for s in samples:
            if isinstance(s.value, bool):
                num, text = float(s.value), None
            elif isinstance(s.value, (int, float)):
                num, text = float(s.value), None
            else:
                num, text = None, None if s.value is None else str(s.value)
            rows.append((s.sampled_at, s.metric, num, text, s.source, 0))
        if rows:
            self.db.store_health_samples(rows)

    async def on_anomaly(self, category: str, title: str, payload: dict[str, Any]) -> None:
        problem_key = f"health:{category}"
        existed = self.db.has_open_problem(problem_key)
        self.db.upsert_incident(
            problem_key=problem_key,
            incident_type="health_guard",
            severity="DEGRADED" if category in {"thermal", "storage"} else "PROBLEM",
            title=title,
            detail=json.dumps(payload, ensure_ascii=False),
        )
        if not existed:
            try:
                await self.ha.persistent_notification(
                    "Suzie Doctor обнаружил проблему",
                    f"{title}. Suzie Doctor запускает целевой аудит.",
                    f"suzie_doctor_{category}",
                )
            except Exception as exc:
                self.record_background_error("notification:health_anomaly", exc)
            else:
                self.record_background_ok("notification:health_anomaly")
            asyncio.create_task(
                self._run_anomaly_audit(category),
                name=f"health_anomaly:{category}",
            )

    async def _run_anomaly_audit(self, category: str) -> None:
        try:
            await self.run_audit(
                "targeted",
                f"health_guard:{category}",
                target_context=dict(self.last_health),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.record_background_error(f"health_anomaly_audit:{category}", exc)
        else:
            self.record_background_ok(f"health_anomaly_audit:{category}")

    async def on_recovery(self, category: str, title: str, payload: dict[str, Any]) -> None:
        self.db.resolve_problem(
            f"health:{category}",
            f"Health Guard recovered: {json.dumps(payload, ensure_ascii=False)}",
        )

    async def system_busy(self) -> tuple[bool, str | None]:
        try:
            snapshot = await self.ha.bridge_snapshot()
        except Exception as exc:
            self.record_background_error("system_busy:bridge_snapshot", exc)
            snapshot = None
        else:
            self.record_background_ok("system_busy:bridge_snapshot")
        if isinstance(snapshot, dict):
            for entity in snapshot.get("backup_entities", []):
                state = str(entity.get("state") or "").lower()
                if state in {"create_backup", "creating_a_backup", "receive_backup", "receiving_a_backup", "restore_backup", "restoring_a_backup"}:
                    return True, f"backup:{state}"
        return False, None

    async def run_audit(
        self,
        audit_type: str,
        reason: str | None = None,
        *,
        target_context: dict[str, Any] | None = None,
        simulated: bool = False,
    ) -> dict[str, Any]:
        if self._audit_lock.locked():
            return {"result": "BUSY", "reason": "another_audit_running"}
        if audit_type in {"first_run", "daily", "developer_full", "full"}:
            busy, busy_reason = await self.system_busy()
            if busy:
                return {"result": "DELAYED", "reason": busy_reason}
        async with self._audit_lock:
            result = await self.auditor.run(
                audit_type,
                reason,
                allow_generic_recovery=self.options.trust_mode != "manual",
                target_context=target_context,
                simulated=simulated,
            )
            if audit_type in {"first_run", "daily", "full", "developer_full"}:
                self.last_full_audit = result
            return result

    async def first_run(self) -> None:
        try:
            self.bootstrap_status = {"status": "running"}
            self.bootstrap_status.update(
                await bootstrap_bridge(
                    self.supervisor,
                    self.ha,
                    auto_install=self.options.auto_install_bridge,
                    auto_restart_once=self.options.auto_restart_core_once,
                )
            )
            self.bootstrap_status["status"] = "done"
        except Exception as exc:
            self.bootstrap_status = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

        await asyncio.sleep(5)
        result = await self.run_audit("first_run", "installation")
        self.db.set_meta("first_run_completed", datetime.now(UTC).isoformat())
        try:
            summary = "Система в норме." if result.get("result") == "HEALTHY" else f"Результат: {result.get('result')}. Найдено: {len(result.get('findings', []))}."
            await self.ha.persistent_notification(
                "Suzie Doctor: первичный аудит завершён",
                summary,
                "suzie_doctor_first_audit",
            )
        except Exception as exc:
            self.record_background_error("notification:first_run", exc)
        else:
            self.record_background_ok("notification:first_run")

    async def bridge_watch_loop(self) -> None:
        while True:
            try:
                if not self.db.get_meta("bridge_attached_audit"):
                    snapshot = await self.ha.bridge_snapshot()
                    if isinstance(snapshot, dict):
                        result = await self.run_audit("full", "bridge_attached")
                        if result.get("result") != "BUSY":
                            self.db.set_meta("bridge_attached_audit", datetime.now(UTC).isoformat())
                    self.record_background_ok("bridge_watch")
                else:
                    self.record_background_ok("bridge_watch")
                    await asyncio.sleep(300)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.record_background_error("bridge_watch", exc)
            await asyncio.sleep(60)

    async def hourly_loop(self) -> None:
        await asyncio.sleep(60)
        while True:
            try:
                await self.run_audit("hourly", "scheduled")
                self.db.cleanup(self.options.retention_days)
                self.record_background_ok("hourly")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.record_background_error("hourly", exc)
            await asyncio.sleep(3600)

    async def daily_loop(self) -> None:
        last_date: str | None = None
        while True:
            try:
                info = await self.supervisor.info()
                tz_name = str(info.get("timezone") or "UTC")
                tz = ZoneInfo(tz_name)
                now = datetime.now(tz)
                hhmm = now.strftime("%H:%M")
                today = now.date().isoformat()
                if hhmm == self.options.daily_audit_time and last_date != today:
                    result = await self.run_audit("daily", "scheduled")
                    if result.get("result") != "DELAYED":
                        last_date = today
                self.record_background_ok("daily")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.record_background_error("daily", exc)
            await asyncio.sleep(30)


async def api_health(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    return web.json_response({"status": "ok", "version": APP_VERSION, "started_at": rt.started_at})


async def api_dashboard(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    data = rt.db.dashboard()
    data.update(
        {
            "doctor_status": "running",
            "app_version": APP_VERSION,
            "bridge_version": BRIDGE_VERSION,
            "protocol_pack_version": PROTOCOL_PACK_VERSION,
            "health": rt.last_health,
            "bootstrap": rt.bootstrap_status,
            "background_status": rt.background_status,
            "background_errors": {
                key: value
                for key, value in rt.background_status.items()
                if value.get("state") == "error"
            },
            "settings": asdict(rt.options),
        }
    )
    return web.json_response(data)


async def api_incidents(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    return web.json_response({"incidents": rt.db.incidents(200), "audits": rt.db.audits(30)})


async def api_settings(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    return web.json_response(asdict(rt.options))


async def api_dev_audit(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    if not rt.options.developer_mode:
        raise web.HTTPForbidden()
    body = await request.json() if request.can_read_body else {}
    audit_type = str(body.get("type", "developer_full"))
    reason = str(body.get("reason", "developer_manual"))
    result = await rt.run_audit(audit_type, reason)
    return web.json_response(result)


async def _dev_config_entry_recovery_test(rt: Runtime, state: str, reads: int = 1) -> dict[str, Any]:
    if state not in {"setup_retry", "setup_error"}:
        return {"result": "TEST_REJECTED", "reason": "unsupported_simulated_state"}

    snapshot = await rt.ha.bridge_snapshot()
    entries = snapshot.get("config_entries", []) if isinstance(snapshot, dict) else []
    target = next(
        (
            entry
            for entry in entries
            if isinstance(entry, dict)
            and str(entry.get("domain") or "") == "suzie_doctor"
            and str(entry.get("state") or "") == "loaded"
        ),
        None,
    )
    if not isinstance(target, dict):
        return {"result": "TEST_NOT_READY", "reason": "suzie_doctor_config_entry_not_loaded"}

    entry_id = str(target.get("entry_id") or "")
    rt.ha.simulate_entry_state_reads(entry_id, state, reads)
    result = await rt.run_audit(
        f"developer_simulated_{state}",
        f"controlled_{state}_treatment_test",
    )
    result["test_target"] = {
        "entry_id": entry_id,
        "domain": "suzie_doctor",
        "real_state_before": str(target.get("state") or ""),
        "simulated_state": state,
    }
    return result


async def api_dev_setup_retry_test(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    if not rt.options.developer_mode:
        raise web.HTTPForbidden()
    result = await _dev_config_entry_recovery_test(rt, "setup_retry")
    return web.json_response(result, status=409 if result.get("result") == "TEST_NOT_READY" else 200)


async def api_dev_setup_error_test(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    if not rt.options.developer_mode:
        raise web.HTTPForbidden()
    result = await _dev_config_entry_recovery_test(rt, "setup_error")
    return web.json_response(result, status=409 if result.get("result") == "TEST_NOT_READY" else 200)


async def api_dev_failed_recovery_test(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    if not rt.options.developer_mode:
        raise web.HTTPForbidden()

    # Keep the simulated symptom visible through the repeat diagnosis.
    # The actual integration reload is still real and safe, but Doctor must
    # classify the observed treatment outcome as FAILED.
    result = await _dev_config_entry_recovery_test(rt, "setup_retry", reads=2)
    finding = (result.get("findings") or [{}])[0]
    cleanup_resolved = False
    if isinstance(finding, dict) and finding.get("simulated"):
        problem_key = str(finding.get("problem_key") or "")
        if problem_key:
            cleanup_resolved = rt.db.resolve_problem(
                problem_key,
                "Developer failed-treatment simulation cleanup.",
            )
    result["simulated_cleanup_resolved"] = cleanup_resolved
    return web.json_response(result)


async def api_dev_protocol_inventory(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    if not rt.options.developer_mode:
        raise web.HTTPForbidden()
    inventory = rt.protocol_engine.inventory()
    inventory["recent_runs"] = rt.db.protocol_runs(10)
    inventory["telemetry_queue_depth"] = len(rt.db.telemetry_queue(1000))
    return web.json_response(inventory)


async def api_dev_protocol_pack_diagnostics(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    if not rt.options.developer_mode:
        raise web.HTTPForbidden()

    loaded = rt.protocol_engine.load_pack()
    results = []
    for card in loaded["cards"]:
        result = await rt.protocol_engine.execute_card(
            card,
            context={},
            trust_mode=rt.options.trust_mode,
            simulated=True,
            developer_override=False,
        )
        results.append(
            {
                "disease_id": card["disease_id"],
                "protocol_id": card["protocol"]["id"],
                "status": card["protocol"]["status"],
                "automation_class": card["automation_class"],
                "result": result.get("result"),
                "diagnosis_confirmed": result.get("diagnosis_confirmed"),
                "treatment_allowed": result.get("treatment_allowed"),
                "treatment_gate": result.get("treatment_gate"),
                "diagnostics": result.get("diagnostics", []),
                "error": result.get("error"),
            }
        )

    return web.json_response(
        {
            "pack": loaded["pack"],
            "results": results,
            "protocol_runs_created": 0,
            "note": "WATCH cards remain non-treatment diagnostics.",
        }
    )


async def api_dev_filesystem_readonly_regression_test(
    request: web.Request,
) -> web.Response:
    rt: Runtime = request.app["runtime"]
    if not rt.options.developer_mode:
        raise web.HTTPForbidden()

    cases = [
        {
            "id": "haos_etc_hosts_erofs",
            "expected": False,
            "logs": (
                "etc-hosts.mount: Failed to prepare /etc/hosts: "
                "Read-only file system"
            ),
        },
        {
            "id": "haos_etc_hostname_erofs",
            "expected": False,
            "logs": (
                "etc-hostname.mount: Failed to prepare /etc/hostname: "
                "Read-only file system"
            ),
        },
        {
            "id": "haos_root_erofs_readonly",
            "expected": False,
            "logs": "VFS: Mounted root (erofs filesystem) readonly on device 0:20.",
        },
        {
            "id": "real_ext4_remount_readonly",
            "expected": True,
            "logs": "EXT4-fs (nvme0n1p8): Remounting filesystem read-only",
        },
        {
            "id": "mutable_config_write_failed",
            "expected": True,
            "logs": (
                "sqlite3: unable to write /config/home-assistant_v2.db: "
                "Read-only file system"
            ),
        },
        {
            "id": "benign_then_real_failure",
            "expected": True,
            "logs": (
                "etc-hosts.mount: Failed to prepare /etc/hosts: "
                "Read-only file system\n"
                "EXT4-fs (nvme0n1p8): Remounting filesystem read-only"
            ),
        },
        {
            "id": "irrelevant_readonly_text",
            "expected": False,
            "logs": "notice: documentation mentions a read-only file system image",
        },
    ]

    results = []
    for case in cases:
        actual = rt.protocol_engine.classify_filesystem_readonly_logs(case["logs"])
        results.append(
            {
                "id": case["id"],
                "expected": case["expected"],
                "actual": actual,
                "pass": actual is case["expected"],
            }
        )

    passed = all(item["pass"] for item in results)
    return web.json_response(
        {
            "result": "PASS" if passed else "FAIL",
            "cases": results,
            "live_host_logs_read": False,
            "live_database_touched": False,
            "note": (
                "Regression guard for HAOS immutable EROFS false positives; "
                "uses the production filesystem-readonly classifier."
            ),
        }
    )


async def api_dev_mount_recovery_test(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    if not rt.options.developer_mode:
        raise web.HTTPForbidden()

    class FakeSupervisor:
        def __init__(
            self,
            mount_name: str,
            *,
            after_state: str,
            reload_accepted: bool = True,
        ) -> None:
            self.mount_name = mount_name
            self.state = "inactive"
            self.after_state = after_state
            self.reload_accepted = reload_accepted
            self.reload_calls = 0

        async def info(self) -> dict[str, Any]:
            return {}

        async def supervisor_info(self) -> dict[str, Any]:
            return {}

        async def host_info(self) -> dict[str, Any]:
            return {}

        async def core_info(self) -> dict[str, Any]:
            return {}

        async def core_stats(self) -> dict[str, Any]:
            return {}

        async def backups_info(self) -> dict[str, Any]:
            return {"backups": []}

        async def network_info(self) -> dict[str, Any]:
            return {}

        async def mounts_info(self) -> dict[str, Any]:
            return {
                "mounts": [
                    {
                        "name": self.mount_name,
                        "state": self.state,
                        "type": "cifs",
                        "usage": "media",
                    }
                ]
            }

        async def reload_mount(self, name: str) -> bool:
            if name != self.mount_name:
                return False
            self.reload_calls += 1
            if self.reload_accepted:
                self.state = self.after_state
            return self.reload_accepted

    class FakeHA:
        async def get_config(self) -> dict[str, Any]:
            return {}

        async def bridge_snapshot(self) -> dict[str, Any]:
            return {"issues": [], "config_entries": []}

    def mount_finding(result: dict[str, Any]) -> dict[str, Any]:
        return next(
            (
                item
                for item in result.get("findings", [])
                if isinstance(item, dict)
                and item.get("kind") == "supervisor_mount"
            ),
            {},
        )

    with TemporaryDirectory(prefix="suzie-doctor-mount-test-") as tmp:
        test_db = Database(Path(tmp) / "test.sqlite3")
        test_db.initialize()
        try:
            fake_ha = FakeHA()

            success_supervisor = FakeSupervisor(
                "dev_mount_success",
                after_state="active",
            )
            success_auditor = Auditor(
                test_db,
                success_supervisor,
                fake_ha,
                rt.protocol_engine,
            )
            success_result = await success_auditor.run(
                "developer_mount_recovery",
                "simulated_mount_success",
            )
            success_finding = mount_finding(success_result)
            success_open = (
                "supervisor_mount:dev_mount_success"
                in test_db.open_problem_keys(("supervisor_mount:",))
            )

            failure_supervisor = FakeSupervisor(
                "dev_mount_failure",
                after_state="inactive",
            )
            failure_auditor = Auditor(
                test_db,
                failure_supervisor,
                fake_ha,
                rt.protocol_engine,
            )
            failure_first = await failure_auditor.run(
                "developer_mount_recovery",
                "simulated_mount_failure_first",
            )
            failure_first_finding = mount_finding(failure_first)
            reload_calls_after_first = failure_supervisor.reload_calls

            failure_second = await failure_auditor.run(
                "developer_mount_recovery",
                "simulated_mount_failure_second",
            )
            failure_second_finding = mount_finding(failure_second)
            failure_open = (
                "supervisor_mount:dev_mount_failure"
                in test_db.open_problem_keys(("supervisor_mount:",))
            )

            cases = [
                {
                    "id": "success_reload_once",
                    "pass": success_supervisor.reload_calls == 1,
                    "actual": success_supervisor.reload_calls,
                    "expected": 1,
                },
                {
                    "id": "success_repeat_active",
                    "pass": success_finding.get("repeat_diagnosis_state") == "active",
                    "actual": success_finding.get("repeat_diagnosis_state"),
                    "expected": "active",
                },
                {
                    "id": "success_treatment_result",
                    "pass": success_finding.get("treatment_result") == "SUCCESS",
                    "actual": success_finding.get("treatment_result"),
                    "expected": "SUCCESS",
                },
                {
                    "id": "success_incident_resolved",
                    "pass": not success_open,
                    "actual": success_open,
                    "expected": False,
                },
                {
                    "id": "failure_repeat_inactive",
                    "pass": failure_first_finding.get("repeat_diagnosis_state") == "inactive",
                    "actual": failure_first_finding.get("repeat_diagnosis_state"),
                    "expected": "inactive",
                },
                {
                    "id": "failure_first_attempt_once",
                    "pass": reload_calls_after_first == 1,
                    "actual": reload_calls_after_first,
                    "expected": 1,
                },
                {
                    "id": "failure_second_attempt_blocked",
                    "pass": (
                        failure_supervisor.reload_calls == 1
                        and failure_second_finding.get("reload_skipped")
                        == "already_attempted_this_episode"
                    ),
                    "actual": {
                        "reload_calls": failure_supervisor.reload_calls,
                        "reload_skipped": failure_second_finding.get("reload_skipped"),
                    },
                    "expected": {
                        "reload_calls": 1,
                        "reload_skipped": "already_attempted_this_episode",
                    },
                },
                {
                    "id": "failure_incident_stays_open",
                    "pass": failure_open,
                    "actual": failure_open,
                    "expected": True,
                },
            ]
        finally:
            test_db.conn.close()

    passed = all(item["pass"] for item in cases)
    return web.json_response(
        {
            "result": "PASS" if passed else "FAIL",
            "cases": cases,
            "live_supervisor_touched": False,
            "live_database_touched": False,
            "live_mounts_touched": False,
            "note": (
                "Production Auditor mount-recovery regression using fake "
                "Supervisor/HA providers and a temporary SQLite database."
            ),
        }
    )


async def api_dev_trigger_matching_test(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    if not rt.options.developer_mode:
        raise web.HTTPForbidden()

    loaded = rt.protocol_engine.load_pack()
    card = next(
        (
            item
            for item in loaded.get("cards", [])
            if item.get("disease_id")
            == "DISEASE-RECORDER-EXTERNAL-DB-UNREACHABLE-001"
        ),
        None,
    )
    if not isinstance(card, dict):
        return web.json_response(
            {"result": "FAIL", "reason": "external_recorder_card_missing"},
            status=500,
        )

    matching_event = {
        "type": "disease_confirmed",
        "match": "DISEASE-RECORDER-WRITE-UNAVAILABLE-001",
    }
    unrelated_event = {
        "type": "disease_confirmed",
        "match": "DISEASE-STORAGE-READONLY-001",
    }
    cases = [
        {"id": "positive_mysql", "context": {"recorder_present": True, "database_family": "mysql", "trigger_events": [matching_event]}, "expected_run": True},
        {"id": "positive_postgresql", "context": {"recorder_present": True, "database_family": "postgresql", "trigger_events": [matching_event]}, "expected_run": True},
        {"id": "no_trigger_mysql", "context": {"recorder_present": True, "database_family": "mysql", "trigger_events": []}, "expected_run": False},
        {"id": "unrelated_trigger_mysql", "context": {"recorder_present": True, "database_family": "mysql", "trigger_events": [unrelated_event]}, "expected_run": False},
        {"id": "wrong_database_family", "context": {"recorder_present": True, "database_family": "oracle", "trigger_events": [matching_event]}, "expected_run": False},
        {"id": "unknown_database_family", "context": {"recorder_present": True, "database_family": "", "trigger_events": [matching_event]}, "expected_run": False},
        {"id": "sqlite_negative_path", "context": {"recorder_present": True, "database_family": "sqlite", "trigger_events": [matching_event]}, "expected_run": False},
    ]

    results = []
    for case in cases:
        context = case["context"]
        applicable, applicability_reason = rt.protocol_engine.card_scan_applicability(
            card, mode="triggered", context=context
        )
        trigger_matched = False
        trigger_reason = None
        if applicable:
            trigger_matched, trigger_reason = rt.protocol_engine.card_trigger_match(
                card, context=context
            )
        should_run = applicable and trigger_matched
        results.append(
            {
                "id": case["id"],
                "expected_run": case["expected_run"],
                "actual_run": should_run,
                "applicable": applicable,
                "applicability_reason": applicability_reason,
                "trigger_matched": trigger_matched,
                "trigger_reason": trigger_reason,
                "pass": should_run is case["expected_run"],
            }
        )

    bridge_events = _bridge_trigger_events(
        {
            "issues": [
                {
                    "active": True,
                    "dismissed_version": None,
                    "translation_key": "issue_mount_mount_failed",
                    "issue_id": "active_mount",
                },
                {
                    "active": False,
                    "dismissed_version": None,
                    "translation_key": "historical_issue",
                    "issue_id": "historical",
                },
                {
                    "active": True,
                    "dismissed_version": "2026.9.3",
                    "translation_key": "dismissed_issue",
                    "issue_id": "dismissed",
                },
            ],
            "config_entries": [
                {"domain": "demo", "state": "setup_retry"},
                {"domain": "healthy", "state": "loaded"},
            ],
        }
    )
    bridge_event_set = {
        (str(item.get("type") or ""), str(item.get("match") or ""))
        for item in bridge_events
        if isinstance(item, dict)
    }
    results.extend(
        [
            {
                "id": "bridge_active_repair_event",
                "expected_run": True,
                "actual_run": (
                    "repair_issue",
                    "issue_mount_mount_failed",
                )
                in bridge_event_set,
                "pass": (
                    "repair_issue",
                    "issue_mount_mount_failed",
                )
                in bridge_event_set,
            },
            {
                "id": "bridge_problem_config_entry_event",
                "expected_run": True,
                "actual_run": (
                    "config_entry_state",
                    "demo:setup_retry",
                )
                in bridge_event_set,
                "pass": (
                    "config_entry_state",
                    "demo:setup_retry",
                )
                in bridge_event_set,
            },
            {
                "id": "bridge_ignored_noise_absent",
                "expected_run": True,
                "actual_run": (
                    ("repair_issue", "historical_issue") not in bridge_event_set
                    and ("repair_issue", "dismissed_issue") not in bridge_event_set
                    and ("config_entry_state", "healthy:loaded") not in bridge_event_set
                ),
                "pass": (
                    ("repair_issue", "historical_issue") not in bridge_event_set
                    and ("repair_issue", "dismissed_issue") not in bridge_event_set
                    and ("config_entry_state", "healthy:loaded") not in bridge_event_set
                ),
            },
        ]
    )

    passed = all(item["pass"] for item in results)
    return web.json_response(
        {
            "result": "PASS" if passed else "FAIL",
            "cases": results,
            "diagnostics_executed": False,
            "live_database_touched": False,
            "live_logs_read": False,
            "note": "Pure trigger/applicability regression for the triggered-only external Recorder DB card.",
        }
    )


async def api_dev_release_gate(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    if not rt.options.developer_mode:
        raise web.HTTPForbidden()

    async def _response_json(
        handler: Any,
    ) -> dict[str, Any]:
        response = await handler(request)
        try:
            payload = json.loads(response.text)
        except Exception:
            return {
                "result": "FAIL",
                "reason": "selftest_response_not_json",
            }
        return payload if isinstance(payload, dict) else {
            "result": "FAIL",
            "reason": "selftest_response_not_mapping",
        }

    readonly = await _response_json(api_dev_filesystem_readonly_regression_test)
    triggers = await _response_json(api_dev_trigger_matching_test)
    mounts = await _response_json(api_dev_mount_recovery_test)
    recurrence = await _response_json(api_dev_recurrence_test)

    pack_inventory: dict[str, Any]
    try:
        pack_inventory = rt.protocol_engine.inventory()
    except Exception as exc:
        pack_inventory = {
            "pack": {},
            "cards": [],
            "inventory_error": f"{type(exc).__name__}: {exc}",
        }

    pack_meta = pack_inventory.get("pack") or {}
    cards = pack_inventory.get("cards") or []
    unsupported = [
        {
            "disease_id": item.get("disease_id"),
            "unsupported_primitives": item.get("unsupported_primitives"),
        }
        for item in cards
        if isinstance(item, dict) and item.get("unsupported_primitives")
    ]
    version_consistent = (
        str(pack_meta.get("pack_version") or "") == PROTOCOL_PACK_VERSION
    )
    active_background_errors = {
        key: value
        for key, value in rt.background_status.items()
        if value.get("state") == "error"
    }

    checks = {
        "filesystem_readonly": readonly.get("result") == "PASS",
        "trigger_matching": triggers.get("result") == "PASS",
        "mount_recovery": mounts.get("result") == "PASS",
        "recurrence": recurrence.get("result") == "PASS",
        "pack_version_consistent": version_consistent,
        "supported_primitives_only": not unsupported,
        "background_errors_clear": not active_background_errors,
    }
    passed = all(checks.values())

    return web.json_response(
        {
            "result": "PASS" if passed else "FAIL",
            "checks": checks,
            "versions": {
                "app": APP_VERSION,
                "bridge": BRIDGE_VERSION,
                "protocol_pack": PROTOCOL_PACK_VERSION,
                "loaded_pack": pack_meta.get("pack_version"),
            },
            "suites": {
                "filesystem_readonly": {
                    "result": readonly.get("result"),
                    "cases": len(readonly.get("cases") or []),
                },
                "trigger_matching": {
                    "result": triggers.get("result"),
                    "cases": len(triggers.get("cases") or []),
                },
                "mount_recovery": {
                    "result": mounts.get("result"),
                    "cases": len(mounts.get("cases") or []),
                },
                "recurrence": {
                    "result": recurrence.get("result"),
                },
            },
            "unsupported_primitives": unsupported,
            "active_background_errors": active_background_errors,
            "live_mounts_touched": False,
            "live_database_touched_by_pure_suites": False,
            "note": (
                "Developer release gate aggregates safe regression suites; "
                "real daily audit remains a separate live verification step."
            ),
        }
    )


async def api_dev_protocol_selftest(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    if not rt.options.developer_mode:
        raise web.HTTPForbidden()

    snapshot = await rt.ha.bridge_snapshot()
    entries = snapshot.get("config_entries", []) if isinstance(snapshot, dict) else []
    target = next(
        (
            entry
            for entry in entries
            if isinstance(entry, dict)
            and str(entry.get("domain") or "") == "suzie_doctor"
            and str(entry.get("state") or "") == "loaded"
        ),
        None,
    )
    if not isinstance(target, dict):
        return web.json_response(
            {"result": "TEST_NOT_READY", "reason": "suzie_doctor_config_entry_not_loaded"},
            status=409,
        )

    entry_id = str(target.get("entry_id") or "")
    problem_key = "developer:protocol:selftest"
    rt.db.resolve_problem(problem_key, "Reset stale developer protocol self-test.")
    incident_id = rt.db.upsert_incident(
        problem_key=problem_key,
        incident_type="developer_protocol_test",
        severity="PROBLEM",
        title="Protocol Engine self-test",
        detail="Simulated setup_retry on Suzie Doctor integration.",
        simulated=True,
    )

    card = {
        "schema_version": 1,
        "disease_id": "DISEASE-DEVELOPER-CONFIG-ENTRY-RETRY-001",
        "title": "Developer config-entry recovery self-test",
        "component": "suzie_doctor",
        "protocol": {
            "id": "PROTOCOL-DEVELOPER-CONFIG-ENTRY-RELOAD-001",
            "version": "1.0.0",
            "status": "ACTIVE",
        },
        "source_evidence": [],
        "triggers": {"any": []},
        "preconditions": [],
        "diagnostics": [
            {
                "id": "entry_state",
                "primitive": "config_entry_state",
                "args": {"entry_id": "$entry_id"},
                "save_as": "entry_state",
            }
        ],
        "confirm": {
            "all": [{"expr": "entry_state == 'setup_retry'"}],
        },
        "exclude": [],
        "dont_do": [],
        "checkpoint": {
            "required": False,
            "primitive": None,
            "args": {},
        },
        "treatment": [
            {
                "step": 1,
                "primitive": "reload_config_entry",
                "args": {"entry_id": "$entry_id"},
                "max_attempts": 1,
            }
        ],
        "verify": {
            "rerun_diagnostics": True,
            "success_when": "no_original_symptoms",
        },
        "fallback": [],
        "rollback": [],
        "cooldown_seconds": 0,
        "recurrence_rule": "daily_audit_boundary",
        "on_failure": "ESCALATION_REQUIRED",
        "automation_class": "AUTO_SAFE",
    }

    rt.ha.simulate_entry_state_once(entry_id, "setup_retry")
    result = await rt.protocol_engine.execute_card(
        card,
        context={"entry_id": entry_id},
        incident_id=incident_id,
        trust_mode=rt.options.trust_mode,
        simulated=True,
        developer_override=True,
    )

    cleanup_resolved = rt.db.resolve_problem(
        problem_key,
        "Developer protocol self-test cleanup.",
    )
    telemetry = rt.db.telemetry_queue(1)
    latest_telemetry = telemetry[0] if telemetry else None
    if latest_telemetry is not None:
        payload = latest_telemetry.get("payload") or {}
        latest_telemetry = {
            "seq": latest_telemetry.get("seq"),
            "created_at": latest_telemetry.get("created_at"),
            "result": payload.get("result"),
            "protocol_id": payload.get("protocol_id"),
            "protocol_version": payload.get("protocol_version"),
            "simulated": payload.get("simulated"),
        }

    result["simulated_cleanup_resolved"] = cleanup_resolved
    result["latest_telemetry"] = latest_telemetry
    return web.json_response(result)


async def api_dev_persistence_prepare(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    if not rt.options.developer_mode:
        raise web.HTTPForbidden()

    problem_key = "developer:persistence:simulated"
    rt.db.resolve_problem(problem_key, "Reset stale developer persistence test.")
    incident_id = rt.db.upsert_incident(
        problem_key=problem_key,
        incident_type="developer_test",
        severity="PROBLEM",
        title="Persistence restart self-test",
        detail="This simulated unfinished incident must survive a Doctor App restart.",
        simulated=True,
    )
    incident = rt.db.incident_by_id(incident_id) or {}
    return web.json_response(
        {
            "result": "PREPARED",
            "incident_id": incident_id,
            "problem_key": problem_key,
            "status": incident.get("status"),
            "resolved_at": incident.get("resolved_at"),
            "simulated": bool(incident.get("simulated")),
        }
    )


async def api_dev_persistence_check(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    if not rt.options.developer_mode:
        raise web.HTTPForbidden()
    body = await request.json() if request.can_read_body else {}
    incident_id = str(body.get("incident_id") or "")
    incident = rt.db.incident_by_id(incident_id) if incident_id else None
    persisted_open = bool(
        incident
        and incident.get("status") == "OPEN"
        and incident.get("resolved_at") is None
        and bool(incident.get("simulated"))
    )
    cleanup_resolved = False
    if incident:
        problem_key = str(incident.get("problem_key") or "")
        if problem_key:
            cleanup_resolved = rt.db.resolve_problem(
                problem_key,
                "Developer persistence restart self-test cleanup.",
            )
    return web.json_response(
        {
            "result": "PASS" if persisted_open else "FAIL",
            "incident_id": incident_id,
            "persisted_open": persisted_open,
            "incident": incident,
            "cleanup_resolved": cleanup_resolved,
        }
    )


async def api_dev_targeted_audit_test(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    if not rt.options.developer_mode:
        raise web.HTTPForbidden()

    result = await rt.run_audit(
        "targeted",
        "health_guard:storage",
        target_context={"storage_used_percent": 95.0},
        simulated=True,
    )
    finding = (result.get("findings") or [{}])[0]
    cleanup_resolved = False
    if isinstance(finding, dict):
        problem_key = str(finding.get("problem_key") or "")
        if problem_key:
            cleanup_resolved = rt.db.resolve_problem(
                problem_key,
                "Developer targeted-audit simulation cleanup.",
            )
    result["simulated_cleanup_resolved"] = cleanup_resolved
    return web.json_response(result)


async def api_dev_recurrence_test(request: web.Request) -> web.Response:
    rt: Runtime = request.app["runtime"]
    if not rt.options.developer_mode:
        raise web.HTTPForbidden()

    # Exercise the production recurrence code against an isolated in-memory DB.
    test_db = Database(":memory:")
    test_db.initialize()
    key = "developer:recurrence:selftest"

    first_id = test_db.upsert_incident(
        problem_key=key,
        incident_type="developer_test",
        severity="PROBLEM",
        title="Recurrence self-test",
        detail="first episode",
        simulated=True,
    )
    test_db.resolve_problem(key, "developer self-test first recovery")

    same_episode_id = test_db.upsert_incident(
        problem_key=key,
        incident_type="developer_test",
        severity="PROBLEM",
        title="Recurrence self-test",
        detail="returned before daily boundary",
        simulated=True,
    )
    same_episode_ok = same_episode_id == first_id

    test_db.resolve_problem(key, "developer self-test second recovery")
    daily_id = test_db.begin_audit("daily", "developer_recurrence_boundary")
    test_db.finish_audit(daily_id, "HEALTHY", 0, {"simulated": True})

    recurrence_id = test_db.upsert_incident(
        problem_key=key,
        incident_type="developer_test",
        severity="PROBLEM",
        title="Recurrence self-test",
        detail="returned after daily boundary",
        simulated=True,
    )
    recurrence = test_db.incident_by_id(recurrence_id) or {}
    recurrence_ok = (
        recurrence_id != first_id
        and recurrence.get("recurrence_of") == first_id
        and int(recurrence.get("recurrence_count") or 0) == 1
    )

    return web.json_response(
        {
            "result": "PASS" if same_episode_ok and recurrence_ok else "FAIL",
            "same_episode": {
                "first_id": first_id,
                "returned_id": same_episode_id,
                "same_id": same_episode_ok,
            },
            "after_daily_boundary": {
                "new_id": recurrence_id,
                "recurrence_of": recurrence.get("recurrence_of"),
                "recurrence_count": recurrence.get("recurrence_count"),
                "new_episode": recurrence_ok,
            },
            "live_database_touched": False,
        }
    )


async def ui_index(request: web.Request) -> web.Response:
    ingress_base = request.headers.get("X-Ingress-Path", "").rstrip("/")
    html = UI_HTML.replace("__INGRESS_BASE__", json.dumps(ingress_base))
    return web.Response(text=html, content_type="text/html")


UI_HTML = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Suzie Doctor</title><style>
:root{font-family:system-ui,-apple-system,sans-serif;color-scheme:light dark}body{margin:0;background:var(--bg,#101418)}
main{max-width:980px;margin:auto;padding:18px}.top{display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap}
h1{font-size:24px;margin:4px 0}.tabs{display:flex;gap:8px;margin:16px 0}.tabs button,.btn{border:0;border-radius:10px;padding:10px 14px;cursor:pointer}
.card{background:#ffffff0d;border:1px solid #ffffff1f;border-radius:16px;padding:16px;margin:12px 0}.hero{font-size:28px;font-weight:700}.muted{opacity:.68}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.metric{font-size:23px;font-weight:650}.good{color:#63d391}.warn{color:#ffcf66}.bad{color:#ff7b7b}
.row{padding:10px 0;border-bottom:1px solid #ffffff16}.row:last-child{border:0}.hidden{display:none}code{font-size:12px}.pill{display:inline-block;padding:3px 8px;border-radius:999px;background:#ffffff17}
</style></head><body><main>
<div class="top"><div><h1>Suzie Doctor DEV</h1><div class="muted" id="version"></div></div><span class="pill" id="status">загрузка…</span></div>
<div class="tabs"><button onclick="show('home')">Главная</button><button onclick="show('incidents')">Инциденты</button><button onclick="show('settings')">Настройки</button></div>
<section id="home"><div class="card"><div class="hero" id="healthTitle">Проверяю систему…</div><div class="muted" id="auditText"></div></div>
<div class="grid"><div class="card"><div class="muted">За 24 часа исправлено</div><div class="metric" id="fixed24">—</div></div><div class="card"><div class="muted">Найдено за 24 часа</div><div class="metric" id="found24">—</div></div><div class="card"><div class="muted">Открытых проблем</div><div class="metric" id="openCount">—</div></div></div>
<div class="card"><b>Health Guard</b><div id="metrics" class="grid"></div></div><div class="card"><b>Установка bridge</b><pre id="bootstrap" class="muted"></pre></div>
<div class="card" id="devCard"><b>Developer mode</b><p class="muted">Служебные тесты для разработки. Симуляции не учитываются в пользовательской статистике.</p><button class="btn" onclick="devAudit()">Запустить полный аудит</button> <button class="btn" onclick="devTreatmentTest('setup-retry')">Тест setup_retry</button> <button class="btn" onclick="devTreatmentTest('setup-error')">Тест setup_error</button> <button class="btn" onclick="devRecurrenceTest()">Тест recurrence</button> <button class="btn" onclick="devFailedTreatmentTest()">Тест FAILED</button> <button class="btn" onclick="devTargetedTest()">Тест targeted</button> <button class="btn" onclick="devProtocolTest()">Тест protocol</button> <button class="btn" onclick="devReadonlyTest()">Тест readonly</button> <button class="btn" onclick="devTriggerTest()">Тест triggers</button> <button class="btn" onclick="devMountTest()">Тест mount</button> <button class="btn" onclick="devReleaseGate()">Release gate</button><span id="devResult"></span></div></section>
<section id="incidents" class="hidden"><div class="card"><b>Инциденты</b><div id="incidentList"></div></div><div class="card"><b>Последние аудиты</b><div id="auditList"></div></div></section>
<section id="settings" class="hidden"><div class="card"><b>Настройки</b><pre id="settingsText"></pre><p class="muted">В DEV-сборке меняются в Configuration приложения Home Assistant.</p></div></section>
<script>
const BASE=__INGRESS_BASE__;
function api(path){return `${BASE}${path}`}
function show(id){for(const s of ['home','incidents','settings'])document.getElementById(s).classList.toggle('hidden',s!==id);if(id==='incidents')loadIncidents()}
function fmt(v,s=''){return v===undefined||v===null?'—':`${v}${s}`}
async function refresh(){const d=await fetch(api('/api/dashboard')).then(r=>r.json());document.getElementById('status').textContent='Doctor работает';document.getElementById('version').textContent=`App ${d.app_version} · Bridge ${d.bridge_version} · Pack ${d.protocol_pack_version}`;document.getElementById('fixed24').textContent=d.fixed_24h;document.getElementById('found24').textContent=d.found_24h;document.getElementById('openCount').textContent=d.open_incidents;document.getElementById('healthTitle').textContent=d.open_incidents?`Есть проблем: ${d.open_incidents}`:(d.last_audit&&d.last_audit.result==='OBSERVE'?'Есть наблюдения':'Система в норме');document.getElementById('auditText').textContent=d.last_audit?`Последний аудит: ${d.last_audit.audit_type} · ${d.last_audit.result}`:'Первичный аудит ещё не завершён';
const h=d.health||{};const items=[['Температура CPU',h.cpu_temperature_c,' °C'],['CPU',h.host_cpu_percent,' %'],['RAM',h.host_memory_percent,' %'],['Load 5m',h.load_5m,''],['Диск',h.storage_used_percent,' %'],['Ресурс диска использован',h.disk_life_time_percent,' %']];document.getElementById('metrics').innerHTML=items.map(x=>`<div><div class="muted">${x[0]}</div><div class="metric">${fmt(x[1],x[2])}</div></div>`).join('');document.getElementById('bootstrap').textContent=JSON.stringify(d.bootstrap,null,2);document.getElementById('settingsText').textContent=JSON.stringify(d.settings,null,2);document.getElementById('devCard').style.display=d.settings.developer_mode?'block':'none'}
async function loadIncidents(){const d=await fetch(api('/api/incidents')).then(r=>r.json());document.getElementById('incidentList').innerHTML=d.incidents.length?d.incidents.map(i=>`<div class="row"><b>${i.title}</b> <span class="pill">${i.status}</span><div class="muted">${i.severity} · ${i.opened_at}</div><div>${i.detail||''}</div></div>`).join(''):'<p class="muted">Инцидентов нет.</p>';document.getElementById('auditList').innerHTML=d.audits.map(a=>`<div class="row"><b>${a.audit_type}</b> · ${a.result||'RUNNING'}<div class="muted">${a.started_at} · найдено ${a.found_count}</div></div>`).join('')}
async function devAudit(){document.getElementById('devResult').textContent=' выполняется…';const r=await fetch(api('/api/dev/audit'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'developer_full',reason:'ui'})});const d=await r.json();document.getElementById('devResult').textContent=` ${d.result}`;await refresh()}
async function devTreatmentTest(path){document.getElementById('devResult').textContent=' тест лечения…';const r=await fetch(api('/api/dev/test/'+path),{method:'POST'});const d=await r.json();const f=(d.findings||[])[0]||{};document.getElementById('devResult').textContent=` ${d.result} · ${f.treatment_result||'NO_RESULT'} · repeat=${f.repeat_diagnosis_state||'—'}`;await refresh()}
async function devRecurrenceTest(){document.getElementById('devResult').textContent=' тест recurrence…';const r=await fetch(api('/api/dev/test/recurrence'),{method:'POST'});const d=await r.json();document.getElementById('devResult').textContent=` recurrence ${d.result}`;await refresh()}
async function devFailedTreatmentTest(){document.getElementById('devResult').textContent=' тест FAILED…';const r=await fetch(api('/api/dev/test/failed-recovery'),{method:'POST'});const d=await r.json();const f=(d.findings||[])[0]||{};document.getElementById('devResult').textContent=` FAILED test: ${f.treatment_result||d.result} · repeat=${f.repeat_diagnosis_state||'—'}`;await refresh()}
async function devTargetedTest(){document.getElementById('devResult').textContent=' тест targeted…';const r=await fetch(api('/api/dev/test/targeted'),{method:'POST'});const d=await r.json();const t=d.targeted||{};document.getElementById('devResult').textContent=` targeted ${d.result} · sources=${(t.collected_sources||[]).join(',')}`;await refresh()}
async function devProtocolTest(){document.getElementById('devResult').textContent=' тест protocol…';const r=await fetch(api('/api/dev/test/protocol'),{method:'POST'});const d=await r.json();document.getElementById('devResult').textContent=` protocol ${d.result} · telemetry=${d.telemetry_seq||'—'}`;await refresh()}
async function devReadonlyTest(){document.getElementById('devResult').textContent=' тест readonly…';const r=await fetch(api('/api/dev/test/filesystem-readonly'),{method:'POST'});const d=await r.json();const ok=(d.cases||[]).filter(x=>x.pass).length;document.getElementById('devResult').textContent=` readonly ${d.result} · ${ok}/${(d.cases||[]).length}`;await refresh()}

async function devTriggerTest(){document.getElementById('devResult').textContent=' тест triggers…';const r=await fetch(api('/api/dev/test/triggers'),{method:'POST'});const d=await r.json();const ok=(d.cases||[]).filter(x=>x.pass).length;document.getElementById('devResult').textContent=' triggers '+d.result+' · '+ok+'/'+(d.cases||[]).length;await refresh()}
async function devMountTest(){document.getElementById('devResult').textContent=' тест mount…';const r=await fetch(api('/api/dev/test/mount-recovery'),{method:'POST'});const d=await r.json();const ok=(d.cases||[]).filter(x=>x.pass).length;document.getElementById('devResult').textContent=' mount '+d.result+' · '+ok+'/'+(d.cases||[]).length;await refresh()}
async function devReleaseGate(){document.getElementById('devResult').textContent=' release gate…';const r=await fetch(api('/api/dev/test/release-gate'),{method:'POST'});const d=await r.json();const failed=Object.entries(d.checks||{}).filter(x=>!x[1]).map(x=>x[0]);document.getElementById('devResult').textContent=' gate '+d.result+(failed.length?' · fail='+failed.join(','):'');await refresh()}

refresh();setInterval(refresh,30000);
</script></main></body></html>'''


async def on_startup(app: web.Application) -> None:
    rt: Runtime = app["runtime"]
    guard = HealthGuard(
        rt.collect_fast,
        rt.collect_normal,
        rt.store_samples,
        rt.on_anomaly,
        rt.on_recovery,
        on_error=rt.record_background_error,
        on_success=rt.record_background_ok,
    )
    app["tasks"] = [
        asyncio.create_task(guard.run(), name="health_guard"),
        asyncio.create_task(rt.first_run(), name="first_run"),
        asyncio.create_task(rt.hourly_loop(), name="hourly"),
        asyncio.create_task(rt.daily_loop(), name="daily"),
        asyncio.create_task(rt.bridge_watch_loop(), name="bridge_watch"),
    ]


async def on_cleanup(app: web.Application) -> None:
    for task in app.get("tasks", []):
        task.cancel()
    await asyncio.gather(*app.get("tasks", []), return_exceptions=True)


async def ingress_dispatch(request: web.Request) -> web.Response:
    """Accept Home Assistant Ingress paths with an arbitrary prefix."""
    path = request.path.rstrip("/") or "/"
    if request.method == "GET":
        if path.endswith("/api/health"):
            return await api_health(request)
        if path.endswith("/api/dashboard"):
            return await api_dashboard(request)
        if path.endswith("/api/incidents"):
            return await api_incidents(request)
        if path.endswith("/api/settings"):
            return await api_settings(request)
    if request.method == "POST" and path.endswith("/api/dev/audit"):
        return await api_dev_audit(request)
    if request.method == "POST" and path.endswith("/api/dev/test/setup-retry"):
        return await api_dev_setup_retry_test(request)
    if request.method == "POST" and path.endswith("/api/dev/test/setup-error"):
        return await api_dev_setup_error_test(request)
    if request.method == "POST" and path.endswith("/api/dev/test/recurrence"):
        return await api_dev_recurrence_test(request)
    if request.method == "POST" and path.endswith("/api/dev/test/failed-recovery"):
        return await api_dev_failed_recovery_test(request)
    if request.method == "POST" and path.endswith("/api/dev/test/targeted"):
        return await api_dev_targeted_audit_test(request)
    if request.method == "GET" and path.endswith("/api/dev/protocols"):
        return await api_dev_protocol_inventory(request)
    if request.method == "POST" and path.endswith("/api/dev/test/protocol-pack"):
        return await api_dev_protocol_pack_diagnostics(request)
    if request.method == "POST" and path.endswith("/api/dev/test/protocol"):
        return await api_dev_protocol_selftest(request)
    if request.method == "POST" and path.endswith("/api/dev/test/filesystem-readonly"):
        return await api_dev_filesystem_readonly_regression_test(request)
    if request.method == "POST" and path.endswith("/api/dev/test/triggers"):
        return await api_dev_trigger_matching_test(request)
    if request.method == "POST" and path.endswith("/api/dev/test/mount-recovery"):
        return await api_dev_mount_recovery_test(request)
    if request.method == "POST" and path.endswith("/api/dev/test/release-gate"):
        return await api_dev_release_gate(request)
    if request.method == "POST" and path.endswith("/api/dev/test/persistence/prepare"):
        return await api_dev_persistence_prepare(request)
    if request.method == "POST" and path.endswith("/api/dev/test/persistence/check"):
        return await api_dev_persistence_check(request)
    return await ui_index(request)


def create_app() -> web.Application:
    app = web.Application()
    app["runtime"] = Runtime()
    app.router.add_get("/", ui_index)
    app.router.add_get("/api/health", api_health)
    app.router.add_get("/api/dashboard", api_dashboard)
    app.router.add_get("/api/incidents", api_incidents)
    app.router.add_get("/api/settings", api_settings)
    app.router.add_post("/api/dev/audit", api_dev_audit)
    app.router.add_post("/api/dev/test/setup-retry", api_dev_setup_retry_test)
    app.router.add_post("/api/dev/test/setup-error", api_dev_setup_error_test)
    app.router.add_post("/api/dev/test/recurrence", api_dev_recurrence_test)
    app.router.add_post("/api/dev/test/failed-recovery", api_dev_failed_recovery_test)
    app.router.add_post("/api/dev/test/targeted", api_dev_targeted_audit_test)
    app.router.add_get("/api/dev/protocols", api_dev_protocol_inventory)
    app.router.add_post("/api/dev/test/protocol-pack", api_dev_protocol_pack_diagnostics)
    app.router.add_post("/api/dev/test/protocol", api_dev_protocol_selftest)
    app.router.add_post(
        "/api/dev/test/filesystem-readonly",
        api_dev_filesystem_readonly_regression_test,
    )
    app.router.add_post("/api/dev/test/triggers", api_dev_trigger_matching_test)
    app.router.add_post("/api/dev/test/mount-recovery", api_dev_mount_recovery_test)
    app.router.add_post("/api/dev/test/release-gate", api_dev_release_gate)
    app.router.add_post("/api/dev/test/persistence/prepare", api_dev_persistence_prepare)
    app.router.add_post("/api/dev/test/persistence/check", api_dev_persistence_check)
    app.router.add_route("*", "/{tail:.*}", ingress_dispatch)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def run() -> None:
    print(f"Suzie Doctor HTTP server starting on 8099 | app={APP_VERSION} bridge={BRIDGE_VERSION}", flush=True)
    web.run_app(create_app(), host="0.0.0.0", port=8099)
