# Suzie Doctor DEV

Experimental developer build of Suzie Doctor for Home Assistant.

## Current development status

Current build versions:

- App: `0.2.32-dev`
- Integration bridge: `0.2.4-dev`
- Protocol Pack: `0.1.4-dev`

The live test installation on Home Assistant OS / Raspberry Pi 5 currently has:

- Ingress UI and persistent SQLite storage in `/data`;
- Health Guard, hourly checks, first-run/full/daily audit engine;
- local background-loop error observability for Health Guard, hourly, daily and bridge-watch tasks;
- background status tracks recovery/last-ok state and provider-level metric collection failures instead of leaving stale errors permanently active;
- correct Home Assistant Repair classification;
- continuous Home Assistant recommendation executor monitors active Repairs, persistent notifications and update entities every minute;
- fixable Repairs are executed through the native Home Assistant RepairFlow; empty confirmation steps are accepted automatically, while required credentials/selections/external authorization are never invented;
- available update entities are installed through native update.install with backup=true; updates already in progress are never replayed and system updates are serialized across scans;
- persistent notifications are monitored as context, but arbitrary notification text is never converted into a command;
- controlled generic recovery for config entries with repeat diagnosis;
- native Supervisor mount recovery for confirmed `inactive` mounts with one reload attempt per incident episode and repeat diagnosis;
- Developer mount-recovery regression exercises success, failure and one-attempt guard without touching live mounts or the production Doctor database;
- Developer release gate aggregates safe readonly/trigger/mount/recurrence/retention/recommendation-executor/server-client regressions, Pack consistency and active background-error checks;
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
