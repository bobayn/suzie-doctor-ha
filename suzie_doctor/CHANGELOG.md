## 0.2.48-dev

- Align Release Gate connector-security aggregation with the autonomous treatment policy test name (`autonomous_risk_decision_is_doctor_owned`).
- Test-only aggregation fix; no treatment behavior change.

## 0.2.47-dev

- Coalesce event-driven runtime ERROR evidence with related HA Repair findings before Doctor Server consultation.
- Pass a canonical problem_key so different detection paths can refresh one open Case instead of spawning duplicate doctors.

## 0.2.46-dev

- Fix long-lived Home Assistant WebSocket event subscription through Supervisor by authenticating the proxy request with SUPERVISOR_TOKEN, matching the existing proven one-shot WS path.
- No Bridge/Core restart required; Bridge remains 0.2.5-dev.

## 0.2.45-dev

- Add event-driven Home Assistant runtime ERROR/CRITICAL wake path.
- Bridge 0.2.5-dev forwards bounded ERROR/CRITICAL records as internal `suzie_doctor_error` events.
- Doctor App subscribes continuously, deduplicates repeated fingerprints, runs a bounded targeted audit immediately, records `ha_runtime_error`, and consults Doctor Server for escalation.
- Periodic Health Guard/hourly/daily scans remain as fallback; Doctor self-errors are excluded to prevent loops.

## 0.2.44-dev

- Fix Release Gate incompatible-Suite regression test to supply the new Suzie Doctor autonomous risk assessment before exercising the Suite fail-closed gate.
- No treatment-policy relaxation; this is a test-contract alignment release.

## 0.2.43-dev

- Align Home Assistant UI translations and README with the autonomous Suzie Doctor risk policy; trust_mode no longer describes human approval.
- No treatment-policy change from 0.2.42-dev; Suite/Connector/Skill remain 0.1.3-dev.

## 0.2.42-dev

- Skill Core 0.1.3-dev adds the Autonomous Treatment Principle: Suzie Doctor itself judges harm probability, irreversibility and harm magnitude for each state-changing treatment.
- Suzie Doctor does not execute a treatment it assesses as IRREVERSIBLE + HIGH probability + SUBSTANTIAL/CATASTROPHIC harm; it seeks a safer/reversible path instead.
- CONFIRM_REQUIRED is now legacy metadata for explicit autonomous Doctor risk review, not a human approval gate. full_trust or legacy confirmation cannot replace risk_assessment.
- doctor.diagnose execute=true now carries structured risk_assessment through the same Connector Core on Web and API surfaces.
- Confirmed server protocols discovered by background audit are routed to Suzie Doctor for risk review instead of being locally executed without Doctor judgment.
- Owner absolute prohibitions and hard mechanical invariants remain binding. HUMAN_ACTION_REQUIRED is reserved for physical work, credentials/OAuth, or unavailable capabilities.
- App 0.2.42-dev; Suite/Connector/Skill 0.1.3-dev; Bridge 0.2.4-dev; Emergency Pack 0.1.4-dev.

## 0.2.41-dev

- Added the exact-client Doctor Server command bridge. The installed App polls outbound over the existing TLS-pinned, Ed25519-authenticated server channel; commands are bound to client_id and execute only through ConnectorCore.invoke.
- Command transport never accepts model-supplied human confirmation. Existing ProtocolEngine trust/confirmation gates remain authoritative.
- Added one-command-at-a-time execution per HA client to avoid conflicting parallel mutations.
- Skill Core 0.1.2-dev now defines the Web/API Case journal lifecycle: CASE pointer -> get -> atomic claim -> heartbeat/stage -> treatment/verify -> complete-next; duplicate claims and ambiguous targets stop fail-closed.
- The same real dialog can take another queued Case after completion; dialog_id remains immutable while server-side assignment_seq/dialog_ref advances (-2, -3, ...).
- App 0.2.41-dev; Suite/Connector/Skill 0.1.2-dev; Bridge 0.2.4-dev; Emergency Pack 0.1.4-dev.

# Changelog

## 0.2.40-dev

- Doctor now repairs a stale self-update UI state instead of merely suppressing
  a repeated self-update. When the running App already equals the offered
  latest_version but Home Assistant still reports an older installed_version,
  Recommendation Executor reloads the single exact Supervisor (hassio) config
  entry through Home Assistant's native reload_config_entry service.
- The repair is verified by re-reading update.suzie_doctor_dev_update and is
  successful only when state=off and installed_version=latest_version=APP_VERSION.
- Ambiguous/missing Supervisor config-entry targeting fails closed and never
  falls back to reinstalling the already-running Doctor version.
- Recommendation regression now covers successful state synchronization and
  exact-target refusal.

## 0.2.39-dev

- Prevent repeated self-update cold-backup loops when Home Assistant's
  update.suzie_doctor_dev_update state lags behind Supervisor after a
  successful Doctor App upgrade. For this exact self-update target only, the
  running App version is authoritative when it already equals latest_version.
- A genuinely newer Doctor version remains actionable and all non-Doctor update
  entities retain the broad native-INSTALL owner-intent policy from 0.2.38-dev.
- Recommendation self-test now covers the stale self-update state regression.

## 0.2.38-dev

