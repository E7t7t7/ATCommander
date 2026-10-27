"""Verified one-battle Takeover requirements and bounded hero cycling.

The live daily schema is type 12, max_progress 1, req.commander_ip and a
description requiring a battle with that show's hero equipped. A win is not
required. Other shapes remain unsupported. Hero shows come from exact static
card IDs, and temporary selections use verified restoration around normal play.
"""
from __future__ import annotations

import re

from .diagnostic_privacy import private_key
from .daily_hero_requirements import IP_NAMES, commander_ip_requirement, plan_commander_ip
from .local_commands import CommandInputError, CommandResult, CommandUnavailable
from .shop_rewards import api_rejected
from .temporary_daily_hero import exact_active_commander, temporary_daily_commander
from .utility_commands import integer, rows


def _schema_integer(value, fallback=-1):
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value.strip())
    return fallback


def _requirement_paths(value, prefix="", depth=0):
    if not isinstance(value, dict) or depth > 4:
        return []
    found = []
    for key, child in value.items():
        if private_key(key):
            continue
        path = prefix + str(key)
        found.append(path)
        found.extend(_requirement_paths(child, path + ".", depth + 1))
    return found


def takeover_status(live, now):
    """Report ready only for the verified native one-battle commander_ip shape."""
    now = _schema_integer(now, 0)
    if not isinstance(live, dict) or api_rejected(live) or "user_achievements" not in live or now <= 0:
        return {"status": "unavailable", "quests": [],
                "message": "Current quest data is unavailable; Takeover hero cycling cannot start."}
    matches = []

    def visit(source, inherited=False, inherited_expiry=-1, inherited_start=0, depth=0, invalid_window=False):
        if depth > 4:
            return
        for quest in rows(source):
            label = " ".join(str(quest.get(key) or "") for key in ("name", "desc"))
            req = quest.get("req") if isinstance(quest.get("req"), dict) else {}
            matched = inherited or "commander_ip" in req or bool(re.search(r"\btakeover\b", label, re.IGNORECASE))
            expiry = _schema_integer(quest.get("end_time"), inherited_expiry)
            start = _schema_integer(quest.get("start_time"), inherited_start if quest.get("start_time") is None else -1)
            window_invalid = invalid_window or any(
                key in quest and _schema_integer(quest[key], -1) < 0
                for key in ("start_time", "end_time")
            )
            # Children cannot outlive or begin before their authoritative
            # parent's window, including when they declare an unbounded end.
            if inherited_expiry>0 and expiry>=0:expiry=min(inherited_expiry,expiry) if expiry else inherited_expiry
            if inherited_start>0 and start>=0:start=max(inherited_start,start)
            if window_invalid:expiry=start=-1
            children = quest.get("sub_quests")
            if children:
                visit(children, matched, expiry, start, depth + 1, window_invalid)
            if not matched or children:
                continue
            status = _schema_integer(quest.get("status"))
            progress = _schema_integer(quest.get("progress"))
            target = _schema_integer(quest.get("max_progress"))
            ip = commander_ip_requirement(quest)
            quest_id = str(quest.get("id")) if _schema_integer(quest.get("id"), 0) > 0 else None
            description = str(quest.get("desc") or "")
            native_description = bool(re.search(r"\bplay\s+(?:a|one|1)\s+battle\b.*\bhero\b.*\bequipped\b", description, re.I))
            native_description = native_description and not bool(re.search(r"\bwin\b", description, re.I))
            schema = (ip is not None and set(req) == {"commander_ip"}
                      and _schema_integer(quest.get("type")) == 12 and target == 1
                      and native_description and quest_id is not None
                      and status in {0, 1, 2} and 0 <= progress <= target)
            matches.append({"quest": quest.get("id"), "name": str(quest.get("name") or "Takeover"),
                            "progress": progress if progress >= 0 else None,
                            "target": target if target > 0 else None,
                            "active": (start <= now and (expiry == 0 or now < expiry)) if expiry >= 0 and start >= 0 else None,
                            "complete": status in {1, 2} or target > 0 and progress >= target,
                            "requirement_fields": _requirement_paths(req),
                            "commander_ip": ip, "show": IP_NAMES.get(ip),
                            "schema_verified": schema,
                            "end_time": expiry if expiry >= 0 else None})

    visit(live.get("user_achievements"))
    if any(q["active"] is None for q in matches):
        return {"status": "unavailable", "quests": matches,
                "message": "A Takeover quest is exposed without a verified current end time. No hero was switched and no fight was started."}
    current = [q for q in matches if q["active"]]
    if not current:
        return {"status": "no_match", "quests": matches,
                "message": "No current Takeover quest is exposed. No hero was switched and no fight was started."}
    if all(q["complete"] for q in current):
        return {"status": "complete", "quests": matches,
                "message": "The exposed Takeover quests are complete. No hero cycling is needed."}
    pending = [quest for quest in current if not quest["complete"]]
    identities = [str(quest["quest"]) for quest in pending]
    if not all(quest["schema_verified"] for quest in pending) or len(set(identities)) != len(identities):
        return {"status": "unsupported", "quests": matches,
                "message": "A current Takeover quest has an unsupported or ambiguous hero requirement. No hero was switched and no fight was started."}
    groups = []
    for quest in pending:
        group = next((row for row in groups if row["commander_ip"] == quest["commander_ip"]), None)
        if group is None:
            group = {"commander_ip": quest["commander_ip"], "show": quest["show"], "quest_ids": [], "battles": 1}
            groups.append(group)
        group["quest_ids"].append(str(quest["quest"]))
    return {"status": "ready", "quests": matches, "requirements": groups,
            "message": f"{len(groups)} current show requirement(s) need one battle each with a verified unlocked hero equipped; a win is not required."}


