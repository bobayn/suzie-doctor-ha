from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from doctor_v2_live import DoctorV2Runtime
from protocol_factory import build_card

DEFAULT_DB=Path('/var/lib/suzie-doctor-server/server.sqlite3')
DEFAULT_SCHEMA=Path('/opt/suzie-doctor-server/doctor_v2_schema.sql')
DEFAULT_NORMALIZED=Path('/var/lib/suzie-doctor-server/knowledge/normalized_knowledge.json')
DEFAULT_GENERATED=Path('/var/lib/suzie-doctor-server/knowledge/generated_protocols.json')
DEFAULT_REPORT=Path('/var/lib/suzie-doctor-server/knowledge/v2_candidate_audit_2026-09-25.json')


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value,list): return []
    return [str(x).strip() for x in value if str(x).strip()]


def _urls_from_disease(disease:dict[str,Any])->list[dict[str,Any]]:
    out=[]; seen=set()
    ev=disease.get('evidence') if isinstance(disease.get('evidence'),dict) else {}
    for item in ev.get('supporting') or []:
        if not isinstance(item,dict): continue
        url=str(item.get('source') or '').strip()
        if not url.startswith(('http://','https://')) or url in seen: continue
        seen.add(url)
        out.append({'url':url,'kind':'legacy_knowledge_source','evidence':str(item.get('evidence') or '')[:1200]})
    return out


def _draft_disease(disease:dict[str,Any], candidate:dict[str,Any])->dict[str,Any]:
    ev=disease.get('evidence') if isinstance(disease.get('evidence'),dict) else {}
    must=_clean_list(ev.get('required'))
    if not must: must=_clean_list(candidate.get('checks'))
    if not must: must=_clean_list(disease.get('fingerprints'))[:6]
    if not must: must=[f"independently confirm {disease.get('title') or disease.get('disease_id')} functional failure"]
    symptoms=_clean_list(disease.get('symptoms'))
    if not symptoms: symptoms=_clean_list(disease.get('fingerprints'))[:8]
    if not symptoms: symptoms=[str(disease.get('title') or candidate.get('title') or 'functional failure')]
    return {
      'disease_id':str(disease.get('disease_id') or ''),
      'title':str(disease.get('title') or candidate.get('title') or disease.get('disease_id') or ''),
      'component':str(disease.get('component') or candidate.get('component') or 'unknown'),
      'symptoms':symptoms,
      'diagnostic_criteria':{
        'must':must,
        'exclusions':_clean_list(ev.get('exclusions')),
      },
    }


def derived_candidate(disease:dict[str,Any], candidate:dict[str,Any], card:dict[str,Any])->dict[str,Any]:
    factory=card.get('factory') or {}
    source_id=str(candidate.get('protocol_id') or factory.get('source_candidate_id') or '')
    material=f"{disease.get('disease_id')}|{source_id}".encode()
    pid='EXP-KB-'+hashlib.sha256(material).hexdigest()[:12].upper()
    risk=str(candidate.get('risk') or 'MEDIUM').upper()
    out={
      'protocol_id':pid,
      'origin':'LEGACY',
      'disease_id':str(disease.get('disease_id') or ''),
      'title':str(candidate.get('title') or disease.get('title') or source_id),
      'component':str(disease.get('component') or candidate.get('component') or 'unknown'),
      'symptoms':_clean_list(disease.get('symptoms')) or _clean_list(disease.get('fingerprints'))[:8] or [str(disease.get('title') or '')],
      'fingerprints':_clean_list(disease.get('fingerprints')),
      'checks':_clean_list(candidate.get('checks')),
      'action':str(candidate.get('action') or '').strip(),
      'verify':_clean_list(candidate.get('verify')),
      'rollback':str(candidate.get('rollback') or '').strip(),
      'risk':risk if risk in {'LOW','MEDIUM','HIGH'} else 'MEDIUM',
      'automation_class':'CONFIRM_REQUIRED',
      'assessment':{'state':'CONFIRM_REQUIRED_HIGH_RISK' if risk=='HIGH' else 'CONFIRM_REQUIRED'},
      'factory_source_candidate_id':source_id,
      'disease':_draft_disease(disease,candidate),
      'external_evidence':_urls_from_disease(disease),
      'legacy_provenance':{
        'source_candidate_id':source_id,
        'generated_protocol_id':str((card.get('protocol') or {}).get('id') or ''),
        'legacy_protocol_status':str((card.get('protocol') or {}).get('status') or ''),
        'legacy_factory_state':str(factory.get('state') or ''),
        'mapping_kind':str(factory.get('mapping_kind') or ''),
        'source_assessment':str(factory.get('source_assessment') or ''),
        'source_automation_class':str(factory.get('source_automation_class') or ''),
      },
    }
    return out


def compile_derived(disease:dict[str,Any], candidate:dict[str,Any])->dict[str,Any]:
    approval=f"{candidate['disease_id']}|{candidate['protocol_id']}"
    return build_card(disease,candidate,approved_keys={approval})


