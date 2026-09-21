from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from store import DoctorV2Store

MIGRATION = Path(__file__).parent / "migrations" / "001_house_wilson.sql"


def main() -> None:
    with TemporaryDirectory(prefix="doctor-server-v2-") as tmp:
        db = DoctorV2Store(Path(tmp) / "server.sqlite3")
        db.migrate_text(MIGRATION.read_text(encoding="utf-8"))

        patient = db.ensure_patient("CLIENT-TEST", "Test")
        assert db.ensure_patient("CLIENT-TEST") == patient
        event = db.append_event(
            patient,
            "OBSERVATION",
            "selftest",
            {"kind": "example"},
        )
        version = db.put_card(patient, {"health": "OBSERVE"})
        assert version == 1
        version = db.put_card(patient, {"health": "INCIDENT"})
        assert version == 2

        job = db.enqueue_house(patient, version, event)
        claimed = db.claim_house()
        assert claimed is not None
        assert claimed["job_id"] == job

        slots = []
        for idx in range(4):
            slot = db.allocate_slot(
                "FIELD_SUZIE",
                f"CASE-{idx + 1}",
            )
            assert slot is not None
            slots.append(slot)
        assert db.allocate_slot("FIELD_SUZIE", "CASE-5") is None

        house = db.allocate_slot("HOUSE", "HOUSE-JOB-1")
        wilson = db.allocate_slot("WILSON", "WILSON-JOB-1")
        assert house is not None
        assert wilson is not None

        dialog = db.open_dialog(
            role_type="HOUSE",
            job_type="HOUSE",
            job_id=str(job),
            project_id="g-p-6ab184e457cc819182e5230e09fdfc18",
            generation=1,
        )
        for no in range(1, 11):
            session = db.start_session(
                dialog,
                watchdog_at="2099-01-01T00:00:00+00:00",
            )
            assert session["session_no"] == no
        try:
            db.start_session(
                dialog,
                watchdog_at="2099-01-01T00:00:00+00:00",
            )
        except ValueError as exc:
            assert str(exc) == "dialog_session_limit"
        else:
            raise AssertionError("11th session must be blocked")

        db.record_validation(
            episode_id="EP-1",
            protocol_id="P-1",
            client_id="C-1",
            independent_group="G-1",
            outcome="SUCCESS",
            verify={"ok": True},
            counted=True,
        )
        db.record_validation(
            episode_id="EP-2",
            protocol_id="P-1",
            client_id="C-1",
            independent_group="G-1",
            outcome="SUCCESS",
            verify={"ok": True},
            counted=True,
        )
        assert db.confirmation_count("P-1") == 1
        db.record_validation(
            episode_id="EP-3",
            protocol_id="P-1",
            client_id="C-2",
            independent_group="G-2",
            outcome="SUCCESS",
            verify={"ok": True},
            counted=True,
        )
        db.record_validation(
            episode_id="EP-4",
            protocol_id="P-1",
            client_id="C-3",
            independent_group="G-3",
            outcome="SUCCESS",
            verify={"ok": True},
            counted=True,
        )
        assert db.confirmation_count("P-1") == 3

    print("PASS doctor_server_v2_store")


if __name__ == "__main__":
    main()
