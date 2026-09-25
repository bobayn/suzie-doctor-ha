from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio
import json
import sys

ROOT=Path(__file__).resolve().parents[2]
LIVE=ROOT/'suzie_doctor/live_runtime/doctor_server'
APPROOT=ROOT/'suzie_doctor/rootfs/app'
sys.path.insert(0,str(LIVE))
sys.path.insert(0,str(APPROOT))
from command_bridge import ClientCommandBridge
from case_journal import CaseJournal
from doctor_v2_live import DoctorV2Runtime
from suzie_doctor.protocol_engine import ProtocolEngine
from suzie_doctor.repair_identity import repair_identity, repair_verify_criterion

class DB:
    def begin_protocol_run(self,**kw): return 1
    def finish_protocol_run(self,*a,**kw): self.finished=(a,kw)
class Sup:
    async def info(self): return {'homeassistant':'test','operating_system':'HAOS','arch':'aarch64'}
    async def reboot_host(self): return {'accepted':True}
    async def restart_core(self): raise TimeoutError('test disconnect')
    async def reload_mount_detailed(self,name): return {'ok':True,'name':name}
class HA:
    def __init__(self,active=False,repairs=None): self.active=active; self.repairs=repairs; self.calls=[]
    async def call_service(self,d,s,data): self.calls.append((d,s,data)); return True
    async def list_repairs(self):
        if self.repairs is not None: return self.repairs
        return ([{'domain':'hacs','issue_id':'x','active':True}] if self.active else [])

def risk():
    return {'harm_probability':'LOW','irreversibility':'REVERSIBLE','harm_magnitude':'LOW','decision':'PROCEED','rationale':'bounded regression'}

def card(primitive,args,disconnect=False):
    return {'schema_version':1,'disease_id':'FIELD-UNCLASSIFIED','title':'one','component':'test','protocol':{'id':'FIELD-ACTION-test','version':'1','status':'FIELD_ONE_SHOT'},'automation_class':'CONFIRM_REQUIRED','diagnostics':[{'id':'criterion','primitive':'ha_repair_absent','args':{'domain':'hacs','issue_id':'x'},'save_as':'original_functional_pass'}],'confirm':{'all':['field_action_authorized']},'exclude':[],'preconditions':[],'checkpoint':{'required':False},'treatment':[{'step':'field_action','primitive':primitive,'args':args,'max_attempts':1}],'verify':{'rerun_diagnostics':True,'success_when':'conditions','conditions':{'all':[{'expr':'original_functional_pass == true'}]}},'rollback':[],'field_action':{'policy':{'disconnect_expected':disconnect}}}

def repair_payload(caps):
    return {'problem_key':'repair:hacs:x','evidence':{'kind':'repair','problem_key':'repair:hacs:x','domain':'hacs','issue_id':'x','active':True,'terminal_resolution_required':True,'is_fixable':True,'field_action_capabilities':caps,'resolution_criterion':{'type':'ha_repair_absent','domain':'hacs','issue_id':'x'}}}

