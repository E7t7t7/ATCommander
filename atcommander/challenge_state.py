"""Resolve fight parameters only from a current active challenge event."""
from __future__ import annotations
from typing import Any


def current_swole(source:dict[str,Any],*,now:Any=None)->dict[str,Any]|None:
    """Resolve Swole from the already-fresh account without extra init calls."""
    seen=set()
    def walk(obj,depth=0):
        if depth>8 or not isinstance(obj,(dict,list)) or id(obj) in seen:return
        seen.add(id(obj))
        if isinstance(obj,dict):
            raw=obj.get("active_events")
            entries=raw.values() if isinstance(raw,dict) else raw if isinstance(raw,list) else []
            for row in entries:
                if isinstance(row,dict) and "swole" in str(row.get("name","")).casefold():yield row
            for key,value in obj.items():
                if key!="upcoming_events":yield from walk(value,depth+1)
        else:
            for value in obj:yield from walk(value,depth+1)
    for event in walk(source):
        current=current_challenge(source,str(event.get("id") or ""),now=now)
        if current:return current
    return None


def current_challenge(source: dict[str, Any], event_id: str, *, now: Any = None) -> dict[str, Any] | None:
    """Return explicit challenge data; rejected, upcoming and ended rows never qualify.

    Active events may appear under compact account wrappers. The outer event ID
    identifies the selected event, while challenge_data.id identifies its fight.
    Neither a remembered level nor the outer event ID is a safe substitute.
    """
    if not isinstance(source, dict) or source.get("result") is False or not event_id:
        return None
    def number(value):
        try: return int(value)
        except (TypeError, ValueError, OverflowError): return None
    server_time = number(source.get("time") or now)
    if server_time is None or server_time <= 0:
        return None
    seen = set()
    def walk(obj):
        if not isinstance(obj, (dict, list)) or id(obj) in seen:
            return
        seen.add(id(obj))
        if isinstance(obj, dict):
            if obj.get("result") is False:
                return
            raw = obj.get("active_events")
            rows = raw.values() if isinstance(raw, dict) else raw if isinstance(raw, list) else []
            for row in rows:
                if isinstance(row, dict) and str(row.get("id") or "") == str(event_id):
                    yield row
            for key, value in obj.items():
                if key != "upcoming_events":
                    yield from walk(value)
        else:
            for value in obj:
                yield from walk(value)
    for row in walk(source):
        if row.get("result") is False:
            continue
        start = number(row.get("start_time"))
        end = number(row.get("end_time"))
        if (start and server_time < start) or (end and server_time >= end):
            continue
        cd = row.get("challenge_data")
        if not isinstance(cd, dict):
            continue
        challenge_id = str(cd.get("id") or row.get("challenge") or "")
        level = number(cd.get("level"))
        energy = cd.get("energy") if isinstance(cd.get("energy"), dict) else {}
        current = number(energy.get("current_value"))
        cost = number(energy.get("battle_cost")) or 1
        if not challenge_id or level is None or level < 0 or current is None or cost < 1:
            continue
        return {"event_id": str(event_id), "challenge_id": challenge_id, "level": level,
                "event_name": str(row.get("name") or ""),
                "points": number(row.get("points")),
                "current": current, "max": number(energy.get("max_value")), "cost": cost,
                "name": str(energy.get("display_name") or "Challenge energy")}
    return None
