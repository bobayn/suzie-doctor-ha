from __future__ import annotations
import importlib.util
import json
import tempfile
from pathlib import Path

MOD = Path("/opt/suzie-doctor-server/incident_ingest.py")

def load():
    spec=importlib.util.spec_from_file_location("doctor_ingest",MOD)
    m=importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(m)
    return m

def main():
    m=load()
    cases=[]
    def add(name,ok,detail=None):
        item={"id":name,"pass":bool(ok)}
        if detail is not None:item["detail"]=detail
        cases.append(item)

    with tempfile.TemporaryDirectory(prefix="doctor-ingest-test-") as td:
        root=Path(td)
        base=root/"queue"; inbox=base/"inbox"; results=base/"results"; processed=base/"processed"
        for p in (base,inbox,results,processed):p.mkdir(parents=True,exist_ok=True)
        master=root/"master.json"; normalized=root/"normalized.json"
        master.write_bytes(Path("/var/lib/suzie-doctor-server/knowledge/forum_knowledge_base.json").read_bytes())
        normalized.write_bytes(Path("/var/lib/suzie-doctor-server/knowledge/normalized_knowledge.json").read_bytes())

        m.BASE=base;m.INBOX=inbox;m.RESULTS=results;m.PROCESSED=processed;m.LOCK=base/"ingest.lock"
        m.MASTER=master;m.NORMALIZED=normalized

        before=len(json.loads(master.read_text())["incidents"])

        payload={
          "request_id":"commit-ok","dry_run":False,
          "incident":{
            "title":"Synthetic transaction self-test",
            "source":"https://example.com/doctor-ingest-selftest-commit",
            "symptoms":"Synthetic transaction test symptom.",
            "evidence":"Synthetic evidence for temporary-copy self-test only.",
            "root_cause":"Synthetic test root cause.",
            "scope":"CROSS_SYSTEM","status":"UNRESOLVED","confidence":0.4,
            "fingerprint":["ingest selftest"],"risk":"LOW","automation":"DIAGNOSTIC_ONLY"
          }
        }
        q=inbox/"commit-ok.json";q.write_text(json.dumps(payload))
        # /bin/true stands in for successful compiler; production compiler is
        # tested separately by the Doctor server pipeline.
        m.PYTHON=Path("/bin/true");m.COMPILER=Path("/tmp/unused")
        m.process(q)
        res=json.loads((results/"commit-ok.json").read_text())
        after=len(json.loads(master.read_text())["incidents"])
        add("commit_appends_one",res.get("result")=="ADDED" and after==before+1,res.get("result"))

        payload["request_id"]="commit-fail"
        payload["incident"]["source"]="https://example.com/doctor-ingest-selftest-rollback"
        q=inbox/"commit-fail.json";q.write_text(json.dumps(payload))
        before_fail=master.read_bytes()
        m.PYTHON=Path("/bin/false")
        m.process(q)
        res2=json.loads((results/"commit-fail.json").read_text())
        add("compiler_failure_rejected",res2.get("result")=="REJECTED",res2.get("result"))
        add("compiler_failure_rolls_back",master.read_bytes()==before_fail)

        dup=m.duplicate_check(
            json.loads(master.read_text())["incidents"][-1],
            json.loads(master.read_text())["incidents"],
        )
        add("exact_duplicate_detected",bool(dup))

        try:
            m.build_incident({"title":"x","source":"file:///etc/passwd","symptoms":"x","evidence":"x"})
            valid=False
        except ValueError:
            valid=True
        add("non_http_source_rejected",valid)

        quarantined=m.build_incident({
            "title":"q","source":"https://example.com/q","symptoms":"q",
            "evidence":"q","automation":"AUTO_SAFE"
        })
        add(
            "external_auto_safe_forced_diagnostic_only",
            quarantined.get("automation")=="DIAGNOSTIC_ONLY"
            and "requested AUTO_SAFE" in quarantined.get("notes",""),
        )

    result="PASS" if all(x["pass"] for x in cases) else "FAIL"
    print(json.dumps({"result":result,"cases":cases},indent=2))
    return 0 if result=="PASS" else 1

if __name__=="__main__":
    raise SystemExit(main())
