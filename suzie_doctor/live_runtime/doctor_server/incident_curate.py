from __future__ import annotations
import fcntl, hashlib, json, os, re, subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BASE=Path('/var/lib/suzie-doctor-curation')
INBOX=BASE/'inbox'; RESULTS=BASE/'results'; PROCESSED=BASE/'processed'; LOCK=BASE/'curation.lock'
MASTER=Path('/var/lib/suzie-doctor-server/knowledge/forum_knowledge_base.json')
NORMALIZED=Path('/var/lib/suzie-doctor-server/knowledge/normalized_knowledge.json')
LEDGER=Path('/var/lib/suzie-doctor-server/knowledge/curation_ledger.json')
AUDIT=Path('/var/lib/suzie-doctor-server/knowledge/curation_audit.jsonl')
COMPILER=Path('/opt/suzie-doctor-server/knowledge_compile.py')
PYTHON=Path('/opt/suzie-doctor-server/venv/bin/python')

ACTIONS={'remove_duplicate','attach_existing_disease','keep_unclassified','create_new_disease','classify_treatment'}
TREATMENT={'DIAGNOSTIC_ONLY','CONFIRM_REQUIRED','CONFIRM_REQUIRED_HIGH_RISK','PRIMITIVE_MAPPING_REQUIRED'}
UNCERTAIN=('root cause unknown','root cause unclear','unproven','not proven','not confirmed','not isolated','not established','suspected','possibly','probably','likely','may be')

def now()->str:return datetime.now(UTC).isoformat()
def clean(v:Any,n:int=1200)->str:return str(v or '').replace('\x00',' ').strip()[:n]
def toks(v:Any)->set[str]:return set(re.findall(r'[a-z0-9_./:+-]{3,}',clean(v,8000).lower()))
def jac(a:set[str],b:set[str])->float:return 0.0 if not a or not b else len(a&b)/len(a|b)
def load(path:Path)->dict[str,Any]:return json.loads(path.read_text(encoding='utf-8'))
def save_atomic(path:Path,data:dict[str,Any],mode:int=0o640)->None:
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.chmod(tmp,mode); tmp.replace(path)
def blank_ledger()->dict[str,Any]:
    return {'schema_version':1,'attachments':{},'force_unclassified':{},'force_diseases':{},'treatment_classifications':{}}
def ledger_load()->dict[str,Any]:
    d=load(LEDGER) if LEDGER.exists() else blank_ledger()
    for k in ('attachments','force_unclassified','force_diseases','treatment_classifications'):d.setdefault(k,{})
    return d
def incident_map(master):return {str(x['id']):x for x in master.get('incidents') or []}
def disease_map(norm):return {str(x['disease_id']):x for x in norm.get('diseases') or []}
def incident_owner(norm)->dict[str,str]:
    out={}
    for d in norm.get('diseases') or []:
        for iid in d.get('incident_refs') or []:out[str(iid)]=str(d['disease_id'])
    return out
def resolve_disease_id(norm:dict[str,Any],value:str)->str:
    aliases={str(k):str(v) for k,v in (norm.get('disease_aliases') or {}).items()}
    current=str(value or '');seen=set()
    while current in aliases and current not in seen:
        seen.add(current);current=aliases[current]
    return current
def strong_duplicate(a,b)->dict[str,Any]:
    same_source=bool(a.get('source')) and str(a.get('source'))==str(b.get('source'))
    title=jac(toks(a.get('title')),toks(b.get('title')))
    root=jac(toks(a.get('root_cause')),toks(b.get('root_cause')))
    symptom=jac(toks(a.get('symptoms')),toks(b.get('symptoms')))
    ok=same_source and title>=.85 and max(root,symptom)>=.70
    return {'ok':ok,'same_source':same_source,'title_similarity':round(title,3),'root_similarity':round(root,3),'symptom_similarity':round(symptom,3)}
def referenced_elsewhere(master:dict[str,Any],iid:str)->list[str]:
    refs=[]
    for key,value in master.items():
        if key=='incidents':continue
        if iid in json.dumps(value,ensure_ascii=False):refs.append(str(key))
    return refs
def generated_disease_id(incident:dict[str,Any])->str:
    scope=re.sub(r'[^A-Z0-9]+','-',str(incident.get('scope') or 'CROSS').upper()).strip('-')[:24] or 'CROSS'
    h=hashlib.sha256((str(incident.get('id'))+'\n'+str(incident.get('root_cause'))).encode()).hexdigest()[:10].upper()
    return f'DISEASE-CURATED-{scope}-{h}'
