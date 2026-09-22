# Suzie Doctor Server

Server-side component of Suzie Doctor.

## Runtime
- Server version: 0.1.4-dev
- HTTPS API: 192.168.0.105:8790
- Service: suzie-doctor-server.service
- Master KB watcher/compiler: suzie-doctor-knowledge-compile.path
- License admin: /opt/suzie-doctor-server/doctorctl.py

## Knowledge pipeline
Master KB
  -> KnowledgeCompiler
  -> Disease normalizer
  -> normalized_knowledge.json
  -> Doctor Server diagnosis / recommendations / signed protocol delivery

The normalizer is conservative:
- merge by verified same-root-cause adjudications or strong duplicate evidence;
- similar symptoms alone never merge Diseases;
- old Disease IDs remain aliases of canonical merged Diseases;
- diagnostic families are routing groups, not diagnoses;
- no protocol candidate is promoted without structured supported primitives.

## Current normalized v2.7
- incidents: 411
- diagnosed incidents: 328
- unclassified incidents: 83
- Diseases: 303
- diagnostic families: 62
- merged Disease IDs: 24 aliases to canonical Diseases
- multi-incident Diseases: 22
- confirmed Diseases: 210
- protocol candidates: 171
- Diseases with protocol candidates: 162
- executable Protocols: 4
- confirmed Diseases with no treatment knowledge yet: 65
- new candidates currently promotable: 0
- diagnostic rules: 158
- DON'T DO rules: 162
- recurring patterns: 117
- quarantined recipes: 5

Candidate gates:
- CONFIRM_REQUIRED: 81
- CONFIRM_REQUIRED_HIGH_RISK: 19
- DIAGNOSTIC_ONLY: 64
- PRIMITIVE_MAPPING_REQUIRED: 7

## Storage
- Master KB: /var/lib/suzie-doctor-server/knowledge/forum_knowledge_base.json
- Compiled KB: /var/lib/suzie-doctor-server/knowledge/compiled_knowledge.json
- Normalized KB: /var/lib/suzie-doctor-server/knowledge/normalized_knowledge.json
- License DB: /var/lib/suzie-doctor-server/server.sqlite3

The HA client keeps only the Emergency Pack and deterministic execution engine.
Forum incidents, source evidence, normalization rules and the Master KB stay
server-side.

## Backups
Kopia includes:
- /var/lib/suzie-doctor-server
- /opt/suzie-doctor-server
- /etc/suzie-doctor-server

## Incident ingest connector
External knowledge enters through the Suzie Home MCP tool
doctor_ingest_incident.

Security model:
- connector can submit one bounded structured incident only;
- default is dry-run;
- connector cannot read/export Master KB, licenses, signing keys or arbitrary files;
- source must be HTTP(S);
- server-side worker performs validation and dedupe;
- every external incident is stored initially as DIAGNOSTIC_ONLY;
- append is atomic; compile/normalize failure restores the previous Master KB;
- result is bounded to incident id, duplicate/match summary and count deltas;
- no executable Protocol is created directly by ingest.

Queue: /var/lib/suzie-doctor-ingest
Worker: suzie-doctor-incident-ingest.service
Watcher: suzie-doctor-incident-ingest.path


## Incident curation connector
Post-ingest knowledge decisions use Suzie Home MCP tool doctor_curate_incident.

Supported bounded actions:
- remove_duplicate: only INC-INGEST-* incidents and only with strong same-source duplicate proof;
- attach_existing_disease: persistently attach evidence to an existing Disease through the curation ledger;
- keep_unclassified: force an unresolved case to remain outside Diseases;
- create_new_disease: only for CONFIRMED incidents with an explicit non-uncertain root cause;
- classify_treatment: one of DIAGNOSTIC_ONLY, CONFIRM_REQUIRED, CONFIRM_REQUIRED_HIGH_RISK, PRIMITIVE_MAPPING_REQUIRED.

Safety:
- default dry-run;
- no Master KB export, file access, shell, keys or licenses;
- no curation action can promote an executable Protocol;
- every committed decision is recorded in curation_ledger.json and curation_audit.jsonl;
- compile/normalize failure restores Master KB and ledger;
- Disease aliases are resolved so durable attachments survive later canonical-ID changes.


## Protocol Factory
The protected server compiles every normalized protocol candidate into a structured Protocol record.

Current v0.1.0 policy:
- every candidate gets one stable generated Protocol record;
- incomplete or non-deterministic treatment remains SUSPENDED and is not sent as an execution package;
- WATCH cards may carry a complete mapped treatment draft but cannot execute treatment;
- ACTIVE requires a complete deterministic mapping to the client primitive allowlist;
- high-risk mapped treatment is always CONFIRM_REQUIRED;
- no arbitrary service-call or shell primitive exists;
- source evidence stays server-side and is stripped from execution packages.

Generated catalog:
/var/lib/suzie-doctor-server/knowledge/generated_protocols.json

The knowledge compiler runs: compile -> normalize -> Protocol Factory. Ingest/curation therefore rebuild the generated catalog transactionally with the rest of Doctor knowledge.


