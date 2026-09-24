#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import Context, FastMCP, Image
from mcp.types import ToolAnnotations
import httpx
from PIL import Image as PILImage, ImageChops, ImageDraw, ImageStat

HOST = os.environ.get("SUZIE_HOME_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("SUZIE_HOME_MCP_PORT", "8766"))
PIXY_DEVICE = "/dev/video0"
PIXY_NAME = "EMEET PIXY 2K"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
PIXY_VISION_STATUS = RUNTIME_DIR / "suzie-pixy-vision" / "status.json"
PIXY_LATEST_FRAME = RUNTIME_DIR / "suzie-pixy-vision" / "latest.jpg"
PIXY_CONTROL_DIR = Path.home() / ".local/state/suzie-home-mcp"
PIXY_CONTROL_FILE = PIXY_CONTROL_DIR / "pixy-command.json"
PIXY_CONTROL_RESULT = PIXY_CONTROL_DIR / "pixy-result.json"
DOCTOR_INGEST_BASE = Path("/var/lib/suzie-doctor-ingest")
DOCTOR_INGEST_INBOX = DOCTOR_INGEST_BASE / "inbox"
DOCTOR_INGEST_RESULTS = DOCTOR_INGEST_BASE / "results"
DOCTOR_CURATE_BASE = Path("/var/lib/suzie-doctor-curation")
DOCTOR_CURATE_INBOX = DOCTOR_CURATE_BASE / "inbox"
DOCTOR_CURATE_RESULTS = DOCTOR_CURATE_BASE / "results"
DOCTOR_MCP_URL = "http://127.0.0.1:8791/mcp"

mcp = FastMCP(
    name="Suzie Home — Orange Pi 4 Pro",
    instructions=(
        "Separate protected home-core connector running directly on orangepi4pro. "
        "No arbitrary shell. Use only structured diagnostics and bounded allowlisted "
        "EMEET PIXY 2K controls, including privacy/session/tracking/PTZ. "
        "Suzie Doctor incident ingest is structured write-only: it can submit one "
        "external incident for protected validation/dedupe but cannot export the KB. "
        "Suzie Doctor Web loader rule: when a user message is CASE #N, use the Doctor "
        "tools in this connector: first load doctor.skill, then doctor.case.get for N, "
        "then atomically doctor.case.claim before any client diagnostics or treatment. "
        "Preserve the doctor_handle returned by claim and pass that exact handle to every "
        "case-bound Doctor/HA/Supervisor tool until complete-next; the handle is a transport "
        "capability, not treatment policy. Before execute=true, Suzie Doctor must make and "
        "pass the autonomous risk_assessment required by the canonical Skill Core. After "
        "claim, follow the canonical Skill Core "
        "returned by doctor.skill exactly; do not duplicate or invent treatment policy in "
        "this adapter."
    ),
    host=HOST,
    port=PORT,
    streamable_http_path="/mcp",
)

def _run(args: list[str], *, timeout: int = 12, binary: bool = False) -> tuple[int, Any, str]:
    try:
        cp = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            text=not binary,
        )
    except subprocess.TimeoutExpired:
        return 124, (b"" if binary else ""), "timeout"
    except Exception as exc:
        return 125, (b"" if binary else ""), f"{type(exc).__name__}: {exc}"
    stderr = cp.stderr.decode("utf-8", "replace") if binary and isinstance(cp.stderr, (bytes, bytearray)) else (cp.stderr or "")
    return cp.returncode, cp.stdout, stderr[-12000:]

def _text(args: list[str], timeout: int = 12, limit: int = 20000) -> dict[str, Any]:
    rc, out, err = _run(args, timeout=timeout, binary=False)
    if rc != 0:
        return {"ok": False, "rc": rc, "error": (err or str(out))[-6000:]}
    return {"ok": True, "text": str(out)[-limit:]}

def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except Exception:
        return None

def _vision_state() -> dict[str, Any]:
    data = _read_json(PIXY_VISION_STATUS)
    if data is None:
        return {"ok": False, "error": "PIXY vision status unavailable"}
    return data

def _vision_goal_met(action: str, state: dict[str, Any]) -> bool:
    if not isinstance(state, dict):
        return False
    nt = state.get("native_tracking") or {}
    if action == "privacy":
        return bool(state.get("privacy")) and not bool(state.get("visual_session")) and not bool(state.get("capture_active"))
    if action == "open":
        return bool(state.get("visual_session")) and bool(state.get("capture_active")) and not bool(state.get("privacy"))
    if action == "tracking_on":
        return bool(state.get("visual_session")) and (nt.get("reported") == "tracking" or nt.get("requested") == "tracking") and bool(nt.get("engaged", True))
    if action == "tracking_off":
        return (nt.get("reported") == "off" or nt.get("requested") == "off" or not bool(nt.get("engaged", False)))
    return True

def _send_vision_command(action: str, *, timeout: float = 5.0) -> dict[str, Any]:
    allowed = {"privacy", "open", "tracking_on", "tracking_off"}
    if action not in allowed:
        return {"ok": False, "error": "unsupported PIXY vision command"}
    try:
        PIXY_CONTROL_DIR.mkdir(parents=True, exist_ok=True)
        req_id = uuid.uuid4().hex
        payload = {"request_id": req_id, "action": action, "created_unix": time.time()}
        tmp = PIXY_CONTROL_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False))
        os.replace(tmp, PIXY_CONTROL_FILE)
    except Exception as exc:
        return {"ok": False, "error": f"control write failed: {type(exc).__name__}: {exc}"}

    deadline = time.monotonic() + max(1.0, min(float(timeout), 8.0))
    result = None
    state = _vision_state()
    while time.monotonic() < deadline:
        r = _read_json(PIXY_CONTROL_RESULT)
        if r and r.get("request_id") == req_id:
            result = r
            if not r.get("ok"):
                return {"ok": False, "command": action, "result": r, "vision": _vision_state()}
        state = _vision_state()
        if result is not None and _vision_goal_met(action, state):
            return {"ok": True, "command": action, "result": result, "vision": state}
        time.sleep(0.05)
    return {"ok": False, "command": action, "error": "PIXY command timeout", "result": result, "vision": state}


