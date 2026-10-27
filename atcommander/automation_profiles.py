"""Named local routines and a bounded, evidence-based daily runner.

Planning is pure. Running delegates to the existing verified command handlers;
it never invents a quest mutation or treats an uncertain action as skippable.
"""
from __future__ import annotations

import copy
import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field

from .account_state import find_ad_state, find_adventure_energy, find_arena_energy
from .challenge_state import current_challenge
from .diagnostic_privacy import private_key, safe_text
from .local_commands import ArgumentSpec, CommandInputError, CommandResult, CommandSpec, CommandUnavailable
from .reward_options import claim_plan
from .shop_rewards import api_rejected, daily_upgrade_progress, safe_upgrade_candidates
from .utility_commands import integer, rows
from .daily_hero_requirements import commander_ip_requirement, plan_commander_ip
from .research_automation import number

BUILTIN_DAILY = "Best Daily"
PROFILE_KEY = "automation_profiles"
DAILY_PLAY_DEFAULTS={"hero_tokens":"","max_refreshes":0,"max_refresh_nixons":0,"buy_basic":False,"use_lootcrate":False,
    "takeover_quest_mode":"","skip":False,"bypass_arena_turn_limit":False,"melee_character_first":False,"mission":None,"adventure_level":"","event":None,
    "deck":None,"min_energy":0,"max_level":None,"swole_level":None,
    "claim_daily":False,"collect_research":False,"collect_quests":False,
    **{"claim_"+kind:False for kind in ("mega","swole","clash","ecmc","sfc")}}


def _private_options(value):
    if isinstance(value,str):
        try:value=json.loads(value)
        except ValueError:return False
    if isinstance(value,dict):
        return any((private_key(key) and key!="hero_tokens") or _private_options(child) for key,child in value.items())
    return isinstance(value,list) and any(_private_options(child) for child in value)


def normalize_steps(registry, steps):
    if not isinstance(steps,list) or not 1<=len(steps)<=100:
        raise CommandInputError("A profile needs 1–100 command steps.")
    normalized=[]
    for row in steps:
        if not isinstance(row,dict):raise CommandInputError("Each profile step needs a command and arguments.")
        spec=registry.spec(row.get("command",""))
        if spec.destructive or spec.sends_message:
            raise CommandInputError("Destructive and messaging commands run individually after review.")
        if any(private_key(arg.name) and arg.name!="hero_tokens" for arg in spec.args) or _private_options(row.get("args",{})):
            raise CommandInputError("Credentials and account registration cannot be saved in an automation profile.")
        args=spec.parse_args(row.get("args",{}))
        normalized.append({"command":spec.name,"args":args})
    return normalized


class AutomationProfiles:
    def __init__(self,store,registry):self.store=store;self.registry=registry
    @staticmethod
    def name(value):
        text=re.sub(r"\s+"," ",unicodedata.normalize("NFKC",str(value or ""))).strip()
        if not text or len(text)>80 or any(ord(char)<32 for char in text):
            raise CommandInputError("Use a profile name of 1–80 characters.")
        return text
    def _saved(self):
        value=self.store.read().get(PROFILE_KEY,{})
        return value if isinstance(value,dict) else {}
    def names(self):
        valid=[]
        for name,record in self._saved().items():
            try:
                label=self.name(name)
                if label.casefold()==BUILTIN_DAILY.casefold():continue
                normalize_steps(self.registry,record.get("steps"))
                valid.append(label)
            except (ValueError,TypeError,AttributeError):continue
        return [BUILTIN_DAILY]+sorted(set(valid),key=str.casefold)
    def save(self,name,steps):
        name=self.name(name)
        if name.casefold()==BUILTIN_DAILY.casefold():raise CommandInputError("Best Daily is built in; save your own profile under another name.")
        normalized=normalize_steps(self.registry,steps)
        saved=self._saved()
        for old in list(saved):
            if str(old).casefold()==name.casefold():saved.pop(old)
        saved[name]={"steps":normalized}
        self.store.update(PROFILE_KEY,saved)
        return copy.deepcopy(normalized)
    def load(self,name):
        name=self.name(name)
        if name.casefold()==BUILTIN_DAILY.casefold():return [{"command":"daily-run","args":{"options":"{}"}}]
        saved=self._saved();key=next((key for key in saved if str(key).casefold()==name.casefold()),None)
        if key is None:raise CommandInputError("That automation profile does not exist.")
        return normalize_steps(self.registry,saved[key].get("steps"))
    def delete(self,name):
        name=self.name(name)
        if name.casefold()==BUILTIN_DAILY.casefold():raise CommandInputError("The built-in Best Daily profile cannot be deleted.")
        saved=self._saved();key=next((key for key in saved if str(key).casefold()==name.casefold()),None)
        if key is None:raise CommandInputError("That automation profile does not exist.")
        saved.pop(key);self.store.update(PROFILE_KEY,saved)


