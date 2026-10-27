"""Resolve PvP Challenges from the current client's explicit type marker.

The public LTEManager.ChallengeType enum defines PvP as 2. PvP Challenges
start through the shared signed startChallenge endpoint. The current public
client provides no separate PvP token-target selection/refresh protocol.
"""
from __future__ import annotations

from typing import Any

from .challenge_state import current_challenge


def _active_events(source):
    seen = set()

    def walk(value):
        if not isinstance(value, (dict, list)) or id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, dict):
            if value.get('result') is False:
                return
            raw = value.get('active_events')
            rows = raw.values() if isinstance(raw, dict) else raw if isinstance(raw, list) else []
            yield from (row for row in rows if isinstance(row, dict))
            for key, child in value.items():
                if key not in ('active_events', 'upcoming_events'):
                    yield from walk(child)
        else:
            for child in value:
                yield from walk(child)

    yield from walk(source)


def resolve_pvp_challenge(
    source: dict[str, Any],
    event_id: str | int | None = None,
    *,
    now: Any = None,
) -> dict[str, Any] | None:
    """Select one fresh active type-2 event, or fail closed on ambiguity.

    An explicit event ID cannot redirect to another event. Auto-selection works
    only when exactly one eligible active PvP Challenge is exposed. Energy may
    be zero: callers apply their own resource reserve before starting combat.
    """
    if not isinstance(source, dict) or source.get('result') is False:
        return None
    requested = str(event_id) if event_id not in (None, '') else None
    candidates = {}
    for row in _active_events(source):
        if row.get('result') is False:
            continue
        cid = str(row.get('id') or '')
        if not cid or requested is not None and cid != requested:
            continue
        challenge = row.get('challenge_data')
        if not isinstance(challenge, dict):
            continue
        marker = challenge.get('challenge_type')
        if isinstance(marker, bool) or str(marker) != '2':
            continue
        # Isolate this verified row so repeated payload wrappers cannot cause
        # current_challenge to select a different type sharing the event ID.
        current = current_challenge({'result': True, 'time': source.get('time') or now,
                                     'active_events': [row]}, cid, now=now)
        if current is not None:
            candidates[cid] = {**current, 'challenge_type': 2,
                               'event_name': str(row.get('name') or 'PvP Challenge')}
    return next(iter(candidates.values())) if len(candidates) == 1 else None
