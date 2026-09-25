## Self-reproducing treatment knowledge loop — 2026-09-25

- Wilson `NIGHTLY_RESEARCH` is now treatment-incident mining, not general news/research: every reviewed external incident must become an executable EXPERIMENTAL candidate or carry a structured rejection reason.
- New Wilson knowledge-loop contract v2 requires `search_coverage`, `incident_reviews`, and governed `protocol_candidates`; prose-only completion is rejected.
- External evidence may create/strengthen a 0/3 candidate but never earns validation credit.
- Verified Field one-shot evidence must be converted to a candidate or explicitly rejected; it can no longer disappear into a prose Wilson summary.
- Candidates must include an embedded draft Disease with diagnostic criteria and must compile to a complete machine treatment through Protocol Factory.
- Experimental candidates may execute against their draft Disease before Master KB publication; this does not publish the Disease or grant Family Doctor authority.
- `VALIDATE_FIRST` is now enforced: once Field independently confirms Disease/applicability and decides `PROCEED`, the selected Experimental candidate must be attempted first unless it is concretely unavailable/blocked.
- Three independent internal verified successes still progress 1/3 -> 2/3 -> 3/3 -> publication review; no candidate becomes ACTIVE directly.

## Doctor Call Lab accessibility hardening — 2026-09-25

- Dedicated Doctor Chromium identity now follows its unique user-data profile across Chromium helper/renderer PIDs instead of requiring exact `browser.pid` ownership for X11/AT-SPI targets.
- X11 window selection prefers the dedicated browser process set, with deterministic active-window/title fallback when more than one matching window exists.
- Accessibility fallback now runs in a separate spawned process under the global transport lock; a hard 65-second deadline terminates a stuck AT-SPI worker and releases the lock for subsequent jobs.
- Accessibility progress is relayed back to Call Lab through IPC, preserving `tab_created/filled/send_ready/submitted/failed` states while keeping hung workers killable.

## Doctor pending dispatch handoff — 2026-09-25

- A live Call Lab job that remains in `trigger_received/tab_created/filled/send_ready` at the dispatcher deadline is handed off to recovery instead of being failed and requeued.
- The same persisted `dispatch_job_id` remains authoritative until submission, explicit failure, or bounded stale timeout.
- This closes the remaining retry-storm path where the original dispatcher could requeue a Case while recovery was correctly following the still-live Call Lab job.

## Doctor persistent pending Web dispatch — 2026-09-25

- Call Lab `dispatch_job_id` is persisted immediately after `/api/run`, before UI submission is confirmed.
- Recovery now polls the same pending Call Lab job through `trigger_received/tab_created/filled/send_ready` instead of treating it as an unsent dispatch and creating another job.
- Pending Call Lab jobs have a bounded stale timeout; only explicit `failed` or stale expiry requeues the Case.
- This prevents accessibility-lock backlog and duplicate prompt storms when Web fallback is slow.

## Doctor related Repair semantic resolver — 2026-09-25

- Runtime/HA findings that reference a Repair by an old transient `repair:<domain>:<issue_id>` key now resolve to the active semantic Repair resolution with the same domain/issue_id when available.
- Related House findings reuse the canonical Repair evidence and functional criterion from Patient Journal instead of reconstructing an issue-id-only verify rule.
- This prevents direct Repair and related runtime/mount findings from spawning parallel Field Cases after Repair identity migration.

## Doctor Call Lab accessibility fallback — 2026-09-25

- Doctor jobs without required app/plugin selections now fall back once from failed CDP submission to the independent X11/AT-SPI accessibility transport.
- The failed CDP tab is closed before fallback, preventing duplicate tabs and preserving one job/one prompt semantics.
- App-aware jobs remain fail-closed rather than silently losing their required app selections.

## Doctor Call Lab trusted keyboard fallback — 2026-09-25

