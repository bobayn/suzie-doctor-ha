# Suzie Doctor DEV

Experimental developer build of Suzie Doctor for Home Assistant.

## Current development status

Current build versions:

- App: 0.2.49-dev
- Integration bridge: `0.2.5-dev`
- Protocol Pack: `0.1.4-dev`
- Suite: 0.1.3-dev
- Connector Core: 0.1.3-dev
- Skill Core: 0.1.3-dev

The live test installation on Home Assistant OS / Raspberry Pi 5 currently has:

- Ingress UI and persistent SQLite storage in `/data`;
- one canonical Suzie Doctor Connector Core is bundled with the App and exposed through Web and API surface adapters with the same tool contract;
- Connector Core now uses a validated adapter/capability registry; unavailable future families are explicit stubs with reasons rather than fake tools or shell fallbacks;
- capability metadata distinguishes availability from permission and carries risk, exact-target policy, confirmation class, checkpoint and rollback support;
- one canonical versioned Suzie Doctor Skill Core is bundled with the App; it contains operating methodology, not the proprietary Master Knowledge Base;\n- Doctor Server can route an exact-client command to the installed App over the existing TLS-pinned/Ed25519 client channel; the App pulls only its own client_id commands and executes them through the same Connector Core, with one command at a time per client and treatment risk is decided by Suzie Doctor through structured risk_assessment; transport/server human-confirmation is not the decision gate;\n- Skill Core includes the shared Case journal lifecycle: get -> atomic claim -> diagnose/treat -> verify -> complete-next, reuse the same real dialog for queued follow-up Cases, and stop on duplicate claim or target conflict;
- Suite compatibility is checked at runtime and blocks all treatment fail-closed while leaving diagnostics available if App/Connector/Skill/Protocol/Bridge/Server-API contracts drift;
- Health Guard, hourly checks, first-run/full/daily audit engine;
- local background-loop error observability for Health Guard, hourly, daily and bridge-watch tasks;
- background status tracks recovery/last-ok state and provider-level metric collection failures instead of leaving stale errors permanently active;
- correct Home Assistant Repair classification;
- continuous Home Assistant recommendation executor monitors active Repairs, persistent notifications and update entities every minute;
- fixable Repairs are executed through the native Home Assistant RepairFlow; empty confirmation steps are accepted automatically, while required credentials/selections/external authorization are never invented;
- every available update entity advertising native INSTALL is queued regardless of category or auto-update preference; installs are global one-at-a-time, backup is requested only when the entity advertises BACKUP, and system updates are serialized across scans;
- persistent notifications are monitored as context, but arbitrary notification text is never converted into a command;
- server-generated Protocols are accepted through the existing pinned-TLS/Ed25519 signed execution-package path and revalidated by the local ProtocolEngine;
- MANUAL generated Protocols are fully curated guidance records: they carry checks/action/verify/rollback to the client but ProtocolEngine can never execute their treatment;
- HAOS DNS treatment is narrowly bounded to the primary IPv4 method=auto interface, requires explicit dns_servers context, creates a backup, verifies internet/DNS state, and retains rollback nameservers;
- generated Protocol primitives include confirmed-Disease gating, HA update discovery/install with backup, config-entry lookup/verified reload, add-on lookup/restart, bounded subsystem reload, full backup checkpoint, config check and controlled Core restart;
- generated-protocol Developer regression executes an in-memory server-style update Protocol through the real ProtocolEngine without touching live Home Assistant;
- controlled generic recovery for config entries with repeat diagnosis;
- native Supervisor mount recovery for confirmed `inactive` mounts with one reload attempt per incident episode and repeat diagnosis;
- Developer mount-recovery regression exercises success, failure and one-attempt guard without touching live mounts or the production Doctor database;
- Developer release gate aggregates safe readonly/trigger/mount/recurrence/retention/recommendation-executor/generated-protocol/server-client regressions, Pack consistency and active background-error checks;
- Developer release gate additionally reports connector_registry, capability_discovery, connector_security, skill_loaded, skill_connector_compatibility, suite_manifest, suite_version_gate and existing_protocol_regression;
- developer adapter contract is documented in docs/CONNECTOR_ADAPTER_CONTRACT.md; Protocol capability coverage tooling/report live under tools/audit_protocol_connector_coverage.py and docs/PROTOCOL_CONNECTOR_COVERAGE_2026-09-19.md;
- one-attempt-per-episode recovery guard;
- recurrence episodes across the daily-audit boundary;
- real targeted Health Guard audits instead of full-audit aliases;
- unfinished-incident persistence across a Doctor App restart;
- deterministic Protocol Engine with no arbitrary shell or `eval`;
- local `protocol_runs` and privacy-bounded telemetry queue;
- retention cleanup covers stale observations, completed protocol runs and age-bounded telemetry while preserving open incidents and unfinished protocol runs;
- the full Incident -> Disease -> 0..N Protocols knowledge model now lives on Suzie Doctor Server, not in the HA client image;
- the client retains only the local four-card Emergency Pack plus the deterministic Protocol Engine;
- each client owns a persistent Ed25519 identity and accepts only TLS-pinned, Ed25519-signed server responses;
- active trial/license can receive short-lived client-bound execution packages; free mode receives diagnosis and written recommendations only;
- server-delivered cards are revalidated locally and can use only the same supported deterministic primitives as local cards;
- legacy local knowledge files are removed from /data on upgrade;
- Protocol Pack loading/validation and Developer Mode self-tests;
- fail-closed scan/applicability matching;
- exact structured trigger matching for triggered-only cards, with confirmed disease events feeding the next root-cause stage;
- active HA Repairs and problem config-entry states are normalized into structured trigger events; inactive/dismissed/healthy records are ignored;
- WATCH-card disease diagnosis wired into daily and targeted audits without treatment;
- disease incidents persist `disease_id` and resolve only after a conclusive negative repeat diagnosis.

