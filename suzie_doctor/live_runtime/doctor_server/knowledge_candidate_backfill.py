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



def _existing_v2_candidates(rt:DoctorV2Runtime)->list[dict[str,Any]]:
    out=[]
    for row in rt.conn.execute(
        'select protocol_id,origin,state,disease_id,candidate_json from doctor_v2_protocol_candidates'
    ):
        try:
            candidate=json.loads(row['candidate_json'] or '{}')
        except Exception:
            candidate={}
        out.append({
            'protocol_id':str(row['protocol_id']),
            'origin':str(row['origin'] or ''),
            'state':str(row['state'] or ''),
            'disease_id':str(row['disease_id'] or ''),
            'candidate':candidate,
        })
    return out


def _historical_field_review(rt:DoctorV2Runtime, *, apply:bool)->dict[str,Any]:
    reviews=[]; inserted=[]
    row=rt.conn.execute(
        'select case_id,outcome,result_json from doctor_cases where case_id=84'
    ).fetchone()
    if row:
        try:
            result=json.loads(row['result_json'] or '{}')
        except Exception:
            result={}
        one=result.get('field_one_shot_evidence') if isinstance(result.get('field_one_shot_evidence'),dict) else {}
        verify=result.get('functional_verify') if isinstance(result.get('functional_verify'),dict) else {}
        action=one.get('action') if isinstance(one.get('action'),dict) else {}
        reconciled=(
            str(row['outcome'] or '').upper()=='FAILED'
            and str(one.get('execution_state') or '').upper()=='VERIFIED_FAIL'
            and str(action.get('name') or '')=='core.restart'
            and str(verify.get('result') or '').upper()=='PASS'
            and isinstance(verify.get('evidence'),list)
            and len(verify.get('evidence') or [])>=2
        )
        if reconciled:
            pid='EXP-FIELD-HACS-RESTART-REQUIRED-001'
            did='DISEASE-FIELD-HACS-RESTART-REQUIRED-001'
            disease={
                'disease_id':did,
                'title':'HACS custom integration update leaves restart_required Repair until Home Assistant Core restart',
                'component':'hacs',
                'symptoms':['active HACS restart_required Repair remains after a custom integration update'],
                'diagnostic_criteria':{
                    'must':[
                        'the exact HACS restart_required Repair is active for the updated custom integration',
                        'Home Assistant Core is running before treatment',
                        'the affected custom integration is installed and its config entry can be checked',
                    ],
                    'exclusions':[
                        'do not use this protocol for unrelated Core failures or unrelated Repairs',
                    ],
                },
            }
            candidate={
                'protocol_id':pid,
                'origin':'INTERNAL_FIELD',
                'disease_id':did,
                'title':'Restart Home Assistant Core to activate a HACS custom integration update with restart_required Repair',
                'component':'hacs',
                'symptoms':list(disease['symptoms']),
                'fingerprints':['HACS','restart_required','custom integration update','Core restart'],
                'checks':list(disease['diagnostic_criteria']['must']),
                'action':'restart Home Assistant Core after confirming the exact HACS restart_required Repair for the updated custom integration',
                'verify':[
                    'the exact HACS restart_required Repair is absent after Core returns',
                    'Home Assistant Core is running',
                    'the affected custom integration config entry is loaded',
                ],
                'rollback':'No repeat restart after functional PASS; if Core fails to return, follow the normal Core recovery path.',
                'risk':'MEDIUM',
                'automation_class':'CONFIRM_REQUIRED',
                'disease':disease,
                'internal_evidence':[
                    {
                        'case_id':84,
                        'evidence_class':'RECONCILED_FUNCTIONAL_PASS_NO_VALIDATION_CREDIT',
                        'command_execution_state':'VERIFIED_FAIL',
                        'functional_verify':'PASS',
                        'note':'Transport timeout prevented signed VERIFIED_PASS, but the original HACS Repair was absent on two later exact-client checks. This creates a 0/3 candidate only.',
                    }
                ],
            }
            approval=f'{did}|{pid}'
            compiled=build_card(disease,candidate,approved_keys={approval})
            factory=compiled.get('factory') if isinstance(compiled.get('factory'),dict) else {}
            machine=bool(factory.get('mapped')) and bool(factory.get('complete_mapping')) and bool(compiled.get('treatment'))
            existing=rt.store.protocol_candidate(pid)
            if existing:
                reviews.append({
                    'case_id':84,'disposition':'CANDIDATE_EXISTS','protocol_id':pid,
                    'validation_stage':existing.get('validation_stage') or '0/3',
                    'validation_credit_added':0,
                })
            elif machine:
                reviews.append({
                    'case_id':84,'disposition':'EXPERIMENTAL_0_3','protocol_id':pid,
                    'mapping_kind':factory.get('mapping_kind'),
                    'validation_credit_added':0,
                    'reason':'independent functional recovery was observed after treatment, but signed command verification failed at the transport boundary',
                })
                if apply:
                    snap=rt.store.upsert_protocol_candidate(
                        protocol_id=pid,origin='INTERNAL_FIELD',disease_id=did,candidate=candidate
                    )
                    inserted.append({
                        'protocol_id':pid,'state':snap.get('state'),
                        'validation_stage':snap.get('validation_stage'),'disease_id':did,
                    })
            else:
                reviews.append({
                    'case_id':84,'disposition':'REJECTED',
                    'reason_class':'NO_COMPLETE_MACHINE_TREATMENT',
                    'reason':str(factory.get('blocker_class') or factory.get('mapping_reason') or 'no complete machine mapping'),
                })

    row=rt.conn.execute(
        'select case_id,outcome,result_json from doctor_cases where case_id=96'
    ).fetchone()
    if row:
        try:
            result=json.loads(row['result_json'] or '{}')
        except Exception:
            result={}
        one=result.get('field_one_shot_evidence') if isinstance(result.get('field_one_shot_evidence'),dict) else {}
        action=one.get('action') if isinstance(one.get('action'),dict) else {}
        if (
            str(action.get('name') or '')=='mount.reload'
            and str(one.get('execution_state') or '').upper()=='VERIFIED_FAIL'
            and one.get('functional_verify_pass') is False
        ):
            reviews.append({
                'case_id':96,
                'disposition':'NEGATIVE_EVIDENCE',
                'action':'mount.reload',
                'validation_credit_added':0,
                'reason':'target mount remained inactive and the semantic Repair remained active; endpoint was unreachable',
                'do_not_generalize':'do not repeat mount.reload for the same episode without new evidence that endpoint reachability changed',
            })
    return {'reviews':reviews,'inserted':inserted}


