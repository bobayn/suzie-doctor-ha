#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import random
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import websocket

LAB = Path("/home/bobayn/suzie-doctor-call-lab")
CONFIG_PATH = LAB / "config.json"
LOG_PATH = LAB / "jobs.jsonl"
HOST = "0.0.0.0"
PORT = 8795
MOCK_URL = f"http://192.168.0.105:{PORT}/mock-chat"
CDP_BASE = "http://127.0.0.1:9223"
OPEN_TAB = str(LAB / "open_tab.sh")

LOCK = threading.RLock()
ACCESSIBILITY_LOCK = threading.Lock()
JOBS: dict[str, dict[str, Any]] = {}

DEFAULT_CONFIG = {
    "project_url": "",
    "house_url": "",
    "wilson_url": "",
    "scheduler_url": "",
    "mock_url": MOCK_URL,
    "max_parallel_web_doctors": 6,
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text())
        if not isinstance(data, dict):
            raise ValueError
    except Exception:
        data = {}
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    return merged


def save_config(data: dict[str, Any]) -> None:
    current = load_config()
    current.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, CONFIG_PATH)


def append_log(entry: dict[str, Any]) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


def new_job(method: str, text: str, target: str, target_url: str = "", apps: list[str] | None = None) -> dict[str, Any]:
    jid = f"{int(time.time() * 1000)}-{random.randrange(1000, 9999)}"
    job = {
        "job_id": jid,
        "method": method,
        "text": text,
        "target": target,
        "target_url": target_url,
        "apps": list(apps or []),
        "state": "queued",
        "created_at": now(),
        "updated_at": now(),
        "detail": {},
    }
    with LOCK:
        JOBS[jid] = job
        append_log(dict(job))
    return job


def update_job(job_id: str, state: str, detail: dict[str, Any] | None = None) -> None:
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job["state"] = state
        job["updated_at"] = now()
        if detail:
            job["detail"] = {**job.get("detail", {}), **detail}
        append_log(dict(job))


def resolve_target(target_kind: str, target_url: str = "") -> str:
    cfg = load_config()
    if target_kind == "mock":
        return str(cfg["mock_url"])
    key = {
        "project": "project_url",
        "house": "house_url",
        "wilson": "wilson_url",
        "scheduler": "scheduler_url",
    }.get(target_kind)
    if key:
        url = str(cfg.get(key) or "").strip()
        if not url.startswith("https://chatgpt.com/"):
            raise ValueError(f"ChatGPT URL is not configured for {target_kind}")
        return url
    if target_kind == "dialog":
        url = str(target_url or "").strip()
        if not url.startswith("https://chatgpt.com/"):
            raise ValueError("dialog URL must be on https://chatgpt.com/")
        return url
    raise ValueError("bad target")


