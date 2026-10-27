"""Local reward plans using the public client's claim messages and live IDs."""
from __future__ import annotations

import re

from .local_commands import CommandUnavailable
from .shop_rewards import api_rejected
from .utility_commands import integer, rows

CLAIM_KINDS = ("daily", "quests", "mega", "swole", "clash", "ecmc", "sfc")


def reward_state(service):
    live = service.snapshot(refresh=True)
    compact = service.call("getUserAccount")
    if not isinstance(live, dict) or not live or api_rejected(live) or not isinstance(compact, dict) or not compact or api_rejected(compact):
        raise CommandUnavailable("Current reward state could not be read. No reward was claimed.")
    merged = {**live, **{k:v for k,v in compact.items() if k not in {"request", "result", "result_message"}}}
    for key in ("user_data", "common_fields"):
        merged[key] = {**(live.get(key) or {}), **(compact.get(key) or {})}
    return merged


def _quest_integer(value):
    if isinstance(value, bool):return None
    if isinstance(value, int):return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        try:return int(value)
        except ValueError:pass
    return None


def _claimable_quests(live, now):
    """Nested achievements keep their own IDs and their parent's time window."""
    found=[];seen=set()
    def walk(value, start=0, expiry=0, depth=0):
        if depth>12:return
        if isinstance(value,list):
            for child in value:walk(child,start,expiry,depth+1)
            return
        if not isinstance(value,dict):return
        if "start_time" in value:
            current=_quest_integer(value["start_time"])
            if current is None or current<0:return
            start=max(start,current)
        if "end_time" in value:
            current=_quest_integer(value["end_time"])
            if current is None or current<0:return
            if current:expiry=min(expiry,current) if expiry else current
        if start and now<start or expiry and now>=expiry:return
        qid=_quest_integer(value.get("id"));target=_quest_integer(value.get("max_progress"))
        progress=_quest_integer(value.get("progress"));status=_quest_integer(value.get("status"))
        if qid is not None and qid>0 and target is not None and target>0 and progress is not None and progress>=target and status in {0,1} and qid not in seen:
            seen.add(qid);found.append({"kind":"quests","name":str(value.get("name") or "Completed quest"),
                "method":"completeAchievement","payload":{"achievement_id":qid}})
        for key, child in value.items():
            if isinstance(child,(dict,list)):walk(child,start,expiry,depth+1)
    # Top-level account keys are actual achievement IDs. Child IDs are never
    # invented from a parent's ID, list position, or arbitrary container key.
    for quest in rows(live.get("user_achievements")):walk(quest)
    return found


def bounded_dependency_claims(service, max_claims=100):
    """Claim fresh eligible quests, including aggregates unlocked by a claim."""
    if isinstance(max_claims,bool) or not isinstance(max_claims,int) or not 1<=max_claims<=100:
        raise CommandUnavailable("Quest claiming requires a limit from 1 to 100.")
    completed=[];attempted=set()
    def stopped():
        stop=getattr(service,"stop",None)
        return stop is not None and stop.is_set()
    def fresh():
        service.context.invalidate()
        live=reward_state(service)
        if not isinstance(live.get("user_achievements"),(dict,list)):
            raise CommandUnavailable("Current quest metadata is unavailable. No quest claim was repeated.")
        return live
    if stopped():return completed
    live=fresh()
    while len(completed)<max_claims and not stopped():
        choices=live.get("active_choices")
        if choices:
            if not isinstance(choices,(dict,list)):
                raise CommandUnavailable("The pending reward choices could not be read. No quest was claimed.")
            # Use the established Rogu -> Characters -> Items choice command.
            service.execute("item-choose")
            if stopped():return completed
            live=fresh()
            if live.get("active_choices"):
                raise CommandUnavailable("The pending reward choice is still unconfirmed. No quest was claimed.")
        plan=_claimable_quests(live,int(service.clock()))
        if not plan:return completed
        step=plan[0];qid=step["payload"]["achievement_id"]
        if qid in attempted:
            raise CommandUnavailable("A previously claimed quest became eligible again. Its request was not replayed.")
        attempted.add(qid)
        response=service.call(step["method"],step["payload"])
        if not isinstance(response,dict) or not response or api_rejected(response):
            raise CommandUnavailable("AT did not confirm the quest claim. Its request was not retried.")
        if stopped():return completed
        live=fresh()
        if any(row["payload"]["achievement_id"]==qid for row in _claimable_quests(live,int(service.clock()))):
            raise CommandUnavailable("The quest remained eligible after its claim. Its request was not retried.")
        completed.append({"kind":"quests","name":step["name"],"confirmed":True})
    return completed