- Submission verification no longer mistakes prompt text still present in the composer for a sent user message.
- If trusted CDP mouse input leaves the original composer unchanged for two seconds and no conversation/user-message exists, Call Lab performs one trusted CDP Enter fallback after refocusing the composer.
- The fallback is bounded and only runs with explicit evidence that the first send did not occur, avoiding duplicate Doctor prompts.

## Doctor Call Lab trusted send input — 2026-09-25

- Send now uses browser-native CDP `Input.dispatchMouseEvent` on the resolved Send-button coordinates instead of DOM `.click()`.
- This prevents React composer text from being cleared without an actual ChatGPT submission on project-root Doctor tabs.

## Doctor stale STARTING dispatch recovery — 2026-09-25

- A Field Web session stuck in `STARTING/DISPATCHING` without `ui_sent` is now recovered after a bounded 45-second start timeout.
- Recovery uses the normal `fail_dispatch` path, releases the Field reservation, records an audit event, and retries through persistent backoff instead of leaving a Case stranded forever.

## Doctor Call Lab submission confirmation — 2026-09-25

- CDP submission verification now accepts a released composer plus creation of a new ChatGPT `/c/` conversation as reliable submission evidence, even before the user-message DOM has hydrated.
- Failed submissions still close their orphan tab; successful submissions are no longer falsely marked failed and prematurely closed just because the full prompt text is not immediately visible in the page body.

## Doctor retirement active-resolution guard — 2026-09-25

- Superseded Repair retirement now excludes any Case that is still the current Field Case of a non-RESOLVED resolution.
- Historical links to older resolved fingerprints can no longer retire an actively treated semantic Repair Case.

## Doctor superseded Repair Case retirement — 2026-09-25

- Repair reconciliation now retires non-terminal legacy Field Cases whose Repair resolution is already `RESOLVED/superseded`, provided no active state-changing Doctor command exists.
- Retired Cases close as `CANCELLED` with audit evidence, linked Field queue entries are cancelled, stale resolution pointers are cleared, and any remaining Web dialog is closed.
- This prevents pre-semantic-identity Repair Cases from continuing to consume Field capacity after the canonical Repair moved to a newer semantic resolution.

## Doctor semantic Repair contract — 2026-09-25

- Canonical Skill now includes `mount.reload`, semantic Repair identity, and the rule that a rotated HA `issue_id` is not resolution when the same semantic Repair remains active.
- Field action policy rejections that occur before a signed package are explicitly not treatment attempts; Field must correct the request shape and may retry safely.
- Field dispatch prompt now requires advertised rollback/checkpoint policy and semantic Repair verification.

## Doctor semantic Repair attestation and active-Field dedup — 2026-09-25

- Server-side Repair SUCCESS attestation now matches semantic Repair identity rather than trusting transient issue IDs.
- Repeated active Repair events are deduplicated directly against a live `current_field_case_id`; they no longer create fresh House jobs while a Field Case is already working the same semantic problem.
- Resolution ingestion preserves a valid live Field pointer but clears stale/nonexistent pointers.

## Doctor stable Repair identity — 2026-09-25

- Active Home Assistant Repairs now use a semantic problem identity when HA exposes stable object placeholders such as `reference`, `entry_id`, `device_id`, `entity_id` or `slug`; otherwise the existing issue-id identity remains the fallback.
- `ha_repair_absent` now verifies semantic identity, so a Repair cannot falsely PASS merely because Home Assistant rotated its `issue_id` while the same underlying fault remains active.
- Repair reconciliation preserves a live Field Case pointer across repeated events for the same semantic problem while clearing stale/nonexistent pointers.
- Audit and deterministic recommendation bookkeeping use the same identity helper as Field verification.

## Doctor signed-attempt semantics — 2026-09-25

- Field no-repeat and cooldown now count only requests that actually received a signed execution package.
- Preflight/policy rejection remains audited but no longer poisons the Case as a treatment attempt.
- Future Patient Cards add `do_not_repeat` only for signed Field actions that reached functional `VERIFIED_FAIL`.

