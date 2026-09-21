"""Suzie Doctor Server v2 policy helpers.

This module is intentionally side-effect free.  It can be imported by the live server
after its current DB/API implementation is inspected and adapted.
"""
from __future__ import annotations

from dataclasses import dataclass

HOUSE_PROJECT_ID = "g-p-6ab184e457cc819182e5230e09fdfc18"
WILSON_PROJECT_ID = "g-p-6ab1850655508191afad64d3cc104b3a"

TOTAL_AI_SLOTS = 6
FIELD_SUZIE_MAX = 4
HOUSE_RESERVED = 1
WILSON_RESERVED = 1
SESSION_MAX_SECONDS = 10 * 60
DIALOG_MAX_SESSIONS = 10

HOUSE_DECISIONS = {
    "OBSERVE",
    "RECHECK_LATER",
    "IGNORE_AS_NOISE",
    "HUMAN_ACTION_REQUIRED",
    "DISPATCH_SUZIE",
}

ROUTING_FAMILY = "FAMILY_DOCTOR_PROTOCOL_OR_PATIENT_JOURNAL"
ROUTING_HOUSE = "PATIENT_JOURNAL_HOUSE_REVIEW"


@dataclass(frozen=True)
class RoleQuota:
    total: int = TOTAL_AI_SLOTS
    field: int = FIELD_SUZIE_MAX
    house: int = HOUSE_RESERVED
    wilson: int = WILSON_RESERVED

    def valid(self) -> bool:
        return self.field + self.house + self.wilson == self.total


def next_dialog_action(*, natural_completion: bool, session_no: int) -> str:
    """Return CLOSE, CONTINUE_SAME_DIALOG, or ROTATE_DIALOG."""
    if natural_completion:
        return "CLOSE"
    if session_no < 1:
        raise ValueError("session_no must be >= 1")
    if session_no < DIALOG_MAX_SESSIONS:
        return "CONTINUE_SAME_DIALOG"
    return "ROTATE_DIALOG"


def protocol_validation_state(confirmations: int) -> str:
    if confirmations <= 0:
        return "FIELD_TESTING"
    if confirmations == 1:
        return "VALIDATED_1_3"
    if confirmations == 2:
        return "VALIDATED_2_3"
    return "VALIDATED_3_3"


def may_publish_new_protocol(*, confirmations: int, consistency_ok: bool) -> bool:
    return confirmations >= 3 and consistency_ok


def external_evidence_confirmation_increment() -> int:
    return 0


def family_doctor_or_house(*, approved_active_protocol: bool) -> str:
    if approved_active_protocol:
        return "FAMILY_DOCTOR"
    return "PATIENT_JOURNAL_HOUSE"