@dataclass(frozen=True)
class DailyOptions:
    nixon_budget:int=3000
    cm_stones_budget:int=0
    arena_reserve:int=5
    adventure_reserve:int=5
    max_battles:int=20
    event_battles:int=6
    swole_reserve:int=1
    watch_ads:bool=True
    research_pairs:int=3
    research_speedup_hours:int=0
    adventure_level:str="mastery"
    include_events:bool=True

    @classmethod
    def parse(cls,options=None):
        if isinstance(options,cls):return options
        if isinstance(options,str):
            try:options=json.loads(options)
            except ValueError:raise CommandInputError("Daily options must be a JSON object.") from None
        options={} if options is None else options
        if not isinstance(options,dict) or set(options)-set(cls.__dataclass_fields__):
            raise CommandInputError("Daily options contain an unsupported setting.")
        defaults=asdict(cls());parsed={}
        for key,default in defaults.items():
            kind="bool" if isinstance(default,bool) else "int" if isinstance(default,int) else "text"
            maximum=3000 if key=="nixon_budget" else 100000 if key=="cm_stones_budget" else 1000 if key in {"max_battles","event_battles"} else 100 if key=="research_pairs" else 10000
            parsed[key]=ArgumentSpec(key,kind=kind,default=default,minimum=0,maximum=maximum).parse(options.get(key))
        return cls(**parsed)


@dataclass
class DailyPlan:
    steps:list[dict]=field(default_factory=list)
    skipped:list[dict]=field(default_factory=list)
    unresolved:list[dict]=field(default_factory=list)
    quest_coverage:bool=False


def _daily_quests(live,now,unknown=None):
    found=[]
    def unverified(quest):
        if unknown is not None:unknown.append({"quest":str(quest.get("name")or"Daily quest"),"remaining":None,
            "reason":"Current expiry, start time, status or progress metadata is unavailable; completion cannot be verified."})
    def visit(source,inherited=False,expiry=None,start=0,depth=0,invalid_window=False):
        if depth>4:
            if inherited:unverified({"name":"Nested Daily quest"})
            return
        for quest in rows(source):
            daily=inherited or str(quest.get("is_daily")).casefold() in {"1","true"} or bool(re.match(r"^daily\b",str(quest.get("name", "")),re.I))
            end=number(quest["end_time"]) if "end_time" in quest else expiry
            begin=number(quest["start_time"]) if "start_time" in quest else start
            window_invalid=invalid_window or any(key in quest and (number(quest[key]) is None or number(quest[key])<0)
                                                for key in ("start_time","end_time"))
            if expiry is not None and expiry>0 and end is not None and end>=0:
                end=min(expiry,end) if end else expiry
            if begin is not None and start>0:begin=max(start,begin)
            if quest.get("sub_quests"):
                visit(quest["sub_quests"],daily,end,begin if begin is not None else start,depth+1,window_invalid);continue
            if not daily:continue
            if window_invalid or end is None or begin is None:
                unverified(quest);continue
            if end>0 and now>=end or begin>now:continue
            target=number(quest.get("max_progress"));progress=number(quest.get("progress"));status=number(quest.get("status"))
            if status not in {0,1,2} or target is None or target<=0 or progress is None or progress<0:
                unverified(quest)
                continue
            found.append({**quest,"end_time":end,"start_time":begin,"remaining":0 if status==2 else max(0,target-progress)})
    visit(live.get("user_achievements"))
    return found


