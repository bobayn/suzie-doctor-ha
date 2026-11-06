"""Regression: Doctor waits for human Marketplace trust and resumes updates after acceptance.

All calls are mocked/in-memory. Never touches live Home Assistant or Marketplace.
"""
import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "suzie_doctor/rootfs/app"))

from suzie_doctor.db import Database
from suzie_doctor.recommendations import recommendation_selftest


async def test() -> None:
    with TemporaryDirectory(prefix="doctor-marketplace-consent-") as tmp:
        db = Database(Path(tmp) / "test.sqlite3")
        db.initialize()
        try:
            result = await recommendation_selftest(db)
            failing = [case for case in result.get("cases", []) if not case.get("pass")]
            assert result.get("result") == "PASS", failing
            assert result.get("live_actions_executed") is False
            print("MARKETPLACE_CONSENT_DOCTOR_SELFTEST_PASS", len(result["cases"]))
        finally:
            db.conn.close()


if __name__ == "__main__":
    asyncio.run(test())
