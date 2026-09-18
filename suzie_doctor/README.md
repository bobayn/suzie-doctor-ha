# Suzie Doctor DEV

Experimental developer build of Suzie Doctor for Home Assistant.

## Current development status

Current build versions:

- App: `0.2.18-dev`
- Integration bridge: `0.2.4-dev`
- Protocol Pack: `0.1.3-dev`

The live test installation on Home Assistant OS / Raspberry Pi 5 currently has:

- Ingress UI and persistent SQLite storage in `/data`;
- Health Guard, hourly checks, first-run/full/daily audit engine;
- correct Home Assistant Repair classification;
- controlled generic recovery for config entries with repeat diagnosis;
- one-attempt-per-episode recovery guard;
- recurrence episodes across the daily-audit boundary;
- real targeted Health Guard audits instead of full-audit aliases;
- unfinished-incident persistence across a Doctor App restart;
- deterministic Protocol Engine with no arbitrary shell or `eval`;
- local `protocol_runs` and privacy-bounded telemetry queue;
- Protocol Pack loading/validation and Developer Mode self-tests;
- fail-closed scan/applicability matching;
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
- `read_host_metrics(filesystem_readonly)` — conservative current-boot HAOS host-journal evidence;
- `config_entry_state`;
- `reload_config_entry`;
- `wait`;
- `notify_user`.

Current scan policy:

- daily: local Mosquitto duplicate-client-ID, Recorder functional write, and filesystem read-only diagnostics when applicable;
- targeted storage: filesystem read-only diagnostic only;
- the external-Recorder-DB card remains triggered-only and applies only to MySQL/MariaDB/PostgreSQL, so SQLite systems are not misclassified;
- missing/unknown applicability is fail-closed: the card is skipped rather than guessed;
- diagnostic uncertainty does not close an existing disease incident.

The next product step is to add real trigger matching for triggered-only cards, then expand the treatment/fallback/rollback side only for protocols that have enough evidence to move from `WATCH` to `ACTIVE`.

## Safety properties already enforced

- specialized Home Assistant/Supervisor APIs before privileged access;
- no arbitrary shell primitives;
- no infinite recovery loops;
- repeat diagnosis after recovery/treatment;
- simulated Developer Mode cases are hidden from normal incident/value statistics;
- telemetry contains protocol/system metadata only, not IP addresses, device names, logs, or user content;
- Bridge version is independent from App version to avoid unnecessary Core restart requirements.

Not for public production use yet.
