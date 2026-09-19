import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import yaml
from suzie_doctor import APP_VERSION, BRIDGE_VERSION, PROTOCOL_PACK_VERSION
from suzie_doctor.app import Runtime
from suzie_doctor.connector import ConnectorError
from suzie_doctor.db import Database
from suzie_doctor.protocol_engine import ProtocolEngine
from suzie_doctor.suite import ROOT
from suzie_doctor.transport import DoctorConnectorCore, WebAdapter, APIAdapter, SessionContext, CONTRACT_FIELDS


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def run_surface(self, surface, *, failure=False, confirmed=True, mismatch=None, permission=True, operation="doctor.treat", injected=None):
        trace=[]
        card=yaml.safe_load((ROOT/'protocol_pack/cards/mqtt_duplicate_client_id.yaml').read_text())
        card['protocol']['status']='ACTIVE'
        card['preconditions']=[]
        card['treatment'][0]['args']['message']='treat'
        card['rollback']=[{'primitive':'notify_user','args':{'message':'rollback'}}]
        class Backend:
            broken=True
            async def get_config(self): return {'version':'fixture'}
            async def info(self): return {'homeassistant':'fixture'}
            async def addon_logs(self, slug, **kwargs):
                trace.append('diagnose')
                return 'Client test already connected, closing old connection.' if self.broken else 'ok'
            async def persistent_notification(self, *args, **kwargs):
                message=kwargs.get('message', args[1] if len(args)>1 else '')
                trace.append(message)
                if message=='treat' and not failure: self.broken=False
                return True
        class Server:
            client_id="fixture-client"
            async def ensure_enrolled(self): pass
            async def diagnose(self,evidence): return {'execution_packages':[{'fixture':True}]}
            def validate_execution_package(self,package):
                trace.append('validate_package')
                return copy.deepcopy(card)
        with TemporaryDirectory() as tmp:
            db=Database(Path(tmp)/'t.db');db.initialize()
            backend=Backend()
            engine=ProtocolEngine(db,backend,backend,app_version=APP_VERSION,
                bridge_version=BRIDGE_VERSION,pack_version=PROTOCOL_PACK_VERSION)
            rt=SimpleNamespace(protocol_engine=engine,doctor_server=Server(),options=SimpleNamespace(trust_mode='safe_auto'))
            async def diagnose(evidence,**kwargs): return await Runtime.doctor_server_diagnose(rt,evidence,**kwargs)
            rt.doctor_server_diagnose=diagnose
            adapter=surface(DoctorConnectorCore(rt))
            if mismatch:
                adapter.declaration[mismatch]='incompatible'
            session=SessionContext(frozenset({'doctor.read','doctor.treat'} if permission else {'doctor.read'}),confirmed)
            def envelope(name,args): return {('tool' if surface is WebAdapter else 'name'):name,'arguments':args}
            try:
                caps=await adapter.call(envelope('doctor.capabilities',{}),session)
                skill=await adapter.call(envelope('doctor.skill',{}),session)
                evidence={'confirmed_disease_id':card['disease_id'], **(injected or {})}
                result=await adapter.call(envelope(operation,{'evidence':evidence}),session)
                # Only run identity/timing differ; compare every policy/result field.
                def stable(value):
                    if isinstance(value,dict): return {k:stable(v) for k,v in value.items() if k not in {'protocol_run_id','duration_ms'}}
                    if isinstance(value,list): return [stable(v) for v in value]
                    return value
                return stable(result),trace,caps,skill
            finally: db.conn.close()

    async def test_same_protocol_success_and_failed_verify_rollback(self):
        for failure in [False, True]:
            with self.subTest(failure=failure):
                web=await self.run_surface(WebAdapter,failure=failure)
                api=await self.run_surface(APIAdapter,failure=failure)
                self.assertEqual(web,api)
                self.assertIn('treat',web[1])
                self.assertEqual('rollback' in web[1],failure)
                self.assertGreaterEqual(web[1].count('diagnose'),2)

    async def test_same_confirmation_gate(self):
        web=await self.run_surface(WebAdapter,confirmed=False)
        api=await self.run_surface(APIAdapter,confirmed=False)
        self.assertEqual(web,api)
        self.assertNotIn('treat',web[1])
        self.assertIn('confirmation_required',json.dumps(web[0]))

    async def test_every_contract_mismatch_blocks_before_server_execution(self):
        for field in CONTRACT_FIELDS:
            web=await self.run_surface(WebAdapter,mismatch=field)
            api=await self.run_surface(APIAdapter,mismatch=field)
            self.assertEqual(web,api)
            self.assertEqual(web[0]['result'],'ADAPTER_INCOMPATIBLE')
            self.assertEqual(web[1],[])
            self.assertTrue(web[2]['compatibility']['diagnosis_allowed'])

    async def test_missing_permission(self):
        for adapter in [WebAdapter,APIAdapter]:
            with self.assertRaisesRegex(ConnectorError,'permission_denied:doctor.treat'):
                await self.run_surface(adapter,permission=False)

    async def test_incompatible_adapter_can_still_diagnose(self):
        for surface in [WebAdapter, APIAdapter]:
            result, trace, caps, skill = await self.run_surface(surface, mismatch='skill_schema_version', operation='doctor.diagnose')
            self.assertEqual(result['execution_results'], [])
            self.assertEqual(trace, [])
            self.assertIn('references/connector.md', skill['references'])

    async def test_model_cannot_inject_session_policy(self):
        for surface in [WebAdapter, APIAdapter]:
            for field in ['execute', 'explicit_confirmation', 'trust_mode', 'developer_override']:
                with self.assertRaisesRegex(ConnectorError, 'session_policy_is_not_a_tool_argument'):
                    await self.run_surface(surface, injected={field: True})
