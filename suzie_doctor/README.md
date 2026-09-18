# Suzie Doctor DEV

Experimental developer build of Suzie Doctor for Home Assistant.

## Current development status

Current tested versions:

- App: `0.2.17-dev`
- Integration bridge: `0.2.4-dev`
- Protocol Pack: `0.1.2-dev`

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
- Protocol Pack loading/validation and Developer Mode self-tests.

## Bootstrap rule

Suzie Doctor bootstrap never restarts Home Assistant Core.

The legacy `auto_restart_core_once` option is retained only for configuration compatibility and is ignored. Any future Core restart may only exist inside an explicit treatment protocol with its own safety rules.

## Protocol Pack status

The three starter disease cards remain `WATCH`; none is allowed to auto-treat yet.

Implemented diagnostic primitives:

- `mqtt_probe` — duplicate MQTT client-ID evidence from Mosquitto logs;
- `verify_recorder_write` — functional state-write + Recorder-history verification;
- `read_host_metrics(filesystem_readonly)` — conservative current-boot HAOS host-journal evidence;
- `config_entry_state`;
- `reload_config_entry`;
- `wait`;
- `notify_user`.

The live diagnostic pass currently reports:

- MQTT duplicate client ID: not confirmed;
- Recorder functional write: healthy;
- filesystem read-only: not confirmed.

The next product step is not to activate those cards blindly. First add trigger/applicability matching so a disease card is only considered where its technical applicability is proven; then wire Protocol Pack diagnosis into normal daily/targeted Doctor operation.

## Safety properties already enforced

- specialized Home Assistant/Supervisor APIs before privileged access;
- no arbitrary shell primitives;
- no infinite recovery loops;
- repeat diagnosis after recovery/treatment;
- simulated Developer Mode cases are hidden from normal incident/value statistics;
- telemetry contains protocol/system metadata only, not IP addresses, device names, logs, or user content;
- Bridge version is independent from App version to avoid unnecessary Core restart requirements.

Not for public production use yet.