async def engine_checks():
    e=ProtocolEngine(DB(),Sup(),HA(False),app_version='t',bridge_version='t',pack_version='t')
    out=await e.execute_card(card('reload_subsystem',{'subsystem':'automation'}),context={'field_action_authorized':True},risk_assessment=risk(),execution_actor='field_suzie')
    assert out['result']=='SUCCESS' and out['verify_passed'] is True and out['new_protocol_evidence'] is True
    e2=ProtocolEngine(DB(),Sup(),HA(False),app_version='t',bridge_version='t',pack_version='t')
    out2=await e2.execute_card(card('reboot_host',{},True),context={'field_action_authorized':True},risk_assessment=risk(),execution_actor='field_suzie')
    assert out2['result']=='CONNECTION_LOST_EXPECTED' and out2['verify_pending'] is True and out2['verify_performed'] is False
    verify=await e2.verify_field_one_shot(card('reboot_host',{},True),context={})
    assert verify['result']=='VERIFIED_PASS' and verify['verify_passed'] is True
    e4=ProtocolEngine(DB(),Sup(),HA(False),app_version='t',bridge_version='t',pack_version='t')
    pending=await e4.execute_card(card('restart_core',{},True),context={'field_action_authorized':True},risk_assessment=risk(),execution_actor='field_suzie')
    assert pending['result']=='CONNECTION_LOST_EXPECTED' and pending['verify_pending'] is True and pending['treatment'][0]['attempts']==1 and pending['treatment'][0]['ok'] is None
    resumed=await e4.verify_field_one_shot(card('restart_core',{},True),context={})
    assert resumed['result']=='VERIFIED_PASS' and resumed['verify_passed'] is True
    e3=ProtocolEngine(DB(),Sup(),HA(True),app_version='t',bridge_version='t',pack_version='t')
    failed=await e3.execute_card(card('reload_subsystem',{'subsystem':'automation'}),context={'field_action_authorized':True},risk_assessment=risk(),execution_actor='field_suzie')
    assert failed['result']=='FAILED' and failed['verify_passed'] is False and failed['new_protocol_evidence'] is False
    old_ident=repair_identity(domain='hassio',issue_id='old-id',translation_key='issue_mount_mount_failed',translation_placeholders={'reference':'garage_camera_archive','storage_url':'/config/storage'})
    new_ident=repair_identity(domain='hassio',issue_id='new-id',translation_key='issue_mount_mount_failed',translation_placeholders={'reference':'garage_camera_archive','storage_url':'/config/storage'})
    assert old_ident['problem_key']==new_ident['problem_key'] and old_ident['identity_mode']=='semantic'
    semantic=card('reload_mount',{'name':'garage_camera_archive'})
    semantic['diagnostics'][0]['args']=repair_verify_criterion(old_ident)
    rotated=[{'domain':'hassio','issue_id':'new-id','translation_key':'issue_mount_mount_failed','translation_placeholders':{'reference':'garage_camera_archive','storage_url':'/config/storage'},'active':True}]
    sem_engine=ProtocolEngine(DB(),Sup(),HA(repairs=rotated),app_version='t',bridge_version='t',pack_version='t')
    sem_fail=await sem_engine.verify_field_one_shot(semantic,context={})
    assert sem_fail['verify_passed'] is False
    sem_pass=await ProtocolEngine(DB(),Sup(),HA(repairs=[]),app_version='t',bridge_version='t',pack_version='t').verify_field_one_shot(semantic,context={})
    assert sem_pass['verify_passed'] is True
    allowed=e2._treatment_allowed(card('reboot_host',{},True),trust_mode='full_trust',risk_assessment=risk(),execution_actor='family_doctor',developer_override=False)
    assert allowed[0] is False and allowed[1]=='field_one_shot_field_only'

