from __future__ import annotations
import json,sys
sys.path.insert(0,'/opt/suzie-doctor-server')
import disease_normalize as n

P='/var/lib/suzie-doctor-server/knowledge/normalized_knowledge.json'
M='/var/lib/suzie-doctor-server/knowledge/forum_knowledge_base.json'

def main():
    d=json.load(open(P,encoding='utf-8')); m=json.load(open(M,encoding='utf-8'))
    owner={}
    duplicates=[]
    for disease in d['diseases']:
        for iid in disease.get('incident_refs') or []:
            if iid in owner: duplicates.append(iid)
            owner[iid]=disease['disease_id']
    cases=[]
    def add(name,ok): cases.append({'id':name,'pass':bool(ok)})
    add('all_411_accounted',len(owner)+len(d['unclassified_incident_refs'])==411)
    add('no_incident_in_two_diseases',not duplicates)
    add('diagnosed_stat_matches',d['stats']['diagnosed_incidents']==len(owner))
    add('unclassified_stat_matches',d['stats']['unclassified_incidents']==len(d['unclassified_incident_refs']))
    add('disease_count_reduced',d['stats']['diseases']<d['stats']['pre_normalization_diseases'])
    add('multi_incident_diseases_present',d['stats']['multi_incident_diseases']>=15)
    add('no_candidate_auto_promoted',d['stats']['new_protocols_promotable']==0)
    add('four_executable_protocols_preserved',d['stats']['executable_protocols']==4)
    add('all_171_candidates_assessed',sum(d['candidate_assessment_counts'].values())==171)
    add('auto_safe_waits_for_primitives',d['candidate_assessment_counts'].get('PRIMITIVE_MAPPING_REQUIRED')==7)

    for group in n.VERIFIED:
        present=[x for x in group if x in owner]
        if len(present)>=2:
            add('merge_'+group[0],len({owner[x] for x in present})==1)

    never_merge=[
        ('INC-HA-BACKUP-003','INC-HA-BACKUP-004'),
        ('INC-HA-RECORDER-007','INC-HA-RECORDER-014'),
        ('INC-LINUX-SUPERVISOR-002','INC-LINUX-SUPERVISOR-004'),
        ('INC-HA-HACS-007','INC-HA-HACS-008'),
        ('INC-CROSS-TRUENAS-005','INC-CROSS-TRUENAS-006'),
        ('INC-HA-AUTO-005','INC-HA-AUTO-006'),
        ('INC-VENDOR-REOLINK-001','INC-VENDOR-REOLINK-003'),
        ('INC-FUTURE-BLE-001','INC-FUTURE-BLE-002'),
        ('INC-CROSS-SYNOLOGY-002','INC-CROSS-SYNOLOGY-004'),
        ('INC-FUTURE-ZIGBEE-001','INC-FUTURE-ZIGBEE-002'),
        ('INC-HA-RECORDER-006','INC-HA-RECORDER-007'),
    ]
    for a,b in never_merge:
        if a in owner and b in owner:
            add('separate_'+a,owner[a]!=owner[b])

    by={x['id']:x for x in m['incidents']}
    add('heuristic_duplicate_detected',bool(n.auto_reason(by['INC-FUTURE-FRIGATE-001'],by['INC-FUTURE-FRIGATE-008'])))
    add('heuristic_distinct_rejected',n.auto_reason(by['INC-HA-BACKUP-003'],by['INC-HA-BACKUP-004']) is None)

    result='PASS' if all(x['pass'] for x in cases) else 'FAIL'
    print(json.dumps({'result':result,'cases':cases,'stats':d['stats'],'candidate_assessment_counts':d['candidate_assessment_counts']},indent=2))
    return 0 if result=='PASS' else 1

if __name__=='__main__': raise SystemExit(main())
