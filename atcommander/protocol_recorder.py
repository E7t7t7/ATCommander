from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
import websocket

from .config import APP_DIR

PORT = int(os.environ.get("ATC_PROTOCOL_PORT", "9223"))
TRACE_FILE = APP_DIR / os.environ.get("ATC_PROTOCOL_FILE", "protocol_trace_sanitized.jsonl")
TRACE_LABEL = os.environ.get("ATC_PROTOCOL_LABEL", "general")
AD_MODE = os.environ.get("ATC_PROTOCOL_AD_MODE", "0") == "1"
SENSITIVE_KEYS = {
    "password", "password_hash", "pass", "auth", "authorization",
    "session", "session_id", "cookie", "set-cookie", "login_hash",
    "kong_token", "kong_vars", "clisig", "msgsig", "__request_fingerprint",
}
SENSITIVE_PARTS = ("password", "passwd", "cookie", "authorization", "token", "session", "secret", "login_hash", "loginhash", "credential")


def _flatten(data: dict[str, list[str]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, values in data.items():
        out[key] = values[0] if len(values) == 1 else values
    return out


def _parse_post(raw_post: str) -> dict[str, object]:
    if not raw_post:
        return {}
    # parse_qs accepts arbitrary JSON as one giant *key*, so JSON must be
    # recognized first. Otherwise analytics/session fields can escape recursive
    # redaction because the secrets are embedded in the dictionary key.
    try:
        parsed = json.loads(raw_post)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"_json": parsed}
    except Exception:
        pass
    return _flatten(parse_qs(raw_post, keep_blank_values=True))


def _sanitize(value):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            # Protect old malformed rows and any unusual encoded JSON field
            # names, even if a caller bypasses _parse_post.
            if str(k).lstrip().startswith(("{", "[")):
                try:
                    encoded_key = json.loads(str(k))
                except (ValueError, TypeError):
                    out["_encoded_field"] = "<redacted>"
                else:
                    out["_encoded_field"] = _sanitize(encoded_key)
                continue
            lk = str(k).lower()
            if lk in SENSITIVE_KEYS or any(part in lk for part in SENSITIVE_PARTS) or lk.endswith("sig"):
                out[k] = "<redacted>"
            elif lk == "user_id":
                out[k] = "<user>"
            else:
                out[k] = _sanitize(v)
        return out
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, str):
        stripped = value.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                # An unparsed body must not become a raw diagnostic preview.
                return "<unparsed structured value>"
            return json.dumps(_sanitize(decoded), ensure_ascii=False, separators=(",", ":"))
        # Response errors and other text sometimes contain form/query fragments.
        # Preserve their message while removing credential values in those forms.
        return re.sub(
            r"(?i)((?:password(?:_hash)?|passwd|login_?hash|kong_token|kong_vars|clisig|msgsig|session(?:_id)?|authorization|cookie|[A-Za-z_]*token)\s*[=:]\s*)[^\s&;,]+",
            r"\1<redacted>", value,
        )
    return value


def _targets() -> list[dict]:
    try:
        r = requests.get(f"http://127.0.0.1:{PORT}/json", timeout=1.0)
        if r.ok:
            data = r.json()
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _write(record: dict) -> None:
    record = dict(record)
    record.setdefault("label", TRACE_LABEL)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with TRACE_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_sanitize(record), ensure_ascii=False, separators=(",", ":")) + "\n")


def _is_at_api_url(url: str) -> bool:
    low = (url or "").lower()
    # 1.7.3 listened to one exact cb-live URL. Some game actions are routed
    # through alternate Synapse hosts/paths, so captures such as Upgrade could
    # happen in the browser yet be invisible to the recorder.
    return (
        ("synapsegames.com" in low and ("api.php" in low or "/api" in low))
        or ("animationthrowdown" in low and "api" in low)
    )


