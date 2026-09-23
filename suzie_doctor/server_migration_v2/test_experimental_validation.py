from pathlib import Path
from tempfile import TemporaryDirectory
import sys

ROOT=Path(__file__).resolve().parents[2]
LIVE=ROOT/'suzie_doctor/live_runtime/doctor_server'
sys.path.insert(0,str(LIVE))
from doctor_v2_live import DoctorV2Runtime


def main():
    with TemporaryDirectory() as td:
        db=Path(td)/'server.sqlite3'
        rt=DoctorV2Runtime(db,LIVE/'doctor_v2_schema.sql')
        try:
            assert rt._normalize_field_case_cursor('60') == 60
            assert rt._normalize_field_case_cursor('E2E:60') == 60
            assert rt._normalize_field_case_cursor('broken') == 0
            with rt.conn:
                rt.conn.execute(
                    "insert into doctor_v2_wilson_cursors(stream,cursor_value) values('FIELD_CASE_REPORTS','E2E:60')"
                )
            assert rt.ensure_wilson_baseline() == '60'
            cur=rt.conn.execute(
                "select cursor_value from doctor_v2_wilson_cursors where stream='FIELD_CASE_REPORTS'"
            ).fetchone()
            assert cur and cur[0] == '60'

            assert rt.house_priority_from_payload({'evidence':{'severity':'CRITICAL'}}) == 100
            assert rt.house_priority_from_payload({'evidence':{'severity':'PROBLEM'}}) == 70
            assert rt.house_priority_from_payload({'evidence':{'severity':'LOW'}}) == 50
            assert rt.house_priority_from_payload({'evidence':{}}) == 50

            pid='EXP-TEST-001'; disease='DISEASE-TEST-001'; patient='patient-exp'
            rt.store.upsert_protocol_candidate(
                protocol_id=pid,origin='INTERNAL_FIELD',disease_id=disease,
                candidate={
                    'protocol_id':pid,'disease_id':disease,
                    'title':'Controlled test protocol',
                    'symptoms':['controlled functional failure'],
                    'checks':['confirm controlled functional failure'],
                    'action':'controlled validation action',
                    'verify':['controlled functional criterion restored'],
                    'risk':'LOW','automation_class':'AUTO_SAFE',
                },
            )
            rt.store.ensure_patient(patient,{'disease_id':disease,'symptom':'controlled functional failure'})
            event=rt.store.append_event(
                patient_id=patient,event_type='OBSERVATION',source='test',
                payload={'disease_id':disease,'symptom':'controlled functional failure'},
                create_house_job=True,
            )
            job=rt.store.claim_house_job('house-dialog')
            house=rt.house_get(int(job['house_job_id']))
            matches=house['experimental_protocol_candidates']
            assert matches and matches[0]['protocol_id']==pid, matches
            assert matches[0]['validation_stage']=='0/3'
            rt.store.ensure_patient(patient,{'health':'normal'})
            unrelated_event=rt.store.append_event(
                patient_id=patient,event_type='OBSERVATION',source='test-unrelated',
                payload={'symptom':'totally unrelated current trigger'},create_house_job=True,
            )
            unrelated_job=rt.conn.execute(
                'select house_job_id from doctor_v2_house_jobs where trigger_event_id=?',(unrelated_event,)
            ).fetchone()
            unrelated=rt.house_get(int(unrelated_job['house_job_id']))
            assert unrelated['experimental_protocol_candidates']==[], unrelated['experimental_protocol_candidates']
            decided=rt.house_decide(int(job['house_job_id']),{
                'finding_class':'CASE','significance':'MEDIUM','decision':'DISPATCH_SUZIE','field_priority':'NORMAL',
                'house_directive':'VALIDATE_FIRST','experimental_protocol_id':pid,'validation_stage':'0/3',
            })
            q=decided['field_queue']; assert q['house_directive']=='VALIDATE_FIRST'
            rt.set_field_legacy_case(int(q['queue_id']),101)
            result={'experimental_validation':{
                'protocol_id':pid,'independent_diagnosis_performed':True,'disease_confirmed':True,
                'applicable':True,'attempted':True,'risk_decision':'PROCEED','treatment_result':'FAILED',
                'verify_result':'FAIL','continued_case_diagnosis':True,'reason':'controlled negative','evidence':{},
            }}
            neg=rt.normalize_field_validation_result(101,patient,result)
            assert neg and not neg['success'] and neg['verified']
            w1=rt.enqueue_field_validation_wilson(101,neg,result)
            assert rt.claim_wilson(w1)
            out=rt.wilson_complete(w1,{'validations':[dict(neg)]},output_cursor='EXP:101')
            assert out['validation_results'][0]['validation_stage']=='0/3'
            assert out['validation_results'][0]['negative_episodes']==1
            cur=rt.conn.execute(
                "select cursor_value from doctor_v2_wilson_cursors where stream='FIELD_CASE_REPORTS'"
            ).fetchone()
            assert cur and cur[0] == '60', 'validation Wilson output_cursor must not move FIELD_CASE_REPORTS cursor'
            for n in range(1,4):
                val={
                    'protocol_id':pid,'episode_key':f'field:{101+n}','source':'FIELD_CASE','internal_verified':True,
                    'patient_id':patient,'field_case_id':str(101+n),'success':True,'verified':True,
                    'evidence':{'verify':'PASS','n':n},
                }
                wid=rt.enqueue_wilson('HOURLY_REVIEW',{'required_validations':[val]},f'FIELD_VALIDATION:{101+n}')
                assert rt.claim_wilson(wid)
                out=rt.wilson_complete(wid,{'validations':[dict(val)]},output_cursor=f'EXP:{101+n}')
                assert out['validation_results'][0]['validation_stage']==f'{n}/3'
            snap=rt.store.protocol_candidate(pid)
            assert snap['state']=='VALIDATED_3_3' and snap['validation_stage']=='3/3'
            pub=rt.conn.execute('select status from doctor_v2_protocol_publication_queue where protocol_id=?',(pid,)).fetchone()
            assert pub and pub['status']=='WAITING'
            assert snap['state']!='APPROVED_ACTIVE'

            # A claimed House job with no OPEN dialog must self-heal instead of
            # holding the only reserved House slot forever.
            rt.store.append_event(
                patient_id=patient,event_type='OBSERVATION',source='stuck-test',
                payload={'disease_id':disease,'symptom':'stuck house'},
                priority=77,create_house_job=True,
            )
            stuck=rt.next_house_waiting(); assert stuck
            assignment=f"house:{int(stuck['house_job_id'])}"
            slot=rt.role_acquire('HOUSE',assignment); assert slot == 'house-1'
            claimed=rt.claim_house(int(stuck['house_job_id']),'dlg-stuck'); assert claimed
            rt.dialog_open('dlg-stuck','HOUSE',assignment,'house-project',1)
            ses=rt.open_session('dlg-stuck',{})
            rt.store.end_session('dlg-stuck',int(ses['ordinal']),'NATURAL')
            recovered=rt.recover_stranded_house_wilson()
            assert recovered and recovered[0]['assignment_id']==assignment
            jobrow=rt.conn.execute(
                'select status,claimed_dialog_id from doctor_v2_house_jobs where house_job_id=?',
                (int(stuck['house_job_id']),),
            ).fetchone()
            assert jobrow['status']=='WAITING' and jobrow['claimed_dialog_id'] is None
            slotrow=rt.conn.execute(
                "select state,assignment_id from doctor_v2_role_slots where slot_id='house-1'"
            ).fetchone()
            assert slotrow['state']=='FREE' and slotrow['assignment_id'] is None
            print('EXPERIMENTAL_VALIDATION_CHAIN_TEST_PASS')
        finally:
            rt.store.close()

if __name__=='__main__': main()
