from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

BASE = Path("/var/lib/suzie-doctor-ingest")
INBOX = BASE / "inbox"
RESULTS = BASE / "results"
PROCESSED = BASE / "processed"
LOCK = BASE / "ingest.lock"
MASTER = Path("/var/lib/suzie-doctor-server/knowledge/forum_knowledge_base.json")
NORMALIZED = Path("/var/lib/suzie-doctor-server/knowledge/normalized_knowledge.json")
COMPILER = Path("/opt/suzie-doctor-server/knowledge_compile.py")
PYTHON = Path("/opt/suzie-doctor-server/venv/bin/python")

ALLOWED_STATUS = {"CONFIRMED", "PROBABLE", "PARTIAL", "UNRESOLVED"}
ALLOWED_RISK = {"LOW", "MEDIUM", "HIGH"}
ALLOWED_AUTOMATION = {"AUTO_SAFE", "CONFIRM_REQUIRED", "DIAGNOSTIC_ONLY"}
ALLOWED_SCOPE = {"HA", "CROSS_SYSTEM", "LINUX", "FUTURE", "VENDOR"}
LIST_FIELDS = {
    "fingerprint": 16,
    "hypotheses": 16,
    "diagnostics": 24,
    "verify": 16,
    "failed_attempts": 16,
}
TEXT_LIMITS = {
    "title": 400,
    "symptoms": 4000,
    "evidence": 6000,
    "root_cause": 3000,
    "fix": 4000,
    "rollback": 2500,
    "notes": 3000,
    "source_date": 120,
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def clean_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    return text[:limit]


def norm(value: Any) -> str:
    text = clean_text(value, 8000).lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9_./:+-]+", " ", text)
    return " ".join(text.split())


def tokens(value: Any) -> set[str]:
    return set(re.findall(r"[a-z0-9_./:+-]{3,}", norm(value)))


def jaccard(a: set[str], b: set[str]) -> float:
    return 0.0 if not a or not b else len(a & b) / len(a | b)


def safe_list(value: Any, *, max_items: int, item_limit: int = 800) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("list field must be an array")
    out: list[str] = []
    for item in value[:max_items]:
        text = clean_text(item, item_limit)
        if text:
            out.append(text)
    return out


def validate_source(source: str) -> None:
    parsed = urlparse(source)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("source must be an http(s) URL")
    if len(source) > 1500:
        raise ValueError("source URL too long")


