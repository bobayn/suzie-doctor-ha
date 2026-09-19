import asyncio
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from suzie_doctor import APP_VERSION, BRIDGE_VERSION, PROTOCOL_PACK_VERSION
from suzie_doctor import app
from suzie_doctor.connector import DoctorConnector, ConnectorError
from suzie_doctor.db import Database
from suzie_doctor.protocol_engine import ProtocolEngine
from suzie_doctor.suite import ROOT
from suzie_doctor.suite_selftest import suite_selftest


class SuiteTests(unittest.IsolatedAsyncioTestCase):
    async def test_release_checks(self):
        checks = await suite_selftest()
        self.assertTrue(all(checks.values()), checks)

    async def test_existing_pure_regressions(self):
        with TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / 'test.sqlite3'); db.initialize()
            engine = ProtocolEngine(db, object(), object(), app_version=APP_VERSION,
                bridge_version=BRIDGE_VERSION, pack_version=PROTOCOL_PACK_VERSION, pack_root=ROOT/'protocol_pack')
            rt = SimpleNamespace(options=SimpleNamespace(developer_mode=True), db=db, protocol_engine=engine)
            request = SimpleNamespace(app={'runtime':rt})
            handlers = [app.api_dev_filesystem_readonly_regression_test, app.api_dev_trigger_matching_test,
                app.api_dev_mount_recovery_test, app.api_dev_recurrence_test, app.api_dev_retention_test,
                app.api_dev_recommendation_executor_test, app.api_dev_generated_protocol_test, app.api_dev_manual_protocol_test]
            try:
                for handler in handlers:
                    with self.subTest(suite=handler.__name__):
                        result = json.loads((await handler(request)).text)
                        self.assertEqual(result['result'], 'PASS', result)
            finally: db.conn.close()

    async def test_unavailable_does_not_call_backend(self):
        c = DoctorConnector(object(), object())
        called=[]
        async def call(*args): called.append(args); return True
        with self.assertRaises(ConnectorError):
            await c.dispatch('install_update', {'entity_id':'update.test'}, {}, call)
        self.assertFalse(called)

    async def test_unknown_health_remains_unknown(self):
        class Empty:
            async def get_config(self): return {}
            async def info(self): return {}
        c = DoctorConnector(Empty(), Empty()); await c.refresh_health()
        self.assertEqual(c.health, {'ha':'unknown','supervisor':'unknown'})

    async def test_statuses_and_schemas_block(self):
        with TemporaryDirectory() as tmp:
            db=Database(Path(tmp)/'t.db');db.initialize()
            engine=ProtocolEngine(db,object(),object(),app_version=APP_VERSION,
                bridge_version=BRIDGE_VERSION,pack_version=PROTOCOL_PACK_VERSION)
            try:
                for status in ['WATCH','MANUAL','SUSPENDED','AWAITING_PROTOCOL_REVIEW']:
                    card={'schema_version':1,'protocol':{'status':status},'automation_class':'CONFIRM_REQUIRED'}
                    allowed,_=engine._treatment_allowed(card,trust_mode='full_trust',explicit_confirmation=True,developer_override=False)
                    self.assertFalse(allowed)
                for schema in [0,2,'1',None,True]:
                    card={'schema_version':schema,'protocol':{'status':'ACTIVE'},'automation_class':'CONFIRM_REQUIRED'}
                    allowed,_=engine._treatment_allowed(card,trust_mode='full_trust',explicit_confirmation=True,developer_override=False)
                    self.assertFalse(allowed)
            finally: db.conn.close()

    async def test_diagnose_endpoint_never_executes(self):
        calls=[]
        async def diagnose(body,**kwargs): calls.append(kwargs);return {'result':'DIAGNOSIS_ONLY'}
        class Request:
            app={'runtime':SimpleNamespace(doctor_server_diagnose=diagnose)}
            async def json(self): return {'symptoms':['test']}
        await app.api_connector_diagnose(Request())
        self.assertEqual(calls,[{'execute':False}])
        class BadRequest(Request):
            async def json(self): return {'execute':True}
        with self.assertRaises(app.web.HTTPBadRequest): await app.api_connector_diagnose(BadRequest())
        self.assertEqual(len(calls),1)

    def test_release_contains_no_master_kb(self):
        files=list((ROOT/'skills').rglob('*'))
        self.assertTrue((ROOT/'skills/suzie-doctor/SKILL.md').is_file())
        for p in files:
            self.assertNotIn(p.name, {'forum_knowledge_base.json','compiled_knowledge.json','generated_protocols.json'})


if __name__ == '__main__': unittest.main()
