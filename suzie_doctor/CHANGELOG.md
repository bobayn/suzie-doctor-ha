# Changelog

## 0.2.33-dev
- Extended ProtocolEngine for server-generated Protocol Factory cards while preserving the deterministic primitive allowlist and signed execution-package validation.
- Added confirmed_disease, update_state/install_update, config_entry_info/reload_config_entry_verified, addon_info/restart_addon, reload_subsystem, create_backup, check_config and restart_core primitives.
- install_update always requests backup=true and verifies the update entity converges; config-entry reload/add-on restart/Core restart verify recovery before returning success.
- Core restart Protocols remain confirmation-gated and the normal Doctor bootstrap still never restarts Core.
- Confirmed Disease server consultations now inject a local disease_confirmed/disease_id execution context, so server cards cannot self-assert confirmation.
- Added a four-case generated-protocol regression to the Developer release gate. The test uses fake HA/Supervisor providers and never performs live actions.
- App 0.2.33-dev; Protocol Pack remains 0.1.4-dev; Bridge remains 0.2.4-dev.

## 0.2.32-dev
- Added the Home Assistant Recommendation Executor as a continuous one-minute background loop.
- Doctor now monitors active Home Assistant Repairs, persistent notifications, and all available update entities without waiting for the user to open Settings.
- Fixable Repairs are executed through the native Home Assistant RepairFlow. Empty/confirm forms are submitted automatically; flows that require credentials, choices, menus, OAuth/external steps, or other real user input stop as NEEDS_INPUT instead of inventing values.
- Available update entities are installed through native update.install with backup=true, matching the UI backed-up update path. In-progress updates are not replayed; system-level updates are serialized so Core/OS/Supervisor restart boundaries do not trigger a burst of subsequent updates.
- Persistent notification text is monitor-only unless a dedicated structured adapter exists; arbitrary text can never become a service/shell command.
- Added bounded post-action verification, temporary deferral after failures/required-input flows, dashboard recommendation status, and a six-case simulated recommendation regression suite.
- Added the recommendation regression to the Developer release gate. No Home Assistant Core restart is part of this app deployment.
- App 0.2.32-dev; Protocol Pack remains 0.1.4-dev; Bridge remains 0.2.4-dev.

## 0.2.31-dev
- Moved the Master Knowledge Base out of the Home Assistant client and onto Suzie Doctor Server on Orange Pi 4 Pro.
- The client image no longer contains the 411-incident forum corpus or KnowledgeCompiler; the four-card Protocol Pack remains as the local Emergency Pack.
- Added a mutually authenticated application protocol: each Doctor client generates its own Ed25519 identity, signs every request, verifies the pinned TLS server and verifies Ed25519-signed server responses.
- Added trial/license gating on the server. Active trial/license may receive short-lived client-bound execution packages; free mode receives diagnosis/recommendations only.
- Server execution packages contain no source_evidence and still pass the local ProtocolEngine schema, primitive allowlist, trust-mode and diagnosis/verification gates.
- Confirmed local Disease findings are automatically consulted with Doctor Server; unknown generic findings are sent as structured evidence for candidates/recommendations only.
- Added signed Doctor Server client regression to the release gate and legacy local-KB cleanup from /data.
- App 0.2.31-dev; Protocol Pack remains 0.1.4-dev; Bridge remains 0.2.4-dev.

## 0.2.30-dev
- Added the canonical Knowledge model: Incident -> Disease -> 0..N Protocols.
- Added deterministic KnowledgeCompiler with conservative root-cause grouping, unclassified incident pool, candidate clusters, shared cross-disease diagnostic playbooks, and protocol candidates.
- Bundled the v2.7 compiler projection with all 411 forum incidents and all 142 candidate recipes; the full external corpus remains the source of global diagnostic/DON'T-DO/pattern knowledge.
- Added runtime /data/forum_knowledge_base.json override, five-minute change watcher, compiled output at /data/compiled_knowledge.json, and atomic Developer import endpoint.
- Protocol Engine now permits multiple unique protocol IDs for one disease ID; duplicate protocol IDs remain rejected.
- Added Disease schema v1 and Developer knowledge-compiler regression; release gate now includes the new suite.
- Evidence gaps remain warnings rather than invented diagnoses: v2.7 currently exposes five orphan recipe references for later source repair.
- No new treatment is promoted to ACTIVE by the compiler. Protocol Pack stays 0.1.4-dev; Bridge stays 0.2.4-dev.

## 0.2.29-dev
- Completed local retention coverage: stale observations, finished protocol runs and telemetry older than the configured history window are cleaned together with existing samples/audits/resolved incidents.
- Open incidents and unfinished protocol runs are explicitly preserved regardless of age. Unsent telemetry is still age-bounded to keep the documented privacy-bounded queue finite.
- Database cleanup now returns per-table removal counts and logs only non-zero cleanup activity.
- Added isolated POST /api/dev/test/retention regression with nine cases and included it in the Developer release gate.
- Protocol Pack stays 0.1.4-dev; Bridge stays 0.2.4-dev.

## 0.2.28-dev
- Added Developer release gate POST /api/dev/test/release-gate and UI button.
- Gate aggregates filesystem-readonly, trigger-matching, mount-recovery and recurrence suites, validates Protocol Pack version/primitive compatibility, and requires no active background errors.
- The gate intentionally does not replace the separate real daily audit live verification.
- Protocol Pack stays 0.1.4-dev; Bridge stays 0.2.4-dev.

## 0.2.27-dev
- Expanded self-observability with background_status state/recovery timestamps while retaining background_errors as the active-error subset.
- Supervisor core_stats/host_info collection failures and HA bridge availability checks are now recorded instead of silently degrading metric coverage.
- Health Guard successful iterations clear active error state while retaining bounded error history; notification and anomaly-audit failures are also observable.
- Protocol Pack stays 0.1.4-dev; Bridge stays 0.2.4-dev.

## 0.2.26-dev
- Added local background-loop error observability: Health Guard fast/normal collectors, hourly audit, daily audit, and bridge-watch exceptions are no longer silently swallowed.
- Background failures are written to the Doctor container log and summarized in dashboard.background_errors with count, last timestamp and bounded error text.
- No external telemetry or new automatic treatment is introduced. Protocol Pack stays 0.1.4-dev; Bridge stays 0.2.4-dev.

## 0.2.25-dev
- Fixed Developer regression routing from 0.2.24-dev: bridge-trigger normalization cases now belong to POST /api/dev/test/triggers, not the filesystem-readonly suite.
- Filesystem-readonly regression remains exactly seven cases; trigger regression is now ten cases including active Repair, problem config-entry, and ignored historical/dismissed/healthy bridge records.
- Production bridge-trigger wiring from 0.2.24-dev is unchanged. Protocol Pack stays 0.1.4-dev; Bridge stays 0.2.4-dev.

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