def compiler_run()->tuple[bool,str]:
    cp=subprocess.run([str(PYTHON),str(COMPILER)],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=180,check=False)
    return cp.returncode==0,(cp.stdout+cp.stderr)[-1800:]
def write_result(rid:str,data:dict[str,Any])->None:
    path=RESULTS/f'{rid}.json'; tmp=path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.chmod(tmp,0o644);tmp.replace(path)
def audit(event:dict[str,Any])->None:
    with AUDIT.open('a',encoding='utf-8') as f:
        f.write(json.dumps(event,ensure_ascii=False,separators=(',',':'))+'\n')

def validate_request(req:dict[str,Any])->tuple[str,str,str,bool,str,str]:
    action=clean(req.get('action'),80);iid=clean(req.get('incident_id'),180);reason=clean(req.get('reason'),1000)
    dry=bool(req.get('dry_run',True));target=clean(req.get('target_disease_id'),180);other=clean(req.get('target_incident_id'),180)
    if action not in ACTIONS:raise ValueError('unsupported curation action')
    if not iid:raise ValueError('incident_id is required')
    if len(reason)<12:raise ValueError('reason must explain the evidence for the decision')
    return action,iid,reason,dry,target,other

def process(path:Path)->None:
    rid=path.stem
    try:
        req=load(path)
        if clean(req.get('request_id'),128)!=rid:raise ValueError('request_id mismatch')
        action,iid,reason,dry,target_disease,target_incident=validate_request(req)
        master=load(MASTER); norm=load(NORMALIZED); ledger=ledger_load()
        by=incident_map(master); diseases=disease_map(norm); owners=incident_owner(norm)
        if iid not in by:raise ValueError('incident_id not found')
        incident=by[iid]
        current_owner=owners.get(iid)
        result={'ok':True,'dry_run':dry,'action':action,'incident_id':iid,'current_disease_id':current_owner}

        if action=='remove_duplicate':
            if not iid.startswith('INC-INGEST-'):raise ValueError('only externally ingested incidents may be removed')
            if not target_incident or target_incident not in by:raise ValueError('target_incident_id not found')
            if target_incident==iid:raise ValueError('duplicate target cannot equal incident')
            proof=strong_duplicate(incident,by[target_incident])
            if not proof['ok']:raise ValueError('duplicate evidence is not strong enough for removal')
            refs=referenced_elsewhere(master,iid)
            if refs:raise ValueError('incident is referenced elsewhere: '+','.join(refs))
            result.update({'result':'WOULD_REMOVE_DUPLICATE' if dry else 'REMOVED_DUPLICATE','target_incident_id':target_incident,'duplicate_proof':proof})

        elif action=='attach_existing_disease':
            if not target_disease or target_disease not in diseases:raise ValueError('target_disease_id not found')
            target_refs=[str(x) for x in diseases[target_disease].get('incident_refs') or []]
            if not target_refs:raise ValueError('cannot attach to a Pack-only Disease without corpus evidence')
            if current_owner==target_disease:
                result.update({'result':'NO_CHANGE','target_disease_id':target_disease})
                write_result(rid,result);return
            result.update({'result':'WOULD_ATTACH' if dry else 'ATTACHED','target_disease_id':target_disease})

        elif action=='keep_unclassified':
            if iid in (norm.get('unclassified_incident_refs') or []):
                result.update({'result':'NO_CHANGE'})
                write_result(rid,result);return
            result.update({'result':'WOULD_KEEP_UNCLASSIFIED' if dry else 'KEPT_UNCLASSIFIED'})

        elif action=='create_new_disease':
            if current_owner:
                raise ValueError('incident already belongs to a Disease')
            status=str(incident.get('status') or '').upper()
            root=clean(incident.get('root_cause'),3000)
            if status!='CONFIRMED':raise ValueError('new explicit Disease requires CONFIRMED incident')
            if not root:raise ValueError('new explicit Disease requires root_cause')
            low=root.lower()
            if any(term in low for term in UNCERTAIN):raise ValueError('root_cause remains uncertain')
            new_id=generated_disease_id(incident)
            result.update({'result':'WOULD_CREATE_DISEASE' if dry else 'CREATED_DISEASE','disease_id':new_id})

        elif action=='classify_treatment':
            cls=clean(req.get('treatment_classification'),80).upper()
            if cls not in TREATMENT:raise ValueError('invalid treatment_classification')
            result.update({'result':'WOULD_CLASSIFY_TREATMENT' if dry else 'TREATMENT_CLASSIFIED','treatment_classification':cls})

        if dry:
            write_result(rid,result);return

        master_before=MASTER.read_bytes();ledger_before=LEDGER.read_bytes() if LEDGER.exists() else None
        try:
            if action=='remove_duplicate':
                master['incidents']=[x for x in master.get('incidents') or [] if str(x.get('id'))!=iid]
                for key in ('attachments','force_unclassified','force_diseases','treatment_classifications'):
                    ledger.get(key,{}).pop(iid,None)
                save_atomic(MASTER,master)
                save_atomic(LEDGER,ledger)
            elif action=='attach_existing_disease':
                ledger['attachments'][iid]={'disease_id':target_disease,'reason':reason,'decided_at':now(),'request_id':rid}
                ledger['force_unclassified'].pop(iid,None);ledger['force_diseases'].pop(iid,None)
                save_atomic(LEDGER,ledger)
            elif action=='keep_unclassified':
                ledger['force_unclassified'][iid]={'reason':reason,'decided_at':now(),'request_id':rid}
                ledger['attachments'].pop(iid,None);ledger['force_diseases'].pop(iid,None)
                save_atomic(LEDGER,ledger)
            elif action=='create_new_disease':
                new_id=result['disease_id']
                ledger['force_diseases'][iid]={'disease_id':new_id,'reason':reason,'decided_at':now(),'request_id':rid}
                ledger['attachments'].pop(iid,None);ledger['force_unclassified'].pop(iid,None)
                save_atomic(LEDGER,ledger)
            elif action=='classify_treatment':
                ledger['treatment_classifications'][iid]={'classification':result['treatment_classification'],'reason':reason,'decided_at':now(),'request_id':rid}
                save_atomic(LEDGER,ledger)

            ok,detail=compiler_run()
            if not ok:raise RuntimeError('compile/normalize failed: '+detail)
            after=load(NORMALIZED);after_owner=incident_owner(after)
            if action=='remove_duplicate' and iid in incident_map(load(MASTER)):raise RuntimeError('duplicate removal verification failed')
            if action=='attach_existing_disease' and after_owner.get(iid)!=resolve_disease_id(after,target_disease):raise RuntimeError('attach verification failed')
            if action=='keep_unclassified' and iid not in (after.get('unclassified_incident_refs') or []):raise RuntimeError('unclassified verification failed')
            if action=='create_new_disease' and after_owner.get(iid)!=result['disease_id']:raise RuntimeError('new Disease verification failed')
            if int((after.get('stats') or {}).get('executable_protocols') or 0)!=4:
                raise RuntimeError('curation may not change executable Protocol count')
        except Exception:
            MASTER.write_bytes(master_before);os.chmod(MASTER,0o640)
            if ledger_before is None:LEDGER.unlink(missing_ok=True)
            else:LEDGER.write_bytes(ledger_before);os.chmod(LEDGER,0o640)
            compiler_run()
            raise

        after=load(NORMALIZED)
        result['after_disease_id']=incident_owner(after).get(iid)
        result['counts']={
            'incidents':(after.get('stats') or {}).get('incidents'),
            'diseases':(after.get('stats') or {}).get('diseases'),
            'unclassified_incidents':(after.get('stats') or {}).get('unclassified_incidents'),
            'executable_protocols':(after.get('stats') or {}).get('executable_protocols'),
        }
        audit({'at':now(),'request_id':rid,'action':action,'incident_id':iid,'reason':reason,'result':result['result'],'target_disease_id':target_disease or None,'target_incident_id':target_incident or None,'treatment_classification':result.get('treatment_classification')})
        write_result(rid,result)
    except Exception as exc:
        write_result(rid,{'ok':False,'result':'REJECTED','error':f'{type(exc).__name__}: {clean(exc,1200)}'})

def main()->int:
    BASE.mkdir(parents=True,exist_ok=True)
    with LOCK.open('a+') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        for path in sorted(INBOX.glob('*.json')):
            rid=path.stem
            try:process(path)
            finally:
                path.replace(PROCESSED/f'{rid}.json')
                old=sorted(PROCESSED.glob('*.json'),key=lambda p:p.stat().st_mtime)
                for stale in old[:-300]:stale.unlink(missing_ok=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
