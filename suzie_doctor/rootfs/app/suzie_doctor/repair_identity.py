from __future__ import annotations

import hashlib
import json
from typing import Any

# Only placeholders that identify the affected object are used for semantic
# Repair identity.  Descriptive/error/version placeholders are intentionally
# excluded so a changing message cannot create a new logical problem.
IDENTITY_PLACEHOLDER_KEYS = (
    "reference",
    "config_entry_id",
    "entry_id",
    "device_id",
    "entity_id",
    "addon",
    "slug",
    "repository",
    "integration",
    "mount",
    "name",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def repair_identity(
    *,
    domain: Any,
    issue_id: Any,
    translation_key: Any = None,
    translation_placeholders: Any = None,
) -> dict[str, Any]:
    domain_s = _text(domain)
    issue_s = _text(issue_id)
    key_s = _text(translation_key)
    placeholders = translation_placeholders if isinstance(translation_placeholders, dict) else {}
    identity_placeholders = {
        key: _text(placeholders.get(key))
        for key in IDENTITY_PLACEHOLDER_KEYS
        if _text(placeholders.get(key))
    }
    if domain_s and key_s and identity_placeholders:
        raw = json.dumps(
            {
                "domain": domain_s,
                "translation_key": key_s,
                "identity_placeholders": identity_placeholders,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
        problem_key = f"repair:{domain_s}:semantic:{key_s}:{digest}"
        mode = "semantic"
    else:
        problem_key = f"repair:{domain_s}:{issue_s}"
        mode = "issue_id"
    return {
        "problem_key": problem_key,
        "identity_mode": mode,
        "domain": domain_s,
        "issue_id": issue_s,
        "translation_key": key_s,
        "identity_placeholders": identity_placeholders,
    }


def repair_matches_identity(item: Any, identity: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    if _text(item.get("domain")) != _text(identity.get("domain")):
        return False
    if str(identity.get("identity_mode") or "issue_id") != "semantic":
        return _text(item.get("issue_id")) == _text(identity.get("issue_id"))
    if _text(item.get("translation_key")) != _text(identity.get("translation_key")):
        return False
    placeholders = item.get("translation_placeholders")
    placeholders = placeholders if isinstance(placeholders, dict) else {}
    expected = identity.get("identity_placeholders")
    expected = expected if isinstance(expected, dict) else {}
    return bool(expected) and all(_text(placeholders.get(k)) == _text(v) for k, v in expected.items())


def repair_verify_criterion(identity: dict[str, Any]) -> dict[str, Any]:
    out = {
        "type": "ha_repair_absent",
        "domain": _text(identity.get("domain")),
        "issue_id": _text(identity.get("issue_id")),
    }
    if str(identity.get("identity_mode") or "") == "semantic":
        out.update(
            {
                "identity_mode": "semantic",
                "translation_key": _text(identity.get("translation_key")),
                "identity_placeholders": dict(identity.get("identity_placeholders") or {}),
            }
        )
    return out
