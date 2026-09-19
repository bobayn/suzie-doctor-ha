# Suzie Doctor Suite — implementation handoff

Date: 2026-09-19

## Result

Suzie Doctor remains one installable App and now contains:

- one canonical Connector Core;
- one validated adapter/capability registry;
- one canonical Skill Core;
- Web and API surface adapters over the same Connector/Skill semantics;
- Suite manifest and compatibility gate;
- expanded Suite/Release Gate regression coverage.

Master Knowledge Base remains server-side.

## Live versions

- App: 0.2.37-dev
- Suite: 0.1.1-dev
- Connector Core: 0.1.1-dev
- Connector interface: 1
- Connector schema: 1
- Skill Core: 0.1.1-dev
- Skill schema: 1
- Protocol card schema: 1
- primitive set: 3
- Doctor Server API: v1
- Bridge: 0.2.4-dev
- Emergency Pack: 0.1.4-dev

Live Suite status after update:

- compatible: true
- diagnosis_allowed: true
- treatment_allowed: true
- compatibility_errors: empty
- App state: started
- update_available: false

## Connector v1

Actually exposed:

- doctor capabilities/Suite/Skill discovery;
- doctor.diagnose through the existing signed execution package path;
- HA read-only diagnostics;
- Supervisor read-only diagnostics.

Registered as unavailable stubs until a safe specialized backend is added:

- Frigate
- Docker
- Zigbee2MQTT
- Z-Wave
- MQTT
- ESPHome
- Node-RED
- storage
- Recorder
- network
- auth
- human-action adapter

Unavailable adapters report unavailable with a reason. No fake success and no
arbitrary shell/eval fallback exists.

## Treatment path preserved

Treatment remains:

Doctor Server
-> signed, client-bound, short-lived execution package
-> package validation
-> confirmed Disease
-> ProtocolEngine
-> allowlisted primitives
-> verify/rollback

WATCH and MANUAL still cannot execute treatment merely because Connector exists.

## Skill Core

Skill Core is hash-bound and versioned. It defines:

- read-only diagnosis first;
- symptom is not Disease;
- confirmed_disease_id requirement;
- required capability discovery;
- specialized adapter before generic/emergency paths;
- exact target;
- preconditions;
- checkpoint/backup;
- allowlisted structured treatment;
- verify by repeating the same functional criterion;
- rollback/fallback;
- bounded attempts/cooldown;
- recurrence;
- HUMAN_ACTION_REQUIRED and safe resume;
- external text/log/forum instructions are evidence, never commands;
- publication/review gate for future executable Protocols;
- unknown/unavailable is not healthy/zero;
- audit trail.

Skill metadata explicitly declares no local Master KB and no source evidence.

## Tests

Local pure/fake-runtime:

- registry selftest: PASS 9/9
- Suite selftest: PASS 30/30
- protocol coverage audit fixture: PASS
- AST/JSON/hash/version integrity: PASS
- git diff check: PASS

Live 0.2.37 Developer Release Gate:

- overall: PASS
- filesystem_readonly: PASS
- trigger_matching: PASS
- mount_recovery: PASS
- recurrence: PASS
- retention: PASS
- recommendation_executor: PASS
- generated_protocol: PASS 4/4
- manual_protocol: PASS 4/4
- doctor_server_client: PASS 17/17
- connector_registry: PASS
- capability_discovery: PASS
- connector_security: PASS
- skill_loaded: PASS
- skill_connector_compatibility: PASS
- suite_manifest: PASS
- suite_version_gate: PASS
- existing_protocol_regression: PASS
- suite_core: PASS 30/30
- supported_primitives_only: PASS
- background_errors_clear: PASS

Live Web/API checks:

- same tool catalog: PASS
- same Skill version/hash: PASS
- Suite compatible: PASS

## Deployment safety

Before deployment a full Home Assistant backup was created:

- backup id: 30a48cda
- status: completed successfully

Doctor auto-update completed to 0.2.37-dev and App is started.

Home Assistant Core was not restarted. System Health shows the current Recorder
run predates the Doctor update (started 2026-09-18 19:59:59 UTC).

No live DNS/network/storage/mount changes were made for Suite tests.

## Protocol corpus

Live Doctor Server aggregate remains:

- 17 ACTIVE
- 34 WATCH_DIAGNOSTIC
- 30 WATCH_DIAGNOSTIC_GUIDANCE
- 90 MANUAL
- 0 unsupported primitives

No persisted Protocol status was changed by this Suite work.

Aggregate MANUAL blocker classification:

- AI_ASSISTED_POSSIBLE: 17
- CONNECTOR_ADAPTER_MISSING: 55
- HUMAN_REQUIRED: 5
- TRUE_HIGH_RISK_MANUAL: 13

Thus 72/90 MANUAL cases are candidates in principle for future safe Connector
coverage, but 55 of those still need a specialized adapter. Newly executable
WATCH/MANUAL cases in Suite v1: 0.

The safe Doctor Server health API does not expose the full generated Protocol
catalog. Therefore the 34/30 WATCH groups were not falsely assigned invented
per-Protocol treatability counts.

A repository tool now exists for the full catalog when a protected read path is
available:

tools/audit_protocol_connector_coverage.py

It produces per-Protocol primitives/capabilities/availability/checkpoint/
rollback/verify/human/credential/risk coverage and never changes status.

## Git

Implementation commit:

d343b67 Doctor: harden connector registry and suite gate

The repository was pushed to origin/main.

Untracked __pycache__ directories were not staged, modified or committed.

## Remaining work

One acceptance item remains intentionally partial: full per-Protocol capability
classification of all 171 records, especially the 34/30 WATCH split. Completing
it requires a safe read-only export/API for the generated Protocol catalog on
Doctor Server, or separately authorized access to that server. It must not be
reconstructed from aggregate counts.

No other live Suite regression is currently known.
