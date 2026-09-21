from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3

from doctor_v2_store import DoctorV2Store, HOUSE_PROJECT_ID, WILSON_PROJECT_ID


def main() -> None:
    root = Path(__file__).resolve().parent
    with TemporaryDirectory() as td:
        db = Path(td) / "server.sqlite3"
        sqlite3.connect(db).close()
        store = DoctorV2Store(db, root / "doctor_v2_schema.sql")
        try:
            store.install_schema()
            slots = store.conn.execute("SELECT role,COUNT(*) n FROM doctor_v2_role_slots GROUP BY role").fetchall()
            counts = {r["role"]: r["n"] for r in slots}
            assert counts == {"FIELD_SUZIE":4,"HOUSE":1,"WILSON":1}, counts
            targets = {r["role"]:r["project_id"] for r in store.conn.execute("SELECT role,project_id FROM doctor_v2_role_targets")}
            assert targets["HOUSE"] == HOUSE_PROJECT_ID
            assert targets["WILSON"] == WILSON_PROJECT_ID

            store.ensure_patient("patient-A", {"health":"degraded"})
            eid = store.append_event(
                patient_id="patient-A",event_type="OBSERVATION",source="test",
                payload={"symptom":"x"},create_house_job=True,
            )
            assert eid > 0
            job = store.claim_house_job("house-dialog-1")
            assert job and job["patient_id"] == "patient-A"
            decision_id = store.submit_house_decision(int(job["house_job_id"]), {
                "finding_class":"CASE","significance":"HIGH","decision":"DISPATCH_SUZIE","field_priority":"HIGH"
            })
            assert decision_id > 0
            q = store.conn.execute("SELECT status FROM doctor_v2_field_queue").fetchone()
            assert q and q["status"] == "WAITING"

            ids=[]
            for i in range(4):
                s=store.acquire_role_slot("FIELD_SUZIE",f"case-{i}")
                assert s
                ids.append(s)
            assert store.acquire_role_slot("FIELD_SUZIE","case-overflow") is None
            assert store.acquire_role_slot("HOUSE","house-job") == "house-1"
            assert store.acquire_role_slot("WILSON","wilson-job") == "wilson-1"

            store.open_dialog(dialog_id="d1",role="FIELD_SUZIE",assignment_id="case-1",project_id="field")
            for i in range(1,10):
                a=store.start_session("d1", {"i":i})
                assert a.ordinal == i
                r=store.end_session("d1",i,"WATCHDOG_10M")
                assert r["continue_same_dialog"] is True
            a=store.start_session("d1", {"i":10})
            assert a.ordinal == 10 and a.rotate_after is True
            r=store.end_session("d1",10,"WATCHDOG_10M")
            assert r["dialog_closed"] and r["rotate"]

            store.open_dialog(dialog_id="d2",role="HOUSE",assignment_id="house-2",project_id=HOUSE_PROJECT_ID)
            a=store.start_session("d2", {})
            r=store.end_session("d2",a.ordinal,"NATURAL")
            assert r["dialog_closed"] and not r["rotate"]

            store.conn.execute(
                "INSERT INTO doctor_v2_protocol_candidates(protocol_id,origin,state,candidate_json) VALUES('p1','INTERNAL_FIELD','FIELD_TESTING','{}')"
            )
            store.conn.commit()
            for n in range(1,4):
                v=store.record_protocol_validation(
                    protocol_id="p1",episode_key=f"episode-{n}",success=True,verified=True,evidence={"n":n}
                )
                assert v["verified_successes"] == n
            assert v["state"] == "VALIDATED_3_3"
            duplicate=store.record_protocol_validation(
                protocol_id="p1",episode_key="episode-3",success=True,verified=True,evidence={"duplicate":True}
            )
            assert duplicate["verified_successes"] == 3

            print("DOCTOR_V2_STORE_TEST_PASS")
        finally:
            store.close()


if __name__ == "__main__":
    main()
