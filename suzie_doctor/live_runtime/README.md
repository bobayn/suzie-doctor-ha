# Suzie Doctor verified live runtime

This directory is the source snapshot synchronized from the verified Web-only
live deployment on orangepi4pro after the House/Wilson v2 migration.

Canonical architecture:
- 4 FIELD_SUZIE Web slots;
- 1 reserved HOUSE Web slot;
- 1 reserved WILSON Web slot;
- 10-minute SESSION, maximum 10 SESSION per DIALOG;
- natural completion closes the DIALOG;
- unknown/no-approved-protocol observations go to Patient Journal + House;
- House decides whether Field Suzie is dispatched;
- Wilson consumes completed Field Case Reports and external research;
- only verified internal episodes count toward the 1/3 -> 2/3 -> 3/3 admission path;
- API transport remains disabled; this snapshot is for the Web/MCP deployment.

The live production database/config/keys are intentionally not stored here.
Use server_migration_v2/ for the additive SQLite migration and this directory
for the runtime source that was validated after migration.