## Doctor merged Field resolution targeting — 2026-09-25

- Merged/related Field Cases now resolve the canonical terminal Repair via `canonical_resolution_fingerprint` or `active_repair.problem_key`, independent of which related House queue is encountered first.
- `DISPATCHED`, `VERIFYING`, and Field completion all update the same canonical Repair resolution after Field dedup.
- Isolated V2 migration tests remain compatible when the legacy `doctor_cases` table is absent.

## Doctor terminal Repair Field dedup — 2026-09-25

- One unresolved terminal Repair resolution now owns at most one open Field Case.
- Related House findings (`ha_error`, mount evidence, etc.) that structurally reference an existing Repair are merged into the canonical Field Case instead of spawning parallel Field doctors.
- Fresh House evidence, capabilities and do-not-repeat facts are appended to `related_house_updates[]` while the primary Case identity stays stable.
- Resolution pointers preserve a live Field Case across repeated House `DISPATCH_SUZIE` decisions.

## Doctor disruptive verify hardening and mount reload — 2026-09-25

- `core.restart` and `host.reboot` now treat clean POST acceptance and transport loss as non-terminal disruptive execution and enter persisted deferred functional verification.
- Deferred verify uses a bounded grace window with throttled rechecks before final `VERIFIED_FAIL`; the state-changing action is never reissued while the command remains claimed.
- Supervisor distinguishes explicit HTTP rejection from ambiguous timeout/connection loss for disruptive POSTs.
- Added structured `mount.reload` Field action backed by the existing Supervisor mount reload API, with exact mount name, one attempt and cooldown.

## Doctor mount reload Field action — 2026-09-25

- Added structured mount.reload via Supervisor mount reload with exact mount-name binding.
- App 0.2.68-dev / Suite 0.1.12-dev / Connector 0.1.7-dev.

## Doctor disruptive action deferred verify — 2026-09-25

- Disconnect-tolerant Field actions now defer functional verification after transport timeout/loss instead of becoming immediate FAIL or retrying.
- core.restart uses the persisted verify-only resume path; compatibility action strings are normalized to the canonical structured Field action.

## Doctor missing Web dialog recovery — 2026-09-25

- Active Web Field Cases are reconciled against live Chromium dialog tabs every 15 seconds.
- Missing dialog + stale heartbeat (>90 s) requeues the Case with backoff when no Field mutation command is active.
- A queued/claimed `doctor.action.request` protects the Case from UI-loss requeue so restart/reboot resume cannot be duplicated.
- Exhausted stale/missing-dialog recovery now ends as machine `FAILED` evidence, never `HUMAN_REQUIRED`; terminal Repairs remain unresolved and re-enter reconciliation.

## Doctor Web transport backpressure — 2026-09-25

- Failed Call Lab CDP submissions now close their newly-created orphan Chromium tab instead of leaking it.
- Legacy live Call Lab plugin-picker fixes were synchronized back into canonical source before the cleanup change.
- Field Web dispatch now persists `dispatch_failures` and `dispatch_retry_after`; retries use bounded exponential backoff and successful dispatch resets the state.
- This prevents retry storms from multiplying tabs and driving the dedicated Doctor Chromium into OOM/CDP failure.

## Doctor server fingerprint guard — 2026-09-25

- Doctor Server now prefers the primary non-Repair `evidence.problem_key` for Patient Journal fingerprinting, even when an older client mistakenly promotes a related Repair key to the top level.
- This prevents unrelated runtime findings from occupying a Repair fingerprint and blocking canonical Repair reconciliation.

## Doctor House fairness and fingerprint ownership — 2026-09-25

