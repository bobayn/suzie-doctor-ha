from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp import web

from . import APP_VERSION, BRIDGE_VERSION, PROTOCOL_PACK_VERSION
from .audit import Auditor
from .bootstrap import bootstrap_bridge
from .db import Database
from .ha_api import HomeAssistantClient
from .health_guard import HealthGuard, MetricSample
from .local_metrics import LocalMetrics
from .options import Options, load_options
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
        self.auditor = Auditor(self.db, self.supervisor, self.ha)
        self.local_metrics = LocalMetrics()
        self.started_at = datetime.now(UTC).isoformat()
        self.last_health: dict[str, Any] = {}
        self.bootstrap_status: dict[str, Any] = {"status": "pending"}
        self.last_full_audit: dict[str, Any] | None = None
        self._audit_lock = asyncio.Lock()

    async def collect_fast(self) -> dict[str, Any]:
        local = self.local_metrics.snapshot()
        try:
            core = await self.supervisor.core_stats()
        except Exception:
            core = {}
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
        except Exception:
            host = {}
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
            except Exception:
                pass
            asyncio.create_task(self.run_audit("targeted", f"health_guard:{category}"))

    async def system_busy(self) -> tuple[bool, str | None]:
        try:
            snapshot = await self.ha.bridge_snapshot()
        except Exception:
            snapshot = None
        if isinstance(snapshot, dict):
            for entity in snapshot.get("backup_entities", []):
                state = str(entity.get("state") or "").lower()
                if state in {"create_backup", "creating_a_backup", "receive_backup", "receiving_a_backup", "restore_backup", "restoring_a_backup"}:
                    return True, f"backup:{state}"
        return False, None

    async def run_audit(self, audit_type: str, reason: str | None = None) -> dict[str, Any]:
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
        except Exception:
            pass

    async def bridge_watch_loop(self) -> None:
        while True:
            try:
                if not self.db.get_meta("bridge_attached_audit"):
                    snapshot = await self.ha.bridge_snapshot()
                    if isinstance(snapshot, dict):
                        result = await self.run_audit("full", "bridge_attached")
                        if result.get("result") != "BUSY":
                            self.db.set_meta("bridge_attached_audit", datetime.now(UTC).isoformat())
                else:
                    await asyncio.sleep(300)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(60)

    async def hourly_loop(self) -> None:
        await asyncio.sleep(60)
        while True:
            try:
                await self.run_audit("hourly", "scheduled")
                self.db.cleanup(self.options.retention_days)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
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
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
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
<div class="card" id="devCard"><b>Developer mode</b><p class="muted">Служебный ручной запуск полного аудита только для разработки.</p><button class="btn" onclick="devAudit()">Запустить полный аудит</button><span id="devResult"></span></div></section>
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
refresh();setInterval(refresh,30000);
</script></main></body></html>'''


async def on_startup(app: web.Application) -> None:
    rt: Runtime = app["runtime"]
    guard = HealthGuard(rt.collect_fast, rt.collect_normal, rt.store_samples, rt.on_anomaly)
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
    app.router.add_route("*", "/{tail:.*}", ingress_dispatch)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def run() -> None:
    print(f"Suzie Doctor HTTP server starting on 8099 | app={APP_VERSION} bridge={BRIDGE_VERSION}", flush=True)
    web.run_app(create_app(), host="0.0.0.0", port=8099)
