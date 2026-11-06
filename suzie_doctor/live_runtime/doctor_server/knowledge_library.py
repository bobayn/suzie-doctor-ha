from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

KNOWLEDGE_TYPES = {
    "DISEASE_KNOWLEDGE", "ACTIVE_PROTOCOL", "EXPERIMENTAL_PROTOCOL",
    "MANUAL_TREATMENT", "REFERENCE_ONLY", "NEGATIVE_EVIDENCE",
    "UNRESOLVED_HYPOTHESIS", "EXTERNAL_EVIDENCE",
}

MANUAL_REJECTION_CLASSES = {
    "HA_CONFIG_EDIT_REQUIRED", "STORAGE_OR_DATABASE_RECOVERY",
    "PARTIAL_MULTI_STEP", "NETWORK_OR_DNS_CONFIG",
    "EXTERNAL_HOST_OR_CONTAINER_CONFIG", "PRODUCT_CONFIG_ADAPTER_REQUIRED",
    "NO_SAFE_DETERMINISTIC_MAPPING", "CREDENTIAL_OR_AUTH_FLOW",
    "EXACT_TARGET_CONTEXT_REQUIRED", "HIGH_RISK_MANUAL_RECOVERY",
    "HARDWARE_OR_RF_PHYSICAL", "SECURITY_OR_RECOMMISSION_MANUAL",
}

GROUPS = {
    "DISEASE_KNOWLEDGE": "matching_diseases",
    "ACTIVE_PROTOCOL": "active_protocols",
    "EXPERIMENTAL_PROTOCOL": "experimental_protocols",
    "MANUAL_TREATMENT": "manual_treatments",
    "REFERENCE_ONLY": "reference_knowledge",
    "NEGATIVE_EVIDENCE": "negative_evidence",
    "UNRESOLVED_HYPOTHESIS": "unresolved_hypotheses",
    "EXTERNAL_EVIDENCE": "external_evidence",
}


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _strings(value: Any):
    if value is None:
        return
    if isinstance(value, str):
        if value.strip():
            yield value.strip()
        return
    if isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _strings(item)
        return
    yield str(value)


def _tokens(value: Any) -> set[str]:
    text = " ".join(_strings(value)).lower()
    return {x for x in re.findall(r"[\w./:+-]{2,}", text, flags=re.UNICODE) if len(x) > 1}


def _urls(value: Any) -> list[str]:
    out = []
    for s in _strings(value):
        if s.startswith(("https://", "http://")) and s not in out:
            out.append(s)
    return out[:20]