- Primary findings now retain their own `problem_key`; related Repairs are context only and cannot hijack deduplication fingerprints.
- House scheduler persists `scheduler_yield_until` / `scheduler_yield_count` and enforces a two-session decision quantum when other House work is waiting.
- Over-budget legacy House dialogs are recovered after restart, yielding the slot without rewriting prior House decisions.
- Yielded House jobs automatically return after cooldown, preventing one stuck analysis from starving other unresolved Repairs.

## Doctor House priority preemption — 2026-09-25

- At a 10-minute House session boundary, a claimed lower-priority House job yields the single House slot when a strictly higher-priority waiting job exists.
- The preempted job returns to WAITING, its completed dialog/session history is preserved, and it is redispatched later; no House decision is rewritten.
- This prevents long low-priority investigations from starving terminal Repairs and other higher-priority Doctor work.

## Doctor stale-surface compatibility transport — 2026-09-25

- Field Web sessions with cached MCP schemas can use existing `doctor.diagnose` with `execute=true` and `evidence.field_action_request`; Doctor MCP maps it to the same canonical `doctor.action.request` Core path.
- The compatibility adapter shares one implementation with the direct tool and preserves exact Case/client binding, risk assessment, signed one-shot policy, single mutation and mandatory functional verify.
- Field dispatch explicitly treats a missing new tool name in a stale Web schema as transport staleness, not `MISSING_CAPABILITY`.

## Doctor resolution pointer cleanup — 2026-09-25

- `current_field_case_id` now tracks only an active Field Case and is cleared on House re-evaluation and Field completion.
- Historical Case linkage remains in the immutable House/Field journals; resolution state no longer carries stale active pointers.

## Doctor Field action policy cleanup — 2026-09-25

- Field one-shot Server allowlist now exactly matches the four published actions; hidden `subsystem.reload` was removed from Field policy while remaining available to normal Protocols.
- `host.reboot` now validates that the exact target is the local HAOS host.

## Doctor HUMAN gate patch — 2026-09-25

- `MISSING_CAPABILITY` now requires exact machine-readable `human_requirement.capability` in both House and Field completion paths.
- Free-text action descriptions can no longer bypass current Field capability checks such as `core.restart`.

## Doctor runtime patch — 2026-09-25

- Field `doctor.diagnose` is now always bound to its active Case as `FIELD_CASE_DIAGNOSTIC` unless it is an Experimental validation request.
- A Field diagnostic `NO_MATCH` stays inside the current Case and cannot recursively auto-escalate into duplicate Field Cases.
- Regression coverage now checks single-mutation serialization and negative functional verification.
- Doctor V2 now releases stale reserved House/Wilson slots left BUSY before a WAITING job reached CLAIMED, so server restarts cannot wedge dispatch.

## 0.2.65-dev / Suite 0.1.11-dev — 2026-09-24

- Completed the House-dispatched Field `doctor.action.request` end-to-end path without requiring a known Disease/Protocol.
- Added semantic Field actions (`integration.reload`, `addon.restart`, `core.restart`, `host.reboot`) with exact-target, blast-radius, attempt/cooldown and owner-prohibition gates.
- Added command execution states and reboot-safe verify-only resume; disruptive actions are not replayed after connection loss.
- Added `ha_repair_absent` mandatory functional verify and one-shot evidence for Wilson; one success does not promote a Protocol.
- Added terminal Repair resolution lifecycle and exact-client repair reconciliation, including expiring/re-evaluated WAITING_HUMAN.
- HUMAN decisions now require PHYSICAL_ACTION/CREDENTIAL/OAUTH/MISSING_CAPABILITY with a concrete reason; missing capability is rejected when current Field capabilities provide it.
- Field Cases now preserve Patient Card/trigger/problem/Repair/House/attempt/do-not-repeat/Experimental context.

## 0.2.63-dev / Suite 0.1.9-dev — 2026-09-23

