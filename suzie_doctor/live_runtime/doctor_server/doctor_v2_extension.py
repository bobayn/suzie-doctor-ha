from __future__ import annotations
import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from aiohttp import web
from doctor_v2_live import DoctorV2Runtime
from knowledge_candidate_backfill import audit as audit_legacy_candidates

HOUSE_PROJECT_URL="https://chatgpt.com/g/g-p-6ab184e457cc819182e5230e09fdfc18-doktor-khaus"
WILSON_PROJECT_URL="https://chatgpt.com/g/g-p-6ab1850655508191afad64d3cc104b3a-doktor-vilson"

class V2Extension:
    def __init__(self, server: Any, db_path: str, schema_path: str) -> None:
        self.server=server
        self.runtime=DoctorV2Runtime(db_path,schema_path)
        self.tasks:set[asyncio.Task[Any]]=set()
        self.next_hourly=time.monotonic()+3600
        self.runtime.ensure_wilson_baseline()
        self._reconcile_legacy_experimental_knowledge()

    def _reconcile_legacy_experimental_knowledge(self)->None:
        normalized_path=Path('/var/lib/suzie-doctor-server/knowledge/normalized_knowledge.json')
        generated_path=Path('/var/lib/suzie-doctor-server/knowledge/generated_protocols.json')
        report_path=Path('/var/lib/suzie-doctor-server/knowledge/v2_candidate_audit_latest.json')
        if not normalized_path.exists() or not generated_path.exists():
            return
        try:
            result=audit_legacy_candidates(
                self.runtime,
                json.loads(normalized_path.read_text(encoding='utf-8')),
                json.loads(generated_path.read_text(encoding='utf-8')),
                apply=True,
            )
            report_path.write_text(
                json.dumps(result,ensure_ascii=False,indent=2)+'\n',
                encoding='utf-8',
            )
            try:
                report_path.chmod(0o640)
            except Exception:
                pass
            self.server.db.event('doctor_v2_legacy_candidate_reconciled',None,{
                'source_candidates':result.get('source_candidates'),
                'counts':result.get('counts'),
                'rejection_classes':result.get('rejection_classes'),
                'inserted':len(result.get('inserted') or []),
                'report_path':str(report_path),
            })
        except Exception as exc:
            self.server.db.event('doctor_v2_legacy_candidate_reconcile_error',None,{
                'error':f'{type(exc).__name__}: {exc}',
            })

    def _spawn(self,coro,name:str)->None:
        t=asyncio.create_task(coro,name=name);self.tasks.add(t);t.add_done_callback(self.tasks.discard)

    def _project_url(self, role:str)->str:
        if role=="HOUSE": return HOUSE_PROJECT_URL
        if role=="WILSON": return WILSON_PROJECT_URL
        return str(self.server.config.get("field_project_url") or "")

    def _target(self, role:str)->str:
        return {"HOUSE":"house","WILSON":"wilson","FIELD_SUZIE":"project"}[role]

    def _project_id(self, role:str)->str:
        url=self._project_url(role)
        m=re.search(r"/g/(g-p-[A-Za-z0-9]+)",url)
        return m.group(1) if m else url

    def reserve_field(self,case_id:int)->str|None:
        assignment=f"case:{int(case_id)}"
        row=self.runtime.conn.execute(
            "select slot_id from doctor_v2_role_slots where role='FIELD_SUZIE' and assignment_id=? and state='BUSY'",
            (assignment,),
        ).fetchone()
        if row:return str(row[0])
        return self.runtime.role_acquire("FIELD_SUZIE",assignment)

    def release_field(self,case_id:int)->None:
        assignment=f"case:{int(case_id)}"
        row=self.runtime.conn.execute(
            "select slot_id from doctor_v2_role_slots where role='FIELD_SUZIE' and assignment_id=? and state='BUSY'",
            (assignment,),
        ).fetchone()
        if row:self.runtime.role_release(str(row[0]),assignment)

    def bind_field_dialog(self,case_id:int,dialog_id:str)->dict[str,Any]|None:
        assignment=f"case:{int(case_id)}"
        current=self.runtime.current_dialog("FIELD_SUZIE",assignment)
        if current and str(current.get("dialog_id"))==str(dialog_id):
            return current
        generation=int(current.get("generation") or 0)+1 if current else 1
        return self.runtime.dialog_open(
            str(dialog_id),"FIELD_SUZIE",assignment,
            self._project_id("FIELD_SUZIE"),generation,
        )

    async def field_finished(self,case_id:int,client_id:str,outcome:str,result:dict[str,Any])->dict[str,Any]:
        assignment=f"case:{int(case_id)}"
        self.runtime.field_done_for_case(int(case_id))
        self.runtime.mark_resolution_field_finished(int(case_id),str(outcome),dict(result or {}))
        self.runtime.store.ensure_patient(
            str(client_id),
            {"last_field_case_id":int(case_id),"last_field_outcome":str(outcome),"last_field_report":dict(result or {})},
        )
        self.runtime.store.append_event(
            patient_id=str(client_id),event_type="CASE_EVENT",source="FIELD_SUZIE",
            payload={"case_id":int(case_id),"outcome":str(outcome),"result":dict(result or {})},
            fingerprint=f"case:{int(case_id)}",create_house_job=False,
        )
        wilson_job_id=None
        validation=result.get("experimental_validation") if isinstance(result,dict) else None
        requirement=self.runtime.field_validation_requirement(int(case_id))
        normalized_validation=(
            isinstance(validation,dict)
            and requirement is not None
            and str(validation.get("protocol_id") or "") == str(requirement.get("experimental_protocol_id") or "")
            and str(validation.get("episode_key") or "") == f"field:{int(case_id)}"
            and str(validation.get("source") or "").upper() == "FIELD_CASE"
        )
        if normalized_validation:
            wilson_job_id=self.runtime.enqueue_field_validation_wilson(
                int(case_id),validation,dict(result or {})
            )
        if isinstance(result,dict) and result.get("new_protocol_evidence") is True:
            cursor=f"FIELD_ONE_SHOT:{int(case_id)}"
            existing=self.runtime.conn.execute("select wilson_job_id from doctor_v2_wilson_jobs where input_cursor=? limit 1",(cursor,)).fetchone()
            if not existing:
                wilson_job_id=self.runtime.enqueue_wilson("HOURLY_REVIEW",{
                    "knowledge_loop_contract_version":2,
                    "reason":"FIELD_ONE_SHOT_EVIDENCE",
                    "case_id":int(case_id),
                    "patient_id":str(client_id),
                    "case_report":dict(result or {}),
                    "publication_rule":"candidate_then_3_internal_verified_then_publication",
                },cursor)
        await self._finish_assignment("FIELD_SUZIE",assignment)
        return {
            "experimental_validation_recorded":bool(normalized_validation),
            "wilson_job_id":wilson_job_id,
        }

    def sync_field_slots(self)->None:
        rows=list(self.runtime.conn.execute(
            "select slot_id,assignment_id from doctor_v2_role_slots where role='FIELD_SUZIE' and state='BUSY'"
        ))
        for row in rows:
            assignment=str(row["assignment_id"] or "")
            if not assignment.startswith("case:"):continue
            try:case_id=int(assignment.split(":",1)[1])
            except Exception:continue
            case=self.runtime.conn.execute(
                "select state from doctor_cases where case_id=?",(case_id,)
            ).fetchone()
            if not case or str(case["state"]) not in {"ASSIGNED","CLAIMED","TREATING","VERIFYING"}:
                self.runtime.role_release(str(row["slot_id"]),assignment)

    async def state(self,request:web.Request)->web.Response:
        await self.server.require_doctor_operator(request)
        return web.json_response({"ok":True,**self.runtime.snapshot()})

    async def house_get(self,request:web.Request)->web.Response:
        await self.server.require_doctor_operator(request)
        job=self.runtime.house_get(int(request.match_info["job_id"]))
        if not job: raise web.HTTPNotFound(text="House job not found")
        return web.json_response({"ok":True,"job":job})

    async def house_decision(self,request:web.Request)->web.Response:
        body=await self.server.doctor_json_body(request)
        job_id=int(request.match_info["job_id"])
        job=self.runtime.house_get(job_id)
        if not job:
            raise web.HTTPNotFound(text="House job not found")
        if job.get("status")=="DONE":
            row=self.runtime.conn.execute(
                "select * from doctor_v2_house_decisions where house_job_id=?",
                (job_id,),
            ).fetchone()
            if not row:
                raise web.HTTPConflict(text="House job done without decision")
            decided={"decision_id":int(row["decision_id"]),"field_queue":None}
            fq=self.runtime.conn.execute(
                "select * from doctor_v2_field_queue where source_house_decision_id=?",
                (int(row["decision_id"]),),
            ).fetchone()
            if fq: decided["field_queue"]=dict(fq)
            return web.json_response({"ok":True,"idempotent_replay":True,**decided})
        if job.get("status")!="CLAIMED":
            raise web.HTTPConflict(text="House job is not claimed")

        finding_class=str(body.get("finding_class") or "OBSERVATION").upper()
        significance=str(body.get("significance") or "LOW").upper()
        if significance=="NONE":
            significance="LOW"
        decision=str(body.get("decision") or "").upper()
        field_priority=str(body.get("field_priority") or "NORMAL").upper()
        if finding_class not in {"EVENT","OBSERVATION","INCIDENT","CASE"}:
            raise web.HTTPBadRequest(text="invalid finding_class")
        if significance not in {"LOW","MEDIUM","HIGH","CRITICAL"}:
            raise web.HTTPBadRequest(text="invalid significance")
        if decision not in {"OBSERVE","RECHECK_LATER","IGNORE_AS_NOISE","HUMAN_ACTION_REQUIRED","DISPATCH_SUZIE"}:
            raise web.HTTPBadRequest(text="invalid House decision")
        if field_priority not in {"LOW","NORMAL","HIGH","URGENT"}:
            raise web.HTTPBadRequest(text="invalid field_priority")
        if decision=="DISPATCH_SUZIE":
            known=self.runtime.conn.execute(
                "select 1 from clients where client_id=? limit 1",
                (str(job["patient_id"]),),
            ).fetchone()
            if not known:
                raise web.HTTPConflict(text="House cannot dispatch unknown client_id")

        result={
            "finding_class":finding_class,
            "significance":significance,
            "decision":decision,
            "field_priority":field_priority,
            **(dict(body.get("result") or {}) if isinstance(body.get("result"),dict) else {}),
        }
        trigger_event=next(
            (e for e in (job.get("recent_events") or [])
             if int(e.get("event_id") or 0)==int(job.get("trigger_event_id") or 0)),
            {},
        )
        trigger_payload=trigger_event.get("payload") if isinstance(trigger_event,dict) else {}
        if not isinstance(trigger_payload,dict):
            trigger_payload={}
        trigger_evidence=trigger_payload.get("evidence")
        if not isinstance(trigger_evidence,dict):
            trigger_evidence={}
        active_repair=None
        if (
            str(trigger_evidence.get("kind") or "")=="repair"
            and trigger_evidence.get("active") is True
            and trigger_evidence.get("terminal_resolution_required") is True
            and trigger_evidence.get("domain")
            and trigger_evidence.get("issue_id")
        ):
            active_repair={
                "domain":str(trigger_evidence.get("domain")),
                "issue_id":str(trigger_evidence.get("issue_id")),
                "problem_key":str(trigger_evidence.get("problem_key") or ""),
                "ha_severity":str(trigger_evidence.get("ha_severity") or "warning"),
                "is_fixable":bool(trigger_evidence.get("is_fixable")),
                "translation_key":trigger_evidence.get("translation_key"),
                "translation_placeholders":trigger_evidence.get("translation_placeholders") or {},
                "resolution_criterion":trigger_evidence.get("resolution_criterion") or {
                    "type":"ha_repair_absent",
                    "domain":str(trigger_evidence.get("domain")),
                    "issue_id":str(trigger_evidence.get("issue_id")),
                },
            }
            if decision in {"OBSERVE","RECHECK_LATER","IGNORE_AS_NOISE"}:
                raise web.HTTPBadRequest(
                    text="Active Home Assistant Repair requires DISPATCH_SUZIE or HUMAN_ACTION_REQUIRED until verified absent"
                )
            result["active_repair"]=active_repair
            result["resolution_fingerprint"]=str(active_repair.get("problem_key") or "")
        elif decision=="DISPATCH_SUZIE":
            related_candidates=[]
            result_problem_key=str(result.get("problem_key") or "").strip()
            if result_problem_key.startswith("repair:"):
                related_candidates.append({"fingerprint":result_problem_key})
            result_evidence=result.get("evidence") if isinstance(result.get("evidence"),dict) else {}
            rd=str(result_evidence.get("repair_domain") or "").strip()
            ri=str(result_evidence.get("repair_issue_id") or "").strip()
            if rd and ri:
                related_candidates.append({"fingerprint":f"repair:{rd}:{ri}","domain":rd,"issue_id":ri})
            related=trigger_evidence.get("related_findings")
            if isinstance(related,list):
                for item in related:
                    if isinstance(item,dict) and str(item.get("kind") or "")=="repair":
                        related_candidates.append({
                            "fingerprint":str(item.get("problem_key") or "").strip(),
                            "domain":str(item.get("domain") or "").strip(),
                            "issue_id":str(item.get("issue_id") or "").strip(),
                        })
            seen=set()
            for candidate in related_candidates:
                key=(str(candidate.get("fingerprint") or ""),str(candidate.get("domain") or ""),str(candidate.get("issue_id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                context=self.runtime.resolve_active_repair_context(
                    str(job["patient_id"]),
                    candidate.get("fingerprint"),
                    domain=candidate.get("domain"),
                    issue_id=candidate.get("issue_id"),
                )
                if not context:
                    continue
                evidence=dict(context.get("evidence") or {})
                active_repair={
                    "domain":str(evidence.get("domain") or ""),
                    "issue_id":str(evidence.get("issue_id") or ""),
                    "problem_key":str(context.get("fingerprint") or ""),
                    "ha_severity":str(evidence.get("ha_severity") or "warning"),
                    "is_fixable":bool(evidence.get("is_fixable")),
                    "translation_key":evidence.get("translation_key"),
                    "translation_placeholders":evidence.get("translation_placeholders") or {},
                    "repair_identity":evidence.get("repair_identity") or {},
                    "resolution_criterion":evidence.get("resolution_criterion") or {
                        "type":"ha_repair_absent",
                        "domain":str(evidence.get("domain") or ""),
                        "issue_id":str(evidence.get("issue_id") or ""),
                    },
                }
                result["active_repair"]=active_repair
                result["resolution_fingerprint"]=str(context.get("fingerprint") or "")
                break
        if decision=="HUMAN_ACTION_REQUIRED":
            human=result.get("human_requirement") if isinstance(result.get("human_requirement"),dict) else {}
            human_type=str(human.get("type") or "").upper().strip()
            human_reason=str(human.get("reason") or "").strip()
            if human_type not in {"PHYSICAL_ACTION","CREDENTIAL","OAUTH","MISSING_CAPABILITY"} or not human_reason:
                raise web.HTTPBadRequest(text="House HUMAN_ACTION_REQUIRED requires human_requirement.type and reason")
            if human_type=="MISSING_CAPABILITY":
                requested=str(human.get("capability") or "").strip()
                if not requested:
                    raise web.HTTPBadRequest(text="MISSING_CAPABILITY requires exact human_requirement.capability")
                available={str(x) for x in (trigger_evidence.get("field_action_capabilities") or []) if str(x)}
                if requested in available or (requested=="doctor.action.request" and available):
                    raise web.HTTPConflict(text="House MISSING_CAPABILITY conflicts with available Field action capability; re-evaluate")
            result["human_requirement"]={"type":human_type,"reason":human_reason,"capability":str(human.get("capability") or ""),"action":str(human.get("action") or "")}

        candidates=list(job.get("experimental_protocol_candidates") or [])
        directive=str(result.get("house_directive") or "").upper().strip()
        experimental_id=str(result.get("experimental_protocol_id") or "").strip()
        review=result.get("experimental_protocol_candidate_review")
        if (not directive and not experimental_id and decision=="DISPATCH_SUZIE"
                and isinstance(review,dict)):
            assessment=str(review.get("assessment") or "").upper().strip()
            reviewed_id=str(review.get("protocol_id") or "").strip()
            if assessment in {"MATCHING_VALIDATION_TARGET","VALIDATE_FIRST","APPLICABLE_VALIDATION_TARGET"} and reviewed_id:
                directive="VALIDATE_FIRST"
                experimental_id=reviewed_id
                result["house_directive"]="VALIDATE_FIRST"
                result["experimental_protocol_id"]=reviewed_id
                result.setdefault("validation_stage",str(review.get("validation_stage") or ""))
        if directive or experimental_id:
            if decision != "DISPATCH_SUZIE":
                raise web.HTTPBadRequest(text="Experimental validation directive requires DISPATCH_SUZIE")
            if directive != "VALIDATE_FIRST" or not experimental_id:
                raise web.HTTPBadRequest(text="Experimental dispatch requires house_directive=VALIDATE_FIRST and experimental_protocol_id")
            matched=next((x for x in candidates if str(x.get("protocol_id"))==experimental_id),None)
            if not matched:
                raise web.HTTPBadRequest(text="Experimental Protocol is not a matched 0/3-2/3 candidate for this Patient Card")
            result["house_directive"]="VALIDATE_FIRST"
            result["experimental_protocol_id"]=experimental_id
            result["validation_stage"]=str(matched.get("validation_stage") or "0/3")
            result["experimental_candidate"]={
                "protocol_id":experimental_id,
                "disease_id":matched.get("disease_id"),
                "validation_stage":result["validation_stage"],
                "match_reasons":matched.get("match_reasons") or [],
                "candidate":matched.get("candidate") or {},
            }
        elif decision=="DISPATCH_SUZIE" and candidates:
            disposition=str(result.get("experimental_candidate_disposition") or "").upper().strip()
            decline_reason=str(result.get("experimental_decline_reason") or "").strip()
            if disposition not in {"DECLINE_EXPERIMENTAL","NOT_APPLICABLE","NOT_SAFE","NOT_USEFUL"} or not decline_reason:
                raise web.HTTPBadRequest(text="House DISPATCH_SUZIE with matched Experimental candidates requires VALIDATE_FIRST or explicit experimental_candidate_disposition plus reason")
        decided=self.runtime.house_decide(job_id,result)
        self.runtime.mark_resolution_house_decision(job_id,result)
        fq=decided.get("field_queue")
        legacy=None
        if fq:
            previous_attempts=[]
            do_not_repeat=[]
            rows=self.server.command_bridge.conn.execute(
                """select command_id,case_id,arguments_json,status,result_json,execution_state,created_at,package_json
                   from doctor_client_commands where client_id=? and tool_name='doctor.action.request'
                   order by created_at desc limit 20""",(str(job["patient_id"]),)
            ).fetchall()
            for row in rows:
                try: args=json.loads(str(row["arguments_json"] or "{}"))
                except Exception: args={}
                try: command_result=json.loads(str(row["result_json"] or "{}"))
                except Exception: command_result={}
                item={"command_id":str(row["command_id"]),"case_id":int(row["case_id"]),"action":args.get("action") or {},"exact_target":args.get("exact_target") or {},"execution_state":str(row["execution_state"] or ""),"status":str(row["status"] or ""),"created_at":str(row["created_at"] or ""),"result":command_result}
                signed_package=False
                try:
                    signed_package=bool(json.loads(str(row["package_json"] or "{}")))
                except Exception:
                    signed_package=False
                item["signed_package"]=signed_package
                previous_attempts.append(item)
                if signed_package and str(row["execution_state"] or "")=="VERIFIED_FAIL":
                    do_not_repeat.append({"action":item["action"],"exact_target":item["exact_target"],"reason":"previous_verified_fail"})
            problem_key=str((active_repair or {}).get("problem_key") or trigger_evidence.get("problem_key") or trigger_event.get("fingerprint") or "")
            original_criterion=(active_repair or {}).get("resolution_criterion") or trigger_evidence.get("resolution_criterion")
            case_problem={
                "patient_card_version":int(job.get("card_version") or 0),
                "trigger_event_id":int(job.get("trigger_event_id") or 0),
                "problem_key":problem_key,
                "domain":str((active_repair or {}).get("domain") or trigger_evidence.get("domain") or ""),
                "issue_id":str((active_repair or {}).get("issue_id") or trigger_evidence.get("issue_id") or ""),
                "terminal_resolution_required":bool(active_repair),
                "original_functional_criterion":original_criterion,
                "house_job_id":job_id,
                "house_decision_id":decided["decision_id"],
                "house_decision":{"decision":decision,"finding_class":finding_class,"significance":significance,"field_priority":field_priority,"summary":result.get("summary"),"rationale":result.get("rationale")},
                "house_result":result,
                "house_directive":result.get("house_directive"),
                "experimental_protocol_id":result.get("experimental_protocol_id"),
                "validation_stage":result.get("validation_stage"),
                "experimental_candidate":result.get("experimental_candidate"),
                "experimental_protocol_candidates":candidates,
                "active_repair":result.get("active_repair"),
                "field_action_capabilities":list(trigger_evidence.get("field_action_capabilities") or []),
                "previous_attempts":previous_attempts,
                "do_not_repeat":do_not_repeat,
            }
            existing_field=self.runtime.active_field_case_for_house(
                job_id,str(result.get("resolution_fingerprint") or "") or None
            ) if bool(active_repair) else None
            async with self.server.journal_gate:
                if existing_field:
                    case=self.server.journal.merge_house_evidence(
                        int(existing_field["case_id"]),
                        {
                            "house_job_id":job_id,
                            "house_decision_id":decided["decision_id"],
                            "patient_card_version":case_problem["patient_card_version"],
                            "trigger_event_id":case_problem["trigger_event_id"],
                            "summary":result.get("summary"),
                            "house_result":result,
                            "active_repair":case_problem.get("active_repair"),
                            "original_functional_criterion":original_criterion,
                            "field_action_capabilities":case_problem["field_action_capabilities"],
                            "do_not_repeat":do_not_repeat,
                        },
                    )
                    created=False
                else:
                    case,created=self.server.journal.escalate(
                        client_id=str(job["patient_id"]),
                        source_key=f"v2-house-decision:{decided['decision_id']}",
                        source_request_id=None,
                        summary=str(result.get("summary") or f"House dispatched patient {job['patient_id']}"),
                        problem=case_problem,
                        disease_id=str(result.get("disease_id") or "") or None,
                        priority=int(fq.get("priority") or 50),
                        actor="doctor_house",
                    )
            self.runtime.set_field_legacy_case(int(fq["queue_id"]),int(case["case_id"]))
            legacy={"case_id":case["case_id"],"case_ref":case["case_ref"],"created":created,"deduplicated":bool(existing_field)}
        await self._finish_assignment("HOUSE",f"house:{job_id}")
        return web.json_response({"ok":True,**decided,"legacy_field_case":legacy})

    async def wilson_get(self,request:web.Request)->web.Response:
        await self.server.require_doctor_operator(request)
        job=self.runtime.wilson_get(int(request.match_info["job_id"]))
        if not job: raise web.HTTPNotFound(text="Wilson job not found")
        return web.json_response({"ok":True,"job":job})

    async def wilson_complete(self,request:web.Request)->web.Response:
        body=await self.server.doctor_json_body(request)
        job_id=int(request.match_info["job_id"])
        existing=self.runtime.wilson_get(job_id)
        if not existing:
            raise web.HTTPNotFound(text="Wilson job not found")
        if existing.get("status") in {"DONE","FAILED"}:
            return web.json_response({
                "ok":True,
                "idempotent_replay":True,
                "wilson_job_id":job_id,
                "status":existing.get("status"),
                "output_cursor":existing.get("output_cursor"),
            })
        result=dict(body.get("result") or {}) if isinstance(body.get("result"),dict) else {}
        out=str(body.get("output_cursor") or "") or None
        if not out and existing.get("mode")=="HOURLY_REVIEW":
            batch=existing.get("batch") or {}
            if batch.get("cursor_to") is not None:
                out=str(batch["cursor_to"])
        done=self.runtime.wilson_complete(job_id,result,out,failed=bool(body.get("failed",False)))
        await self._finish_assignment("WILSON",f"wilson:{job_id}")
        return web.json_response({"ok":True,**done})

    async def _finish_assignment(self,role:str,assignment_id:str)->None:
        d=self.runtime.current_dialog(role,assignment_id)
        if d and d.get("state")=="OPEN":
            row=self.runtime.conn.execute("select ordinal from doctor_v2_web_sessions where dialog_id=? and ended_at is null order by ordinal desc limit 1",(d["dialog_id"],)).fetchone()
            if row:
                try:self.runtime.dialog_end(str(d["dialog_id"]),int(row[0]),"NATURAL")
                except Exception:pass
            self._spawn(self.server._close_web_dialog_later(str(d["dialog_id"])),"v2_close_"+assignment_id.replace(":","_"))
        row=self.runtime.conn.execute("select slot_id from doctor_v2_role_slots where role=? and assignment_id=? and state='BUSY'",(role,assignment_id)).fetchone()
        if row:self.runtime.role_release(str(row[0]),assignment_id)

    async def _dispatch_new(self,role:str,job_id:int,generation:int=1,already_claimed:bool=False)->None:
        assignment=(f"case:{job_id}" if role=="FIELD_SUZIE" else f"{role.lower()}:{job_id}")
        previous=self.runtime.current_dialog(role,assignment)
        if previous and generation <= int(previous.get("generation") or 0):
            generation=int(previous.get("generation") or 0)+1
        slot=None
        if not already_claimed:
            slot=self.runtime.role_acquire(role,assignment)
            if not slot:return
        try:
            target=self._target(role)
            if role=="HOUSE":
                compat_id=8_000_000_000+job_id
                text=(
                    f"HOUSE JOB #{job_id}. Compatibility transport: call doctor.case.get "
                    f"case_id={compat_id}. Analyze returned House job. Finish exactly once "
                    f"with doctor.case.complete_next doctor_handle=HOUSE:{job_id}; put "
                    "finding_class, significance, decision, field_priority in result. "
                    "You MUST review experimental_protocol_candidates in the returned House job. "
                    "A matching 0/3-2/3 candidate never forces dispatch by itself. If the Patient Card "
                    "already merits Field investigation and one candidate has reasonable real-world "
                    "validation grounds, DISPATCH_SUZIE with experimental_protocol_id, its validation_stage, "
                    "and house_directive=VALIDATE_FIRST. Otherwise decide normally. "
                    "If the trigger evidence is an active Home Assistant Repair with "
                    "terminal_resolution_required=true, it is an unresolved Doctor task: "
                    "do NOT OBSERVE, RECHECK_LATER or IGNORE_AS_NOISE. Either DISPATCH_SUZIE "
                    "for real resolution or HUMAN_ACTION_REQUIRED only for a genuine physical/credential/OAuth/missing-capability step. HUMAN_ACTION_REQUIRED MUST include result.human_requirement with type PHYSICAL_ACTION, CREDENTIAL, OAUTH or MISSING_CAPABILITY and a concrete reason; absence of Disease/Protocol is never a human reason."
                )
            elif role=="WILSON":
                compat_id=9_000_000_000+job_id
                text=(
                    f"WILSON JOB #{job_id}. Compatibility transport: call doctor.case.get "
                    f"case_id={compat_id}. Perform returned Wilson job. Finish exactly once "
                    f"with doctor.case.complete_next doctor_handle=WILSON:{job_id}, "
                    "outcome SUCCESS or FAILED, structured result. If batch contains "
                    "required_validations, return each in result.validations with the exact "
                    "protocol_id, episode_key, success and verified facts. Analyze positive "
                    "and negative evidence; do not rewrite Field facts. Your primary job is to "
                    "manufacture reusable treatment knowledge, not summarize news. For NIGHTLY_RESEARCH, "
                    "mine real technical incidents from issues/discussions/forums/docs, identify treatments "
                    "that actually restored function, and return search_coverage plus incident_reviews. "
                    "Every relevant successful treatment must become a structured EXPERIMENTAL protocol_candidate "
                    "0/3 or be explicitly REJECTED with a concrete safety/applicability/evidence reason. "
                    "Each candidate must include protocol_id, disease_id, embedded disease draft with diagnostic_criteria, "
                    "symptoms/checks/action/verify/risk/automation_class and external_evidence URLs. External evidence "
                    "never earns validation credit. For internal new_protocol_evidence, create/update a candidate or "
                    "explicitly reject with reason. 3/3 goes to publication review, never directly ACTIVE."
                )
            else:
                text=(
                    f"CONTINUE CASE #{job_id}. This is the same unfinished Field Case in a new "
                    "10x10 dialog generation. Read doctor.case.get, then call doctor.case.claim "
                    "for this case; the canonical MCP will resume the existing claim when valid. "
                    "Continue from server-backed Case state and do not repeat completed actions."
                )
            job=await self.server._call_lab_run(method="cdp",text=text,target=target)
            jid=str(job.get("job_id") or "")
            if not jid:raise RuntimeError("Call Lab returned no job_id")
            deadline=time.monotonic()+100
            tab_id=""
            while time.monotonic()<deadline:
                cur=await self.server._call_lab_job(jid)
                if cur:
                    state=str(cur.get("state") or "")
                    if state=="failed":raise RuntimeError(f"Call Lab dispatch failed: {cur.get('detail')}")
                    if state=="submitted":
                        tab_id=str((cur.get("detail") or {}).get("tab_id") or "")
                        if tab_id:break
                await asyncio.sleep(.5)
            if not tab_id:raise TimeoutError("v2 web dispatch timeout")
            final=await self.server._wait_final_dialog(tab_id)
            if not final:raise TimeoutError("v2 final dialog id timeout")
            dialog_id,_url=final
            if role=="FIELD_SUZIE":
                self.bind_field_dialog(job_id,dialog_id)
            else:
                self.runtime.dialog_open(dialog_id,role,assignment,self._project_id(role),generation)
            if role=="HOUSE":
                if already_claimed:
                    with self.runtime.conn:self.runtime.conn.execute("update doctor_v2_house_jobs set claimed_dialog_id=? where house_job_id=?",(dialog_id,job_id))
                else:
                    claimed=self.runtime.claim_house(job_id,dialog_id)
                    if not claimed:raise RuntimeError("House claim lost")
            elif role=="WILSON":
                if not already_claimed:
                    claimed=self.runtime.claim_wilson(job_id)
                    if not claimed:raise RuntimeError("Wilson claim lost")
            self.server.db.event("doctor_v2_web_dispatched",None,{"role":role,"job_id":job_id,"dialog_id":dialog_id,"generation":generation})
        except Exception as exc:
            self.server.db.event("doctor_v2_dispatch_error",None,{"role":role,"job_id":job_id,"error":f"{type(exc).__name__}: {exc}"})
            if not already_claimed:
                if role=="HOUSE":
                    with self.runtime.conn:self.runtime.conn.execute("update doctor_v2_house_jobs set status='WAITING',claimed_dialog_id=NULL,claimed_at=NULL where house_job_id=? and status='CLAIMED'",(job_id,))
                else:
                    with self.runtime.conn:self.runtime.conn.execute("update doctor_v2_wilson_jobs set status='WAITING' where wilson_job_id=? and status='CLAIMED'",(job_id,))
                if slot:self.runtime.role_release(slot,assignment)

    async def _continue_dialog(self,item:dict[str,Any])->None:
        role=str(item["role"]); assignment=str(item["assignment_id"]); dialog_id=str(item["dialog_id"]); ordinal=int(item["ordinal"])
        try:
            result=self.runtime.dialog_end(dialog_id,ordinal,"WATCHDOG_10M")
        except Exception:return
        if role=="HOUSE":
            try:
                job_id=int(assignment.split(":",1)[1])
            except Exception:
                job_id=0
            preempt=(self.runtime.preempt_house_for_higher_priority(job_id,dialog_id) if job_id else None)
            if preempt:
                self.server.db.event("doctor_v2_house_priority_preempted",None,preempt)
                self._spawn(self.server._close_web_dialog_later(dialog_id),f"v2_close_preempted_house_{job_id}")
                return
            quantum=(self.runtime.house_quantum_exhausted(job_id,dialog_id,max_sessions=2) if job_id else None)
            if quantum:
                self.server.db.event("doctor_v2_house_quantum_yielded",None,quantum)
                self._spawn(self.server._close_web_dialog_later(dialog_id),f"v2_close_quantum_house_{job_id}")
                return
        if result.get("continue_same_dialog"):
            nxt=self.runtime.open_session(dialog_id,{"continuation":True,"assignment_id":assignment})
            url=self._project_url(role).rstrip("/")+"/c/"+dialog_id
            job_id=int(assignment.split(":",1)[1])
            if role=="HOUSE":
                compat_id=8_000_000_000+job_id
                text=f"CONTINUE HOUSE {assignment}. Re-read doctor.case.get case_id={compat_id}; do not repeat completed work. Finish via doctor.case.complete_next doctor_handle=HOUSE:{job_id}."
            elif role=="WILSON":
                compat_id=9_000_000_000+job_id
                text=f"CONTINUE WILSON {assignment}. Re-read doctor.case.get case_id={compat_id}; do not repeat completed work. Finish via doctor.case.complete_next doctor_handle=WILSON:{job_id}."
            else:
                text=f"CONTINUE CASE #{job_id}. Same Field Case and dialog; use the existing doctor_handle if present, otherwise call doctor.case.claim case_id={job_id} to resume it. Do not repeat completed actions."
            try:
                await self.server._call_lab_run(method="cdp",text=text,target="dialog",target_url=url)
                self.server.db.event("doctor_v2_session_continued",None,{"role":role,"assignment_id":assignment,"dialog_id":dialog_id,"ordinal":nxt["ordinal"]})
            except Exception as exc:
                self.server.db.event("doctor_v2_continue_error",None,{"role":role,"assignment_id":assignment,"error":f"{type(exc).__name__}: {exc}"})
            return
        if result.get("rotate") and role in {"HOUSE","WILSON","FIELD_SUZIE"}:
            try:job_id=int(assignment.split(":",1)[1])
            except Exception:return
            old=self.runtime.current_dialog(role,assignment) or {}
            gen=int(old.get("generation") or 1)+1
            self._spawn(self._dispatch_new(role,job_id,generation=gen,already_claimed=True),f"v2_rotate_{role}_{job_id}_{gen}")

    def _nightly_due(self)->tuple[bool,str]:
        now=datetime.now(ZoneInfo("Europe/Kyiv"))
        date=now.date().isoformat()
        hour=int(self.server.config.get("wilson_nightly_hour_local",5))
        return now.hour>=hour,date

    def _schedule_wilson(self)->None:
        now_m=time.monotonic()
        if now_m>=self.next_hourly:
            self.next_hourly=now_m+3600
            if not self.runtime.has_open_wilson_mode("HOURLY_REVIEW"):
                batch=self.runtime.build_hourly_wilson_batch()
                if batch:self.runtime.enqueue_wilson("HOURLY_REVIEW",batch,str(batch["cursor_from"]))
        due,date=self._nightly_due()
        if due:
            exists=self.runtime.conn.execute("select 1 from doctor_v2_wilson_jobs where mode='NIGHTLY_RESEARCH' and input_cursor=? limit 1",(date,)).fetchone()
            if not exists:
                self.runtime.enqueue_wilson("NIGHTLY_RESEARCH",{
                    "knowledge_loop_contract_version":2,
                    "research_date":date,
                    "purpose":"TREATMENT_INCIDENT_MINING",
                    "scope":"external technical incidents/issues/discussions/forums with observed treatment outcomes; not general news",
                    "required_output":"search_coverage + incident_reviews + protocol_candidates",
                    "external_evidence_internal_confirmations":0,
                },date)

    async def run(self)->None:
        while True:
            try:
                if not bool(self.server.config.get("doctor_v2_dispatch_enabled", False)):
                    await asyncio.sleep(2)
                    continue
                recovered=self.runtime.recover_stranded_house_wilson()
                if recovered:
                    self.server.db.event("doctor_v2_stranded_assignment_recovered",None,{"items":recovered})
                overbudget=self.runtime.recover_overbudget_house(max_sessions=2)
                if overbudget:
                    self.server.db.event("doctor_v2_house_overbudget_recovered",None,overbudget)
                    dialog_id=str((self.runtime.current_dialog("HOUSE",f"house:{overbudget['job_id']}") or {}).get("dialog_id") or "")
                    if dialog_id:
                        self._spawn(self.server._close_web_dialog_later(dialog_id),f"v2_close_overbudget_house_{overbudget['job_id']}")
                self._schedule_wilson()
                self.sync_field_slots()
                for item in self.runtime.expired_sessions():
                    if item["role"] in {"HOUSE","WILSON","FIELD_SUZIE"}:self._spawn(self._continue_dialog(item),f"v2_watchdog_{item['dialog_id']}_{item['ordinal']}")
                h=self.runtime.next_house_waiting()
                if h and not self.runtime.conn.execute("select 1 from doctor_v2_role_slots where role='HOUSE' and state='BUSY'").fetchone():
                    self._spawn(self._dispatch_new("HOUSE",int(h["house_job_id"])),f"v2_house_{h['house_job_id']}")
                w=self.runtime.next_wilson_waiting()
                if w and not self.runtime.conn.execute("select 1 from doctor_v2_role_slots where role='WILSON' and state='BUSY'").fetchone():
                    self._spawn(self._dispatch_new("WILSON",int(w["wilson_job_id"])),f"v2_wilson_{w['wilson_job_id']}")
                await asyncio.sleep(1)
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.server.db.event("doctor_v2_loop_error",None,{"error":f"{type(exc).__name__}: {exc}"})
                await asyncio.sleep(2)

    async def close(self)->None:
        for t in list(self.tasks):t.cancel()
        if self.tasks:await asyncio.gather(*list(self.tasks),return_exceptions=True)
        self.runtime.store.close()
