# Suzie Doctor DEV

Experimental developer build of Suzie Doctor for Home Assistant.

This build:
- runs as a Home Assistant App with Ingress UI;
- stores state in `/data` (SQLite);
- installs the small `suzie_doctor` custom integration bridge into Home Assistant config on first run;
- never restarts Home Assistant Core as part of bridge bootstrap; restart actions belong only to explicit treatment logic;
- performs first-run and daily audits;
- runs lightweight Health Guard monitoring;
- keeps incidents and audit history locally.

Not for public production use yet.
