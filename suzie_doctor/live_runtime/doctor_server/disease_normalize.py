from __future__ import annotations
import json,re
from collections import defaultdict
from pathlib import Path
from typing import Any

MASTER=Path('/var/lib/suzie-doctor-server/knowledge/forum_knowledge_base.json')
COMPILED=Path('/var/lib/suzie-doctor-server/knowledge/compiled_knowledge.json')
OUTPUT=Path('/var/lib/suzie-doctor-server/knowledge/normalized_knowledge.json')
CURATION=Path('/var/lib/suzie-doctor-server/knowledge/curation_ledger.json')
STATUS={'UNRESOLVED':0,'PARTIAL':1,'PROBABLE':2,'CONFIRMED':3,'PACK_ONLY':4}
STOP={'a','an','and','are','as','at','be','because','been','but','by','can','cause','caused','could','did','does','for','from','had','has','have','if','in','into','is','it','its','not','of','on','or','that','the','their','this','to','was','were','when','while','with','without'}
ALIASES={'serial':'container','docker':'container','fs':'storage','inode':'storage','sup':'supervisor'}
VERIFIED=[
['INC-LINUX-SERIAL-001','INC-LINUX-DOCKER-006'],
['INC-LINUX-DOCKER-001','INC-LINUX-DOCKER-007','INC-LINUX-DOCKER-008'],
['INC-FUTURE-FRIGATE-001','INC-FUTURE-FRIGATE-008'],
['INC-FUTURE-FRIGATE-002','INC-FUTURE-FRIGATE-009'],
['INC-FUTURE-FRIGATE-003','INC-FUTURE-FRIGATE-010'],
['INC-FUTURE-FRIGATE-004','INC-FUTURE-FRIGATE-011'],
['INC-FUTURE-FRIGATE-005','INC-FUTURE-FRIGATE-012'],
['INC-CROSS-BACKUP-RESTORE-001','INC-HA-BACKUP-011'],
['INC-CROSS-NODERED-001','INC-CROSS-NODERED-002'],
['INC-CROSS-UNRAID-001','INC-CROSS-UNRAID-005'],
['INC-FUTURE-ZWAVE-006','INC-FUTURE-ZWAVE-012'],
['INC-CROSS-DNS-001','INC-CROSS-DNS-002'],
['INC-HA-HACS-001','INC-HA-HACS-004'],
['INC-LINUX-FS-001','INC-LINUX-INODE-001','INC-LINUX-INODE-002'],
['INC-LINUX-SUPERVISOR-001','INC-LINUX-SUPERVISOR-002','INC-LINUX-SUPERVISOR-003'],
['INC-HA-RECORDER-003','INC-HA-RECORDER-005','INC-HA-RECORDER-006','INC-HA-RECORDER-008'],
['INC-CROSS-NAMING-001','INC-HA-NAMING-001'],
['INC-HA-ESPHOME-008','INC-HA-ESPHOME-002'],
['INC-FUTURE-ZIGBEE-015','INC-FUTURE-ZIGBEE-009'],
['INC-FUTURE-ZWAVE-010','INC-FUTURE-ZWAVE-013'],
['INC-HA-SUP-009','INC-HA-SUP-010'],
['INC-CROSS-MATTER-001','INC-CROSS-MATTER-015'],
]

def norm(v:Any)->str:
    s=re.sub(r'https?://\S+',' ',str(v or '').lower())
    return ' '.join(re.sub(r'[^a-z0-9_./:+-]+',' ',s).split())

def toks(v:Any)->set[str]:
    return {x for x in re.findall(r'[a-z0-9_./:+-]{3,}',norm(v)) if x not in STOP}

def jac(a:set[str],b:set[str])->float:
    return 0.0 if not a or not b else len(a&b)/len(a|b)

def fps(x:dict[str,Any])->set[str]:
    out=set()
    for v in x.get('fingerprint') or []: out|=toks(v)
    return out

