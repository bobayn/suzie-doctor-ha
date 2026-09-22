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
            """select d.*,j.trigger_event_id,e.payload_json as event_payload_json,e.created_at as event_created_at,
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
            fp=str(ev.get("fingerprint") or "")
            if fp.startswith("CONTROLLED_") or bool(payload.get("simulated")): continue
            subject=("incident:"+str(ev.get("problem_key"))) if ev.get("problem_key") else ("fingerprint:"+fp if fp else f"house-event:{x.get('trigger_event_id')}")
            decision=str(x.get("decision") or "")
            outcome=str(x.get("case_outcome") or "")
            field=str(x.get("field_status") or "")
            status,title,message,verified,action=("OBSERVING","Наблюдаю за ситуацией","House проверил данные: подтверждённой неисправности пока нет.",False,False)
            if decision=="IGNORE_AS_NOISE": status,title,message,verified=("NO_ACTION_NEEDED","Проверено — действий не требуется","House проверил событие и не подтвердил проблему, требующую действий.",True)
            elif decision=="RECHECK_LATER": message="House пока не подтверждает неисправность. Состояние будет проверено повторно."
            elif decision=="HUMAN_ACTION_REQUIRED": status,title,message,action=("ACTION_NEEDED","Нужно ваше внимание","House подтвердил, что для безопасного продолжения требуется действие владельца.",True)
            elif decision=="DISPATCH_SUZIE":
                if field=="DONE" and outcome in {"SUCCESS","RESOLVED"}: status,title,message,verified=("REVIEW_COMPLETE","Дополнительная проверка завершена","Углублённая проверка завершена; вмешательство владельца не требуется.",True)
                elif field=="DONE" and outcome=="HUMAN_REQUIRED": status,title,message,action=("ACTION_NEEDED","Нужно ваше внимание","После дополнительной проверки требуется действие владельца.",True)
                else: status,title,message=("CHECKING","Проверяю подробнее","House передал случай на углублённую диагностику. Это ещё не подтверждённая неисправность.")
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

    def house_get(self, job_id:int) -> dict[str,Any]|None:
        r=self.conn.execute("select * from doctor_v2_house_jobs where house_job_id=?",(int(job_id),)).fetchone()
        if not r: return None
        d=dict(r)
        card=self.conn.execute("select * from doctor_v2_patient_cards where patient_id=?",(d["patient_id"],)).fetchone()
        d["patient_card"]=dict(card) if card else None
        if d["patient_card"]: d["patient_card"]["state"]=self._loads(d["patient_card"].pop("state_json",None))
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
        validation_results=[]
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
            validations=result.get("validations")
            if isinstance(validations,list):
                for item in validations:
                    if not isinstance(item,dict):continue
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
        with self.conn:
            status="FAILED" if failed else "DONE"
            self.conn.execute("update doctor_v2_wilson_jobs set status=?,result_json=?,output_cursor=?,completed_at=CURRENT_TIMESTAMP where wilson_job_id=?",(status,self._j(result),output_cursor,int(job_id)))
            if output_cursor is not None and not failed:
                self.conn.execute("insert into doctor_v2_wilson_cursors(stream,cursor_value) values('FIELD_CASE_REPORTS',?) on conflict(stream) do update set cursor_value=excluded.cursor_value,updated_at=CURRENT_TIMESTAMP",(str(output_cursor),))
        return {"wilson_job_id":int(job_id),"status":status,"output_cursor":output_cursor,"validation_results":validation_results}

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