def _urls_from_wilson_item(item:dict[str,Any])->list[str]:
    out=[]; seen=set()
    for key in ('source','evidence','sources','source_urls'):
        value=item.get(key)
        values=[value] if isinstance(value,str) else value if isinstance(value,list) else []
        for raw in values:
            url=str(raw).strip()
            if url.startswith(('http://','https://')) and url not in seen:
                seen.add(url); out.append(url)
    return out


def _candidate_external_urls(candidate:dict[str,Any])->set[str]:
    urls=set()
    for item in candidate.get('external_evidence') or []:
        if isinstance(item,dict):
            url=str(item.get('url') or '').strip()
            if url.startswith(('http://','https://')):
                urls.add(url)
    return urls


def _wilson_history_review(rt:DoctorV2Runtime)->dict[str,Any]:
    v2=_existing_v2_candidates(rt)
    by_url:dict[str,list[str]]={}
    for row in v2:
        for url in _candidate_external_urls(row.get('candidate') or {}):
            by_url.setdefault(url,[]).append(row['protocol_id'])
    reviews=[]
    for row in rt.conn.execute(
        "select wilson_job_id,result_json from doctor_v2_wilson_jobs where mode='NIGHTLY_RESEARCH' and status='DONE' order by wilson_job_id"
    ):
        try:
            result=json.loads(row['result_json'] or '{}')
        except Exception:
            result={}
        for bucket in ('candidates','findings','incident_reviews'):
            items=result.get(bucket)
            if not isinstance(items,list):
                continue
            for index,item in enumerate(items,1):
                if not isinstance(item,dict):
                    continue
                urls=_urls_from_wilson_item(item)
                matched=sorted({pid for url in urls for pid in by_url.get(url,[])})
                key=str(item.get('finding_id') or item.get('incident_key') or item.get('title') or item.get('topic') or f'job-{row["wilson_job_id"]}-{bucket}-{index}')
                if bucket=='incident_reviews' and str(item.get('disposition') or '').upper() in {'CANDIDATE_CREATED','CANDIDATE_UPDATED','REJECTED'}:
                    reviews.append({
                        'wilson_job_id':int(row['wilson_job_id']),'item_key':key,
                        'disposition':'ALREADY_STRUCTURED',
                        'original_disposition':str(item.get('disposition') or '').upper(),
                        'protocol_id':item.get('protocol_id'),
                        'source_urls':urls,
                    })
                    continue
                if matched:
                    reviews.append({
                        'wilson_job_id':int(row['wilson_job_id']),'item_key':key,
                        'disposition':'CANDIDATE_EXISTS',
                        'protocol_ids':matched,'source_urls':urls,
                        'validation_credit_added':0,
                    })
                    continue
                experimental=item.get('experimental_protocol_candidate')
                treatment_text=' '.join([
                    str(item.get('proposed_treatment') or ''),
                    str(item.get('treatment_governance') or ''),
                    str(item.get('proposed_protocol') or ''),
                    json.dumps(experimental,ensure_ascii=False) if isinstance(experimental,dict) else '',
                    ' '.join(str(x) for x in (item.get('positive_evidence') or []) if isinstance(x,str)),
                ]).lower()
                has_treatment=bool(
                    isinstance(experimental,dict)
                    or str(item.get('proposed_treatment') or '').strip()
                    or ('experimental protocol' in str(item.get('kind') or '').lower())
                    or ('experimental_workaround' in str(item.get('classification') or '').lower())
                    or ('potential experimental protocol' in str(item.get('treatment_governance') or '').lower())
                )
                rollback_signal=any(token in treatment_text for token in ('revert','rollback','known-good','known good','previous version','downgrade'))
                recovery_signal=any(token in treatment_text for token in ('restored','restore','fixed','hotfix','worked','resolved'))
                if rollback_signal and (has_treatment or recovery_signal):
                    reviews.append({
                        'wilson_job_id':int(row['wilson_job_id']),'item_key':key,
                        'disposition':'REJECTED','rejection_class':'UNSUPPORTED_CAPABILITY',
                        'rejection_reason':'evidence points to version rollback/downgrade, but current signed Doctor primitives do not provide a bounded rollback/downgrade treatment path',
                        'source_urls':urls,'validation_credit_added':0,
                    })
                elif has_treatment:
                    reviews.append({
                        'wilson_job_id':int(row['wilson_job_id']),'item_key':key,
                        'disposition':'REJECTED','rejection_class':'INSUFFICIENT_EVIDENCE',
                        'rejection_reason':'a treatment hypothesis exists, but no current v2 executable candidate with sufficient bounded evidence was established',
                        'source_urls':urls,'validation_credit_added':0,
                    })
                else:
                    reviews.append({
                        'wilson_job_id':int(row['wilson_job_id']),'item_key':key,
                        'disposition':'REJECTED','rejection_class':'NO_SUCCESSFUL_TREATMENT',
                        'rejection_reason':'Wilson finding is diagnostic/external evidence without a reusable successful treatment proven strongly enough to create an experiment',
                        'source_urls':urls,'validation_credit_added':0,
                    })
    counts=Counter(x['disposition'] for x in reviews)
    rejection=Counter(x.get('rejection_class') for x in reviews if x['disposition']=='REJECTED')
    return {'items_reviewed':len(reviews),'counts':dict(counts),'rejection_classes':dict(rejection),'reviews':reviews}



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
    field_history=_historical_field_review(rt,apply=apply)
    if field_history.get('inserted'):
        inserted.extend(field_history['inserted'])
    wilson_history=_wilson_history_review(rt)
    counts=Counter(x['disposition'] for x in dispositions)
    reject=Counter(x.get('reason_class') for x in dispositions if x['disposition']=='REJECTED')
    return {
        'schema_version':2,
        'source_candidates':len(dispositions),
        'apply':bool(apply),
        'counts':dict(counts),
        'rejection_classes':dict(reject),
        'inserted':inserted,
        'dispositions':dispositions,
        'historical_field':field_history,
        'wilson_history':wilson_history,
    }


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