def _listen(target_id: str, ws_url: str, stop: threading.Event, seen: set[str], lock: threading.Lock):
    ws = None
    pending: dict[str, dict] = {}
    cmd_map: dict[int, tuple[str, str]] = {}
    try:
        ws = websocket.create_connection(ws_url, timeout=2, suppress_origin=True)
        ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
        ws.settimeout(2)
        cmd = 1000
        while not stop.is_set():
            try:
                msg = json.loads(ws.recv())
            except Exception as exc:
                if exc.__class__.__name__ == "WebSocketTimeoutException":
                    continue
                break
            method = msg.get("method")
            params = msg.get("params") or {}
            if method == "Network.requestWillBeSent":
                req = params.get("request") or {}
                url = str(req.get("url") or "")
                low_url = url.lower()
                is_at_api = _is_at_api_url(url)
                resource_type = str(params.get("type") or "")
                http_method = str(req.get("method") or "GET").upper()
                adish = any(x in low_url for x in ("/ad", "ads.", "advert", "video", "reward", "vast", "doubleclick", "googleads", "ima3", "ironsource", "applovin", "chartboost", "unityads"))
                # Some live-client actions have repeatedly escaped the nominal AT
                # API URL filter. During an *isolated* Learn capture we can safely
                # observe any XHR/fetch mutation from the game window, then let the
                # semantic learner decide whether it belongs to the requested action.
                # This is especially important for Upgrade Card, which the user's
                # client performs successfully while older recorders reported zero
                # useful API requests.
                isolated_mutation_labels = {"daily_upgrade", "siege", "swole_battle", "challenge_battle", "item_choice"}
                broad_mutation = TRACE_LABEL in isolated_mutation_labels and resource_type in {"XHR", "Fetch", "Other"} and http_method in {"POST", "PUT", "PATCH"}
                if not is_at_api and not broad_mutation:
                    if AD_MODE and adish:
                        parsed_meta = urlparse(url)
                        _write({"ts": time.time(), "kind": "ad_request_meta", "method": req.get("method"), "host": parsed_meta.netloc, "path": parsed_meta.path[:400], "resource_type": resource_type})
                    continue
                rid = str(params.get("requestId") or "")
                parsed = urlparse(url)
                query = _flatten(parse_qs(parsed.query, keep_blank_values=True))
                raw_post = str(req.get("postData") or "")
                post = _parse_post(raw_post)
                parsed_path = parsed.path.rstrip("/").split("/")[-1] if parsed.path else ""
                message_name = str(
                    query.get("message") or post.get("message") or post.get("action") or
                    post.get("command") or post.get("cmd") or ""
                )
                # For broad isolated captures preserve a path hint so the review UI
                # never collapses a real mutation into an empty-message row. This
                # path hint is diagnostic only unless the normal AT `message` field
                # is recovered from late POST data or the echoed response.
                display_name = message_name or (f"@{parsed_path}" if parsed_path else "@mutation")
                pending[rid] = {"message": message_name, "display_name": display_name, "url": url, "is_at_api": is_at_api}
                _write({"ts": time.time(), "kind": "request", "message": display_name, "query": _sanitize(query), "post": _sanitize(post), "request_id": rid, "url_host": parsed.netloc, "url_path": parsed.path[:400], "target_type": resource_type, "target_id": target_id})
                # Chromium sometimes omits postData from requestWillBeSent even when
                # the request has a form body. Ask CDP for it explicitly.
                if not raw_post and rid:
                    cmd += 1
                    try:
                        ws.send(json.dumps({"id": cmd, "method": "Network.getRequestPostData", "params": {"requestId": rid}}))
                        cmd_map[cmd] = (rid, "post")
                    except Exception:
                        pass
            elif method == "Network.responseReceived":
                rid = str(params.get("requestId") or "")
                if rid not in pending:
                    continue
                cmd += 1
                ws.send(json.dumps({"id": cmd, "method": "Network.getResponseBody", "params": {"requestId": rid}}))
                cmd_map[cmd] = (rid, "body")
                pending[rid]["status"] = (params.get("response") or {}).get("status")
            elif "id" in msg:
                mid = msg.get("id")
                action = cmd_map.pop(mid, None)
                if not action:
                    continue
                rid, kind = action
                info = pending.get(rid) or {}
                if kind == "post":
                    raw = str((msg.get("result") or {}).get("postData") or "")
                    post = _parse_post(raw)
                    if post:
                        m = str(post.get("message") or post.get("action") or post.get("command") or post.get("cmd") or info.get("message") or "")
                        if m:
                            info["message"] = m
                        shown = m or str(info.get("display_name") or "@mutation")
                        _write({"ts": time.time(), "kind": "request_postdata", "message": shown, "post": _sanitize(post), "request_id": rid, "target_id": target_id})
                    continue
                body = (msg.get("result") or {}).get("body")
                parsed_body = None
                if isinstance(body, str) and body:
                    try:
                        parsed_body = json.loads(body)
                    except Exception:
                        parsed_body = {"_unparsed_body_length": len(body)}
                # If requestWillBeSent had no message, response.body.request often
                # contains it. Preserve it on the response row for the learner.
                echoed = parsed_body.get("request") if isinstance(parsed_body, dict) and isinstance(parsed_body.get("request"), dict) else {}
                m = str(
                    info.get("message") or echoed.get("message") or echoed.get("action") or
                    echoed.get("command") or echoed.get("cmd") or info.get("display_name") or ""
                )
                _write({"ts": time.time(), "kind": "response", "message": m, "status": info.get("status"), "request_id": rid, "body": _sanitize(parsed_body), "target_id": target_id})
                pending.pop(rid, None)
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        with lock:
            seen.discard(target_id)


def main() -> int:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    print("AT Commander Protocol Recorder")
    print(f"Capture label: {TRACE_LABEL}")
    print("Recording sanitized AT API traffic from the special Edge window on port 9223.")
    print("Perform ONLY the action(s) you want learned, then press Ctrl+C here.")
    print(f"Trace: {TRACE_FILE}")
    stop = threading.Event(); seen: set[str] = set(); lock = threading.Lock()
    allowed_types = {"page", "iframe", "worker", "service_worker", "shared_worker"}
    try:
        while True:
            for target in _targets():
                tid = str(target.get("id") or ""); ws_url = target.get("webSocketDebuggerUrl")
                if not tid or not ws_url or target.get("type") not in allowed_types:
                    continue
                with lock:
                    if tid in seen:
                        continue
                    seen.add(tid)
                threading.Thread(target=_listen, args=(tid, ws_url, stop, seen, lock), daemon=True).start()
            time.sleep(0.35)
    except KeyboardInterrupt:
        stop.set(); time.sleep(0.3)
        print("\nStopped. The sanitized trace is saved locally.")
        return 0

if __name__ == "__main__":
    raise SystemExit(main())
