from policy import (
    RoleQuota,
    HOUSE_PROJECT_ID,
    WILSON_PROJECT_ID,
    next_dialog_action,
    protocol_validation_state,
    may_publish_new_protocol,
    external_evidence_confirmation_increment,
    family_doctor_or_house,
)

def main() -> None:
    assert RoleQuota().valid()
    assert RoleQuota().field == 4
    assert RoleQuota().house == 1
    assert RoleQuota().wilson == 1

    assert HOUSE_PROJECT_ID == "g-p-6ab184e457cc819182e5230e09fdfc18"
    assert WILSON_PROJECT_ID == "g-p-6ab1850655508191afad64d3cc104b3a"

    assert next_dialog_action(natural_completion=True, session_no=1) == "CLOSE"
    assert next_dialog_action(natural_completion=False, session_no=1) == "CONTINUE_SAME_DIALOG"
    assert next_dialog_action(natural_completion=False, session_no=9) == "CONTINUE_SAME_DIALOG"
    assert next_dialog_action(natural_completion=False, session_no=10) == "ROTATE_DIALOG"

    assert protocol_validation_state(0) == "FIELD_TESTING"
    assert protocol_validation_state(1) == "VALIDATED_1_3"
    assert protocol_validation_state(2) == "VALIDATED_2_3"
    assert protocol_validation_state(3) == "VALIDATED_3_3"
    assert may_publish_new_protocol(confirmations=2, consistency_ok=True) is False
    assert may_publish_new_protocol(confirmations=3, consistency_ok=False) is False
    assert may_publish_new_protocol(confirmations=3, consistency_ok=True) is True
    assert external_evidence_confirmation_increment() == 0

    assert family_doctor_or_house(approved_active_protocol=True) == "FAMILY_DOCTOR"
    assert family_doctor_or_house(approved_active_protocol=False) == "PATIENT_JOURNAL_HOUSE"

    print("PASS doctor_server_v2_policy")

if __name__ == "__main__":
    main()
