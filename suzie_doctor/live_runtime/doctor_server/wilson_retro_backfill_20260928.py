from __future__ import annotations
import argparse, json, re
from collections import Counter
from pathlib import Path
from doctor_v2_live import DoctorV2Runtime
from knowledge_candidate_backfill import audit as legacy_audit, derived_candidate, compile_derived

DB=Path("/var/lib/suzie-doctor-server/server.sqlite3")
SCHEMA=Path("/opt/suzie-doctor-server/doctor_v2_schema.sql")
MASTER=Path("/var/lib/suzie-doctor-server/knowledge/forum_knowledge_base.json")
NORMALIZED=Path("/var/lib/suzie-doctor-server/knowledge/normalized_knowledge.json")
GENERATED=Path("/var/lib/suzie-doctor-server/knowledge/generated_protocols.json")
REPORT=Path("/var/lib/suzie-doctor-server/knowledge/wilson_retro_backfill_20260928.json")

RECOVERY=re.compile(r"\b(restor(?:e|ed|es|ing)|recover(?:ed|y|s|ing)?|fix(?:ed|es)?|worked|working|resolved|success(?:ful|fully)?|returned|stable|stability|known[- ]good|eliminated|stopped crashes|resume[ds]?)\b",re.I)
UNSAFE=re.compile(r"\b(factory reset|erase|delete database|reset nvm|security key|recommission|re-pair|non-secure inclusion|credential|password|oauth|token|physically|wiring|antenna|flash bootloader)\b",re.I)
WORKAROUND=re.compile(r"\b(workaround|temporary|bypass)\b",re.I)
UNCERTAIN=re.compile(r"\b(may|might|could|possible|possibly|suspect|hypothesis|unverified|not verified|unknown|unclear|anecdote)\b",re.I)

MANUAL_EXCLUDE={
 "PROTOCOL-KB-CANDIDATE-6E07CA4F7D":"diagnostic_only_no_treatment",
 "RCP-HTTP-AUTH-PTR-LATENCY-001":"verified_containment_not_root_cause_treatment",
 "PROTOCOL-KB-CANDIDATE-6E3C4A7B46":"guidance_only_no_exact_treatment",
 "PROTOCOL-KB-CANDIDATE-5FF7573045":"explicit_emergency_bypass_not_repair",
 "PROTOCOL-KB-CANDIDATE-A44262A909":"multiple_alternative_treatments_not_one_validatable_protocol",
 "RCP-Z2M-NETWORK-IDENTITY-001":"escalation_only_no_exact_treatment",
}
MANUAL_INCLUDE={"RCP-ENDPOINT-SCHEME-VERIFY-001":"exact_http_to_https_recovery_same_api_key"}

def load(p): return json.loads(Path(p).read_text(encoding="utf-8"))

