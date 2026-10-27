from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

from .config import APP_DIR
from .protocol_config import load_profiles, save_profiles

GAME_URL = "https://www.kongregate.com/en/games/throwdown/animation-throwdown"
PORT = 9223
PROFILE_DIR = APP_DIR / "learn_browser_profile"


class LearningBrowser:
    """One persistent Edge instance reused for every learning capture.

    The browser is deliberately independent from the capture process. The user
    logs in once, then Begin/Finish Capture can be repeated indefinitely without
    reopening Edge or losing cookies.
    """

    def __init__(self):
        self.edge_proc: subprocess.Popen | None = None

    @staticmethod
    def _edge() -> str | None:
        cands = [
            shutil.which("msedge"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        ]
        return next((x for x in cands if x and Path(x).exists()), None)

    @staticmethod
    def _targets() -> list[dict[str, Any]]:
        try:
            r = requests.get(f"http://127.0.0.1:{PORT}/json", timeout=0.8)
            if r.ok:
                data = r.json()
                return data if isinstance(data, list) else []
        except Exception:
            pass
        return []

    def is_running(self) -> bool:
        return bool(self._targets())

    def _close_extra_tabs(self) -> None:
        """Keep one AT page and close first-run/restored junk tabs."""
        targets = [t for t in self._targets() if t.get("type") == "page"]
        if not targets:
            return
        keeper = next(
            (
                t
                for t in targets
                if "kongregate.com" in str(t.get("url") or "").lower()
                and "animation-throwdown" in str(t.get("url") or "").lower()
            ),
            None,
        )
        if keeper is None:
            keeper = targets[0]
        for t in targets:
            if t.get("id") == keeper.get("id"):
                continue
            try:
                requests.get(f"http://127.0.0.1:{PORT}/json/close/{t.get('id')}", timeout=0.8)
            except Exception:
                pass

    def ensure_open(self) -> tuple[bool, str]:
        if self.is_running():
            return True, "Learning Browser already open — reusing the saved login session."

        edge = self._edge()
        if not edge:
            return False, "Microsoft Edge was not found."

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        from .embedded_browser import get_embedded_state
        hosted = get_embedded_state().get("registered") is True
        cmd = [
            edge,
            f"--remote-debugging-port={PORT}",
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-features=CalculateNativeWinOcclusion",
            # 1.7.16 runs the reusable browser as a compact app window instead
            # of a normal tabbed Edge window. Ads/battle learning stay isolated
            # from the user's regular browser and popup tabs are easier to contain.
            "--window-size=640,720",
            "--window-position=-10000,24" if hosted else "--window-position=24,24",
            f"--app={GAME_URL}",
        ]
        try:
            self.edge_proc = subprocess.Popen(
                cmd,
                creationflags=flags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            return False, f"Could not launch the Learning Browser: {exc}"

        deadline = time.time() + 12
        while time.time() < deadline:
            if self.is_running():
                time.sleep(0.8)
                self._close_extra_tabs()
                return True, (
                    "Mini AT browser opened in its own 640x720 app window. Log into Kongregate/AT once if needed. "
                    "This same isolated browser/profile is reused for ads and Learn actions."
                )
            time.sleep(0.25)
        return False, "Edge opened, but AT Commander could not connect to its learning/debugging port."


    def _set_window_bounds(self, *, left:int, top:int, width:int=640, height:int=720) -> bool:
        """Move the dedicated mini-game window without touching the user's cursor.

        Background mode deliberately keeps the Edge renderer alive instead of
        minimizing it. Chromium aggressively throttles minimized/occluded game
        windows, which can break Unity timers and signed Fetch generation.
        """
        from .embedded_browser import get_embedded_state, request_attach
        state=get_embedded_state()
        if state.get("registered"):
            # A registered Commander pane owns placement. Never move its child
            # back to desktop coordinates, even if an attachment is unavailable.
            return request_attach().get("embedded") is True
        rows=[t for t in self._targets() if t.get("type")=="page" and t.get("webSocketDebuggerUrl")]
        if not rows:return False
        target=next((t for t in rows if "animation-throwdown" in str(t.get("url") or "").lower()),rows[0])
        try:
            import websocket
            ws=websocket.create_connection(target["webSocketDebuggerUrl"],timeout=2,suppress_origin=True)
            try:
                ws.send(json.dumps({"id":1,"method":"Browser.getWindowForTarget","params":{"targetId":target.get("id")}}))
                wid=None;deadline=time.time()+2
                while time.time()<deadline:
                    msg=json.loads(ws.recv())
                    if msg.get("id")==1:
                        wid=(msg.get("result") or {}).get("windowId");break
                if wid is None:return False
                ws.send(json.dumps({"id":2,"method":"Browser.setWindowBounds","params":{"windowId":wid,"bounds":{"left":int(left),"top":int(top),"width":int(width),"height":int(height),"windowState":"normal"}}}))
                deadline=time.time()+2
                while time.time()<deadline:
                    msg=json.loads(ws.recv())
                    if msg.get("id")==2:return not bool(msg.get("error"))
            finally:
                try:ws.close()
                except Exception:pass
        except Exception:
            return False
        return False

    def show(self) -> bool:
        return self._set_window_bounds(left=24,top=24)

    def background(self) -> bool:
        # Off-screen rather than minimized: the game keeps rendering/signing, but
        # the helper no longer occupies the user's desktop or steals the mouse.
        return self._set_window_bounds(left=-10000,top=24)

    def close(self) -> None:
        # Close every page attached to this dedicated user-data-dir. If the process
        # handle is stale, CDP closes still work.
        for t in list(self._targets()):
            if t.get("type") != "page":
                continue
            try:
                requests.get(f"http://127.0.0.1:{PORT}/json/close/{t.get('id')}", timeout=0.8)
            except Exception:
                pass
        if self.edge_proc and self.edge_proc.poll() is None:
            try:
                self.edge_proc.terminate()
                self.edge_proc.wait(timeout=2)
            except Exception:
                try:
                    self.edge_proc.kill()
                except Exception:
                    pass
        self.edge_proc = None


class LearnSession:
    """A per-action recorder; it never launches or closes Edge."""

    def __init__(self, key: str):
        self.key = key
        self.trace = APP_DIR / f"learn_{key}_sanitized.jsonl"
        self.proc: subprocess.Popen | None = None

    def start(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        try:
            self.trace.unlink()
        except Exception:
            pass
        env = os.environ.copy()
        env.update(
            {
                "ATC_PROTOCOL_PORT": str(PORT),
                "ATC_PROTOCOL_FILE": self.trace.name,
                "ATC_PROTOCOL_LABEL": self.key,
            }
        )
        if self.key == "ad_flow":
            env["ATC_PROTOCOL_AD_MODE"] = "1"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "atcommander.protocol_recorder"],
            env=env,
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return self.trace

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        return self.trace


def _records(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
            except Exception:
                pass
    except Exception:
        pass
    return rows
# Fields the AT web client echoes in response.body.request on nearly every call.
# They are useful for replaying the browser request, but not for learning the
# action shape and may contain credentials/session identifiers.
_BOILERPLATE_FIELDS = {
    "message", "user_id", "password", "password_hash", "pass", "auth",
    "authorization", "cookie", "session", "session_id", "kong_id",
    "kong_token", "kong_name", "kong_vars", "unity", "client_version",
    "client_version_full", "platform", "graphics_device_type",
    "graphics_device_version", "os_version", "resolution", "ad_id",
    "client_time", "clisig", "msgsig", "__request_fingerprint",
}

def _is_boilerplate_field(key: object) -> bool:
    low = str(key).lower()
    if low in _BOILERPLATE_FIELDS:
        return True
    if low.endswith("_data_version") or low.endswith("_version") and low not in {"version"}:
        return True
    if any(x in low for x in ("password", "cookie", "authorization", "token", "session")):
        return True
    return False

def _action_fields(data: object) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if not _is_boilerplate_field(k)}

def _enriched_request_rows(path: Path) -> list[dict[str, Any]]:
    """Recover action parameters from the echoed response request.

    The AT web client often sends an opaque request that CDP exposes with an
    empty postData, while the JSON response echoes the fully-expanded request
    under body.request. 1.7.2 only looked at the request row, causing valid
    Upgrade/Swole/Siege captures to be reported as 'no useful API calls'.
    """
    rows = _records(path)
    responses: dict[str, dict[str, Any]] = {}
    postdata_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("kind") == "response" and row.get("request_id"):
            responses[str(row.get("request_id"))] = row
        elif row.get("kind") == "request_postdata" and row.get("request_id"):
            postdata_rows[str(row.get("request_id"))] = row
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("kind") != "request":
            continue
        enriched = dict(row)
        merged: dict[str, Any] = {}
        merged.update(_action_fields(row.get("query")))
        merged.update(_action_fields(row.get("post")))
        late = postdata_rows.get(str(row.get("request_id") or "")) or {}
        merged.update(_action_fields(late.get("post")))
        resp = responses.get(str(row.get("request_id") or "")) or {}
        body = resp.get("body") or {}
        echoed = body.get("request") if isinstance(body, dict) else None
        merged.update(_action_fields(echoed))
        row_message = str(row.get("message") or "")
        late_message = str(late.get("message") or "")
        echoed_message = str((echoed or {}).get("message") or (echoed or {}).get("action") or (echoed or {}).get("command") or (echoed or {}).get("cmd") or "")
        resp_message = str(resp.get("message") or "")
        # A recorder path hint begins with @ and is diagnostic only. Prefer a
        # real action recovered from late POST data / echoed response whenever
        # one exists, otherwise retain the hint so local review can show that a
        # mutation happened without pretending it is a replayable AT message.
        message = echoed_message or (late_message if not late_message.startswith("@") else "") or (row_message if not row_message.startswith("@") else "") or resp_message or late_message or row_message
        enriched["message"] = message
        enriched["post"] = merged
        out.append(enriched)
    return out



_TERMS = {
    "deck_switch": ("deck", "select", "current"),
    "hero_switch": ("hero", "commander", "leader"),
    "rumble": ("rumble", "guild", "battle"),
    "rumble_defense": ("rumble", "defense", "defence", "deck"),
    "siege": ("siege", "guild", "battle"),
    "siege_defense": ("siege", "defense", "defence", "deck"),
    "siege_island": ("island", "siege", "slot", "node"),
    "swole_status": ("swole", "status", "training"),
    "swole_training": ("swole", "train", "training"),
    "swole_battle": ("swole", "fight", "battle", "challenge"),
    "mail_claim_all": ("mail", "claim", "gift"),
    "mail_delete": ("mail", "delete", "remove"),
    "item_spend": ("spend", "item", "use", "consume", "purchase"),
    "item_choice": ("choice", "select", "reward", "card", "item"),
    "nixon_spend": ("nixon", "coin", "pack", "purchase", "buy"),
    "golden_pack_spend": ("golden", "turd", "pack", "purchase", "buy"),
    "recycle_cards": ("recycle", "salvage"),
    "daily_upgrade": ("upgrade", "level", "unit", "card"),
    "adventure_progress": ("mission", "adventure", "island", "campaign", "quest", "stage"),
    "adventure_start": ("mission", "adventure", "start", "battle"),
    "challenge_battle": ("challenge", "fight", "battle", "event"),
}

_NOISE = {
    "init",
    "getstaticfiles",
    "getinventory",
    "getcombomastery",
    "getcombomasteries",
    "ping",
    "heartbeat",
    "playbattlefieldcard",
}
_MUTATION_WORDS = (
    "set", "select", "change", "update", "save", "start", "join", "assign",
    "claim", "delete", "remove", "spend", "buy", "purchase", "recycle",
    "salvage", "upgrade", "train", "equip", "activate", "use",
)


def _candidate_score(key: str, row: dict[str, Any]) -> int:
    msg = str(row.get("message") or "").strip()
    low = msg.lower()
    post = row.get("post") or {}
    query = row.get("query") or {}
    keys = " ".join([*(str(k) for k in post.keys()), *(str(k) for k in query.keys())]).lower()
    hay = f"{low} {keys}"

    # Require an action-specific fingerprint for operations where a generic
    # background mutation would be dangerous to auto-link. The old learner could
    # otherwise mistake a nearby training/prefs call for Upgrade/Swole battle.
    post_keys={str(k).lower() for k in post}
    if key == "daily_upgrade" and not ("upgrade" in low or "enhance" in low or any(any(t in k for t in ("unit","card","upgrade")) for k in post_keys)):
        return -250
    if key == "swole_battle" and not (any(t in low for t in ("fight","battle","challenge")) or any(any(t in k for t in ("battle","challenge","event")) for k in post_keys)):
        return -250
    if key == "challenge_battle" and not (any(t in low for t in ("fight","battle","challenge")) or any(any(t in k for t in ("battle","challenge","event")) for k in post_keys)):
        return -250
    if key == "item_choice" and not (any(t in low for t in ("choice","choose","select","reward")) or any(any(t in k for t in ("choice","reward","selected")) for k in post_keys)):
        return -250
    if key == "mail_claim_all" and not any(t in hay for t in ("mail","claim","gift")):
        return -250
    if key == "mail_delete" and not ("mail" in hay and any(t in hay for t in ("delete","remove"))):
        return -250
    if key == "golden_pack_spend" and not ("buy" in low or "purchase" in low or "store" in hay or "pack" in hay):
        return -250

    score = 0
    for term in _TERMS.get(key, ()):  # specific action vocabulary
        if term in hay:
            score += 24
    if any(v in low for v in _MUTATION_WORDS):
        score += 28
    if post:
        score += 18
    if low in _NOISE:
        score -= 160
    if not msg:
        score -= 100

    # Strong semantic parameter hints.
    if key == "deck_switch" and any("deck" in str(k).lower() for k in post):
        score += 70
    if key == "hero_switch" and any(any(t in str(k).lower() for t in ("hero", "commander", "leader")) for k in post):
        score += 70
    if key == "siege_island" and any(any(t in str(k).lower() for t in ("island", "slot", "node")) for k in post):
        score += 70
    if key == "siege":
        # A valid live Siege capture should be the actual fightGuildSiege mutation,
        # preferably carrying AT's opaque/nested combat_data. Do not let a nearby
        # status refresh win the auto-link just because it happened in the window.
        if low == "fightguildsiege":
            score += 180
        if any("combat" in str(k).lower() for k in post):
            score += 220
        if any(any(t in str(k).lower() for t in ("slot", "location", "defender", "target")) for k in post):
            score += 55
    if key.endswith("_defense") and any(any(t in str(k).lower() for t in ("defense", "defence", "deck")) for k in post):
        score += 45
    if key == "swole_training" and any("train" in str(k).lower() or "hero" in str(k).lower() for k in post):
        score += 45
        if low == "startherotraining" and any("hero" in str(k).lower() for k in post): score += 120
        if low == "completeherotraining": score -= 80
    if key == "swole_battle" and ("swole" in low or "fight" in low or "battle" in low):
        score += 70
    if key == "adventure_start" and any("mission" in str(k).lower() for k in post):
        score += 55
    if key == "daily_upgrade" and ("upgrade" in low or any(any(t in str(k).lower() for t in ("unit","card","upgrade")) for k in post)):
        score += 110
        if any(t in low for t in ("upgradeunit","upgradecard","levelunit","upgrade")): score += 60
    if key == "adventure_progress" and any(t in low for t in ("mission","quest","campaign","adventure")):
        score += 45
    if key == "challenge_battle" and ("challenge" in low or any(any(t in str(k).lower() for t in ("challenge","event")) for k in post)):
        score += 90

    # Read/status actions are allowed to be GET-like/API read messages.
    if key in {"swole_status", "adventure_progress"} and any(t in hay for t in _TERMS[key]):
        score += 20
    return score


def _siege_combat_template(value: Any) -> Any:
    """Preserve a captured Siege combat-data shape while replacing live IDs.

    AT has returned "Invalid combat data" when fightGuildSiege is called with
    guessed top-level IDs. A successful browser request may instead carry a
    nested/JSON combat_data blob. Keep that exact structure but parameterize the
    target-specific identifiers so the profile can follow A → B → C later.
    """
    was_string=isinstance(value,str)
    obj=value
    if was_string:
        try: obj=json.loads(value)
        except Exception: return value
    def walk(v):
        if isinstance(v,dict):
            out={}
            for k,x in v.items():
                lk=str(k).lower()
                if 'event' in lk and 'id' in lk: out[k]='{event_id}'
                elif 'faction' in lk and 'id' in lk: out[k]='{enemy_faction_id}'
                elif 'battleground' in lk and 'id' in lk: out[k]='{battleground_id}'
                elif any(t in lk for t in ('slot','island','node')) and ('id' in lk or lk in {'slot','island','node'}): out[k]='{target_slot_id}'
                elif 'location' in lk and ('id' in lk or lk=='location'): out[k]='{target_location_id}'
                elif any(t in lk for t in ('target_user','defender','user')) and ('id' in lk or lk in {'target_user','defender','user'}): out[k]='{target_user_id}'
                else: out[k]=walk(x)
            return out
        if isinstance(v,list): return [walk(x) for x in v]
        return v
    templ=walk(obj)
    if was_string:
        try:return json.dumps(templ,separators=(',',':'))
        except Exception:return value
    return templ


def _profile_from_row(key: str, row: dict[str, Any]) -> dict[str, Any]:
    post = dict(row.get("post") or {})
    captured_action = dict(post)
    for secret in ("password", "password_hash", "user_id", "message", "auth", "cookie", "session"):
        post.pop(secret, None)
    for k in list(post):
        lk = str(k).lower()
        if key == "siege" and "combat_data" in lk:
            post[k] = _siege_combat_template(post[k])
        elif "deck" in lk:
            post[k] = "{deck_id}"
        elif any(t in lk for t in ("hero", "commander", "leader")):
            post[k] = "{hero_id}"
        elif any(t in lk for t in ("island", "slot", "node")) and key == "siege_island":
            post[k] = "{island_id}"
        elif key == "siege" and "id" in lk and any(t in lk for t in ("slot", "island", "node", "position")):
            post[k] = "{target_slot_id}"
        elif key == "siege" and "id" in lk and any(t in lk for t in ("location", "territory", "target", "enemy", "defender", "siege")):
            post[k] = "{target_location_id}"
        elif "mission" in lk:
            post[k] = "{mission_id}"
        elif key == "challenge_battle" and "challenge" in lk:
            post[k] = "{challenge_id}"
        elif key == "challenge_battle" and "event" in lk:
            post[k] = "{event_id}"
        elif key == "swole_battle" and "challenge" in lk:
            post[k] = "{challenge_id}"
        elif key == "swole_battle" and "event" in lk:
            post[k] = "{event_id}"
        elif key in {"mail_claim_all", "mail_delete"} and lk in {"mail_id", "message_id"}:
            post[k] = "{mail_id}"
        elif key in {"mail_claim_all", "mail_delete"} and lk in {"mail_type", "type"}:
            post[k] = "{mail_type}"
        elif key == "item_choice" and any(x in lk for x in ("choice", "reward", "card", "unit", "item")) and ("id" in lk or lk in {"choice","reward","card","unit","item"}):
            post[k] = "{choice_id}"
        elif key == "item_spend" and "id" in lk and any(x in lk for x in ("item", "consum", "reward", "currency", "product", "offer")):
            post[k] = "{item_id}"
        elif key in {"nixon_spend","golden_pack_spend"} and "id" in lk and any(x in lk for x in ("pack", "product", "offer", "store", "purchase", "item", "bundle")):
            post[k] = "{purchase_id}"
        elif any(x in lk for x in ("quantity", "amount", "count", "num")) and key in {"item_spend", "nixon_spend", "golden_pack_spend"}:
            post[k] = "{quantity}"
        elif key == "daily_upgrade" and "unit_index" in lk:
            post[k] = "{unit_index}"
        elif key == "daily_upgrade" and "id" in lk and any(x in lk for x in ("unit", "card")):
            post[k] = "{unit_id}"
    profile = {
        "message": str(row.get("message") or ""),
        "data": post,
        "learned_at": time.time(),
        "source_trace": str(row.get("label") or key),
    }
    # Siege starts succeed in the live browser with only slot_id visible as an
    # action field, yet the same minimal Python HTTP request is rejected as
    # "Invalid combat data." Keep the target-local CDP request id so Commander
    # can replay the browser's exact signed/enveloped XHR instead of rebuilding it.
    if key == "siege":
        if row.get("request_id") is not None:
            profile["browser_request_id"] = str(row.get("request_id"))
        if row.get("target_id") is not None:
            profile["browser_target_id"] = str(row.get("target_id"))
        profile["captured_data"] = captured_action
    # Challenge-backed fights can require the live browser/client combat context
    # even when their visible POST fields are correct. Preserve the target id on
    # any future re-learn so 1.7.13 can attach to the exact game target instead of
    # relying on URL heuristics. Existing profiles remain compatible.
    elif key in {"challenge_battle","swole_battle","adventure_start","nixon_spend","golden_pack_spend"}:
        if row.get("request_id") is not None:
            profile["browser_request_id"] = str(row.get("request_id"))
        if row.get("target_id") is not None:
            profile["browser_target_id"] = str(row.get("target_id"))
        profile["captured_data"] = captured_action
    return profile


def profile_candidates_from_trace(key: str, path: Path) -> list[dict[str, Any]]:
    rows = [r for r in _enriched_request_rows(path) if r.get("message")]
    candidates = []
    for row in rows:
        score = _candidate_score(key, row)
        if score <= -50:
            continue
        candidates.append(
            {
                "score": score,
                "message": str(row.get("message") or ""),
                "keys": sorted(str(k) for k in (row.get("post") or {}).keys()),
                "profile": _profile_from_row(key, row),
            }
        )
    # Upgrade captures have repeatedly returned no semantic match. If the broad
    # recorder saw AT API mutations but none passed the upgrade fingerprint,
    # surface them for manual local review instead of claiming nothing happened.
    if not candidates and key == "daily_upgrade":
        for row in rows:
            msg=str(row.get("message") or "").strip(); post=row.get("post") or {}
            if not msg or msg.lower() in _NOISE or not post: continue
            candidates.append({"score":1,"message":msg,"keys":sorted(str(k) for k in post.keys()),"profile":_profile_from_row(key,row)})

    # Keep the best occurrence of each message+key-shape.
    unique: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for c in candidates:
        sig = (c["message"], tuple(c["keys"]))
        if sig not in unique or c["score"] > unique[sig]["score"]:
            unique[sig] = c
    return sorted(unique.values(), key=lambda c: c["score"], reverse=True)


def auto_profile_from_trace(key: str, path: Path) -> dict[str, Any] | None:
    cands = profile_candidates_from_trace(key, path)
    if not cands:
        return None
    best = cands[0]
    if str((best.get("profile") or {}).get("message") or "").startswith("@"):
        return None
    # Isolated capture + high semantic score: safe to auto-link. If only one
    # meaningful request happened, accept a lower score too.
    if best["score"] >= 38 or (len(cands) == 1 and best["score"] >= 12):
        return best["profile"]
    # A large lead over the runner-up is also strong evidence.
    if len(cands) > 1 and best["score"] >= 25 and best["score"] - cands[1]["score"] >= 25:
        return best["profile"]
    return None


def install_candidate_profile(key: str, candidate: dict[str, Any]) -> bool:
    p = candidate.get("profile") if isinstance(candidate, dict) else None
    if not isinstance(p, dict) or not p.get("message") or str(p.get("message")).startswith("@"):
        return False
    profiles = load_profiles()
    profiles[key] = p
    save_profiles(profiles)
    return True


def install_profile(key: str, path: Path) -> bool:
    p = auto_profile_from_trace(key, path)
    if not p:
        return False
    profiles = load_profiles()
    profiles[key] = p
    save_profiles(profiles)
    return True


def install_siege_flow_profile(path: Path) -> tuple[bool, list[str]]:
    """Capture the complete short API sequence that precedes a successful Siege start.

    On the user's account the final fightGuildSiege request exposes only slot_id,
    yet replaying that one call outside the browser returns "Invalid combat data".
    That strongly indicates the web client primes server-side combat state with one
    or more calls immediately before fightGuildSiege. Preserve that short sequence
    and replay it in order rather than guessing hidden combat_data fields.
    """
    rows = _enriched_request_rows(path)
    if not rows:
        return False, []
    fight_idx = None
    for i, row in enumerate(rows):
        if str(row.get("message") or "").lower() == "fightguildsiege":
            fight_idx = i
    if fight_idx is None:
        return False, []
    fight_row = rows[fight_idx]
    try:
        final_ts = float(fight_row.get("ts") or 0)
    except Exception:
        final_ts = 0.0

    # Keep only the brief setup burst before the successful start. The learning
    # instructions already isolate one manual Siege start, so this sequence is
    # intentionally permissive while excluding obvious battle-turn/noise calls.
    keep=[]
    for i, row in enumerate(rows[:fight_idx+1]):
        try: ts=float(row.get("ts") or 0)
        except Exception: ts=0.0
        if final_ts and ts and final_ts-ts > 12.0:
            continue
        msg=str(row.get("message") or "").strip()
        low=msg.lower()
        if not msg or msg.startswith("@"):
            continue
        if low in {"init","getstaticfiles","getinventory","getcombomastery","getcombomasteries","ping","heartbeat","playbattlefieldcard"}:
            continue
        # Never include a second battle mutation from an old/overlapping capture.
        if "fight" in low and low != "fightguildsiege":
            continue
        keep.append(row)

    # Bound the replay sequence. The final call must always be present.
    if len(keep) > 10:
        keep = keep[-10:]
    flow=[]
    names=[]
    for row in keep:
        prof=_profile_from_row("siege", row)
        # CDP request ids are per-browser-request and become stale; flow replay is
        # API driven, so strip those transient ids from each step.
        prof.pop("browser_request_id", None)
        prof.pop("browser_target_id", None)
        prof.pop("captured_data", None)
        flow.append(prof)
        names.append(str(prof.get("message") or ""))
    if not flow or str(flow[-1].get("message") or "").lower() != "fightguildsiege":
        return False, names

    final = _profile_from_row("siege", fight_row)
    final.pop("browser_request_id", None)
    final.pop("browser_target_id", None)
    final["flow"] = flow
    final["flow_messages"] = names
    final["learned_at"] = time.time()
    profiles=load_profiles(); profiles["siege"]=final; save_profiles(profiles)
    return True, names


def migrate_last_siege_browser_replay_metadata() -> bool:
    """Enrich a 1.7.7 Siege profile from the already-saved local learn trace.

    1.7.7 recorded request_id but did not persist it into protocol_profiles.json.
    Reuse that local trace so the user does not have to spend another Siege attack
    merely to teach the same successful fightGuildSiege call again.
    """
    profiles=load_profiles(); cur=profiles.get('siege') or {}
    if isinstance(cur,dict) and cur.get('browser_request_id'):
        return False
    path=APP_DIR/'learn_siege_sanitized.jsonl'
    if not path.exists():return False
    try:cands=profile_candidates_from_trace('siege',path)
    except Exception:return False
    hit=next((c for c in cands if str((c.get('profile') or {}).get('message') or '').lower()=='fightguildsiege' and (c.get('profile') or {}).get('browser_request_id')),None)
    if not hit:return False
    learned=hit.get('profile') or {}
    if isinstance(cur,dict):
        # Preserve the already-confirmed template/data while adding browser replay metadata.
        merged=dict(cur)
        for k in ('browser_request_id','browser_target_id','captured_data'):
            if learned.get(k) is not None:merged[k]=learned.get(k)
        profiles['siege']=merged
    else:profiles['siege']=learned
    save_profiles(profiles);return True


def migrate_last_native_replay_metadata(keys=("swole_battle","challenge_battle","adventure_start")) -> list[str]:
    """Enrich existing learned profiles from their already-saved local traces.

    Older builds kept only the reconstructed action fields for these calls. The
    local sanitized trace still contains Chromium request/target IDs, so recover
    those IDs without asking the user to spend another fight/pack when the same
    browser session is still alive. A stale ID is harmless; runners report that
    clearly and the user can refresh the one native capture only when necessary.
    """
    profiles=load_profiles(); changed=[]
    for key in keys:
        cur=profiles.get(key) or {}
        if isinstance(cur,dict) and cur.get("browser_request_id"):
            continue
        path=APP_DIR/f"learn_{key}_sanitized.jsonl"
        if not path.exists():
            continue
        try:cands=profile_candidates_from_trace(key,path)
        except Exception:continue
        hit=next((c for c in cands if (c.get("profile") or {}).get("browser_request_id")),None)
        if not hit:continue
        learned=hit.get("profile") or {}
        merged=dict(cur) if isinstance(cur,dict) else {}
        if not merged.get("message"): merged.update(learned)
        for k in ("browser_request_id","browser_target_id","captured_data"):
            if learned.get(k) is not None: merged[k]=learned.get(k)
        profiles[key]=merged; changed.append(key)
    if changed: save_profiles(profiles)
    return changed
