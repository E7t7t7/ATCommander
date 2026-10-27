"""Fill the native ad chamber with exact owned cards and watch a real ad once."""
from __future__ import annotations

import time

from .research_ad_targets import (
    select_research_unlock_target, research_unlock_target_unchanged,
    verify_research_unlock_target,
)


def _exact_selection(state, target):
    return bool(isinstance(state, dict) and state.get('ok') is True
                and state.get('verified_public_build') is True
                and state.get('chamber') == 3 and state.get('ad_locked') is True
                and state.get('selected_card_ids') == [target['card1'], target['card2']])


def play_research_unlock_ad(client, context, log=None, should_stop=None, wait_seconds=140):
    """Use ordinary navigation/selection/Watch/Close, then exact server evidence.

    A Watch input is sent at most once. An uncertain delivered input is never
    replayed. Paid controls and reward completion APIs are never invoked.
    """
    from . import ad_ui as ui
    from .research_ad_ui import _view, _click
    from .native_research_ui import select_native_research_pair, read_native_research_selection
    from .ad_browser_focus import GameInputStopped

    emit = log or (lambda _message: None)
    stopped = should_stop or (lambda: False)

    def failure(message, started=False, closed=False):
        return {'ok': False, 'phase': 'unlock', 'playback_started': started,
                'closed_completed_ad': closed, 'message': message}

    if stopped():
        return failure('Research ad stopped before selecting cards.')
    try:
        duration = max(30, min(240, float(wait_seconds)))
        prep = ui.prepare_ad_game_ui(client=client, log=emit, should_stop=stopped, wait_for_canvas=60)
    except Exception:
        return failure('The exact owned pair and native ad chamber could not be prepared. No ad was started.')
    if not isinstance(prep, dict) or not prep.get('ok'):
        return failure('The real game did not load. No research ad was started.')
    if stopped():
        return failure('Research ad stopped after preparing the game. No ad was started.')
    prep['_commander_should_stop'] = stopped
    try:
        # Startup choices/rewards may change inventory or gem mirrors. Freeze
        # their exact current state only after verified settlement finishes.
        target = select_research_unlock_target(client.init(), context)
    except Exception:
        return failure('The exact owned pair and empty ad chamber were not verified after startup. No ad was started.')
    if stopped():
        return failure('Research ad stopped before inspecting the native pair. No ad was started.')
    try:
        existing = read_native_research_selection(prep)
    except Exception:
        existing = None
    selected = _exact_selection(existing, target)
    if selected:
        emit('Research ad: reused the fresh verified exact native pair.')
    deadline = time.monotonic() + duration
    route, previous, previous_close = set(), None, None
    dismissals = ad_frames = 0
    started = closed_at = last_read = None
    while time.monotonic() < deadline:
        if stopped():
            return failure('Research ad stopped; any pending start must be checked before another playback.',
                           started is not None, closed_at is not None)
        try:
            matches, canvas = _view(prep)
        except Exception:
            return failure('Research controls could not be read. Playback was not repeated.',
                           started is not None, closed_at is not None)
        picker = 'research_picker_marker' in matches
        native_screen = picker or any(key in matches for key in (
            'research_hub_marker', 'research_entry', 'research_time_title',
            'research_boost_title', 'research_boost_description',
            'research_unlock_watch_button'))
        if started is None:
            action = None
            if not selected and 'inventory' in route:
                # Inventory's label can disappear as the ordinary picker
                # settles. The exact native popup/card guards establish the
                # selection surface after the single verified tab input.
                try:
                    selection = select_native_research_pair(
                        prep, [target['card1'], target['card2']], should_stop=stopped,
                        card1=target['card1'], card2=target['card2'])
                except Exception:
                    selection = None
                if not _exact_selection(selection, target):
                    return failure('The native picker did not confirm both exact cards. No research ad was started.')
                selected = True
                emit('Research ad: selected both exact owned cards through the native picker.')
                previous = None
                continue
            elif picker and not selected:
                action = ('inventory', matches['research_picker_marker'])
            elif selected and 'research_unlock_watch_button' in matches:
                action = ('watch', matches['research_unlock_watch_button'])
            elif {'research_hub_marker', 'research_ad_chamber'} <= matches.keys() and 'chamber' not in route:
                action = ('chamber', matches['research_ad_chamber'])
            elif 'research_entry' in matches and 'research' not in route:
                action = ('research', matches['research_entry'])
            elif 'close' in matches and not native_screen and dismissals < 3:
                action = ('dismiss', matches['close'])
            if action is None:
                previous = None
            else:
                name, point = action
                try:
                    identity = (name, round(point['x'], 2), round(point['y'], 2))
                except (TypeError, KeyError):
                    return failure('Research input could not be verified. No input was sent.')
                if previous != identity:
                    previous = identity
                else:
                    if name == 'watch':
                        try:
                            ready = research_unlock_target_unchanged(target, client.init())
                            exact = _exact_selection(read_native_research_selection(prep), target)
                        except Exception:
                            ready = exact = False
                        if not ready or not exact:
                            return failure('The selected pair or ad chamber changed before Watch. No ad was started.')
                        if stopped():
                            return failure('Research ad stopped before Watch. No research was started.')
                        started = time.monotonic()
                    elif name == 'dismiss':
                        dismissals += 1
                    else:
                        route.add(name)
                    previous = None
                    if stopped():
                        return failure('Research ad stopped before browser input. No research was started.')
                    try:
                        _click(prep, canvas, point)
                    except GameInputStopped:
                        if name == 'watch':
                            started = None  # The shared input guard stops before its press.
                        return failure('Research ad stopped before browser input.', started is not None)
                    except Exception:
                        return failure('Research browser input has an unknown outcome and was not repeated.',
                                       started is not None)
                    emit('Research ad: selected the real Watch control.' if name == 'watch'
                         else 'Research ad: navigating the detected native controls.')
        elif closed_at is None:
            elapsed = time.monotonic() - started
            ad_frames = 0 if native_screen else ad_frames + 1
            if native_screen and elapsed >= 5:
                return failure('The game returned without completed research ad playback. No ad was repeated.', True)
            if not native_screen and ad_frames >= 3 and elapsed >= 12 and 'close' in matches:
                point = matches['close']
                identity = (round(point['x'], 2), round(point['y'], 2))
                if previous_close != identity:
                    previous_close = identity
                else:
                    closed_at = time.monotonic()
                    try:
                        _click(prep, canvas, point)
                    except Exception:
                        pass  # Only fresh credit can resolve an uncertain Close.
                    emit("Research ad: closed completed playback using the ad's own Close control.")
            else:
                previous_close = None
        elif time.monotonic() - closed_at >= 3 and (last_read is None or time.monotonic() - last_read >= 8):
            last_read = time.monotonic()
            try:
                confirmed = verify_research_unlock_target(target, client.init())
            except Exception:
                confirmed = False
            if confirmed:
                return {'ok': True, 'verified': True, 'phase': 'unlock', 'slot_index': 3,
                        'pair': target['pair'], 'playback_started': True,
                        'closed_completed_ad': True, 'gems_spent': 0,
                        'message': 'AT confirmed the exact new research pair after real ad playback; spent 0 gems.'}
            if time.monotonic() - closed_at >= 24:
                return failure('AT did not confirm the exact new research task. No ad was repeated.', True, True)
        time.sleep(1)
    return failure('The game did not expose completed research ad playback within the wait. No ad was repeated.',
                   started is not None, closed_at is not None)
