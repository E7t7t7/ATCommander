"""Plan island completion from a fresh account and the current public catalogs."""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any
from .shop_rewards import api_rejected


class AdventureScanError(RuntimeError):
    pass


def read_adventure_energy(live: dict) -> tuple[int, int | None]:
    if not isinstance(live,dict) or api_rejected(live) or not isinstance(live.get("user_data"),dict):
        raise AdventureScanError("Fresh Adventure energy is unavailable.")
    user=live["user_data"]
    values=[_integer(user[key],"energy") for key in ("energy","current_energy","mission_energy","adventure_energy") if user.get(key) is not None]
    if not values or len(set(values))!=1:
        raise AdventureScanError("Adventure energy is missing or conflicting; no fight was started.")
    maxima=[_integer(user[key],"maximum energy") for key in ("max_energy","mission_max_energy","adventure_max_energy") if user.get(key) is not None]
    if len(set(maxima))>1:
        raise AdventureScanError("Adventure maximum energy values conflict.")
    return values[0],maxima[0] if maxima else None


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        result = int(value)
    else:
        raise AdventureScanError(f"Adventure {label} is missing or invalid; no route was assumed.")
    if result < minimum:
        raise AdventureScanError(f"Adventure {label} is invalid; no route was assumed.")
    return result


def _index(raw: Any, aliases: tuple[str, ...], label: str) -> dict[str, dict]:
    if not isinstance(raw, (dict, list)):
        raise AdventureScanError(f"Fresh Adventure {label} data is unavailable.")
    result = {}
    entries = raw.items() if isinstance(raw, dict) else [(None, row) for row in raw]
    for key, row in entries:
        if not isinstance(row, dict):
            raise AdventureScanError(f"Adventure {label} contains an invalid row.")
        identities = [_integer(row[field], label + " ID", 1) for field in aliases if field in row]
        if key is not None:
            identities.append(_integer(str(key), label + " ID", 1))
        if not identities or len(set(identities)) != 1:
            raise AdventureScanError(f"Adventure {label} IDs conflict; no route was assumed.")
        identity = str(identities[0])
        if identity in result:
            raise AdventureScanError(f"Adventure {label} IDs are duplicated.")
        result[identity] = row
    return result


def mission_progress(live: dict, mission_id: int | str, campaign_id: int | str | None = None) -> dict:
    """Read normalized mastery, including boss stages whose raw wins differ."""
    if not isinstance(live, dict) or api_rejected(live):
        raise AdventureScanError("The fresh Adventure account read was rejected.")
    current = _index(live.get("current_missions"), ("id", "mission_id"), "mission")
    completions = _index(live.get("mission_completions"), ("id", "mission_id"), "completion")
    return _progress(str(_integer(str(mission_id), "mission ID", 1)), current, completions, campaign_id)


def _progress(mid: str, current: dict, completions: dict, campaign_id: Any = None) -> dict:
    dynamic, saved = current.get(mid), completions.get(mid)
    if saved and campaign_id is not None and saved.get("campaign_id") not in (None,""):
        if str(_integer(saved["campaign_id"], "completion campaign ID", 1)) != str(campaign_id):
            raise AdventureScanError("Adventure stage progress belongs to a different island.")
    level = _integer(dynamic.get("level"), "mission level", 1) if dynamic else None
    if level is not None and level > 13:
        raise AdventureScanError("Adventure mission level exceeds the verified mastery range.")
    wins = _integer(saved.get("complete"), "completion count") if saved else (level - 1 if level is not None else 0)
    if level is not None and min(wins, 12) != level - 1:
        raise AdventureScanError("Adventure stage level and saved completion count disagree.")
    cost = _integer(dynamic.get("energy"), "energy cost") if dynamic else None
    return {"wins": wins, "level": level, "energy_cost": cost, "available": dynamic is not None}


def _ordered(campaigns: dict[str, dict]) -> list[str]:
    """Preserve catalog order while putting explicit prerequisites first."""
    output, visiting, done = [], set(), set()
    def visit(cid):
        if cid in done:
            return
        if cid in visiting:
            raise AdventureScanError("Adventure island prerequisites contain a cycle.")
        visiting.add(cid)
        requirement = campaigns[cid].get("campaign_req")
        if requirement not in (None, "", "0", 0):
            parent = str(_integer(str(requirement), "island prerequisite", 1))
            if parent in campaigns:
                visit(parent)
        visiting.remove(cid)
        done.add(cid)
        output.append(cid)
    for cid in campaigns:
        visit(cid)
    return output


