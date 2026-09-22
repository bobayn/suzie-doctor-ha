from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

NORMALIZED = Path("/var/lib/suzie-doctor-server/knowledge/normalized_knowledge.json")
OUTPUT = Path("/var/lib/suzie-doctor-server/knowledge/generated_protocols.json")
APPROVALS = Path("/var/lib/suzie-doctor-server/knowledge/protocol_factory_approvals.json")

FACTORY_VERSION = "0.1.0"
ALLOWED_PRIMITIVES = {
    "addon_info",
    "check_config",
    "config_entry_info",
    "config_entry_set_enabled",
    "config_entry_state",
    "confirmed_disease",
    "context_value",
    "context_list",
    "core_memory_stability",
    "entity_registry_info",
    "ensure_update_current",
    "create_backup",
    "google_assistant_set_exposed",
    "install_update",
    "install_hacs_supported",
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
SAFE_CONTEXT_DANGEROUS_OVERRIDES = {
    "PROTOCOL-KB-CANDIDATE-F1AFF02F2A",
    "RCP-DNS-HAOS-001",
    "RCP-BOOT-DNS-DEPENDENCY-001",
}

DANGEROUS_ACTION_HINTS = (
    "format",
    "factory reset",
    "reset nvm",
    "erase",
    "delete database",
    "remove database",
    "re-pair",
    "recommission",
    "re-commission",
    "non-secure inclusion",
    "disable security",
    "firewall",
    "resolver",
    "dns server",
    "mount usb bus",
    "passthrough",
    "serial/uart",
)

PRODUCT_SELECTORS = (
    (re.compile(r"\bzigbee2mqtt\b|\bz2m\b", re.I), "zigbee2mqtt"),
    (re.compile(r"\bz[- ]?wave js(?: ui)?\b|\bzwave js(?: ui)?\b", re.I), "z-wave js"),
    (re.compile(r"\bfrigate\b", re.I), "frigate"),
    (re.compile(r"\besphome\b", re.I), "esphome"),
    (re.compile(r"\bhacs\b", re.I), "hacs"),
    (re.compile(r"\bmosquitto\b", re.I), "mosquitto"),
    (re.compile(r"\bnode[- ]?red\b|\bnodered\b", re.I), "node-red"),
    (re.compile(r"\bpacketriot\b", re.I), "packetriot"),
)

COMPONENT_UPDATE_SELECTORS = {
    # Only components whose generic "update to a release" unambiguously
    # refers to one HA update entity. Product names in action text are
    # handled separately by PRODUCT_SELECTORS.
    "frigate": "frigate",
}


COMPLETE_MAPPING_OVERRIDES = {
    "RCP-ADDON-BINARY-PRECONFIG-001",
    "RCP-Z2M-COVER-REGRESSION-001",
    "RCP-Z2M-NETWORK-ADAPTER-RECOVERY-001",
}


CURATED_UPDATE_TARGETS = {
    "DISEASE-KB-BACKUP-0CC8501B82": ("core", "system_update"),
    "DISEASE-KB-FRIGATE-C9D119E243": ("frigate", "component_update"),
    "DISEASE-KB-MQTT-3297750F2A": ("mosquitto", "component_update"),
    "DISEASE-KB-RECORDER-E5775C72EE": ("core", "system_update"),
    "DISEASE-KB-TADO-2F17646D97": ("core", "system_update"),
    "DISEASE-KB-ZWAVE-A42DD1D3D8": ("z-wave js", "component_update"),
}


DOMAIN_SELECTORS = {
    "mqtt": "mqtt",
    "esphome": "esphome",
    "hue": "hue",
    "shelly": "shelly",
    "tado": "tado",
    "reolink": "reolink",
    "bluetooth": "bluetooth",
    "zha": "zha",
    "zwave_js": "zwave_js",
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def clean(value: Any, limit: int = 2000) -> str:
    return str(value or "").replace("\x00", " ").strip()[:limit]


def stable_protocol_id(candidate_id: str, disease_id: str) -> str:
    digest = hashlib.sha256(
        (candidate_id + "\n" + disease_id).encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"PROTOCOL-GENERATED-{digest}"


def severity(risk: str) -> str:
    return {
        "LOW": "PROBLEM",
        "MEDIUM": "DEGRADED",
        "HIGH": "CRITICAL",
    }.get(risk, "PROBLEM")


def assessment_state(candidate: dict[str, Any]) -> str:
    assessment = candidate.get("assessment")
    if isinstance(assessment, dict) and assessment.get("state"):
        return clean(assessment["state"], 80).upper()
    return clean(candidate.get("automation_class"), 80).upper() or "DIAGNOSTIC_ONLY"


def positive_action_text(action: str) -> str:
    clauses = re.split(r"[.;]", action)
    kept: list[str] = []
    for clause in clauses:
        lowered = clause.lower()
        if any(
            marker in lowered
            for marker in (
                "do not",
                "avoid ",
                "never ",
                "workaround only",
                "for expert recovery only",
                "containment only",
                "only when needed",
            )
        ):
            continue
        kept.append(clause)
    return " ".join(kept)


def mapping_is_complete(action: str, kind: str) -> bool:
    positive = positive_action_text(action)
    if kind in {"system_update", "component_update"}:
        return re.search(
            r"\b(enable|disable|configure|set|restore|recreate|install|persist|"
            r"apply|mount|expose|change|move|remove|replace|validate|create|map|config|clear|edit|restart|reload)\b",
            positive,
            re.I,
        ) is None
    if kind == "addon_restart":
        return re.search(
            r"\b(enable|disable|configure|set|restore|install|persist|apply|"
            r"mount|expose|change|move|remove|replace|validate|map|config|clear|edit)\b",
            positive,
            re.I,
        ) is None
    if kind == "core_restart":
        return re.search(
            r"\b(enable|disable|configure|set|restore|install|persist|apply|"
            r"expose|change|move|remove|replace|map|config|clear|edit)\b",
            positive,
            re.I,
        ) is None
    if kind == "create_backup":
        return re.search(
            r"\b(update|upgrade|restore|recreate|install|migrate|migration)\b",
            positive,
            re.I,
        ) is None
    return True


def blocker_for(
    disease: dict[str, Any],
    candidate: dict[str, Any],
    mapping: dict[str, Any],
    *,
    mapped: bool,
    complete: bool,
) -> tuple[str | None, str | None]:
    if mapped and complete:
        return None, None

    action = clean(candidate.get("action"), 5000)
    positive = positive_action_text(action).lower()
    component = clean(disease.get("component"), 120).lower()
    risk = clean(candidate.get("risk"), 40).upper()

    if mapped and not complete:
        return (
            "PARTIAL_MULTI_STEP",
            "At least one deterministic step is mapped, but the source treatment "
            "also requires additional steps that are not yet safely executable.",
        )

    if any(
        term in positive
        for term in (
            "re-pair", "recommission", "re-commission", "non-secure inclusion",
            "reset nvm", "factory reset", "erase coordinator", "security key",
        )
    ):
        return (
            "SECURITY_OR_RECOMMISSION_MANUAL",
            "Treatment changes network/security identity or commissioning state.",
        )

    if any(
        term in positive
        for term in (
            "physically ", "antenna", "rf ", "usb noise", "cable",
            "move the coordinator", "separate coordinator", "wiring",
        )
    ):
        return (
            "HARDWARE_OR_RF_PHYSICAL",
            "Treatment requires a physical hardware/RF action.",
        )

    if any(
        term in positive
        for term in (
            "password", "credential", "oauth", "token", "authorization",
            "secret",
        )
    ):
        return (
            "CREDENTIAL_OR_AUTH_FLOW",
            "Treatment requires authentication material or an interactive auth flow.",
        )

    if component in {
        "docker", "proxmox", "unraid", "truenas", "synology", "systemd",
    } or any(
        term in positive
        for term in (
            "container", "docker", "vm ", "qemu", "systemd", "daemon-reload",
            "passthrough", "bind mount", "host networking",
        )
    ):
        return (
            "EXTERNAL_HOST_OR_CONTAINER_CONFIG",
            "Treatment belongs to a host/container/VM platform outside HA's bounded API.",
        )

    if component in {
        "dns", "discovery", "mdns", "proxy", "tls", "bootdns",
    } or any(
        term in positive
        for term in (
            "firewall", "resolver", "dns ", "route", "routing", "vlan",
            "websocket forwarding", "trusted proxy", "certificate",
        )
    ):
        return (
            "NETWORK_OR_DNS_CONFIG",
            "Treatment changes network, DNS, proxy or TLS configuration and needs a dedicated adapter.",
        )

    if component in {
        "storage", "fs", "recorder", "mariadb", "backup", "nfs", "smb",
    } and any(
        term in positive
        for term in (
            "recover", "restore", "delete", "remove", "relocate", "mount",
            "database", "sqlite", "backup configuration", "copy of the same backup",
        )
    ):
        return (
            "STORAGE_OR_DATABASE_RECOVERY",
            "Treatment is a storage/database/restore workflow that must preserve data and needs a dedicated recovery adapter.",
        )

    if component in {
        "auto", "config", "template", "http",
    } or any(
        term in positive
        for term in (
            "yaml", "template", "condition syntax", "configuration scope",
            "setting", "settings", "resource url",
        )
    ):
        return (
            "HA_CONFIG_EDIT_REQUIRED",
            "Treatment requires a structured Home Assistant configuration edit not represented by a current primitive.",
        )

    if component in {
        "frigate", "nodered", "z2m", "zigbee", "zwave", "hacs", "esphome",
    } and any(
        term in positive
        for term in (
            "configure", "configuration", "option", "discovery", "adapter",
            "device path", "serial", "port", "frontend resource",
        )
    ):
        return (
            "PRODUCT_CONFIG_ADAPTER_REQUIRED",
            "Treatment needs a product-specific structured configuration adapter.",
        )

    if any(
        term in positive
        for term in (
            "enable", "disable", "entry", "entity",
        )
    ):
        return (
            "EXACT_TARGET_CONTEXT_REQUIRED",
            "Treatment changes an HA entry/entity but the source Protocol does not yet provide one exact stable target.",
        )

    if risk == "HIGH":
        return (
            "HIGH_RISK_MANUAL_RECOVERY",
            "High-risk treatment has no complete deterministic rollback/verification path yet.",
        )

    return (
        "NO_SAFE_DETERMINISTIC_MAPPING",
        "No complete deterministic mapping exists in the current Doctor primitive set.",
    )


def product_selector(action: str) -> str | None:
    for pattern, selector in PRODUCT_SELECTORS:
        if pattern.search(action):
            return selector
    return None


def system_update_target(action: str) -> str | None:
    if not re.search(r"\b(update|upgrade|fixed|fix|release|patch|version)\b", action, re.I):
        return None
    if re.search(r"\b(core|home assistant core)\b", action, re.I):
        return "core"
    if re.search(r"\bsupervisor\b", action, re.I):
        return "supervisor"
    if re.search(r"\b(haos|home assistant os|operating system)\b", action, re.I):
        return "os"
    if re.search(r"\bhome assistant\b", action, re.I):
        return "core"
    return None


def map_treatment(
    disease: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    action = clean(candidate.get("action"), 5000)
    lowered = action.lower()
    positive_lowered = positive_action_text(action).lower()
    component = clean(disease.get("component"), 120).lower()
    risk = clean(candidate.get("risk"), 40).upper() or "MEDIUM"
    source_candidate_id = clean(candidate.get("protocol_id"), 180)

    if (
        source_candidate_id not in SAFE_CONTEXT_DANGEROUS_OVERRIDES
        and any(term in positive_lowered for term in DANGEROUS_ACTION_HINTS)
    ):
        return {
            "mapped": False,
            "reason": "dangerous_or_environment_specific_change",
        }

    if source_candidate_id == "PROTOCOL-KB-CANDIDATE-CBDFB6B856":
        return {
            "mapped": True,
            "kind": "hacs_supported_install",
            "reason": "official_hacs_app_repository_installer",
            "diagnostics": [],
            "confirm": [{"expr": "disease_confirmed == true"}],
            "checkpoint": {
                "required": True,
                "primitive": "create_backup",
                "args": {
                    "name": "Suzie Doctor before HACS supported install",
                    "timeout_seconds": 300,
                },
            },
            "treatment": [
                {"step": 1, "primitive": "install_hacs_supported",
                 "args": {"timeout_seconds": 180}, "max_attempts": 1},
                {"step": 2, "primitive": "check_config",
                 "args": {}, "max_attempts": 1},
                {"step": 3, "primitive": "restart_core",
                 "args": {"timeout_seconds": 180}, "max_attempts": 1},
            ],
        }

    if source_candidate_id == "PROTOCOL-KB-CANDIDATE-088F68579F":
        return {
            "mapped": True,
            "kind": "hacs_supported_install",
            "reason": "official_hacs_app_repository_installer",
            "diagnostics": [],
            "confirm": [
                {"expr": "disease_confirmed == true"},
            ],
            "checkpoint": {
                "required": True,
                "primitive": "create_backup",
                "args": {
                    "name": "Suzie Doctor before HACS supported install",
                    "timeout_seconds": 300,
                },
            },
            "treatment": [
                {"step": 1, "primitive": "ensure_update_current",
                 "args": {"target": "supervisor", "timeout_seconds": 600},
                 "max_attempts": 1},
                {"step": 2, "primitive": "install_hacs_supported",
                 "args": {"timeout_seconds": 180}, "max_attempts": 1},
                {"step": 3, "primitive": "check_config",
                 "args": {}, "max_attempts": 1},
                {"step": 4, "primitive": "restart_core",
                 "args": {"timeout_seconds": 180}, "max_attempts": 1},
            ],
        }

    if source_candidate_id in {
        "PROTOCOL-KB-CANDIDATE-F1AFF02F2A",
        "RCP-DNS-HAOS-001",
        "RCP-BOOT-DNS-DEPENDENCY-001",
    }:
        return {
            "mapped": True,
            "kind": "haos_primary_dns_context",
            "reason": "supervisor_primary_auto_dns_with_context_and_rollback",
            "diagnostics": [
                {"id": "network_before", "primitive": "network_primary_info",
                 "args": {}, "save_as": "network_before"},
                {"id": "dns_target", "primitive": "context_list",
                 "args": {"key": "dns_servers"}, "save_as": "dns_target"},
            ],
            "confirm": [
                {"expr": "disease_confirmed == true"},
                {"expr": "network_before.found == true"},
                {"expr": "network_before.method == 'auto'"},
                {"expr": "dns_target.found == true"},
            ],
            "checkpoint": {
                "required": True,
                "primitive": "create_backup",
                "args": {
                    "name": "Suzie Doctor before HAOS DNS change",
                    "timeout_seconds": 300,
                },
            },
            "treatment": [
                {"step": 1, "primitive": "network_set_primary_dns",
                 "args": {"nameservers": "$dns_target.value",
                          "timeout_seconds": 60}, "max_attempts": 1},
            ],
            "rollback": [
                {"step": 1, "primitive": "network_set_primary_dns",
                 "args": {"nameservers": "$network_before.nameservers",
                          "timeout_seconds": 60}, "max_attempts": 1},
            ],
        }

    if source_candidate_id == "RCP-WYOMING-MEMORY-001":
        return {
            "mapped": True,
            "kind": "wyoming_memory_recovery",
            "reason": "exact_wyoming_entry_update_and_memory_slope_verify",
            "diagnostics": [
                {"id": "config_target_id", "primitive": "context_value",
                 "args": {"key": "config_entry_id"}, "save_as": "config_target_id"},
                {"id": "config_target", "primitive": "config_entry_info",
                 "args": {"entry_id": "$config_target_id.value"},
                 "save_as": "config_target"},
                {"id": "update_target", "primitive": "update_state",
                 "args": {"target": "core"}, "save_as": "update_target"},
            ],
            "confirm": [
                {"expr": "disease_confirmed == true"},
                {"expr": "config_target_id.found == true"},
                {"expr": "config_target.found == true"},
                {"expr": "config_target.domain == 'wyoming'"},
                {"expr": "update_target.found == true"},
                {"expr": "update_target.available == true"},
            ],
            "checkpoint": {
                "required": True,
                "primitive": "create_backup",
                "args": {
                    "name": "Suzie Doctor before Wyoming memory recovery",
                    "timeout_seconds": 300,
                },
            },
            "treatment": [
                {"step": 1, "primitive": "config_entry_set_enabled",
                 "args": {"entry_id": "$config_target_id.value", "enabled": False,
                          "timeout_seconds": 60}, "max_attempts": 1},
                {"step": 2, "primitive": "restart_core",
                 "args": {"timeout_seconds": 180}, "max_attempts": 1},
                {"step": 3, "primitive": "install_update",
                 "args": {"entity_id": "$update_target.entity_id",
                          "timeout_seconds": 600}, "max_attempts": 1},
                {"step": 4, "primitive": "config_entry_set_enabled",
                 "args": {"entry_id": "$config_target_id.value", "enabled": True,
                          "timeout_seconds": 60}, "max_attempts": 1},
                {"step": 5, "primitive": "core_memory_stability",
                 "args": {"duration_seconds": 60, "interval_seconds": 10,
                          "max_increase_percent": 8.0, "max_percent": 90.0},
                 "max_attempts": 1},
            ],
            "rollback": [
                {"step": 1, "primitive": "config_entry_set_enabled",
                 "args": {"entry_id": "$config_target_id.value", "enabled": False,
                          "timeout_seconds": 60}, "max_attempts": 1},
                {"step": 2, "primitive": "restart_core",
                 "args": {"timeout_seconds": 180}, "max_attempts": 1},
            ],
        }

    if source_candidate_id == "PROTOCOL-KB-CANDIDATE-A8B3968C87":
        return {
            "mapped": True,
            "kind": "google_assistant_unexpose_context",
            "reason": "exact_entity_voice_assistant_exposure_update",
            "diagnostics": [
                {"id": "entity_target", "primitive": "context_value",
                 "args": {"key": "entity_id"}, "save_as": "entity_target"},
            ],
            "confirm": [
                {"expr": "disease_confirmed == true"},
                {"expr": "entity_target.found == true"},
            ],
            "treatment": [
                {"step": 1, "primitive": "google_assistant_set_exposed",
                 "args": {"entity_id": "$entity_target.value",
                          "exposed": False},
                 "max_attempts": 1},
            ],
            "rollback": [
                {"step": 1, "primitive": "google_assistant_set_exposed",
                 "args": {"entity_id": "$entity_target.value",
                          "exposed": True},
                 "max_attempts": 1},
            ],
        }

    if source_candidate_id == "PROTOCOL-KB-CANDIDATE-27342DCCC0":
        return {
            "mapped": True,
            "kind": "entity_device_class_context",
            "reason": "exact_entity_and_device_class_registry_update",
            "diagnostics": [
                {"id": "entity_target", "primitive": "context_value",
                 "args": {"key": "entity_id"}, "save_as": "entity_target"},
                {"id": "device_class_target", "primitive": "context_value",
                 "args": {"key": "device_class"}, "save_as": "device_class_target"},
                {"id": "entity_registry", "primitive": "entity_registry_info",
                 "args": {"entity_id": "$entity_target.value"},
                 "save_as": "entity_registry"},
            ],
            "confirm": [
                {"expr": "disease_confirmed == true"},
                {"expr": "entity_target.found == true"},
                {"expr": "device_class_target.found == true"},
                {"expr": "entity_registry.found == true"},
            ],
            "treatment": [
                {"step": 1, "primitive": "set_entity_device_class",
                 "args": {"entity_id": "$entity_target.value",
                          "device_class": "$device_class_target.value"},
                 "max_attempts": 1}
            ],
            "rollback": [
                {"step": 1, "primitive": "set_entity_device_class",
                 "args": {"entity_id": "$entity_target.value",
                          "device_class": "$entity_registry.device_class"},
                 "max_attempts": 1}
            ],
        }

    if source_candidate_id == "PROTOCOL-KB-CANDIDATE-8B1352CEDF":
        return {
            "mapped": True,
            "kind": "disable_conflicting_config_entry",
            "reason": "exact_conflicting_config_entry_disable_after_backup",
            "diagnostics": [
                {
                    "id": "config_target_id",
                    "primitive": "context_value",
                    "args": {"key": "config_entry_id"},
                    "save_as": "config_target_id",
                },
                {
                    "id": "config_target",
                    "primitive": "config_entry_info",
                    "args": {"entry_id": "$config_target_id.value"},
                    "save_as": "config_target",
                },
            ],
            "confirm": [
                {"expr": "disease_confirmed == true"},
                {"expr": "config_target_id.found == true"},
                {"expr": "config_target.found == true"},
            ],
            "checkpoint": {
                "required": True,
                "primitive": "create_backup",
                "args": {
                    "name": "Suzie Doctor before disabling conflicting custom entry",
                    "timeout_seconds": 300,
                },
            },
            "treatment": [
                {
                    "step": 1,
                    "primitive": "config_entry_set_enabled",
                    "args": {
                        "entry_id": "$config_target_id.value",
                        "enabled": False,
                        "timeout_seconds": 60,
                    },
                    "max_attempts": 1,
                }
            ],
            "rollback": [
                {
                    "step": 1,
                    "primitive": "config_entry_set_enabled",
                    "args": {
                        "entry_id": "$config_target_id.value",
                        "enabled": True,
                        "timeout_seconds": 60,
                    },
                    "max_attempts": 1,
                }
            ],
        }

    if source_candidate_id == "RCP-HA-CUSTOM-SAFE-MODE-001":
        return {
            "mapped": True,
            "kind": "disable_custom_entry_restart",
            "reason": "exact_confirmed_config_entry_safe_mode_recovery",
            "diagnostics": [
                {
                    "id": "config_target_id",
                    "primitive": "context_value",
                    "args": {"key": "config_entry_id"},
                    "save_as": "config_target_id",
                },
                {
                    "id": "config_target",
                    "primitive": "config_entry_info",
                    "args": {
                        "entry_id": "$config_target_id.value"
                    },
                    "save_as": "config_target",
                },
            ],
            "confirm": [
                {"expr": "disease_confirmed == true"},
                {"expr": "config_target_id.found == true"},
                {"expr": "config_target.found == true"},
            ],
            "checkpoint": {
                "required": True,
                "primitive": "create_backup",
                "args": {
                    "name": "Suzie Doctor before disabling custom integration",
                    "timeout_seconds": 300,
                },
            },
            "treatment": [
                {
                    "step": 1,
                    "primitive": "config_entry_set_enabled",
                    "args": {
                        "entry_id": "$config_target_id.value",
                        "enabled": False,
                        "timeout_seconds": 60,
                    },
                    "max_attempts": 1,
                },
                {
                    "step": 2,
                    "primitive": "check_config",
                    "args": {},
                    "max_attempts": 1,
                },
                {
                    "step": 3,
                    "primitive": "restart_core",
                    "args": {"timeout_seconds": 180},
                    "max_attempts": 1,
                },
            ],
            "rollback": [
                {
                    "step": 1,
                    "primitive": "config_entry_set_enabled",
                    "args": {
                        "entry_id": "$config_target_id.value",
                        "enabled": True,
                        "timeout_seconds": 60,
                    },
                    "max_attempts": 1,
                }
            ],
        }

    if source_candidate_id == "RCP-ENTITY-DISABLED-001":
        return {
            "mapped": True,
            "kind": "entity_enable_context",
            "reason": "exact_entity_registry_enable_with_verified_reload",
            "diagnostics": [
                {
                    "id": "entity_target",
                    "primitive": "context_value",
                    "args": {"key": "entity_id"},
                    "save_as": "entity_target",
                },
                {
                    "id": "entity_registry",
                    "primitive": "entity_registry_info",
                    "args": {"entity_id": "$entity_target.value"},
                    "save_as": "entity_registry",
                },
            ],
            "confirm": [
                {"expr": "disease_confirmed == true"},
                {"expr": "entity_target.found == true"},
                {"expr": "entity_registry.found == true"},
                {"expr": "entity_registry.disabled == true"},
            ],
            "treatment": [
                {
                    "step": 1,
                    "primitive": "set_entity_enabled",
                    "args": {
                        "entity_id": "$entity_target.value",
                        "enabled": True,
                        "timeout_seconds": 60,
                    },
                    "max_attempts": 1,
                }
            ],
            "rollback": [
                {
                    "step": 1,
                    "primitive": "set_entity_enabled",
                    "args": {
                        "entity_id": "$entity_target.value",
                        "enabled": False,
                        "timeout_seconds": 60,
                    },
                    "max_attempts": 1,
                }
            ],
        }

    curated_update = CURATED_UPDATE_TARGETS.get(
        clean(disease.get("disease_id"), 180)
    )
    if curated_update:
        curated_target, curated_kind = curated_update
        return {
            "mapped": True,
            "kind": curated_kind,
            "reason": f"reviewed_update_target:{curated_target}",
            "diagnostics": [
                {
                    "id": "update_target",
                    "primitive": "update_state",
                    "args": {"target": curated_target},
                    "save_as": "update_target",
                }
            ],
            "confirm": [
                {"expr": "disease_confirmed == true"},
                {"expr": "update_target.found == true"},
                {"expr": "update_target.available == true"},
            ],
            "treatment": [
                {
                    "step": 1,
                    "primitive": "install_update",
                    "args": {
                        "entity_id": "$update_target.entity_id",
                        "timeout_seconds": 600,
                    },
                    "max_attempts": 1,
                }
            ],
        }

    target = system_update_target(action)
    if target:
        return {
            "mapped": True,
            "kind": "system_update",
            "reason": f"native_update_entity:{target}",
            "diagnostics": [
                {
                    "id": "update_target",
                    "primitive": "update_state",
                    "args": {"target": target},
                    "save_as": "update_target",
                }
            ],
            "confirm": [
                {"expr": "disease_confirmed == true"},
                {"expr": "update_target.found == true"},
                {"expr": "update_target.available == true"},
            ],
            "treatment": [
                {
                    "step": 1,
                    "primitive": "install_update",
                    "args": {
                        "entity_id": "$update_target.entity_id",
                        "timeout_seconds": 600,
                    },
                    "max_attempts": 1,
                }
            ],
        }

    product = product_selector(action)
    if product is None:
        product = COMPONENT_UPDATE_SELECTORS.get(component)
    if (
        product
        and re.search(
            r"\b(update|upgrade|fixed|fix(?:ed)? release|fix(?:ed)? version|fix(?:ed)? patch|release containing|version containing)\b",
            action,
            re.I,
        )
    ):
        return {
            "mapped": True,
            "kind": "component_update",
            "reason": f"native_update_entity_selector:{product}",
            "diagnostics": [
                {
                    "id": "update_target",
                    "primitive": "update_state",
                    "args": {"target": product},
                    "save_as": "update_target",
                }
            ],
            "confirm": [
                {"expr": "disease_confirmed == true"},
                {"expr": "update_target.found == true"},
                {"expr": "update_target.available == true"},
            ],
            "treatment": [
                {
                    "step": 1,
                    "primitive": "install_update",
                    "args": {
                        "entity_id": "$update_target.entity_id",
                        "timeout_seconds": 600,
                    },
                    "max_attempts": 1,
                }
            ],
        }

    if (
        product
        and re.search(r"\brestart\b", action, re.I)
        and re.search(r"\b(add-?on|app|zigbee2mqtt|z2m|z[- ]?wave js|frigate|mosquitto)\b", action, re.I)
    ):
        return {
            "mapped": True,
            "kind": "addon_restart",
            "reason": f"supervisor_addon_restart:{product}",
            "diagnostics": [
                {
                    "id": "addon_target",
                    "primitive": "addon_info",
                    "args": {"selector": product},
                    "save_as": "addon_target",
                }
            ],
            "confirm": [
                {"expr": "disease_confirmed == true"},
                {"expr": "addon_target.found == true"},
            ],
            "treatment": [
                {
                    "step": 1,
                    "primitive": "restart_addon",
                    "args": {
                        "slug": "$addon_target.slug",
                        "timeout_seconds": 120,
                    },
                    "max_attempts": 1,
                }
            ],
        }

    if re.search(r"\breload\b", action, re.I) and re.search(
        r"\bautomation(?:s)?\b", action, re.I
    ):
        return {
            "mapped": True,
            "kind": "subsystem_reload",
            "reason": "homeassistant_automation_reload",
            "diagnostics": [],
            "confirm": [{"expr": "disease_confirmed == true"}],
            "treatment": [
                {
                    "step": 1,
                    "primitive": "reload_subsystem",
                    "args": {"subsystem": "automation"},
                    "max_attempts": 1,
                }
            ],
        }

    if re.search(
        r"\b(reload|restart)\b.{0,100}\b(integration|config entry)\b|"
        r"\b(integration|config entry)\b.{0,100}\b(reload|restart)\b",
        action,
        re.I,
    ):
        domain = DOMAIN_SELECTORS.get(component)
        if domain:
            return {
                "mapped": True,
                "kind": "config_entry_reload",
                "reason": f"unique_config_entry_domain:{domain}",
                "diagnostics": [
                    {
                        "id": "config_target",
                        "primitive": "config_entry_info",
                        "args": {"domain": domain},
                        "save_as": "config_target",
                    }
                ],
                "confirm": [
                    {"expr": "disease_confirmed == true"},
                    {"expr": "config_target.found == true"},
                ],
                "treatment": [
                    {
                        "step": 1,
                        "primitive": "reload_config_entry_verified",
                        "args": {
                            "entry_id": "$config_target.entry_id",
                            "timeout_seconds": 90,
                        },
                        "max_attempts": 1,
                    }
                ],
            }

    if (
        re.search(r"\b(core restart|restart core|restart home assistant)\b", action, re.I)
        and risk in {"MEDIUM", "HIGH"}
    ):
        return {
            "mapped": True,
            "kind": "core_restart",
            "reason": "checked_backed_up_core_restart",
            "diagnostics": [],
            "confirm": [{"expr": "disease_confirmed == true"}],
            "treatment": [
                {
                    "step": 1,
                    "primitive": "check_config",
                    "args": {},
                    "max_attempts": 1,
                },
                {
                    "step": 2,
                    "primitive": "create_backup",
                    "args": {
                        "name": "Suzie Doctor before protocol Core restart",
                        "timeout_seconds": 300,
                    },
                    "max_attempts": 1,
                },
                {
                    "step": 3,
                    "primitive": "restart_core",
                    "args": {"timeout_seconds": 180},
                    "max_attempts": 1,
                },
            ],
        }

    if (
        re.search(r"\b(create|make|take)\b.{0,80}\b(fresh |new |full )?backup\b", action, re.I)
        and not re.search(r"\brestore\b", action, re.I)
    ):
        return {
            "mapped": True,
            "kind": "create_backup",
            "reason": "native_full_backup",
            "diagnostics": [],
            "confirm": [{"expr": "disease_confirmed == true"}],
            "treatment": [
                {
                    "step": 1,
                    "primitive": "create_backup",
                    "args": {
                        "name": "Suzie Doctor protocol backup",
                        "timeout_seconds": 300,
                    },
                    "max_attempts": 1,
                }
            ],
        }

    return {
        "mapped": False,
        "complete": False,
        "reason": "no_deterministic_primitive_mapping",
    }


def build_card(
    disease: dict[str, Any],
    candidate: dict[str, Any],
    *,
    approved_keys: set[str],
) -> dict[str, Any]:
    disease_id = clean(disease.get("disease_id"), 180)
    source_candidate_id = clean(
        candidate.get("protocol_id"), 180
    ) or "CANDIDATE-UNKNOWN"
    approval_key = f"{disease_id}|{source_candidate_id}"
    approved = approval_key in approved_keys
    risk = clean(candidate.get("risk"), 40).upper() or "MEDIUM"
    source_class = clean(
        candidate.get("automation_class"), 80
    ).upper() or "DIAGNOSTIC_ONLY"
    assessment = assessment_state(candidate)
    mapping = map_treatment(disease, candidate)
    mapped = bool(mapping.get("mapped"))
    if mapped:
        mapping["complete"] = (
            source_candidate_id in COMPLETE_MAPPING_OVERRIDES
            or mapping_is_complete(
                clean(candidate.get("action"), 5000),
                str(mapping.get("kind") or ""),
            )
        )
    complete = bool(mapping.get("complete"))

    if assessment == "DIAGNOSTIC_ONLY" and approved:
        protocol_status = "WATCH"
        automation_class = "DIAGNOSTIC_ONLY"
        factory_state = (
            "WATCH_DIAGNOSTIC"
            if mapped and complete
            else "WATCH_DIAGNOSTIC_GUIDANCE"
        )
    elif mapped and complete and approved:
        if assessment == "CONFIRM_REQUIRED_HIGH_RISK":
            protocol_status = "ACTIVE"
            automation_class = "CONFIRM_REQUIRED"
            factory_state = "ACTIVE_CONFIRM_HIGH_RISK"
        elif assessment == "CONFIRM_REQUIRED":
            protocol_status = "ACTIVE"
            automation_class = "CONFIRM_REQUIRED"
            factory_state = "ACTIVE_CONFIRM"
        elif (
            source_class == "AUTO_SAFE"
            and risk == "LOW"
            and assessment != "PRIMITIVE_MAPPING_REQUIRED"
        ):
            protocol_status = "ACTIVE"
            automation_class = "AUTO_SAFE"
            factory_state = "ACTIVE_AUTO_SAFE"
        else:
            protocol_status = "ACTIVE"
            automation_class = "CONFIRM_REQUIRED"
            factory_state = "ACTIVE_CONFIRM"
    else:
        automation_class = "DIAGNOSTIC_ONLY"
        if not approved:
            protocol_status = "SUSPENDED"
            factory_state = "AWAITING_PROTOCOL_REVIEW"
        else:
            # Baseline candidate has been fully curated. If machine execution
            # is still blocked, publish it as MANUAL rather than leaving dead
            # knowledge in SUSPENDED.
            protocol_status = "MANUAL"
            if mapped and not complete:
                factory_state = "MANUAL_PARTIAL_MAPPING"
            else:
                factory_state = "MANUAL_GUIDANCE"

    diagnostics = [
        {
            "id": "confirmed_disease",
            "primitive": "confirmed_disease",
            "args": {"disease_id": disease_id},
            "save_as": "disease_confirmed",
        }
    ]
    diagnostics.extend(mapping.get("diagnostics") or [])
    confirm = (
        mapping.get("confirm")
        if mapped
        else [{"expr": "disease_confirmed == true"}]
    )
    treatment = (
        mapping.get("treatment") or []
        if mapped and complete
        else []
    )
    blocker_class, blocker_detail = blocker_for(
        disease,
        candidate,
        mapping,
        mapped=mapped,
        complete=complete,
    )
    deferred_treatment_blocker_class = None
    deferred_treatment_blocker_detail = None
    if assessment == "DIAGNOSTIC_ONLY" and blocker_class:
        # WATCH is publishable without treatment. Preserve why treatment
        # is unavailable, but do not treat that as a publication blocker.
        deferred_treatment_blocker_class = blocker_class
        deferred_treatment_blocker_detail = blocker_detail
        blocker_class = None
        blocker_detail = None

    manual = None
    if protocol_status == "MANUAL":
        manual = {
            "checks": [clean(x, 1000) for x in (candidate.get("checks") or [])[:12]],
            "action": clean(candidate.get("action"), 3000),
            "verify": [clean(x, 1000) for x in (candidate.get("verify") or [])[:12]],
            "rollback": clean(candidate.get("rollback"), 2000),
            "machine_blocker_class": blocker_class,
            "machine_blocker_detail": blocker_detail,
        }

    card = {
        "schema_version": 1,
        "disease_id": disease_id,
        "title": clean(
            candidate.get("title")
            or disease.get("title")
            or source_candidate_id,
            500,
        ),
        "component": clean(disease.get("component"), 120) or "unknown",
        "severity": severity(risk),
        "protocol": {
            "id": stable_protocol_id(source_candidate_id, disease_id),
            "version": "0.1.0",
            "status": protocol_status,
        },
        "source_evidence": [],
        "triggers": {
            "any": [
                {
                    "type": "disease_confirmed",
                    "match": disease_id,
                }
            ]
        },
        "preconditions": [],
        "diagnostics": diagnostics,
        "confirm": {"all": confirm},
        "exclude": [],
        "dont_do": [],
        "checkpoint": (
            mapping.get("checkpoint")
            if mapped and complete and mapping.get("checkpoint")
            else {
                "required": False,
                "primitive": None,
                "args": {},
            }
        ),
        "treatment": treatment,
        "verify": {
            "rerun_diagnostics": False,
            "success_when": "primitive_self_verified",
        },
        "fallback": [],
        "rollback": (
            mapping.get("rollback") or []
            if mapped and complete
            else []
        ),
        "cooldown_seconds": 3600,
        "recurrence_rule": "daily_audit_boundary",
        "on_failure": "ESCALATION_REQUIRED",
        "automation_class": automation_class,
        "manual": manual,
        "factory": {
            "factory_version": FACTORY_VERSION,
            "state": factory_state,
            "mapped": mapped,
            "complete_mapping": complete,
            "approved_for_publish": approved,
            "approval_key": approval_key,
            "mapping_kind": mapping.get("kind"),
            "mapping_reason": mapping.get("reason"),
            "blocker_class": blocker_class,
            "blocker_detail": blocker_detail,
            "deferred_treatment_blocker_class": deferred_treatment_blocker_class,
            "deferred_treatment_blocker_detail": deferred_treatment_blocker_detail,
            "source_candidate_id": source_candidate_id,
            "source_assessment": assessment,
            "source_automation_class": source_class,
            "source_risk": risk,
            "source_action": clean(candidate.get("action"), 3000),
            "source_checks": [
                clean(x, 1000)
                for x in (candidate.get("checks") or [])[:12]
            ],
            "source_verify": [
                clean(x, 1000)
                for x in (candidate.get("verify") or [])[:12]
            ],
            "rollback_guidance": clean(
                candidate.get("rollback"), 2000
            ),
        },
    }
    return card


def card_primitives(card: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for section in ("diagnostics", "treatment", "fallback", "rollback"):
        for item in card.get(section) or []:
            if isinstance(item, dict) and item.get("primitive"):
                out.add(str(item["primitive"]))
    checkpoint = card.get("checkpoint")
    if isinstance(checkpoint, dict) and checkpoint.get("primitive"):
        out.add(str(checkpoint["primitive"]))
    return out


def build() -> dict[str, Any]:
    data = json.loads(NORMALIZED.read_text(encoding="utf-8"))
    approval_data = (
        json.loads(APPROVALS.read_text(encoding="utf-8"))
        if APPROVALS.exists()
        else {}
    )
    approved_keys = {
        str(x) for x in (approval_data.get("approved_keys") or [])
    }
    cards: list[dict[str, Any]] = []
    source_count = 0
    for disease in data.get("diseases") or []:
        if not isinstance(disease, dict):
            continue
        for candidate in disease.get("protocol_candidates") or []:
            if not isinstance(candidate, dict):
                continue
            source_count += 1
            cards.append(
                build_card(
                    disease,
                    candidate,
                    approved_keys=approved_keys,
                )
            )

    states = Counter(
        str((card.get("factory") or {}).get("state"))
        for card in cards
    )
    mapping_kinds = Counter(
        str((card.get("factory") or {}).get("mapping_kind"))
        for card in cards
        if (card.get("factory") or {}).get("mapped")
    )
    status_counts = Counter(
        str((card.get("protocol") or {}).get("status"))
        for card in cards
    )
    blocker_counts = Counter(
        str((card.get("factory") or {}).get("blocker_class"))
        for card in cards
        if (card.get("factory") or {}).get("blocker_class")
    )
    class_counts = Counter(
        str(card.get("automation_class"))
        for card in cards
    )
    unsupported = sorted(
        {
            primitive
            for card in cards
            for primitive in card_primitives(card)
            if primitive not in ALLOWED_PRIMITIVES
        }
    )

    result = {
        "schema_version": 1,
        "factory_version": FACTORY_VERSION,
        "generated_at": now_iso(),
        "source_protocol_candidates": source_count,
        "approval_policy": {
            "approved_keys": len(approved_keys),
            "source": str(APPROVALS),
        },
        "stats": {
            "protocols": len(cards),
            "factory_states": dict(sorted(states.items())),
            "mapping_kinds": dict(sorted(mapping_kinds.items())),
            "protocol_status": dict(sorted(status_counts.items())),
            "blocker_classes": dict(sorted(blocker_counts.items())),
            "automation_class": dict(sorted(class_counts.items())),
            "unsupported_primitives": unsupported,
        },
        "protocols": cards,
    }
    if source_count != len(cards):
        raise RuntimeError("candidate/protocol accounting mismatch")
    if unsupported:
        raise RuntimeError(
            "unsupported generated primitives: " + ",".join(unsupported)
        )
    ids = [
        str((card.get("protocol") or {}).get("id"))
        for card in cards
    ]
    if len(ids) != len(set(ids)):
        raise RuntimeError("generated protocol IDs are not unique")
    for card in cards:
        factory = card.get("factory") or {}
        status = str((card.get("protocol") or {}).get("status") or "")
        blocker = factory.get("blocker_class")
        if status == "SUSPENDED" and not blocker:
            raise RuntimeError("SUSPENDED protocol has no blocker classification")
        if status in {"ACTIVE", "WATCH"} and blocker:
            raise RuntimeError("machine-publishable protocol unexpectedly has a blocker")
        if status == "MANUAL" and not card.get("manual"):
            raise RuntimeError("MANUAL protocol has no manual guidance")
        if (
            str((card.get("protocol") or {}).get("status")) == "ACTIVE"
            and not factory.get("complete_mapping")
        ):
            raise RuntimeError("incomplete protocol may not be ACTIVE")
        if (
            str(card.get("automation_class")) == "AUTO_SAFE"
            and str(factory.get("source_risk")) != "LOW"
        ):
            raise RuntimeError("AUTO_SAFE generated protocol must be LOW risk")
        if (
            factory.get("complete_mapping")
            and str(factory.get("source_assessment"))
            == "CONFIRM_REQUIRED_HIGH_RISK"
            and str(card.get("automation_class")) != "CONFIRM_REQUIRED"
        ):
            raise RuntimeError("high-risk executable protocol lost confirmation gate")
    return result


def main() -> int:
    result = build()
    temp = OUTPUT.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temp, 0o640)
    temp.replace(OUTPUT)
    print(
        json.dumps(
            {
                "result": "PASS",
                "factory_version": FACTORY_VERSION,
                "protocols": result["stats"]["protocols"],
                "factory_states": result["stats"]["factory_states"],
                "mapping_kinds": result["stats"]["mapping_kinds"],
                "protocol_status": result["stats"]["protocol_status"],
                "automation_class": result["stats"]["automation_class"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
