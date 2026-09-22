# Web E2E validation — 2026-09-22

A controlled live Web-only validation was executed on orangepi4pro after the
House/Wilson migration. It intentionally allowed no Home Assistant state changes.

Flow validated:

1. Patient Journal created a controlled architecture-validation observation.
2. Real Web Doctor House claimed the House job.
3. House returned DISPATCH_SUZIE and created a Field queue item / legacy Case.
4. Real Web Field Suzie claimed the Case and used only read-only Connector tools:
   - supervisor.info
   - doctor.suite
   - ha.repairs.list
   - ha.config.read
   - ha.notifications.list
5. Field finished SUCCESS with an explicit read-only verification report.
6. Real Web Doctor Wilson consumed that completed Field Case Report in HOURLY_REVIEW.
7. Wilson classified it as a controlled read-only E2E validation, created no Disease/Protocol candidate, and assigned 0/3 validation credit.

Observed transport defect and fix:

- The first House completion used a semantic finding_class outside the strict
  EVENT/OBSERVATION/INCIDENT/CASE transport enum and was rejected with HTTP 400.
- The canonical Doctor MCP adapter now preserves the original value as
  raw_finding_class and deterministically maps only the descriptive finding class
  to CASE (or OBSERVATION for IGNORE_AS_NOISE). House decision itself remains strict
  and is never coerced.
- The same House Web dialog was continued and completed successfully after this fix.

Result: WEB_HOUSE_FIELD_WILSON_E2E_PASS.

This validation did not enable or exercise API transport.