- Active Home Assistant Repairs are now terminal Doctor tasks regardless of HA warning/error severity.
- Deterministic repair remains first-line; unresolved/non-fixable Repairs trigger immediate smart Patient Journal -> House routing from the minute recommendation loop.
- House cannot OBSERVE/RECHECK/IGNORE an active terminal Repair; it must dispatch Field or require owner action.
- Active Repair House jobs are priority 85 and fingerprint-deduplicated while pending/in Field/awaiting owner action.
- Field SUCCESS/RESOLVED is fail-closed unless an attested `ha.repairs.list` command proves the exact Repair is absent.

## 0.2.62-dev — 2026-09-23

- Adds explicit ProtocolEngine postcondition verification with `verify.success_when=conditions`.
- Execution results now expose `verify_performed` and `verify_passed`.
- Experimental SUCCESS/PASS evidence is rejected unless the exact signed execution attests both `verify_performed=true` and `verify_passed=true`.
- Field self-report can no longer manufacture a successful 1/3 validation without signed functional verification.
- Canonical Skill updated and hash refreshed.

## Experimental Wilson evidence guard — 2026-09-23

- Wilson validation jobs are created only from Server-normalized `VALIDATE_FIRST` Field results bound to the exact protocol and `episode_key=field:<case_id>`.
- A normal Field Case cannot create Experimental validation evidence merely by self-reporting an `experimental_validation` object.
- Prevents duplicate/non-authorized Cases from contaminating Wilson progression.

## 0.2.61-dev — 2026-09-23

- Handles `FIELD_EXPERIMENTAL_VALIDATION` before normal published-Disease matching, so an unpublished Experimental candidate cannot fall through to ordinary `NO_MATCH`/auto-escalation.
- Exact House-selected Case/candidate authorization is checked before Experimental consultation.
- `candidate_disease_id` remains a hypothesis; a signed Experimental package is returned only after Field independently supplies the matching `confirmed_disease_id`.
- Prevents Experimental consultation from creating a duplicate Field Case.
- Clarified the canonical Skill workflow and refreshed its content hash.

## Experimental matcher token hardening — 2026-09-23

- Candidate matching tokenizes structured values only, not JSON field names.
- Common transport/log words such as `evidence`, `message`, `source`, `error`, `this` and related stopwords no longer create Experimental matches.
- Added a frontend-error regression proving a candidate cannot match on generic schema/log vocabulary.

## House priority from current event severity — 2026-09-23

- Doctor V2 now derives House queue priority from the current event severity instead of assigning every Patient Card priority 50.
- CRITICAL/RED events pre-empt ordinary backlog; HIGH/PROBLEM/WARNING are ordered above LOW/unknown observations.
- Priority is routing metadata only; it does not upgrade diagnosis significance or force Field dispatch.

## Experimental matching anchor hardening — 2026-09-23

- Experimental candidate matching now requires an anchor in the current House trigger/current explicit Disease context.
- Recent patient history may increase confidence but can no longer create a match by itself.
- Prevents a prior Experimental validation event from making the same candidate appear on unrelated later Patient Cards.

## House Experimental transport normalization — 2026-09-23

- Normalizes an explicit House `MATCHING_VALIDATION_TARGET` candidate review into canonical `house_directive=VALIDATE_FIRST`, `experimental_protocol_id`, and validation stage.
- A House `DISPATCH_SUZIE` with matched Experimental candidates can no longer silently ignore those candidates: it must either issue `VALIDATE_FIRST` or explicitly decline Experimental validation with a reason.
- This closes the real Web E2E gap where House recognized the 0/3 candidate but the Field Case was created without the validation directive.

## Doctor Server v2 dispatch recovery — 2026-09-23

- Normalizes legacy/non-numeric Wilson FIELD_CASE_REPORTS cursors such as `E2E:60` instead of crashing the v2 dispatch loop.
- Wilson validation-only jobs no longer overwrite the global Field Case cursor with arbitrary model output cursors.
- Automatically requeues stranded CLAIMED House/Wilson jobs when their dialog is already closed and frees the reserved role slot.
- Added regression coverage so Experimental matching cannot be starved by a dead House slot.