def _tasks_ready(live,kind,now):
    return any(integer(row.get("task_type"))==kind and 0<integer(row.get("end_time"))<=now for row in rows(live.get("user_tasks")))


def _active_commander(live):
    active=str((live.get("user_data")or{}).get("active_deck") or "")
    deck=next((row for row in rows(live.get("user_decks")) if str(row.get("deck_id",row.get("id")))==active),None)
    commander=(deck or {}).get("commander")or{}
    hero=next((commander.get(key) for key in ("unit_id","commander_id","hero_id") if commander.get(key) is not None),None)
    return active,hero,deck


def _combo_pair_available(live,recipes):
    """Avoid extra combo-quest fights with an unverified or PC-only deck."""
    _,_,deck=_active_commander(live)
    known=live.get("user_combos_compressed")
    if not deck or not isinstance(known,dict) or not isinstance(recipes,dict):return False
    owned={str(row.get("unit_index",row.get("id"))):str(row.get("unit_id")) for row in rows(live.get("user_units"))}
    cards={owned.get(str(row.get("unit_index",row.get("id")))) for row in rows(deck.get("units"))}
    for key in known:
        parts=str(key).split("~")
        if len(parts)==2 and all(part in cards for part in parts) and (key in recipes or "~".join(reversed(parts)) in recipes):return True
    return False


