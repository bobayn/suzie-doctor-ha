# Suzie Doctor — Connector capability audit baseline

Date: 2026-09-19

This document is an analytical baseline for Connector/Suite v1. It does not
change any persisted Protocol status and it does not publish treatment.

## Authoritative live aggregate

Doctor Server /health reported:

- Protocol Factory records: 171
- ACTIVE: 17
- WATCH: 64
  - WATCH_DIAGNOSTIC: 34
  - WATCH_DIAGNOSTIC_GUIDANCE: 30
- MANUAL: 90
  - MANUAL_GUIDANCE: 82
  - MANUAL_PARTIAL_MAPPING: 8
- unsupported primitives: 0
- all ACTIVE: CONFIRM_REQUIRED

The live aggregate is consistent with the accepted baseline.

## Suite v1 effect on existing statuses

Connector/Suite v1 does not promote any Protocol automatically.

- 17 ACTIVE retain the existing signed execution-package -> ProtocolEngine
  treatment path.
- 64 WATCH remain diagnosis/guidance only.
- 90 MANUAL remain non-executable by ProtocolEngine.
- New external findings still require the publication/review gate.

Therefore the number of newly executable WATCH/MANUAL protocols introduced by
this Suite change is intentionally 0.

## MANUAL blocker analysis

The live Doctor Server aggregate reports the following 90 primary blocker
classes:

| Blocker class | Count | Analytical class |
|---|---:|---|
| EXACT_TARGET_CONTEXT_REQUIRED | 2 | AI_ASSISTED_POSSIBLE |
| HA_CONFIG_EDIT_REQUIRED | 7 | AI_ASSISTED_POSSIBLE |
| PARTIAL_MULTI_STEP | 8 | AI_ASSISTED_POSSIBLE |
| EXTERNAL_HOST_OR_CONTAINER_CONFIG | 29 | CONNECTOR_ADAPTER_MISSING |
| NETWORK_OR_DNS_CONFIG | 8 | CONNECTOR_ADAPTER_MISSING |
| PRODUCT_CONFIG_ADAPTER_REQUIRED | 10 | CONNECTOR_ADAPTER_MISSING |
| STORAGE_OR_DATABASE_RECOVERY | 8 | CONNECTOR_ADAPTER_MISSING |
| CREDENTIAL_OR_AUTH_FLOW | 4 | HUMAN_REQUIRED |
| HARDWARE_OR_RF_PHYSICAL | 1 | HUMAN_REQUIRED |
| HIGH_RISK_MANUAL_RECOVERY | 2 | TRUE_HIGH_RISK_MANUAL |
| NO_SAFE_DETERMINISTIC_MAPPING | 11 | TRUE_HIGH_RISK_MANUAL |

Aggregate interpretation:

- AI_ASSISTED_POSSIBLE: 17
- CONNECTOR_ADAPTER_MISSING: 55
- HUMAN_REQUIRED: 5
- TRUE_HIGH_RISK_MANUAL: 13

So 72/90 MANUAL cases are candidates for future safe Connector/AI-assisted
coverage in principle, but 55 of those still lack a specialized Connector
adapter and none are promoted by this report. Five clearly require a human
credential/physical step at the current blocker level. Thirteen remain
high-risk/no-safe-mapping at this aggregate level.

## WATCH limitation

The Server health endpoint exposes the WATCH subtype counts but not enough
per-Protocol fields to honestly classify the 34 WATCH_DIAGNOSTIC and 30
WATCH_DIAGNOSTIC_GUIDANCE records into treatable / diagnostic-only /
human-required buckets.

No count is invented here.

The repository now includes:

tools/audit_protocol_connector_coverage.py

It accepts the full generated Protocol catalog plus the installed Connector
contract and produces JSON/Markdown per-Protocol coverage fields including:

- required primitives;
- required Connector families;
- adapter availability;
- full treatment coverage;
- checkpoint/rollback/verify coverage;
- human/credential requirements;
- risk/blocker classification.

The tool is analytical only. It never mutates Protocol status.

## Current Connector v1 capability policy

Actually exposed now:

- doctor.* canonical Suite/Skill/capability discovery;
- doctor.diagnose using the existing signed treatment path;
- ha.* read-only diagnostics;
- supervisor.* read-only diagnostics.

Reserved but unavailable until a safe specialized backend is implemented:

- frigate
- docker
- z2m
- zwave
- mqtt
- esphome
- nodered
- storage
- recorder
- network
- auth
- human

Unavailable means unavailable. It does not silently fall back to arbitrary
shell/root access.