def build_incident(payload: dict[str, Any]) -> dict[str, Any]:
    source = clean_text(payload.get("source"), 1500)
    validate_source(source)
    title = clean_text(payload.get("title"), TEXT_LIMITS["title"])
    symptoms = clean_text(payload.get("symptoms"), TEXT_LIMITS["symptoms"])
    root_cause = clean_text(payload.get("root_cause"), TEXT_LIMITS["root_cause"])
    evidence = clean_text(payload.get("evidence"), TEXT_LIMITS["evidence"])
    if not title or not symptoms or not evidence:
        raise ValueError("title, symptoms and evidence are required")

    scope = clean_text(payload.get("scope") or "CROSS_SYSTEM", 40).upper()
    status = clean_text(payload.get("status") or "UNRESOLVED", 40).upper()
    risk = clean_text(payload.get("risk") or "MEDIUM", 40).upper()
    requested_automation = clean_text(
        payload.get("automation") or "DIAGNOSTIC_ONLY", 40
    ).upper()
    automation = "DIAGNOSTIC_ONLY"
    if scope not in ALLOWED_SCOPE:
        raise ValueError(f"scope must be one of {sorted(ALLOWED_SCOPE)}")
    if status not in ALLOWED_STATUS:
        raise ValueError(f"status must be one of {sorted(ALLOWED_STATUS)}")
    if risk not in ALLOWED_RISK:
        raise ValueError(f"risk must be one of {sorted(ALLOWED_RISK)}")
    if requested_automation not in ALLOWED_AUTOMATION:
        raise ValueError(
            f"automation must be one of {sorted(ALLOWED_AUTOMATION)}"
        )
    try:
        confidence = float(payload.get("confidence", 0.5))
    except Exception as exc:
        raise ValueError("confidence must be numeric") from exc
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0 and 1")

    incident = {
        "scope": scope,
        "status": status,
        "confidence": round(confidence, 4),
        "title": title,
        "fingerprint": safe_list(
            payload.get("fingerprint"),
            max_items=LIST_FIELDS["fingerprint"],
            item_limit=240,
        ),
        "symptoms": symptoms,
        "hypotheses": safe_list(
            payload.get("hypotheses"),
            max_items=LIST_FIELDS["hypotheses"],
        ),
        "diagnostics": safe_list(
            payload.get("diagnostics"),
            max_items=LIST_FIELDS["diagnostics"],
        ),
        "evidence": evidence,
        "failed_attempts": safe_list(
            payload.get("failed_attempts"),
            max_items=LIST_FIELDS["failed_attempts"],
        ),
        "root_cause": root_cause,
        "fix": clean_text(payload.get("fix"), TEXT_LIMITS["fix"]),
        "verify": safe_list(
            payload.get("verify"),
            max_items=LIST_FIELDS["verify"],
        ),
        "rollback": clean_text(
            payload.get("rollback"),
            TEXT_LIMITS["rollback"],
        ),
        "risk": risk,
        "automation": automation,
        "source_date": clean_text(
            payload.get("source_date"),
            TEXT_LIMITS["source_date"],
        ),
        "source": source,
        "notes": clean_text(payload.get("notes"), TEXT_LIMITS["notes"]),
    }

    # External findings always enter quarantine as DIAGNOSTIC_ONLY.
    if requested_automation != "DIAGNOSTIC_ONLY":
        incident["notes"] = (
            (incident["notes"] + " ").strip()
            + f"External ingest requested {requested_automation}; stored as "
              "DIAGNOSTIC_ONLY pending separate curation, structured primitive "
              "mapping and server safety gates."
        ).strip()
    digest = hashlib.sha256(
        (source + "\n" + title + "\n" + root_cause).encode("utf-8")
    ).hexdigest()[:12].upper()
    incident["id"] = f"INC-INGEST-{scope}-{digest}"
    return incident


def duplicate_check(
    incident: dict[str, Any],
    existing: list[dict[str, Any]],
) -> dict[str, Any] | None:
    title_t = tokens(incident["title"])
    root_t = tokens(incident["root_cause"])
    symptom_t = tokens(incident["symptoms"])
    for old in existing:
        if str(old.get("id")) == incident["id"]:
            return {
                "kind": "exact_id",
                "incident_id": old.get("id"),
            }
        if str(old.get("source") or "") != incident["source"]:
            continue
        title_score = jaccard(title_t, tokens(old.get("title")))
        root_score = jaccard(root_t, tokens(old.get("root_cause")))
        symptom_score = jaccard(symptom_t, tokens(old.get("symptoms")))
        if title_score >= 0.70 and max(root_score, symptom_score) >= 0.45:
            return {
                "kind": "same_source_probable_duplicate",
                "incident_id": old.get("id"),
                "title_similarity": round(title_score, 3),
                "root_similarity": round(root_score, 3),
                "symptom_similarity": round(symptom_score, 3),
            }
    return None


def candidate_matches(
    incident: dict[str, Any],
    normalized: dict[str, Any],
) -> list[dict[str, Any]]:
    query = (
        tokens(incident["title"])
        | tokens(incident["root_cause"])
        | set().union(*(tokens(x) for x in incident["fingerprint"]))
    )
    scored: list[tuple[float, dict[str, Any]]] = []
    for disease in normalized.get("diseases") or []:
        target = (
            tokens(disease.get("title"))
            | tokens(disease.get("root_cause"))
            | set().union(
                *(tokens(x) for x in (disease.get("fingerprints") or []))
            )
        )
        score = jaccard(query, target)
        if score >= 0.12:
            scored.append((score, disease))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "disease_id": d.get("disease_id"),
            "title": clean_text(d.get("title"), 240),
            "score": round(score, 3),
        }
        for score, d in scored[:3]
    ]


