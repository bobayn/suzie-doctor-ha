from __future__ import annotations

import asyncio
import hashlib
import json
import ssl
import secrets
from pathlib import Path
from typing import Any, Literal

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations

ROOT = Path("/opt/suzie-doctor-mcp")
BUNDLE = ROOT / "bundle"
TOKEN_PATH = Path("/var/lib/suzie-doctor-server/keys/doctor_operator_token")
TLS_CERT = Path("/var/lib/suzie-doctor-server/tls/server_tls_cert.pem")
SERVER_URL = "https://192.168.0.105:8790"

skill_text = (BUNDLE / "suzie_doctor_skill/SKILL.md").read_text(encoding="utf-8")
skill_meta = json.loads(
    (BUNDLE / "suzie_doctor_skill/metadata.json").read_text(encoding="utf-8")
)
contract = json.loads(
    (BUNDLE / "suite/connector_contract.json").read_text(encoding="utf-8")
)
manifest = json.loads(
    (BUNDLE / "suite/manifest.json").read_text(encoding="utf-8")
)

actual_skill_hash = hashlib.sha256(skill_text.encode("utf-8")).hexdigest()
if actual_skill_hash != str(skill_meta.get("sha256") or ""):
    raise RuntimeError("canonical Skill bundle sha256 mismatch")
if contract.get("canonical_core") is not True:
    raise RuntimeError("Connector contract is not canonical")
if manifest.get("connector", {}).get("version") != contract.get("version"):
    raise RuntimeError("Suite/Connector version mismatch")
if manifest.get("skill", {}).get("version") != skill_meta.get("version"):
    raise RuntimeError("Suite/Skill version mismatch")

mcp = MCPServer(
    name="suzie-doctor",
    title="Suzie Doctor",
    description=(
        "Canonical Suzie Doctor Web/API adapter. Case ownership is held on "
        "Doctor Server; exact-client commands execute through the installed "
        "client Connector Core."
    ),
    instructions=(
        "When dispatched with CASE #N, load doctor.skill, inspect/claim that "
        "Case before any client action, use exact-client read-only diagnostics "
        "first, verify treatment, then call doctor.case.complete_next."
    ),
    version="0.1.7-dev",
)

CLAIM_HANDLES: dict[str, dict[str, Any]] = {}
HANDLE_LOCK = asyncio.Lock()


async def _active_handle(doctor_handle: str) -> dict[str, Any]:
    handle = str(doctor_handle or "").strip()
    if not handle:
        raise RuntimeError("DOCTOR_HANDLE_REQUIRED")
    async with HANDLE_LOCK:
        state = dict(CLAIM_HANDLES.get(handle) or {})
    if not state.get("case_id") or not state.get("claim_token"):
        raise RuntimeError("INVALID_OR_EXPIRED_DOCTOR_HANDLE")
    return state


async def _new_handle(state: dict[str, Any]) -> str:
    handle = "DH-" + secrets.token_urlsafe(24)
    async with HANDLE_LOCK:
        CLAIM_HANDLES[handle] = dict(state)
    return handle


async def _drop_handle(doctor_handle: str) -> None:
    async with HANDLE_LOCK:
        CLAIM_HANDLES.pop(str(doctor_handle or ""), None)