def scan_adventure_islands(live: dict, campaign_catalog: dict, mission_catalog: dict,
                           *, target: str = "mastery") -> dict:
    """Enumerate every stage in active tracks, including still-locked stages.

    An absent dynamic mission never means completion. First-clear flags on
    campaigns also cannot substitute for their individual mastery counters.
    This function performs no I/O, browser input, preference writes or fights.
    """
    if target not in {"progress", "mastery"}:
        raise AdventureScanError("Choose stage completion or 100% mastery.")
    if not isinstance(live, dict) or api_rejected(live):
        raise AdventureScanError("The fresh Adventure account read was rejected.")
    now = _integer(live.get("time"), "server time", 1)
    campaigns = _index(campaign_catalog, ("id", "campaign_id"), "catalog island")
    missions = _index(mission_catalog, ("id", "mission_id"), "catalog mission")
    active = _index(live.get("current_campaigns"), ("id", "campaign_id"), "current island")
    current = _index(live.get("current_missions"), ("id", "mission_id"), "mission")
    completions = _index(live.get("mission_completions"), ("id", "mission_id"), "completion")
    def in_window(row):
        for field in ("start_time", "end_time"):
            if field in row and row[field] is not None:
                moment = _integer(row[field], "island time window")
                if field == "start_time" and moment > now or field == "end_time" and moment and now >= moment:
                    return False
        return True
    closed_islands = {cid for cid, row in active.items() if not in_window(row)}
    active = {cid: row for cid, row in active.items() if cid not in closed_islands}
    def track(meta):
        return str(meta.get("track") or "main")
    active_tracks = {track(campaigns[cid]) for cid in active if cid in campaigns}
    limited = live.get("limited_adventure_data")
    if isinstance(limited, dict) and limited.get("limited_adventure_track"):
        limited_track = str(limited["limited_adventure_track"])
        end = _integer(limited.get("limited_adventure_end_time"), "seasonal island expiry")
        raw_unlock = live.get("limited_adventure_island_unlock")
        unlock = (True if raw_unlock is True or type(raw_unlock) is int and raw_unlock == 1
                  or isinstance(raw_unlock,str) and raw_unlock.casefold() in {"true","1"} else
                  False if raw_unlock is False or type(raw_unlock) is int and raw_unlock == 0
                  or isinstance(raw_unlock,str) and raw_unlock.casefold() in {"false","0"} else None)
        if raw_unlock is not None and unlock is None:
            raise AdventureScanError("The seasonal island unlock flag is invalid.")
        if end and now >= end or unlock is False:
            active_tracks.discard(limited_track)
        elif limited_track not in {track(meta) for meta in campaigns.values()}:
            raise AdventureScanError("The live seasonal track is missing from the current island catalog.")
        elif unlock is True:
            active_tracks.add(limited_track)
    scoped = {cid: meta for cid, meta in campaigns.items()
              if cid not in closed_islands and track(meta) in active_tracks and in_window(meta)}
    if not scoped:
        raise AdventureScanError("No catalog-backed current Adventure islands were exposed.")
    goal = 12 if target == "mastery" else 1
    islands, used_missions = [], set()
    for cid in _ordered(scoped):
        meta = scoped[cid]
        ordered = meta.get("missions")
        if not isinstance(ordered, list) or not ordered:
            raise AdventureScanError("A current Adventure island has no verified stage order.")
        stages = []
        for number, identity in enumerate(ordered, 1):
            mid = str(_integer(identity, "catalog mission ID", 1))
            if mid not in missions or mid in used_missions:
                raise AdventureScanError("Adventure stage mapping is missing or duplicated.")
            used_missions.add(mid)
            for source in (missions[mid],current.get(mid) or {}):
                for alias in ("campaign_id","campaign","campaignId"):
                    if source.get(alias) not in (None,""):
                        parent=source[alias]
                        if isinstance(parent,Mapping):parent=parent.get("id",parent.get("campaign_id"))
                        if str(_integer(str(parent),"stage campaign ID",1))!=cid:
                            raise AdventureScanError("Adventure stage metadata belongs to a different island.")
            progress = _progress(mid, current, completions, cid)
            available = cid in active and progress["available"]
            stages.append({"mission": int(mid), "stage": number, **progress,
                           "available": available, "complete": progress["wins"] >= goal, "target_wins": goal})
        completed = sum(stage["complete"] for stage in stages)
        first = next((stage for stage in stages if not stage["complete"]), None)
        state = "Complete" if first is None else "Ready" if first["available"] else "Locked"
        units = sum(min(stage["wins"], goal) for stage in stages)
        islands.append({"id": cid, "name": str(meta.get("name") or f"Island {cid}"), "track": track(meta),
                        "completed_stages": completed, "total_stages": len(stages),
                        "progress_percent": round(units * 100 / (goal * len(stages)), 1),
                        "remaining_wins": sum(max(0, goal-stage["wins"]) for stage in stages),
                        "state": state, "reason": "Waiting for the game to unlock the next stage." if state == "Locked" else "",
                        "missions": stages})
    first_island = next((island for island in islands if island["state"] != "Complete"), None)
    next_mission = None
    ready_island = next((island for island in islands if island["state"] == "Ready"), None)
    if ready_island is not None:
        first_stage = next(stage for stage in ready_island["missions"] if not stage["complete"])
        if first_stage["available"]:
            next_mission = {**first_stage, "campaign_id": ready_island["id"], "island": ready_island["name"]}
    total = sum(island["total_stages"] for island in islands)
    completed = sum(island["completed_stages"] for island in islands)
    units = sum(min(stage["wins"], goal) for island in islands for stage in island["missions"])
    ignored = len(set(active) - set(campaigns))
    return {"target": target, "islands": islands, "next_mission": next_mission,
            "all_complete": first_island is None, "completed_stages": completed, "total_stages": total,
            "progress_percent": round(units * 100 / (total * goal), 1),
            "warnings": ([f"{ignored} account campaign records are outside the current public island map."] if ignored else [])}