def claim_plan(live, kinds, now):
    """Preview one request per selected reward source, never purchased tiers."""
    out = []; seen = set()
    def add(kind, name, method, payload):
        key = (method, tuple(sorted(payload.items())))
        if key not in seen:
            seen.add(key); out.append({"kind":kind, "name":name, "method":method, "payload":payload})
    if "daily" in kinds:
        add("daily", "Daily Login", "claimDailyBonus", {})
    if "quests" in kinds:
        for quest in _claimable_quests(live,now):
            add("quests",quest["name"],quest["method"],quest["payload"])
    if "mega" in kinds:
        for quest in rows(live.get("user_mega_quest")):
            qid = integer(quest.get("id"), -1)
            expiry = integer(quest.get("end_time"))
            if qid > 0 and (not expiry or now < expiry):
                add("mega", str(quest.get("name") or "Mega Quest"), "claimMegaQuestMilestoneRewards", {"mega_quest_id":qid})
    for event in rows(live.get("active_events")):
        # Shop packs may mention Clash or Swole; they are not reward events.
        name = str(event.get("name") or ""); label = name.casefold()
        etype = integer(event.get("type"), -1)
        if etype == 12 and "sfc" in label: kind = "sfc"
        elif etype == 6 and "swole" in label: kind = "swole"
        elif etype == 6 and any(word in label for word in ("clash", "melee")): kind = "clash"
        elif etype == 6 and ("elite card master" in label or "ecmc" in label): kind = "ecmc"
        else: continue
        if kind not in kinds: continue
        start = integer(event.get("start_time")); eid = integer(event.get("id"), -1)
        if eid <= 0 or start and now < start: continue
        # Ended rows can remain claimable in active_events during their claim
        # window. Upcoming events are excluded; the server decides eligibility.
        payload = {"event_id":eid}
        cycle = event.get("cycle")
        if integer(event.get("is_cycling")) and cycle is None: continue
        if cycle is not None:
            if integer(cycle, -1) < 0: continue
            payload["cycle"] = integer(cycle)
        add(kind, name, "claimMilestoneRewards", payload)
    return out


def execute_claim_plan(service, plan):
    completed = []
    for step in plan:
        if getattr(service, "stop", None) is not None and service.stop.is_set(): break
        try:
            answer = service.call(step["method"], step["payload"])
        except CommandUnavailable as exc:
            # This exact game rejection means an exposed event has no reward.
            # Other errors may have uncertain outcomes and must stop the run.
            if step["method"] in {"claimMilestoneRewards", "claimMegaQuestMilestoneRewards"} and re.search(r"\bwarning_23_text\b", str(exc)):
                completed.append({"kind":step["kind"], "name":step["name"], "confirmed":False,
                    "unavailable":True, "reason":"The game confirmed that no reward is currently claimable for this event."})
                continue
            raise
        # Some successful claim responses are empty of rewards because nothing
        # new was earned. Report confirmed requests, not invented reward counts.
        if not isinstance(answer,dict) or not answer or api_rejected(answer):
            raise CommandUnavailable("The claim outcome was not confirmed. No request was retried.")
        from .shop_rewards import extract_item_choices
        if extract_item_choices(answer, service.catalog("card")):
            from .utility_commands import resolve_choice_rewards
            resolve_choice_rewards(service, answer)
        completed.append({"kind":step["kind"], "name":step["name"], "confirmed":True})
        service.context.invalidate()
    return completed


def vip_active(service, live):
    value = (live.get("common_fields") or {}).get("is_vip_active")
    if value is None:
        current = service.call("getUserAccount")
        value = (current.get("common_fields") or {}).get("is_vip_active")
    if str(value).casefold() in {"1", "true"}: return True
    if str(value).casefold() in {"0", "false"}: return False
    raise CommandUnavailable("AT did not expose an explicit current VIP status. No crate was opened.")