def raw_component(x:dict[str,Any])->str:
    parts=[p for p in str(x.get('id') or '').split('-') if p]
    return parts[2].lower() if len(parts)>=4 and parts[1] in {'HA','CROSS','LINUX','FUTURE','VENDOR'} else str(x.get('scope') or 'unknown').lower()

def component(x:dict[str,Any])->str:
    raw=raw_component(x)
    return ALIASES.get(raw,raw)

def family_id(c:str)->str:
    return 'FAMILY-'+(re.sub(r'[^A-Z0-9]+','-',c.upper()).strip('-') or 'UNKNOWN')

class UF:
    def __init__(self,ids:list[str]): self.p={x:x for x in ids}
    def f(self,x:str)->str:
        if self.p[x]!=x:self.p[x]=self.f(self.p[x])
        return self.p[x]
    def u(self,a:str,b:str)->None:
        a,b=self.f(a),self.f(b)
        if a!=b:self.p[b]=a

def similarity(a,b):
    return {'root':jac(toks(a.get('root_cause')),toks(b.get('root_cause'))),'fingerprint':jac(fps(a),fps(b)),'fix':jac(toks(a.get('fix')),toks(b.get('fix'))),'title':jac(toks(a.get('title')),toks(b.get('title')))}

def auto_reason(a,b):
    s=similarity(a,b); same_source=bool(a.get('source')) and a.get('source')==b.get('source'); same_comp=component(a)==component(b)
    if same_source and max(s['fingerprint'],s['fix'],s['title'])>=.45 and max(s['root'],s['fingerprint'])>=.10:return 'same_source_strong_signature'
    if same_source and s['root']>=.12 and s['fingerprint']>=.30:return 'same_source_root_fingerprint'
    if same_comp and s['root']>=.35 and s['fingerprint']>=.40:return 'same_component_root_fingerprint'
    if same_comp and s['root']>=.40 and s['title']>=.20:return 'same_component_very_close_root'
    return None

def gate(c,d):
    auto=str(c.get('automation_class') or 'DIAGNOSTIC_ONLY'); risk=str(c.get('risk') or 'UNKNOWN').upper(); reasons=[]
    if d.get('diagnosis_status')!='CONFIRMED': reasons.append('disease_not_confirmed')
    if auto=='DIAGNOSTIC_ONLY': state='DIAGNOSTIC_ONLY'; reasons.append('diagnostic_only_source')
    elif risk=='HIGH': state='CONFIRM_REQUIRED_HIGH_RISK'; reasons.append('high_risk')
    elif auto=='AUTO_SAFE' and risk=='LOW': state='PRIMITIVE_MAPPING_REQUIRED'; reasons.append('structured_primitive_mapping_missing')
    else: state='CONFIRM_REQUIRED'; reasons.append('explicit_confirmation_required')
    if not c.get('verify'):reasons.append('verify_missing')
    if not str(c.get('rollback') or '').strip():reasons.append('rollback_missing')
    if not c.get('checks'):reasons.append('diagnostic_checks_missing')
    return {'state':state,'promotable':False,'reasons':sorted(set(reasons))}

