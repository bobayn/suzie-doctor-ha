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
from doctor_v2_live import DoctorV2Runtime
from suzie_doctor.protocol_engine import ProtocolEngine

class DB:
    def begin_protocol_run(self,**kw): return 1
    def finish_protocol_run(self,*a,**kw): self.finished=(a,kw)
class Sup:
    async def info(self): return {'homeassistant':'test','operating_system':'HAOS','arch':'aarch64'}
    async def reboot_host(self): return {'accepted':True}
class HA:
    def __init__(self,active=False): self.active=active; self.calls=[]
    async def call_service(self,d,s,data): self.calls.append((d,s,data)); return True
    async def list_repairs(self): return ([{'domain':'hacs','issue_id':'x','active':True}] if self.active else [])

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
    e3=ProtocolEngine(DB(),Sup(),HA(True),app_version='t',bridge_version='t',pack_version='t')
    failed=await e3.execute_card(card('reload_subsystem',{'subsystem':'automation'}),context={'field_action_authorized':True},risk_assessment=risk(),execution_actor='field_suzie')
    assert failed['result']=='FAILED' and failed['verify_passed'] is False and failed['new_protocol_evidence'] is False
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
    assert 'FIELD_CASE_DIAGNOSTIC' in doctor_mcp
    assert 'evidence["field_case_id"] = int(state["case_id"])' in doctor_mcp
    assert 'field_case_route = routing_intent == "FIELD_CASE_DIAGNOSTIC"' in server
    assert 'field_case_diagnosis_no_match' in server
    assert any(x.get('name')=='doctor.action.request' for x in contract['tools'])
    assert {x['name'] for x in contract['field_actions']} >= {'integration.reload','addon.restart','core.restart','host.reboot'}
    assert 'Field action command was already signed/executed' in server
    assert 'same Field action already attempted in this Case' in server
    assert 'field_binding_sha256' in server and 'expected_field_binding' in client
    assert '/v1/field-action-resume' in server and 'deferred_result_submission' in app
    assert 'CONNECTION_LOST_EXPECTED' in server and 'VERIFY_PENDING' in app
    assert 'Field HUMAN_REQUIRED requires human_requirement.type and reason' in server
    assert 'Field MISSING_CAPABILITY requires exact human_requirement.capability' in server
    assert 'House HUMAN_ACTION_REQUIRED requires human_requirement.type and reason' in extension
    assert 'MISSING_CAPABILITY requires exact human_requirement.capability' in extension
    assert 'House MISSING_CAPABILITY conflicts with available Field action capability; re-evaluate' in extension
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
        rt=DoctorV2Runtime(Path(td)/'v2.db',LIVE/'doctor_v2_schema.sql')
        a=rt.journal_to_house('patient-123456','test',repair_payload(['core.restart']),fingerprint='repair:hacs:x')
        b=rt.journal_to_house('patient-123456','test',repair_payload(['core.restart']),fingerprint='repair:hacs:x')
        assert not a['deduplicated'] and b['deduplicated']
        rt.conn.execute("update doctor_v2_house_jobs set status='DONE' where house_job_id=?",(a['house_job_id'],))
        info=rt._repair_resolution_info(repair_payload(['core.restart']),'repair:hacs:x')
        rt.conn.execute("update doctor_v2_resolutions set state='WAITING_HUMAN',human_requirement_json=?,capabilities_hash=?,material_hash=?,next_recheck_at=datetime('now','+15 minutes')",(rt._j({'type':'PHYSICAL_ACTION','reason':'bounded physical step'}),info['capabilities_hash'],info['material_hash']))
        rt.conn.commit()
        wait=rt.journal_to_house('patient-123456','test',repair_payload(['core.restart']),fingerprint='repair:hacs:x')
        assert wait['deduplicated'] and wait['resolution_state']=='WAITING_HUMAN'
        changed=rt.journal_to_house('patient-123456','test',repair_payload(['core.restart','host.reboot']),fingerprint='repair:hacs:x')
        assert not changed['deduplicated'] and changed['house_job_id']!=a['house_job_id']
    asyncio.run(engine_checks())
    print('FIELD_ACTION_E2E_TEST_PASS')

if __name__=='__main__': main()
