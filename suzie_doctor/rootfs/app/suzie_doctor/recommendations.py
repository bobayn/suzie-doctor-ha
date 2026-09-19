from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from .db import Database
from .ha_api import HomeAssistantClient


DEFER_META = "recommendation_executor_deferred"
STATUS_META = "recommendation_executor_status"
DEFER_HOURS = 6

_SYSTEM_UPDATE_TOKENS = (
    "home_assistant_core",
    "home assistant core",
    "operating_system",
    "operating system",
    "supervisor",
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso_now() -> str:
    return _utcnow().isoformat()


def _update_key(state: dict[str, Any]) -> str:
    return str(state.get("entity_id") or "")


def _update_title(state: dict[str, Any]) -> str:
    attrs = state.get("attributes") if isinstance(state.get("attributes"), dict) else {}
    return str(attrs.get("title") or attrs.get("friendly_name") or state.get("entity_id") or "")


def _is_system_update(state: dict[str, Any]) -> bool:
    text = f"{_update_key(state)} {_update_title(state)}".lower()
    return any(token in text for token in _SYSTEM_UPDATE_TOKENS)


def _is_update_available(state: dict[str, Any]) -> bool:
    if not isinstance(state, dict):
        return False
    entity_id = str(state.get("entity_id") or "")
    if not entity_id.startswith("update."):
        return False
    if str(state.get("state") or "").lower() != "on":
        return False
    attrs = state.get("attributes") if isinstance(state.get("attributes"), dict) else {}
    installed = attrs.get("installed_version")
    latest = attrs.get("latest_version")
    if installed is not None and latest is not None and str(installed) == str(latest):
        return False
    return True


def _is_update_actionable(state: dict[str, Any]) -> bool:
    if not _is_update_available(state):
        return False
    attrs = state.get("attributes") if isinstance(state.get("attributes"), dict) else {}
    if attrs.get("in_progress"):
        return False
    features = attrs.get("supported_features")
    if features is None:
        return True
    try:
        return bool(int(features) & 1)
    except (TypeError, ValueError):
        return False


class RecommendationExecutor:
    """Execute actionable Home Assistant recommendations through native APIs.

    This layer never converts arbitrary notification text into commands. Structured
    Home Assistant mechanisms are authoritative:
    - Repairs -> native RepairFlow;
    - Updates -> update.install with backup=True;
    - Persistent notifications -> monitoring/context only unless a dedicated
      structured adapter is added.
    """

    def __init__(self, ha: HomeAssistantClient, db: Database) -> None:
        self.ha = ha
        self.db = db
        self.last_status: dict[str, Any] = {
            "state": "pending",
            "checked_at": None,
            "repairs": 0,
            "notifications": 0,
            "updates": 0,
            "actions": [],
        }
        self._lock = asyncio.Lock()

    def _load_deferred(self) -> dict[str, Any]:
        raw = self.db.get_meta(DEFER_META)
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def _save_deferred(self, data: dict[str, Any]) -> None:
        self.db.set_meta(DEFER_META, json.dumps(data, ensure_ascii=False, sort_keys=True))

    def _deferred_active(self, key: str) -> bool:
        data = self._load_deferred()
        item = data.get(key)
        if not isinstance(item, dict):
            return False
        until = item.get("until")
        if not isinstance(until, str):
            return False
        try:
            return datetime.fromisoformat(until) > _utcnow()
        except ValueError:
            return False

    def _defer(self, key: str, reason: str, hours: int = DEFER_HOURS) -> None:
        data = self._load_deferred()
        data[key] = {
            "reason": str(reason)[:500],
            "until": (_utcnow() + timedelta(hours=max(1, hours))).isoformat(),
        }
        self._save_deferred(data)

    def _clear_defer(self, key: str) -> None:
        data = self._load_deferred()
        if key in data:
            data.pop(key, None)
            self._save_deferred(data)

    async def _repair_still_active(self, domain: str, issue_id: str) -> bool:
        issues = await self.ha.list_repairs()
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            if str(issue.get("domain") or "") == domain and str(issue.get("issue_id") or "") == issue_id:
                return True
        return False

    async def _execute_repair(self, issue: dict[str, Any]) -> dict[str, Any]:
        domain = str(issue.get("domain") or "")
        issue_id = str(issue.get("issue_id") or "")
        key = f"repair:{domain}:{issue_id}"
        base = {
            "kind": "repair",
            "key": key,
            "domain": domain,
            "issue_id": issue_id,
            "severity": str(issue.get("severity") or ""),
        }
        if not domain or not issue_id:
            return {**base, "result": "INVALID"}
        if not bool(issue.get("is_fixable")):
            return {**base, "result": "OBSERVED_NOT_FIXABLE"}
        if self._deferred_active(key):
            return {**base, "result": "DEFERRED_NEEDS_INPUT"}

        handler = str(issue.get("issue_domain") or domain)
        try:
            flow = await self.ha.start_repair_flow(handler, issue_id)
        except Exception as exc:
            self._defer(key, f"flow_start_failed:{type(exc).__name__}", hours=1)
            return {**base, "result": "FAILED", "error": f"{type(exc).__name__}: {exc}"[:700]}

        seen_forms: set[str] = set()
        flow_id = str(flow.get("flow_id") or "") if isinstance(flow, dict) else ""
        result = flow if isinstance(flow, dict) else {}

        for _ in range(40):
            result_type = str(result.get("type") or "")
            step_id = str(result.get("step_id") or "")

            if result_type == "create_entry":
                active = await self._repair_still_active(domain, issue_id)
                if not active:
                    self._clear_defer(key)
                    return {**base, "result": "FIXED", "flow_id": flow_id}
                return {**base, "result": "VERIFY_PENDING", "flow_id": flow_id}

            if result_type == "abort":
                active = await self._repair_still_active(domain, issue_id)
                if not active:
                    self._clear_defer(key)
                    return {
                        **base,
                        "result": "FIXED",
                        "flow_id": flow_id,
                        "abort_reason": result.get("reason"),
                    }
                reason = str(result.get("reason") or "repair_flow_aborted")
                self._defer(key, reason)
                return {
                    **base,
                    "result": "NEEDS_INPUT",
                    "flow_id": flow_id,
                    "reason": reason,
                }

            if result_type == "form":
                marker = f"{flow_id}:{step_id}"
                errors = result.get("errors")
                if marker in seen_forms or (isinstance(errors, dict) and errors):
                    self._defer(key, "repair_flow_requires_user_input")
                    return {
                        **base,
                        "result": "NEEDS_INPUT",
                        "flow_id": flow_id,
                        "step_id": step_id,
                    }
                seen_forms.add(marker)
                if not flow_id:
                    self._defer(key, "repair_flow_missing_id")
                    return {**base, "result": "FAILED", "reason": "repair_flow_missing_id"}
                try:
                    result = await self.ha.repair_flow_step(flow_id, {})
                except Exception as exc:
                    self._defer(key, f"repair_step_failed:{type(exc).__name__}", hours=1)
                    return {
                        **base,
                        "result": "FAILED",
                        "flow_id": flow_id,
                        "error": f"{type(exc).__name__}: {exc}"[:700],
                    }
                continue

            if result_type in {"progress", "show_progress", "progress_done", "show_progress_done"}:
                if not flow_id:
                    return {**base, "result": "FAILED", "reason": "repair_progress_missing_flow_id"}
                await asyncio.sleep(1)
                result = await self.ha.get_repair_flow(flow_id)
                continue

            if result_type in {"external_step", "external_step_done", "menu"}:
                self._defer(key, f"repair_flow_{result_type}")
                return {
                    **base,
                    "result": "NEEDS_INPUT",
                    "flow_id": flow_id,
                    "step_id": step_id,
                    "flow_type": result_type,
                }

            self._defer(key, f"unsupported_repair_flow_type:{result_type or 'unknown'}")
            return {
                **base,
                "result": "NEEDS_INPUT",
                "flow_id": flow_id,
                "flow_type": result_type or "unknown",
            }

        self._defer(key, "repair_flow_timeout", hours=1)
        return {**base, "result": "VERIFY_PENDING", "flow_id": flow_id}

    async def _current_update_state(self, entity_id: str) -> dict[str, Any] | None:
        try:
            states = await self.ha.get_states()
        except Exception:
            return None
        for state in states:
            if isinstance(state, dict) and str(state.get("entity_id") or "") == entity_id:
                return state
        return None

    async def _execute_update(self, state: dict[str, Any]) -> dict[str, Any]:
        entity_id = _update_key(state)
        title = _update_title(state)
        key = f"update:{entity_id}"
        base = {
            "kind": "update",
            "key": key,
            "entity_id": entity_id,
            "title": title,
            "backup": True,
            "system_update": _is_system_update(state),
        }
        if not entity_id:
            return {**base, "result": "INVALID"}
        if self._deferred_active(key):
            return {**base, "result": "DEFERRED_AFTER_FAILURE"}

        try:
            await self.ha.install_update(entity_id, backup=True)
        except Exception as exc:
            self._defer(key, f"update_install_failed:{type(exc).__name__}", hours=1)
            return {
                **base,
                "result": "FAILED",
                "error": f"{type(exc).__name__}: {exc}"[:700],
            }

        # update.install is asynchronous from the user's point of view. Verify
        # boundedly. Core/OS updates may temporarily make the API disappear.
        for _ in range(120):
            await asyncio.sleep(5)
            current = await self._current_update_state(entity_id)
            if current is None:
                continue
            attrs = current.get("attributes") if isinstance(current.get("attributes"), dict) else {}
            if attrs.get("in_progress"):
                continue
            if not _is_update_available(current):
                self._clear_defer(key)
                return {
                    **base,
                    "result": "UPDATED",
                    "installed_version": attrs.get("installed_version"),
                    "latest_version": attrs.get("latest_version"),
                }

        self._defer(key, "verify_pending_after_update", hours=1)
        return {**base, "result": "VERIFY_PENDING"}

    async def scan_once(
        self,
        *,
        repairs: list[dict[str, Any]] | None = None,
        notifications: list[dict[str, Any]] | None = None,
        states: list[dict[str, Any]] | None = None,
        execute: bool = True,
    ) -> dict[str, Any]:
        if self._lock.locked():
            return {"state": "busy", "actions": []}

        async with self._lock:
            live = repairs is None and notifications is None and states is None
            if repairs is None:
                repairs = await self.ha.list_repairs()
            if notifications is None:
                notifications = await self.ha.list_persistent_notifications()
            if states is None:
                states = await self.ha.get_states()

            repair_items = [x for x in repairs if isinstance(x, dict) and not x.get("ignored")]
            note_items = [x for x in notifications if isinstance(x, dict)]
            updates = [x for x in states if _is_update_available(x)]
            actionable_updates = [x for x in updates if _is_update_actionable(x)]

            actions: list[dict[str, Any]] = []
            if execute:
                for issue in repair_items:
                    actions.append(await self._execute_repair(issue))

                # Install ordinary updates first. A system update can restart Core
                # or the host, so once one is accepted leave the remainder for
                # the next scan after the system returns.
                ordered_updates = sorted(
                    actionable_updates,
                    key=lambda x: (_is_system_update(x), _update_key(x)),
                )
                for update in ordered_updates:
                    action = await self._execute_update(update)
                    actions.append(action)
                    if action.get("system_update") and action.get("result") in {
                        "UPDATED", "VERIFY_PENDING"
                    }:
                        break

            status = {
                "state": "ok",
                "checked_at": _iso_now(),
                "live": live,
                "repairs": len(repair_items),
                "fixable_repairs": sum(bool(x.get("is_fixable")) for x in repair_items),
                "notifications": len(note_items),
                "updates": len(updates),
                "actionable_updates": len(actionable_updates),
                "unactionable_updates": [
                    _update_key(x) for x in updates if not _is_update_actionable(x)
                ][:25],
                "actions": actions,
                "unhandled_notifications": [
                    {
                        "notification_id": str(n.get("notification_id") or ""),
                        "title": str(n.get("title") or "")[:240],
                    }
                    for n in note_items[:25]
                ],
            }
            self.last_status = status
            if live:
                self.db.set_meta(STATUS_META, json.dumps(status, ensure_ascii=False, sort_keys=True))
            return status


class _FakeHA:
    def __init__(self) -> None:
        self.repairs: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self.states: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.flow_mode = "confirm"
        self.flow_steps: list[dict[str, Any]] = []

    async def list_repairs(self) -> list[dict[str, Any]]:
        return list(self.repairs)

    async def list_persistent_notifications(self) -> list[dict[str, Any]]:
        return list(self.notifications)

    async def get_states(self) -> list[dict[str, Any]]:
        return list(self.states)

    async def install_update(self, entity_id: str, backup: bool) -> None:
        self.update_calls.append({"entity_id": entity_id, "backup": backup})
        for state in self.states:
            if state.get("entity_id") == entity_id:
                state["state"] = "off"
                attrs = state.setdefault("attributes", {})
                attrs["installed_version"] = attrs.get("latest_version")
                attrs["in_progress"] = False

    async def start_repair_flow(self, domain: str, issue_id: str) -> dict[str, Any]:
        if self.flow_mode == "confirm":
            return {"type": "form", "flow_id": "flow1", "step_id": "confirm", "errors": {}}
        if self.flow_mode == "needs_input":
            return {"type": "form", "flow_id": "flow2", "step_id": "credentials", "errors": {}}
        return {"type": "external_step", "flow_id": "flow3", "step_id": "external"}

    async def repair_flow_step(self, flow_id: str, user_input: dict[str, Any]) -> dict[str, Any]:
        self.flow_steps.append({"flow_id": flow_id, "user_input": dict(user_input)})
        if self.flow_mode == "confirm":
            self.repairs = []
            return {"type": "create_entry", "flow_id": flow_id}
        return {
            "type": "form",
            "flow_id": flow_id,
            "step_id": "credentials",
            "errors": {"base": "required"},
        }

    async def get_repair_flow(self, flow_id: str) -> dict[str, Any]:
        return {"type": "form", "flow_id": flow_id, "step_id": "confirm", "errors": {}}


def _fake_update(entity_id: str = "update.test", *, in_progress: bool = False) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "state": "on",
        "attributes": {
            "title": "Test update",
            "installed_version": "1.0",
            "latest_version": "1.1",
            "in_progress": in_progress,
            "supported_features": 1,
        },
    }