- Recommendation Executor now treats every available Home Assistant update entity
  advertising native INSTALL as owner intent to install; no update category,
  auto_update setting, or importance allowlist filters it out.
- Update installation is globally serialized: if any update entity reports
  in_progress, Doctor starts no other update. Each accepted update is verified
  before the next one is attempted.
- backup=true is requested only when the entity advertises the native BACKUP
  feature; entities without backup support are still installed.
- Potentially restarting system updates remain one-per-scan so the next scan
  resumes the queue after Home Assistant returns.
- Recommendation self-test now covers owner-intent install, unsupported install
  capability, global in-progress blocking, strict sequential execution, and
  system-update serialization.

## 0.2.37-dev
- Added a real validated Connector adapter/capability registry instead of relying only on a flat tool list.
- Adapter records now carry version, availability/health/reason, permission level, read/write/dangerous operations, confirmation class, checkpoint/rollback support and compatibility metadata.
- Reserved Frigate, Docker, Zigbee2MQTT, Z-Wave, MQTT, ESPHome, Node-RED, storage, Recorder, network, auth and human adapters are explicit unavailable stubs; they cannot fake success or fall back to shell.
- Connector capability records now enforce risk/target policy and preserve checkpoint/rollback metadata; duplicate adapters/capabilities, unknown tools and missing exact targets fail closed.
- Added Connector schema v1 to the Suite manifest/compatibility gate while keeping Connector interface v1 backward compatible.
- Skill Core 0.1.1-dev is bound to Connector interface v1, declares no local Master KB/source evidence, adds explicit recurrence/verify/HUMAN_ACTION_REQUIRED/AI-assisted governance rules, and is packaged by identical Web/API Skill loaders.
- Expanded Developer Suite selftests for registry duplicates, deterministic discovery, unavailable adapters, exact targets, exception propagation, Web/API semantic parity, Skill vocabulary, and incompatible-version diagnosis/treatment behavior.
- Release Gate now exposes connector_registry, capability_discovery, connector_security, skill_loaded, skill_connector_compatibility, suite_manifest, suite_version_gate and existing_protocol_regression checks.
- Existing ProtocolEngine primitive set and signed execution-package treatment path are unchanged; WATCH and MANUAL treatment gates are unchanged.
- App 0.2.37-dev; Suite/Connector/Skill 0.1.1-dev; Bridge 0.2.4-dev; Emergency Pack 0.1.4-dev.

## 0.2.36-dev
- Added Suzie Doctor Suite 0.1.0-dev: one canonical Connector Core plus one canonical Skill Core bundled inside the App image.
- Added surface-neutral Connector interface v1 with Web and API adapters that resolve to the same tool catalog and execution policy.
- Added canonical Skill schema v1 and hash-bound SKILL.md; the Skill contains Doctor operating methodology only and does not contain the proprietary Master Knowledge Base.
- Added Suite manifest compatibility gate covering App, Connector, Skill, Protocol schema/primitive set, Doctor Server API contract, Bridge and Emergency Pack versions.
- ProtocolEngine treatment now fails closed when Suite compatibility is invalid, including under developer override; diagnostic paths remain available.
- Added initial safe Connector tools for Doctor capability/Skill/Suite discovery, HA read-only diagnostics, Supervisor read-only diagnostics, and signed Doctor Server treatment through doctor.diagnose.
- No arbitrary shell/eval/generic write primitive is exposed by Connector Core; doctor.diagnose remains the only treatment-capable Connector tool and keeps the existing signed execution-package path.
- Added Developer Suite regression for manifest/Skill compatibility, unsafe-tool rejection, Web/API parity and incompatibility fail-closed behavior.
- App 0.2.36-dev; Suite/Connector/Skill 0.1.0-dev; Bridge remains 0.2.4-dev; Protocol Pack remains 0.1.4-dev.

## 0.2.35-dev
- Added MANUAL as a first-class generated Protocol status for fully curated treatments that the HA app must not execute (external Docker/NAS/database recovery/credentials/physical work).
- MANUAL cards remain signed, client-bound and diagnosable, expose checks/action/verify/rollback guidance, and are hard-gated from treatment by ProtocolEngine because only ACTIVE may execute.
- Added bounded HAOS network primitives for read-only primary-interface inspection and primary IPv4 auto-mode DNS replacement with post-write verification. Static-address interfaces fail closed.
- Added context-list gating for exact dns_servers/entity_ids supplied by the confirmed diagnosis context.
- Expanded safe registry/config-entry primitives used by curated protocols; no generic shell or arbitrary service primitive was added.
- Added a four-case MANUAL/network fake-runtime regression to the Developer release gate. It performs no live Home Assistant network writes.
- App 0.2.35-dev; Bridge remains 0.2.4-dev; Protocol Pack remains 0.1.4-dev.

## 0.2.34-dev
- Extended the signed Doctor Server client regression to require a real generated Protocol package from the Protocol Factory.
- The suite now validates generated package binding/expiry, nested diagnostics/treatment preservation after server sanitization, and primitive compatibility with the local ProtocolEngine.
- This regression does not execute the generated treatment against live Home Assistant; execution remains covered by the fake-provider generated Protocol regression.
- App 0.2.34-dev; Protocol Pack remains 0.1.4-dev; Bridge remains 0.2.4-dev.

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