## Bootstrap rule

Suzie Doctor bootstrap never restarts Home Assistant Core.

The legacy `auto_restart_core_once` option is retained only for configuration compatibility and is ignored. Any future Core restart may only exist inside an explicit treatment protocol with its own safety rules.

## Protocol Pack status

The four current disease cards remain `WATCH`; none is allowed to auto-treat yet.

Implemented diagnostic primitives:

- `mqtt_probe` — duplicate MQTT client-ID evidence from Mosquitto logs;
- `verify_recorder_write` — functional state-write + Recorder-history verification;
- `read_host_metrics(filesystem_readonly)` — conservative current-boot HAOS host-journal evidence that ignores normal immutable EROFS root/bind-mount noise and requires strong remount/forced-readonly evidence or a mutable data-path write failure;
- `config_entry_state`;
- `reload_config_entry`;
- `wait`;
- `notify_user`.

Current scan policy:

- daily: local Mosquitto duplicate-client-ID, Recorder functional write, and filesystem read-only diagnostics when applicable;
- targeted storage: filesystem read-only diagnostic only;
- the external-Recorder-DB card remains triggered-only, applies only to MySQL/MariaDB/PostgreSQL, and runs only after `DISEASE-RECORDER-WRITE-UNAVAILABLE-001` is confirmed; SQLite systems are fail-closed;
- missing/unknown applicability is fail-closed: the card is skipped rather than guessed;
- diagnostic uncertainty does not close an existing disease incident.

The next product step is to expand safe native repair/recovery coverage and treatment/fallback/rollback only for protocols that have enough evidence to move from `WATCH` to `ACTIVE`.

## Safety properties already enforced

- specialized Home Assistant/Supervisor APIs before privileged access;
- no arbitrary shell primitives;
- no infinite recovery loops;
- repeat diagnosis after recovery/treatment;
- simulated Developer Mode cases are hidden from normal incident/value statistics;
- telemetry contains protocol/system metadata only, not IP addresses, device names, logs, or user content;
- Bridge version is independent from App version to avoid unnecessary Core restart requirements.
- Developer Mode includes a filesystem-readonly regression self-test that runs the production classifier against normal HAOS EROFS lines and strong real-failure examples without touching live host logs or the live database.

Not for public production use yet.
