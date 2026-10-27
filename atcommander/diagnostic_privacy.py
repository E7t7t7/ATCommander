"""Privacy helpers for diagnostic reports; never performs network/account work."""
from __future__ import annotations

import json
import re
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit


_PRIVATE_PARTS = (
    "password", "passwd", "loginhash", "login_hash", "credential", "cookie",
    "authorization", "token", "session", "secret", "clisig", "msgsig", "adsig",
    "kong_vars", "user_id", "userid", "kong_id", "device_id", "advertising_id",
    "fingerprint", "request_id", "server_player_id",
)
_PRIVATE_KEY = r"[\w-]*(?:password|passwd|login_?hash|credential|cookie|authorization|token|session|secret|clisig|msgsig|adsig|kong_vars|user_?id|kong_id|device_id|advertising_id|fingerprint|request_id|server_player_id)[\w-]*"
_ASSIGNMENT = re.compile(
    rf"(?i)(?P<key>[\"']?{_PRIVATE_KEY}[\"']?\s*[=:]\s*)"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s&;,}\])]+)"
)
_URL = re.compile(r"(?i)(?:https?://|wss?://|//)[^\s\"'<>`\[\]{}()]+")


def private_key(key: Any) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(part in normalized for part in _PRIVATE_PARTS) or normalized.endswith("sig")


def safe_url(value: Any) -> str:
    """Keep source origin/path only, including for URLs with embedded userinfo."""
    raw = str(value or "")
    try:
        parts = urlsplit(raw)
        if parts.scheme.lower() not in {"http", "https", "", "file"}:
            # data/blob URLs can embed a complete private payload.
            return "<non-public source>"
        authority = parts.hostname or ""
        if ":" in authority:
            authority = f"[{authority}]"
        if parts.port is not None:
            authority += f":{parts.port}"
        return urlunsplit((parts.scheme, authority, parts.path, "", ""))
    except (ValueError, TypeError):
        return "<invalid source URL>"


def private_literals(value: Any) -> set[str]:
    """Collect private request values in memory for scrubbing static excerpts."""
    found: set[str] = set()

    def visit(item: Any, sensitive: bool = False) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                visit(child, sensitive or private_key(key))
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child, sensitive)
        elif isinstance(item, str):
            if sensitive and len(item) >= 4:
                found.add(item)
            decoded = unquote(item)
            if sensitive and decoded != item and len(decoded) >= 4:
                found.add(decoded)
            try:
                structured = json.loads(decoded)
            except (ValueError, TypeError):
                if "=" in decoded:
                    structured = parse_qs(decoded, keep_blank_values=True)
                else:
                    structured = None
            if isinstance(structured, (dict, list)):
                visit(structured, sensitive)
        elif sensitive and item is not None and len(str(item)) >= 4:
            found.add(str(item))

    visit(value)
    return found


def safe_text(value: str, literals: Iterable[str] = ()) -> str:
    # Preserve line structure for caller excerpts while scrubbing both clear and
    # URL-encoded copies of actual private request values before persistence.
    text = value
    for literal in sorted(set(literals), key=len, reverse=True):
        if not literal:
            continue
        for encoded in {literal, quote(literal, safe=""), json.dumps(literal)[1:-1]}:
            text = text.replace(encoded, "<redacted>")
    text = _URL.sub(lambda match: safe_url(match.group(0)), text)
    # Encoded embedded forms are common in Unity/Kong source and error strings.
    # Decode only for inspection; if private keys are found, suppress that
    # opaque string instead of exposing a partially decoded credential body.
    decoded = text
    for _ in range(2):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    if decoded != text and _ASSIGNMENT.search(decoded):
        return "<redacted encoded diagnostic text>"
    text = _ASSIGNMENT.sub(lambda match: match.group("key") + "<redacted>", text)
    # Escaped JSON can be nested inside a larger source string rather than be a
    # standalone JSON document. Match its escaped key/value boundary explicitly.
    text = re.sub(
        rf'(?i)(\\\"{_PRIVATE_KEY}\\\"\s*:\s*\\\")(?:\\\\.|[^\"])*?(\\\")',
        r'\1<redacted>\2', text,
    )
    return text


def sanitize_report(value: Any, literals: Iterable[str] = ()) -> Any:
    """Recursively remove credentials, source URL parameters and private text."""
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            # Field names in this report originate in the client request. Keep
            # ordinary field names, but never preserve an encoded JSON key.
            safe_key = safe_text(str(key), literals)
            if str(key).lstrip().startswith(("{", "[")) or safe_key != str(key):
                safe_key = "<encoded field>"
            if private_key(key):
                if isinstance(child, dict) and set(child) <= {"present", "length"}:
                    result[safe_key] = {"present": bool(child.get("present")), "length": int(child.get("length") or 0)}
                else:
                    result[safe_key] = "<redacted>"
            elif str(key).lower().endswith("url") and isinstance(child, str):
                result[safe_key] = safe_url(child)
            else:
                result[safe_key] = sanitize_report(child, literals)
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize_report(child, literals) for child in value]
    if isinstance(value, str):
        try:
            structured = json.loads(value)
        except (ValueError, TypeError):
            return safe_text(value, literals)
        if isinstance(structured, (dict, list)):
            return json.dumps(sanitize_report(structured, literals), ensure_ascii=False, separators=(",", ":"))
        return safe_text(value, literals)
    return value
