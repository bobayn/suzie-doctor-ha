# Source recovery status — 2026-11-06

This branch contains **12 files copied from the running Doctor Server and MCP adapter on Orange Pi 4 Pro**. Each copy was verified against the source with an exact Git blob SHA.

The recovery is **not yet complete**. The running `doctor_server/server.py` file was verified with SHA-256 `fae505ea6c8be3a3b4815b28aa905b6c8415efed84de55facbad63ba966c1535` and exact Git blob SHA `10eef58201270879e1f5475e64729c14bab8365c`, but the existing file in this branch is an older copy. Do **not** merge this branch into main or deploy it until the entire current source has been securely recovered and independently reviewed.

Read-only attempts to transmit a remaining section of `server.py` were rejected by the security gate. Do not attempt to bypass the gate by additional encodings or transports; review the source locally and use a vetted release process.

No running Doctor service was changed or restarted. The separate TLS Field repair implementation is in PR #18, not this recovery branch.
