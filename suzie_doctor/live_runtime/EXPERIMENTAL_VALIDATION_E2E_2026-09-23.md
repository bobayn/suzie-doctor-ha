# Experimental Protocol validation Web E2E — 2026-09-23

## Goal

Validate the closed Doctor v2 chain for an unpublished Experimental Protocol candidate:

`0/3 candidate -> Server matching -> Patient Card -> House semantic decision -> DISPATCH_SUZIE + VALIDATE_FIRST -> Field independent diagnosis -> signed EXPERIMENTAL treatment -> verify -> Wilson evidence -> 1/3`

API transport remained disabled throughout. The controlled treatment was a signed no-op protocol using only the `confirmed_disease` primitive; it did not mutate Home Assistant or devices.

## Defects found and fixed during live E2E

1. House correctly recognized the 0/3 candidate as `MATCHING_VALIDATION_TARGET` and chose `DISPATCH_SUZIE`, but its first real Web result omitted canonical `house_directive=VALIDATE_FIRST` / `experimental_protocol_id`. Server normalization was added so this explicit semantic review becomes the canonical directive. A House dispatch with matched Experimental candidates can no longer silently ignore them: it must validate first or explicitly decline Experimental validation with a reason.
2. Experimental matching originally considered recent patient history too broadly, so the controlled candidate could appear on unrelated later House jobs. Matching is now anchored to the current trigger/current explicit Disease; history may corroborate but cannot create a match by itself.
3. A legacy Wilson cursor such as `E2E:60` could crash the v2 dispatch loop, and a closed House/Wilson dialog could strand a CLAIMED role slot. Cursor normalization and stranded-assignment recovery were added.

## Successful controlled validation

- Experimental candidate: `CONTROLLED-EXP-E2E-20260923-001`, initial stage `0/3`.
- Server matching supplied the candidate to House from the current controlled trigger.
- House dispatched Field with `VALIDATE_FIRST` at stage `0/3`.
- Field Case #66 independently confirmed the controlled Disease and candidate applicability. House selection was not treated as proof.
- Field used the exact-client signed `doctor.diagnose` execution path with a structured `PROCEED` risk assessment.
- The signed EXPERIMENTAL no-op execution returned `SUCCESS` and protocol verify returned `PASS` for the original controlled functional criterion.
- Server attested the attempt against the exact Case command journal and produced required Field validation evidence `field:66`, `success=true`, `verified=true`.
- Wilson job #8 received the required Field evidence. The Web Wilson job remained claimed, so the same canonical compatibility transport completed the Wilson job with unchanged Field facts; Server enforced that success/verified could not be rewritten.
- Progression result was exactly `0/3 -> 1/3`, state `VALIDATED_1_3`, `publication_ready=false`, and publication queue remained empty.

This confirms that a successful independent INTERNAL Field verify is the only event that increments Experimental validation progression. 3/3 still does not imply ACTIVE; publication review remains separate.

## Cleanup

After verification, all controlled candidate/evidence/House/Field/Wilson runtime artifacts were removed from the live database. The global `FIELD_CASE_REPORTS` cursor was restored to the last non-controlled Case (`60`). Database `quick_check` returned `ok`. A pre-cleanup SQLite backup was retained under the existing Doctor migration-backup area.
