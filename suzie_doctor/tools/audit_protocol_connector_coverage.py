#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

BLOCKER_CLASSIFICATION = {
    "EXACT_TARGET_CONTEXT_REQUIRED": "AI_ASSISTED_POSSIBLE",
    "HA_CONFIG_EDIT_REQUIRED": "AI_ASSISTED_POSSIBLE",
    "PARTIAL_MULTI_STEP": "AI_ASSISTED_POSSIBLE",
    "EXTERNAL_HOST_OR_CONTAINER_CONFIG": "CONNECTOR_ADAPTER_MISSING",
    "NETWORK_OR_DNS_CONFIG": "CONNECTOR_ADAPTER_MISSING",
    "PRODUCT_CONFIG_ADAPTER_REQUIRED": "CONNECTOR_ADAPTER_MISSING",
    "STORAGE_OR_DATABASE_RECOVERY": "CONNECTOR_ADAPTER_MISSING",
    "CREDENTIAL_OR_AUTH_FLOW": "HUMAN_REQUIRED",
    "HARDWARE_OR_RF_PHYSICAL": "HUMAN_REQUIRED",
    "HIGH_RISK_MANUAL_RECOVERY": "TRUE_HIGH_RISK_MANUAL",
    "NO_SAFE_DETERMINISTIC_MAPPING": "TRUE_HIGH_RISK_MANUAL",
}

