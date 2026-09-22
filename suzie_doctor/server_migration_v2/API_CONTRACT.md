# Doctor Server v2 API additions
## Additive contract; existing signed v1 client endpoints stay compatible

### Constants
- TOTAL_AI_SLOTS=6
- FIELD_SUZIE_MAX=4
- HOUSE_RESERVED=1
- WILSON_RESERVED=1
- SESSION_MAX_SECONDS=600
- DIALOG_MAX_SESSIONS=10

### Canonical Web projects
- HOUSE: g-p-6ab184e457cc819182e5230e09fdfc18
- WILSON: g-p-6ab1850655508191afad64d3cc104b3a

### Routing intents accepted from client /v1/diagnose
`FAMILY_DOCTOR_PROTOCOL_OR_PATIENT_JOURNAL`
1. If confirmed Disease has suitable signed APPROVED/legacy ACTIVE Protocol:
   return package for Family Doctor and mark execution_actor=family_doctor.
2. Otherwise append a Patient Journal event, refresh Patient Card and enqueue House job.
3. Do not auto-create Field Case.

`PATIENT_JOURNAL_HOUSE_REVIEW`
1. Append Observation/Event.
2. Refresh Patient Card.
3. Create/coalesce House job.
4. Do not auto-dispatch Field Suzie.

### House internal endpoints
- POST /internal/house/jobs/next
- GET  /internal/house/jobs/{job_id}
- POST /internal/house/jobs/{job_id}/claim
- POST /internal/house/jobs/{job_id}/decision

Decision enum:
- OBSERVE
- RECHECK_LATER
- IGNORE_AS_NOISE
- HUMAN_ACTION_REQUIRED
- DISPATCH_SUZIE

DISPATCH_SUZIE creates/activates a Field CASE and queues it if all 4 Field slots are busy.

### Wilson internal endpoints
- POST /internal/wilson/jobs/next
- GET  /internal/wilson/jobs/{job_id}
- POST /internal/wilson/jobs/{job_id}/claim
- POST /internal/wilson/jobs/{job_id}/complete

Modes:
- HOURLY_REVIEW
- NIGHTLY_RESEARCH

Hourly input is incremental since committed cursor.
Nightly external evidence never becomes executable in the same ingest pass.

### Role slots
Atomic allocator:
- FIELD_SUZIE slots 1..4
- HOUSE slot 1
- WILSON slot 1

Reserved roles cannot be borrowed.

### 10/10 Web lifecycle
A logical job owns a role slot independently of physical Web dialogs.

On Web invocation:
1. Create/open dialog generation.
2. Start session N.
3. Set watchdog_at = start + 600 s.
4. If model naturally returns:
   persist result/checkpoint, close session, close dialog.
5. If watchdog fires before natural return:
   persist all server-side action/result events already received;
   mark session TIMEBOX_CONTINUE;
   invoke continuation in SAME dialog if session_no < 10.
6. If session_no == 10 and unfinished:
   checkpoint atomically;
   close dialog reason DIALOG_SESSION_LIMIT;
   create new dialog generation for SAME logical job.
7. Never release logical role slot merely because dialog rotates.
8. Release role slot when logical job completes/deferred/human-required according to role policy.

### Case completion
Natural Field completion closes the current Web dialog.
Do not assign next Case into that naturally completed dialog.
Legacy complete-next may remain API-compatible but next_case MUST be null for Web Field flow.

### Trusted execution_actor
Signed server-routed client commands may carry:
- execution_actor=field_suzie
- execution_actor=family_doctor

It is server-authenticated metadata, never a user/tool argument.

Family Doctor package:
- ACTIVE only
- signed/client-bound/unexpired
- exact Disease/Protocol
- client ProtocolEngine still enforces trust mode, automation class, preconditions, checkpoint, verify and rollback.

### New Protocol admission
Legacy ACTIVE/WATCH/MANUAL remain grandfathered.

New candidate lifecycle:
CANDIDATE -> FIELD_TESTING -> VALIDATED_1_3 -> VALIDATED_2_3 -> VALIDATED_3_3 -> APPROVED_ACTIVE

Only independent internally verified episodes may increment validation.
External evidence is always 0/3.

### Recovery / idempotency
Every state-changing client command must have an immutable command_id/idempotency key.
Checkpoint stores do_not_repeat and latest action/result refs.
A continuation after timeout/dialog rotation reconstructs from:
latest checkpoint + persisted events after checkpoint.