def main():
 ap=argparse.ArgumentParser(); ap.add_argument("--apply",action="store_true"); args=ap.parse_args()
 master=load(MASTER); norm=load(NORMALIZED); generated=load(GENERATED)
 by_id={str(x.get("id")):x for x in master.get("incidents") or [] if isinstance(x,dict)}
 source={(str(d.get("disease_id") or ""),str(c.get("protocol_id") or "")):(d,c)
         for d in norm.get("diseases") or [] if isinstance(d,dict)
         for c in d.get("protocol_candidates") or [] if isinstance(c,dict)}
 gen={(str(card.get("disease_id") or ""),str((card.get("factory") or {}).get("source_candidate_id") or "")):card
      for card in generated.get("protocols") or [] if isinstance(card,dict)}
 rt=DoctorV2Runtime(DB,SCHEMA)
 inserted=[]; decisions=[]
 try:
  base=legacy_audit(rt,norm,generated,apply=False)
  existing=[]
  for row in rt.conn.execute("select protocol_id,disease_id,candidate_json,state from doctor_v2_protocol_candidates"):
   try: cand=json.loads(row["candidate_json"] or "{}")
   except Exception: cand={}
   existing.append({"protocol_id":str(row["protocol_id"]),"disease_id":str(row["disease_id"] or ""),"state":str(row["state"] or ""),"candidate":cand})

  for disp in base.get("dispositions") or []:
   if disp.get("disposition")!="REJECTED": continue
   did=str(disp.get("disease_id") or ""); src=str(disp.get("source_candidate_id") or "")
   pair=source.get((did,src)); card=gen.get((did,src))
   if not pair or not card:
    decisions.append({"disease_id":did,"source_candidate_id":src,"decision":"REJECTED","reasons":["source_or_generated_card_missing"]}); continue
   disease,candidate=pair; cid=str(candidate.get("protocol_id") or src)
   incidents=[by_id.get(str(i)) for i in candidate.get("derived_from") or []]
   incidents=[x for x in incidents if isinstance(x,dict)]
   evidence_text=" ".join(str(x.get(k) or "") for x in incidents for k in ("evidence","notes","fix","root_cause"))
   action=str(candidate.get("action") or "").strip(); reasons=[]
   if not incidents: reasons.append("no_source_incident")
   elif not all(str(x.get("status") or "").upper()=="CONFIRMED" for x in incidents): reasons.append("incident_not_confirmed")
   if not str(disease.get("root_cause") or "").strip(): reasons.append("no_root_cause")
   if not action: reasons.append("no_treatment_action")
   if not (candidate.get("verify") or []): reasons.append("no_functional_verify")
   if not RECOVERY.search(evidence_text): reasons.append("no_observed_recovery")
   if UNSAFE.search(action): reasons.append("unsafe_or_sensitive_treatment")
   if WORKAROUND.search(action) and UNCERTAIN.search(evidence_text): reasons.append("workaround_only_or_uncertain")
   if cid in MANUAL_EXCLUDE: reasons.append(MANUAL_EXCLUDE[cid])
   if cid in MANUAL_INCLUDE: reasons=[]

   duplicate=None
   for ex in existing:
    ec=ex.get("candidate") or {}
    if str(ec.get("factory_source_candidate_id") or "")==cid:
     duplicate=ex; break
    if ex["disease_id"]==did and str(ec.get("action") or "").strip().lower()==action.lower():
     duplicate=ex; break
   if duplicate:
    decisions.append({"disease_id":did,"source_candidate_id":cid,"decision":"DUPLICATE_EXISTING_V2","existing_protocol_id":duplicate["protocol_id"]}); continue
   if reasons:
    decisions.append({"disease_id":did,"source_candidate_id":cid,"decision":"REJECTED","reasons":sorted(set(reasons)),"old_mapping_rejection":disp.get("reason_class"),"title":candidate.get("title")}); continue

   derived=derived_candidate(disease,candidate,card)
   source_auto=str(candidate.get("automation_class") or "").upper(); risk=str(derived.get("risk") or "MEDIUM").upper()
   derived["automation_class"]="DIAGNOSTIC_ONLY" if source_auto=="DIAGNOSTIC_ONLY" or risk=="HIGH" else "CONFIRM_REQUIRED"
   compiled=compile_derived(disease,derived); factory=compiled.get("factory") if isinstance(compiled.get("factory"),dict) else {}
   available=bool(factory.get("mapped")) and bool(factory.get("complete_mapping")) and bool(compiled.get("treatment") or [])
   derived["execution_mapping"]="AVAILABLE" if available else "UNAVAILABLE"
   if not available:
    derived["execution_mapping_reason"]=str(factory.get("mapping_reason") or factory.get("blocker_class") or "no_complete_treatment_mapping")[:300]
   else:
    derived.pop("execution_mapping_reason",None)
   derived["retro_backfill"]={"batch":"WILSON_RETRO_2026-09-28","validation_credit_added":0,"evidence_basis":"confirmed_master_incident_with_observed_functional_recovery","old_mapping_rejection":disp.get("reason_class"),"source_incident_ids":[str(x.get("id")) for x in incidents]}
   decision={"disease_id":did,"source_candidate_id":cid,"decision":"EXPERIMENTAL_0_3","protocol_id":derived["protocol_id"],"title":derived.get("title"),"risk":derived.get("risk"),"automation_class":derived.get("automation_class"),"execution_mapping":derived.get("execution_mapping"),"execution_mapping_reason":derived.get("execution_mapping_reason")}
   decisions.append(decision)
   if args.apply:
    snap=rt.store.upsert_protocol_candidate(protocol_id=derived["protocol_id"],origin="LEGACY",disease_id=did,candidate=derived)
    inserted.append({"protocol_id":derived["protocol_id"],"disease_id":did,"state":snap.get("state"),"validation_stage":snap.get("validation_stage")})
    existing.append({"protocol_id":derived["protocol_id"],"disease_id":did,"state":str(snap.get("state") or ""),"candidate":derived})

  counts=Counter(x["decision"] for x in decisions)
  reject_reasons=Counter(r for x in decisions if x["decision"]=="REJECTED" for r in x.get("reasons") or [])
  result={"schema_version":1,"batch":"WILSON_RETRO_2026-09-28","apply":bool(args.apply),"source_legacy_candidates":len(base.get("dispositions") or []),"old_mapping_rejected":sum(1 for x in base.get("dispositions") or [] if x.get("disposition")=="REJECTED"),"counts":dict(counts),"rejection_reasons":dict(reject_reasons),"inserted":inserted,"decisions":decisions,"governance":{"external_validation_credit":0,"candidate_initial_stage":"0/3","active_watch_manual_untouched":True,"publication_untouched":True}}
  REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
  print(json.dumps({k:result[k] for k in ("apply","old_mapping_rejected","counts","rejection_reasons")},ensure_ascii=False,indent=2))
  if args.apply: print(json.dumps({"inserted":len(inserted)},ensure_ascii=False))
 finally:
  rt.store.close()

if __name__=="__main__": main()