BLOCKER_FAMILY = {
    "EXACT_TARGET_CONTEXT_REQUIRED": "context/exact-target",
    "HA_CONFIG_EDIT_REQUIRED": "ha",
    "PARTIAL_MULTI_STEP": "multi-adapter",
    "EXTERNAL_HOST_OR_CONTAINER_CONFIG": "docker/host",
    "NETWORK_OR_DNS_CONFIG": "network",
    "PRODUCT_CONFIG_ADAPTER_REQUIRED": "product-specific",
    "STORAGE_OR_DATABASE_RECOVERY": "storage/recorder",
    "CREDENTIAL_OR_AUTH_FLOW": "auth",
    "HARDWARE_OR_RF_PHYSICAL": "human/hardware",
    "HIGH_RISK_MANUAL_RECOVERY": "human/high-risk",
    "NO_SAFE_DETERMINISTIC_MAPPING": "none-safe-yet",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def protocol_records(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if not isinstance(raw, dict):
        raise ValueError("catalog must be a JSON object or array")
    for key in ("protocols", "records", "generated_protocols", "items"):
        value = raw.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    if raw and all(isinstance(v, dict) for v in raw.values()):
        return [dict(v) for v in raw.values()]
    raise ValueError("cannot locate protocol list in catalog")


def card_from_record(record: dict[str, Any]) -> dict[str, Any]:
    for key in ("card", "protocol_card", "generated_card"):
        value = record.get(key)
        if isinstance(value, dict):
            return value
    return record


def protocol_id(record: dict[str, Any], card: dict[str, Any]) -> str:
    protocol = card.get("protocol")
    if isinstance(protocol, dict) and protocol.get("id"):
        return str(protocol["id"])
    for source in (record, card):
        for key in ("protocol_id", "id"):
            if source.get(key):
                return str(source[key])
    return "UNKNOWN_PROTOCOL"


def status_of(record: dict[str, Any], card: dict[str, Any]) -> str:
    protocol = card.get("protocol")
    if isinstance(protocol, dict) and protocol.get("status"):
        return str(protocol["status"]).upper()
    for source in (record, card):
        if source.get("status"):
            return str(source["status"]).upper()
    factory = card.get("factory") if isinstance(card.get("factory"), dict) else {}
    state = str(factory.get("state") or record.get("factory_state") or "").upper()
    if state.startswith("ACTIVE"):
        return "ACTIVE"
    if state.startswith("WATCH"):
        return "WATCH"
    if state.startswith("MANUAL"):
        return "MANUAL"
    if "AWAITING" in state or "SUSPENDED" in state:
        return "SUSPENDED"
    return "UNKNOWN"


def factory_state_of(record: dict[str, Any], card: dict[str, Any]) -> str:
    factory = card.get("factory") if isinstance(card.get("factory"), dict) else {}
    return str(
        factory.get("state")
        or record.get("factory_state")
        or record.get("state")
        or ""
    )


def blocker_of(record: dict[str, Any], card: dict[str, Any]) -> str:
    factory = card.get("factory") if isinstance(card.get("factory"), dict) else {}
    for source in (record, factory, card):
        for key in ("blocker_class", "manual_blocker_class", "blocker"):
            value = source.get(key)
            if isinstance(value, str) and value:
                return value.upper()
    return ""


def collect_primitives(value: Any) -> set[str]:
    result: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            primitive = node.get("primitive")
            if isinstance(primitive, str) and primitive:
                result.add(primitive)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return result


def has_nonempty(card: dict[str, Any], key: str) -> bool:
    value = card.get(key)
    if isinstance(value, list):
        return bool(value)
    if isinstance(value, dict):
        return bool(value)
    return value not in (None, "", False)


def manual_classification(blocker: str) -> str:
    return BLOCKER_CLASSIFICATION.get(
        blocker,
        "MANUAL_REVIEW_REQUIRED",
    )


def required_connector_families(
    blocker: str,
    primitives: set[str],
) -> list[str]:
    families: set[str] = set()
    if blocker in BLOCKER_FAMILY:
        families.add(BLOCKER_FAMILY[blocker])
    for primitive in primitives:
        if primitive.startswith(("config_entry_", "entity_registry_", "set_entity_")):
            families.add("ha")
        elif primitive in {
            "addon_info",
            "restart_addon",
            "create_backup",
            "network_primary_info",
            "network_set_primary_dns",
            "read_host_metrics",
            "check_config",
            "restart_core",
        }:
            families.add("supervisor")
        elif primitive == "mqtt_probe":
            families.add("mqtt")
        elif primitive in {"install_update", "update_state"}:
            families.add("ha/supervisor")
    return sorted(families)


def audit(
    catalog: Any,
    contract: dict[str, Any],
) -> dict[str, Any]:
    records = protocol_records(catalog)
    adapters = {
        str(item.get("family") or ""): bool(item.get("available"))
        for item in contract.get("adapters") or []
        if isinstance(item, dict)
    }
    rows: list[dict[str, Any]] = []
    classifications: Counter[str] = Counter()
    statuses: Counter[str] = Counter()

    for record in records:
        card = card_from_record(record)
        status = status_of(record, card)
        factory_state = factory_state_of(record, card)
        blocker = blocker_of(record, card)
        primitives = collect_primitives(card)
        families = required_connector_families(blocker, primitives)
        unavailable = [
            family
            for family in families
            if family in adapters and not adapters[family]
        ]

        if status == "ACTIVE":
            classification = "ACTIVE_CURRENT"
        elif status == "MANUAL":
            classification = manual_classification(blocker)
        elif status == "WATCH":
            classification = "WATCH_REVIEW_REQUIRED"
        elif status == "SUSPENDED":
            classification = "REVIEW_GATE_REQUIRED"
        else:
            classification = "UNKNOWN_REVIEW_REQUIRED"

        rows.append(
            {
                "protocol_id": protocol_id(record, card),
                "disease_id": str(card.get("disease_id") or record.get("disease_id") or ""),
                "status": status,
                "factory_state": factory_state,
                "blocker_class": blocker,
                "classification": classification,
                "required_primitives": sorted(primitives),
                "required_connector_families": families,
                "unavailable_connector_families": unavailable,
                "full_treatment_coverage": status == "ACTIVE" and not unavailable,
                "checkpoint_coverage": has_nonempty(card, "checkpoint"),
                "rollback_coverage": has_nonempty(card, "rollback"),
                "verify_coverage": has_nonempty(card, "verify"),
                "human_step_required": classification == "HUMAN_REQUIRED",
                "credential_required": blocker == "CREDENTIAL_OR_AUTH_FLOW",
                "risk_class": str(
                    card.get("automation_class")
                    or record.get("automation_class")
                    or blocker
                    or ""
                ),
            }
        )
        statuses[status] += 1
        classifications[classification] += 1

    return {
        "protocol_count": len(rows),
        "status_counts": dict(sorted(statuses.items())),
        "classification_counts": dict(sorted(classifications.items())),
        "rows": rows,
        "note": (
            "This is an analytical coverage report only. It does not change "
            "persisted Protocol status or publish treatment."
        ),
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Suzie Doctor Protocol Connector Coverage",
        "",
        f"Protocols audited: {report['protocol_count']}",
        "",
        "## Status counts",
        "",
    ]
    for key, value in report["status_counts"].items():
        lines.append(f"- {key}: {value}")
    lines += ["", "## Analytical classifications", ""]
    for key, value in report["classification_counts"].items():
        lines.append(f"- {key}: {value}")
    lines += [
        "",
        "## Per-protocol rows",
        "",
        "| Protocol | Status | Classification | Blocker | Families | Full treatment | Human | Credential |",
        "|---|---|---|---|---|---:|---:|---:|",
    ]
    for row in report["rows"]:
        lines.append(
            "| {protocol_id} | {status} | {classification} | {blocker_class} | "
            "{families} | {full} | {human} | {credential} |".format(
                protocol_id=row["protocol_id"],
                status=row["status"],
                classification=row["classification"],
                blocker_class=row["blocker_class"] or "-",
                families=", ".join(row["required_connector_families"]) or "-",
                full="yes" if row["full_treatment_coverage"] else "no",
                human="yes" if row["human_step_required"] else "no",
                credential="yes" if row["credential_required"] else "no",
            )
        )
    lines += ["", report["note"], ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    args = parser.parse_args()

    report = audit(load_json(args.catalog), load_json(args.contract))
    if args.json_out:
        args.json_out.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    if args.md_out:
        args.md_out.write_text(markdown(report), encoding="utf-8")
    if not args.json_out and not args.md_out:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
