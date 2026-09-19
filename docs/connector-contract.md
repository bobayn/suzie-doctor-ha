# Suzie Doctor Suite v1

One App image contains the Connector, Skill and Suite manifest. `COPY rootfs /` packages
all components atomically. Bridge and Emergency Pack keep their independent versions;
this release does not require a Core restart.

The external v1 contract uses the existing authenticated HA Ingress/App proxy:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | /api/suite | Component versions and compatibility reasons |
| GET | /api/connector/capabilities | Runtime registry and bounded backend health probes |
| GET | /api/connector/skill | Packaged Skill text and version metadata |
| POST | /api/connector/diagnose | Signed server diagnosis, never executes treatment |

No public port, arbitrary primitive endpoint, shell, generic service executor or
new permanent administrator credential is introduced. This is an embedded connector
facade, **not a separately connectable ChatGPT OAuth/MCP endpoint**. External AI treatment uses the shared `DoctorConnectorCore` through the same signed-package engine. A production remote authentication/approval middleware remains an integration step; HTTP defaults to read-only until that trusted host middleware provides a `SessionContext`. Do not bypass this through developer endpoints.

## Adapter contract

`Adapter` declares identity, version, implementation/access level and schema constraints.
`Capability` declares its stable ID, original primitive, read/write/probe/notify semantics,
risk, confirmation class, exact/fixed target, checkpoint/rollback support and required
backend methods. Discovery adds availability, health and reasons. The registry rejects
duplicates. Primitive execution delegates to the original deterministic implementation;
false returns and exceptions keep their existing semantics.

A capability being implemented is not permission to run it. A backend health of unknown
is reported explicitly. Presence of a Python method does not prove permission for every
operation: actual API denial remains a failed operation. Specialized Frigate/Zigbee/etc.
backends remain unavailable; generic Supervisor add-on support does not claim them.
`network` only wraps the existing bounded primary IPv4 auto DNS workflow. `recorder`
is only the existing functional probe, not offline DB recovery. `mqtt` only reads the
existing Mosquitto log evidence. `storage` remains unsupported as a protocol adapter;
existing Auditor mount recovery remains unchanged and observes the Suite gate.

## Execution and compatibility

ProtocolEngine derives required capabilities from diagnostics/treatment/checkpoint/
fallback/rollback. It requires compatible component versions, schema and capabilities
before treatment, preserving status, trust, diagnosis, signature/client/expiry and review
gates. Incompatible Suite disables recommendation execution and generic Auditor recovery
while allowing monitoring. Startup stores compatibility metadata; dashboard displays
blocked treatment. Explicit future server wire API versions are rejected; absent metadata
on the existing signed `/v1` endpoint means the legacy v1 contract.

Skill metadata declares the capability vocabulary, schema and version; its SHA256 is
bound by the Suite manifest. Do not bundle Master KB or source evidence into client artifacts.
The separately installed personal Skill is a convenience copy, not an independently updated
source of runtime truth; it must fetch the packaged version when versions differ.

## Add an adapter

1. Implement a narrow backend using the product's documented structured API.
2. Register metadata and precise permission/method dependencies. Leave unavailable if absent.
3. Define exact-target validation, preconditions, real checkpoint/rollback and functional verify.
4. Add the primitive to the engine allowlist only after review. Never map unknown operations
   to generic service, HTTP URL, filesystem edits, shell or eval.
5. Update packaged Skill metadata and the Suite manifest together.
6. Test missing permissions, target ambiguity, backend exceptions, partial failure, rollback
   and post-change verify on a fake backend; do not use live HA as the mutation test fixture.
7. Run `PYTHONPATH=suzie_doctor/rootfs/app python -m unittest discover -s tests -v` and the
   on-device Release Gate. Protocol status promotion is a separate reviewed decision.

## Corpus audit

`scripts/audit_protocol_capabilities.py` accepts an evidence-free list of real normalized
cards and optionally a discovery snapshot. It emits one row per protocol without changing
status. It rejects aggregate counts; it cannot infer 171 exact protocol mappings from totals.
`full_treatment_coverage` requires actual complete_mapping and verify, not just known primitives.
Do not publish the private server corpus into this public repository.

## One Core, multiple transports (0.2.0)

Canonical connector logic: `suzie_doctor/connector.py` plus `transport.py`; canonical Skill Core: `skills/suzie-doctor/SKILL.md`, `metadata.json`, and `references/`. These ship in one App image. There is no Web-specific or API-specific treatment policy or KB.

Invariant: **same Disease + same client state + same permissions = same treatment policy**. Web and API call the same `DoctorConnectorCore.call` and `Runtime.doctor_server_diagnose`, including signature/client/expiry validation, capability resolution, ProtocolEngine gates, treatment plan, verify, rollback and persistent audit. The adapter does not accept trust mode, permissions, confirmation or developer override from model arguments.

The Suite manifest versions Suite, App, Connector Core, Connector interface, Skill Core, Skill schema, Protocol schema, Server API, Bridge and Emergency Pack. Legacy `connector_version`/`skill_version` remain display aliases. `transport_adapters.web` and `.api` advertise supported core/interface/schema versions. Each implementation independently declares `ADAPTER_CONTRACT`; treatment compares all four fields exactly and fails closed, including missing/incorrect types. Safe discovery/diagnosis remains accessible with read permission.

### Integration boundary

- `WebAdapter.call({"tool": name, "arguments": args}, session)`
- `APIAdapter.call({"name": name, "arguments": args}, session)`
- HTTP equivalents: POST `/api/connector/web-tool` and `/api/connector/api-tool` through existing protected Ingress/App proxy.
- `tool_contract()` supplies the same names/input schemas for either loader. `doctor.capabilities` returns this contract and backend capability registry.
- `doctor.skill` returns the exact canonical text, metadata and references for Web packaging, API system-context injection, or Work runtime loading. No rewritten surface-specific prompt.
- `doctor.diagnose` and `doctor.treat` accept only `{ "evidence": {...} }`; treatment does not expose raw backend primitives. Backend namespace IDs remain unchanged across surfaces.
- The authenticated host constructs an immutable `SessionContext` with `doctor.read`, optionally `doctor.treat`, and a verified human confirmation. It must bind the confirmation to its client/session/request. Never deserialize a session from model JSON. HTTP handlers do not manufacture treatment grants.
- These adapters are integration code, not an installed public OAuth service or a complete MCP server. MCP/SDK glue serializes the canonical contract into its supported envelope and delegates; it must not introduce policy.

### Parity tests

`tests/test_transport.py` runs the same controlled Protocol through both adapters and the real Runtime/ProtocolEngine using isolated SQLite and fake server/backends. It compares discovery, full Skill bundle, gates, treatment results, verify and rollback traces. Success, failed functional verify with rollback, missing confirmation, missing permission and each incompatible contract field are covered. Server signature validation is replaced by a fixture here; this is not a live signed-server E2E claim. Existing signed-server Release Gate is still required before live release.