def _fresh(service):
    service.context.invalidate()
    return service.snapshot(refresh=True)


def _squad(service, live):
    return live if isinstance(live.get("user_hero_squad"), (dict, list)) else service.call("getHeroSquad")


def _battle_gate(service, live, args):
    reserve = integer(args.get("reserve"))
    if args["mode"] == "arena":
        from .account_state import find_arena_energy
        energy, _ = find_arena_energy(live)
        if energy is None:
            raise CommandUnavailable("Live Arena energy is unavailable; no Takeover battle was started.")
        if energy <= reserve or energy < integer(args.get("min_energy")):
            return "Current Arena energy and the configured reserve prevent a Takeover battle."
    else:
        from .pvp_challenge import resolve_pvp_challenge
        current = resolve_pvp_challenge(live, args.get("event"), now=service.clock())
        if current is None:
            raise CommandUnavailable("No unambiguous current PvP Challenge is available for Takeover. Select its current event ID.")
        if current["current"] < current["cost"] + reserve:
            return "Current PvP Challenge energy and the configured reserve prevent a Takeover battle."
        if args.get("max_level") is not None and current["level"] >= integer(args["max_level"]):
            return "The configured maximum PvP Challenge level prevents a Takeover battle."
    return ""


def run_takeover_battles(service, args):
    """Use normal one-battle play per unique current requirement; never retry one.

    Purchases, claims, research collection and token hunting are incompatible with
    this hero-cycling block because repeating normal play would repeat those actions
    or reset their budgets. The caller can run those explicit commands separately.
    """
    mode = args.get("mode")
    if mode not in {"arena", "pvp-challenge"} or args.get("takeover_quest_mode") != mode:
        raise CommandInputError("Takeover quest mode must match Arena or PvP Challenge mode.")
    live = _fresh(service)
    status = takeover_status(live, int(service.clock()))
    if status["status"] == "complete":
        return CommandResult(message=status["message"], data={"takeover_status": "complete"})
    if status["status"] != "ready":
        raise CommandUnavailable(status["message"])
    if getattr(service, "stop", None) is not None and service.stop.is_set():
        return CommandResult(message="Takeover stopped before any selection or battle.", data={"takeover_status": "ready", "battles": 0})
    extras = ("claim_daily", "collect_research", "use_lootcrate", "buy_basic", "collect_quests",
              "claim_mega", "claim_swole", "claim_clash", "claim_ecmc", "claim_sfc")
    if args.get("hero_tokens") or any(args.get(key) for key in extras):
        raise CommandInputError("Takeover hero cycling cannot combine purchases, claims, research collection or token hunting. Run those commands separately so their costs cannot repeat.")
    limit = integer(args.get("count"), 0)
    if limit < 1 or limit > 1000:
        raise CommandInputError("Takeover count must be a bounded positive total battle limit.")
    if args.get("mission") or args.get("adventure_level") or args.get("swole_level"):
        raise CommandInputError("Takeover mode cannot combine Adventure or Swole route options.")
    stop_reason = _battle_gate(service, live, args)
    if stop_reason:
        return CommandResult(message=stop_reason, data={"takeover_status": "ready", "battles": 0})
    cards = service.catalog("card")
    hero_catalog = service.context._catalogs.get("hero", {})
    requested_deck = args.get("deck")
    planned_live = live
    if requested_deck is not None:
        planned_live = {**live, "user_data": {**(live.get("user_data") or {}), "active_deck": requested_deck}}
    selected_state = exact_active_commander(planned_live)
    squad = _squad(service, live)
    for requirement in status["requirements"][:limit]:
        plan = plan_commander_ip(requirement["commander_ip"], squad, cards, hero_catalog, selected_state.commander_id)
        if plan["selected"] is None:
            raise CommandUnavailable(plan["reason"])
    if requested_deck is not None and selected_state.deck_id != exact_active_commander(live).deck_id:
        try:
            service.execute("deck-select", {"deck": requested_deck})
        except Exception:
            pass
        live = _fresh(service)
        selected_state = exact_active_commander(live)
        if selected_state.deck_id != str(requested_deck):
            raise CommandUnavailable("The requested Takeover deck selection was not confirmed. No selection was retried and no battle started.")
    completed = []
    hero_reports = []
    attempted = set()
    for _ in range(limit):
        if getattr(service, "stop", None) is not None and service.stop.is_set():
            stop_reason = "Takeover stopped before another battle."
            break
        live = _fresh(service)
        current_status = takeover_status(live, int(service.clock()))
        if current_status["status"] == "complete":
            status = current_status
            break
        if current_status["status"] != "ready":
            raise CommandUnavailable("Current Takeover requirements changed or became unavailable. No further battle was started.")
        requirement = next((row for row in current_status["requirements"] if row["commander_ip"] not in attempted), None)
        if requirement is None:
            stop_reason = "Each current show requirement has already had its one battle attempt; no fight was repeated."
            status = current_status
            break
        stop_reason = _battle_gate(service, live, args)
        if stop_reason:
            status = current_status
            break
        current_deck = exact_active_commander(live)
        if (current_deck.deck_id != selected_state.deck_id or current_deck.commander_id != selected_state.commander_id
                or selected_state.unit_indices is not None and current_deck.unit_indices != selected_state.unit_indices):
            raise CommandUnavailable("The active deck or commander changed during Takeover planning. No hero was overwritten or further battle started.")
        plan = plan_commander_ip(requirement["commander_ip"], _squad(service, live), cards, hero_catalog, current_deck.commander_id)
        if plan["selected"] is None:
            raise CommandUnavailable(plan["reason"])
        nested = {**args, "count": 1, "takeover_quest_mode": "", "deck": None}
        nested.update({key: False for key in extras})
        before = {str(row["quest"]): row for row in current_status["quests"] if str(row["quest"]) in requirement["quest_ids"]}
        attempted.add(requirement["commander_ip"])
        with temporary_daily_commander(service.call, lambda: _fresh(service), plan["selected"]["hero_id"], expected_deck_id=current_deck.deck_id, expected_commander_id=current_deck.commander_id) as report:
            answer = service.execute("play", nested)
        hero_reports.append(report)
        if len(answer.rows) > 1:
            raise CommandUnavailable("The one-battle Takeover request returned an unexpected battle count. No further battle was started.")
        completed.extend({**row, "battle": len(completed) + 1, "takeover_show": requirement["show"]} for row in answer.rows)
        after_status = takeover_status(_fresh(service), int(service.clock()))
        after = {str(row["quest"]): row for row in after_status["quests"]}
        advanced = len(before) == len(requirement["quest_ids"]) and all(
            key in after and after[key]["active"] and after[key]["schema_verified"]
            and after[key]["commander_ip"] == before[key]["commander_ip"]
            and after[key]["end_time"] == before[key]["end_time"]
            and (after[key]["complete"] or after[key]["progress"] > before[key]["progress"])
            for key in before
        )
        status = after_status
        if not answer.rows:
            stop_reason = answer.message or "The existing play command started no Takeover battle."
            break
        if not advanced:
            stop_reason = "AT did not confirm Takeover progress after the battle. That fight was not repeated."
            break
    remaining = [row for row in status["quests"] if row["active"] and not row["complete"]]
    if not stop_reason and remaining:
        stop_reason = "The total battle limit was reached with current Takeover requirements remaining."
    return CommandResult(rows=completed,
        message=f"Completed {len(completed)} Takeover battle(s)." + (" " + stop_reason if stop_reason else " Current Takeover requirements are complete."),
        data={"takeover_status": status["status"], "quests": status["quests"], "hero_restorations": hero_reports, "battles": len(completed)})