def wait_cdp(timeout: float = 12.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(CDP_BASE + "/json/version", timeout=1.5) as r:
                if r.status == 200:
                    return
        except Exception as exc:
            last = exc
        time.sleep(0.25)
    raise RuntimeError(f"CDP not ready: {last}")


def cdp_new_target(url: str) -> dict[str, Any]:
    req = urllib.request.Request(
        CDP_BASE + "/json/new?" + urllib.parse.quote(url, safe=""),
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def cdp_close_target(target_id: str) -> bool:
    value = str(target_id or "").strip()
    if not value:
        return False
    try:
        req = urllib.request.Request(
            CDP_BASE + "/json/close/" + urllib.parse.quote(value, safe=""),
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=4) as r:
            return 200 <= int(r.status) < 300
    except Exception:
        return False


def cdp_fill(job_id: str, target: str, text: str, apps: list[str] | None = None) -> None:
    apps = list(apps or [])
    page_id = ""
    ws = None
    try:
        wait_cdp()
        page = cdp_new_target(target)
        page_id = str(page.get("id") or "")
        ws_url = page.get("webSocketDebuggerUrl")
        if not ws_url:
            raise RuntimeError("no page websocket URL")
        update_job(job_id, "tab_created", {"tab_id": page.get("id"), "url": target})

        ws = websocket.create_connection(
            ws_url,
            timeout=12,
            origin="http://127.0.0.1:9223",
        )
        seq = 0

        def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
            nonlocal seq
            seq += 1
            current = seq
            ws.send(json.dumps({"id": current, "method": method, "params": params}))
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                msg = json.loads(raw)
                if msg.get("id") == current:
                    return msg
            raise TimeoutError(method)

        call("Runtime.enable", {})
        call("Page.bringToFront", {})

        composer_ready_expression = """
(() => {
  const selectors = [
    '#prompt-textarea',
    "textarea[data-id='root']",
    "textarea[placeholder*='Message']",
    "textarea[placeholder*='Сообщ']",
    "textarea",
    "div[contenteditable='true'][data-placeholder]",
    "div[contenteditable='true']"
  ];
  let el = null;
  for (const s of selectors) {
    for (const n of document.querySelectorAll(s)) {
      const r = n.getBoundingClientRect();
      const st = getComputedStyle(n);
      const visible = r.width > 20 && r.height > 10 &&
                      st.display !== 'none' &&
                      st.visibility !== 'hidden' &&
                      st.opacity !== '0';
      const editable =
        (!('disabled' in n) || !n.disabled) &&
        (n instanceof HTMLTextAreaElement ||
         n instanceof HTMLInputElement ||
         n.getAttribute('contenteditable') === 'true');
      if (visible && editable && n.isConnected) {
        el = n;
        break;
      }
    }
    if (el) break;
  }
  return {
    ready: document.readyState === 'complete' && !!el,
    documentReady: document.readyState,
    found: !!el,
    tag: el ? el.tagName : null,
    id: el ? (el.id || '') : null,
    className: el ? String(el.className || '') : null,
    url: location.href,
    title: document.title
  };
})()
"""
        composer_deadline = time.time() + 60
        composer_stable = 0
        composer_last = None
        while time.time() < composer_deadline:
            res = call("Runtime.evaluate", {
                "expression": composer_ready_expression,
                "returnByValue": True,
            })
            composer_last = (
                res.get("result", {})
                .get("result", {})
                .get("value")
            )
            if isinstance(composer_last, dict) and composer_last.get("ready"):
                composer_stable += 1
                if composer_stable >= 5:
                    break
            else:
                composer_stable = 0
            time.sleep(0.25)

        if composer_stable < 5:
            raise RuntimeError(f"composer_not_stably_ready: {composer_last}")

        focus_expression = """
(() => {
  const el =
    document.querySelector('#prompt-textarea') ||
    document.querySelector("textarea[data-id='root']") ||
    document.querySelector("textarea") ||
    document.querySelector("div[contenteditable='true']");
  if (!el) return {ok:false, reason:'composer_missing_at_focus'};
  el.focus();
  if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
    el.select();
  } else {
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);
  }
  return {
    ok:true,
    url:location.href,
    title:document.title,
    tag:el.tagName,
    id:el.id || '',
    role:el.getAttribute('role') || ''
  };
})()
"""
        focus_res = call("Runtime.evaluate", {
            "expression": focus_expression,
            "returnByValue": True,
        })
        focus_value = (
            focus_res.get("result", {})
            .get("result", {})
            .get("value")
        )
        if not isinstance(focus_value, dict) or not focus_value.get("ok"):
            raise RuntimeError(f"composer_focus_failed: {focus_value}")

        before_url = str(focus_value.get("url") or target)

        call("Input.insertText", {"text": text})

        fill_verify_expression = """
(() => {
  const el =
    document.querySelector('#prompt-textarea') ||
    document.querySelector("textarea[data-id='root']") ||
    document.querySelector("textarea") ||
    document.querySelector("div[contenteditable='true']");
  const actual = el ? (('value' in el) ? el.value : el.textContent) : '';
  return {
    ok: actual === TEXT_PLACEHOLDER,
    actual,
    url:location.href,
    title:document.title,
    tag:el ? el.tagName : null,
    id:el ? (el.id || '') : null,
    role:el ? (el.getAttribute('role') || '') : null
  };
})()
""".replace("TEXT_PLACEHOLDER", json.dumps(text))

        fill_deadline = time.time() + 10
        fill_value = None
        while time.time() < fill_deadline:
            fill_res = call("Runtime.evaluate", {
                "expression": fill_verify_expression,
                "returnByValue": True,
            })
            fill_value = (
                fill_res.get("result", {})
                .get("result", {})
                .get("value")
            )
            if isinstance(fill_value, dict) and fill_value.get("ok"):
                break
            time.sleep(0.2)

        if not isinstance(fill_value, dict) or not fill_value.get("ok"):
            raise RuntimeError(f"native_input_not_verified: {fill_value}")

        update_job(job_id, "filled", {
            **fill_value,
            "input": "cdp_Input.insertText",
            "composer_ready_stable_checks": composer_stable,
        })

        # Attach ChatGPT apps as real app mentions. Typing an app name in the
        # prompt is not sufficient; the suggestion must be selected so the
        # composer contains an app-mention element.
        attached_apps = []
        for app in apps:
            # ChatGPT 2026-09 UI: private plugins are selected through the
            # composer's + menu search rather than the first visible app list.
            picker_js = """
(() => {
  const b = document.querySelector('[data-testid="composer-plus-btn"]') ||
            [...document.querySelectorAll('button')].find(x => /Добавить файлы и другое|Add files|Add/i.test(x.getAttribute('aria-label') || ''));
  if (!b) return {ok:false, reason:'plus_button_not_found'};
  const r=b.getBoundingClientRect();
  const st=getComputedStyle(b);
  const visible=r.width>0 && r.height>0 && st.display!=='none' && st.visibility!=='hidden' && st.opacity!=='0';
  if (!visible || b.disabled || b.getAttribute('aria-disabled')==='true') return {ok:false, reason:'plus_button_not_ready'};
  return {ok:true, x:r.left+r.width/2, y:r.top+r.height/2, expanded:b.getAttribute('aria-expanded')};
})()
"""
            pr = call("Runtime.evaluate", {"expression": picker_js, "returnByValue": True})
            pv = pr.get("result", {}).get("result", {}).get("value")
            if not isinstance(pv, dict) or not pv.get("ok"):
                raise RuntimeError(f"plugin_picker_open_failed:{app}:{pv}")
            call("Input.dispatchMouseEvent", {"type":"mouseMoved","x":pv["x"],"y":pv["y"]})
            call("Input.dispatchMouseEvent", {"type":"mousePressed","x":pv["x"],"y":pv["y"],"button":"left","buttons":1,"clickCount":1})
            call("Input.dispatchMouseEvent", {"type":"mouseReleased","x":pv["x"],"y":pv["y"],"button":"left","buttons":0,"clickCount":1})
            time.sleep(0.6)

            # The opened + menu focuses its plugin/file/skill search field.
            # Feed the private plugin name there with a trusted CDP text input.
            call("Input.insertText", {"text": app})
            time.sleep(0.9)

            result_js = """
(() => {
  const wanted = APP_PLACEHOLDER;
  const visible = x => {
    const r=x.getBoundingClientRect(), st=getComputedStyle(x);
    return r.width>0 && r.height>0 && st.display!=='none' && st.visibility!=='hidden' && st.opacity!=='0';
  };
  const spans=[...document.querySelectorAll('span')].filter(visible).filter(x => (x.innerText || x.textContent || '').trim() === wanted);
  for (const s of spans) {
    const item = s.closest('[tabindex="0"][data-fill]') || s.closest('[tabindex="0"]') || s.closest('.__menu-item');
    if (item && visible(item)) {
      const r=item.getBoundingClientRect();
      return {ok:true, x:r.left+r.width/2, y:r.top+r.height/2, tag:item.tagName, text:(item.innerText||item.textContent||'').trim()};
    }
  }
  const labels=[...document.querySelectorAll('[data-fill],.__menu-item')].filter(visible)
      .map(x => (x.innerText || x.textContent || '').trim()).filter(Boolean).slice(-40);
  return {ok:false, reason:'plugin_search_result_not_found', wanted, labels};
})()
""".replace("APP_PLACEHOLDER", json.dumps(app))
            rr = call("Runtime.evaluate", {"expression": result_js, "returnByValue": True})
            selected = rr.get("result", {}).get("result", {}).get("value")
            if not isinstance(selected, dict) or not selected.get("ok"):
                raise RuntimeError(f"plugin_search_failed:{app}:{selected}")
            call("Input.dispatchMouseEvent", {"type":"mouseMoved","x":selected["x"],"y":selected["y"]})
            call("Input.dispatchMouseEvent", {"type":"mousePressed","x":selected["x"],"y":selected["y"],"button":"left","buttons":1,"clickCount":1})
            call("Input.dispatchMouseEvent", {"type":"mouseReleased","x":selected["x"],"y":selected["y"],"button":"left","buttons":0,"clickCount":1})
            time.sleep(0.4)

            verify_app_js = """
(() => {
  const wanted = APP_PLACEHOLDER;
  const oldMention = [...document.querySelectorAll('[app-mention-display-name]')].some(x => x.getAttribute('app-mention-display-name') === wanted);
  const newPill = [...document.querySelectorAll('a[href*="/plugins/"][href*="plugin_detail_origin=inline_selection_pill"]')].some(x => (x.innerText || x.textContent || '').trim() === wanted);
  return oldMention || newPill;
})()
""".replace("APP_PLACEHOLDER", json.dumps(app))
            verified = False
            for _ in range(30):
                vr = call("Runtime.evaluate", {"expression": verify_app_js, "returnByValue": True})
                verified = bool(vr.get("result", {}).get("result", {}).get("value"))
                if verified:
                    break
                time.sleep(0.15)
            if not verified:
                raise RuntimeError(f"plugin_selection_not_verified:{app}")
            attached_apps.append(app)
        if attached_apps:
            update_job(job_id, "apps_attached", {"apps": attached_apps})

        expected_apps_js = json.dumps(apps, ensure_ascii=False)
        text_condition = (
            "v === TEXT_PLACEHOLDER" if not apps
            else "v.startsWith(TEXT_PLACEHOLDER) && EXPECTED_APPS.every(a => mentions.includes(a))"
        )

        ready_expression = """
(() => {
  const el =
    document.querySelector('#prompt-textarea') ||
    document.querySelector("textarea[data-id='root']") ||
    document.querySelector("textarea") ||
    document.querySelector("div[contenteditable='true']");
  const b =
    document.querySelector('[data-testid="send-button"]') ||
    document.querySelector('button[aria-label="Send"]') ||
    document.querySelector('button[aria-label="Send message"]') ||
    document.querySelector('button[aria-label="Отправить"]') ||
    document.querySelector('button[aria-label="Отправить сообщение"]') ||
    document.querySelector('button[aria-label="Надіслати"]') ||
    document.querySelector('button[aria-label="Надіслати повідомлення"]') ||
    [...document.querySelectorAll('button')].find(x => {
      const label = (x.getAttribute('aria-label') || '').trim().toLowerCase();
      return [
        'send', 'send message',
        'отправить', 'отправить сообщение',
        'надіслати', 'надіслати повідомлення'
      ].includes(label);
    });
  const r = b ? b.getBoundingClientRect() : null;
  const st = b ? getComputedStyle(b) : null;
  const visible = !!b && !!r && r.width > 0 && r.height > 0 &&
          st.display !== 'none' &&
          st.visibility !== 'hidden' &&
          st.opacity !== '0';
  const enabled = !!b && !b.disabled && b.getAttribute('aria-disabled') !== 'true';
  const v = el ? (('value' in el) ? el.value : el.textContent) : '';
  const mentions = [
    ...[...document.querySelectorAll('[app-mention-display-name]')].map(x => x.getAttribute('app-mention-display-name')),
    ...[...document.querySelectorAll('a[href*="/plugins/"][href*="plugin_detail_origin=inline_selection_pill"]')].map(x => (x.innerText || x.textContent || '').trim())
  ].filter(Boolean);
  const EXPECTED_APPS = EXPECTED_APPS_PLACEHOLDER;
  return {
    found: !!b,
    visible,
    enabled,
    textStillCorrect: TEXT_CONDITION_PLACEHOLDER,
    ariaDisabled: b ? b.getAttribute('aria-disabled') : null
  };
})()
""".replace("TEXT_CONDITION_PLACEHOLDER", text_condition).replace("TEXT_PLACEHOLDER", json.dumps(text)).replace("EXPECTED_APPS_PLACEHOLDER", expected_apps_js)

        ready_deadline = time.time() + 45
        stable_ready = 0
        last_ready = None
        while time.time() < ready_deadline:
            ready_res = call("Runtime.evaluate", {
                "expression": ready_expression,
                "returnByValue": True,
            })
            last_ready = (
                ready_res.get("result", {})
                .get("result", {})
                .get("value")
            )
            if (
                isinstance(last_ready, dict)
                and last_ready.get("found")
                and last_ready.get("visible")
                and last_ready.get("enabled")
                and last_ready.get("textStillCorrect")
            ):
                stable_ready += 1
                if stable_ready >= 3:
                    break
            else:
                stable_ready = 0
            time.sleep(0.25)

        if stable_ready < 3:
            raise RuntimeError(f"send_not_stably_ready: {last_ready}")

        update_job(job_id, "send_ready", {
            "stable_checks": stable_ready,
            "ready": last_ready,
        })

        submit_expression = """
(() => {
  const b =
    document.querySelector('[data-testid="send-button"]') ||
    document.querySelector('button[aria-label="Send"]') ||
    document.querySelector('button[aria-label="Send message"]') ||
    document.querySelector('button[aria-label="Отправить"]') ||
    document.querySelector('button[aria-label="Отправить сообщение"]') ||
    document.querySelector('button[aria-label="Надіслати"]') ||
    document.querySelector('button[aria-label="Надіслати повідомлення"]') ||
    [...document.querySelectorAll('button')].find(x => {
      const label = (x.getAttribute('aria-label') || '').trim().toLowerCase();
      return [
        'send', 'send message',
        'отправить', 'отправить сообщение',
        'надіслати', 'надіслати повідомлення'
      ].includes(label);
    });
  if (!b) return {ok:false, reason:'send_button_disappeared'};
  const r = b.getBoundingClientRect();
  const st = getComputedStyle(b);
  const visible = r.width > 0 && r.height > 0 &&
          st.display !== 'none' &&
          st.visibility !== 'hidden' &&
          st.opacity !== '0';
  const enabled = !b.disabled && b.getAttribute('aria-disabled') !== 'true';
  if (!visible || !enabled) {
    return {ok:false, reason:'send_button_not_ready_at_click', visible, enabled};
  }
  return {ok:true, x:r.left + r.width/2, y:r.top + r.height/2};
})()
"""
        submit_res = call("Runtime.evaluate", {
            "expression": submit_expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        submit_value = (
            submit_res.get("result", {})
            .get("result", {})
            .get("value")
        )
        if not isinstance(submit_value, dict) or not submit_value.get("ok"):
            raise RuntimeError(f"send click failed: {submit_value}")
        x = float(submit_value.get("x") or 0)
        y = float(submit_value.get("y") or 0)
        if x <= 0 or y <= 0:
            raise RuntimeError(f"invalid send button coordinates: {submit_value}")
        # CDP-dispatched mouse input is browser-native/trusted input; DOM .click()
        # may clear the React composer without actually creating a conversation.
        call("Input.dispatchMouseEvent", {"type":"mouseMoved","x":x,"y":y})
        call("Input.dispatchMouseEvent", {"type":"mousePressed","x":x,"y":y,"button":"left","clickCount":1})
        call("Input.dispatchMouseEvent", {"type":"mouseReleased","x":x,"y":y,"button":"left","clickCount":1})

        verify_expression = f"""
(() => {{
  const el =
    document.querySelector('#prompt-textarea') ||
    document.querySelector("textarea[data-id='root']") ||
    document.querySelector("textarea") ||
    document.querySelector("div[contenteditable='true']");
  const v = el ? (('value' in el) ? el.value : el.textContent) : '';
  const expected={json.dumps(text)};
  const prefix=expected.slice(0, Math.min(180, expected.length));
  const userTexts=[...document.querySelectorAll('[data-message-author-role="user"]')].map(x => (x.innerText || x.textContent || ''));
  return {{
    url:location.href,
    composer:v || '',
    textVisibleInConversation:userTexts.some(t => t.includes(expected) || (prefix && t.includes(prefix))),
    conversationCreated:location.href !== {json.dumps(before_url)} && location.pathname.includes('/c/'),
    userMessageCount:userTexts.length
  }};
}})()
"""
        verified = None
        verify_started = time.time()
        verify_deadline = verify_started + 15
        keyboard_fallback_sent = False
        while time.time() < verify_deadline:
            check = call("Runtime.evaluate", {
                "expression": verify_expression,
                "returnByValue": True,
            })
            verified = (
                check.get("result", {})
                .get("result", {})
                .get("value")
            )
            if isinstance(verified, dict):
                composer_released = str(verified.get("composer") or "") != text
                text_visible = bool(verified.get("textVisibleInConversation"))
                conversation_created = bool(verified.get("conversationCreated"))
                # A new /c/ URL plus a released composer is reliable submission
                # evidence for project-root prompts even when the message DOM has
                # not hydrated yet.  Do not close that tab as a false failure.
                if composer_released and (text_visible or conversation_created):
                    update_job(job_id, "submitted", {
                        "send": "semantic_dom_button",
                        "ready_stable_checks": stable_ready,
                        "url": verified.get("url"),
                        "url_changed": str(verified.get("url") or "") != before_url,
                        "composer_released": composer_released,
                        "text_visible_in_conversation": text_visible,
                        "conversation_created": conversation_created,
                        "user_message_count": int(verified.get("userMessageCount") or 0),
                    })
                    ws.close()
                    ws = None
                    return
                if (
                    not keyboard_fallback_sent
                    and time.time() - verify_started >= 2.0
                    and str(verified.get("composer") or "") == text
                    and not conversation_created
                    and int(verified.get("userMessageCount") or 0) == 0
                ):
                    focus_expression = """
(() => {
  const el = document.querySelector('#prompt-textarea') || document.querySelector("textarea[data-id='root']") || document.querySelector('textarea') || document.querySelector("div[contenteditable='true']");
  if (!el) return false;
  el.focus();
  return document.activeElement === el || el.contains(document.activeElement);
})()
"""
                    call("Runtime.evaluate", {"expression": focus_expression, "returnByValue": True})
                    call("Input.dispatchKeyEvent", {"type":"keyDown","key":"Enter","code":"Enter","windowsVirtualKeyCode":13,"nativeVirtualKeyCode":13})
                    call("Input.dispatchKeyEvent", {"type":"keyUp","key":"Enter","code":"Enter","windowsVirtualKeyCode":13,"nativeVirtualKeyCode":13})
                    keyboard_fallback_sent = True
                    update_job(job_id, "send_ready", {"keyboard_fallback":"cdp_enter"})
            time.sleep(0.2)
        raise RuntimeError(f"submission_not_confirmed: {verified}")
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        failed_tab_closed = bool(page_id and cdp_close_target(page_id))
        # Doctor House/Field jobs do not need app/plugin selection.  When CDP
        # can fill the composer but cannot produce a trusted submission, retry
        # once through the independent X11/AT-SPI transport instead of opening
        # another CDP tab.  Jobs that require explicit apps remain fail-closed.
        if not apps:
            update_job(job_id, "send_ready", {
                "fallback_transport": "accessibility",
                "cdp_error": error_text,
                "failed_tab_closed": failed_tab_closed,
                "failed_tab_id": page_id or None,
            })
            accessibility_fill(job_id, target, text)
            return
        update_job(job_id, "failed", {
            "error": error_text,
            "failed_tab_closed": failed_tab_closed,
            "failed_tab_id": page_id or None,
        })


def extension_fill(job_id: str, target: str, text: str) -> None:
    try:
        trigger = (
            f"http://127.0.0.1:{PORT}/extension-trigger?"
            + urllib.parse.urlencode({"job_id": job_id, "target": target, "text": text})
        )
        subprocess.Popen(
            [OPEN_TAB, trigger],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        update_job(job_id, "trigger_opened", {"trigger": "extension"})
    except Exception as exc:
        update_job(job_id, "failed", {"error": f"{type(exc).__name__}: {exc}"})


def node_attrs(node: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for item in node.getAttributes() or []:
            if ":" in item:
                k, v = item.split(":", 1)
                out[k] = v
    except Exception:
        pass
    return out


def find_accessible_composer() -> tuple[Any, dict[str, Any]] | tuple[None, dict[str, Any]]:
    import pyatspi

    desktop = pyatspi.Registry.getDesktop(0)
    try:
        browser_pid = int((LAB / "browser.pid").read_text().strip())
    except Exception as exc:
        return None, {"error": f"browser_pid_unavailable:{exc}"}

    browser_apps = []
    for i in range(desktop.childCount):
        try:
            app = desktop.getChildAtIndex(i)
            if int(app.get_process_id()) == browser_pid:
                browser_apps.append(app)
        except Exception:
            continue
    if len(browser_apps) != 1:
        return None, {
            "error": "doctor_browser_accessibility_target_not_unique",
            "browser_pid": browser_pid,
            "matches": len(browser_apps),
        }

    candidates: list[tuple[int, Any, dict[str, Any]]] = []
    stack = list(browser_apps)
    seen = 0
    while stack and seen < 15000:
        node = stack.pop()
        seen += 1
        try:
            role_name = node.getRoleName()
            name = node.name or ""
            attrs = node_attrs(node)
            states = node.getState()
            editable = states.contains(pyatspi.STATE_EDITABLE)
            focusable = states.contains(pyatspi.STATE_FOCUSABLE)
            focused = states.contains(pyatspi.STATE_FOCUSED)
            showing = states.contains(pyatspi.STATE_SHOWING)
            visible = states.contains(pyatspi.STATE_VISIBLE)
            score = 0
            attrs_text = " ".join(f"{k}={v}" for k, v in attrs.items())
            lower_name = name.lower()
            xml_roles = str(attrs.get("xml-roles") or "").lower()
            css_class = str(attrs.get("class") or "")
            is_prompt = (
                attrs.get("id") == "prompt-textarea"
                or "prompt-textarea" in attrs_text
                or (
                    "textbox" in xml_roles
                    and "prosemirror" in css_class.lower()
                    and (
                        "message" in lower_name
                        or "сообщ" in lower_name
                        or "повідом" in lower_name
                        or "chatgpt" in lower_name
                    )
                )
            )
            if not is_prompt:
                score = -1000
            else:
                score += 150
            if editable:
                score += 20
            if focusable:
                score += 5
            if showing and visible:
                score += 60
            if focused:
                score += 80
            if editable and is_prompt:
                candidates.append(
                    (
                        score,
                        node,
                        {
                            "name": name,
                            "role": role_name,
                            "attrs": attrs,
                            "seen": seen,
                        },
                    )
                )
            count = node.childCount
            for i in range(count - 1, -1, -1):
                try:
                    stack.append(node.getChildAtIndex(i))
                except Exception:
                    pass
        except Exception:
            continue

    if not candidates:
        return None, {"seen": seen, "candidates": 0}
    candidates.sort(key=lambda item: item[0], reverse=True)
    score, node, meta = candidates[0]
    meta["score"] = score
    meta["candidates"] = len(candidates)
    return node, meta


def find_accessible_send_button() -> tuple[Any, dict[str, Any]] | tuple[None, dict[str, Any]]:
    import pyatspi

    desktop = pyatspi.Registry.getDesktop(0)
    try:
        browser_pid = int((LAB / "browser.pid").read_text().strip())
    except Exception as exc:
        return None, {"error": f"browser_pid_unavailable:{exc}"}

    browser_apps = []
    for i in range(desktop.childCount):
        try:
            app = desktop.getChildAtIndex(i)
            if int(app.get_process_id()) == browser_pid:
                browser_apps.append(app)
        except Exception:
            continue
    if len(browser_apps) != 1:
        return None, {
            "error": "doctor_browser_accessibility_target_not_unique",
            "browser_pid": browser_pid,
            "matches": len(browser_apps),
        }

    stack = list(browser_apps)
    candidates = []
    seen = 0
    while stack and seen < 20000:
        node = stack.pop()
        seen += 1
        try:
            role = (node.getRoleName() or "").lower()
            name = node.name or ""
            low = name.lower()
            states = node.getState()
            showing = states.contains(pyatspi.STATE_SHOWING)
            visible = states.contains(pyatspi.STATE_VISIBLE)
            enabled = states.contains(pyatspi.STATE_ENABLED)
            send_name = (
                "send message" in low
                or "отправить сообщение" in low
                or "надіслати повідомлення" in low
            )
            if send_name and "button" in role and showing and visible and enabled:
                candidates.append((node, {
                    "name": name,
                    "role": role,
                    "seen": seen,
                }))
            for i in range(node.childCount - 1, -1, -1):
                try:
                    stack.append(node.getChildAtIndex(i))
                except Exception:
                    pass
        except Exception:
            continue

    if len(candidates) != 1:
        return None, {"error": "send_button_not_unique", "matches": len(candidates), "seen": seen}
    return candidates[0]


def accessibility_fill(job_id: str, target: str, text: str) -> None:
    with ACCESSIBILITY_LOCK:
        _accessibility_fill_locked(job_id, target, text)


def _accessibility_fill_locked(job_id: str, target: str, text: str) -> None:
    try:
        browser_pid = int((LAB / "browser.pid").read_text().strip())
        wm = subprocess.run(
            ["wmctrl", "-lp"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        windows = []
        for line in wm:
            parts = line.split(None, 4)
            if len(parts) >= 3 and parts[2].isdigit() and int(parts[2]) == browser_pid:
                windows.append(parts[0])
        if len(windows) != 1:
            raise RuntimeError(f"doctor_browser_window_not_unique:{windows}")
        window_id = windows[0]

        subprocess.run(
            ["wmctrl", "-ia", window_id],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.2)
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", "ctrl+t"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.2)
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--delay", "1", "--", target],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", "Return"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        update_job(job_id, "tab_created", {
            "url": target,
            "open": "keyboard_ctrl_t",
            "window_id": window_id,
        })
        deadline = time.time() + 35
        last_meta: dict[str, Any] = {}
        while time.time() < deadline:
            node, meta = find_accessible_composer()
            last_meta = meta
            if node is not None:
                node.grab_focus()
                time.sleep(0.15)
                import pyatspi
                if not node.getState().contains(pyatspi.STATE_FOCUSED):
                    raise RuntimeError("composer_focus_not_confirmed")
                subprocess.run(
                    ["xdotool", "key", "--clearmodifiers", "ctrl+a"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                subprocess.run(
                    ["xdotool", "key", "--clearmodifiers", "BackSpace"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                subprocess.run(
                    ["xdotool", "type", "--clearmodifiers", "--delay", "10", "--", text],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(0.2)
                actual = ""
                try:
                    t = node.queryText()
                    actual = t.getText(0, t.characterCount)
                except Exception:
                    pass
                if text in actual or actual == text:
                    update_job(job_id, "filled", {**meta, "actual": actual})
                    send_node, send_meta = find_accessible_send_button()
                    if send_node is None:
                        raise RuntimeError(f"send_button_not_found:{send_meta}")
                    action = send_node.queryAction()
                    if action.nActions < 1:
                        raise RuntimeError("send_button_has_no_action")
                    action.doAction(0)
                    verify_deadline = time.time() + 15
                    last_after = actual
                    while time.time() < verify_deadline:
                        time.sleep(0.25)
                        after_node, after_meta = find_accessible_composer()
                        if after_node is None:
                            update_job(job_id, "submitted", {
                                "send": "atspi_semantic_action",
                                "verify": "composer_replaced",
                                **send_meta,
                            })
                            return
                        try:
                            after_text = after_node.queryText()
                            last_after = after_text.getText(0, after_text.characterCount)
                        except Exception:
                            last_after = ""
                        if text not in last_after:
                            update_job(job_id, "submitted", {
                                "send": "atspi_semantic_action",
                                "verify": "composer_cleared",
                                **send_meta,
                            })
                            return
                    raise RuntimeError(f"submission_not_confirmed:{last_after!r}")
                raise RuntimeError(f"keyboard_fill_not_reflected:{actual!r}")
            time.sleep(0.5)
        raise RuntimeError(f"accessible composer timeout: {last_meta}")
    except Exception as exc:
        update_job(job_id, "failed", {"error": f"{type(exc).__name__}: {exc}"})


def run_job(job: dict[str, Any]) -> None:
    method = str(job["method"])
    try:
        target = resolve_target(str(job["target"]), str(job.get("target_url") or ""))
    except Exception as exc:
        update_job(job["job_id"], "failed", {"error": str(exc)})
        return
    if method == "extension":
        extension_fill(job["job_id"], target, str(job["text"]))
    elif method == "cdp":
        cdp_fill(job["job_id"], target, str(job["text"]), list(job.get("apps") or []))
    elif method == "accessibility":
        accessibility_fill(job["job_id"], target, str(job["text"]))
    else:
        update_job(job["job_id"], "failed", {"error": "unknown method"})


def latest_jobs(limit: int = 40) -> list[dict[str, Any]]:
    with LOCK:
        return sorted(
            (dict(v) for v in JOBS.values()),
            key=lambda x: x.get("created_at", ""),
            reverse=True,
        )[:limit]


def page_html() -> str:
    cfg = load_config()
    project = html.escape(str(cfg.get("project_url") or ""), quote=True)
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Suzie Doctor Call Lab</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:1050px;margin:24px auto;padding:0 16px;background:#111;color:#eee}}
.card{{background:#1d1d1d;border:1px solid #3a3a3a;border-radius:12px;padding:16px;margin:12px 0}}
input{{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid #555;background:#161616;color:#fff}}
button{{padding:10px 14px;margin:5px;border-radius:8px;border:1px solid #666;background:#292929;color:#fff;cursor:pointer}}
button:hover{{background:#383838}}
.good{{color:#8fda8f}} .bad{{color:#ff9696}} .muted{{color:#aaa}}
code{{background:#292929;padding:2px 5px;border-radius:4px}}
table{{width:100%;border-collapse:collapse;font-size:14px}} td,th{{border-bottom:1px solid #333;padding:7px;text-align:left}}
</style></head><body>
<h1>Suzie Doctor — Call Lab</h1>
<div class="card">
  <b>Project URL</b>
  <p class="muted">Утром открой Project Suzie Doctor в отдельном Doctor Chromium и вставь сюда его URL один раз.</p>
  <input id="project" value="{project}" placeholder="https://chatgpt.com/...">
  <button onclick="save()">Сохранить URL проекта</button>
  <span id="saveState"></span>
</div>
<div class="card">
  <b>Текст теста</b>
  <input id="text" value="1111">
  <p>
    <button onclick="runJob('extension','mock','1111')">A Extension · mock · 1111</button>
    <button onclick="runJob('cdp','mock','2222')">B CDP · mock · 2222</button>
    <button onclick="runJob('accessibility','mock','3333')">C Accessibility · mock · 3333</button>
  </p>
  <p>
    <button onclick="runJob('extension','project')">A Extension → ChatGPT</button>
    <button onclick="runJob('cdp','project')">B CDP → ChatGPT</button>
    <button disabled>C Accessibility — REAL FAIL</button>
  </p>
  <p class="muted">Каждое нажатие обязано создать новую вкладку. В composer кладётся число + текущее время и затем нажимается semantic Send без координат.</p>
</div>
<div class="card"><b>Последние вызовы</b><div id="jobs"></div></div>
<script>
async function save(){{
 const r=await fetch('/api/config',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{project_url:document.getElementById('project').value}})}});
 const j=await r.json(); document.getElementById('saveState').textContent=j.ok?' сохранено':' ошибка: '+j.error;
}}
async function runJob(method,target,forced){{
 const base=forced||document.getElementById('text').value;
 const d=new Date();
 const time=d.toLocaleTimeString('ru-RU',{{hour12:false}})+'.'+String(d.getMilliseconds()).padStart(3,'0');
 const text=base+' — '+time;
 const r=await fetch('/api/run',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{method:method,target:target,text:text}})}});
 const j=await r.json(); if(!j.ok) alert(j.error); refresh();
}}
function esc(x){{return String(x==null?'':x).replace(/[&<>"]/g,function(c){{return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c];}});}}
async function refresh(){{
 const r=await fetch('/api/status'); const j=await r.json();
 let h='<table><tr><th>время</th><th>метод</th><th>текст</th><th>цель</th><th>состояние</th><th>детали</th></tr>';
 for(const x of j.jobs){{
  const cls=x.state==='filled'?'good':x.state==='failed'?'bad':'';
  h+='<tr><td>'+esc((x.updated_at||'').slice(11,19))+'</td><td>'+esc(x.method)+'</td><td><code>'+esc(x.text)+'</code></td><td>'+esc(x.target)+'</td><td class="'+cls+'">'+esc(x.state)+'</td><td>'+esc(JSON.stringify(x.detail||{{}}))+'</td></tr>';
 }}
 document.getElementById('jobs').innerHTML=h+'</table>';
}}
setInterval(refresh,1000);refresh();
</script></body></html>"""


def mock_html() -> str:
    return """<!doctype html><html><head><meta charset="utf-8"><title>Suzie Doctor Mock Chat</title>
<style>body{font-family:sans-serif;background:#202123;color:white;padding:40px}#prompt-textarea{border:2px solid #888;border-radius:12px;min-height:80px;padding:16px;background:#343541;outline:none}</style></head>
<body><h2>Mock ChatGPT composer</h2><p>Это локальный тест, не ChatGPT.</p>
<div id="prompt-textarea" contenteditable="true" role="textbox" aria-label="Message ChatGPT"></div>
<button id="send-button" aria-label="Отправить сообщение">Send</button>
<p id="submitted"></p>
<script>
const c=document.getElementById("prompt-textarea");
const b=document.getElementById("send-button");
function send(){
  if(!c.textContent) return;
  document.getElementById("submitted").textContent="SUBMITTED:"+c.textContent;
  c.textContent="";
}
b.addEventListener("click",send);
c.addEventListener("keydown",e=>{
  if(e.key==="Enter" && !e.shiftKey){
    e.preventDefault();
    send();
  }
});
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "SuzieDoctorCallLab/0.5"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _html(self, status: int, text: str) -> None:
        raw = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict[str, Any]:
        n = min(int(self.headers.get("Content-Length") or 0), 65536)
        raw = self.rfile.read(n) if n else b"{}"
        obj = json.loads(raw.decode("utf-8"))
        return obj if isinstance(obj, dict) else {}

    def _client_allowed(self) -> bool:
        ip = str(self.client_address[0] or "")
        return ip == "127.0.0.1" or ip.startswith("192.168.0.")

    def do_GET(self) -> None:
        if not self._client_allowed():
            self._json(403, {"ok": False, "error": "LAN only"})
            return
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            self._html(200, page_html())
            return
        if path == "/browser-home":
            self._html(200, "<h2>Suzie Doctor browser ready</h2><p>Окно оставь открытым. Управление: <a href='/'>Call Lab</a>.</p>")
            return
        if path == "/mock-chat":
            self._html(200, mock_html())
            return
        if path == "/extension-trigger":
            self._html(200, "<h3>Extension trigger</h3><p>Эта вкладка должна закрыться автоматически.</p>")
            return
        if path == "/api/status":
            self._json(200, {"ok": True, "config": load_config(), "jobs": latest_jobs()})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if not self._client_allowed():
            self._json(403, {"ok": False, "error": "LAN only"})
            return
        path = urllib.parse.urlsplit(self.path).path
        try:
            body = self._body()
        except Exception as exc:
            self._json(400, {"ok": False, "error": f"bad json: {exc}"})
            return

        if path == "/api/config":
            url = str(body.get("project_url") or "").strip()
            if url and not url.startswith("https://chatgpt.com/"):
                self._json(400, {"ok": False, "error": "URL должен начинаться с https://chatgpt.com/"})
                return
            save_config({"project_url": url})
            self._json(200, {"ok": True, "config": load_config()})
            return

        if path == "/api/run":
            method = str(body.get("method") or "")
            target = str(body.get("target") or "")
            target_url = str(body.get("target_url") or "").strip()
            text = str(body.get("text") or "")[:500]
            raw_apps = body.get("apps") or []
            apps = [str(x).strip() for x in raw_apps if str(x).strip()] if isinstance(raw_apps, list) else []
            allowed_apps = {"Suzie Gmail", "Suzie Home", "Suzie Home Assistant"}
            if any(x not in allowed_apps for x in apps) or len(apps) > 2:
                self._json(400, {"ok": False, "error": "bad apps"})
                return
            if method not in {"extension", "cdp", "accessibility"}:
                self._json(400, {"ok": False, "error": "bad method"})
                return
            if target not in {"mock", "project", "house", "wilson", "scheduler", "dialog"}:
                self._json(400, {"ok": False, "error": "bad target"})
                return
            if not text:
                self._json(400, {"ok": False, "error": "empty text"})
                return
            cfg = load_config()
            target_key = {"project":"project_url","house":"house_url","wilson":"wilson_url","scheduler":"scheduler_url"}.get(target)
            if target_key and not str(cfg.get(target_key) or ""):
                self._json(400, {"ok": False, "error": f"URL is not configured for {target}"})
                return
            if target == "dialog" and not target_url.startswith("https://chatgpt.com/"):
                self._json(400, {"ok": False, "error": "bad dialog URL"})
                return
            job = new_job(method, text, target, target_url, apps)
            threading.Thread(target=run_job, args=(job,), daemon=True).start()
            self._json(202, {"ok": True, "job": job})
            return

        if path == "/api/ack":
            job_id = str(body.get("job_id") or "")
            state = str(body.get("state") or "")
            detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
            if state not in {"trigger_received", "tab_created", "filled", "send_ready", "submitted", "failed"}:
                self._json(400, {"ok": False, "error": "bad state"})
                return
            update_job(job_id, state, detail)
            self._json(200, {"ok": True})
            return

        self._json(404, {"ok": False, "error": "not found"})


def main() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config({})
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
