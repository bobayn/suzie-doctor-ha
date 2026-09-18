# Changelog

## 0.2.19-dev
- Fixed HAOS false-positive `DISEASE-STORAGE-READONLY-001` caused by normal immutable EROFS startup lines for `/etc/hosts` and `/etc/hostname`.
- `read_host_metrics(filesystem_readonly)` now requires strong filesystem remount/forced-readonly evidence, or a `Read-only file system` failure on mutable Home Assistant data paths.
- Live verification on HAOS 18.3 / Core 2026.9.3: Recorder write probe healthy, backup fresh, storage ~16.8% used, storage disease NOT_CONFIRMED, prior false-positive incident RESOLVED.
- Bridge remains `0.2.4-dev`; no Home Assistant Core restart was required.

## 0.2.18-dev
- Protocol Pack `0.1.3-dev`: added fail-closed scan/applicability policy without changing primitive set v3.
- Wired WATCH-card diagnosis into production daily and targeted audits; WATCH still cannot execute treatment.
- Added official Home Assistant WebSocket `system_health/info` reader for Recorder applicability without reading `db_url` or secrets.
- Added general Recorder write-path disease card and kept the external-DB disease triggered-only / external-engine-only.
- Disease incidents now carry `disease_id`; conclusive negative diagnosis resolves them, while skipped/failed/uncertain diagnostics leave prior incidents unchanged.
- Added idempotent SQLite schema v3 migration so existing `/data` databases gain the `disease_id` column safely.
- MQTT daily scan applies only when the local `core_mosquitto` App is installed; targeted storage scan selects only the storage card.

## 0.2.17-dev
- Added conservative HAOS current-boot filesystem read-only diagnostic probe.
- Protocol Pack `0.1.2-dev` / primitive set v3: all three starter WATCH cards now have implemented diagnostic primitives.
- Live WATCH-card pass verified MQTT duplicate-ID absent, Recorder write healthy, and filesystem read-only absent.
- Added disease severity metadata to starter cards in preparation for automatic disease incidents.

## 0.2.16-dev
- Added functional Recorder write/history probe using Home Assistant REST APIs.
- Added Mosquitto duplicate client-ID probe from Supervisor App logs.
- Added read-only Protocol Pack diagnostic self-test; WATCH cards never execute treatment.
- Protocol Pack `0.1.1-dev` / primitive set v2.

## 0.2.15-dev
- Added deterministic MVP Protocol Engine.
- Added Protocol Pack inventory/validation.
- Added local protocol-run persistence and privacy-bounded telemetry queue.
- Verified simulated disease protocol end-to-end: diagnose -> confirm -> treatment -> repeat diagnosis -> SUCCESS -> telemetry.

## 0.2.14-dev
- Verified targeted storage audit uses only host/core-stats/backups.
- Added unfinished-incident persistence test across Doctor App restart; live test PASS.
- Clarified the legacy Core-restart bootstrap option is ignored.

## 0.2.13-dev
- Made Health Guard targeted audits actually targeted.
- Added Health Guard incident recovery when a confirmed metric returns to normal.

## 0.2.12-dev
- Limited real generic reload recovery to one attempt per incident episode.
- Recurrence after a daily boundary now requires disease diagnosis instead of repeating generic recovery.
- Added controlled FAILED-treatment test.

## 0.2.11-dev
- Implemented recurrence semantics:
  - before next daily audit: same incident episode;
  - after daily audit boundary: new linked recurrence episode.
- Added isolated recurrence self-test using in-memory SQLite.

## 0.2.10-dev
- Added verified safe generic recovery for `setup_error`.
- Corrected documentation: bridge bootstrap never restarts Home Assistant Core.

## 0.2.9-dev
- First controlled end-to-end treatment test:
  `setup_retry -> incident -> reload -> repeat diagnosis -> loaded -> SUCCESS`.
- Added immediate repeat diagnosis after generic recovery.
- Developer simulations no longer pollute normal incident/value statistics.

## 0.2.8-dev
- Fixed Home Assistant Repair classification using active/dismissed/severity semantics.
- Reclassified old false-positive Repair incidents as `DISCARDED`.

## 0.2.7-dev
- Fixed Supervisor discovery registration by using POST discovery and locally retained UUID.

## 0.2.6-dev
- Hard rule: Doctor bootstrap never restarts Home Assistant Core.

## 0.2.0-dev
- First installable developer build.
- App repository layout.
- Automatic bridge bootstrap.
- Supervisor discovery for the bridge.
- First-run audit and Health Guard.
- Three-screen Ingress UI.
