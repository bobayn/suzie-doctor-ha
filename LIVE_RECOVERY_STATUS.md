# Live Doctor source recovery — 2026-11-06

## Status: COMPLETE for inventoried Doctor Server and MCP Python/SQL/README sources

This recovery branch preserves **13 byte-for-byte verified source files** copied from the running Orange Pi 4 Pro Doctor Server and MCP adapter. All other inventoried code files already matched the original `main` blobs and were retained. Uploads were checked against each file's original Git blob SHA, not merely by comparing displayed text.

The last missing file, `suzie_doctor/live_runtime/doctor_server/server.py`, was supplied by the owner from the actual host and verified:

- Size: **163795 bytes**
- Live/upload SHA-256: `fae505ea6c8be3a3b4815b28aa905b6c8415efed84de55facbad63ba966c1535`
- Exact Git blob SHA: `10eef58201270879e1f5475e64729c14bab8365c`
- Python syntax: valid
- Sensitive-content scan: no embedded private keys, common API token patterns, or literal credentials found; references to external secret file paths are not credential values.

## Scope and merge precautions

This is a **source-of-truth recovery**, not a new deployed release. No Orange Pi file, systemd unit, or Home Assistant app was changed. Branch/PR #19 can be merged into `main` after relevant CI and code-diff review. The separate `http.ssl_profile.update` Field implementation in PR #18 must be reconciled against this recovered server source before release.

The GitHub repository is public; review any future added files for credentials before committing. This recovery excludes runtime state, private databases, configuration values, licenses, tokens and keys. Do not upload those files.
