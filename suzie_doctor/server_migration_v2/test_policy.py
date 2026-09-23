from pathlib import Path
import subprocess
import sys
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

    # Customer-facing journal is deliberately separate from raw technical incidents.
    root = Path(__file__).resolve().parents[2]
    db_source = (root / "suzie_doctor/rootfs/app/suzie_doctor/db.py").read_text()
    app_source = (root / "suzie_doctor/rootfs/app/suzie_doctor/app.py").read_text()
    server_client_source = (root / "suzie_doctor/rootfs/app/suzie_doctor/server_client.py").read_text()
    live_source = (root / "suzie_doctor/live_runtime/doctor_server/doctor_v2_live.py").read_text()
    assert "CREATE TABLE IF NOT EXISTS customer_journal" in db_source
    assert 'actor="FAMILY_DOCTOR"' in db_source
    assert '"/v1/customer-feed"' in server_client_source
    assert "def customer_feed(" in live_source
    assert "event_fingerprint" in live_source
    assert "migration_smoke" in live_source
    assert "Камера счётчика газа временно недоступна" in live_source
    assert "Интерфейс Home Assistant сообщил техническое событие" in live_source
    assert "experimental_protocol_candidates" in live_source
    assert "VALIDATE_FIRST" in live_source
    extension_source = (root / "suzie_doctor/live_runtime/doctor_server/doctor_v2_extension.py").read_text()
    assert "MATCHING_VALIDATION_TARGET" in extension_source
    assert "experimental_candidate_disposition" in extension_source
    assert "requires VALIDATE_FIRST or explicit experimental_candidate_disposition" in extension_source
    assert 'episode_key") or "") == f"field:{int(case_id)}"' in extension_source
    assert 'str(validation.get("source") or "").upper() == "FIELD_CASE"' in extension_source
    server_source = (root / "suzie_doctor/live_runtime/doctor_server/server.py").read_text()
    engine_source = (root / "suzie_doctor/rootfs/app/suzie_doctor/protocol_engine.py").read_text()
    assert '"EXPERIMENTAL"' in engine_source
    assert 'experimental_field_only' in engine_source
    assert 'success_when == "conditions"' in engine_source
    assert 'response["verify_performed"] = verify_performed' in engine_source
    assert 'response["verify_passed"]' in engine_source
    assert 'Experimental validation PASS requires attested signed verify_performed=true and verify_passed=true' in server_source
    skill_source = (root / "suzie_doctor/rootfs/app/suzie_doctor_skill/SKILL.md").read_text()
    assert "House Experimental Candidate routing" in skill_source
    assert "continue the Case:" in skill_source
    assert "Техническое событие само по себе не считается проблемой" in app_source
    assert "Открытых проблем</div>" not in app_source
    assert "Найдено за 24 часа</div>" not in app_source

    # Existing CI already runs this policy test. Compile the verified live Web runtime
    # here as well so runtime syntax stays covered without requiring a workflow-file update.
    import py_compile
    repo_root = Path(__file__).resolve().parents[2]
    runtime_files = [
        *sorted((repo_root / "suzie_doctor/live_runtime/doctor_server").glob("*.py")),
        repo_root / "suzie_doctor/live_runtime/doctor_mcp/server.py",
        repo_root / "suzie_doctor/live_runtime/suzie_home_mcp/server.py",
        repo_root / "suzie_doctor/live_runtime/call_lab/server.py",
    ]
    for runtime_file in runtime_files:
        py_compile.compile(str(runtime_file), doraise=True)

    subprocess.run(
        [sys.executable, str(repo_root / "suzie_doctor/server_migration_v2/test_experimental_validation.py")],
        check=True,
    )
    print("PASS doctor_server_v2_policy")

if __name__ == "__main__":
    main()
