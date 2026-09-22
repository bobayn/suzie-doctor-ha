from __future__ import annotations
import importlib.util,json,tempfile
from pathlib import Path

MOD=Path('/opt/suzie-doctor-server/incident_curate.py')

def loadmod():
    spec=importlib.util.spec_from_file_location('curate',MOD)
    m=importlib.util.module_from_spec(spec);assert spec and spec.loader;spec.loader.exec_module(m);return m

def main():
    m=loadmod();cases=[]
    def add(i,ok,detail=None):
        x={'id':i,'pass':bool(ok)}
        if detail is not None:x['detail']=detail
        cases.append(x)

    a={'id':'INC-INGEST-HA-A','source':'https://example.com/a','title':'Same failure','root_cause':'same root cause confirmed','symptoms':'same symptom','status':'CONFIRMED','scope':'HA'}
    b={'id':'INC-OLD','source':'https://example.com/a','title':'Same failure','root_cause':'same root cause confirmed','symptoms':'same symptom','status':'CONFIRMED','scope':'HA'}
    add('strong_duplicate_accepts_exact',m.strong_duplicate(a,b)['ok'])
    c=dict(b);c['source']='https://example.com/other'
    add('strong_duplicate_requires_same_source',not m.strong_duplicate(a,c)['ok'])
    add('disease_id_stable',m.generated_disease_id(a)==m.generated_disease_id(a))

    with tempfile.TemporaryDirectory(prefix='doctor-curate-test-') as td:
        root=Path(td);base=root/'q'
        for p in (base,base/'inbox',base/'results',base/'processed'):p.mkdir(parents=True,exist_ok=True)
        master=root/'master.json';norm=root/'norm.json';ledger=root/'ledger.json';audit=root/'audit.jsonl'
        master.write_text(json.dumps({'incidents':[a,b],'recipes':[],'recurring_patterns':[]})+'\n')
        norm.write_text(json.dumps({'stats':{'incidents':2,'diseases':1,'unclassified_incidents':1,'executable_protocols':4},'diseases':[{'disease_id':'DISEASE-OLD','incident_refs':['INC-OLD'],'protocols':[],'protocol_candidates':[]}],'unclassified_incident_refs':['INC-INGEST-HA-A']})+'\n')
        ledger.write_text(json.dumps(m.blank_ledger())+'\n')
        m.BASE=base;m.INBOX=base/'inbox';m.RESULTS=base/'results';m.PROCESSED=base/'processed';m.LOCK=base/'lock'
        m.MASTER=master;m.NORMALIZED=norm;m.LEDGER=ledger;m.AUDIT=audit

        req={'request_id':'dry-remove','action':'remove_duplicate','incident_id':'INC-INGEST-HA-A','target_incident_id':'INC-OLD','reason':'Synthetic exact duplicate self-test evidence only.','dry_run':True}
        q=m.INBOX/'dry-remove.json';q.write_text(json.dumps(req));m.process(q)
        res=json.loads((m.RESULTS/'dry-remove.json').read_text())
        add('dry_remove_reports_would_remove',res.get('result')=='WOULD_REMOVE_DUPLICATE')
        add('dry_remove_does_not_mutate',len(json.loads(master.read_text())['incidents'])==2)

        m.compiler_run=lambda:(True,'ok')
        req['request_id']='commit-remove';req['dry_run']=False
        q=m.INBOX/'commit-remove.json';q.write_text(json.dumps(req));m.process(q)
        res=json.loads((m.RESULTS/'commit-remove.json').read_text())
        add('commit_remove_succeeds',res.get('result')=='REMOVED_DUPLICATE')
        add('commit_remove_removes_one',len(json.loads(master.read_text())['incidents'])==1)

        # Rollback test: classification mutates ledger first, then compiler fails.
        master.write_text(json.dumps({'incidents':[b],'recipes':[],'recurring_patterns':[]})+'\n')
        before=ledger.read_bytes()
        m.compiler_run=lambda:(False,'forced failure')
        req2={'request_id':'rollback','action':'classify_treatment','incident_id':'INC-OLD','reason':'Forced compiler failure must restore the curation ledger.','treatment_classification':'CONFIRM_REQUIRED','dry_run':False}
        q=m.INBOX/'rollback.json';q.write_text(json.dumps(req2));m.process(q)
        res2=json.loads((m.RESULTS/'rollback.json').read_text())
        add('compiler_failure_rejected',res2.get('result')=='REJECTED')
        add('compiler_failure_rolls_back_ledger',ledger.read_bytes()==before)

    result='PASS' if all(x['pass'] for x in cases) else 'FAIL'
    print(json.dumps({'result':result,'cases':cases},indent=2))
    return 0 if result=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
