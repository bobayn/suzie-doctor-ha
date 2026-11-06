"""Integration-level mock of signed Field action through ProtocolEngine."""
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "suzie_doctor/rootfs/app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from suzie_doctor.protocol_engine import ProtocolEngine
from test_http_ssl_profile_action import FakeHA


class DB:
    def begin_protocol_run(self, **kwargs):
        return 1

    def finish_protocol_run(self, *args, **kwargs):
        pass


class Supervisor:
    async def info(self):
        return {"homeassistant": "2026.11.0", "operating_system": "HAOS", "arch": "aarch64"}


RISK = {
    "harm_probability": "LOW",
    "irreversibility": "REVERSIBLE",
    "harm_magnitude": "MODERATE",
    "decision": "PROCEED",
    "rationale": "Exact native HA transaction with built-in five-minute auto-rollback",
}


def field_card():
    return {
        "schema_version": 1,
        "disease_id": "FIELD-UNCLASSIFIED",
        "title": "Native TLS profile update",
        "component": "http",
        "protocol": {"id": "FIELD-ACTION-SSL-TEST", "version": "1", "status": "FIELD_ONE_SHOT"},
        "automation_class": "CONFIRM_REQUIRED",
        "diagnostics": [{
            "id": "original_functional_criterion",
            "primitive": "ha_repair_absent",
            "args": {"domain": "http", "issue_id": "ssl_profile_outdated"},
            "save_as": "original_functional_pass",
        }],
        "confirm": {"all": ["field_action_authorized"]},
        "exclude": [],
        "preconditions": [],
        "checkpoint": {"required": False},
        "treatment": [{
            "step": "field_action",
            "primitive": "http_ssl_profile_upgrade",
            "args": {"domain": "http", "issue_id": "ssl_profile_outdated", "ssl_profile": "intermediate_v6"},
            "max_attempts": 1,
        }],
        "verify": {
            "rerun_diagnostics": True,
            "success_when": "conditions",
            "conditions": {"all": [{"expr": "original_functional_pass == true"}]},
        },
        "rollback": [],
        "field_action": {
            "binding": {"command_id": "test-command", "field_case_id": 460, "client_id": "ha-test"},
            "policy": {"disconnect_expected": True, "verify_grace_seconds": 480},
        },
    }


async def checks():
    ha = FakeHA()
    engine = ProtocolEngine(DB(), Supervisor(), ha, app_version="test", bridge_version="test", pack_version="test")
    card = field_card()

    # Family Doctor never receives one-shot mutation rights.
    family = await engine.execute_card(
        card, context={"field_action_authorized": True},
        trust_mode="safe_auto", risk_assessment=RISK, execution_actor="family_doctor",
    )
    assert family["result"] == "DIAGNOSIS_ONLY"
    assert ha.configure_count == 0

    # House-dispatched, risk-reviewed Field is allowed to stage it.
    staged = await engine.execute_card(
        card, context={"field_action_authorized": True},
        trust_mode="safe_auto", risk_assessment=RISK, execution_actor="field_suzie",
    )
    assert staged["result"] == "CONNECTION_LOST_EXPECTED", staged
    assert staged["verify_pending"] and ha.promote_count == 0
    assert ha.pending["ssl_profile"] == "intermediate_v6"

    ha.repair_active = False  # HA actually cleared original Repair after restart.
    done = await engine.verify_field_one_shot(card)
    assert done["result"] == "VERIFIED_PASS", done
    assert done["verify_passed"] and done["new_protocol_evidence"]
    assert done["tls_transaction"]["verified"]
    assert ha.stable["ssl_profile"] == "intermediate_v6" and ha.pending is None

    # Signed package with no Field binding must never promote.
    other = FakeHA()
    await other.ws_command("http/config/configure", config={
        "ssl_certificate": "/ssl/fullchain.pem",
        "ssl_key": "/ssl/privkey.pem",
        "ssl_profile": "intermediate_v6",
        "server_port": 8123,
        "trusted_proxies": ["192.168.0.0/16"],
    })
    other.repair_active = False
    malformed = field_card()
    malformed["field_action"]["binding"] = {}
    try:
        await ProtocolEngine(DB(), Supervisor(), other, app_version="test", bridge_version="test", pack_version="test").verify_field_one_shot(malformed)
        raise AssertionError("unsigned promotion must be refused")
    except Exception as exc:
        assert "Field Case binding" in str(exc)
    assert other.promote_count == 0


if __name__ == "__main__":
    asyncio.run(checks())
    print("http_ssl_field_engine: PASS")