def _tracking_is_on(state: dict[str, Any]) -> bool:
    nt = (state or {}).get("native_tracking") or {}
    return bool(
        nt.get("reported") == "tracking"
        or nt.get("requested") == "tracking"
        or nt.get("engaged") is True
    )


def _current_ptz() -> dict[str, int] | None:
    got = _text([
        "v4l2-ctl", "-d", PIXY_DEVICE,
        "--get-ctrl=pan_absolute,tilt_absolute,zoom_absolute",
    ])
    if not got.get("ok"):
        return None
    vals: dict[str, int] = {}
    for line in str(got.get("text") or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().split()[0] if value.strip() else ""
        if key in {"pan_absolute", "tilt_absolute", "zoom_absolute"}:
            try:
                vals[key] = int(value)
            except Exception:
                pass
    return vals if vals else None


def _wait_fresh_vision_frame(after_seq: int | None = None, *, timeout: float = 5.0) -> tuple[dict[str, Any], bytes] | None:
    deadline = time.monotonic() + max(1.0, min(float(timeout), 8.0))
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = _vision_state()
        if isinstance(state, dict):
            last_state = state
            seq = state.get("camera_frame_seq")
            fresh = after_seq is None or (seq is not None and seq != after_seq)
            if fresh and state.get("visual_session") and state.get("capture_active") and PIXY_LATEST_FRAME.exists():
                try:
                    data = PIXY_LATEST_FRAME.read_bytes()
                    if 1000 <= len(data) <= 8 * 1024 * 1024 and data.startswith(b"\xff\xd8"):
                        return state, data
                except Exception:
                    pass
        time.sleep(0.04)
    return None


def _frame_recognition(state: dict[str, Any], index: int, *, analyze: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "index": index,
        "captured_unix": state.get("captured_unix"),
        "camera_frame_seq": state.get("camera_frame_seq"),
        "scene_quality": state.get("scene_quality"),
        "mean_luma": state.get("mean_luma"),
        "backend": state.get("backend"),
        "model": state.get("model"),
        "detector_ms": state.get("detector_ms"),
        "npu_inference_ms": state.get("npu_inference_ms"),
    }
    if analyze:
        objects = state.get("objects")
        out["objects"] = objects
        counts: dict[str, int] = {}
        if isinstance(objects, list):
            for obj in objects:
                if isinstance(obj, dict):
                    name = str(obj.get("class") or "unknown")
                    counts[name] = counts.get(name, 0) + 1
        out["object_counts"] = counts
        speaker = state.get("active_speaker")
        if speaker is not None:
            out["active_speaker"] = speaker
        faces = state.get("speaker_faces")
        if isinstance(faces, list):
            out["face_count"] = len(faces)
    return out


def _image_change_pct(previous: bytes | None, current: bytes) -> float | None:
    if not previous:
        return None
    try:
        with PILImage.open(io.BytesIO(previous)) as a0, PILImage.open(io.BytesIO(current)) as b0:
            a = a0.convert("L").resize((160, 120))
            b = b0.convert("L").resize((160, 120))
            diff = ImageChops.difference(a, b)
            mean = float(ImageStat.Stat(diff).mean[0])
            return round(mean * 100.0 / 255.0, 2)
    except Exception:
        return None


def _contact_sheet(frames: list[bytes], labels: list[str] | None = None) -> bytes:
    if not frames:
        raise ValueError("no frames")
    thumbs = []
    for raw in frames:
        with PILImage.open(io.BytesIO(raw)) as src:
            img = src.convert("RGB")
            img.thumbnail((320, 240))
            thumbs.append(img.copy())
    cols = min(4, len(thumbs))
    rows = (len(thumbs) + cols - 1) // cols
    cell_w, cell_h, label_h = 320, 240, 24
    sheet = PILImage.new("RGB", (cell_w * cols, (cell_h + label_h) * rows), "black")
    draw = ImageDraw.Draw(sheet)
    for i, img in enumerate(thumbs):
        col, row = i % cols, i // cols
        x = col * cell_w + (cell_w - img.width) // 2
        y = row * (cell_h + label_h) + (cell_h - img.height) // 2
        sheet.paste(img, (x, y))
        label = labels[i] if labels and i < len(labels) else f"#{i+1}"
        draw.text((col * cell_w + 6, row * (cell_h + label_h) + cell_h + 4), label[:48], fill="white")
    buf = io.BytesIO()
    sheet.save(buf, format="JPEG", quality=86, optimize=True)
    return buf.getvalue()


def _capture_series(*, count: int, interval_ms: int, analyze: bool, labels: list[str] | None = None) -> tuple[list[dict[str, Any]], list[bytes]]:
    results: list[dict[str, Any]] = []
    frames: list[bytes] = []
    previous: bytes | None = None
    seq = _vision_state().get("camera_frame_seq")
    for i in range(count):
        if i:
            time.sleep(interval_ms / 1000.0)
        got = _wait_fresh_vision_frame(seq, timeout=max(3.0, interval_ms / 1000.0 + 2.0))
        if got is None:
            results.append({"index": i + 1, "ok": False, "error": "fresh vision frame timeout"})
            continue
        state, raw = got
        seq = state.get("camera_frame_seq")
        meta = _frame_recognition(state, i + 1, analyze=analyze)
        meta["ok"] = True
        meta["change_from_previous_pct"] = _image_change_pct(previous, raw)
        if labels and i < len(labels):
            meta["label"] = labels[i]
        results.append(meta)
        frames.append(raw)
        previous = raw
    return results, frames


def _series_summary(frames_meta: list[dict[str, Any]]) -> dict[str, Any]:
    changes = [x.get("change_from_previous_pct") for x in frames_meta if isinstance(x.get("change_from_previous_pct"), (int, float))]
    object_totals: dict[str, int] = {}
    for item in frames_meta:
        for name, n in (item.get("object_counts") or {}).items():
            object_totals[str(name)] = max(object_totals.get(str(name), 0), int(n))
    return {
        "frames_requested": len(frames_meta),
        "frames_ok": sum(1 for x in frames_meta if x.get("ok")),
        "max_objects_seen": object_totals,
        "change_pct_avg": round(sum(changes) / len(changes), 2) if changes else None,
        "change_pct_max": round(max(changes), 2) if changes else None,
        "note": "change_pct is pixel change, not identity recognition",
    }

def _meminfo() -> dict[str, Any]:
    vals: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if ":" not in line:
                continue
            key, rest = line.split(":", 1)
            num = rest.strip().split()[0]
            if num.isdigit():
                vals[key] = int(num)
    except Exception:
        return {}
    total = vals.get("MemTotal", 0)
    avail = vals.get("MemAvailable", 0)
    used = max(0, total - avail)
    return {
        "ram_total_mb": round(total / 1024, 1) if total else None,
        "ram_used_mb": round(used / 1024, 1) if total else None,
        "ram_pct": round(used * 100 / total, 1) if total else None,
    }

def _cpu_temp() -> float | None:
    for p in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
        try:
            v = float(p.read_text().strip())
            if abs(v) > 1000:
                v /= 1000.0
            if -20 <= v <= 130:
                return round(v, 1)
        except Exception:
            pass
    return None

@mcp.tool()
def home_core_status() -> dict[str, Any]:
    """Read a bounded health snapshot of the Orange Pi 4 Pro home-core host."""
    try:
        du = shutil.disk_usage("/")
        disk = {
            "root_total_gb": round(du.total / (1024**3), 1),
            "root_used_gb": round(du.used / (1024**3), 1),
            "root_pct": round(du.used * 100 / du.total, 1),
        }
    except Exception:
        disk = {}
    try:
        uptime_s = int(float(Path("/proc/uptime").read_text().split()[0]))
    except Exception:
        uptime_s = None
    rc, kernel, _ = _run(["huname", "-r"])
    rc2, addresses, _ = _run(["ip", "-br", "address"])
    rc3, links, _ = _run(["ip", "-br", "link"])
    return {
        "ok": True,
        "hostname": socket.gethostname(),
        "kernel": str(kernel).strip() if rc == 0 else None,
        "uptime_s": uptime_s,
        "cpu_temp_c": _cpu_temp(),
        **_meminfo(),
        **disk,
        "addresses": str(addresses)[-8000:] if rc2 == 0 else None,
        "links": str(links)[-8000:] if rc3 == 0 else None,
    }

@mcp.tool()
def usb_overview() -> dict[str, Any]:
    """Read USB identities/topology and V4L2 device names on Orange Pi 4 Pro."""
    result: dict[str, Any] = {"ok": True}
    if shutil.which("lsusb"):
        result["lsusb"] = _text(["lsusb"], limit=12000)
        result["topology"] = _text(["lsusb", "-t"], limit=12000)
    else:
        result["lsusb"] = {"ok": False, "error": "lsusb not installed"}
    if shutil.which("v4l2-ctl"):
        result["video_devices"] = _text(["v4l2-ctl", "--list-devices"], limit=12000)
    else:
        result["video_devices"] = {"ok": False, "error": "v4l2-ctl not installed"}
    return result

@mcp.tool()
def pixy_camera(
    action: str = "status",
    pan: int | None = None,
    tilt: int | None = None,
    zoom: int | None = None,
    focus: int | None = None,
    autofocus: bool | None = None,
    width: int = 1280,
    height: int = 720,
    count: int = 5,
    interval_ms: int = 1000,
    analyze: bool = True,
    scan_degrees: list[int] | None = None,
    scan_tilt_degrees: int = 0,
    settle_ms: int = 700,
):
    """Safe EMEET PIXY 2K tool. Actions: status, vision_status, snapshot, burst/sequence, scan/room_scan, PTZ/center/focus, privacy, wake/open, tracking on/off. Burst and scan use transient in-memory frames and return local recognition metadata plus a contact sheet; no video archive is created."""
    original_action = str(action or "status").strip()
    raw_action = original_action.lower()
    a = raw_action
    # Vision-memory bridge for clients with a cached pixy_camera schema.
    # Examples: memory:status | memory:pull | memory:events | memory:search:person
    if raw_action == "memory" or raw_action.startswith("memory:"):
        import json as _json
        import vision_memory_api as _vma
        subaction = "status" if raw_action == "memory" else original_action.split(":", 1)[1]
        result = _vma.handle(subaction, count if count is not None else 20)
        if isinstance(result, dict) and "_image_bytes" in result:
            data = result.pop("_image_bytes")
            meta = result.pop("_image_meta", {})
            return (_json.dumps({"ok": True, **meta}, ensure_ascii=False), Image(data=data, format="jpeg"))
        return result
    # Backward-compatible compact syntax works even for clients that cached the
    # older tool schema and therefore cannot yet send the new count/interval fields.
    # Examples: burst:8:500  |  scan:-45,0,45  |  scan:-30,0,30:10:800
    if raw_action.startswith("burst:") or raw_action.startswith("sequence:") or raw_action.startswith("series:"):
        parts = raw_action.split(":")
        a = "burst"
        try:
            if len(parts) > 1 and parts[1]:
                count = int(parts[1])
            if len(parts) > 2 and parts[2]:
                interval_ms = int(parts[2])
            if len(parts) > 3 and parts[3]:
                analyze = parts[3] not in {"0", "false", "no", "off"}
        except ValueError:
            return {"ok": False, "error": "burst syntax: burst:COUNT:INTERVAL_MS[:ANALYZE]"}
    elif raw_action.startswith("scan:") or raw_action.startswith("room_scan:") or raw_action.startswith("ptz_scan:"):
        prefix, spec = raw_action.split(":", 1)
        a = "scan"
        parts = spec.split(":")
        try:
            if parts and parts[0]:
                scan_degrees = [int(v.strip()) for v in parts[0].split(",") if v.strip()]
            if len(parts) > 1 and parts[1]:
                scan_tilt_degrees = int(parts[1])
            if len(parts) > 2 and parts[2]:
                settle_ms = int(parts[2])
        except ValueError:
            return {"ok": False, "error": "scan syntax: scan:PAN_DEG_LIST[:TILT_DEG[:SETTLE_MS]]"}

    if a == "status":
        devices = _text(["v4l2-ctl", "--list-devices"], limit=12000)
        ctrls = _text([
            "v4l2-ctl", "-d", PIXY_DEVICE,
            "--get-ctrl=pan_absolute,tilt_absolute,zoom_absolute,focus_automatic_continuous,focus_absolute",
        ], limit=12000)
        return {
            "ok": bool(devices.get("ok") and ctrls.get("ok")),
            "target": socket.gethostname(),
            "camera": PIXY_NAME,
            "device": PIXY_DEVICE,
            "devices": devices,
            "controls": ctrls,
            "vision": _vision_state(),
        }

    if a in {"vision", "vision_status", "tracking_status"}:
        return _vision_state()

    if a in {"privacy", "return_to_privacy"}:
        return _send_vision_command("privacy")

    if a in {"wake", "open", "open_visual"}:
        return _send_vision_command("open")

    if a in {"tracking_on", "track_on"}:
        return _send_vision_command("tracking_on")

    if a in {"tracking_off", "track_off"}:
        return _send_vision_command("tracking_off")

    if a == "controls":
        return _text(["v4l2-ctl", "-d", PIXY_DEVICE, "--list-ctrls"], limit=20000)

    if a == "formats":
        return _text(["v4l2-ctl", "-d", PIXY_DEVICE, "--list-formats-ext"], limit=24000)

    if a == "snapshot":
        # Use the already-running vision stream instead of competing with it for /dev/video0.
        initial = _vision_state()
        was_visual = bool(initial.get("visual_session")) and not bool(initial.get("privacy"))
        opened_here = not was_visual
        before_seq = initial.get("camera_frame_seq")
        if opened_here:
            opened = _send_vision_command("open")
            if not opened.get("ok"):
                return opened
        try:
            got = _wait_fresh_vision_frame(before_seq, timeout=5.0)
            if got is None:
                return {"ok": False, "error": "fresh vision snapshot timeout", "vision": _vision_state()}
            state, data = got
            meta = _frame_recognition(state, 1, analyze=bool(analyze))
            meta.update({
                "ok": True,
                "target": socket.gethostname(),
                "camera": PIXY_NAME,
                "source": "vision_stream",
                "bytes": len(data),
                "transient": True,
            })
            return (json.dumps(meta, ensure_ascii=False), Image(data=data, format="jpeg"))
        finally:
            if opened_here:
                _send_vision_command("privacy")

    if a in {"burst", "sequence", "series"}:
        n = int(count)
        gap = int(interval_ms)
        if n < 1 or n > 20:
            return {"ok": False, "error": "count must be 1..20"}
        if gap < 250 or gap > 10000:
            return {"ok": False, "error": "interval_ms must be 250..10000"}
        initial = _vision_state()
        was_visual = bool(initial.get("visual_session")) and not bool(initial.get("privacy"))
        opened_here = not was_visual
        if opened_here:
            opened = _send_vision_command("open")
            if not opened.get("ok"):
                return opened
        try:
            meta, frames = _capture_series(count=n, interval_ms=gap, analyze=bool(analyze))
            if not frames:
                return {"ok": False, "error": "no burst frames captured", "frames": meta}
            labels = [f"#{i+1}" for i in range(len(frames))]
            sheet = _contact_sheet(frames, labels)
            payload = {
                "ok": True,
                "mode": "burst",
                "count": n,
                "interval_ms": gap,
                "analyze": bool(analyze),
                "frames": meta,
                "summary": _series_summary(meta),
                "contact_sheet": {"frames": len(frames), "bytes": len(sheet)},
                "transient": True,
                "archive_created": False,
            }
            return (json.dumps(payload, ensure_ascii=False), Image(data=sheet, format="jpeg"))
        finally:
            if opened_here:
                _send_vision_command("privacy")

    if a in {"scan", "room_scan", "ptz_scan"}:
        positions = list(scan_degrees) if scan_degrees is not None else [-30, 0, 30]
        if not positions or len(positions) > 7:
            return {"ok": False, "error": "scan_degrees must contain 1..7 positions"}
        try:
            positions = [int(v) for v in positions]
        except Exception:
            return {"ok": False, "error": "scan_degrees must contain integers"}
        if any(v < -90 or v > 90 for v in positions):
            return {"ok": False, "error": "scan_degrees positions must be within -90..90"}
        tilt_deg = int(scan_tilt_degrees)
        if tilt_deg < -45 or tilt_deg > 45:
            return {"ok": False, "error": "scan_tilt_degrees must be -45..45"}
        settle = int(settle_ms)
        if settle < 300 or settle > 3000:
            return {"ok": False, "error": "settle_ms must be 300..3000"}
        initial = _vision_state()
        was_visual = bool(initial.get("visual_session")) and not bool(initial.get("privacy"))
        initial_tracking = _tracking_is_on(initial)
        original_ptz = _current_ptz()
        if not was_visual:
            opened = _send_vision_command("open")
            if not opened.get("ok"):
                return opened
        held = _send_vision_command("tracking_off")
        if not held.get("ok"):
            if not was_visual:
                _send_vision_command("privacy")
            return held
        metas: list[dict[str, Any]] = []
        frames: list[bytes] = []
        previous: bytes | None = None
        try:
            seq = _vision_state().get("camera_frame_seq")
            for idx, deg in enumerate(positions, 1):
                raw_pan = deg * 3600
                raw_tilt = tilt_deg * 3600
                setting = f"pan_absolute={raw_pan},tilt_absolute={raw_tilt}"
                if zoom is not None:
                    zv = int(zoom)
                    if zv < 100 or zv > 150:
                        return {"ok": False, "error": "zoom must be 100..150"}
                    setting += f",zoom_absolute={zv}"
                moved = _text(["v4l2-ctl", "-d", PIXY_DEVICE, "--set-ctrl=" + setting])
                if not moved.get("ok"):
                    metas.append({"index": idx, "ok": False, "pan_deg": deg, "error": moved.get("error")})
                    continue
                time.sleep(settle / 1000.0)
                got = _wait_fresh_vision_frame(seq, timeout=5.0)
                if got is None:
                    metas.append({"index": idx, "ok": False, "pan_deg": deg, "error": "fresh vision frame timeout"})
                    continue
                state, raw = got
                seq = state.get("camera_frame_seq")
                item = _frame_recognition(state, idx, analyze=bool(analyze))
                item.update({"ok": True, "pan_deg": deg, "tilt_deg": tilt_deg, "change_from_previous_pct": _image_change_pct(previous, raw)})
                metas.append(item)
                frames.append(raw)
                previous = raw
            if not frames:
                return {"ok": False, "error": "no scan frames captured", "frames": metas}
            labels = [f"{m.get('pan_deg', '?')} deg" for m in metas if m.get("ok")]
            sheet = _contact_sheet(frames, labels)
            payload = {
                "ok": True,
                "mode": "room_scan",
                "positions_deg": positions,
                "tilt_deg": tilt_deg,
                "settle_ms": settle,
                "analyze": bool(analyze),
                "frames": metas,
                "summary": _series_summary(metas),
                "contact_sheet": {"frames": len(frames), "bytes": len(sheet)},
                "transient": True,
                "archive_created": False,
            }
            return (json.dumps(payload, ensure_ascii=False), Image(data=sheet, format="jpeg"))
        finally:
            if not was_visual:
                _send_vision_command("privacy")
            else:
                if original_ptz:
                    values = []
                    for key in ("pan_absolute", "tilt_absolute", "zoom_absolute"):
                        if key in original_ptz:
                            values.append(f"{key}={original_ptz[key]}")
                    if values:
                        _text(["v4l2-ctl", "-d", PIXY_DEVICE, "--set-ctrl=" + ",".join(values)])
                _send_vision_command("tracking_on" if initial_tracking else "tracking_off")

    if a == "center":
        # Safe high-level wake/center command: open visual session, establish the
        # canonical forward pose, and make sure verified group-01 tracking is ON.
        opened = _send_vision_command("open")
        if not opened.get("ok"):
            return opened
        setr = _text([
            "v4l2-ctl", "-d", PIXY_DEVICE,
            "--set-ctrl=pan_absolute=0,tilt_absolute=0,zoom_absolute=100",
        ])
        if not setr.get("ok"):
            return setr
        tracking = _send_vision_command("tracking_on")
        return {
            "ok": bool(tracking.get("ok")),
            "mode": "visual_tracking",
            "open": opened,
            "tracking": tracking,
            "controls": _text([
                "v4l2-ctl", "-d", PIXY_DEVICE,
                "--get-ctrl=pan_absolute,tilt_absolute,zoom_absolute",
            ]),
        }

    if a == "ptz":
        # Calling PTZ without coordinates is a safe HOLD command: keep the visual
        # session as-is but disable native tracking so manual motor commands do not
        # fight the camera's own target follower.
        if pan is None and tilt is None and zoom is None:
            return _send_vision_command("tracking_off")

        pv = int(pan) if pan is not None else None
        tv = int(tilt) if tilt is not None else None
        zv = int(zoom) if zoom is not None else None

        # Canonical parked pose means privacy, not merely pointing downward. This
        # closes video and tracking while leaving the PIXY microphone alive.
        if pv == 0 and tv == 324000 and (zv is None or zv == 100):
            return _send_vision_command("privacy")

        settings: list[str] = []
        if pv is not None:
            if pv < -540000 or pv > 540000 or pv % 3600:
                return {"ok": False, "error": "pan must be -540000..540000, step 3600"}
            settings.append(f"pan_absolute={pv}")
        if tv is not None:
            if tv < -324000 or tv > 324000 or tv % 3600:
                return {"ok": False, "error": "tilt must be -324000..324000, step 3600"}
            settings.append(f"tilt_absolute={tv}")
        if zv is not None:
            if zv < 100 or zv > 150:
                return {"ok": False, "error": "zoom must be 100..150"}
            settings.append(f"zoom_absolute={zv}")

        # A manual PTZ move is a visual operation. Open the camera first, then hold
        # tracking OFF while the requested bounded movement is applied.
        opened = _send_vision_command("open")
        if not opened.get("ok"):
            return opened
        held = _send_vision_command("tracking_off")
        if not held.get("ok"):
            return held
        setr = _text(["v4l2-ctl", "-d", PIXY_DEVICE, "--set-ctrl=" + ",".join(settings)])
        if not setr.get("ok"):
            return setr
        return {
            "ok": True,
            "mode": "manual_ptz",
            "tracking": held,
            "controls": _text([
                "v4l2-ctl", "-d", PIXY_DEVICE,
                "--get-ctrl=pan_absolute,tilt_absolute,zoom_absolute",
            ]),
            "vision": _vision_state(),
        }

    if a == "focus":
        settings: list[str] = []
        if autofocus is not None:
            settings.append(f"focus_automatic_continuous={1 if autofocus else 0}")
        if focus is not None:
            v = int(focus)
            if v < 0 or v > 1023:
                return {"ok": False, "error": "focus must be 0..1023"}
            if autofocus is not False:
                return {"ok": False, "error": "manual focus requires autofocus=false"}
            settings.append(f"focus_absolute={v}")
        if not settings:
            return {"ok": False, "error": "focus requires autofocus and/or focus"}
        setr = _text(["v4l2-ctl", "-d", PIXY_DEVICE, "--set-ctrl=" + ",".join(settings)])
        if not setr.get("ok"):
            return setr
        return _text([
            "v4l2-ctl", "-d", PIXY_DEVICE,
            "--get-ctrl=focus_automatic_continuous,focus_absolute",
        ])

    return {"ok": False, "error": "unsupported action"}


@mcp.tool()
def doctor_ingest_incident(
    title: str,
    source: str,
    symptoms: str,
    evidence: str,
    root_cause: str = "",
    scope: str = "CROSS_SYSTEM",
    status: str = "UNRESOLVED",
    confidence: float = 0.5,
    fingerprint: list[str] | None = None,
    hypotheses: list[str] | None = None,
    diagnostics: list[str] | None = None,
    failed_attempts: list[str] | None = None,
    fix: str = "",
    verify: list[str] | None = None,
    rollback: str = "",
    risk: str = "MEDIUM",
    automation: str = "DIAGNOSTIC_ONLY",
    source_date: str = "",
    notes: str = "",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Submit one structured external incident to Suzie Doctor. Default dry_run validates and deduplicates without changing the Master KB. With dry_run=false the protected Doctor worker appends only after validation, recompiles and normalizes knowledge, and returns a bounded result. This tool cannot read/export the Master KB, licenses, keys, files, or execute shell commands."""
    request_id = uuid.uuid4().hex
    request = {
        "request_id": request_id,
        "dry_run": bool(dry_run),
        "incident": {
            "title": str(title),
            "source": str(source),
            "symptoms": str(symptoms),
            "evidence": str(evidence),
            "root_cause": str(root_cause),
            "scope": str(scope),
            "status": str(status),
            "confidence": float(confidence),
            "fingerprint": list(fingerprint or []),
            "hypotheses": list(hypotheses or []),
            "diagnostics": list(diagnostics or []),
            "failed_attempts": list(failed_attempts or []),
            "fix": str(fix),
            "verify": list(verify or []),
            "rollback": str(rollback),
            "risk": str(risk),
            "automation": str(automation),
            "source_date": str(source_date),
            "notes": str(notes),
        },
    }

    # Bound payload before it reaches the privileged Doctor worker.
    raw = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > 65536:
        return {"ok": False, "result": "REJECTED", "error": "incident payload exceeds 64 KiB"}

    try:
        DOCTOR_INGEST_INBOX.mkdir(parents=True, exist_ok=True)
        DOCTOR_INGEST_RESULTS.mkdir(parents=True, exist_ok=True)
        target = DOCTOR_INGEST_INBOX / f"{request_id}.json"
        temp = DOCTOR_INGEST_BASE / f".{request_id}.json.tmp"
        temp.write_text(raw + "\n", encoding="utf-8")
        os.chmod(temp, 0o660)
        temp.replace(target)
    except Exception as exc:
        return {
            "ok": False,
            "result": "QUEUE_ERROR",
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
        }

    result_path = DOCTOR_INGEST_RESULTS / f"{request_id}.json"
    timeout_s = 45.0 if dry_run else 210.0
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception as exc:
                return {
                    "ok": False,
                    "result": "RESULT_ERROR",
                    "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                }
            finally:
                result_path.unlink(missing_ok=True)
            if not isinstance(result, dict):
                return {"ok": False, "result": "RESULT_ERROR"}
            # Never return arbitrary worker data if future versions grow fields.
            allowed = {
                "ok", "result", "dry_run", "incident_id", "duplicate",
                "candidate_diseases", "disease", "unclassified",
                "candidate_diseases_before_ingest", "counts", "validated",
                "error",
            }
            return {k: v for k, v in result.items() if k in allowed}
        time.sleep(0.1)

    return {
        "ok": False,
        "result": "TIMEOUT",
        "error": "Doctor ingest worker did not return within the bounded timeout",
    }


@mcp.tool()
def doctor_curate_incident(
    action: str,
    incident_id: str,
    reason: str,
    target_disease_id: str = "",
    target_incident_id: str = "",
    treatment_classification: str = "",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Curate one already-ingested Suzie Doctor incident through a bounded server-side workflow. Actions: remove_duplicate, attach_existing_disease, keep_unclassified, create_new_disease, classify_treatment. Default dry_run=true. This tool cannot read/export the Master KB, licenses, keys, files, shell, or promote executable Protocols."""
    request_id = uuid.uuid4().hex
    request = {
        "request_id": request_id,
        "dry_run": bool(dry_run),
        "action": str(action),
        "incident_id": str(incident_id),
        "reason": str(reason),
        "target_disease_id": str(target_disease_id),
        "target_incident_id": str(target_incident_id),
        "treatment_classification": str(treatment_classification),
    }
    raw = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > 16384:
        return {"ok": False, "result": "REJECTED", "error": "curation payload exceeds 16 KiB"}

    try:
        DOCTOR_CURATE_INBOX.mkdir(parents=True, exist_ok=True)
        DOCTOR_CURATE_RESULTS.mkdir(parents=True, exist_ok=True)
        target = DOCTOR_CURATE_INBOX / f"{request_id}.json"
        temp = DOCTOR_CURATE_BASE / f".{request_id}.json.tmp"
        temp.write_text(raw + "\n", encoding="utf-8")
        os.chmod(temp, 0o660)
        temp.replace(target)
    except Exception as exc:
        return {
            "ok": False,
            "result": "QUEUE_ERROR",
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
        }

    result_path = DOCTOR_CURATE_RESULTS / f"{request_id}.json"
    timeout_s = 45.0 if dry_run else 210.0
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception as exc:
                return {
                    "ok": False,
                    "result": "RESULT_ERROR",
                    "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                }
            finally:
                result_path.unlink(missing_ok=True)
            if not isinstance(result, dict):
                return {"ok": False, "result": "RESULT_ERROR"}
            allowed = {
                "ok", "result", "dry_run", "action", "incident_id",
                "current_disease_id", "target_disease_id",
                "target_incident_id", "duplicate_proof", "disease_id",
                "treatment_classification", "after_disease_id",
                "counts", "error",
            }
            return {k: v for k, v in result.items() if k in allowed}
        time.sleep(0.1)

    return {
        "ok": False,
        "result": "TIMEOUT",
        "error": "Doctor curation worker did not return within the bounded timeout",
    }


@mcp.tool()
def vision_memory(action: str = "status", limit: int = 20):
    """Safe dialog-scoped PIXY memory. Actions: status, pull, unread, events, dialogs, dialog:<id>, summary:<id>, search:<text>, keyframe:<id>, people, person_add:<name>|<relation>|<aliases>, important:<keyframe_id>, ack:<event_id>. No video archive; only event metadata and selected keyframes from active Suzie dialogs."""
    import json as _json
    import vision_memory_api as _vma
    result = _vma.handle(action, limit)
    if isinstance(result, dict) and "_image_bytes" in result:
        data = result.pop("_image_bytes")
        meta = result.pop("_image_meta", {})
        return (_json.dumps({"ok": True, **meta}, ensure_ascii=False), Image(data=data, format="jpeg"))
    return result



async def _doctor_proxy(
    ctx: Context,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Thin transport adapter to canonical local Doctor MCP.
    It never receives or exposes Doctor Server operator credentials.
    """
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(120.0),
    ) as http_client:
        async with streamable_http_client(
            DOCTOR_MCP_URL,
            http_client=http_client,
            terminate_on_close=True,
        ) as streams:
            read_stream, write_stream, *_ = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    tool_name,
                    dict(arguments or {}),
                )
                payload = result.model_dump(by_alias=True)
                if bool(payload.get("isError")):
                    content = payload.get("content") or []
                    text = "; ".join(
                        str(item.get("text") or "")
                        for item in content
                        if isinstance(item, dict)
                    )
                    raise RuntimeError(
                        "Suzie Doctor tool failed: "
                        + (text[:1000] or tool_name)
                    )
                structured = payload.get("structuredContent")
                if isinstance(structured, dict):
                    return structured
                return {"ok": True, "content": payload.get("content") or []}


@mcp.tool(
    name="doctor.skill",
    description="Canonical Suzie Doctor Skill Core for Web/API Doctor sessions.",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def doctor_skill(ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "doctor.skill")


@mcp.tool(
    name="doctor.capabilities",
    description="Doctor orchestration plus exact claimed-client Connector capabilities.",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def doctor_capabilities(ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "doctor.capabilities")


@mcp.tool(
    name="doctor.queue",
    description="Read the shared Doctor Case queue and active doctor sessions.",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def doctor_queue(ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "doctor.queue")


@mcp.tool(
    name="doctor.case.get",
    description="Read one Case before claiming or treating it.",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def doctor_case_get(case_id: int, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(
        ctx, "doctor.case.get", {"case_id": int(case_id)}
    )


@mcp.tool(
    name="doctor.case.claim",
    description=(
        "Atomically claim one assigned Case. Conflict means another doctor "
        "owns it; stop and do not treat."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def doctor_case_claim(case_id: int, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(
        ctx, "doctor.case.claim", {"case_id": int(case_id)}
    )


@mcp.tool(
    name="doctor.case.heartbeat",
    description="Renew the active claimed Case lease.",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def doctor_case_heartbeat(doctor_handle: str, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "doctor.case.heartbeat", {"doctor_handle": doctor_handle})


@mcp.tool(
    name="doctor.case.stage",
    description="Set active Case stage: CLAIMED, TREATING or VERIFYING.",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def doctor_case_stage(stage: str, doctor_handle: str, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(
        ctx, "doctor.case.stage", {"stage": str(stage), "doctor_handle": doctor_handle}
    )


@mcp.tool(
    name="doctor.case.complete_next",
    description=(
        "Finish verified active Case and atomically take the next waiting "
        "Suzie Case in this same dialog if one exists. Outcome is restricted "
        "to SUCCESS, RESOLVED, HUMAN_REQUIRED, UNSAFE_TO_TREAT, or FAILED."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def doctor_case_complete_next(
    outcome: Literal["SUCCESS", "RESOLVED", "HUMAN_REQUIRED", "UNSAFE_TO_TREAT", "FAILED"],
    result: dict[str, Any],
    doctor_handle: str,
    ctx: Context,
) -> dict[str, Any]:
    return await _doctor_proxy(
        ctx,
        "doctor.case.complete_next",
        {"outcome": str(outcome), "result": dict(result or {}), "doctor_handle": doctor_handle},
    )


@mcp.tool(
    name="doctor.command.get",
    description="Read one already-issued exact-client command result.",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def doctor_command_get(
    command_id: str,
    doctor_handle: str,
    ctx: Context,
) -> dict[str, Any]:
    return await _doctor_proxy(
        ctx, "doctor.command.get", {"command_id": str(command_id), "doctor_handle": doctor_handle}
    )


@mcp.tool(
    name="doctor.suite",
    description="Read live Suite compatibility from the exact claimed client.",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def doctor_suite(doctor_handle: str, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "doctor.suite", {"doctor_handle": doctor_handle})


@mcp.tool(
    name="doctor.v2.state",
    description="Read Doctor Server v2 House/Wilson/4+1+1/10x10 state.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_v2_state(ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "doctor.v2.state", {})


@mcp.tool(
    name="doctor.house.job.get",
    description="Read one claimed Doctor House job with Patient Card, matched Experimental 0/3-2/3 candidates, and recent journal events.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_house_job_get(job_id: int, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "doctor.house.job.get", {"job_id": int(job_id)})


@mcp.tool(
    name="doctor.house.decision",
    description="Complete one Doctor House job with a canonical House decision.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
async def doctor_house_decision(
    job_id: int,
    finding_class: str,
    significance: str,
    decision: str,
    ctx: Context,
    field_priority: str = "NORMAL",
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _doctor_proxy(
        ctx,
        "doctor.house.decision",
        {
            "job_id": int(job_id),
            "finding_class": str(finding_class),
            "significance": str(significance),
            "decision": str(decision),
            "field_priority": str(field_priority),
            "result": dict(result or {}),
        },
    )


@mcp.tool(
    name="doctor.wilson.job.get",
    description="Read one claimed Doctor Wilson job.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_wilson_job_get(job_id: int, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "doctor.wilson.job.get", {"job_id": int(job_id)})


@mcp.tool(
    name="doctor.wilson.complete",
    description="Complete one Doctor Wilson job and commit its cursor after successful knowledge work.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
async def doctor_wilson_complete(
    job_id: int,
    result: dict[str, Any],
    ctx: Context,
    output_cursor: str = "",
    failed: bool = False,
) -> dict[str, Any]:
    return await _doctor_proxy(
        ctx,
        "doctor.wilson.complete",
        {
            "job_id": int(job_id),
            "result": dict(result or {}),
            "output_cursor": str(output_cursor or ""),
            "failed": bool(failed),
        },
    )


@mcp.tool(
    name="ha.config.read",
    description="Read Home Assistant config from the exact claimed Case client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_ha_config_read(doctor_handle: str, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "ha.config.read", {"doctor_handle": doctor_handle})


@mcp.tool(
    name="ha.repairs.list",
    description="Read active Home Assistant Repairs from the exact claimed client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_ha_repairs_list(doctor_handle: str, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "ha.repairs.list", {"doctor_handle": doctor_handle})


@mcp.tool(
    name="ha.notifications.list",
    description="Read persistent notifications from the exact claimed client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_ha_notifications_list(doctor_handle: str, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "ha.notifications.list", {"doctor_handle": doctor_handle})


@mcp.tool(
    name="ha.config_entries.list",
    description="Read config entries from the exact claimed client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_ha_config_entries_list(
    doctor_handle: str,
    ctx: Context,
    domain: str = "",
) -> dict[str, Any]:
    return await _doctor_proxy(
        ctx, "ha.config_entries.list", {"domain": str(domain), "doctor_handle": doctor_handle}
    )


@mcp.tool(
    name="supervisor.info",
    description="Read Supervisor info from the exact claimed client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_supervisor_info(doctor_handle: str, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "supervisor.info", {"doctor_handle": doctor_handle})


@mcp.tool(
    name="supervisor.host.info",
    description="Read host info from the exact claimed client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_supervisor_host_info(doctor_handle: str, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "supervisor.host.info", {"doctor_handle": doctor_handle})


@mcp.tool(
    name="supervisor.core.info",
    description="Read HA Core info from the exact claimed client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_supervisor_core_info(doctor_handle: str, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "supervisor.core.info", {"doctor_handle": doctor_handle})


@mcp.tool(
    name="supervisor.network.info",
    description="Read network info from the exact claimed client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_supervisor_network_info(doctor_handle: str, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "supervisor.network.info", {"doctor_handle": doctor_handle})


@mcp.tool(
    name="supervisor.addons.list",
    description="Read HA app/add-on inventory from the exact claimed client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_supervisor_addons_list(doctor_handle: str, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "supervisor.addons.list", {"doctor_handle": doctor_handle})


@mcp.tool(
    name="supervisor.mounts.list",
    description="Read Supervisor mounts from the exact claimed client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_supervisor_mounts_list(doctor_handle: str, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "supervisor.mounts.list", {"doctor_handle": doctor_handle})


@mcp.tool(
    name="supervisor.backups.list",
    description="Read Supervisor backups from the exact claimed client.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
async def doctor_supervisor_backups_list(doctor_handle: str, ctx: Context) -> dict[str, Any]:
    return await _doctor_proxy(ctx, "supervisor.backups.list", {"doctor_handle": doctor_handle})


@mcp.tool(
    name="doctor.action.request",
    description=(
        "Field-Suzie only signed one-shot structured action on the exact claimed client. "
        "Requires risk assessment and mandatory functional verify criterion."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
)
async def doctor_action_request(
    action: dict[str, Any],
    exact_target: dict[str, Any],
    reason: str,
    evidence: dict[str, Any],
    risk_assessment: dict[str, Any],
    expected_result: str,
    verify_criterion: dict[str, Any],
    doctor_handle: str,
    ctx: Context,
    checkpoint: dict[str, Any] | None = None,
    rollback: list[dict[str, Any]] | None = None,
    fallback: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    args = {
        "doctor_handle": doctor_handle,
        "action": dict(action or {}),
        "exact_target": dict(exact_target or {}),
        "reason": str(reason or ""),
        "evidence": dict(evidence or {}),
        "risk_assessment": dict(risk_assessment or {}),
        "expected_result": str(expected_result or ""),
        "verify_criterion": dict(verify_criterion or {}),
        "checkpoint": dict(checkpoint) if isinstance(checkpoint, dict) else None,
        "rollback": list(rollback or []),
        "fallback": list(fallback or []),
    }
    return await _doctor_proxy(ctx, "doctor.action.request", args)


@mcp.tool(
    name="doctor.diagnose",
    description=(
        "Consult/execute the signed Doctor protocol on the exact claimed client. "
        "For House VALIDATE_FIRST Cases, experimental_protocol_id stays bound to the "
        "active Case and may yield a Field-only signed EXPERIMENTAL package. execute=true "
        "requires the structured autonomous risk_assessment made by Suzie Doctor; this "
        "transport does not make that judgment."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def doctor_diagnose(
    evidence: dict[str, Any],
    doctor_handle: str,
    ctx: Context,
    execute: bool = False,
    risk_assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if execute and not isinstance(risk_assessment, dict):
        raise RuntimeError("execute=true requires Suzie Doctor risk_assessment")
    args: dict[str, Any] = {
        "evidence": dict(evidence or {}),
        "execute": bool(execute),
        "doctor_handle": doctor_handle,
    }
    if isinstance(risk_assessment, dict):
        args["risk_assessment"] = dict(risk_assessment)
    return await _doctor_proxy(ctx, "doctor.diagnose", args)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
