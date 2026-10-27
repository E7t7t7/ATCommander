"""Automatic later Mom's Box ads through genuine native Watch and ad Close.

Every stage uses observed public control artwork. Input stays in Commander's
dedicated game browser; no calibration, purchase or completion API is used.
"""
from __future__ import annotations

import time

from .ad_visual_templates import TEMPLATES as AD_TEMPLATES
from .mom_ad_targets import freeze_mom_ad_target, mom_ad_target_unchanged, verify_mom_ad_target


AREAS = {
    "mom_title": [.15, .85, 0, .30],
    "mom_watch_button": [0, 1, .50, 1],
    "mom_prompt_title": [.15, .85, 0, .35],
    "mom_prompt_watch_button": [.10, .90, .50, 1],
    "mom_entry": [0, 1, 0, 1],
    "shop_entry": [0, 1, 0, 1],
    "shop_title": [.15, .85, 0, .30],
    "mom_shop_tile": [0, 1, .10, 1],
    "native_popup_title": [.15, .85, 0, .35],
    "close": [.55, 1, 0, .32],
}


def _view(prep):
    from .automatic_ads import _view as surface, classify_pixels
    from .mom_ad_templates import TEMPLATES as MOM_TEMPLATES
    templates = {**MOM_TEMPLATES, "close": AD_TEMPLATES["close"]}
    areas = {key: AREAS.get(key, [0, 1, 0, 1]) for key in templates}
    return surface(prep, lambda shot, evaluate: classify_pixels(shot, evaluate, templates=templates, areas=areas))


def _click(prep, canvas, point):
    # Unity needs the same trusted down/hold/up browser input as research.
    from .research_ad_ui import _click as click
    click(prep, canvas, point)


def _fresh_target(client):
    return client.init(), client.call("getStoreData")


def play_mom_ad(client, log=None, should_stop=None, wait_seconds=100):
    """Run one normal rewarded playback and verify its pull without gem spending.

    A native first-free offer is excluded. After an uncertain Watch, Close or
    server result, this call never starts another playback or sends a reward call.
    """
    from . import ad_ui as ui
    emit = log or (lambda _message: None)
    stopped = should_stop or (lambda: False)

    def failure(message, started=False, closed=False):
        return {"ok": False, "playback_started": started, "closed_completed_ad": closed, "message": message}

    if stopped():
        return failure("Mom's Box ad stopped before playback.")
    try:
        prep = ui.prepare_ad_game_ui(client=client, log=emit, should_stop=stopped, wait_for_canvas=60)
    except Exception:
        return failure("The game browser could not be prepared. No Mom's Box ad was started.")
    if not isinstance(prep, dict) or not prep.get("ok"):
        return failure("The real game did not load. No Mom's Box ad was started.")
    if stopped():
        return failure("Mom's Box ad stopped before playback.")
    try:
        # Settling an existing reward may change the offer. Freeze its fresh
        # identity only after verified startup navigation reaches the game.
        live, store = _fresh_target(client)
        target = freeze_mom_ad_target(live, store)
    except Exception:
        return failure("A current later Mom's Box offer was not verified. No ad was started.")
    try:
        duration = max(20, min(150, float(wait_seconds)))
    except (TypeError, ValueError):
        return failure("The Mom's Box playback wait could not be read. No ad was started.")
    deadline = time.monotonic() + duration
    started = closed_at = last_read = None
    route = set()
    dismissals = ad_frames = 0
    previous = previous_close = None
    while time.monotonic() < deadline:
        if stopped():
            return failure("Mom's Box ad stopped; any pending pull must be checked before another playback.",
                           started is not None, closed_at is not None)
        try:
            matches, canvas = _view(prep)
        except Exception:
            return failure("The Mom's Box controls could not be read. Playback was not repeated.",
                           started is not None, closed_at is not None)
        panel = "mom_title" in matches
        prompt = "mom_prompt_title" in matches
        native_screen = panel or prompt or any(key in matches for key in
            ("shop_title", "shop_entry", "mom_entry", "mom_shop_tile", "native_popup_title"))
        if started is None:
            action = None
            if prompt and "mom_prompt_watch_button" in matches:
                action = ("watch", matches["mom_prompt_watch_button"])
            elif panel and "mom_watch_button" in matches:
                action = ("watch", matches["mom_watch_button"])
            elif "shop_title" in matches and "mom_shop_tile" in matches and "mom" not in route:
                action = ("mom", matches["mom_shop_tile"])
            elif "mom_entry" in matches and "mom" not in route:
                action = ("mom", matches["mom_entry"])
            elif "shop_entry" in matches and "shop" not in route:
                action = ("shop", matches["shop_entry"])
            elif "native_popup_title" in matches and "close" in matches and dismissals < 3:
                action = ("dismiss", matches["close"])
            if action is None:
                previous = None
            else:
                name, point = action
                try:
                    identity = (name, round(point["x"], 2), round(point["y"], 2))
                except (TypeError, KeyError):
                    return failure("The detected Mom's Box input could not be verified. No input was sent.")
                if identity != previous:
                    previous = identity
                else:
                    if name == "watch":
                        try:
                            current, store = _fresh_target(client)
                            unchanged = mom_ad_target_unchanged(target, current, store, native_watch_available=True)
                        except Exception:
                            unchanged = False
                        if not unchanged:
                            return failure("The Mom's Box offer changed before Watch. No ad was started.")
                        started = time.monotonic()
                    elif name == "dismiss":
                        dismissals += 1
                    else:
                        route.add(name)
                    previous = None
                    try:
                        _click(prep, canvas, point)
                    except Exception:
                        return failure("Mom's Box browser input has an unknown outcome and was not repeated.", started is not None)
                    emit("Mom's Box ad: selected the real Watch control." if name == "watch"
                         else "Mom's Box ad: navigating the detected native controls.")
        elif closed_at is None:
            elapsed = time.monotonic() - started
            ad_frames = 0 if native_screen else ad_frames + 1
            if native_screen and elapsed >= 5:
                return failure("The game returned without completed Mom's Box ad playback. No ad was repeated.", True)
            if not native_screen and ad_frames >= 3 and elapsed >= 12 and "close" in matches:
                point = matches["close"]
                try:
                    identity = (round(point["x"], 2), round(point["y"], 2))
                except (TypeError, KeyError):
                    return failure("The completed ad Close control could not be verified. No close was guessed.", True)
                if previous_close != identity:
                    previous_close = identity
                else:
                    closed_at = time.monotonic()
                    try:
                        _click(prep, canvas, point)
                    except Exception:
                        # An uncertain delivered Close can only be resolved by
                        # fresh server credit; the input is never repeated.
                        pass
                    emit("Mom's Box ad: closed finished playback using the ad's own Close control.")
            else:
                previous_close = None
        elif time.monotonic() - closed_at >= 3 and (last_read is None or time.monotonic() - last_read >= 8):
            last_read = time.monotonic()
            try:
                after = client.init()
                confirmed = verify_mom_ad_target(target, after)
            except Exception:
                confirmed = False
            if confirmed:
                return {"ok": True, "verified": True, "playback_started": True,
                        "closed_completed_ad": True, "pulls": 1, "gems_spent": 0,
                        "pending_choice": bool(after.get("active_choices")),
                        "message": "AT confirmed one real rewarded Mom's Box pull; spent 0 gems."}
            if time.monotonic() - closed_at >= 24:
                return failure("AT did not confirm the exact Mom's Box pull without gem spending. No ad was repeated.", True, True)
        time.sleep(1)
    return failure("The game did not expose a completed Mom's Box ad within the wait. No ad was repeated.",
                   started is not None, closed_at is not None)