def main():
    master=json.loads(MASTER.read_text(encoding='utf-8'))
    compiled=json.loads(COMPILED.read_text(encoding='utf-8'))
    curation=json.loads(CURATION.read_text(encoding='utf-8')) if CURATION.exists() else {}
    prior=json.loads(OUTPUT.read_text(encoding='utf-8')) if OUTPUT.exists() else {}
    forced_unclassified=set(str(x) for x in (curation.get('force_unclassified') or {}))
    forced_diseases={str(k):v for k,v in (curation.get('force_diseases') or {}).items()}
    attachments={str(k):v for k,v in (curation.get('attachments') or {}).items()}
    treatment_classifications={str(k):v for k,v in (curation.get('treatment_classifications') or {}).items()}
    inc=list(master.get('incidents') or []); by={str(x['id']):x for x in inc}; ids=sorted(by)
    uf=UF(ids); audit=[]
    protected=forced_unclassified | set(forced_diseases)
    for group in VERIFIED:
        present=[x for x in group if x in by and x not in protected]
        for a,b in zip(present,present[1:]):
            uf.u(a,b); audit.append({'a':a,'b':b,'reason':'verified_v2_7_duplicate_or_same_root_cause','automatic':False})
    for i,a_id in enumerate(ids):
        a=by[a_id]
        for b_id in ids[i+1:]:
            b=by[b_id]
            if a_id in protected or b_id in protected:
                continue
            reason=auto_reason(a,b)
            if reason and uf.f(a_id)!=uf.f(b_id):
                uf.u(a_id,b_id); audit.append({'a':a_id,'b':b_id,'reason':reason,'automatic':True,'similarity':similarity(a,b)})
    # Explicit curation attachments are durable and override heuristic grouping.
    prior_by_disease={
        str(d.get('disease_id')):d
        for d in (prior.get('diseases') or [])
        if isinstance(d,dict) and d.get('disease_id')
    }
    prior_aliases={
        str(k):str(v)
        for k,v in (prior.get('disease_aliases') or {}).items()
    }
    def resolve_prior_disease_id(value):
        current=str(value or '')
        seen=set()
        while current in prior_aliases and current not in seen:
            seen.add(current);current=prior_aliases[current]
        return current
    for iid,decision in attachments.items():
        if iid not in by or iid in protected or not isinstance(decision,dict):
            continue
        target=str(decision.get('disease_id') or '')
        resolved_target=resolve_prior_disease_id(target)
        target_d=prior_by_disease.get(resolved_target)
        refs=[str(x) for x in (target_d.get('incident_refs') or [])] if target_d else []
        refs=[x for x in refs if x in by and x not in protected and x!=iid]
        if refs:
            uf.u(iid,refs[0])
            audit.append({
                'a':iid,'b':refs[0],
                'reason':'curation_attach_existing_disease',
                'automatic':False,'target_disease_id':target
            })

    groups=defaultdict(list)
    for iid in ids: groups[uf.f(iid)].append(iid)

    source_by_inc={}; pack_only=[]
    for d in compiled.get('diseases') or []:
        refs=[str(x) for x in d.get('incident_refs') or []]
        if not refs: pack_only.append(d)
        for iid in refs: source_by_inc[iid]=d

    patterns=defaultdict(set)
    for p in master.get('recurring_patterns') or []:
        for iid in p.get('evidence') or []: patterns[str(iid)].add(str(p.get('id') or ''))
    recipes=defaultdict(set)
    for r in master.get('recipes') or []:
        for iid in r.get('derived_from') or []: recipes[str(iid)].add(str(r.get('id') or ''))

    diseases=[]; aliases={}; consumed=set()
    for member_ids in sorted(groups.values(),key=lambda x:x[0]):
        if any(i in forced_unclassified for i in member_ids):
            continue
        src=[source_by_inc[i] for i in member_ids if i in source_by_inc]
        forced=[(i,forced_diseases[i]) for i in member_ids if i in forced_diseases]
        if not src and not forced: continue
        ranked=sorted((by[i] for i in member_ids),key=lambda x:(STATUS.get(str(x.get('status') or '').upper(),-1),float(x.get('confidence') or 0)),reverse=True)
        anchor=ranked[0]; executable=[d for d in src if d.get('protocols')]
        if forced:
            canonical=str(forced[0][1].get('disease_id') or '')
        elif executable: canonical=str(executable[0]['disease_id'])
        elif str(anchor['id']) in source_by_inc: canonical=str(source_by_inc[str(anchor['id'])]['disease_id'])
        else: canonical=min(str(d['disease_id']) for d in src)
        if not canonical:
            raise RuntimeError('curated Disease is missing disease_id')
        source_ids=sorted({str(d['disease_id']) for d in src})
        if forced and canonical not in source_ids:
            source_ids.append(canonical)
            source_ids=sorted(set(source_ids))
        for old in source_ids:
            if old!=canonical: aliases[old]=canonical

        protocols={}; candidates={}
        for d in src:
            for p in d.get('protocols') or []: protocols[str(p.get('protocol_id') or '')]=p
            for c in d.get('protocol_candidates') or []: candidates[str(c.get('protocol_id') or '')]=c
        family_comp=component(anchor)
        raw_components=sorted({raw_component(x) for x in ranked})
        disease={
            'disease_id':canonical,
            'title':str(anchor.get('title') or canonical),
            'component':raw_component(anchor),
            'component_aliases':raw_components,
            'family_id':family_id(family_comp),
            'diagnosis_status':max((str(x.get('status') or '').upper() for x in ranked),key=lambda s:STATUS.get(s,-1)),
            'confidence':max(float(x.get('confidence') or 0) for x in ranked),
            'root_cause':str(anchor.get('root_cause') or ''),
            'incident_refs':sorted(member_ids),
            'source_disease_refs':source_ids,
            'fingerprints':sorted({str(fp) for x in ranked for fp in (x.get('fingerprint') or []) if str(fp).strip()}),
            'evidence':{
                'supporting':[{'incident_id':str(x['id']),'status':str(x.get('status') or ''),'confidence':float(x.get('confidence') or 0),'evidence':str(x.get('evidence') or ''),'source':str(x.get('source') or '')} for x in ranked],
                'required':[],
                'exclusions':[],
                'note':'Fingerprints are supporting recognition evidence; required/exclusion evidence stays empty unless explicitly structured.'
            },
            'pattern_refs':sorted({p for i in member_ids for p in patterns[i] if p}),
            'recipe_refs':sorted({r for i in member_ids for r in recipes[i] if r}),
            'protocols':sorted(protocols.values(),key=lambda x:str(x.get('protocol_id') or '')),
            'protocol_candidates':[],
            'executable_in_pack':bool(protocols),
            'curation':{
                'forced_disease':bool(forced),
                'attachments':[
                    {'incident_id':i,**attachments[i]}
                    for i in member_ids if i in attachments
                ],
                'treatment_classifications':[
                    {'incident_id':i,**treatment_classifications[i]}
                    for i in member_ids if i in treatment_classifications
                ],
            },
        }
        disease['protocol_candidates']=[{**c,'assessment':gate(c,disease)} for c in sorted(candidates.values(),key=lambda x:str(x.get('protocol_id') or ''))]
        curated_treatments=[
            treatment_classifications[i]
            for i in member_ids
            if i in treatment_classifications
            and isinstance(treatment_classifications[i],dict)
        ]
        if curated_treatments:
            chosen=sorted(
                curated_treatments,
                key=lambda x:str(x.get('decided_at') or ''),
            )[-1]
            cls=str(chosen.get('classification') or '')
            allowed={'DIAGNOSTIC_ONLY','CONFIRM_REQUIRED','CONFIRM_REQUIRED_HIGH_RISK','PRIMITIVE_MAPPING_REQUIRED'}
            if cls in allowed:
                for candidate in disease['protocol_candidates']:
                    derived={str(x) for x in (candidate.get('derived_from') or [])}
                    if derived & set(member_ids):
                        assessment=dict(candidate.get('assessment') or {})
                        assessment['state']=cls
                        assessment['promotable']=False
                        reasons=set(str(x) for x in (assessment.get('reasons') or []))
                        reasons.add('curation_classification')
                        assessment['reasons']=sorted(reasons)
                        candidate['assessment']=assessment
        diseases.append(disease); consumed.update(member_ids)

    for src in pack_only:
        d=dict(src); comp=str(d.get('component') or 'unknown').lower()
        d['family_id']=family_id(comp); d['component_aliases']=[comp]; d['source_disease_refs']=[str(d['disease_id'])]
        d['evidence']={'supporting':[],'required':[],'exclusions':[],'note':'Pack-only Disease; evidence is carried by the executable card.'}
        d['pattern_refs']=[]; d['recipe_refs']=[]
        d['protocol_candidates']=[{**c,'assessment':gate(c,d)} for c in d.get('protocol_candidates') or []]
        diseases.append(d)

    fams={}
    for d in diseases:
        fid=str(d['family_id'])
        f=fams.setdefault(fid,{'family_id':fid,'component':d.get('component'),'disease_refs':[],'incident_refs':[],'purpose':'diagnostic_routing_only'})
        f['disease_refs'].append(d['disease_id']); f['incident_refs']+=d.get('incident_refs') or []
    families=[]
    for f in fams.values():
        f['disease_refs']=sorted(set(f['disease_refs'])); f['incident_refs']=sorted(set(f['incident_refs'])); families.append(f)

    original_unclassified={str(x) for x in compiled.get('unclassified_incident_refs') or []}
    original_unclassified |= forced_unclassified
    unclassified=sorted(original_unclassified-consumed)
    clusters=[]
    for c in compiled.get('candidate_clusters') or []:
        refs=[str(x) for x in c.get('incident_refs') or [] if str(x) in unclassified]
        if len(refs)>=2: clusters.append({**c,'incident_refs':refs})

    counts=defaultdict(int)
    for d in diseases:
        for c in d.get('protocol_candidates') or []: counts[str((c.get('assessment') or {}).get('state'))]+=1

    old_stats=compiled.get('stats') or {}; old_count=int(old_stats.get('diseases') or len(compiled.get('diseases') or []))
    stats=dict(old_stats)
    stats.update({
        'pre_normalization_diseases':old_count,
        'diagnosed_incidents':len(consumed),
        'diseases':len(diseases),
        'families':len(families),
        'merged_disease_count':max(0,old_count-len(diseases)),
        'multi_incident_diseases':sum(len(d.get('incident_refs') or [])>1 for d in diseases),
        'unclassified_incidents':len(unclassified),
        'candidate_clusters':len(clusters),
        'new_protocols_promotable':0,
        'executable_protocols':sum(len(d.get('protocols') or []) for d in diseases),
        'confirmed_diseases':sum(d.get('diagnosis_status')=='CONFIRMED' for d in diseases),
        'diseases_with_protocol_candidates':sum(bool(d.get('protocol_candidates')) for d in diseases),
        'diseases_with_executable_protocols':sum(bool(d.get('protocols')) for d in diseases),
        'diseases_without_treatment_knowledge':sum(not d.get('protocol_candidates') and not d.get('protocols') for d in diseases),
        'confirmed_without_treatment_knowledge':sum(d.get('diagnosis_status')=='CONFIRMED' and not d.get('protocol_candidates') and not d.get('protocols') for d in diseases),
    })
    result={
        'schema_version':2,
        'normalization_version':'1.0',
        'source':compiled.get('source'),
        'stats':stats,
        'disease_aliases':aliases,
        'families':sorted(families,key=lambda x:x['family_id']),
        'diseases':sorted(diseases,key=lambda x:x['disease_id']),
        'unclassified_incident_refs':unclassified,
        'candidate_clusters':clusters,
        'shared_recipes':compiled.get('shared_recipes') or [],
        'candidate_assessment_counts':dict(sorted(counts.items())),
        'merge_audit':audit,
        'curation_summary':{
            'attachments':len(attachments),
            'force_unclassified':len(forced_unclassified),
            'force_diseases':len(forced_diseases),
            'treatment_classifications':len(treatment_classifications),
        },
        'integrity':compiled.get('integrity') or {},
    }
    tmp=OUTPUT.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    tmp.chmod(0o640); tmp.replace(OUTPUT)
    print(json.dumps({'result':'NORMALIZED','stats':stats,'candidate_assessment_counts':result['candidate_assessment_counts'],'aliases':len(aliases),'merge_edges':len(audit)},ensure_ascii=False))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
