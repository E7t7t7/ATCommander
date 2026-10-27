from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .api import ATClient
from .config import APP_DIR
from .credentials import load_credentials
from .account_state import find_ad_state

OUT = APP_DIR / "ad_state_probe_sanitized.jsonl"

SENSITIVE_PATH_TERMS = (
    "password", "hash", "cookie", "authorization", "session", "token",
    "email", "user_id", "userid", "device_id", "advertising_id",
)
NOISY_TIME_TERMS = (
    "server_time", "server.time", "timestamp", "heartbeat", "last_login",
    "request_time", "current_time", "now",
)
AD_HINTS = (
    "ad", "video", "bonus", "vip", "reward", "loot", "watch", "crate",
    "commercial", "advert", "boost",
)


def _write(obj: dict[str, Any]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


def _safe_scalar(v: Any) -> Any:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        return v[:240]
    return None


def _flatten(obj: Any, path: str = "", depth: int = 0, out: dict[str, Any] | None = None) -> dict[str, Any]:
    if out is None:
        out = {}
    if depth > 9:
        return out
    lp = path.lower()
    if any(term in lp for term in SENSITIVE_PATH_TERMS):
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            _flatten(v, p, depth + 1, out)
    elif isinstance(obj, list):
        # Indexes can shift across init calls; keep short lists only.
        if len(obj) <= 50:
            for i, v in enumerate(obj):
                _flatten(v, f"{path}[{i}]", depth + 1, out)
    else:
        sv = _safe_scalar(obj)
        if sv is not None:
            # Ignore generic clocks unless they are explicitly ad-related.
            low = path.lower()
            if any(t in low for t in NOISY_TIME_TERMS) and not any(h in low for h in AD_HINTS):
                return out
            out[path] = sv
    return out


def _focused(flat: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in flat.items() if any(h in k.lower() for h in AD_HINTS)}


def _diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in sorted(set(before) | set(after)):
        a = before.get(key, "<missing>")
        b = after.get(key, "<missing>")
        if a != b:
            rows.append({"path": key, "before": a, "after": b, "ad_related": any(h in key.lower() for h in AD_HINTS)})
    return rows


def main() -> int:
    creds = load_credentials()
    if not creds.ready:
        print("[ERROR] AT credentials are not available. Run Get_AT_Login_Hash.bat first.")
        return 2

    client = ATClient(creds)
    OUT.unlink(missing_ok=True)

    print("AT Commander - Ad State Probe")
    print("This polls your own AT account state while you trigger Bender from your phone.")
    print("No login hash is written to the probe file.")
    print("\nTake your phone screen recording, show the current ad bonus, then run the Bender ad command.")
    print("Leave this window running until Bender finishes, then press Ctrl+C.\n")

    try:
        init0 = client.init()
    except Exception as exc:
        print(f"[ERROR] Initial account read failed: {exc}")
        return 3

    prev = _flatten(init0)
    _write({
        "kind": "baseline",
        "ts": time.time(),
        "ad_state": find_ad_state(init0),
        "focused": _focused(prev),
    })
    print(f"Baseline captured. Watching for changes... ({OUT})")

    try:
        while True:
            time.sleep(1.0)
            try:
                data = client.init()
            except Exception as exc:
                _write({"kind": "poll_error", "ts": time.time(), "error": str(exc)[:300]})
                print(f"Poll warning: {exc}")
                continue
            cur = _flatten(data)
            changes = _diff(prev, cur)
            if changes:
                # Record all stable scalar changes, but print ad-related ones first.
                _write({
                    "kind": "changes",
                    "ts": time.time(),
                    "ad_state": find_ad_state(data),
                    "changes": changes,
                })
                focused_changes = [c for c in changes if c.get("ad_related")]
                if focused_changes:
                    print("AD-RELATED STATE CHANGE:")
                    for c in focused_changes[:20]:
                        print(f"  {c['path']}: {c['before']} -> {c['after']}")
                else:
                    print(f"Account state changed ({len(changes)} field(s)); recorded for comparison.")
            prev = cur
    except KeyboardInterrupt:
        print("\nStopped. Run Export_Ad_State_Probe.bat and send the exported JSONL with your phone recording.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