def write_result(request_id: str, payload: dict[str, Any]) -> None:
    path = RESULTS / f"{request_id}.json"
    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # Result payload is bounded and contains no Master KB/raw evidence export.
    # Parent directory is 2770, so traversal remains restricted.
    os.chmod(temp, 0o644)
    temp.replace(path)


def process(path: Path) -> None:
    request_id = path.stem
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise ValueError("request root must be an object")
        if clean_text(request.get("request_id"), 128) != request_id:
            raise ValueError("request_id mismatch")
        dry_run = bool(request.get("dry_run", True))
        incident = build_incident(request.get("incident") or {})

        master_bytes = MASTER.read_bytes()
        master = json.loads(master_bytes.decode("utf-8"))
        existing = list(master.get("incidents") or [])
        duplicate = duplicate_check(incident, existing)
        normalized = json.loads(NORMALIZED.read_text(encoding="utf-8"))
        candidates = candidate_matches(incident, normalized)

        if duplicate:
            write_result(request_id, {
                "ok": True,
                "result": "DUPLICATE",
                "dry_run": dry_run,
                "incident_id": incident["id"],
                "duplicate": duplicate,
                "candidate_diseases": candidates,
            })
            return

        if dry_run:
            write_result(request_id, {
                "ok": True,
                "result": "WOULD_ADD",
                "dry_run": True,
                "incident_id": incident["id"],
                "candidate_diseases": candidates,
                "validated": True,
            })
            return

        before_stats = dict(normalized.get("stats") or {})
        rollback = MASTER.with_suffix(".json.ingest-rollback")
        rollback.write_bytes(master_bytes)
        os.chmod(rollback, 0o640)
        try:
            master["incidents"] = existing + [incident]
            temp = MASTER.with_suffix(".json.ingest-tmp")
            temp.write_text(
                json.dumps(master, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.chmod(temp, 0o640)
            temp.replace(MASTER)
            cp = subprocess.run(
                [str(PYTHON), str(COMPILER)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=180,
                check=False,
            )
            if cp.returncode != 0:
                raise RuntimeError(
                    "compile/normalize failed: " + cp.stderr[-1500:]
                )
            after = json.loads(NORMALIZED.read_text(encoding="utf-8"))
        except Exception:
            MASTER.write_bytes(master_bytes)
            os.chmod(MASTER, 0o640)
            subprocess.run(
                [str(PYTHON), str(COMPILER)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=180,
                check=False,
            )
            raise
        finally:
            rollback.unlink(missing_ok=True)

        disease = None
        for item in after.get("diseases") or []:
            if incident["id"] in (item.get("incident_refs") or []):
                disease = {
                    "disease_id": item.get("disease_id"),
                    "title": clean_text(item.get("title"), 240),
                    "diagnosis_status": item.get("diagnosis_status"),
                }
                break
        after_stats = dict(after.get("stats") or {})
        write_result(request_id, {
            "ok": True,
            "result": "ADDED",
            "dry_run": False,
            "incident_id": incident["id"],
            "disease": disease,
            "unclassified": disease is None,
            "candidate_diseases_before_ingest": candidates,
            "counts": {
                "incidents_before": before_stats.get("incidents"),
                "incidents_after": after_stats.get("incidents"),
                "diseases_before": before_stats.get("diseases"),
                "diseases_after": after_stats.get("diseases"),
                "unclassified_before": before_stats.get("unclassified_incidents"),
                "unclassified_after": after_stats.get("unclassified_incidents"),
                "protocol_candidates_after": after_stats.get("protocol_candidates"),
                "new_protocols_promotable": after_stats.get(
                    "new_protocols_promotable"
                ),
                "executable_protocols": after_stats.get("executable_protocols"),
            },
        })
    except Exception as exc:
        write_result(request_id, {
            "ok": False,
            "result": "REJECTED",
            "error": f"{type(exc).__name__}: {clean_text(exc, 1200)}",
        })


def main() -> int:
    BASE.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        for path in sorted(INBOX.glob("*.json")):
            request_id = path.stem
            try:
                process(path)
            finally:
                target = PROCESSED / f"{request_id}.json"
                path.replace(target)
                old = sorted(PROCESSED.glob("*.json"), key=lambda p: p.stat().st_mtime)
                for stale in old[:-200]:
                    stale.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