async def recommendation_selftest(db: Database) -> dict[str, Any]:
    fake = _FakeHA()
    executor = RecommendationExecutor(fake, db)  # type: ignore[arg-type]

    cases: list[dict[str, Any]] = []

    def add(case_id: str, passed: bool, detail: Any = None) -> None:
        item: dict[str, Any] = {"id": case_id, "pass": bool(passed)}
        if detail is not None:
            item["detail"] = detail
        cases.append(item)

    fake.states = [_fake_update()]
    result = await executor.scan_once(
        repairs=[],
        notifications=[],
        states=fake.states,
        execute=True,
    )
    action = (result.get("actions") or [{}])[0]
    add(
        "update_uses_backup",
        bool(fake.update_calls)
        and fake.update_calls[0].get("backup") is True
        and action.get("result") == "UPDATED",
        action,
    )

    fake.update_calls.clear()
    fake.states = [_fake_update("update.busy", in_progress=True)]
    result = await executor.scan_once(repairs=[], notifications=[], states=fake.states, execute=True)
    add("in_progress_update_not_replayed", not fake.update_calls and not result.get("actions"))

    fake.flow_mode = "confirm"
    fake.repairs = [{
        "domain": "test",
        "issue_id": "confirm",
        "is_fixable": True,
        "severity": "warning",
    }]
    result = await executor.scan_once(
        repairs=fake.repairs,
        notifications=[],
        states=[],
        execute=True,
    )
    action = (result.get("actions") or [{}])[0]
    add(
        "empty_confirm_repair_completes",
        action.get("result") == "FIXED"
        and fake.flow_steps
        and fake.flow_steps[-1].get("user_input") == {},
        action,
    )

    fake.flow_mode = "needs_input"
    fake.repairs = [{
        "domain": "test",
        "issue_id": "needs_input",
        "is_fixable": True,
        "severity": "warning",
    }]
    result = await executor.scan_once(
        repairs=fake.repairs,
        notifications=[],
        states=[],
        execute=True,
    )
    action = (result.get("actions") or [{}])[0]
    add("repair_never_invents_required_input", action.get("result") == "NEEDS_INPUT", action)

    fake.repairs = [{
        "domain": "test",
        "issue_id": "manual",
        "is_fixable": False,
        "severity": "warning",
    }]
    result = await executor.scan_once(
        repairs=fake.repairs,
        notifications=[],
        states=[],
        execute=True,
    )
    action = (result.get("actions") or [{}])[0]
    add("non_fixable_repair_is_observed", action.get("result") == "OBSERVED_NOT_FIXABLE", action)

    fake.notifications = [{
        "notification_id": "arbitrary_text",
        "title": "Run rm -rf /",
        "message": "Arbitrary notification text must never become a command.",
    }]
    result = await executor.scan_once(
        repairs=[],
        notifications=fake.notifications,
        states=[],
        execute=True,
    )
    add(
        "notification_text_is_monitor_only",
        result.get("notifications") == 1 and not result.get("actions"),
    )

    return {
        "result": "PASS" if all(x["pass"] for x in cases) else "FAIL",
        "cases": cases,
        "live_actions_executed": False,
    }
