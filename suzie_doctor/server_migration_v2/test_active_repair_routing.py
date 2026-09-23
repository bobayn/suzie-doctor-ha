from pathlib import Path
from tempfile import TemporaryDirectory
import sys

ROOT=Path(__file__).resolve().parents[2]
LIVE=ROOT/'suzie_doctor/live_runtime/doctor_server'
sys.path.insert(0,str(LIVE))
from doctor_v2_live import DoctorV2Runtime


def main():
    audit=(ROOT/'suzie_doctor/rootfs/app/suzie_doctor/audit.py').read_text()
    app=(ROOT/'suzie_doctor/rootfs/app/suzie_doctor/app.py').read_text()
    extension=(LIVE/'doctor_v2_extension.py').read_text()
    server=(LIVE/'server.py').read_text()

    assert 'HA Repair warning reclassified to OBSERVE' not in audit
    assert '"terminal_resolution_required": True' in audit
    assert '"type": "ha_repair_absent"' in audit
    assert 'reason="repair_followup"' in app
    assert '"terminal_resolution_required", "is_fixable"' in app
    assert 'Active Home Assistant Repair requires DISPATCH_SUZIE or HUMAN_ACTION_REQUIRED until verified absent' in extension
    assert 'do NOT OBSERVE, RECHECK_LATER or IGNORE_AS_NOISE' in extension
    assert 'def attest_repair_resolution(' in server
    assert 'Active HA Repair SUCCESS requires attested ha.repairs.list verification' in server
    assert 'Active HA Repair is still present; Case cannot close SUCCESS' in server

    with TemporaryDirectory() as td:
        rt=DoctorV2Runtime(Path(td)/'server.sqlite3',LIVE/'doctor_v2_schema.sql')
        try:
            payload={
                'evidence':{
                    'kind':'repair','severity':'warning','active':True,
                    'terminal_resolution_required':True,
                    'domain':'sensor','issue_id':'units_changed_sensor.test_cycles',
                }
            }
            assert rt.house_priority_from_payload(payload)==85
            first=rt.journal_to_house(
                'patient-repair','diagnose',payload,
                fingerprint='repair:sensor:units_changed_sensor.test_cycles',
            )
            second=rt.journal_to_house(
                'patient-repair','diagnose',payload,
                fingerprint='repair:sensor:units_changed_sensor.test_cycles',
            )
            assert first['house_job_id']==second['house_job_id']
            assert second['deduplicated'] is True
            count=rt.conn.execute('select count(*) from doctor_v2_house_jobs').fetchone()[0]
            assert count==1
            print('ACTIVE_REPAIR_ROUTING_TEST_PASS',first['house_job_id'])
        finally:
            rt.store.close()

if __name__=='__main__': main()