async def _api(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(
        verify=str(TLS_CERT),
        timeout=httpx.Timeout(20.0),
    ) as client:
        response = await client.request(
            method,
            SERVER_URL + path,
            headers=headers,
            json=body,
        )
    if response.status_code not in (200, 202):
        raise RuntimeError(
            f"Doctor Server {path} HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Doctor Server returned non-object JSON")
    return data


async def _heartbeat_state(state: dict[str, Any]) -> None:
    await _api(
        "POST",
        f"/v1/doctor/case/{int(state['case_id'])}/heartbeat",
        body={"claim_token": state["claim_token"]},
    )


async def _invoke_client(
    doctor_handle: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    wait_seconds: float = 90.0,
) -> dict[str, Any]:
    state = await _active_handle(doctor_handle)
    await _heartbeat_state(state)
    queued = await _api(
        "POST",
        f"/v1/doctor/case/{int(state['case_id'])}/tool",
        body={
            "claim_token": state["claim_token"],
            "tool_name": tool_name,
            "arguments": arguments,
        },
    )
    command = queued.get("command") or {}
    command_id = str(command.get("command_id") or "")
    if not command_id:
        raise RuntimeError("Doctor Server did not return command_id")

    deadline = asyncio.get_running_loop().time() + wait_seconds
    while asyncio.get_running_loop().time() < deadline:
        item = await _api("GET", f"/v1/doctor/command/{command_id}")
        current = item.get("command") or {}
        status = str(current.get("status") or "")
        if status == "COMPLETED":
            await _heartbeat_state(state)
            return {
                "command_id": command_id,
                "status": status,
                "result": current.get("result") or {},
            }
        if status == "FAILED":
            raise RuntimeError(
                f"CLIENT_COMMAND_FAILED {command_id}: {current.get('error')}"
            )
        await asyncio.sleep(0.35)

    return {
        "command_id": command_id,
        "status": "PENDING",
        "message": (
            "Command is still running or the client is unavailable. "
            "Do not reissue the same state-changing action blindly; use "
            "doctor.command.get."
        ),
    }


@mcp.tool(
    name="doctor.skill",
    description="Load the canonical versioned Suzie Doctor Skill Core.",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def doctor_skill() -> dict[str, Any]:
    return {
        "canonical": True,
        "metadata": skill_meta,
        "skill": skill_text,
    }


@mcp.tool(
    name="doctor.capabilities",
    description=(
        "Return Doctor Server orchestration capabilities. If a Case is already "
        "claimed in this MCP session, also return live exact-client Connector "
        "capabilities from that installed Doctor App."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def doctor_capabilities() -> dict[str, Any]:
    base = await _api("GET", "/v1/doctor/capabilities")
    base["canonical_bundle"] = {
        "connector_version": contract.get("version"),
        "skill_version": skill_meta.get("version"),
        "suite_version": manifest.get("suite_version"),
        "tools": [x.get("name") for x in contract.get("tools", [])],
    }
    base["field_actions"] = list(contract.get("field_actions") or [])
    base["doctor_handle"] = {
        "required_after_claim": True,
        "transport_session_independent": True,
        "contains_claim_token": False,
    }
    return base


@mcp.tool(
    name="doctor.queue",
    description="Read the shared Doctor Case queue and active doctor sessions.",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def doctor_queue() -> dict[str, Any]:
    data = await _api("GET", "/v1/doctor/queue")
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    data["open_cases"] = int(stats.get("open_cases") or 0)
    data["active_doctors"] = int(stats.get("active_doctors") or 0)
    data["waiting_for_suzie"] = int(stats.get("waiting_for_suzie") or 0)
    return data


@mcp.tool(
    name="doctor.case.get",
    description="Read one Case by numeric case_id before claiming or treating it.",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def doctor_case_get(case_id: int) -> dict[str, Any]:
    cid = int(case_id)
    if 9_000_000_000 < cid < 9_100_000_000:
        job_id = cid - 9_000_000_000
        data = await _api("GET", f"/internal/wilson/jobs/{job_id}")
        return {"ok": True, "compat_role": "WILSON", "compat_job_id": job_id, **data}
    if 8_000_000_000 < cid < 8_100_000_000:
        job_id = cid - 8_000_000_000
        data = await _api("GET", f"/internal/house/jobs/{job_id}")
        return {"ok": True, "compat_role": "HOUSE", "compat_job_id": job_id, **data}
    return await _api("GET", f"/v1/doctor/case/{cid}")


@mcp.tool(
    name="doctor.case.claim",
    description=(
        "Atomically claim one ASSIGNED Case for this Doctor session. "
        "A conflict means another doctor owns it and this session must stop."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def doctor_case_claim(case_id: int) -> dict[str, Any]:
    cid = int(case_id)
    existing_handle = ""
    existing_state: dict[str, Any] = {}
    async with HANDLE_LOCK:
        for handle, state in CLAIM_HANDLES.items():
            if int(state.get("case_id") or 0) == cid:
                existing_handle = str(handle)
                existing_state = dict(state)
                break
    if existing_handle:
        try:
            await _heartbeat_state(existing_state)
            data = await _api("GET", f"/v1/doctor/case/{cid}")
            case = dict(data.get("case") or {})
            case.pop("claim_token", None)
            return {
                "ok": True,
                "claimed": True,
                "resumed": True,
                "case": case,
                "doctor_handle": existing_handle,
                "claim_token_exposed": False,
            }
        except Exception:
            await _drop_handle(existing_handle)

    data = await _api(
        "POST",
        f"/v1/doctor/case/{cid}/claim",
        body={},
    )
    case = dict(data.get("case") or {})
    claim_token = str(case.pop("claim_token", "") or "")
    if not claim_token:
        raise RuntimeError("claim succeeded without transport claim token")
    handle = await _new_handle({
        "case_id": int(case_id),
        "claim_token": claim_token,
        "client_id": case.get("client_id"),
        "doctor_session_id": case.get("doctor_session_id"),
    })
    return {
        "ok": True,
        "claimed": True,
        "case": case,
        "doctor_handle": handle,
        "claim_token_exposed": False,
    }


@mcp.tool(
    name="doctor.case.heartbeat",
    description="Renew the active Case lease for this MCP Doctor session.",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def doctor_case_heartbeat(doctor_handle: str) -> dict[str, Any]:
    state = await _active_handle(doctor_handle)
    return await _api(
        "POST",
        f"/v1/doctor/case/{int(state['case_id'])}/heartbeat",
        body={"claim_token": state["claim_token"]},
    )


@mcp.tool(
    name="doctor.case.stage",
    description="Set the active Case stage to CLAIMED, TREATING, or VERIFYING.",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def doctor_case_stage(stage: str, doctor_handle: str) -> dict[str, Any]:
    state = await _active_handle(doctor_handle)
    return await _api(
        "POST",
        f"/v1/doctor/case/{int(state['case_id'])}/stage",
        body={
            "claim_token": state["claim_token"],
            "stage": str(stage).upper(),
        },
    )


@mcp.tool(
    name="doctor.case.complete_next",
    description=(
        "Atomically close the active Case after verification and check the "
        "shared journal for the next unassigned Suzie Case. Outcome must be one "
        "of SUCCESS, RESOLVED, HUMAN_REQUIRED, UNSAFE_TO_TREAT, FAILED. If one "
        "is returned, continue in the same real dialog and claim that new Case next."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def doctor_case_complete_next(
    outcome: Literal["SUCCESS", "RESOLVED", "HUMAN_REQUIRED", "UNSAFE_TO_TREAT", "FAILED"],
    result: dict[str, Any],
    doctor_handle: str,
) -> dict[str, Any]:
    handle = str(doctor_handle or "").strip()
    upper_outcome = str(outcome).upper()
    if handle.startswith("WILSON:"):
        job_id = int(handle.split(":", 1)[1])
        payload = dict(result or {})
        output_cursor = str(payload.pop("output_cursor", "") or "")
        return await _api(
            "POST",
            f"/internal/wilson/jobs/{job_id}/complete",
            body={
                "result": payload,
                "output_cursor": output_cursor,
                "failed": upper_outcome == "FAILED",
            },
        )
    if handle.startswith("HOUSE:"):
        job_id = int(handle.split(":", 1)[1])
        payload = dict(result or {})
        required = {"finding_class", "significance", "decision"}
        missing = sorted(required - set(payload))
        if missing:
            raise RuntimeError("HOUSE_COMPAT_RESULT_MISSING:" + ",".join(missing))
        raw_finding_class = str(payload.pop("finding_class")).upper()
        finding_class = raw_finding_class
        significance = str(payload.pop("significance")).upper()
        decision = str(payload.pop("decision")).upper()
        field_priority = str(payload.pop("field_priority", "NORMAL")).upper()
        allowed_finding_classes = {"EVENT", "OBSERVATION", "INCIDENT", "CASE"}
        if finding_class not in allowed_finding_classes:
            payload.setdefault("raw_finding_class", raw_finding_class)
            finding_class = (
                "OBSERVATION"
                if decision == "IGNORE_AS_NOISE"
                else "CASE"
            )
        if significance == "NONE":
            significance = "LOW"
        if field_priority == "NONE":
            field_priority = "LOW"
        if decision != "DISPATCH_SUZIE" and field_priority not in {"LOW","NORMAL","HIGH","URGENT"}:
            field_priority = "LOW"
        return await _api(
            "POST",
            f"/internal/house/jobs/{job_id}/decision",
            body={
                "finding_class": finding_class,
                "significance": significance,
                "decision": decision,
                "field_priority": field_priority,
                "result": payload,
            },
        )
    state = await _active_handle(handle)
    data = await _api(
        "POST",
        f"/v1/doctor/case/{int(state['case_id'])}/complete-next",
        body={
            "claim_token": state["claim_token"],
            "outcome": upper_outcome,
            "result": result,
        },
    )
    await _drop_handle(handle)
    return data


@mcp.tool(
    name="doctor.command.get",
    description=(
        "Read the state/result of one already-issued client command. "
        "Use after a PENDING result; do not blindly reissue writes."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def doctor_command_get(command_id: str, doctor_handle: str) -> dict[str, Any]:
    state = await _active_handle(doctor_handle)
    data = await _api("GET", f"/v1/doctor/command/{command_id}")
    command = data.get("command") or {}
    if int(command.get("case_id") or 0) != int(state["case_id"]):
        raise RuntimeError("command does not belong to active Case")
    return data


@mcp.tool(
    name="doctor.suite",
    description="Read the exact claimed client's live Suite compatibility status.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_suite(doctor_handle: str) -> dict[str, Any]:
    return await _invoke_client(doctor_handle, "doctor.suite", {}, wait_seconds=30)


@mcp.tool(
    name="ha.config.read",
    description="Read Home Assistant config from the exact client bound to the active Case.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def ha_config_read(doctor_handle: str) -> dict[str, Any]:
    return await _invoke_client(doctor_handle, "ha.config.read", {}, wait_seconds=30)


@mcp.tool(
    name="ha.repairs.list",
    description="List active Home Assistant Repairs on the exact active Case client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def ha_repairs_list(doctor_handle: str) -> dict[str, Any]:
    return await _invoke_client(doctor_handle, "ha.repairs.list", {}, wait_seconds=30)


@mcp.tool(
    name="ha.notifications.list",
    description="List persistent notifications on the exact active Case client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def ha_notifications_list(doctor_handle: str) -> dict[str, Any]:
    return await _invoke_client(doctor_handle, "ha.notifications.list", {}, wait_seconds=30)


@mcp.tool(
    name="ha.config_entries.list",
    description="List Home Assistant config entries on the exact active Case client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def ha_config_entries_list(doctor_handle: str, domain: str = "") -> dict[str, Any]:
    return await _invoke_client(
        doctor_handle,
        "ha.config_entries.list",
        {"domain": domain},
        wait_seconds=30,
    )


@mcp.tool(
    name="supervisor.info",
    description="Read Supervisor info from the exact active Case client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def supervisor_info(doctor_handle: str) -> dict[str, Any]:
    return await _invoke_client(doctor_handle, "supervisor.info", {}, wait_seconds=30)


@mcp.tool(
    name="supervisor.host.info",
    description="Read host info from the exact active Case client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def supervisor_host_info(doctor_handle: str) -> dict[str, Any]:
    return await _invoke_client(doctor_handle, "supervisor.host.info", {}, wait_seconds=30)


@mcp.tool(
    name="supervisor.core.info",
    description="Read HA Core info from the exact active Case client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def supervisor_core_info(doctor_handle: str) -> dict[str, Any]:
    return await _invoke_client(doctor_handle, "supervisor.core.info", {}, wait_seconds=30)


@mcp.tool(
    name="supervisor.network.info",
    description="Read network info from the exact active Case client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def supervisor_network_info(doctor_handle: str) -> dict[str, Any]:
    return await _invoke_client(doctor_handle, "supervisor.network.info", {}, wait_seconds=30)


@mcp.tool(
    name="supervisor.addons.list",
    description="List HA apps/add-ons on the exact active Case client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def supervisor_addons_list(doctor_handle: str) -> dict[str, Any]:
    return await _invoke_client(doctor_handle, "supervisor.addons.list", {}, wait_seconds=30)


@mcp.tool(
    name="supervisor.mounts.list",
    description="List Supervisor mounts on the exact active Case client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def supervisor_mounts_list(doctor_handle: str) -> dict[str, Any]:
    return await _invoke_client(doctor_handle, "supervisor.mounts.list", {}, wait_seconds=30)


@mcp.tool(
    name="supervisor.backups.list",
    description="List Supervisor backups on the exact active Case client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def supervisor_backups_list(doctor_handle: str) -> dict[str, Any]:
    return await _invoke_client(doctor_handle, "supervisor.backups.list", {}, wait_seconds=30)


@mcp.tool(
    name="doctor.v2.state",
    description="Read Doctor Server v2 House/Wilson/4+1+1/10x10 state.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_v2_state() -> dict[str, Any]:
    return await _api("GET", "/v2/state")


@mcp.tool(
    name="doctor.house.job.get",
    description="Read one claimed Doctor House job with Patient Card, matched Experimental 0/3-2/3 candidates, and recent Patient Journal events.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_house_job_get(job_id: int) -> dict[str, Any]:
    return await _api("GET", f"/internal/house/jobs/{int(job_id)}")


@mcp.tool(
    name="doctor.house.decision",
    description="Complete a claimed House job. Experimental validation dispatch uses DISPATCH_SUZIE plus result.house_directive=VALIDATE_FIRST, experimental_protocol_id and validation_stage.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
async def doctor_house_decision(
    job_id: int,
    finding_class: str,
    significance: str,
    decision: str,
    field_priority: str = "NORMAL",
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _api(
        "POST",
        f"/internal/house/jobs/{int(job_id)}/decision",
        body={
            "finding_class": str(finding_class),
            "significance": str(significance),
            "decision": str(decision),
            "field_priority": str(field_priority),
            "result": dict(result or {}),
        },
    )


@mcp.tool(
    name="doctor.wilson.job.get",
    description="Read one claimed Wilson hourly-review or nightly-research job.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_wilson_job_get(job_id: int) -> dict[str, Any]:
    return await _api("GET", f"/internal/wilson/jobs/{int(job_id)}")


@mcp.tool(
    name="doctor.wilson.complete",
    description="Complete a claimed Wilson job and commit its cursor only after successful knowledge work.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
async def doctor_wilson_complete(
    job_id: int,
    result: dict[str, Any],
    output_cursor: str = "",
    failed: bool = False,
) -> dict[str, Any]:
    return await _api(
        "POST",
        f"/internal/wilson/jobs/{int(job_id)}/complete",
        body={
            "result": dict(result or {}),
            "output_cursor": str(output_cursor or ""),
            "failed": bool(failed),
        },
    )


async def _field_action_request_via_core(
    *,
    doctor_handle: str,
    action: dict[str, Any] | str,
    exact_target: dict[str, Any],
    reason: str,
    evidence: dict[str, Any],
    risk_assessment: dict[str, Any],
    expected_result: str,
    verify_criterion: dict[str, Any],
    checkpoint: dict[str, Any] | None = None,
    rollback: list[dict[str, Any]] | None = None,
    fallback: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if isinstance(action, str):
        action = {"name": action.strip()}
    if not isinstance(action, dict) or not isinstance(exact_target, dict):
        raise RuntimeError("action must be a name string or object; exact_target must be an object")
    if not str(action.get("name") or action.get("primitive") or "").strip():
        raise RuntimeError("action requires name or primitive")
    if not isinstance(evidence, dict) or not isinstance(risk_assessment, dict):
        raise RuntimeError("evidence and risk_assessment must be objects")
    if not isinstance(verify_criterion, dict):
        raise RuntimeError("verify_criterion must be an object")
    state = await _active_handle(doctor_handle)
    await _api(
        "POST",
        f"/v1/doctor/case/{int(state['case_id'])}/stage",
        body={"claim_token": state["claim_token"], "stage": "TREATING"},
    )
    args = {
        "action": dict(action),
        "exact_target": dict(exact_target),
        "reason": str(reason or ""),
        "evidence": dict(evidence),
        "risk_assessment": dict(risk_assessment),
        "expected_result": str(expected_result or ""),
        "verify_criterion": dict(verify_criterion),
        "checkpoint": dict(checkpoint) if isinstance(checkpoint, dict) else None,
        "rollback": list(rollback or []),
        "fallback": list(fallback or []),
    }
    return await _invoke_client(doctor_handle, "doctor.action.request", args, wait_seconds=120)


@mcp.tool(
    name="doctor.action.request",
    description=(
        "Field-Suzie only: request one exact-client, signed, one-shot structured action "
        "when no suitable Disease/Protocol treatment exists. Requires autonomous risk "
        "assessment and mandatory functional verify criterion. Family Doctor cannot use it."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
)
async def doctor_action_request(
    doctor_handle: str,
    action: dict[str, Any] | str,
    exact_target: dict[str, Any],
    reason: str,
    evidence: dict[str, Any],
    risk_assessment: dict[str, Any],
    expected_result: str,
    verify_criterion: dict[str, Any],
    checkpoint: dict[str, Any] | None = None,
    rollback: list[dict[str, Any]] | None = None,
    fallback: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return await _field_action_request_via_core(
        doctor_handle=doctor_handle, action=action, exact_target=exact_target,
        reason=reason, evidence=evidence, risk_assessment=risk_assessment,
        expected_result=expected_result, verify_criterion=verify_criterion,
        checkpoint=checkpoint, rollback=rollback, fallback=fallback,
    )


@mcp.tool(
    name="doctor.diagnose",
    description=(
        "Consult Doctor Server from the exact active Case client. execute=false "
        "is diagnostic. execute=true may enter signed treatment only with the "
        "structured autonomous risk_assessment made by Suzie Doctor. For House "
        "VALIDATE_FIRST Cases, pass experimental_protocol_id; the MCP binds the "
        "request to the active field_case_id and Field-only experimental route. "
        "The adapter transports the decision and does not make the risk judgment itself."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def doctor_diagnose(
    doctor_handle: str,
    evidence: dict[str, Any],
    execute: bool = False,
    risk_assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise RuntimeError("evidence must be an object")
    if execute and not isinstance(risk_assessment, dict):
        raise RuntimeError("execute=true requires Suzie Doctor risk_assessment")
    state = await _active_handle(doctor_handle)
    evidence = dict(evidence)
    compatibility_request = evidence.pop("field_action_request", None)
    if compatibility_request is not None:
        if not execute:
            raise RuntimeError("field_action_request compatibility transport requires execute=true")
        if not isinstance(risk_assessment, dict):
            raise RuntimeError("field_action_request compatibility transport requires risk_assessment")
        if not isinstance(compatibility_request, dict):
            raise RuntimeError("field_action_request must be an object")
        result = await _field_action_request_via_core(
            doctor_handle=doctor_handle,
            action=compatibility_request.get("action") or {},
            exact_target=dict(compatibility_request.get("exact_target") or {}),
            reason=str(compatibility_request.get("reason") or ""),
            evidence=dict(compatibility_request.get("evidence") or evidence),
            risk_assessment=dict(risk_assessment),
            expected_result=str(compatibility_request.get("expected_result") or ""),
            verify_criterion=dict(compatibility_request.get("verify_criterion") or {}),
            checkpoint=(dict(compatibility_request["checkpoint"]) if isinstance(compatibility_request.get("checkpoint"), dict) else None),
            rollback=list(compatibility_request.get("rollback") or []),
            fallback=list(compatibility_request.get("fallback") or []),
        )
        result = dict(result)
        result["compatibility_transport"] = "doctor.diagnose"
        result["canonical_tool"] = "doctor.action.request"
        return result

    evidence["field_case_id"] = int(state["case_id"])
    if evidence.get("experimental_protocol_id"):
        evidence["routing_intent"] = "FIELD_EXPERIMENTAL_VALIDATION"
    else:
        # A diagnostic NO_MATCH inside an already House-dispatched Field Case
        # must stay inside that Case.  It must never recursively create another
        # Suzie Case through the generic auto-escalation path.
        evidence["routing_intent"] = "FIELD_CASE_DIAGNOSTIC"
    if execute:
        await _api(
            "POST",
            f"/v1/doctor/case/{int(state['case_id'])}/stage",
            body={"claim_token": state["claim_token"], "stage": "TREATING"},
        )
    args: dict[str, Any] = {"evidence": evidence, "execute": bool(execute)}
    if isinstance(risk_assessment, dict):
        args["risk_assessment"] = dict(risk_assessment)
    return await _invoke_client(
        doctor_handle,
        "doctor.diagnose",
        args,
        wait_seconds=90,
    )


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8791,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=False,
        session_idle_timeout=1800,
        max_sessions=32,
    )
