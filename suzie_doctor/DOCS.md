# Suzie Doctor DEV

Experimental developer build of Suzie Doctor for Home Assistant.

This build:
- runs as a Home Assistant App with Ingress UI;
- stores state in `/data` (SQLite);
- installs the small `suzie_doctor` custom integration bridge into Home Assistant config on first run;
- never restarts Home Assistant Core automatically during Doctor bootstrap/update;
- performs first-run and daily audits;
- runs lightweight Health Guard monitoring;
- keeps incidents and audit history locally;
- uses Suzie Doctor Server for the protected Master Knowledge Base and diagnosis/protocol delivery;
- keeps only the local Emergency Pack in the HA App;
- verifies server TLS and Ed25519 signatures before accepting an execution package;
- never receives forum source evidence as part of an execution package.

Not for public production use yet.
