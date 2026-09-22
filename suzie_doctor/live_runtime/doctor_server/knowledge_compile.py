from __future__ import annotations

import json
import os
from pathlib import Path

import sys
sys.path.insert(0, "/opt/suzie-doctor-server/lib")
from knowledge import KnowledgeCompiler
sys.path.insert(0, "/opt/suzie-doctor-server")
import disease_normalize
import protocol_factory

SOURCE = Path("/var/lib/suzie-doctor-server/knowledge/forum_knowledge_base.json")
TARGET = Path("/var/lib/suzie-doctor-server/knowledge/compiled_knowledge.json")
PACK = Path("/opt/suzie-doctor-server/protocol_pack")


def main() -> int:
    compiler = KnowledgeCompiler(PACK)
    corpus = compiler.load_json(SOURCE)
    result = compiler.compile(corpus)
    stats = result.get("stats") or {}
    integrity = result.get("integrity") or {}
    fatal = {
        key: value
        for key, value in integrity.items()
        if key in {"pack_binding_conflicts", "duplicate_protocol_ids"} and value
    }
    if fatal:
        raise SystemExit(f"fatal knowledge integrity: {fatal}")
    if int(stats.get("incidents") or 0) < 1:
        raise SystemExit("knowledge corpus contains no incidents")
    temp = TARGET.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temp, 0o640)
    temp.replace(TARGET)
    normalize_rc = disease_normalize.main()
    if normalize_rc != 0:
        raise SystemExit("knowledge normalization failed")
    factory_rc = protocol_factory.main()
    if factory_rc != 0:
        raise SystemExit("protocol factory failed")
    factory_data = json.loads(protocol_factory.OUTPUT.read_text(encoding="utf-8"))
    print(json.dumps({
        "result": "COMPILED_NORMALIZED_AND_PROTOCOLS",
        "stats": stats,
        "protocol_factory": factory_data.get("stats") or {},
        "warnings": {
            key: value
            for key, value in integrity.items()
            if key not in {"pack_binding_conflicts", "duplicate_protocol_ids"}
            and value
        },
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
