"""Mock-only regression for signed Field TLS profile transaction.

No network calls, no live HA mutation. Run:
python3 suzie_doctor/server_migration_v2/test_http_ssl_profile_action.py
"""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "suzie_doctor/rootfs/app"))
from suzie_doctor.http_ssl_profile_repair import (
    HTTPSSLProfileError,
    stage_http_ssl_profile,
    verify_and_promote_http_ssl_profile,
)


class FakeHA:
    def __init__(self):
        self.stable = {
            "ssl_certificate": "/ssl/fullchain.pem",
            "ssl_key": "/ssl/privkey.pem",
            "ssl_profile": "modern_v4",
            "server_port": 8123,
            "trusted_proxies": ["192.168.0.0/16"],
            "created_at": "read-only",
            "error": None,
        }
        self.pending = None
        self.revert_at = None
        self.active = "stable"
        self.repair_active = True
        self.configure_count = 0
        self.promote_count = 0
        self.fail_promote = False
        self.disconnect = False

    async def list_repairs(self):
        return ([{"domain": "http", "issue_id": "ssl_profile_outdated", "active": True}]
                if self.repair_active else [])

    async def ws_command(self, cmd, **kw):
        if cmd == "http/config":
            return {"stable": dict(self.stable),
                    "pending": dict(self.pending) if self.pending else None,
                    "revert_at": self.revert_at,
                    "active_config_type": self.active}
        if cmd == "http/config/configure":
            self.configure_count += 1
            payload = kw["config"]
            assert "created_at" not in payload and "error" not in payload
            assert payload["ssl_certificate"] == self.stable["ssl_certificate"]
            assert payload["ssl_key"] == self.stable["ssl_key"]
            assert payload["trusted_proxies"] == self.stable["trusted_proxies"]
            self.pending = dict(payload)
            self.active = "pending"
            self.revert_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
            if self.disconnect:
                raise TimeoutError("expected Core restart disconnect")
            return {"restart": True}
        if cmd == "http/config/promote":
            self.promote_count += 1
            if self.fail_promote:
                raise RuntimeError("no pending HTTP config to promote")
            if self.pending is None:
                raise RuntimeError("no pending HTTP config to promote")
            self.stable = dict(self.pending)
            self.pending = None
            self.revert_at = None
            self.active = "stable"
            return None
        raise AssertionError("unexpected WS command: " + cmd)


async def checks():
    a = FakeHA()
    staged = await stage_http_ssl_profile(a, "intermediate_v6")
    assert staged["accepted"] is True and a.configure_count == 1
    assert a.stable["ssl_profile"] == "modern_v4"
    assert a.pending["ssl_profile"] == "intermediate_v6"
    assert a.promote_count == 0  # no confirmation at treatment time

    # Do not confirm until the original Repair is gone.
    first = await verify_and_promote_http_ssl_profile(a, "intermediate_v6")
    assert not first["verified"] and first["reason"] == "original_repair_still_active"
    assert a.promote_count == 0
    a.repair_active = False
    done = await verify_and_promote_http_ssl_profile(a, "intermediate_v6")
    assert done["verified"] and done["reason"] == "promoted"
    assert a.stable["ssl_profile"] == "intermediate_v6"
    assert a.pending is None and a.revert_at is None
    idem = await verify_and_promote_http_ssl_profile(a, "intermediate_v6")
    assert idem["verified"] and idem["reason"] == "already_promoted"
    assert a.promote_count == 1

    # Reject an unknown choice without mutating HA.
    b = FakeHA()
    try:
        await stage_http_ssl_profile(b, "modern_v6")
        raise AssertionError("modern_v6 must be refused")
    except HTTPSSLProfileError:
        pass
    assert b.configure_count == 0

    # A pre-existing pending change always takes precedence.
    c = FakeHA()
    c.pending = dict(c.stable)
    c.active = "pending"
    try:
        await stage_http_ssl_profile(c, "intermediate_v6")
        raise AssertionError("second transaction must be refused")
    except HTTPSSLProfileError:
        pass
    assert c.configure_count == 0

    # No active original Repair means no guessed action.
    d = FakeHA()
    d.repair_active = False
    try:
        await stage_http_ssl_profile(d, "intermediate_v6")
        raise AssertionError("unrelated change must be refused")
    except HTTPSSLProfileError:
        pass
    assert d.configure_count == 0

    # Changed pending settings must never be promoted.
    e = FakeHA()
    await stage_http_ssl_profile(e, "intermediate_v6")
    e.repair_active = False
    e.pending["server_port"] = 8124
    bad = await verify_and_promote_http_ssl_profile(e, "intermediate_v6")
    assert bad["verified"] is False and e.promote_count == 0

    # Expired deadline = let HA roll back, never promote.
    f = FakeHA()
    await stage_http_ssl_profile(f, "intermediate_v6")
    f.repair_active = False
    f.revert_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    expired = await verify_and_promote_http_ssl_profile(f, "intermediate_v6")
    assert expired["verified"] is False and f.promote_count == 0

    # Native HA rollback leaves original profile and no false SUCCESS.
    g = FakeHA()
    await stage_http_ssl_profile(g, "intermediate_v6")
    g.repair_active = True
    g.pending = None
    g.active = "stable"
    g.revert_at = None
    rollback = await verify_and_promote_http_ssl_profile(g, "intermediate_v6")
    assert rollback["verified"] is False

    # If a valid UI action promoted first, confirmation is idempotent.
    h = FakeHA()
    await stage_http_ssl_profile(h, "intermediate_v6")
    h.repair_active = False
    h.stable = h.pending
    h.pending = None
    h.active = "stable"
    h.revert_at = None
    old_dialog = await verify_and_promote_http_ssl_profile(h, "intermediate_v6")
    assert old_dialog["verified"] and h.promote_count == 0


if __name__ == "__main__":
    asyncio.run(checks())
    print("http_ssl_profile_action: PASS")