## 0.2.60-dev — 2026-09-23

- Fixes Suite bundle compatibility after Experimental validation work: Connector Core remains 0.1.3-dev because the canonical tool interface did not change; only Doctor semantics inside the existing evidence contract changed.
- Keeps Suite 0.1.8-dev and the new Experimental validation architecture while restoring fail-closed manifest/contract version parity.

## 0.2.59-dev / Suite 0.1.8-dev — 2026-09-23

- Closed the Experimental Protocol validation loop: Server matching -> Patient Card -> House VALIDATE_FIRST -> exact Field Case -> signed EXPERIMENTAL treatment -> verify -> Wilson evidence -> 0/3..3/3 -> publication review.
- Experimental matches never auto-dispatch; House dispatch remains semantic and requires the Patient Card to merit Field investigation independently.
- Added Field-only EXPERIMENTAL execution status and exact Case/candidate authorization; Family Doctor cannot execute Experimental packages.
- VALIDATE_FIRST Cases cannot close without independent diagnosis/applicability and structured Experimental validation evidence. Failed/inapplicable/unsafe validation must continue Case diagnosis.
- Attempted validation is attested against the exact-client doctor.diagnose command journal.
- Wilson receives required positive/negative Field evidence, cannot rewrite Field success/verify facts, can suspend/revise candidates, and 3/3 enters publication review without auto-ACTIVE.

## 0.2.58-dev — 2026-09-22

- Customer Journal now starts at feature introduction instead of retroactively turning historical RESOLVED technical rows into Family Doctor achievements.
- Removes the temporary 0.2.57 historical backfill entries on startup.
- Filters controlled Web E2E and migration smoke House events using the durable event source/fingerprint and controlled markers.

## 0.2.57-dev — 2026-09-22

- Reworked the customer-facing Incidents tab into a Customer Journal. Raw technical OPEN/error rows no longer present themselves as unresolved customer problems.
- Family Doctor writes positive verified outcomes only after repeat verification; self-recovery is described separately from Doctor-applied treatment.
- House decisions are exposed through a signed client-bound customer feed and rendered as observe/recheck/no-action/deeper-check/human-action states without exposing internal reasoning as a diagnosis.
- Home dashboard now reports reviewed, restored/verified and owner-attention counts instead of raw found/fixed/open incident counters.
- Technical incidents and audits remain available under expandable diagnostic details.
- Added Customer Journal schema v4, safe backfill for resolved non-simulated incidents, HTML escaping, and CI regression assertions.

## 0.2.55-dev

- Mount-recovery regression now separately covers an accepted reload that stays inactive and a Supervisor-rejected reload with a preserved reason.
- Runtime behavior from 0.2.54-dev is unchanged.

## 0.2.56-dev / Suite 0.1.7-dev / Skill 0.1.7-dev — 2026-09-21

- Introduced Doctor architecture v2 role semantics: Family Doctor, House, Field Suzie and Wilson.
- Added machine-readable 4+1+1 AI role quota and 10/10 Web session/dialog policy to Suite manifest/status.
- Registered canonical House and Wilson ChatGPT Project targets.
- Split treatment authorization by trusted execution actor:
  - Family Doctor may run signed published ACTIVE deterministic treatment under trust-mode/automation-class gates without waking Field AI.
  - Field Suzie still requires contextual risk assessment for state-changing treatment.
- Confirmed Disease audit path now requests the Family Doctor protocol path; unknown/generic findings are marked for Patient Journal + House review.
- Updated canonical Suzie Skill: Field role, House dispatch, Wilson governance, 3/3 new-Protocol admission, no next-Case reuse after natural dialog completion.
- Added regression checks for Family Doctor AUTO_SAFE/CONFIRM_REQUIRED behavior.
- Existing ACTIVE/WATCH/MANUAL knowledge is grandfathered; no retroactive 3/3 migration.
- Central Doctor Server migration is specified in `docs/DOCTOR_ARCHITECTURE_V2_2026-09-21.md` and must be deployed before this branch is production-ready.

