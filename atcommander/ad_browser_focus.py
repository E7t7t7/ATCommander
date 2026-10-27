"""Keep Unity input enabled without repeatedly raising its browser window."""
from __future__ import annotations

import atexit
import json
import threading
import time
import weakref
from collections.abc import MutableMapping

import websocket


_LIVE_SESSIONS = weakref.WeakSet()


class GameInputStopped(RuntimeError):
    """A logical click was cancelled before its pointer press began."""


def check_game_input_stop(prep):
    stopped=prep.get("_commander_should_stop") if isinstance(prep,MutableMapping) else None
    if callable(stopped) and stopped():
        raise GameInputStopped("Game browser input stopped.")


class _RetainedFocusSession:
    """Keep emulation alive: Chromium clears it when this session detaches.

    This connection issues only a focus override and literal read-only health
    checks. Account requests and pointer input continue on their existing
    transports. A lost connection stops input instead of replaying it.
    """

    def __init__(self, ws_url):
        self._socket = None
        self._lock = threading.RLock()
        self._serial = 0
        try:
            self._socket = websocket.create_connection(ws_url, timeout=4, suppress_origin=True)
            self._request("Emulation.setFocusEmulationEnabled", {"enabled": True})
        except Exception:
            self.close()
            raise RuntimeError("The game focus connection is unavailable.") from None
        _LIVE_SESSIONS.add(self)

    def _request(self, method, params):
        with self._lock:
            if self._socket is None:
                raise RuntimeError("The game focus connection is unavailable.")
            try:
                self._serial += 1
                serial = self._serial
                deadline = time.monotonic() + 4.0
                self._socket.send(json.dumps({"id": serial, "method": method, "params": params}))
                for _ in range(256):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._socket.settimeout(remaining)
                    reply = json.loads(self._socket.recv())
                    if not isinstance(reply, dict) or reply.get("id") != serial:
                        continue
                    if reply.get("error") or not isinstance(reply.get("result"), dict):
                        break
                    return reply["result"]
            except Exception:
                pass
            self.close()
            raise RuntimeError("The game focus connection is unavailable.") from None

    def check(self):
        # Read no page data. A response proves the original session is still
        # attached, so its acknowledged focus override remains active.
        reply = self._request("Runtime.evaluate", {"expression": "true", "returnByValue": True})
        result = reply.get("result")
        if reply.get("exceptionDetails") or not isinstance(result, dict) or result.get("value") is not True:
            self.close()
            raise RuntimeError("The game focus connection is unavailable.")

    def close(self):
        with self._lock:
            sock, self._socket = self._socket, None
            if sock is not None:
                try:
                    sock.close(timeout=0)
                except Exception:
                    pass

    def __del__(self):
        self.close()


def release_all_game_input_focus():
    """Close Commander's retained focus sessions during host/app shutdown."""
    for session in list(_LIVE_SESSIONS):
        session.close()


atexit.register(release_all_game_input_focus)


def release_game_input_focus(prep):
    """Release retained focus when a prepared browser is closed/replaced."""
    if not isinstance(prep, MutableMapping):
        return
    cache = prep.pop("_commander_game_focus", None)
    if isinstance(cache, dict):
        for session in list((cache.get("sessions") or {}).values()):
            session.close()


def ensure_game_input_focus(prep, cdp, *, session_factory=None):
    """Initialize focus once for each frame in one prepared game browser.

    Embedded hosts retain their parent application's window placement. Focus
    emulation is held by at most two live CDP sessions per prepared browser.
    Failure propagates before any game pointer input; successful setup is kept
    when a later frame cannot be initialized.
    """
    if not isinstance(prep, MutableMapping):
        raise RuntimeError("The prepared game browser is unavailable.")
    check_game_input_stop(prep)
    game = prep.get("target")
    target = prep.get("input_target") or game
    frames = []
    for frame in (target, game):
        ws = frame.get("webSocketDebuggerUrl") if isinstance(frame, MutableMapping) else None
        if not isinstance(ws, str) or not ws:
            raise RuntimeError("The prepared game input frame is unavailable.")
        if ws not in frames:
            frames.append(ws)
    cache = prep.get("_commander_game_focus")
    if not isinstance(cache, dict) or not isinstance(cache.get("raised"), set) or not isinstance(cache.get("sessions"), dict):
        release_game_input_focus(prep)
        cache = {"raised": set(), "sessions": {}}
        prep["_commander_game_focus"] = cache
    # Navigation may replace an iframe. Its old connection must neither keep
    # that obsolete frame focused nor accumulate with new preparations.
    for ws in list(cache["sessions"]):
        if ws not in frames:
            cache["sessions"].pop(ws).close()
    cache["raised"].intersection_update(frames)
    if prep.get("embedded") is not True and frames[0] not in cache["raised"]:
        cdp(frames[0], "Page.bringToFront", {})
        cache["raised"].add(frames[0])
    factory = session_factory or _RetainedFocusSession
    for ws in frames:
        check_game_input_stop(prep)
        session = cache["sessions"].get(ws)
        if session is None:
            cache["sessions"][ws] = factory(ws)
        else:
            try:
                session.check()
            except Exception:
                cache["sessions"].pop(ws).close()
                raise
    check_game_input_stop(prep)
