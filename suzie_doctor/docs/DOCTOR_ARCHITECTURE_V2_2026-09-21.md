# Suzie Doctor Architecture v2 — Server Contract
## House / Wilson / Family Doctor / Field Suzie

Date: 2026-09-21
Status: implementation contract for Doctor Server migration.

### Canonical roles

- Family/Local Doctor: local App + Connector + ProtocolEngine. Routine recommendations and published ACTIVE deterministic Protocols.
- Doctor Server: registry, Patient Journal/Card, KB/Protocol store, queues, signed packages, exact-client command bridge, role/session accounting.
- Doctor House: semantic triage/dispatcher. Does not normally get client write tools.
- Suzie Field Doctor: complex live diagnosis/treatment after House dispatch.
- Doctor Wilson: scientific/knowledge role; hourly internal review + nightly external research.

### AI capacity

```text
TOTAL = 6
FIELD_SUZIE = 4 max
HOUSE = 1 reserved
WILSON = 1 reserved
```

Reserved House/Wilson slots are never borrowed by extra Field doctors.

### Web projects

House:
- project_id: `g-p-6ab184e457cc819182e5230e09fdfc18`
- URL: https://chatgpt.com/g/g-p-6ab184e457cc819182e5230e09fdfc18-doktor-khaus

Wilson:
- project_id: `g-p-6ab1850655508191afad64d3cc104b3a`
- URL: https://chatgpt.com/g/g-p-6ab1850655508191afad64d3cc104b3a-doktor-vilson

Field Suzie project target remains a separate configured role target and must not be inferred from text.

### Routing

```text
simple/system recommendation
  -> Family Doctor -> execute -> verify

confirmed Disease + APPROVED ACTIVE Protocol
  -> Doctor Server signed package
  -> Family Doctor -> execute -> verify

unknown Disease
known Disease without APPROVED ACTIVE Protocol
failed approved Protocol without deterministic next path
meaningfully changed Patient Card
  -> Patient Journal/Card
  -> House
  -> OBSERVE / RECHECK / HUMAN / DISPATCH_SUZIE

DISPATCH_SUZIE
  -> field queue if 4/4 busy
  -> exact client Field Suzie
  -> Case Report
  -> Wilson
```

Unknown/no-protocol is NOT an automatic Field Case.

### Family Doctor treatment gate

The installed client distinguishes a trusted execution actor.

- `field_suzie`: state-changing treatment still requires contextual AI `risk_assessment`.
- `family_doctor`: only signed ACTIVE deterministic Protocols can execute.
- `AUTO_SAFE`: allowed in safe_auto/full_trust.
- `CONFIRM_REQUIRED`: Family Doctor may execute without Field AI only in full_trust; in safe_auto it returns `family_doctor_field_review_required`.
- manual trust mode blocks automated protocol treatment.
- WATCH/MANUAL/SUSPENDED never become executable through this path.
- preconditions/checkpoint/verify/rollback/fallback remain mandatory.

The execution actor is trusted transport/server context, not a user-selectable Connector argument.

### Protocol admission

Existing legacy statuses remain grandfathered:
- 17 ACTIVE
- 64 WATCH
- 90 MANUAL

New post-v2 Protocol lifecycle:

```text
CANDIDATE
-> FIELD_TESTING
-> VALIDATED_1/3
-> VALIDATED_2/3
-> VALIDATED_3/3
-> APPROVED_ACTIVE
```

Only independent internally verified treatment episodes count toward 3/3.
External evidence is 0/3 regardless of volume.
Post-publication effectiveness monitoring remains separate (large fleet statistics).

### Web session policy 10/10

For House, Wilson and Field Suzie:

- SESSION = one uninterrupted model run.
- SESSION_MAX = 10 minutes.
- DIALOG = one Web thread.
- DIALOG_MAX = 10 SESSION.
- At 10 minutes, if unfinished, Server continues the SAME job in the SAME dialog with another SESSION.
- Natural model completion closes the current dialog immediately.
- After SESSION 10, unfinished work continues in a fresh dialog from server-backed state.
- A CASE/job can span multiple SESSION and DIALOG.
- Server state, not Web chat history, is canonical.

### Canonical server state

Server migration must persist at minimum:

```text
patients
patient_events / patient_journal
patient_cards
cases
case_events
case_assignments
role_slots
web_dialogs
web_sessions
session_checkpoints
diseases
protocols
knowledge_evidence
protocol_validation_episodes
wilson_cursors
house_decisions
```

Every state-changing command/result must be persisted independently of Web dialogue so continuation/recovery cannot repeat risky operations.

### House contract

Inputs:
- Patient Card/version
- triggering observation/change
- relevant history/recurrence
- known Disease/Protocol matches
- previous treatment + verify
- field capacity

Outputs:
- OBSERVE
- RECHECK_LATER
- IGNORE_AS_NOISE
- HUMAN_ACTION_REQUIRED
- DISPATCH_SUZIE

House never treats the patient directly.

### Wilson contract

Hourly:
- completed/changed Field Case Reports since cursor
- normalize Disease
- distinguish root-cause fix/workaround/incidental action
- build/update Protocol Candidates
- count independent internal validation episodes
- preserve supporting and contradicting evidence
- commit cursor only after knowledge writes succeed

Nightly:
- external docs/issues/forums/research
- external evidence -> Disease/Experimental Protocol Candidate only
- never executable in the same ingest pass

Wilson does not dispatch patients and normally has no client write connector.

### Required Doctor Server migration

The live Doctor Server on Orange Pi 4 Pro is outside this repository. Before this branch can become production behavior, Server must implement:

1. Patient Journal/Card persistence and House wake/result.
2. Role quota 4 + 1 + 1.
3. House project target above.
4. Wilson project target above.
5. 10/10 session/dialog watchdog and continuation.
6. Natural completion => close dialog; no complete-next reuse of a naturally completed Field dialog.
7. Field queue.
8. Routing intents accepted from clients:
   - `FAMILY_DOCTOR_PROTOCOL_OR_PATIENT_JOURNAL`
   - `PATIENT_JOURNAL_HOUSE_REVIEW`
9. Signed command `execution_actor` = `family_doctor` or `field_suzie`.
10. New Protocol 3/3 validation state without retroactively changing legacy statuses.
11. Wilson hourly/nightly cursor jobs.
12. Case Report -> Patient Card + Wilson evidence pipeline.

Until the Server migration is deployed, do not merge/deploy the client branch as production merely because it builds.

### Non-regression invariants

Keep:
- exact-client binding
- TLS/Ed25519 signed transport
- signed execution package validation
- expiry
- source evidence stripping
- ProtocolEngine allowlist
- MANUAL/WATCH treatment block
- no arbitrary shell/eval
- specialized adapter before generic/admin
- read-only first
- checkpoint/backup
- mandatory verify
- rollback/fallback
- attempts/cooldown/recurrence
- no automatic bootstrap Core restart
- garage entrance door no automatic self-heal
