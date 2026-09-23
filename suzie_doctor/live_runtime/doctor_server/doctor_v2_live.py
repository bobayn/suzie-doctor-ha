from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any
from doctor_v2_store import DoctorV2Store

TERMINAL_CASE_STATES = {"RESOLVED","FAILED","HUMAN_REQUIRED","CANCELLED"}

class DoctorV2Runtime:
    def __init__(self, db_path: str|Path, schema_path: str|Path) -> None:
        self.db_path=Path(db_path)
        self.store=DoctorV2Store(self.db_path, schema_path)
        self.store.install_schema()

    @property
    def conn(self): return self.store.conn

    @staticmethod
    def _j(v: Any) -> str:
        return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",",":"))

    @staticmethod
    def _loads(v: Any) -> Any:
        if not v: return {}
        try: return json.loads(v)
        except Exception: return {}

    def snapshot(self) -> dict[str,Any]:
        c=self.conn
        return {
            "architecture_version": dict(c.execute("select key,value from doctor_v2_meta")).get("architecture_version"),
            "role_slots": {r[0]:r[1] for r in c.execute("select role,count(*) from doctor_v2_role_slots group by role")},
            "busy_slots": {r[0]:r[1] for r in c.execute("select role,count(*) from doctor_v2_role_slots where state='BUSY' group by role")},
            "waiting_house": c.execute("select count(*) from doctor_v2_house_jobs where status='WAITING'").fetchone()[0],
            "waiting_field": c.execute("select count(*) from doctor_v2_field_queue where status='WAITING'").fetchone()[0],
            "waiting_wilson": c.execute("select count(*) from doctor_v2_wilson_jobs where status='WAITING'").fetchone()[0],
            "open_dialogs": c.execute("select count(*) from doctor_v2_web_dialogs where state='OPEN'").fetchone()[0],
            "quick_check": c.execute("pragma quick_check").fetchone()[0],
        }

    def journal_to_house(self, patient_id: str, source: str, payload: dict[str,Any], *, event_type: str="OBSERVATION", severity: str|None=None, fingerprint: str|None=None, priority: int=50) -> dict[str,Any]:
        state={"latest_source":source,"latest":payload}
        version=self.store.ensure_patient(patient_id,state)
        event_id=self.store.append_event(patient_id=patient_id,event_type=event_type,source=source,payload=payload,severity=severity,fingerprint=fingerprint,create_house_job=True,priority=priority)
        row=self.conn.execute("select house_job_id,status from doctor_v2_house_jobs where trigger_event_id=?",(event_id,)).fetchone()
        return {"patient_id":patient_id,"card_version":version,"event_id":event_id,"house_job_id":int(row[0]) if row else None,"house_status":row[1] if row else None}

    def customer_feed(self, patient_id: str, limit: int=80) -> dict[str,Any]:
        rows=self.conn.execute(
            """select d.*,j.trigger_event_id,e.payload_json as event_payload_json,e.created_at as event_created_at,e.source as event_source,e.fingerprint as event_fingerprint,
                      q.status as field_status,c.outcome as case_outcome
               from doctor_v2_house_decisions d
               join doctor_v2_house_jobs j on j.house_job_id=d.house_job_id
               left join doctor_v2_patient_events e on e.event_id=j.trigger_event_id
               left join doctor_v2_field_queue q on q.source_house_decision_id=d.decision_id
               left join doctor_cases c on c.case_id=q.legacy_case_id
               where d.patient_id=? order by d.decision_id desc limit ?""",
            (str(patient_id),max(1,min(200,int(limit)))),
        ).fetchall()
        entries=[]
        for row in rows:
            x=dict(row); payload=self._loads(x.get("event_payload_json"))
            ev=payload.get("evidence") if isinstance(payload.get("evidence"),dict) else {}
            fp=str(ev.get("fingerprint") or x.get("event_fingerprint") or "")
            event_source=str(x.get("event_source") or "").upper()
            if fp.startswith("CONTROLLED_") or fp.startswith("migration-") or "CONTROLLED" in event_source or "SMOKE" in event_source or bool(payload.get("simulated")) or bool(payload.get("controlled")) or bool(payload.get("migration_smoke")): continue
            subject=("incident:"+str(ev.get("problem_key"))) if ev.get("problem_key") else ("fingerprint:"+fp if fp else f"house-event:{x.get('trigger_event_id')}")
            logger=str(ev.get("logger") or "").lower()
            raw_message=str(ev.get("message") or "").lower()
            if "gas-meter-cam" in raw_message or "192_168_0_120" in raw_message or "diagnostika_gaz" in logger:
                event_title="Камера счётчика газа временно недоступна"
            elif logger.startswith("homeassistant.components.broadlink"):
                event_title="Устройство Broadlink временно не ответило"
            elif "frontend" in logger or "button-card" in raw_message:
                event_title="Интерфейс Home Assistant сообщил техническое событие"
            elif "bluetooth" in logger:
                event_title="Bluetooth сообщил техническое событие"
            elif "scheduler" in logger or "scheduler" in raw_message:
                event_title="Расписание Home Assistant сообщило техническое событие"
            else:
                event_title="Home Assistant сообщил техническое событие"
            decision=str(x.get("decision") or "")
            outcome=str(x.get("case_outcome") or "")
            field=str(x.get("field_status") or "")
            status,title,message,verified,action=("OBSERVING",event_title,"House проверил данные: подтверждённой неисправности пока нет.",False,False)
            if decision=="IGNORE_AS_NOISE": status,message,verified=("NO_ACTION_NEEDED","House проверил событие и не подтвердил проблему, требующую действий.",True)
            elif decision=="RECHECK_LATER": message="House пока не подтверждает неисправность. Состояние будет проверено повторно."
            elif decision=="HUMAN_ACTION_REQUIRED": status,message,action=("ACTION_NEEDED","House подтвердил, что для безопасного продолжения требуется действие владельца.",True)
            elif decision=="DISPATCH_SUZIE":
                if field=="DONE" and outcome in {"SUCCESS","RESOLVED"}: status,message,verified=("REVIEW_COMPLETE","Углублённая проверка завершена; вмешательство владельца не требуется.",True)
                elif field=="DONE" and outcome=="HUMAN_REQUIRED": status,message,action=("ACTION_NEEDED","После дополнительной проверки требуется действие владельца.",True)
                else: status,message=("CHECKING","House передал случай на углублённую диагностику. Это ещё не подтверждённая неисправность.")
            created=str(x.get("created_at") or "").replace(" ","T",1)
            if created and not (created.endswith("Z") or "+" in created[10:]): created+="+00:00"
            state_at=str(x.get("event_created_at") or "").replace(" ","T",1)
            if state_at and not (state_at.endswith("Z") or "+" in state_at[10:]): state_at+="+00:00"
            entries.append({"id":f"house:{x['decision_id']}","subject_key":subject,"actor":"HOUSE","status_code":status,"title":title,"message":message,"verified":verified,"user_action_required":action,"significance":str(x.get("significance") or "LOW"),"created_at":created,"state_at":state_at or created,"technical_ref":f"house-decision:{x['decision_id']}","source":{"decision":decision,"finding_class":str(x.get("finding_class") or "")}})
        pending=int(self.conn.execute("select count(*) from doctor_v2_house_jobs where patient_id=? and status in ('WAITING','CLAIMED')",(str(patient_id),)).fetchone()[0])
        return {"entries":entries,"pending_count":pending}

    def next_house_waiting(self) -> dict[str,Any]|None:
        r=self.conn.execute("select * from doctor_v2_house_jobs where status='WAITING' order by priority desc,house_job_id limit 1").fetchone()
        return dict(r) if r else None

    def claim_house(self, job_id: int, dialog_id: str) -> dict[str,Any]|None:
        with self.conn:
            n=self.conn.execute("update doctor_v2_house_jobs set status='CLAIMED',claimed_dialog_id=?,claimed_at=CURRENT_TIMESTAMP where house_job_id=? and status='WAITING'",(dialog_id,int(job_id))).rowcount
            if n!=1: return None
            r=self.conn.execute("select * from doctor_v2_house_jobs where house_job_id=?",(int(job_id),)).fetchone()
            return dict(r) if r else None

    @staticmethod
    def _experimental_text(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True).lower()
        except Exception:
            return str(value or "").lower()

    @staticmethod
    def _experimental_tokens(value: Any) -> set[str]:
        import re
        text=DoctorV2Runtime._experimental_text(value)
        return {x for x in re.findall(r"[\w.-]+",text,flags=re.UNICODE) if len(x)>=4}

    @staticmethod
    def _collect_disease_ids(value: Any) -> set[str]:
        found:set[str]=set()
        if isinstance(value,dict):
            for key,item in value.items():
                if str(key) in {"disease_id","confirmed_disease_id","candidate_disease_id"} and item:
                    found.add(str(item))
                found.update(DoctorV2Runtime._collect_disease_ids(item))
        elif isinstance(value,list):
            for item in value:
                found.update(DoctorV2Runtime._collect_disease_ids(item))
        return found

    def experimental_candidates_for_patient(self, patient_id: str, limit:int=5) -> list[dict[str,Any]]:
        card=self.conn.execute(
            "select * from doctor_v2_patient_cards where patient_id=?",
            (str(patient_id),),
        ).fetchone()
        state=self._loads(card["state_json"]) if card else {}
        event_payloads=[]
        for row in self.conn.execute(
            "select payload_json from doctor_v2_patient_events where patient_id=? order by event_id desc limit 30",
            (str(patient_id),),
        ):
            event_payloads.append(self._loads(row[0]))
        patient_context={"state":state,"events":event_payloads}
        disease_ids=self._collect_disease_ids(patient_context)
        patient_text=self._experimental_text(patient_context)
        patient_tokens=self._experimental_tokens(patient_context)
        eligible={"CANDIDATE","FIELD_TESTING","VALIDATED_1_3","VALIDATED_2_3"}
        matched=[]
        rows=self.conn.execute(
            "select * from doctor_v2_protocol_candidates order by updated_at desc"
        ).fetchall()
        for row in rows:
            raw=dict(row)
            if str(raw.get("state")) not in eligible:
                continue
            try: candidate=json.loads(str(raw.get("candidate_json") or "{}"))
            except Exception: candidate={}
            disease_id=str(raw.get("disease_id") or candidate.get("disease_id") or "").strip()
            reasons=[]; score=0
            if disease_id and disease_id in disease_ids:
                score+=100; reasons.append("disease_id_exact")
            elif disease_id and disease_id.lower() in patient_text:
                score+=80; reasons.append("disease_id_in_patient_context")
            candidate_scope={
                "title":candidate.get("title"),
                "symptoms":candidate.get("symptoms"),
                "fingerprints":candidate.get("fingerprints"),
                "evidence":candidate.get("evidence"),
                "checks":candidate.get("checks"),
                "component":candidate.get("component"),
            }
            overlap=patient_tokens & self._experimental_tokens(candidate_scope)
            if overlap:
                score+=min(36,len(overlap)*4)
                reasons.append("symptom_evidence_overlap")
            if score < 8:
                continue
            snap=self.store.protocol_candidate(str(raw["protocol_id"])) or {}
            matched.append({
                "protocol_id":str(raw["protocol_id"]),
                "origin":str(raw.get("origin") or ""),
                "state":str(raw.get("state") or ""),
                "disease_id":disease_id or None,
                "validation_stage":str(snap.get("validation_stage") or "0/3"),
                "verified_successes":int(snap.get("verified_successes") or 0),
                "negative_episodes":int(snap.get("negative_episodes") or 0),
                "match_score":score,
                "match_reasons":reasons,
                "candidate":{
                    "title":candidate.get("title"),
                    "symptoms":candidate.get("symptoms"),
                    "checks":candidate.get("checks"),
                    "action":candidate.get("action"),
                    "verify":candidate.get("verify"),
                    "risk":candidate.get("risk"),
                    "automation_class":candidate.get("automation_class"),
                },
            })
        matched.sort(key=lambda x:(int(x["match_score"]),-int(x["verified_successes"])),reverse=True)
        return matched[:max(1,min(20,int(limit)))]

    def house_get(self, job_id:int) -> dict[str,Any]|None:
        r=self.conn.execute("select * from doctor_v2_house_jobs where house_job_id=?",(int(job_id),)).fetchone()
        if not r: return None
        d=dict(r)
        card=self.conn.execute("select * from doctor_v2_patient_cards where patient_id=?",(d["patient_id"],)).fetchone()
        d["patient_card"]=dict(card) if card else None
        if d["patient_card"]: d["patient_card"]["state"]=self._loads(d["patient_card"].pop("state_json",None))
        d["experimental_protocol_candidates"]=self.experimental_candidates_for_patient(str(d["patient_id"]))
        d["recent_events"]=[]
        for e in self.conn.execute("select * from doctor_v2_patient_events where patient_id=? order by event_id desc limit 30",(d["patient_id"],)):
            x=dict(e); x["payload"]=self._loads(x.pop("payload_json",None)); d["recent_events"].append(x)
        return d

    def house_decide(self, job_id:int, result:dict[str,Any]) -> dict[str,Any]:
        decision_id=self.store.submit_house_decision(int(job_id),result)
        q=self.conn.execute("select * from doctor_v2_field_queue where source_house_decision_id=?",(decision_id,)).fetchone()
        return {"decision_id":decision_id,"field_queue":dict(q) if q else None}

    def set_field_legacy_case(self, queue_id:int, case_id:int) -> None:
        with self.conn:
            self.conn.execute("update doctor_v2_field_queue set legacy_case_id=?,status='ASSIGNED',assignment_id=?,updated_at=CURRENT_TIMESTAMP where queue_id=?",(int(case_id),f"case:{int(case_id)}",int(queue_id)))

    def field_validation_requirement(self, case_id:int) -> dict[str,Any]|None:
        row=self.conn.execute(
            """select queue_id,patient_id,house_directive,experimental_protocol_id,validation_stage
               from doctor_v2_field_queue where legacy_case_id=? limit 1""",
            (int(case_id),),
        ).fetchone()
        if not row or str(row["house_directive"] or "")!="VALIDATE_FIRST":
            return None
        return dict(row)

    def normalize_field_validation_result(
        self, case_id:int, client_id:str, result:dict[str,Any]
    )->dict[str,Any]|None:
        req=self.field_validation_requirement(int(case_id))
        if not req:
            return None
        raw=result.get("experimental_validation")
        if not isinstance(raw,dict):
            raise ValueError("VALIDATE_FIRST Case requires experimental_validation result")
        expected=str(req.get("experimental_protocol_id") or "")
        if str(raw.get("protocol_id") or "") != expected:
            raise ValueError("experimental_validation protocol_id does not match House directive")
        if raw.get("independent_diagnosis_performed") is not True:
            raise ValueError("VALIDATE_FIRST requires independent_diagnosis_performed=true")
        disease_confirmed=raw.get("disease_confirmed")
        if not isinstance(disease_confirmed,bool):
            raise ValueError("experimental_validation disease_confirmed must be boolean")
        attempted=bool(raw.get("attempted"))
        applicable=raw.get("applicable")
        if not isinstance(applicable,bool):
            raise ValueError("experimental_validation applicable must be boolean")
        risk_decision=str(raw.get("risk_decision") or "NOT_ASSESSED").upper()
        if risk_decision not in {"PROCEED","AVOID","NOT_ASSESSED"}:
            raise ValueError("experimental_validation risk_decision invalid")
        treatment_result=str(raw.get("treatment_result") or "NOT_ATTEMPTED").upper()
        if treatment_result not in {"SUCCESS","FAILED","NOT_ATTEMPTED","UNAVAILABLE","BLOCKED"}:
            raise ValueError("experimental_validation treatment_result invalid")
        verify_result=str(raw.get("verify_result") or "NOT_RUN").upper()
        if verify_result not in {"PASS","FAIL","NOT_RUN","INCONCLUSIVE"}:
            raise ValueError("experimental_validation verify_result invalid")
        if treatment_result=="SUCCESS" and verify_result=="NOT_RUN":
            raise ValueError("successful Experimental treatment requires verify")
        if attempted and not disease_confirmed:
            raise ValueError("Experimental treatment cannot be attempted without independently confirmed Disease")
        if attempted and not applicable:
            raise ValueError("Experimental treatment cannot be attempted when candidate is not applicable")
        if attempted and risk_decision!="PROCEED":
            raise ValueError("Experimental treatment attempt requires risk_decision=PROCEED")
        if treatment_result in {"SUCCESS","FAILED"} and not attempted:
            raise ValueError("treatment result requires attempted=true")
        success=(treatment_result=="SUCCESS" and verify_result=="PASS")
        verified=verify_result in {"PASS","FAIL"}
        continued=raw.get("continued_case_diagnosis")
        if not success and continued is not True:
            raise ValueError("failed/inapplicable Experimental validation must continue Case diagnosis before completion")
        normalized={
            "protocol_id":expected,
            "episode_key":f"field:{int(case_id)}",
            "source":"FIELD_CASE",
            "internal_verified":verified,
            "patient_id":str(client_id),
            "field_case_id":str(int(case_id)),
            "success":success,
            "verified":verified,
            "independent_diagnosis_performed":True,
            "disease_confirmed":disease_confirmed,
            "attempted":attempted,
            "applicable":applicable,
            "continued_case_diagnosis":bool(continued),
            "risk_decision":risk_decision,
            "treatment_result":treatment_result,
            "verify_result":verify_result,
            "reason":str(raw.get("reason") or "")[:2000],
            "house_validation_stage":str(req.get("validation_stage") or "0/3"),
            "evidence":dict(raw.get("evidence") or {}),
        }
        return normalized

    def enqueue_field_validation_wilson(
        self, case_id:int, validation:dict[str,Any], case_result:dict[str,Any]
    )->int:
        cursor=f"FIELD_VALIDATION:{int(case_id)}"
        existing=self.conn.execute(
            "select wilson_job_id from doctor_v2_wilson_jobs where input_cursor=? order by wilson_job_id desc limit 1",
            (cursor,),
        ).fetchone()
        if existing:
            return int(existing[0])
        batch={
            "reason":"EXPERIMENTAL_FIELD_VALIDATION",
            "case_id":int(case_id),
            "required_validations":[dict(validation)],
            "case_result":dict(case_result or {}),
        }
        return self.enqueue_wilson("HOURLY_REVIEW",batch,cursor)

    def field_done_for_case(self, case_id:int) -> None:
        with self.conn:
            self.conn.execute("update doctor_v2_field_queue set status='DONE',updated_at=CURRENT_TIMESTAMP where legacy_case_id=? and status in ('ASSIGNED','CLAIMED')",(int(case_id),))

    def role_acquire(self, role:str, assignment_id:str)->str|None:
        return self.store.acquire_role_slot(role,assignment_id)
    def role_release(self, slot_id:str, assignment_id:str)->bool:
        return self.store.release_role_slot(slot_id,assignment_id)

    def dialog_open(self, dialog_id:str, role:str, assignment_id:str, project_id:str|None, generation:int=1)->dict[str,Any]:
        self.store.open_dialog(dialog_id=dialog_id,role=role,assignment_id=assignment_id,project_id=project_id,generation=generation)
        a=self.store.start_session(dialog_id,{"assignment_id":assignment_id,"role":role})
        return {"dialog_id":dialog_id,"ordinal":a.ordinal,"rotate_after":a.rotate_after}

    def dialog_end(self, dialog_id:str, ordinal:int, reason:str)->dict[str,Any]:
        return self.store.end_session(dialog_id,ordinal,reason)

    def dialog_checkpoint(self, dialog_id:str, assignment_id:str, checkpoint:dict[str,Any])->int:
        return self.store.save_checkpoint(dialog_id=dialog_id,assignment_id=assignment_id,checkpoint=checkpoint)

    def current_dialog(self, role:str, assignment_id:str)->dict[str,Any]|None:
        r=self.conn.execute("select * from doctor_v2_web_dialogs where role=? and assignment_id=? order by generation desc limit 1",(role,assignment_id)).fetchone()
        return dict(r) if r else None

    def open_session(self, dialog_id:str, payload:dict[str,Any]|None=None)->dict[str,Any]:
        a=self.store.start_session(dialog_id,payload or {})
        return {"dialog_id":dialog_id,"ordinal":a.ordinal,"rotate_after":a.rotate_after}

    def expired_sessions(self)->list[dict[str,Any]]:
        q="""select s.dialog_id,s.ordinal,d.role,d.assignment_id,d.project_id,d.generation
             from doctor_v2_web_sessions s join doctor_v2_web_dialogs d on d.dialog_id=s.dialog_id
             where s.ended_at is null and d.state='OPEN' and datetime(s.started_at) <= datetime('now','-10 minutes')
             order by s.session_id"""
        return [dict(r) for r in self.conn.execute(q)]

    def enqueue_wilson(self, mode:str, batch:dict[str,Any], input_cursor:str|None=None)->int:
        if mode not in {"HOURLY_REVIEW","NIGHTLY_RESEARCH"}: raise ValueError("bad Wilson mode")
        with self.conn:
            cur=self.conn.execute("insert into doctor_v2_wilson_jobs(mode,input_cursor,batch_json) values(?,?,?)",(mode,input_cursor,self._j(batch)))
            return int(cur.lastrowid)

    def next_wilson_waiting(self)->dict[str,Any]|None:
        r=self.conn.execute("select * from doctor_v2_wilson_jobs where status='WAITING' order by wilson_job_id limit 1").fetchone()
        return dict(r) if r else None

    def claim_wilson(self, job_id:int)->dict[str,Any]|None:
        with self.conn:
            n=self.conn.execute("update doctor_v2_wilson_jobs set status='CLAIMED' where wilson_job_id=? and status='WAITING'",(int(job_id),)).rowcount
            if n!=1:return None
            r=self.conn.execute("select * from doctor_v2_wilson_jobs where wilson_job_id=?",(int(job_id),)).fetchone()
            return dict(r) if r else None

    def wilson_get(self, job_id:int)->dict[str,Any]|None:
        r=self.conn.execute("select * from doctor_v2_wilson_jobs where wilson_job_id=?",(int(job_id),)).fetchone()
        if not r:return None
        d=dict(r); d["batch"]=self._loads(d.pop("batch_json",None)); d["result"]=self._loads(d.pop("result_json",None)); return d

    def wilson_complete(self, job_id:int, result:dict[str,Any], output_cursor:str|None=None, *, failed:bool=False)->dict[str,Any]:
        r=self.conn.execute("select * from doctor_v2_wilson_jobs where wilson_job_id=?",(int(job_id),)).fetchone()
        if not r or r["status"]!="CLAIMED": raise RuntimeError("Wilson job is not claimed")
        batch=self._loads(r["batch_json"])
        required=[x for x in (batch.get("required_validations") or []) if isinstance(x,dict)]
        validations=[x for x in (result.get("validations") or []) if isinstance(x,dict)]
        if required and not failed:
            for req in required:
                pid=str(req.get("protocol_id") or "")
                episode=str(req.get("episode_key") or "")
                got=next((x for x in validations if str(x.get("protocol_id") or "")==pid and str(x.get("episode_key") or "")==episode),None)
                if got is None:
                    raise RuntimeError(f"Wilson must return required Field validation {pid}/{episode}")
                if bool(got.get("success")) != bool(req.get("success")) or bool(got.get("verified")) != bool(req.get("verified")):
                    raise RuntimeError("Wilson cannot rewrite Field success/verify facts")
                if str(got.get("source") or "").upper() not in {"INTERNAL","INTERNAL_FIELD","FIELD_CASE"}:
                    raise RuntimeError("required Field validation must remain internal evidence")
                got.setdefault("patient_id",req.get("patient_id"))
                got.setdefault("field_case_id",req.get("field_case_id"))
                got.setdefault("evidence",req.get("evidence") or {})
        validation_results=[]
        governance_results=[]
        publication_queue=[]
        if not failed:
            candidates=result.get("protocol_candidates")
            if isinstance(candidates,list):
                with self.conn:
                    for item in candidates:
                        if not isinstance(item,dict):continue
                        pid=str(item.get("protocol_id") or "").strip()
                        if not pid:continue
                        origin=str(item.get("origin") or ("EXTERNAL_WILSON" if r["mode"]=="NIGHTLY_RESEARCH" else "INTERNAL_FIELD")).upper()
                        if origin not in {"INTERNAL_FIELD","EXTERNAL_WILSON"}:
                            origin="INTERNAL_FIELD"
                        state="CANDIDATE" if origin=="EXTERNAL_WILSON" else "FIELD_TESTING"
                        self.conn.execute(
                            """insert into doctor_v2_protocol_candidates(protocol_id,origin,state,disease_id,candidate_json)
                               values(?,?,?,?,?)
                               on conflict(protocol_id) do update set
                                 disease_id=coalesce(excluded.disease_id,doctor_v2_protocol_candidates.disease_id),
                                 candidate_json=excluded.candidate_json,
                                 updated_at=CURRENT_TIMESTAMP""",
                            (pid,origin,state,item.get("disease_id"),self._j(item)),
                        )
            for item in validations:
                source=str(item.get("source") or "").upper()
                internal=bool(item.get("internal_verified")) or source in {"INTERNAL","INTERNAL_FIELD","FIELD_CASE"}
                if not internal:
                    continue
                pid=str(item.get("protocol_id") or "").strip()
                episode=str(item.get("episode_key") or "").strip()
                if not pid or not episode:continue
                exists=self.conn.execute("select 1 from doctor_v2_protocol_candidates where protocol_id=?",(pid,)).fetchone()
                if not exists:continue
                vr=self.store.record_protocol_validation(
                    protocol_id=pid,
                    episode_key=episode,
                    success=bool(item.get("success")),
                    verified=bool(item.get("verified")),
                    evidence=dict(item.get("evidence") or {}),
                    installation_id_hash=item.get("installation_id_hash"),
                    patient_id=item.get("patient_id"),
                    field_case_id=item.get("field_case_id"),
                )
                validation_results.append({"protocol_id":pid,"episode_key":episode,**vr})
            actions=[x for x in (result.get("candidate_actions") or []) if isinstance(x,dict)]
            governed:set[str]=set()
            for action in actions:
                pid=str(action.get("protocol_id") or "").strip()
                op=str(action.get("action") or "").upper()
                if not pid or op not in {"SUSPEND","REVISE"}:
                    continue
                current=self.store.protocol_candidate(pid)
                if not current:
                    continue
                governed.add(pid)
                if op=="SUSPEND":
                    with self.conn:
                        self.conn.execute(
                            "update doctor_v2_protocol_candidates set state='SUSPENDED',updated_at=CURRENT_TIMESTAMP where protocol_id=?",
                            (pid,),
                        )
                    governance_results.append({"protocol_id":pid,"action":"SUSPEND","reason":str(action.get("reason") or "")[:1000]})
                else:
                    replacement=action.get("replacement_candidate")
                    if not isinstance(replacement,dict):
                        raise RuntimeError("REVISE requires replacement_candidate")
                    new_pid=str(replacement.get("protocol_id") or "").strip()
                    if not new_pid or new_pid==pid:
                        raise RuntimeError("REVISE requires a new protocol_id")
                    disease_id=str(replacement.get("disease_id") or current.get("disease_id") or "") or None
                    with self.conn:
                        self.conn.execute(
                            "update doctor_v2_protocol_candidates set state='SUSPENDED',updated_at=CURRENT_TIMESTAMP where protocol_id=?",
                            (pid,),
                        )
                        self.conn.execute(
                            """insert into doctor_v2_protocol_candidates(protocol_id,origin,state,disease_id,candidate_json)
                               values(?, 'INTERNAL_FIELD', 'FIELD_TESTING', ?, ?)
                               on conflict(protocol_id) do nothing""",
                            (new_pid,disease_id,self._j(replacement)),
                        )
                    governance_results.append({"protocol_id":pid,"action":"REVISE","replacement_protocol_id":new_pid})
            for vr in validation_results:
                pid=str(vr.get("protocol_id") or "")
                if bool(vr.get("publication_ready")) and pid not in governed:
                    publication_queue.append(self.store.enqueue_publication_review(
                        pid,int(job_id),{"validation":vr,"wilson_result":dict(result or {})}
                    ))
        with self.conn:
            status="FAILED" if failed else "DONE"
            self.conn.execute("update doctor_v2_wilson_jobs set status=?,result_json=?,output_cursor=?,completed_at=CURRENT_TIMESTAMP where wilson_job_id=?",(status,self._j(result),output_cursor,int(job_id)))
            if output_cursor is not None and not failed:
                self.conn.execute("insert into doctor_v2_wilson_cursors(stream,cursor_value) values('FIELD_CASE_REPORTS',?) on conflict(stream) do update set cursor_value=excluded.cursor_value,updated_at=CURRENT_TIMESTAMP",(str(output_cursor),))
        return {
            "wilson_job_id":int(job_id),
            "status":status,
            "output_cursor":output_cursor,
            "validation_results":validation_results,
            "governance_results":governance_results,
            "publication_queue":publication_queue,
        }

    def ensure_wilson_baseline(self)->str:
        r=self.conn.execute("select cursor_value from doctor_v2_wilson_cursors where stream='FIELD_CASE_REPORTS'").fetchone()
        if r:return str(r[0])
        max_case=self.conn.execute("select coalesce(max(case_id),0) from doctor_cases where outcome is not null").fetchone()[0]
        with self.conn:
            self.conn.execute("insert into doctor_v2_wilson_cursors(stream,cursor_value) values('FIELD_CASE_REPORTS',?)",(str(max_case),))
        return str(max_case)

    def build_hourly_wilson_batch(self)->dict[str,Any]|None:
        cursor=int(self.ensure_wilson_baseline() or 0)
        rows=[dict(r) for r in self.conn.execute("select case_id,client_id,source_key,summary,disease_id,state,outcome,result_json,updated_at from doctor_cases where outcome is not null and case_id>? order by case_id",(cursor,))]
        if not rows:return None
        for r in rows:r["result"]=self._loads(r.pop("result_json",None))
        return {"cursor_from":cursor,"cursor_to":max(r["case_id"] for r in rows),"case_reports":rows}

    def has_open_wilson_mode(self,mode:str)->bool:
        return self.conn.execute("select 1 from doctor_v2_wilson_jobs where mode=? and status in ('WAITING','CLAIMED') limit 1",(mode,)).fetchone() is not None
