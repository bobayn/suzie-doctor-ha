# Changelog

## 0.2.24-dev
- Added production structured trigger events from the existing Home Assistant Bridge snapshot: active non-dismissed Repairs and config entries in problem states.
- Repair triggers match the exact translation key (or issue id fallback); config-entry triggers match domain:state.
- Historical inactive Repairs, dismissed Repairs and healthy loaded config entries are excluded from trigger context.
- Extended POST /api/dev/test/triggers to cover the bridge event normalization path; no live changes are performed.
- Protocol Pack stays 0.1.4-dev; Bridge stays 0.2.4-dev.

## 0.2.23-dev
- Added Developer endpoint/UI test POST /api/dev/test/mount-recovery.
- The test runs the production Auditor mount-recovery path against fake Supervisor/HA providers and a temporary SQLite database; live Supervisor, mounts and Doctor /data are not touched.
- Regression cases prove successful reload + repeat state active + incident resolution, failed reload persistence, and the one-attempt-per-episode anti-loop guard.
- Protocol Pack stays 0.1.4-dev; Bridge stays 0.2.4-dev.

## 0.2.22-dev
- Added exact, fail-closed structured trigger matching for triggered Protocol Pack cards.
- The external Recorder DB card now triggers only from a confirmed DISEASE-RECORDER-WRITE-UNAVAILABLE-001 event and still applies only to MySQL/MariaDB/PostgreSQL.
- Daily disease scanning now performs a second triggered-only stage using confirmed disease events from the first stage; triggered cards are overlaid into the same disease summary without duplicate rows.
- Added Developer endpoint/UI test POST /api/dev/test/triggers with seven pure cases; it executes no diagnostics and does not touch the live Recorder database or logs.
- Protocol Pack bumped to 0.1.4-dev; external Recorder protocol card bumped to 0.1.1; Bridge stays 0.2.4-dev.
- Confirmed separately that the stale Home Assistant hassio update entity is a transient Supervisor-integration refresh lag: the entity self-corrected to the installed Doctor version before a config-entry reload was issued.

## 0.2.21-dev
- Added authoritative Supervisor mount-state checks to normal Doctor audits so an `inactive` network mount cannot be missed when the HA Repair registry lags behind.
- Added native recovery via `POST /mounts/<name>/reload`; Doctor never removes or recreates the mount automatically.
- Added one reload attempt per incident episode and mandatory repeat diagnosis against Supervisor mount state.
- Successful repeat diagnosis resolves the mount incident; failed/uncertain recovery stays open for deeper network/Samba diagnosis.
- HA `issue_mount_mount_failed` warnings are deduplicated when Supervisor mount state is available.
- Bridge stays `0.2.4-dev`; Protocol Pack stays `0.1.3-dev`; no Home Assistant Core restart is required.

## 0.2.20-dev
- Extracted the production HAOS filesystem read-only log classifier into a deterministic pure helper used by `read_host_metrics(filesystem_readonly)`.
- Added Developer endpoint `POST /api/dev/test/filesystem-readonly` and a UI button for a seven-case regression suite.
- Regression cases cover normal immutable EROFS lines for `/etc/hosts`, `/etc/hostname`, EROFS root mount, real ext4 remount read-only, mutable `/config` write failure, mixed benign+real failure, and irrelevant read-only text.
- The self-test does not read live host logs and does not touch the live database.
- Protocol Pack stays `0.1.3-dev`; Bridge stays `0.2.4-dev`.

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