def _hash(*parts: Any) -> str:
    raw = "|".join(str(x or "") for x in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _bounded(value: Any, depth: int = 0) -> Any:
    if depth >= 4:
        return str(value)[:500]
    if isinstance(value, str):
        return value[:1200]
    if isinstance(value, list):
        return [_bounded(x, depth + 1) for x in value[:8]]
    if isinstance(value, tuple):
        return [_bounded(x, depth + 1) for x in list(value)[:8]]
    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= 20:
                break
            out[str(k)[:120]] = _bounded(v, depth + 1)
        return out
    return value


class DoctorKnowledgeLibrary:
    """Derived, rebuildable retrieval index over canonical Doctor knowledge sources."""

    def __init__(self, conn, knowledge_root: str | Path) -> None:
        self.conn = conn
        self.root = Path(knowledge_root)
        self.last_rebuild: dict[str, Any] = {}

    def _put(self, item: dict[str, Any]) -> None:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
        search_material = {
            "title": item.get("title"), "disease_id": item.get("disease_id"),
            "component": item.get("component"), "payload": payload,
            "positive": item.get("positive_evidence"), "negative": item.get("negative_evidence"),
            "versions": item.get("version_constraints"), "environment": item.get("environment_constraints"),
        }
        self.conn.execute(
            """INSERT OR REPLACE INTO doctor_knowledge_items(
                item_id,knowledge_type,title,disease_id,component,applicability_json,confidence,
                supporting_cases_json,external_sources_json,positive_evidence_json,negative_evidence_json,
                version_constraints_json,environment_constraints_json,treatment_status,safety_json,
                provenance_json,payload_json,search_text,source_kind,source_ref,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (
                str(item["item_id"]), str(item["knowledge_type"]), str(item.get("title") or "")[:500],
                str(item.get("disease_id") or "") or None, str(item.get("component") or "")[:120] or None,
                _j(item.get("applicability") or {}),
                float(item["confidence"]) if isinstance(item.get("confidence"), (int, float)) else None,
                _j(item.get("supporting_cases") or []), _j(item.get("external_sources") or []),
                _j(item.get("positive_evidence") or []), _j(item.get("negative_evidence") or []),
                _j(item.get("version_constraints") or []), _j(item.get("environment_constraints") or []),
                str(item.get("treatment_status") or "")[:120] or None, _j(item.get("safety") or {}),
                _j(provenance), _j(payload), " ".join(sorted(_tokens(search_material)))[:30000],
                str(item.get("source_kind") or "UNKNOWN")[:80], str(item.get("source_ref") or "")[:300],
            ),
        )

    def classify_legacy_candidates(self) -> dict[str, int]:
        audit_path = self.root / "v2_candidate_audit_latest.json"
        audit = _load(audit_path, {})
        by_disease: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for d in audit.get("dispositions") or []:
            if isinstance(d, dict) and d.get("disease_id"):
                by_disease[str(d["disease_id"])].append(d)
        counts = Counter()
        rows = self.conn.execute(
            "SELECT protocol_id,disease_id FROM doctor_v2_protocol_candidates WHERE origin='LEGACY'"
        ).fetchall()
        for row in rows:
            pid, did = str(row["protocol_id"]), str(row["disease_id"] or "")
            ds = by_disease.get(did, [])
            kinds = {str(x.get("disposition") or "") for x in ds}
            rejected = [x for x in ds if str(x.get("disposition") or "") == "REJECTED"]
            if rejected and not ({"DUPLICATE_EXISTING_V2", "ALREADY_ACTIVE"} & kinds):
                r = rejected[0]
                klass = str(r.get("reason_class") or "UNCLASSIFIED_REJECTION")
                ktype = "MANUAL_TREATMENT" if klass in MANUAL_REJECTION_CLASSES else "REFERENCE_ONLY"
                eligibility = "NOT_ELIGIBLE_FOR_V2_EXECUTION"
                reason = str(r.get("reason") or "Legacy candidate is not eligible for V2 execution.")
                counts[ktype] += 1
            else:
                klass = "DUPLICATE_EXISTING_V2" if "DUPLICATE_EXISTING_V2" in kinds else "VALID_LEGACY"
                ktype = "EXPERIMENTAL_PROTOCOL"
                eligibility = "EXPERIMENTAL"
                reason = "Retained as V2 Experimental knowledge."
                counts["EXPERIMENTAL"] += 1
            self.conn.execute(
                """INSERT INTO doctor_v2_protocol_candidate_classification(
                    protocol_id,eligibility,knowledge_type,reason_class,reason,source_audit,updated_at
                ) VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(protocol_id) DO UPDATE SET eligibility=excluded.eligibility,
                    knowledge_type=excluded.knowledge_type,reason_class=excluded.reason_class,
                    reason=excluded.reason,source_audit=excluded.source_audit,updated_at=CURRENT_TIMESTAMP""",
                (pid, eligibility, ktype, klass, reason, audit_path.name),
            )
        return dict(counts)

    def rebuild(self) -> dict[str, Any]:
        normalized = _load(self.root / "normalized_knowledge.json", {})
        generated = _load(self.root / "generated_protocols.json", {})
        master = _load(self.root / "forum_knowledge_base.json", {})
        with self.conn:
            classification = self.classify_legacy_candidates()
            self.conn.execute("DELETE FROM doctor_knowledge_items")

            for d in normalized.get("diseases") or []:
                if not isinstance(d, dict) or not d.get("disease_id"):
                    continue
                did = str(d["disease_id"])
                self._put({
                    "item_id": f"disease:{did}", "knowledge_type": "DISEASE_KNOWLEDGE",
                    "title": d.get("title") or did, "disease_id": did, "component": d.get("component"),
                    "confidence": d.get("confidence"), "positive_evidence": d.get("evidence") or [],
                    "payload": d, "source_kind": "NORMALIZED_KNOWLEDGE", "source_ref": did,
                    "provenance": {"file": "normalized_knowledge.json", "incident_refs": d.get("incident_refs") or [],
                                   "recipe_refs": d.get("recipe_refs") or [], "pattern_refs": d.get("pattern_refs") or []},
                })

            for p in generated.get("protocols") or []:
                if not isinstance(p, dict):
                    continue
                proto = p.get("protocol") if isinstance(p.get("protocol"), dict) else {}
                factory = p.get("factory") if isinstance(p.get("factory"), dict) else {}
                status = str(proto.get("status") or "").upper()
                if status == "ACTIVE": ktype = "ACTIVE_PROTOCOL"
                elif status == "MANUAL": ktype = "MANUAL_TREATMENT"
                else: ktype = "REFERENCE_ONLY"
                pid = str(proto.get("id") or factory.get("source_candidate_id") or _hash(p.get("disease_id"), p.get("title")))
                negative = []
                if factory.get("blocker_class") or factory.get("blocker_detail"):
                    negative.append({"blocker_class": factory.get("blocker_class"), "detail": factory.get("blocker_detail")})
                self._put({
                    "item_id": f"factory:{pid}", "knowledge_type": ktype, "title": p.get("title") or pid,
                    "disease_id": p.get("disease_id"), "component": p.get("component"),
                    "treatment_status": status, "positive_evidence": p.get("source_evidence") or [],
                    "negative_evidence": negative, "safety": {"automation_class": p.get("automation_class"), "severity": p.get("severity")},
                    "payload": p, "source_kind": "OLD_FACTORY", "source_ref": pid,
                    "provenance": {"file": "generated_protocols.json", "source_candidate_id": factory.get("source_candidate_id")},
                })

            rows = self.conn.execute(
                """SELECT c.*,x.eligibility,x.knowledge_type AS classified_type,x.reason_class,x.reason
                   FROM doctor_v2_protocol_candidates c
                   LEFT JOIN doctor_v2_protocol_candidate_classification x ON x.protocol_id=c.protocol_id"""
            ).fetchall()
            for row in rows:
                raw = dict(row)
                try: cand = json.loads(str(raw.get("candidate_json") or "{}"))
                except Exception: cand = {}
                ktype = str(raw.get("classified_type") or "EXPERIMENTAL_PROTOCOL")
                if ktype not in {"EXPERIMENTAL_PROTOCOL","MANUAL_TREATMENT","REFERENCE_ONLY"}:
                    ktype = "REFERENCE_ONLY"
                status = "NOT_ELIGIBLE_FOR_V2_EXECUTION" if raw.get("eligibility") == "NOT_ELIGIBLE_FOR_V2_EXECUTION" else str(raw.get("state") or "")
                neg = []
                if raw.get("reason_class"):
                    neg.append({"reason_class": raw.get("reason_class"), "reason": raw.get("reason")})
                self._put({
                    "item_id": f"v2:{raw['protocol_id']}", "knowledge_type": ktype,
                    "title": cand.get("title") or raw["protocol_id"], "disease_id": raw.get("disease_id") or cand.get("disease_id"),
                    "component": cand.get("component"), "treatment_status": status,
                    "external_sources": _urls(cand.get("external_evidence") or cand.get("evidence")),
                    "positive_evidence": cand.get("positive_evidence") or cand.get("evidence") or [],
                    "negative_evidence": (cand.get("negative_evidence") or []) + neg,
                    "safety": {"risk": cand.get("risk"), "automation_class": cand.get("automation_class"), "execution_mapping": cand.get("execution_mapping")},
                    "payload": cand, "source_kind": f"V2_{raw.get('origin')}", "source_ref": raw["protocol_id"],
                    "provenance": {"protocol_id": raw["protocol_id"], "origin": raw.get("origin"), "state": raw.get("state")},
                })

            for inc in master.get("incidents") or []:
                if not isinstance(inc, dict) or not inc.get("id"):
                    continue
                iid = str(inc["id"])
                self._put({
                    "item_id": f"incident:{iid}", "knowledge_type": "EXTERNAL_EVIDENCE",
                    "title": inc.get("title") or iid, "component": inc.get("scope"), "confidence": inc.get("confidence"),
                    "external_sources": _urls([inc.get("source"), inc.get("evidence"), inc.get("notes")]),
                    "positive_evidence": [inc.get("fix"), inc.get("verify")] if inc.get("fix") else [],
                    "negative_evidence": inc.get("failed_attempts") or [], "payload": inc,
                    "source_kind": "MASTER_INCIDENT", "source_ref": iid,
                    "provenance": {"file": "forum_knowledge_base.json", "incident_id": iid, "source": inc.get("source")},
                })
                failed = inc.get("failed_attempts") or []
                if failed:
                    self._put({
                        "item_id": f"incident-neg:{iid}", "knowledge_type": "NEGATIVE_EVIDENCE",
                        "title": f"Failed attempts: {inc.get('title') or iid}", "component": inc.get("scope"),
                        "negative_evidence": failed, "external_sources": _urls(inc.get("source")), "payload": inc,
                        "source_kind": "MASTER_INCIDENT", "source_ref": iid,
                        "provenance": {"file": "forum_knowledge_base.json", "incident_id": iid},
                    })
                hypotheses = inc.get("hypotheses") or []
                if hypotheses and str(inc.get("status") or "").upper() not in {"RESOLVED","FIXED","CLOSED"}:
                    self._put({
                        "item_id": f"incident-hyp:{iid}", "knowledge_type": "UNRESOLVED_HYPOTHESIS",
                        "title": f"Unresolved hypotheses: {inc.get('title') or iid}", "component": inc.get("scope"),
                        "payload": {"hypotheses": hypotheses, "evidence": inc.get("evidence")},
                        "external_sources": _urls(inc.get("source")), "source_kind": "MASTER_INCIDENT", "source_ref": iid,
                        "provenance": {"file": "forum_knowledge_base.json", "incident_id": iid},
                    })

            for rule in master.get("dont_do") or []:
                if not isinstance(rule, dict): continue
                rid = str(rule.get("id") or _hash(rule.get("rule")))
                self._put({
                    "item_id": f"dontdo:{rid}", "knowledge_type": "NEGATIVE_EVIDENCE",
                    "title": f"Do not repeat: {str(rule.get('rule') or '')[:220]}",
                    "negative_evidence": [rule.get("rule")], "payload": rule,
                    "source_kind": "MASTER_DONT_DO", "source_ref": rid,
                    "provenance": {"file": "forum_knowledge_base.json", "rule_id": rid},
                })

            for row in self.conn.execute("SELECT case_id,client_id,disease_id,outcome,result_json FROM doctor_cases WHERE outcome IS NOT NULL"):
                try: result = json.loads(str(row["result_json"] or "{}"))
                except Exception: result = {}
                positive = result.get("successful_actions") or result.get("verified_facts") or []
                negative = result.get("failed_actions") or result.get("do_not_repeat") or []
                if positive:
                    self._put({
                        "item_id": f"case-pos:{row['case_id']}", "knowledge_type": "REFERENCE_ONLY",
                        "title": f"Verified Field Case #{row['case_id']} outcome", "disease_id": row["disease_id"],
                        "supporting_cases": [int(row["case_id"])], "positive_evidence": positive, "payload": result,
                        "source_kind": "FIELD_CASE", "source_ref": str(row["case_id"]),
                        "provenance": {"case_id": int(row["case_id"]), "outcome": row["outcome"]},
                    })
                if negative:
                    self._put({
                        "item_id": f"case-neg:{row['case_id']}", "knowledge_type": "NEGATIVE_EVIDENCE",
                        "title": f"Field Case #{row['case_id']} failed/do-not-repeat evidence", "disease_id": row["disease_id"],
                        "supporting_cases": [int(row["case_id"])], "negative_evidence": negative, "payload": result,
                        "source_kind": "FIELD_CASE", "source_ref": str(row["case_id"]),
                        "provenance": {"case_id": int(row["case_id"]), "outcome": row["outcome"]},
                    })

            for row in self.conn.execute("SELECT * FROM doctor_v2_protocol_validation_episodes"):
                ev = json.loads(str(row["evidence_json"] or "{}")) if row["evidence_json"] else {}
                success = bool(row["success"]) and bool(row["verified"])
                self._put({
                    "item_id": f"validation:{row['validation_id']}",
                    "knowledge_type": "REFERENCE_ONLY" if success else "NEGATIVE_EVIDENCE",
                    "title": f"Protocol validation {row['protocol_id']} / {row['episode_key']}",
                    "supporting_cases": [row["field_case_id"]] if row["field_case_id"] else [],
                    "positive_evidence": [ev] if success else [], "negative_evidence": [] if success else [ev],
                    "payload": ev, "source_kind": "VALIDATION_EPISODE", "source_ref": str(row["validation_id"]),
                    "provenance": {"protocol_id": row["protocol_id"], "episode_key": row["episode_key"], "verified": bool(row["verified"])},
                })

            for row in self.conn.execute("SELECT * FROM doctor_wilson_knowledge_updates ORDER BY wilson_job_id,update_key"):
                try: payload = json.loads(str(row["payload_json"] or "{}"))
                except Exception: payload = {}
                try: provenance = json.loads(str(row["provenance_json"] or "{}"))
                except Exception: provenance = {}
                self._put({
                    "item_id": f"wilson:{row['update_key']}", "knowledge_type": row["knowledge_type"],
                    "title": row["title"], "disease_id": row["disease_id"], "component": row["component"],
                    "external_sources": _urls(payload), "positive_evidence": payload.get("positive_evidence") or [],
                    "negative_evidence": payload.get("negative_evidence") or [], "payload": payload,
                    "source_kind": "WILSON_SCIENCE", "source_ref": row["update_key"],
                    "provenance": {**provenance, "wilson_job_id": int(row["wilson_job_id"]), "source_scope": row["source_scope"]},
                })

        stats = dict(self.conn.execute("SELECT knowledge_type,COUNT(*) FROM doctor_knowledge_items GROUP BY knowledge_type"))
        v2_rows = [[r[0], r[1], int(r[2])] for r in self.conn.execute(
            "SELECT origin,state,COUNT(*) FROM doctor_v2_protocol_candidates GROUP BY origin,state ORDER BY origin,state"
        )]
        v2_states = dict(self.conn.execute("SELECT state,COUNT(*) FROM doctor_v2_protocol_candidates GROUP BY state"))
        eligible_states = dict(self.conn.execute("""SELECT c.state,COUNT(*)
            FROM doctor_v2_protocol_candidates c
            LEFT JOIN doctor_v2_protocol_candidate_classification x ON x.protocol_id=c.protocol_id
            WHERE COALESCE(x.eligibility,'EXPERIMENTAL')='EXPERIMENTAL'
            GROUP BY c.state"""))
        old_status = dict((generated.get("stats") or {}).get("protocol_status") or {})
        self.last_rebuild = {
            "ok": True,
            "items": sum(stats.values()),
            "by_type": stats,
            "legacy_classification": classification,
            "old_factory_total": int((generated.get("stats") or {}).get("protocols") or len(generated.get("protocols") or [])),
            "old_factory_protocol_status": old_status,
            "v2_protocol_candidates_total": sum(int(x[1]) for x in v2_states.items()),
            "v2_by_origin_state": v2_rows,
            "reference_only_legacy": int(classification.get("MANUAL_TREATMENT", 0)),
            "valid_legacy_experimental": int(classification.get("EXPERIMENTAL", 0)),
            "validation_stages": {
                "0/3": int(eligible_states.get("CANDIDATE", 0)) + int(eligible_states.get("FIELD_TESTING", 0)),
                "1/3": int(eligible_states.get("VALIDATED_1_3", 0)),
                "2/3": int(eligible_states.get("VALIDATED_2_3", 0)),
                "3/3": int(eligible_states.get("VALIDATED_3_3", 0)),
            },
        }
        return dict(self.last_rebuild)

    def persist_wilson_updates(self, wilson_job_id: int, updates: list[dict[str, Any]]) -> int:
        count = 0
        for u in updates:
            if not isinstance(u, dict):
                continue
            key = str(u.get("update_key") or "").strip()
            ktype = str(u.get("knowledge_type") or "").upper().strip()
            title = str(u.get("title") or "").strip()
            scope = str(u.get("source_scope") or "").upper().strip()
            if not key or ktype not in KNOWLEDGE_TYPES - {"ACTIVE_PROTOCOL"} or not title or scope not in {"EXTERNAL","INTERNAL"}:
                raise RuntimeError("Wilson library_update requires update_key, supported knowledge_type, title and source_scope")
            payload = u.get("payload") if isinstance(u.get("payload"), dict) else {}
            provenance = u.get("provenance") if isinstance(u.get("provenance"), dict) else {}
            self.conn.execute(
                """INSERT INTO doctor_wilson_knowledge_updates(
                    wilson_job_id,update_key,knowledge_type,title,disease_id,component,source_scope,payload_json,provenance_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(wilson_job_id,update_key) DO UPDATE SET knowledge_type=excluded.knowledge_type,
                    title=excluded.title,disease_id=excluded.disease_id,component=excluded.component,
                    source_scope=excluded.source_scope,payload_json=excluded.payload_json,provenance_json=excluded.provenance_json""",
                (int(wilson_job_id), key, ktype, title, str(u.get("disease_id") or "") or None,
                 str(u.get("component") or "") or None, scope, _j(payload), _j(provenance)),
            )
            count += 1
        return count

    def search(self, context: dict[str, Any], limit_per_type: int = 5) -> dict[str, Any]:
        limit = max(1, min(int(limit_per_type or 5), 10))
        disease_id = str(context.get("disease_id") or "").strip()
        component = str(context.get("component") or "").strip().lower()
        query = _tokens({k: context.get(k) for k in (
            "symptoms","fingerprints","versions","environment","current_evidence","attempted_actions","failed_actions","case_context"
        )})
        failed_tokens = _tokens(context.get("failed_actions") or context.get("attempted_actions"))
        ranked: dict[str, list[tuple[float, dict[str, Any], list[str]]]] = defaultdict(list)
        for row in self.conn.execute("SELECT * FROM doctor_knowledge_items"):
            raw = dict(row)
            reasons = []
            score = 0.0
            if disease_id and str(raw.get("disease_id") or "") == disease_id:
                score += 100.0; reasons.append("disease_id_exact")
            if component and str(raw.get("component") or "").lower() == component:
                score += 35.0; reasons.append("component_exact")
            target = set(str(raw.get("search_text") or "").split())
            overlap = query & target
            if overlap:
                score += 60.0 * len(overlap) / max(1, len(query)); reasons.append(f"token_overlap:{len(overlap)}")
            if raw["knowledge_type"] == "NEGATIVE_EVIDENCE" and failed_tokens & target:
                score += 25.0; reasons.append("failed_action_warning")
            if score <= 0:
                continue
            def load_field(name, default):
                try: return json.loads(str(raw.get(name) or _j(default)))
                except Exception: return default
            item = {
                "item_id": raw["item_id"], "knowledge_type": raw["knowledge_type"], "title": raw["title"],
                "disease_id": raw["disease_id"], "component": raw["component"], "confidence": raw["confidence"],
                "applicability": _bounded(load_field("applicability_json", {})),
                "supporting_cases": _bounded(load_field("supporting_cases_json", [])),
                "external_sources": _bounded(load_field("external_sources_json", [])),
                "positive_evidence": _bounded(load_field("positive_evidence_json", [])),
                "negative_evidence": _bounded(load_field("negative_evidence_json", [])),
                "version_constraints": _bounded(load_field("version_constraints_json", [])),
                "environment_constraints": _bounded(load_field("environment_constraints_json", [])),
                "treatment_status": raw["treatment_status"], "safety": _bounded(load_field("safety_json", {})),
                "provenance": _bounded(load_field("provenance_json", {})), "why_matched": reasons,
                "score": round(score, 3),
            }
            ranked[raw["knowledge_type"]].append((score, item, reasons))
        out = {name: [] for name in GROUPS.values()}
        for ktype, group in GROUPS.items():
            vals = sorted(ranked.get(ktype, []), key=lambda x: (-x[0], str(x[1].get("title") or "")))[:limit]
            out[group] = [x[1] for x in vals]
        selected=[item for group in out.values() for item in group]
        affected_versions=[]
        relevant_sources=[]
        for item in selected:
            for value in item.get("version_constraints") or []:
                text=str(value)[:300]
                if text and text not in affected_versions:
                    affected_versions.append(text)
            for value in item.get("external_sources") or []:
                text=str(value)[:1200]
                if text.startswith(("https://","http://")) and text not in relevant_sources:
                    relevant_sources.append(text)
        failure_modes=[]
        for item in out.get("negative_evidence",[])[:limit]:
            failure_modes.append({
                "title": item.get("title"),
                "negative_evidence": item.get("negative_evidence") or [],
                "provenance": item.get("provenance") or {},
                "why_matched": item.get("why_matched") or [],
            })
        stats = dict(self.conn.execute("SELECT knowledge_type,COUNT(*) FROM doctor_knowledge_items GROUP BY knowledge_type"))
        excluded = self.conn.execute("SELECT COUNT(*) FROM doctor_v2_protocol_candidate_classification WHERE eligibility='NOT_ELIGIBLE_FOR_V2_EXECUTION'").fetchone()[0]
        return {"ok": True, "library_status": "READY", "bounded": True, "limit_per_type": limit,
                "query": {"disease_id": disease_id or None, "component": component or None, "tokens": sorted(query)[:40]},
                **out,
                "affected_versions": affected_versions[:20],
                "known_failure_modes": failure_modes,
                "relevant_external_sources": relevant_sources[:20],
                "stats": {"items_by_type": stats, "excluded_v2_legacy_from_experimental": int(excluded)}}