## 0.2.54-dev

- Supervisor mount recovery records the exact Home Assistant Repair-equivalent action (`supervisor.mount.reload`).
- Rejected mount reloads preserve the Supervisor status/error instead of collapsing to a bare false result.
- This makes it explicit when local Doctor already pulled HA's safe Reload thread and why it failed.
- Existing one-attempt-per-incident-episode bound is unchanged; destructive actions remain excluded.
- Suite 0.1.6-dev; Skill Core 0.1.6-dev; Connector Core 0.1.3-dev; Bridge 0.2.5-dev.

## 0.2.53-dev

- Skill Core 0.1.6-dev makes reversibility an explicit treatment-order invariant.
- Safe retry/reload/restart/reboot must be considered before destructive repair when relevant.
- Delete/wipe/reset/remove/recreate operations are never generic recovery fallbacks.
- Destructive treatment requires an exact signed Protocol path, checkpoint/recovery path and normal autonomous-risk/owner-prohibition gates.
- Suite 0.1.6-dev; Connector Core 0.1.3-dev; Bridge 0.2.5-dev.

## 0.2.52-dev

- Incident UI renders persisted UTC timestamps in `Europe/Kyiv` local time.
- Incident rows show both opened and last-updated times.
- Incident list is ordered by `updated_at` (last activity), with `opened_at` as a stable secondary key.
- Audit rows show localized started/finished times.
- Recurrence/episode semantics and incident persistence logic are unchanged.
- Suite remains 0.1.5-dev; Skill Core 0.1.5-dev; Connector Core 0.1.3-dev; Bridge 0.2.5-dev.

## 0.2.51-dev

- Skill Core 0.1.5-dev adds a mandatory Restart / Reboot Fallback Before Human Escalation.
- When targeted treatment is unavailable or has failed, Suzie Doctor must evaluate the narrowest safe restart level that can actually affect the failed subsystem before returning HUMAN_REQUIRED.
- The fallback ladder is exact-target retry/reload -> affected integration/service/add-on restart -> Home Assistant Core restart when Core-scoped -> HAOS/host reboot for Supervisor/mount/host-level failures when Core restart is insufficient.
- Restart/reboot remains gated by exact scope, owner prohibitions, live contraindications, autonomous risk assessment, bounded attempts/cooldown and mandatory verification of the original functional criterion.
- Bootstrap/update still never restarts Home Assistant Core.
- App 0.2.51-dev; Suite 0.1.5-dev; Connector Core 0.1.3-dev; Bridge 0.2.5-dev; Protocol Pack 0.1.4-dev.

## 0.2.50-dev

- Allow different Cases for the same exact Home Assistant client to be claimed and diagnosed concurrently by separate Doctor sessions.
- Keep state-changing exact-client commands serialized by the existing Doctor Server command bridge / installed Connector Core.
- Skill Core 0.1.4-dev makes the concurrency rule explicit and fixes `doctor.case.complete_next` outcomes to the canonical enum: SUCCESS, RESOLVED, HUMAN_REQUIRED, UNSAFE_TO_TREAT, FAILED.
- Doctor Server 0.1.9-dev adds a same-client parallel-claim regression test while preserving same-Case exclusive ownership.
- App 0.2.50-dev; Suite 0.1.4-dev; Connector Core 0.1.3-dev; Bridge 0.2.5-dev.

## 0.2.49-dev

- Classify MCP/tooling and rejected Supervisor API ERROR records as non-actionable observations instead of system incidents.
- Event delivery still wakes the App, but only actionable runtime faults escalate to Doctor Server/Web Suzie.
- Automation/runtime component errors remain actionable.

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