def main():
    doctor_mcp=(ROOT/'suzie_doctor/live_runtime/doctor_mcp/server.py').read_text()
    home_mcp=(ROOT/'suzie_doctor/live_runtime/suzie_home_mcp/server.py').read_text()
    server=(LIVE/'server.py').read_text()
    extension=(LIVE/'doctor_v2_extension.py').read_text()
    app=(APPROOT/'suzie_doctor/app.py').read_text()
    client=(APPROOT/'suzie_doctor/server_client.py').read_text()
    contract=json.loads((APPROOT/'suite/connector_contract.json').read_text())
    assert 'name="doctor.action.request"' in doctor_mcp
    assert 'name="doctor.action.request"' in home_mcp
    assert 'async def _field_action_request_via_core(' in doctor_mcp
    assert 'compatibility_request = evidence.pop("field_action_request", None)' in doctor_mcp
    assert 'field_action_request compatibility transport requires execute=true' in doctor_mcp
    assert 'canonical_tool' in doctor_mcp and 'doctor.action.request' in doctor_mcp
    assert 'schema, NOT MISSING_CAPABILITY' in server
    assert 'evidence.field_action_request' in server
    assert 'preempt_house_for_higher_priority' in extension
    assert 'doctor_v2_house_priority_preempted' in extension
    assert 'house_quantum_exhausted' in extension
    assert 'doctor_v2_house_quantum_yielded' in extension
    assert 'recover_overbudget_house' in extension
    assert 'doctor_v2_house_overbudget_recovered' in extension
    assert 'scheduler_yield_until' in (LIVE/'doctor_v2_schema.sql').read_text()
    assert 'Related findings are' in app and 'context only' in app
    assert 'journal_fingerprint = (' in server
    assert 'The primary evidence owns the journal fingerprint' in server
    assert 'fingerprint=journal_fingerprint' in server
    assert 'identity=repair_identity(domain,issue_id,item.get("translation_key"),placeholders)' in server
    assert 'active_keys.add(problem_key)' in server
    assert 'repair_verify_criterion(identity)' in server
    assert 'current_field_case_id=NULL' not in (LIVE/'doctor_v2_live.py').read_text().split('def journal_to_house',1)[1].split('def customer_feed',1)[0]
    assert 'active_field_case_for_house' in extension
    assert 'merge_house_evidence' in extension
    assert 'resolution_fingerprint' in extension
    assert 'FIELD_CASE_DIAGNOSTIC' in doctor_mcp
    assert 'evidence["field_case_id"] = int(state["case_id"])' in doctor_mcp
    assert 'field_case_route = routing_intent == "FIELD_CASE_DIAGNOSTIC"' in server
    assert 'field_case_diagnosis_no_match' in server
    assert any(x.get('name')=='doctor.action.request' for x in contract['tools'])
    assert {x['name'] for x in contract['field_actions']} >= {'integration.reload','addon.restart','core.restart','host.reboot','mount.reload'}
    assert 'Field action command was already signed/executed' in server
    assert 'same Field action already attempted in this Case' in server
    assert 'package_json is not null and length(trim(package_json))>2' in server
    assert 'signed_package and str(row["execution_state"] or "")=="VERIFIED_FAIL"' in extension
    assert '"subsystem.reload": {"primitive":"reload_subsystem"' not in server
    assert 'host.reboot exact_target.host must identify the local HAOS host' in server
    assert 'field_binding_sha256' in server and 'expected_field_binding' in client
    call_lab=(ROOT/'suzie_doctor/live_runtime/call_lab/server.py').read_text()
    assert 'def cdp_close_target(' in call_lab and 'failed_tab_closed' in call_lab
    journal=(LIVE/'case_journal.py').read_text()
    assert 'dispatch_retry_after' in journal and 'dispatch_failures' in journal
    assert 'def missing_dialog_candidates(' in journal and 'def recover_missing_dialog(' in journal
    assert "SET state='FAILED',outcome='FAILED'" in journal
    assert 'web_dialog_missing_command_protected' in server
    assert '_reconcile_missing_web_dialogs' in server
    assert '/v1/field-action-resume' in server and 'deferred_result_submission' in app
    assert 'CONNECTION_LOST_EXPECTED' in server and 'VERIFY_PENDING' in app
    assert '"core.restart": {"primitive":"restart_core"' in server and '"disconnect_expected":True' in server
    assert 'action must be a name string or object' in doctor_mcp
    assert 'Field HUMAN_REQUIRED requires human_requirement.type and reason' in server
    assert 'Field MISSING_CAPABILITY requires exact human_requirement.capability' in server
    assert 'House HUMAN_ACTION_REQUIRED requires human_requirement.type and reason' in extension
    assert 'MISSING_CAPABILITY requires exact human_requirement.capability' in extension
    assert 'House MISSING_CAPABILITY conflicts with available Field action capability; re-evaluate' in extension
    assert 'REPAIR_RESOLUTION_SUPERSEDED' in server
    assert 'retire_case(' in (LIVE/'case_journal.py').read_text()
    with TemporaryDirectory() as td:
        b=ClientCommandBridge(Path(td)/'commands.db')
        c=b.enqueue(client_id='client123',case_id=7,tool_name='doctor.action.request',arguments={'action':{'name':'core.restart'},'exact_target':{'component':'homeassistant_core'}},trusted_context={})
        p=b.poll(client_id='client123'); assert p['command_id']==c['command_id']
        c2=b.enqueue(client_id='client123',case_id=8,tool_name='doctor.action.request',arguments={'action':{'name':'addon.restart'},'exact_target':{'slug':'demo'}},trusted_context={})
        assert b.poll(client_id='client123') is None  # single mutation / claimed command serializes client
        b.store_signed_package(client_id='client123',command_id=c['command_id'],package={'package_id':'p'})
        for state in ('SIGNED','EXECUTING','VERIFY_PENDING','VERIFIED_PASS'):
            b.set_execution_state(client_id='client123',command_id=c['command_id'],state=state)
        b.finish(client_id='client123',command_id=c['command_id'],result={'ok':True},error=None)
        assert b.get(c['command_id'])['status']=='COMPLETED'
        p2=b.poll(client_id='client123'); assert p2['command_id']==c2['command_id']
    with TemporaryDirectory() as td:
        j=CaseJournal(Path(td)/'journal.db')
        case,created=j.escalate(client_id='client-backoff',source_key='backoff:test',source_request_id=None,summary='x',problem={'x':1})
        assert created
        first=j.reserve_web_dispatch(max_doctors=1); assert first and first['case']['case_id']==case['case_id']
        j.fail_dispatch(case_id=case['case_id'],session_id=first['session_id'],reason='synthetic failure')
        failed=j.get_case(case['case_id'])
        assert int(failed['dispatch_failures'])==1 and failed['dispatch_retry_after']
        assert j.reserve_web_dispatch(max_doctors=1) is None
        with j.conn:
            j.conn.execute("update doctor_cases set dispatch_retry_after=datetime('now','-1 second') where case_id=?",(case['case_id'],))
        second=j.reserve_web_dispatch(max_doctors=1); assert second
        j.finish_dispatch(case_id=case['case_id'],session_id=second['session_id'],dispatch_job_id='job2',dialog_id='dlg2',conversation_url='https://chatgpt.com/c/dlg2')
        assigned=j.get_case(case['case_id'])
        assert int(assigned['dispatch_failures'])==0 and assigned['dispatch_retry_after'] is None
    with TemporaryDirectory() as td:
        j=CaseJournal(Path(td)/'retire.db')
        case,created=j.escalate(client_id='client-retire',source_key='repair:old',source_request_id=None,summary='legacy repair',problem={'problem_key':'repair:old'})
        assert created and case['state']=='FOR_SUZIE'
        retired=j.retire_case(case['case_id'],reason='REPAIR_RESOLUTION_SUPERSEDED',detail={'fingerprint':'repair:old'})
        assert retired['state']=='CANCELLED'
        closed=j.get_case(case['case_id'])
        assert closed['state']=='CANCELLED' and closed['outcome']=='CANCELLED'
        assert closed['result']['retirement_reason']=='REPAIR_RESOLUTION_SUPERSEDED'
    with TemporaryDirectory() as td:
        j=CaseJournal(Path(td)/'missing-dialog.db')
        case,_=j.escalate(client_id='client-missing',source_key='missing:test',source_request_id=None,summary='x',problem={'x':1})
        res=j.reserve_web_dispatch(max_doctors=1); assert res
        j.finish_dispatch(case_id=case['case_id'],session_id=res['session_id'],dispatch_job_id='job',dialog_id='dlg-missing',conversation_url='https://chatgpt.com/c/dlg-missing')
        with j.conn:
            j.conn.execute("update doctor_sessions set last_seen=datetime('now','-5 minutes'),updated_at=datetime('now','-5 minutes') where session_id=?",(res['session_id'],))
        candidates=j.missing_dialog_candidates(set(),grace_seconds=90); assert candidates and int(candidates[0]['case_id'])==case['case_id']
        rec=j.recover_missing_dialog(case_id=case['case_id'],session_id=res['session_id'],max_requeues=2)
        assert rec['action']=='REQUEUED' and j.get_case(case['case_id'])['state']=='FOR_SUZIE'
        case2,_=j.escalate(client_id='client-missing',source_key='missing:terminal',source_request_id=None,summary='y',problem={'y':1})
        res2=j.reserve_web_dispatch(max_doctors=1); assert res2
        j.finish_dispatch(case_id=case2['case_id'],session_id=res2['session_id'],dispatch_job_id='job2',dialog_id='dlg-terminal',conversation_url='https://chatgpt.com/c/dlg-terminal')
        terminal=j.recover_missing_dialog(case_id=case2['case_id'],session_id=res2['session_id'],max_requeues=0)
        assert terminal['action']=='FAILED'
        failed=j.get_case(case2['case_id']); assert failed['state']=='FAILED' and failed['outcome']=='FAILED'
    with TemporaryDirectory() as td:
        rt=DoctorV2Runtime(Path(td)/'v2.db',LIVE/'doctor_v2_schema.sql')
        a=rt.journal_to_house('patient-123456','test',repair_payload(['core.restart']),fingerprint='repair:hacs:x')
        b=rt.journal_to_house('patient-123456','test',repair_payload(['core.restart']),fingerprint='repair:hacs:x')
        assert not a['deduplicated'] and b['deduplicated']
        rt.conn.execute("update doctor_v2_house_jobs set status='DONE' where house_job_id=?",(a['house_job_id'],))
        info=rt._repair_resolution_info(repair_payload(['core.restart']),'repair:hacs:x')
        rt.conn.execute("update doctor_v2_resolutions set state='WAITING_HUMAN',current_field_case_id=99,human_requirement_json=?,capabilities_hash=?,material_hash=?,next_recheck_at=datetime('now','+15 minutes')",(rt._j({'type':'PHYSICAL_ACTION','reason':'bounded physical step'}),info['capabilities_hash'],info['material_hash']))
        rt.conn.commit()
        wait=rt.journal_to_house('patient-123456','test',repair_payload(['core.restart']),fingerprint='repair:hacs:x')
        assert wait['deduplicated'] and wait['resolution_state']=='WAITING_HUMAN'
        changed=rt.journal_to_house('patient-123456','test',repair_payload(['core.restart','host.reboot']),fingerprint='repair:hacs:x')
        assert not changed['deduplicated'] and changed['house_job_id']!=a['house_job_id']
        current=rt.conn.execute("select current_field_case_id from doctor_v2_resolutions where patient_id='patient-123456' and fingerprint='repair:hacs:x'").fetchone()[0]
        assert current is None
    with TemporaryDirectory() as td:
        db=Path(td)/'repair-field-dedup.db'
        j=CaseJournal(db); rt=DoctorV2Runtime(db,LIVE/'doctor_v2_schema.sql')
        payload=repair_payload(['mount.reload'])
        first=rt.journal_to_house('patient-dedup','repair',payload,fingerprint='repair:hassio:x',priority=85)
        jid=int(first['house_job_id']); rt.role_acquire('HOUSE',f'house:{jid}'); rt.dialog_open('dd1','HOUSE',f'house:{jid}','house',1); assert rt.claim_house(jid,'dd1')
        dec=rt.house_decide(jid,{'finding_class':'INCIDENT','significance':'HIGH','decision':'DISPATCH_SUZIE','field_priority':'HIGH'})
        rt.mark_resolution_house_decision(jid,{'decision':'DISPATCH_SUZIE'})
        case,_=j.escalate(client_id='patient-dedup',source_key=f"v2-house-decision:{dec['decision_id']}",source_request_id=None,summary='repair field',problem={'problem_key':'repair:hassio:x','field_action_capabilities':['mount.reload']},priority=75)
        rt.set_field_legacy_case(int(dec['field_queue']['queue_id']),int(case['case_id']))
        same=rt.journal_to_house('patient-dedup','repair',payload,fingerprint='repair:hassio:x',priority=85)
        assert same['deduplicated'] is True and same['legacy_case_id']==case['case_id'] and same['field_status']=='ASSIGNED'
        assert rt.conn.execute('select count(*) from doctor_v2_house_jobs').fetchone()[0]==1
        related={'evidence':{'kind':'ha_runtime_error','problem_key':'ha_error:y','related_findings':[{'kind':'repair','problem_key':'repair:hassio:x','domain':'hassio','issue_id':'x'}]}}
        second=rt.journal_to_house('patient-dedup','runtime',related,fingerprint='ha_error:y',priority=75)
        jid2=int(second['house_job_id']); rt.role_acquire('HOUSE',f'house:{jid2}'); rt.dialog_open('dd2','HOUSE',f'house:{jid2}','house',1); assert rt.claim_house(jid2,'dd2')
        dec2=rt.house_decide(jid2,{'finding_class':'INCIDENT','significance':'HIGH','decision':'DISPATCH_SUZIE','field_priority':'HIGH','problem_key':'repair:hassio:x'})
        rt.mark_resolution_house_decision(jid2,{'decision':'DISPATCH_SUZIE','resolution_fingerprint':'repair:hassio:x'})
        found=rt.active_field_case_for_house(jid2,'repair:hassio:x'); assert found and found['case_id']==case['case_id']
        merged=j.merge_house_evidence(case['case_id'],{'house_job_id':jid2,'house_decision_id':dec2['decision_id'],'field_action_capabilities':['mount.reload','core.restart']})
        rt.set_field_legacy_case(int(dec2['field_queue']['queue_id']),int(case['case_id']))
        assert merged['case_id']==case['case_id'] and len(merged['problem']['related_house_updates'])==1
        assert rt.conn.execute("select count(*) from doctor_cases where state not in ('RESOLVED','FAILED','HUMAN_REQUIRED','CANCELLED')").fetchone()[0]==1
    with TemporaryDirectory() as td:
        rt=DoctorV2Runtime(Path(td)/'priority.db',LIVE/'doctor_v2_schema.sql')
        low=rt.journal_to_house('patient-priority','test',{'evidence':{'kind':'runtime','problem_key':'low'}},fingerprint='low',priority=50)
        low_id=int(low['house_job_id'])
        slot=rt.role_acquire('HOUSE',f'house:{low_id}')
        assert slot=='house-1'
        rt.dialog_open('dlg-low','HOUSE',f'house:{low_id}','house',1)
        assert rt.claim_house(low_id,'dlg-low')
        high=rt.journal_to_house('patient-priority','test',{'evidence':{'kind':'repair','problem_key':'high'}},fingerprint='high',priority=85)
        pre=rt.preempt_house_for_higher_priority(low_id,'dlg-low')
        assert pre and pre['higher_job_id']==int(high['house_job_id']) and pre['higher_priority']==85
        assert rt.conn.execute('select status from doctor_v2_house_jobs where house_job_id=?',(low_id,)).fetchone()[0]=='WAITING'
        assert rt.conn.execute("select state from doctor_v2_role_slots where slot_id='house-1'").fetchone()[0]=='FREE'
        assert rt.conn.execute("select state from doctor_v2_web_dialogs where dialog_id='dlg-low'").fetchone()[0]=='CLOSED_NATURAL'
    with TemporaryDirectory() as td:
        rt=DoctorV2Runtime(Path(td)/'quantum.db',LIVE/'doctor_v2_schema.sql')
        first=rt.journal_to_house('patient-q','test',{'evidence':{'problem_key':'q1'}},fingerprint='q1',priority=85)
        second=rt.journal_to_house('patient-q','test',{'evidence':{'problem_key':'q2'}},fingerprint='q2',priority=70)
        first_id=int(first['house_job_id']); second_id=int(second['house_job_id'])
        assert rt.role_acquire('HOUSE',f'house:{first_id}')=='house-1'
        opened=rt.dialog_open('dlg-q','HOUSE',f'house:{first_id}','house',1)
        assert rt.claim_house(first_id,'dlg-q')
        rt.dialog_end('dlg-q',opened['ordinal'],'WATCHDOG_10M')
        a=rt.open_session('dlg-q',{}); assert a['ordinal']==2
        rt.dialog_end('dlg-q',a['ordinal'],'WATCHDOG_10M')
        y=rt.house_quantum_exhausted(first_id,'dlg-q',max_sessions=2)
        assert y and y['reason']=='HOUSE_QUANTUM_EXHAUSTED' and y['next_job_id']==second_id
        row=rt.conn.execute('select status,scheduler_yield_until,scheduler_yield_count from doctor_v2_house_jobs where house_job_id=?',(first_id,)).fetchone()
        assert row['status']=='WAITING' and row['scheduler_yield_until'] and int(row['scheduler_yield_count'])==1
        nxt=rt.next_house_waiting(); assert int(nxt['house_job_id'])==second_id
    with TemporaryDirectory() as td:
        rt=DoctorV2Runtime(Path(td)/'legacy-overbudget.db',LIVE/'doctor_v2_schema.sql')
        first=rt.journal_to_house('patient-o','test',{'evidence':{'problem_key':'o1'}},fingerprint='o1',priority=85)
        second=rt.journal_to_house('patient-o','test',{'evidence':{'problem_key':'o2'}},fingerprint='o2',priority=70)
        first_id=int(first['house_job_id']); second_id=int(second['house_job_id'])
        assert rt.role_acquire('HOUSE',f'house:{first_id}')=='house-1'
        opened=rt.dialog_open('dlg-o','HOUSE',f'house:{first_id}','house',1)
        assert rt.claim_house(first_id,'dlg-o')
        rt.dialog_end('dlg-o',opened['ordinal'],'WATCHDOG_10M')
        a=rt.open_session('dlg-o',{}); assert a['ordinal']==2
        rt.dialog_end('dlg-o',a['ordinal'],'WATCHDOG_10M')
        current=rt.open_session('dlg-o',{})
        assert current['ordinal']==3
        rec=rt.recover_overbudget_house(max_sessions=2)
        assert rec and rec['reason']=='HOUSE_LEGACY_OVERBUDGET' and rec['next_job_id']==second_id
        assert rt.conn.execute('select status from doctor_v2_house_jobs where house_job_id=?',(first_id,)).fetchone()[0]=='WAITING'
        assert rt.conn.execute("select state from doctor_v2_role_slots where slot_id='house-1'").fetchone()[0]=='FREE'
    asyncio.run(engine_checks())
    print('FIELD_ACTION_E2E_TEST_PASS')

if __name__=='__main__': main()
