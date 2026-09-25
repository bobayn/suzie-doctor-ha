# Wilson historical treatment review — 2026-09-25

Scope: historical Suzie Doctor Wilson `NIGHTLY_RESEARCH` results already stored in Doctor DB. This review does not add fresh web verification; it classifies only what Wilson had already gathered.

## Outcome

- Unique external treatment topics after deduplication: 11
- Existing EXPERIMENTAL candidate: 1
- Rejected for now: 10
- External evidence contributes 0 validation credit.

## Reviews

1. **Home Assistant 2026.9 registry/memory bloat** — REJECTED / NO_SUCCESSFUL_TREATMENT. Wilson had disease evidence but no authoritative fixed release or demonstrated reusable treatment. Keep distinct from non-OOM hangs. Sources in Wilson jobs: HA Core issues #181120 and #181318.

2. **Home Assistant 2026.9 non-OOM unresponsiveness** — REJECTED / NO_SUCCESSFUL_TREATMENT. No confirmed root cause and no demonstrated reusable treatment in stored Wilson evidence. Sources include HA Core #181318 and #181212.

3. **OpenAI conversation + HA-MCP schema incompatibility on HA 2026.9** — REJECTED / NO_SUCCESSFUL_TREATMENT. Wilson established the failure pattern but not a treatment plus functional recovery. Source: HA Core #181322.

4. **ViCare 2026.9 / upstream API failure** — REJECTED / NOT_APPLICABLE. Wilson evidence points to pre-existing upstream connection failures; no safe local Doctor treatment was established. Source: HA Core #182286.

5. **HA Core 2026.9.2 update crash with Supervisor rollback** — REJECTED / NOT_MACHINE_ACTIONABLE. Automatic Supervisor rollback is observed containment, not an independently selectable Doctor treatment with a bounded signed target. Source: HA Core #182082.

6. **Zigbee2MQTT 2.14.0 / 2.14.0-1 cover direction inversion** — CANDIDATE PRESENT. Canonical v2 candidate: `EXP-Z2M-COVER-2140-HOTFIX-001`, stage 0/3. Wilson evidence includes issue reports where rollback restored behavior and the upstream 2.14.1 hotfix. External evidence gives zero validation credit. Historical sources: Zigbee2MQTT issues #32999/#33004 and release 2.14.1.

7. **ESPHome web_server number entity shows 0.0 when state is unavailable** — REJECTED / NO_SUCCESSFUL_TREATMENT. Symptom/regression evidence exists but stored Wilson evidence did not establish a successful treatment plus recovery. Source: ESPHome #19331.

8. **Home Assistant MQTT duplicate events/messages** — REJECTED / NO_SUCCESSFUL_TREATMENT. No confirmed root cause and no demonstrated successful treatment in stored Wilson evidence. Source: HA Core #182295.

9. **ESPHome ES8388 audio regression from 2026.5.x** — REJECTED / INSUFFICIENT_EVIDENCE. Single open report; rollback to a last-known-good build is a hypothesis in Wilson data, not a confirmed treatment, and Doctor has no bounded signed firmware rollback path. Source: ESPHome #19262.

10. **Frigate max_frames deregister sibling-tracker bug** — REJECTED / NO_SUCCESSFUL_TREATMENT. Maintainer narrowed the mechanism and referenced PR #24418, but Wilson evidence did not establish a released treatment plus recovery of the original functional criterion. Source: Frigate discussion #24416.

11. **Home Assistant camera.record non-monotonic DTS regression** — REJECTED / UNSUPPORTED_CAPABILITY. Wilson had evidence that rolling Core back to 2026.7.4 restored function, but Doctor currently has no bounded signed Core downgrade primitive, and stored evidence did not establish a safe supported fixed-release update path. Source: HA Core #180487.

## Governance

A rejected item is not forgotten. It remains reusable diagnostic/external evidence and can be reconsidered when new evidence arrives or when Doctor gains a safe missing capability.

A candidate is not ACTIVE. It must be matched by House, independently diagnosed by Field, tried first only when safe/applicable/executable, functionally verified, and then earn three independent internal successes before publication review.
