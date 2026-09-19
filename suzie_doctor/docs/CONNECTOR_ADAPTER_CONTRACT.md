# Suzie Doctor Connector — adapter/capability contract

Suzie Doctor ships one canonical Connector Core inside the App. Web/ChatGPT and
API/MCP/tools are transport adapters over the same registry and tool contract.
They must not invent surface-specific Doctor semantics.

## Registry model

The file /app/suite/connector_contract.json is the versioned public contract
loaded by ConnectorCore.

Every adapter declares:

- adapter_id
- adapter_version
- family
- available
- health
- reason when unavailable
- permissions
- read_operations
- write_operations
- dangerous_operations
- confirmation_class
- checkpoint_support
- rollback_support
- compatibility

Every exposed tool/capability declares:

- globally unique name
- owning adapter_id
- family
- risk
- target_policy
- target_fields when an exact argument target is required
- checkpoint/rollback metadata
- confirmation class
- JSON input schema

Duplicate adapter IDs or capability names are startup errors. Unknown tools,
unavailable adapters and missing exact targets fail closed.

## Capability is not permission

doctor.capabilities reports what the installed Suite can technically expose.
It is not authorization to mutate the client.

A state-changing capability still needs its normal Protocol, trust,
confirmation, target, checkpoint and verification gates. Web/API surface
selection never changes those gates.

## Connector v1

The first Suite exposes read-only Doctor, Home Assistant and Supervisor
capabilities plus one treatment-capable tool: doctor.diagnose.

When execute=true, this tool does not execute arbitrary Connector writes. It
continues through the existing protected path:

Doctor Server
→ signed, client-bound, short-lived execution package
→ local package validation
→ confirmed Disease
→ ProtocolEngine
→ allowlisted primitives
→ verify/rollback

Human confirmation is transport/session-owned context. A model/tool argument
cannot manufacture explicit_confirmation.

## Existing HA/Supervisor treatment primitives

ProtocolEngine continues to use the existing bounded HA/Supervisor clients and
primitive allowlist. Connector v1 wraps those same backends as the common
access facade for external Doctor surfaces; it does not rewrite working
primitives merely to satisfy an abstraction.

This preserves the existing ACTIVE protocol semantics while allowing future
capabilities to be added through specialized adapters.

## Unsupported families

A family with no safe backend remains registered as an unavailable adapter with
a reason. It must not expose a fake-success tool and it must not fall back to a
shell.

Initial reserved families include:

- frigate
- docker
- z2m
- zwave
- mqtt
- esphome
- nodered
- storage
- recorder
- network
- auth
- human

## Adding a new adapter safely

1. Identify a stable structured backend/API.
2. Add the adapter descriptor to the canonical Connector contract.
3. Add only narrow capabilities with explicit schemas.
4. Classify each capability as read-only, structured write, signed treatment,
   or high risk.
5. For writes, define exact target semantics.
6. Declare confirmation/checkpoint/rollback support.
7. Implement the adapter without arbitrary shell/eval or direct .storage
   editing.
8. Verify post-change state using the same functional criterion used for the
   diagnosis.
9. Add registry/security/unit regression cases.
10. Add Web/API parity coverage.
11. Update Suite compatibility metadata if the interface/schema changes.
12. Only then allow reviewed Protocols to depend on the capability.

Adding a capability does not automatically promote WATCH/MANUAL Protocols to
ACTIVE. Protocol publication/review remains a separate gate.

## Required regression behavior

The Developer Suite/Release Gate must prove at least:

- registry loads;
- duplicate adapter/capability rejection;
- deterministic discovery;
- unavailable adapters report unavailable;
- metadata validity;
- exact-target enforcement;
- unsupported tool fail-closed;
- backend exception cannot become success;
- checkpoint/rollback metadata survives;
- Web/API tool and Skill parity;
- incompatible Suite blocks treatment while safe diagnosis remains possible;
- existing signed generated Protocol path still passes;
- WATCH/MANUAL treatment blocks remain intact.