def audit(rt:DoctorV2Runtime, normalized:dict[str,Any], generated:dict[str,Any], apply:bool=False)->dict[str,Any]:
    source={}
    for disease in normalized.get('diseases') or []:
        if not isinstance(disease,dict): continue
        for candidate in disease.get('protocol_candidates') or []:
            if isinstance(candidate,dict): source[(str(disease.get('disease_id') or ''),str(candidate.get('protocol_id') or ''))]=(disease,candidate)
    existing=[]
    for row in rt.conn.execute('select protocol_id,disease_id,candidate_json,state from doctor_v2_protocol_candidates'):
        try: c=json.loads(row['candidate_json'] or '{}')
        except Exception: c={}
        existing.append({'protocol_id':str(row['protocol_id']),'disease_id':str(row['disease_id'] or ''),'candidate':c,'state':str(row['state'] or '')})
    dispositions=[]; inserted=[]
    for card in generated.get('protocols') or []:
        if not isinstance(card,dict): continue
        factory=card.get('factory') or {}; proto=card.get('protocol') or {}
        did=str(card.get('disease_id') or ''); cid=str(factory.get('source_candidate_id') or '')
        pair=source.get((did,cid));
        if not pair:
            dispositions.append({'disease_id':did,'source_candidate_id':cid,'disposition':'REJECTED','reason_class':'SOURCE_NOT_FOUND','reason':'generated card has no normalized source candidate'})
            continue
        disease,candidate=pair
        status=str(proto.get('status') or '')
        if status=='ACTIVE':
            dispositions.append({'disease_id':did,'source_candidate_id':cid,'disposition':'ALREADY_ACTIVE','generated_protocol_id':proto.get('id'),'mapping_kind':factory.get('mapping_kind')})
            continue
        executable=bool(factory.get('mapped')) and bool(factory.get('complete_mapping')) and bool(card.get('treatment'))
        if not executable:
            blocker=str(factory.get('blocker_class') or factory.get('deferred_treatment_blocker_class') or 'NO_COMPLETE_MACHINE_TREATMENT')
            dispositions.append({'disease_id':did,'source_candidate_id':cid,'disposition':'REJECTED','reason_class':blocker,'reason':str(factory.get('blocker_detail') or factory.get('deferred_treatment_blocker_detail') or factory.get('mapping_reason') or 'no complete machine treatment')})
            continue
        derived=derived_candidate(disease,candidate,card)
        compiled=compile_derived(disease,derived)
        cf=compiled.get('factory') or {}
        if not (cf.get('mapped') and cf.get('complete_mapping') and compiled.get('treatment')):
            dispositions.append({'disease_id':did,'source_candidate_id':cid,'disposition':'REJECTED','reason_class':'DERIVED_COMPILE_FAILED','reason':str(cf.get('blocker_class') or cf.get('mapping_reason') or 'derived candidate has no treatment')})
            continue
        duplicate=None
        for ex in existing:
            if ex['disease_id']!=did: continue
            ex_action=str((ex['candidate'] or {}).get('action') or '').lower()
            ex_map=str(((ex['candidate'] or {}).get('legacy_provenance') or {}).get('mapping_kind') or '')
            if not ex_map and isinstance(ex.get('candidate'),dict):
                try:
                    ex_compiled=compile_derived(disease,dict(ex['candidate']))
                    ex_factory=ex_compiled.get('factory') if isinstance(ex_compiled.get('factory'),dict) else {}
                    if ex_factory.get('mapped') and ex_factory.get('complete_mapping') and ex_compiled.get('treatment'):
                        ex_map=str(ex_factory.get('mapping_kind') or '')
                except Exception:
                    ex_map=''
            if ex_map and ex_map==str(cf.get('mapping_kind') or ''):
                duplicate=ex; break
            if ex_action and ex_action==str(derived.get('action') or '').lower():
                duplicate=ex; break
        if duplicate:
            dispositions.append({'disease_id':did,'source_candidate_id':cid,'disposition':'DUPLICATE_EXISTING_V2','existing_protocol_id':duplicate['protocol_id'],'mapping_kind':cf.get('mapping_kind')})
            continue
        dispositions.append({'disease_id':did,'source_candidate_id':cid,'disposition':'EXPERIMENTAL_0_3','protocol_id':derived['protocol_id'],'mapping_kind':cf.get('mapping_kind'),'risk':derived['risk'],'title':derived['title']})
        if apply:
            snap=rt.store.upsert_protocol_candidate(protocol_id=derived['protocol_id'],origin='LEGACY',disease_id=did,candidate=derived)
            inserted.append({'protocol_id':derived['protocol_id'],'state':snap.get('state'),'validation_stage':snap.get('validation_stage'),'disease_id':did})
            existing.append({'protocol_id':derived['protocol_id'],'disease_id':did,'candidate':derived,'state':str(snap.get('state') or '')})
    counts=Counter(x['disposition'] for x in dispositions)
    reject=Counter(x.get('reason_class') for x in dispositions if x['disposition']=='REJECTED')
    return {'schema_version':1,'source_candidates':len(dispositions),'apply':bool(apply),'counts':dict(counts),'rejection_classes':dict(reject),'inserted':inserted,'dispositions':dispositions}


def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--apply',action='store_true'); ap.add_argument('--db',type=Path,default=DEFAULT_DB); ap.add_argument('--schema',type=Path,default=DEFAULT_SCHEMA); ap.add_argument('--normalized',type=Path,default=DEFAULT_NORMALIZED); ap.add_argument('--generated',type=Path,default=DEFAULT_GENERATED); ap.add_argument('--report',type=Path,default=DEFAULT_REPORT); args=ap.parse_args()
    rt=DoctorV2Runtime(args.db,args.schema)
    try:
        result=audit(rt,json.loads(args.normalized.read_text()),json.loads(args.generated.read_text()),apply=args.apply)
        args.report.parent.mkdir(parents=True,exist_ok=True); args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:result[k] for k in ('source_candidates','apply','counts','rejection_classes')},ensure_ascii=False,indent=2))
        if args.apply: print(json.dumps({'inserted':len(result['inserted'])},ensure_ascii=False))
    finally: rt.store.close()
    return 0

if __name__=='__main__': raise SystemExit(main())
