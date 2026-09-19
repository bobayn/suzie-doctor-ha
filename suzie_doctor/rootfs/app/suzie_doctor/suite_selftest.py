"""Pure Suite checks shared by offline CI and the on-device Release Gate."""
from __future__ import annotations
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from . import APP_VERSION, BRIDGE_VERSION, PROTOCOL_PACK_VERSION
from .connector import Adapter, CapabilityRegistry, ConnectorError, DoctorConnector, build_registry
from .suite import ROOT, SuiteCompatibilityGate


async def suite_selftest() -> dict[str, bool]:
    registry = build_registry()
    from .protocol_engine import ProtocolEngine
    checks = {"connector_registry": set(registry.primitives) == ProtocolEngine.SUPPORTED_PRIMITIVES}
    class Backend:
        async def get_config(self): return {"version": "test"}
        async def info(self): return {"version": "test"}
        async def bridge_reload_entry(self, entry_id): return True
    connector = DoctorConnector(Backend(), Backend())
    await connector.refresh_health()
    snapshot = connector.discovery()
    checks["capability_discovery"] = snapshot == connector.discovery() and all(
        not a["available"] for a in snapshot["adapters"] if not a["implemented"])
    duplicates = 0
    try: registry.register_adapter(Adapter("ha"))
    except ConnectorError: duplicates += 1
    try: registry.register(next(iter(registry.capabilities.values())))
    except ConnectorError: duplicates += 1
    calls = []
    async def implementation(name, args, env):
        calls.append(name)
        raise RuntimeError("adapter_failed")
    rejected = 0
    for name, args in (("shell", {}), ("reload_config_entry", {"entry_id": "*"}),
                       ("reload_config_entry", {})):
        try: await connector.dispatch(name, args, {}, implementation)
        except ConnectorError: rejected += 1
    try: await connector.dispatch("reload_config_entry", {"entry_id": "abc"}, {}, implementation)
    except RuntimeError: rejected += 1
    checks["connector_security"] = duplicates == 2 and rejected == 4 and calls == ["reload_config_entry"]
    gate = SuiteCompatibilityGate(APP_VERSION, BRIDGE_VERSION, PROTOCOL_PACK_VERSION)
    known = set(registry.capabilities)
    checks["skill_loaded"] = not gate.errors and bool(gate.skill_text())
    checks["skill_connector_compatibility"] = gate.status(known=known)["compatible"]
    checks["suite_manifest"] = gate.status()["compatible"]
    with TemporaryDirectory(prefix="doctor-suite-selftest-") as temp:
        root = Path(temp)
        shutil.copy(ROOT / "suite_manifest.json", root)
        shutil.copytree(ROOT / "skills", root / "skills")
        path = root / "skills/suzie-doctor/metadata.json"
        metadata = json.loads(path.read_text())
        metadata["capability_schema_version"] = 2
        path.write_text(json.dumps(metadata))
        incompatible = SuiteCompatibilityGate(APP_VERSION, BRIDGE_VERSION, PROTOCOL_PACK_VERSION, root)
        checks["suite_version_gate"] = (not incompatible.status()["treatment_allowed"] and
            incompatible.status()["diagnosis_allowed"] and not gate.status(protocol_schema=2)["treatment_allowed"] and
            not gate.status(server_api=2)["treatment_allowed"] and
            not gate.status(required=["shell.exec"], known=known)["treatment_allowed"])
    return checks
