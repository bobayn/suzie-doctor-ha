"""Bounded Home Assistant HTTP SSL profile transition for a signed Field one-shot.

Only the Home Assistant native HTTP config transaction is used. The five-minute
native rollback is intentionally left intact until functional verification passes.
This module never persists certificate/key values or promotes an unknown change.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_ALLOWED_PROFILE = "intermediate_v6"
_ORIGINAL_PROFILE = "modern_v4"
_HTTP_FIELDS = (
    "server_host", "server_port", "ssl_certificate", "ssl_key",
    "ssl_peer_certificate", "ssl_profile", "cors_allowed_origins",
    "use_x_forwarded_for", "trusted_proxies", "use_x_frame_options",
    "ip_ban_enabled", "login_attempts_threshold",
)


class HTTPSSLProfileError(RuntimeError):
    pass


def _active_repair(repairs: Any) -> bool:
    return any(
        isinstance(issue, dict)
        and issue.get("domain") == "http"
        and issue.get("issue_id") == "ssl_profile_outdated"
        and issue.get("active", True) is not False
        and not issue.get("ignored")
        and not issue.get("dismissed_version")
        for issue in (repairs if isinstance(repairs, list) else [])
    )


def _safe_config(stable: dict[str, Any]) -> dict[str, Any]:
    # Never POST read-only metadata like created_at/error/error_message.
    return {key: stable[key] for key in _HTTP_FIELDS if key in stable}


def _pending_matches(stable: dict[str, Any], pending: dict[str, Any]) -> bool:
    old = _safe_config(stable)
    new = _safe_config(pending)
    return all(
        new.get(key) == ( _ALLOWED_PROFILE if key == "ssl_profile" else value)
        for key, value in old.items()
    ) and set(old) == set(new)


async def stage_http_ssl_profile(ha: Any, profile: str) -> dict[str, Any]:
    """Start a *native* reversible HA transaction. Do not confirm it here."""
    if profile != _ALLOWED_PROFILE:
        raise HTTPSSLProfileError("Only intermediate_v6 is authorized")
    repairs = await ha.list_repairs()
    state = await ha.ws_command("http/config")
    if not isinstance(state, dict) or not isinstance(state.get("stable"), dict):
        raise HTTPSSLProfileError("HTTP stable configuration unavailable")
    stable = state["stable"]
    if (
        state.get("pending") is not None
        or state.get("active_config_type") != "stable"
    ):
        raise HTTPSSLProfileError("Another HTTP configuration is pending")
    if stable.get("ssl_profile") == _ALLOWED_PROFILE:
        if _active_repair(repairs):
            raise HTTPSSLProfileError("Repair still active on target profile")
        return {"accepted": True, "already_stable": True}
    if stable.get("ssl_profile") != _ORIGINAL_PROFILE:
        raise HTTPSSLProfileError("Unexpected SSL profile; require diagnosis")
    if not _active_repair(repairs):
        raise HTTPSSLProfileError("Original HTTP Repair is not active")
    if not stable.get("ssl_certificate") or not stable.get("ssl_key"):
        raise HTTPSSLProfileError("HTTPS certificate/key configuration missing")
    config = _safe_config(stable)
    config["ssl_profile"] = profile
    response = await ha.ws_command("http/config/configure", config=config)
    if not isinstance(response, dict) or response.get("restart") is not True:
        raise HTTPSSLProfileError("HA did not acknowledge restart-required change")
    return {"accepted": True, "restart_expected": True, "profile": profile}


async def verify_and_promote_http_ssl_profile(ha: Any, profile: str) -> dict[str, Any]:
    """Promote only our matching pending change after functional Repair clears.

    This is executed on reconnect from the SAME signed one-shot's verification
    path; no separate background automation may call it.
    """
    if profile != _ALLOWED_PROFILE:
        return {"verified": False, "reason": "unexpected_target_profile"}
    state = await ha.ws_command("http/config")
    if not isinstance(state, dict) or not isinstance(state.get("stable"), dict):
        return {"verified": False, "reason": "http_config_unavailable"}
    repairs = await ha.list_repairs()
    if _active_repair(repairs):
        return {"verified": False, "reason": "original_repair_still_active"}
    stable = state["stable"]
    pending = state.get("pending")
    if (
        state.get("active_config_type") == "stable"
        and stable.get("ssl_profile") == profile
        and pending is None
        and state.get("revert_at") is None
    ):
        return {"verified": True, "reason": "already_promoted"}
    if (
        state.get("active_config_type") != "pending"
        or not isinstance(pending, dict)
        or stable.get("ssl_profile") != _ORIGINAL_PROFILE
        or pending.get("ssl_profile") != profile
        or pending.get("error")
        or not _pending_matches(stable, pending)
    ):
        return {"verified": False, "reason": "pending_transaction_not_ours"}
    revert_at = state.get("revert_at")
    if not isinstance(revert_at, str):
        return {"verified": False, "reason": "rollback_deadline_missing"}
    try:
        deadline = datetime.fromisoformat(revert_at.replace("Z", "+00:00"))
        if deadline.tzinfo is None or deadline <= datetime.now(timezone.utc):
            return {"verified": False, "reason": "rollback_deadline_expired"}
    except ValueError:
        return {"verified": False, "reason": "rollback_deadline_invalid"}
    try:
        await ha.ws_command("http/config/promote")
    except Exception:
        # Stale UI/another safe confirmer may have promoted in parallel.
        # Never attempt the promote command a second time blindly.
        pass
    after = await ha.ws_command("http/config")
    if not isinstance(after, dict):
        return {"verified": False, "reason": "post_promote_config_unavailable"}
    final_repair = await ha.list_repairs()
    success = (
        after.get("active_config_type") == "stable"
        and isinstance(after.get("stable"), dict)
        and after["stable"].get("ssl_profile") == profile
        and after.get("pending") is None
        and after.get("revert_at") is None
        and not _active_repair(final_repair)
    )
    return {
        "verified": bool(success),
        "reason": "promoted" if success else "post_promote_verification_failed",
    }
