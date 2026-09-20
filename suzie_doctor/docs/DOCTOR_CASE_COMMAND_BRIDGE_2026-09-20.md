# Suzie Doctor Case / Command Bridge — 2026-09-20

Doctor Server owns the Case queue and doctor-session ownership. Web and API are transport adapters only.

CASE #N -> doctor.case.get -> atomic claim -> exact client_id -> read-only diagnosis first -> allowed treatment -> verify -> complete-next.

The Doctor App polls Doctor Server over its existing TLS-pinned and Ed25519-authenticated client channel. Commands are bound to one client_id and execute only through ConnectorCore.invoke.

Only one command is active inside one client App at a time. Claimed commands are not automatically requeued after timeout.

After verified completion, complete-next may assign the next waiting Case to the same real dialog. dialog_id never changes; assignment_seq/dialog_ref provides -2, -3 style references.

Safety: exact client only, no arbitrary shell/eval, no auto Core restart, signed treatment path remains authoritative, and safe_auto still blocks CONFIRM_REQUIRED treatment without trusted confirmation.