### Protocol Factory publication gate
The 171 candidates that existed on 2026-09-19 are the explicit reviewed baseline in protocol_factory_approvals.json.
New candidates produced by later ingest/curation may be compiled into structured drafts, but they remain SUSPENDED with AWAITING_PROTOCOL_REVIEW until a separate protocol-publication review admits them. This intentionally keeps nightly knowledge ingestion separate from automatic publication of new treatment.


### Generated Protocol status model
For the reviewed 2026-09-19 baseline of 171 candidates:
- ACTIVE: complete deterministic machine treatment; all current ACTIVE cards require confirmation.
- WATCH: diagnostic-only protocol; treatment is intentionally not executable.
- MANUAL: fully curated non-machine treatment. The signed package contains checks/action/verify/rollback guidance, but the HA ProtocolEngine cannot execute treatment because only ACTIVE may execute.
- SUSPENDED: reserved for future/unreviewed candidates, including AWAITING_PROTOCOL_REVIEW.

Current baseline: 17 ACTIVE, 64 WATCH, 90 MANUAL, 0 SUSPENDED. AUTO_SAFE generated treatment: 0.

The HAOS DNS adapter is intentionally narrow: primary IPv4 method=auto only, explicit dns_servers context, backup before write, verification after write, and rollback to the previous nameserver list. Static interfaces fail closed.

---

## Suzie Doctor Case Journal and AI Doctor Dispatch (0.1.5-dev)

Doctor Server owns the canonical queue of unresolved problems that need Suzie Doctor AI.

### One journal gate

All ownership-changing Case operations pass through one fair journal gate. Server dispatcher
and all Web/API Suzie Doctor sessions use the same gate. The gate is held only for short
atomic journal transactions. Browser/API/model work and actual diagnosis/treatment run
outside the gate.

### Maximum parallel doctors

max_ai_doctors defaults to 5.

Active doctor-session states are STARTING, ASSIGNED, BUSY and CHECKING. A sixth Case remains
FOR_SUZIE without a dialog binding until a slot becomes available or an existing doctor
finishes and takes it.

### Case lifecycle

FOR_SUZIE -> DISPATCHING -> ASSIGNED -> CLAIMED -> TREATING -> VERIFYING -> RESOLVED

Terminal alternatives are HUMAN_REQUIRED, FAILED and CANCELLED.

Every Case carries exact client_id. AI tools must route through that target and never choose
a Home Assistant target from free text.

### Two-phase Web dispatch

The journal gate is not held while ChatGPT loads.

1. Under the gate reserve Case plus one doctor slot and set DISPATCHING.
2. Leave the gate.
3. CDP PRIMARY opens a new dialog in ChatGPT Project Doktor Suzie and sends only CASE #N.
4. Persist UI-sent progress.
5. Wait until temporary local-chatgpt URL becomes final /c/<id>.
6. Re-enter the gate and bind final dialog_id to the Case and doctor session.

If UI already sent CASE #N but final dialog binding is temporarily unavailable, the Case is
not automatically requeued. Duplicate treatment is worse than a stuck Case; reconciliation
binds the existing dialog later.

### Claim safety

doctor.case.claim is atomic. Only an ASSIGNED Case can be claimed. The first doctor receives
an opaque claim token. A second concurrent or duplicate claim gets conflict. Stage and
completion operations require that claim token.

A missed heartbeat never automatically gives an in-treatment Case to another doctor.

### Reuse an existing Web dialog

After treatment Skill Core must call doctor.case.complete_next instead of exiting directly.

That single journal transaction closes the current Case and checks for an unassigned
FOR_SUZIE Case. If one exists, it is assigned to the same doctor session and same real
ChatGPT dialog_id, while assignment_seq increments.

The real dialog_id never changes. A synthetic operator-facing dialog_ref is used:
first assignment = dialog_id, second = dialog_id-2, third = dialog_id-3.

If no waiting Case exists, the doctor session becomes CLOSED and the Web dialog may exit.

### Connector-facing journal contract

Server endpoints map to canonical Connector semantics:
doctor.capabilities, doctor.queue, doctor.case.get, doctor.case.claim,
doctor.case.heartbeat, doctor.case.stage and doctor.case.complete_next.

Web/ChatGPT and future API adapters must expose the same semantic contract. There is one
Connector Core and one Skill Core; transport does not change treatment methodology.

### Web and API policy

Web ChatGPT Project Doktor Suzie is primary. CDP is primary Web launcher. Extension remains
browser-level fallback. The schema stores transport per session/Case and reserves future API
adapter support. api_transport_enabled remains false; no paid API call is made by this build.

### Deployment safety gate

Journal API is active, while web_dispatch_enabled, auto_escalate_to_suzie and
api_transport_enabled remain false until the canonical Connector is connected to the
ChatGPT Project.

### Regression tests

Server --selftest includes doctor_journal_max_five, doctor_journal_double_claim_blocked and
doctor_journal_same_dialog_handoff in addition to all previous protocol/signing tests.