def build_daily_plan(live,options=None,now=None,catalogs=None):
    if not isinstance(live,dict) or api_rejected(live):raise CommandUnavailable("A verified current account snapshot is required before daily actions.")
    options=DailyOptions.parse(options);now=integer(now or live.get("time") or time.time());catalogs=catalogs or {}
    plan=DailyPlan();quests=_daily_quests(live,now,plan.unresolved)
    plan.quest_coverage=bool(quests) and not plan.unresolved
    def add(identity,command,args,label):
        if command=="play":args={**DAILY_PLAY_DEFAULTS,**args}
        existing=next((row for row in plan.steps if row["id"]==identity),None)
        if existing is not None:
            # One purchase can advance several quests. Do not buy a separate
            # stack for every overlapping requirement in the same snapshot.
            key="quantity" if command in {"buy-basic","shop-purchase"} else "max_upgrades" if command=="daily-upgrade" else None
            if key and integer(args.get(key))>integer(existing["args"].get(key)):
                existing.update(command=command,args=args,label=label)
            return
        plan.steps.append({"id":identity,"command":command,"args":args,"label":label})
    def skip(label,reason):plan.skipped.append({"label":label,"reason":reason})
    def unresolved(quest,reason):plan.unresolved.append({"quest":str(quest.get("name") or "Daily quest"),"remaining":quest.get("remaining"),"reason":reason})
    if live.get("active_choices"):add("choices","item-choose",{},"Resolve pending rewards")
    add("login","daily-login",{},"Claim available free daily login rewards")
    if any((row.get("reward") or row.get("rewards") or row.get("reward_data")) and integer(row.get("status"))!=2 for row in rows(live.get("user_mail"))):
        add("mail","user-mail",{"mode":"claim"},"Claim reward mail")
    if _tasks_ready(live,1,now):add("research-collect","research-collect",{},"Collect finished research")
    if _tasks_ready(live,2,now):add("training-collect","user-squad",{"mode":"collect"},"Collect finished Swole training")
    if options.research_pairs>0:
        if isinstance(live.get("user_combos_compressed"),dict) and isinstance(live.get("user_tasks"),(dict,list)) and live.get("research_times_slot"):
            research={"tiers":[1,2,3,4,5],"max_pairs":options.research_pairs,"max_speedup_hours":options.research_speedup_hours,
                "speedups":options.research_speedup_hours>0,"ads":options.watch_ads}
            add("research-queue","research-run",{"options":json.dumps(research),"resume":True,"scope":"daily"},f"Queue up to {options.research_pairs} owned research pairs in the separate bounded daily queue")
        else:skip("Research queue","Current researched-pair, task and chamber metadata is unavailable; no free slot or recipe is assumed.")
    state=live.get("slot_machine_data") or {}
    if options.watch_ads and integer(state.get("cost"),-1)==0 and integer(state.get("pulls"),-1)==0:
        add("mom-free","buy-momfree",{},"Mom's Box verified first free pull")
    else:skip("Mom's Box","No verified first-free offer, or the flow is disabled. Later rewarded-ad pulls need a verified automatic playback capability.")
    boost=find_ad_state(live).get("boost_level")
    if options.watch_ads and boost is not None and boost<3:add("boost","user-ad",{"mode":"watch"},"Verify legitimate ad boost before battles")
    arena_remaining=adventure_remaining=combo_remaining=0
    takeover_steps=[]
    for quest in quests:
        if not quest["remaining"]:continue
        req=quest.get("req") if isinstance(quest.get("req"),dict) else {}
        if "total_pvp_battles_num" in req:
            arena_remaining=max(arena_remaining,quest["remaining"]);continue
        if set(req)=={"play_combo"} and integer(quest.get("type"))==8:
            target=req["play_combo"].get("num") if isinstance(req["play_combo"],dict) else None
            if integer(target,-1)==integer(quest.get("max_progress")) and _combo_pair_available(live,catalogs.get("combo")):
                combo_remaining=max(combo_remaining,quest["remaining"])
            else:unresolved(quest,"The combo target or a researched recipe in the active deck is unverified. Choose a combo-capable saved deck; extra fights are not assumed to play combos.")
            continue
        if set(req)=={"daily_quests_completed"} and integer(quest.get("type"))==24:
            if integer(req["daily_quests_completed"],-1)!=integer(quest.get("max_progress")):
                unresolved(quest,"The aggregate daily target does not match its current progress metadata.")
            continue
        if set(req)=={"commander_ip"} and integer(quest.get("type"))==12 and integer(quest.get("max_progress"))==1:
            active,hero,deck=_active_commander(live)
            selection=plan_commander_ip(commander_ip_requirement(quest),catalogs.get("hero_status"),catalogs.get("card"),current_hero_id=hero)
            if active and hero is not None and deck and selection["selected"]:
                takeover_steps.append((quest,selection))
            else:unresolved(quest,selection["reason"] or "The active deck and original commander cannot be verified before a temporary hero change.")
            continue
        if "upgrade_cards_num" in req:
            progress=daily_upgrade_progress({"user_achievements":[quest]})
            try:safe=safe_upgrade_candidates(live,live,catalogs.get("card",{}))
            except ValueError:safe=[]
            count=min(progress[2],len(safe)) if progress else 0
            if count:add("upgrade","daily-upgrade",{"max_upgrades":min(3,count)},"Protected Daily card upgrades")
            else:unresolved(quest,"No verified upgrade requirement or safe loose cards is available.")
            continue
        purchase=req.get("store_item")
        if isinstance(purchase,dict) and str(purchase.get("id")) in {"0","1"} and integer(purchase.get("number"))==3:
            offer=next((row for row in rows(live.get("store_items")) if str(row.get("id"))=="1"),None)
            price=integer((offer or {}).get("sc_cost"));money=integer((live.get("user_data")or{}).get("money"),integer((live.get("common_fields")or{}).get("soft_currency_balance"),-1))
            count=min(3,quest["remaining"],options.nixon_budget//price if price>0 else 0,money//price if money>=0 and price>0 else 0) if integer(quest.get("max_progress"))==3 else 0
            if count and integer(offer.get("num_available"),1)>0:add("packs","buy-basic",{"quantity":str(count),"budget":options.nixon_budget},f"Daily Basic Packs, planned cost {count*price:,} Nixons within the {options.nixon_budget:,} cap")
            else:unresolved(quest,"Current Basic Pack price, availability, Nixon balance or configured budget prevents the remaining purchase.")
            continue
        if isinstance(purchase,dict) and str(purchase.get("id","")).isdigit():
            sid=str(purchase["id"]);offer=next((row for row in rows(live.get("store_items")) if str(row.get("id"))==sid),None)
            cost=(offer or {}).get("cost")or{};price=integer(cost.get("num")) if isinstance(cost,dict) else 0
            mastery=bool(offer and str(cost.get("item"))=="200008" and "mastery" in (offer.get("tabs") or {}) and integer(offer.get("hc_cost"))==0 and integer(offer.get("sc_cost"))==0)
            from .cm_reviews import CMReviewEngine
            verified=CMReviewEngine(catalogs.get("card",{})).discover_shop({"store_items":[offer]} if offer else {})
            mastery=mastery and any(str(row.get("store_id"))==sid and row.get("currency")=="Mastery Stones" for row in verified)
            owned=next((integer(row.get("number",row.get("quantity",row.get("count")))) for row in rows(live.get("user_items")) if str(row.get("id"))=="200008"),0)
            count=min(quest["remaining"],options.cm_stones_budget//price,owned//price) if mastery and price>0 else 0
            if count:
                add("cm-purchase:"+sid,"shop-purchase",{"store_id":integer(sid),"quantity":count,"budget":count*price,"expected_currency_item":200008},f"Explicit daily CM pack requirement, at most {count*price:,} Mastery Stones")
            else:unresolved(quest,"This shop requirement is not a verified affordable stone-only CM offer with an explicit opt-in budget.")
            continue
        # A misleading quest name never substitutes for the actual req schema.
        unresolved(quest,"The exposed requirement has no verified automation mapping. It remains incomplete; CM budgets never authorize guessed actions.")
    energy,_=find_arena_energy(live)
    available=min(options.max_battles,max(0,(energy or 0)-options.arena_reserve))
    for quest,selection in takeover_steps:
        if available:
            identity="takeover:"+str(selection["required_ip"])
            add(identity,"play",{"mode":"arena","count":1,"reserve":options.arena_reserve},f"Play one required battle with {selection['selected']['name']} ({selection['show']}); restore the original hero")
            next(row for row in plan.steps if row["id"]==identity)["hero_requirement"]=selection["required_ip"]
        else:skip(str(quest.get("name")or"Hero Daily"),"Arena energy/reserve or the global battle limit prevents a required hero battle.")
    count=min(arena_remaining,options.max_battles,max(0,(energy or 0)-options.arena_reserve))
    if count:add("arena","play",{"mode":"arena","count":count,"reserve":options.arena_reserve},f"Complete up to {count} required Arena battles")
    elif arena_remaining:skip("Arena Daily battles","Current energy/reserve or global battle limit prevents another required fight.")
    if combo_remaining and available:
        add("combos","play",{"mode":"arena","count":min(combo_remaining,available),"reserve":options.arena_reserve},"Finish remaining played-combo progress only if normal battles leave it incomplete")
    elif combo_remaining:skip("Play Combos Daily","Arena energy/reserve or the global battle limit prevents another combo battle.")
    # Adventure is added only when a verified quest mapping or explicit saved
    # profile requests it. Unmapped adventure counters stay in unresolved.
    if adventure_remaining:
        add("adventure","play",{"mode":"adventure","count":min(adventure_remaining,options.max_battles),"reserve":options.adventure_reserve,"adventure_level":options.adventure_level},"Required Adventure battles")
    if options.include_events:
        for event in rows(live.get("active_events")):
            current=current_challenge(live,str(event.get("id") or ""),now=now)
            if not current:continue
            label=str(event.get("name") or "Challenge");mode="swole" if "swole" in label.casefold() else "pvp-challenge" if str((event.get("challenge_data")or{}).get("challenge_type"))=="2" else "clash" if "clash" in label.casefold() else "melee" if "melee" in label.casefold() else "challenge"
            reserve=options.swole_reserve if mode=="swole" else 0
            count=min(options.event_battles,options.max_battles,max(0,(current["current"]-reserve)//current["cost"]))
            if count:add("event:"+str(event["id"]),"play",{"mode":mode,"event":integer(event["id"]),"count":count,"reserve":reserve},f"{label}, up to {count} current tickets")
            else:skip(label,"No tickets remain above the configured reserve.")
        siege=live.get("guild_siege_status") or {}
        alive=any(integer(row.get("hp"))>0 for row in rows(siege.get("enemy_locations")))
        if integer(siege.get("phase"),-1)==2 and integer(siege.get("num_attacks"))>0 and alive:
            add("siege","play",{"mode":"siege","count":min(options.event_battles,options.max_battles,integer(siege["num_attacks"]))},"Siege in its verified attack phase")
        else:skip("Siege","No verified attack phase, living target and available attacks.")
    for kind in ("quests","mega","swole","clash","ecmc","sfc"):
        # Keep final claim positions in the queue even if battles have not yet
        # completed the quests. Execution rechecks the current claim plan.
        add("claim:"+kind,"reward-claim",{"kind":kind,"execute":True},"Claim available "+kind+" rewards")
    return plan


def _daily_source(service,options=None):
    options=DailyOptions.parse(options)
    live=service.snapshot(refresh=True)
    if api_rejected(live):raise CommandUnavailable("The current account read was rejected; no daily action was started.")
    catalogs={}
    if any(daily_upgrade_progress({"user_achievements":[quest]}) for quest in _daily_quests(live,integer(service.clock()))):
        catalogs["card"]=service.catalog("card")
    pending=[quest for quest in _daily_quests(live,integer(service.clock())) if quest["remaining"]]
    if any("commander_ip" in (quest.get("req")or{}) for quest in pending):
        catalogs["card"]=service.catalog("card")
        catalogs["hero_status"]=service.call("getHeroSquad")
    if any("play_combo" in (quest.get("req")or{}) for quest in pending):
        catalogs["combo"]=service.catalog("combo")
    if "store_items" not in live and any(isinstance((quest.get("req")or{}).get("store_item"),dict) for quest in _daily_quests(live,integer(service.clock()))):
        store=service.call("getStoreData");live={**live,"store_items":store.get("store_items",[])}
    if any(isinstance((quest.get("req")or{}).get("store_item"),dict) and str(quest["req"]["store_item"].get("id")) not in {"0","1"} for quest in _daily_quests(live,integer(service.clock()))):
        catalogs["card"]=service.catalog("card")
    return live,catalogs


def _apply_daily_availability(service,plan,live,availability=None):
    kept=[]
    for step in plan.steps:
        capability={"mom-free":"mom_free","boost":"boost","login":"daily_login"}.get(step["id"])
        value=getattr(service,"daily_capabilities",{}).get(capability,False) if capability else True
        value=value(live) if callable(value) else value
        reason=None
        if capability and not value:
            reason="Current daily-login eligibility is unverified; already-claimed login rewards are skipped." if capability=="daily_login" else "Automatic legitimate playback/free-pull capability has not been verified for this flow."
            if capability=="daily_login":
                from .login_bonus import login_bonus_status
                reason=login_bonus_status(live)["reason"]
        if step["id"]=="research-queue":
            from .research_commands import research_daily_preflight
            check=research_daily_preflight(service,json.loads(step["args"]["options"]),live=live)
            if not check.get("available"):reason=check.get("reason") or "No verified available daily research step."
        if not reason and availability is not None:reason=availability(step,live)
        if reason:plan.skipped.append({"label":step["label"],"reason":str(reason)})
        else:kept.append(step)
    plan.steps=kept
    return plan


def run_daily_profile(service,options=None,emit=None,availability=None):
    options=DailyOptions.parse(options);emit=emit or (lambda message:None)
    live,catalogs=_daily_source(service,options);initial=_apply_daily_availability(service,build_daily_plan(live,options,service.clock(),catalogs),live,availability)
    completed=[];skipped=list(initial.skipped);battle_count=0
    observed_daily={};verified_complete=set()
    def observe_daily(snapshot):
        for quest in _daily_quests(snapshot,integer(service.clock())):
            qid=integer(quest.get("id"),-1)
            if qid<=0:continue
            identity=(str(qid),integer(quest.get("end_time")))
            observed_daily[identity]=str(quest.get("name")or"Daily quest")
            if not quest["remaining"]:verified_complete.add(identity)
    observe_daily(live)
    remaining_shop_budget={"buy-basic":options.nixon_budget,"shop-purchase":options.cm_stones_budget}
    for queued in initial.steps:
        if getattr(service,"stop",None) is not None and service.stop.is_set():break
        live,catalogs=_daily_source(service,options)
        observe_daily(live)
        current=_apply_daily_availability(service,build_daily_plan(live,options,service.clock(),catalogs),live,availability)
        step=next((row for row in current.steps if row["id"]==queued["id"]),None)
        if step is None:
            skipped.append({"label":queued["label"],"reason":"The current availability or quest state no longer requires this step."});continue
        if step["id"].startswith("claim:") and not claim_plan(live,{step["args"]["kind"]},integer(service.clock())):continue
        if step["command"]=="play":
            count=min(step["args"]["count"],options.max_battles-battle_count)
            if count<=0:
                skipped.append({"label":step["label"],"reason":"The global battle limit was reached."});continue
            step=copy.deepcopy(step);step["args"]["count"]=count
        if step["command"] in remaining_shop_budget:
            step=copy.deepcopy(step)
            cap=min(step["args"]["budget"],remaining_shop_budget[step["command"]])
            if cap<=0:
                skipped.append({"label":step["label"],"reason":"The shared daily purchase budget was already reserved by an earlier step."});continue
            step["args"]["budget"]=cap
            # Reserve the entire allowed cap before transmission. This is
            # conservative if a step buys less, and cannot reset the budget
            # across several offers or depend on a stale balance delta.
            remaining_shop_budget[step["command"]]-=cap
        if live.get("active_choices") and step["command"]!="item-choose":
            service.execute("item-choose")
            live=service.snapshot(refresh=True)
            if live.get("active_choices"):raise CommandUnavailable("A reward choice is still pending. The daily run stopped before another action.")
        emit(step["label"])
        # No generic exception-to-skip conversion: this action may already have
        # consumed a resource. Let the existing serial worker stop and report.
        if step.get("hero_requirement") is not None:
            from .temporary_daily_hero import temporary_daily_commander
            active,hero,_=_active_commander(live)
            selection=plan_commander_ip(step["hero_requirement"],catalogs.get("hero_status"),catalogs.get("card"),current_hero_id=hero)
            if not selection["selected"]:raise CommandUnavailable(selection["reason"])
            def fresh_decks():
                service.context.invalidate()
                return service.snapshot(refresh=True)
            with temporary_daily_commander(service.call,fresh_decks,selection["selected"]["hero_id"],expected_deck_id=active,expected_commander_id=hero):
                result=service.execute(step["command"],step["args"])
            fresh=service.snapshot(refresh=True)
            if result.rows and any(quest["remaining"] for quest in _daily_quests(fresh,integer(service.clock())) if commander_ip_requirement(quest)==step["hero_requirement"]):
                raise CommandUnavailable("The hero battle finished and the original selection was preserved, but its Daily quest did not advance. No extra fight was started.")
        else:result=service.execute(step["command"],step["args"])
        if step["command"]=="item-choose":
            if service.snapshot(refresh=True).get("active_choices"):
                raise CommandUnavailable("The reward choice was not confirmed. The daily profile stopped without replaying the selection.")
        if step["command"]=="play":
            battle_count+=len(result.rows)
            if any(row.get("won") is None for row in result.rows):raise CommandUnavailable("A battle outcome was not verified. The daily profile stopped without another start.")
            unavailable=result.data.get("unavailable_event")
            if unavailable:
                skipped.append({"label":step["label"],"reason":str(unavailable.get("reason") or result.message)})
                if not result.rows:continue
        completed.append({"step":step["label"],"message":result.message})
    live,catalogs=_daily_source(service,options);final=build_daily_plan(live,options,service.clock(),catalogs)
    observe_daily(live)
    if live.get("active_choices") and not (getattr(service,"stop",None) is not None and service.stop.is_set()):
        service.execute("item-choose")
        live,catalogs=_daily_source(service,options)
        if live.get("active_choices"):raise CommandUnavailable("A final reward choice remains unconfirmed. The daily profile stopped without another action.")
        final=build_daily_plan(live,options,service.clock(),catalogs)
    remaining=[{"quest":str(quest.get("name")or"Daily quest"),"remaining":quest["remaining"]} for quest in _daily_quests(live,integer(service.clock())) if quest["remaining"]]
    coverage=final.quest_coverage or initial.quest_coverage and bool(observed_daily) and not final.unresolved and all(identity in verified_complete and (identity[1]==0 or identity[1]>integer(service.clock())) for identity in observed_daily)
    message=f"Best Daily ran {len(completed)} available steps and {battle_count} battles. "
    message+=(f"{len(remaining)} Daily quest(s) remain incomplete; review the limits and unresolved requirements." if remaining else "All exposed current Daily quests are complete." if coverage else "Daily quest completion is unavailable; no all-complete claim is made.")
    detail={"completed":completed,"skipped":skipped,"unresolved":final.unresolved,"remaining":remaining,"options":asdict(options),"quest_coverage":bool(coverage)}
    return CommandResult(rows=completed,message=message,details=json.dumps(detail,ensure_ascii=False,indent=2),data=detail)


def register_automation_profiles(service):
    def preview(current,args):
        options=DailyOptions.parse(args["options"]);live,catalogs=_daily_source(current,options)
        plan=_apply_daily_availability(current,build_daily_plan(live,options,current.clock(),catalogs),live,getattr(current,"daily_availability",None))
        detail={"steps":plan.steps,"skipped":plan.skipped,"unresolved":plan.unresolved,"options":asdict(options),"quest_coverage":plan.quest_coverage}
        return CommandResult(rows=plan.steps,message=f"Best Daily preview: {len(plan.steps)} planned steps; {len(plan.unresolved)} unresolved requirements. No action was sent.",
            details=json.dumps(detail,ensure_ascii=False,indent=2),data=detail)
    service.register(CommandSpec("daily-plan","Automation","Preview Best Daily against current quest requirements and resource limits.",
        (ArgumentSpec("options",default="{}"),)),preview)
    service.register(CommandSpec("daily-run","Automation","Run Best Daily with current quest requirements, explicit resource limits and availability checks.",
        (ArgumentSpec("options",default="{}"),),action=True,spends_resources=True,execution_mode="runner"),
        lambda current,args:run_daily_profile(current,args["options"],emit=lambda message:current.emit({"kind":"log","message":safe_text(message,current.context.private)}),availability=getattr(current,"daily_availability",None)))
