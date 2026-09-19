#!/usr/bin/env python3
"""Read-only audit of a server-exported, evidence-free card list. Never publish status."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'suzie_doctor/rootfs/app'))
from suzie_doctor.connector import build_registry


def audit(cards, available_capabilities=None):
    registry=build_registry()
    rows=[]
    for card in cards:
        if not isinstance(card,dict) or not isinstance(card.get('protocol'),dict):
            raise ValueError('Expected actual normalized protocol cards; cannot audit aggregate counts')
        protocol=card['protocol']
        steps=[]
        for section in ('diagnostics','treatment','fallback','rollback'):
            steps.extend(card.get(section) or [])
        checkpoint=card.get('checkpoint') or {}
        if checkpoint.get('primitive'): steps.append(checkpoint)
        primitives=sorted({s['primitive'] for s in steps if isinstance(s,dict) and s.get('primitive')})
        caps=[registry.primitives[p].capability_id if p in registry.primitives else 'unsupported:'+p for p in primitives]
        missing=[c for c in caps if c.startswith('unsupported:') or (available_capabilities is not None and c not in available_capabilities)]
        manual=card.get('manual') or {}
        blocker=manual.get('machine_blocker_class') or (card.get('factory') or {}).get('blocker_class')
        credential=blocker=='CREDENTIAL_OR_AUTH_FLOW'
        human=credential or blocker=='HARDWARE_OR_RF_PHYSICAL'
        if human: classification='HUMAN_REQUIRED'
        elif blocker=='HIGH_RISK_MANUAL_RECOVERY': classification='TRUE_HIGH_RISK_MANUAL'
        elif missing or blocker in {'EXTERNAL_HOST_OR_CONTAINER_CONFIG','NETWORK_OR_DNS_CONFIG','PRODUCT_CONFIG_ADAPTER_REQUIRED','STORAGE_OR_DATABASE_RECOVERY','HA_CONFIG_EDIT_REQUIRED'}:
            classification='CONNECTOR_ADAPTER_MISSING'
        elif card.get('treatment') and not blocker: classification='AI_ASSISTED_POSSIBLE'
        elif protocol.get('status')=='WATCH' and not manual: classification='DIAGNOSTIC_ONLY'
        else: classification='REVIEW_REQUIRED'
        # Primitive coverage is not proof that a manual recipe has a complete safe mapping.
        factory=card.get('factory') or {}
        full=bool(card.get('treatment')) and not missing and factory.get('complete_mapping') is True and bool(card.get('verify'))
        rows.append({'protocol_id':protocol.get('id'),'status':protocol.get('status'),
            'factory_state':factory.get('state'),'required_primitives':primitives,
            'required_connector_capabilities':caps,'missing_capabilities':missing,
            'availability_basis':'runtime_snapshot' if available_capabilities is not None else 'implementation_only',
            'full_treatment_coverage':full,'checkpoint':checkpoint.get('required',False),
            'rollback_coverage':bool(card.get('rollback')),'verify_coverage':bool(card.get('verify')),
            'human_step_required':human,'credential_required':credential,
            'risk_class':max((registry.primitives[p].risk for p in primitives if p in registry.primitives),
                key=lambda x:{'low':0,'medium':1,'high':2}[x],default='unknown'),
            'classification':classification,'persisted_status_changed':False})
    return {'protocol_count':len(rows),'classifications':dict(Counter(x['classification'] for x in rows)),
        'statuses':dict(Counter(x['status'] for x in rows)),'protocols':rows}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('cards');parser.add_argument('--discovery');parser.add_argument('--output',required=True)
    args=parser.parse_args()
    data=json.loads(Path(args.cards).read_text())
    cards=data if isinstance(data,list) else data.get('cards')
    if not isinstance(cards,list): parser.error('Expected card list or {cards: [...]} export; source evidence is not needed')
    available=None
    if args.discovery:
        snapshot=json.loads(Path(args.discovery).read_text())
        available={c['capability_id'] for a in snapshot['adapters'] for c in a['capabilities'] if c['available']}
    Path(args.output).write_text(json.dumps(audit(cards,available),ensure_ascii=False,indent=2)+'\n')
