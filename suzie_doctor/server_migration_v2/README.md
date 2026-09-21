# Doctor Server v2 migration bundle

This directory is the additive migration package for the 2026-09-21
House / Wilson / Family Doctor / Field Suzie architecture.

It is deliberately split into two stages.

## Stage A — safe additive state migration

Files:

- `doctor_v2_schema.sql`
- `doctor_v2_store.py`
- `doctor_v2_policy.json`
- `doctor_v2_migrate.py`
- `test_doctor_v2_store.py`

The schema uses only new `doctor_v2_*` tables so the existing live Doctor
queue/clients/nonces/server events are not renamed or destroyed.

The store implements:

- Patient Card / Patient Journal state;
- House jobs and decisions;
- Field queue;
- fixed AI role capacity 4 + 1 + 1;
- Web dialog/session state;
- 10 minute SESSION / 10 SESSION DIALOG policy;
- natural completion closes a dialog;
- checkpoint persistence;
- Wilson jobs/cursors;
- independent Protocol validation episodes;
- idempotent 1/3 -> 2/3 -> 3/3 counting.

The local regression test must print:

`DOCTOR_V2_STORE_TEST_PASS`

## Stage B — live Doctor Server integration

The current live Server is installed at:

- code: `/opt/suzie-doctor-server`
- config: `/etc/suzie-doctor-server/config.json`
- DB: `/var/lib/suzie-doctor-server/server.sqlite3`
- service: `suzie-doctor-server.service`

The live Server is not Git-managed and its current post-0.1.11-dev Python source
layout must be inspected before source patching.

Run the migration helper without arguments first. It is read-only and reports:

- actual Python files under `/opt/suzie-doctor-server`;
- config keys (not values/secrets);
- SQLite table names;
- SQLite `PRAGMA quick_check`.

Only after the live source layout is known may Stage B wire the new store into the
existing routes/browser wake/case lifecycle.

## DB apply safety

`doctor_v2_migrate.py --apply-db`:

1. requires the live DB to exist;
2. requires pre-migration `PRAGMA quick_check=ok`;
3. makes an online SQLite backup under
   `/var/lib/suzie-doctor-server/migration-backups/`;
4. adds only the new `doctor_v2_*` schema;
5. seeds exactly:
   - 4 FIELD_SUZIE slots,
   - 1 reserved HOUSE slot,
   - 1 reserved WILSON slot;
6. seeds House/Wilson Project IDs;
7. checks 10/10 metadata;
8. requires post-migration `PRAGMA quick_check=ok`.

It does not restart Home Assistant and does not modify the existing Protocol Factory
or legacy 17 ACTIVE / 64 WATCH / 90 MANUAL knowledge.

## Canonical Web targets

House:

`g-p-6ab184e457cc819182e5230e09fdfc18`

Wilson:

`g-p-6ab1850655508191afad64d3cc104b3a`

## Required Stage-B routing

Existing client routing intents introduced by this branch:

- `FAMILY_DOCTOR_PROTOCOL_OR_PATIENT_JOURNAL`
- `PATIENT_JOURNAL_HOUSE_REVIEW`

Server command packages must provide a trusted, server-owned `execution_actor`:

- `family_doctor`
- `field_suzie`

This value must never be accepted from untrusted model/user arguments.

## Do not merge to production prematurely

The client branch changes behavior to the v2 routing model.
Production merge/deploy must happen only after the live central Doctor Server has been
wired to the v2 state/routing layer and smoke-tested end-to-end.
