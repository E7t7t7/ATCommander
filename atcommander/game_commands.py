"""Game-state tools and serial automation using the existing battle engine."""
from __future__ import annotations

import json
import math
import re
import threading

from .local_commands import ArgumentSpec as Arg, CommandSpec as Spec, CommandResult as Result
from .local_commands import CommandInputError, CommandUnavailable
from .utility_commands import rows, result, section, integer
from .account_state import find_arena_energy, find_adventure_energy, discover_challenge_events
from .challenge_state import current_challenge,current_swole


def missing_research(service, deck):
    data=service.snapshot(refresh=True)
    if not data.get("user_decks"):data=service.client.get_exact_decks(data)
    chosen=next((r for r in rows(data.get("user_decks")) if str(r.get("deck_id",r.get("id")))==str(deck)),None)
    if chosen is None:raise CommandInputError("That deck slot does not exist.")
    owned={str(r.get("unit_index",r.get("id"))):str(r.get("unit_id")) for r in rows(data.get("user_units") or service.snapshot().get("user_units"))}
    cards={owned.get(str(r.get("unit_index",r.get("id")))) for r in rows(chosen.get("units"))}
    known=service.snapshot().get("user_combos_compressed")
    if not isinstance(known,dict):raise CommandUnavailable("AT did not provide current researched combo pairs.")
    catalog=service.catalog("card"); recipes=service.catalog("combo"); missing=[]
    active={frozenset((str(r.get("task_id_1")),str(r.get("task_id_2")))) for r in rows(service.snapshot().get("user_tasks")) if integer(r.get("task_type"))==1}
    for key,value in recipes.items():
        parts=str(key).split("~")
        if len(parts)!=2 or not all(c in cards for c in parts):continue
        reverse="~".join(reversed(parts))
        if key in known or reverse in known:continue
        cid=str(value).split("|",1)[0]
        if not cid.isdigit():continue
        missing.append({"card1":integer(parts[0]),"card2":integer(parts[1]),"combo_id":cid,"name":catalog.get(cid,{}).get("name","Unknown"),"in_progress":frozenset(parts) in active})
    return missing


def register_game_commands(s):
    def add(name,desc,handler,args=(),action=False,**flags):
        s.register(Spec(name,name.split("-")[0].title(),desc,tuple(args),action=action,
                        evidence="Current public game client / established Commander engine",**flags),handler)
    deck=Arg("deck",kind="int",required=True,minimum=1)
    add("research-missing","List current unresearched recipe pairs in a saved deck.",lambda service,args:Result(rows=missing_research(service,args["deck"])),(deck,))
    def research_deck(service,args):
        pending=missing_research(service,args["deck"]); started=completed=hours=0
        # The public task model distinguishes Research=1, Training=2, Mastery=3.
        for pair in pending[:args["max_pairs"]]:
            if getattr(service,"stop",threading.Event()).is_set():break
            if pair["in_progress"]:continue
            live=service.snapshot(refresh=True)
            occupied={integer(r.get("slot_index")) for r in rows(live.get("user_tasks")) if integer(r.get("task_type"))==1}
            slots=[integer(r.get("slot_index"),-1) for r in rows(live.get("research_times_slot"))]
            free=next((slot for slot in slots if slot>=0 and slot not in occupied),None)
            if free is None:break
            response=service.call("researchCombo",{"card1":pair["card1"],"card2":pair["card2"],"slot_index":free});started+=1
            tasks=section(response,"user_tasks")
            task=next((r for r in tasks if integer(r.get("task_type"))==1 and integer(r.get("slot_index"))==free),None)
            if task is None:raise CommandUnavailable("Research started, but its task was not confirmed. No extra start was sent.")
            duration=max(0,integer(task.get("end_time"))-int(service.clock()))
            needed=math.ceil(duration/3600)
            if needed and args["speedups"] and hours+needed<=args["max_hours"]:
                from .session_stats import build_inventory_snapshot
                inventory=build_inventory_snapshot(service.snapshot(refresh=True),item_catalog=service.catalog("item"))["entries"]
                available=next((integer(r.get("count")) for r in inventory.values() if str(r.get("key"))=="1031"),0)
                if available>=needed:
                    response=service.call("useResearchSpeedUpItem",{"item_id":"1031","slot_index":free,"quantity":needed});hours+=needed
                    service.context.invalidate()
                    fresh=service.snapshot(refresh=True)
                    remaining=next((r for r in rows(fresh.get("user_tasks")) if str(r.get("task_index"))==str(task.get("task_index"))),None)
                    if remaining and integer(remaining.get("end_time"))<=int(service.clock()):
                        service.call("completeResearch",{"task_index":remaining["task_index"]});completed+=1
                    elif remaining:
                        break
                    else:
                        researched=fresh.get("user_combos_compressed") or {}
                        if f"{pair['card1']}~{pair['card2']}" in researched or f"{pair['card2']}~{pair['card1']}" in researched:completed+=1
            service.context.invalidate()
        return Result(rows=pending,message=f"Started {started} research pairs; completed {completed}; spent {hours} one-hour speedups. Remaining pairs wait for a free slot or another run.")
    add("research-deck","Research missing pairs in a saved deck. Optional one-hour speedups have an explicit total budget.",research_deck,
        (deck,Arg("max_pairs",kind="int",default=3,minimum=1,maximum=500),Arg("speedups",kind="bool",default=False),Arg("max_hours",kind="int",default=0,minimum=0,maximum=10000)),True,spends_resources=True)

    def recycle(service,args):
        data=service.snapshot(refresh=True); catalog=service.catalog("card")
        if not data.get("user_decks"):data=service.client.get_exact_decks(data)
        protected={str(u.get("unit_index")) for d in rows(data.get("user_decks")) for u in rows(d.get("units"))}
        allowed={i for i,name in ((1,"common"),(2,"rare"),(3,"epic")) if args[name]}
        candidates=[]
        for unit in rows(data.get("user_units") or service.snapshot().get("user_units")):
            index=unit.get("unit_index"); meta=catalog.get(str(unit.get("unit_id")),{})
            if index is None or str(index) in protected or integer(unit.get("level"))!=1 or integer(unit.get("foil_id"))!=0:continue
            if integer(unit.get("rarity") or meta.get("rarity")) in allowed:candidates.append(integer(index))
        if args["execute"]:
            completed = 0
            for start in range(0,len(candidates),100):
                if getattr(service,"stop",threading.Event()).is_set():break
                batch = candidates[start:start+100]
                service.call("salvageUnitList",{"units":json.dumps(batch,separators=(",",":"))})
                completed += len(batch)
            return Result(message=f"Recycled {completed} of {len(candidates)} selected level-one cards.")
        return Result(rows=[{"unit_index":index} for index in candidates],message=f"Preview: {len(candidates)} eligible cards. Saved decks, foils, upgraded cards, Legendaries and Mythics are protected.")
    add("recycle","Preview or recycle conservative level-one loose Common/Rare/Epic cards.",recycle,
        tuple(Arg(name,kind="bool",default=default) for name,default in (("common",True),("rare",True),("epic",False),("execute",False))),True,destructive=True)
    def guild_top(service,args):
        from .utility_commands import guild_members
        from .account_state import real_trophy_metric
        from .public_profiles import public_profile
        live=service.snapshot();roster=guild_members(live)
        if not roster:
            gid=(live.get("common_fields")or{}).get("guild_id")or(live.get("user_data")or{}).get("faction_id")
            if not gid:raise CommandUnavailable("The current guild is unavailable.")
            roster=guild_members(service.call("getGuildInfo",{"faction_id":gid}))
        ranked=[];unavailable=""
        for member in roster[:args["max_profiles"]]:
            if getattr(service,"stop",threading.Event()).is_set():break
            if not member.get("user_id"):continue
            try:profile=public_profile(service,member["user_id"])
            except CommandUnavailable as exc:
                unavailable=str(exc);break
            score=real_trophy_metric(profile)
            if score:ranked.append({"name":member.get("name"),"sfc_rating":score["value"]})
        if not ranked:raise CommandUnavailable("AT did not expose current SFC ratings. Arena divisions were not substituted.")
        return Result(rows=sorted(ranked,key=lambda r:r["sfc_rating"],reverse=True)[:10],message=f"SFC ranking among {len(ranked)} verified profiles of {len(roster)} guild members."+(" Incomplete: "+unavailable if unavailable else ""))
    add("guild-top","Read your guild's top 10 members by SFC rating.",guild_top,(Arg("max_profiles",kind="int",default=50,minimum=1,maximum=100),))
    add("guild-top-list","Read the game's current top SFC guilds.",lambda service,args:result(service.call("getTopGuilds")))
    add("siege-join","Join the available Siege event with your current guild.",lambda service,args:result(service.call("joinGuildSiege")),action=True)
    add("rumble-deck","Set a Rumble defense deck for a current event/cycle.",
        lambda service,args:result(service.call("setDefenseDeck",{"deck_id":args["deck"],"event_id":args["event"],"cycle_id":args["cycle"]})),
        (deck,Arg("event",kind="int",required=True,minimum=1),Arg("cycle",kind="int",required=True,minimum=0)),True)
    def guild_set(service,args):
        data=service.snapshot(refresh=True); guild_id=(data.get("user_data")or{}).get("faction_id") or (data.get("common_fields")or{}).get("guild_id")
        if not guild_id:raise CommandUnavailable("No current guild was found.")
        info=service.call("getGuildInfo",{"faction_id":guild_id})
        def settings_rows(value):
            if isinstance(value,dict) and "min_rating" in value:return [value]
            return rows(value)
        configs=settings_rows(info.get("faction_data")) or settings_rows(info.get("guild_data")) or ([info["faction"]] if isinstance(info.get("faction"),dict) else [])
        if not configs:raise CommandUnavailable("Current guild settings were not exposed; no settings were changed.")
        settings=configs[0]
        settings={**settings,"text":settings.get("text",settings.get("message")),"icon_id":settings.get("icon_id",settings.get("guild_icon"))}
        keys=("min_rating","text","icon_id","min_sfc_rating","name")
        if any(key not in settings or settings[key] is None for key in keys):raise CommandUnavailable("The complete current guild settings could not be verified.")
        values={k:settings[k] for k in keys};values["recruitment_status"]={"public":0,"invite":1,"closed":2}[args["mode"]]
        return result(service.call("setGuildSettings",values))
    add("guild-set","Change recruitment mode while preserving the live guild's other settings.",guild_set,
        (Arg("mode",kind="choice",required=True,choices=("public","invite","closed")),),True,destructive=True)
    def configure(service,args):
        from .guild_config import save_guild_preferences
        gid,roster=guild_context(service,need_roster=args.get("assignments") is not None)
        changes={key:args.get(key) for key in ("name","defense_plan","report_format","columns","assignments")}
        try:preferences=save_guild_preferences(service.store,gid,changes,roster=roster)
        except ValueError as exc:raise CommandInputError(str(exc)) from None
        return Result(message="Local preferences saved for the current guild.",data=preferences)
    def guild_context(service,need_roster=True):
        from .utility_commands import guild_members
        live=service.snapshot(refresh=True)
        gid=(live.get("user_data")or{}).get("faction_id")or(live.get("common_fields")or{}).get("guild_id")
        if not gid:raise CommandUnavailable("No current guild was found.")
        roster=guild_members(live)
        if need_roster and not roster:roster=guild_members(service.call("getGuildInfo",{"faction_id":gid}))
        if need_roster and not roster:raise CommandUnavailable("The current guild roster could not be verified.")
        return gid,roster
    add("guild-configure","Save per-guild local display settings, report columns and exact-member defense assignments.",configure,
        (Arg("name"),Arg("defense_plan"),Arg("report_format",kind="choice",choices=("html","csv","json")),
         Arg("columns",description="Comma-separated public report columns"),Arg("assignments",description="JSON list of member/island/deck/notes assignments")),True)
    def guild_report(service,args):
        from .config import APP_DIR
        from .guild_config import get_guild_preferences,export_guild_report
        from .diagnostic_privacy import sanitize_report
        gid,roster=guild_context(service)
        preferences=get_guild_preferences(service.store,gid)
        try:artifact=export_guild_report(sanitize_report(roster,service.context.private),APP_DIR/'guild_reports',
            preferences=sanitize_report(preferences,service.context.private),report_format=args.get("format"))
        except ValueError as exc:raise CommandInputError(str(exc)) from None
        return Result(message=f"Exported {artifact['members']} current guild members locally.",data={"artifacts":[artifact]})
    add("guild-report","Export a local dark searchable roster report or spreadsheet-compatible CSV/JSON.",guild_report,
        (Arg("format",kind="choice",choices=("html","csv","json")),))
    def defense_plan(service,args):
        from .config import APP_DIR
        from .guild_config import get_guild_preferences,import_defense_plan,export_defense_plan
        from .diagnostic_privacy import sanitize_report
        importing=args["mode"]=="import"
        gid,roster=guild_context(service,need_roster=importing)
        try:
            if importing:
                if not args["path"]:raise CommandInputError("Choose a local CSV or JSON defense-plan path.")
                answer=import_defense_plan(service.store,gid,args["path"],roster=roster,apply=args["apply"])
                return Result(rows=answer["assignments"],message=answer["message"],data={"applied":answer["applied"]})
            preferences=get_guild_preferences(service.store,gid)
            artifact=export_defense_plan(sanitize_report(preferences,service.context.private),APP_DIR/'guild_reports',plan_format=args["format"])
        except ValueError as exc:raise CommandInputError(str(exc)) from None
        return Result(message="Exported the local defense plan.",data={"artifacts":[artifact]})
    add("guild-defense-plan","Preview/import or export local CSV/JSON assignments; apply saves local preferences only.",defense_plan,
        (Arg("mode",kind="choice",default="export",choices=("export","import")),Arg("path",default=""),
         Arg("format",kind="choice",default="csv",choices=("csv","json")),Arg("apply",kind="bool",default=False)),True)
    def linked_guild(service,args,kind):
        from .account_registry import AccountRegistry
        from .api import ATClient
        from .utility_commands import build_service
        registry=AccountRegistry();out=[];artifacts=[];live=service.snapshot();guild=(live.get("user_data")or{}).get("faction_id")or(live.get("common_fields")or{}).get("guild_id")
        if not guild:raise CommandUnavailable("No current guild was found.")
        accounts=[("Current account",service)]
        cache=getattr(service.context,"_linked_services",{})
        for alias in registry.list():
            credentials=registry.load(alias)
            if credentials==service.client.credentials:continue
            linked=cache.get(alias)
            if linked is None or linked.client.credentials!=credentials:
                linked=build_service(ATClient(credentials),store=service.store);cache[alias]=linked
            accounts.append((alias,linked))
        service.context._linked_services=cache
        for alias,linked in accounts:
            if getattr(service,"stop",threading.Event()).is_set():break
            data=linked.snapshot();user=data.get("user_data")or{}
            linked_guild_id=user.get("faction_id")or(data.get("common_fields")or{}).get("guild_id")
            if str(linked_guild_id)!=str(guild):continue
            member=args["member"].casefold()
            if member and member not in alias.casefold() and member not in str(user.get("name","")).casefold():continue
            forwarded={key:value for key,value in args.items() if key!="member"}
            answer=linked.execute("user-"+kind,forwarded)
            service.context.private.update(linked.context.private)
            out.append({"alias":alias,"name":user.get("name"),"data":answer.rows})
            artifacts.extend({**artifact,"alias":alias} for artifact in answer.data.get("artifacts",[]) if isinstance(artifact,dict))
        return Result(rows=out,message="Private data from the current account and local registrations in this guild. Unregistered members' private data is unavailable.",data={"artifacts":artifacts} if artifacts else {})
    member=Arg("member",default="",description="Local account alias or player name")
    from .utility_commands import card_presentation_args,card_map_artifact
    inventory_args=(member,Arg("query",default=""),Arg("min_rarity",kind="int",default=1,minimum=1,maximum=5),Arg("type",kind="choice",default="all",choices=("all","character","object","final_form")),Arg("trait",default=""),Arg("show",default=""),Arg("skill",default=""),Arg("skill2",default=""),Arg("fuse_level",kind="int",minimum=0,maximum=2),*card_presentation_args())
    add("guild-inventory","Filter private card data for this guild's current/local registered accounts.",lambda service,args:linked_guild(service,args,"inventory"),inventory_args)
    add("guild-mastery","Filter owned mastery data for this guild's current/local registered accounts.",lambda service,args:linked_guild(service,args,"mastery"),(member,Arg("query",default="")))
    def guild_decks(service,args):
        from .utility_commands import guild_members
        from .public_profiles import public_profile,profile_decks,mapped_public_cards
        if args["guild"]:members=guild_members(service.call("getGuildInfo",{"faction_id":args["guild"]}))
        else:
            live=service.snapshot();members=guild_members(live)
            if not members:
                gid=(live.get("user_data")or{}).get("faction_id")or(live.get("common_fields")or{}).get("guild_id")
                if gid:members=guild_members(service.call("getGuildInfo",{"faction_id":gid}))
        if args["member"]:
            wanted=args["member"].casefold()
            members=[r for r in members if wanted in str(r.get("name","")).casefold() or wanted==str(r.get("user_id"))]
        if not members:raise CommandUnavailable("No matching guild members were found.")
        out=[];artifacts=[];unavailable="";verified=0
        for member in members[:args["max_profiles"]]:
            if getattr(service,"stop",threading.Event()).is_set():break
            if not member.get("user_id"):continue
            try:profile=public_profile(service,member["user_id"])
            except CommandUnavailable as exc:unavailable=str(exc);break
            decks,active=profile_decks(profile);verified+=1
            wanted=args["deck"] if args["deck"] is not None else active
            for saved in decks:
                if not args["all_decks"] and str(saved.get("deck_id"))!=str(wanted):continue
                cards=mapped_public_cards(saved,service.catalog("card"))
                row={"member":member.get("name"),"deck":saved.get("deck_id"),"name":saved.get("name"),
                     "active":str(saved.get("deck_id"))==str(active),"hero":saved.get("commander"),"card_count":len(cards)}
                if args["map"]:row["cards"]=cards
                if args.get("image_type","none")!="none":
                    artifact=card_map_artifact(service,args,cards,title=f"{member.get('name')} — Deck {saved.get('deck_id')}")
                    if artifact:artifacts.append({**artifact,"member":member.get("name"),"deck":saved.get("deck_id")})
                out.append(row)
        if not out:raise CommandUnavailable("AT did not expose matching public decks."+(" "+unavailable if unavailable else ""))
        return Result(rows=out,message=f"Public decks from {verified} verified profiles of {len(members)} selected guild members."+(" Incomplete: "+unavailable if unavailable else ""),data={"artifacts":artifacts} if artifacts else {})
    add("guild-deck","List or map guild members' public active decks, with optional member/slot filters.",guild_decks,
        (Arg("guild",kind="int",minimum=1),Arg("member",default=""),Arg("deck",kind="int",minimum=0),Arg("map",kind="bool",default=False),Arg("all_decks",kind="bool",default=False),Arg("max_profiles",kind="int",default=50,minimum=1,maximum=100),*card_presentation_args()))

    def play_status(service,args):
        live=service.snapshot(refresh=True)
        arena,_=find_arena_energy(live); adventure,maximum,recharge=find_adventure_energy(live)
        energy_rows=[{"mode":"Arena","energy":arena},{"mode":"Adventure","energy":adventure,"max":maximum}]
        swole=current_swole(live,now=service.clock())
        if swole:energy_rows.append({"mode":"Swole","energy":swole["current"],"max":swole["max"],"level":swole["level"]})
        return Result(rows=energy_rows,details=json.dumps(discover_challenge_events(live),indent=2))
    add("play-status","Read live Arena/Adventure energy and active Challenge/Swole/Clash/Melee events.",play_status)
    def adventure_route(service,args):
        from .adventure_routes import resolve_route
        chosen=resolve_route(service,service.snapshot(),args["level"],args["mission"])
        target="mastered" if str(args["level"]).strip().casefold().endswith("mastery") else "completed"
        return Result(rows=[chosen] if chosen else [],message=f"All exposed missions in this route are {target}." if chosen is None else "Preview only: no energy spent.")
    add("adventure-route","Preview a chapter-stage, story progress/mastery, or a scoped campaign route such as 9040-progress.",adventure_route,
        (Arg("level",default=""),Arg("mission",kind="int",minimum=1)))
    def arena_targets(service,args):
        from .hero_hunting import hero_names
        names=hero_names(service);data=service.call("getHuntingTargets")
        return Result(rows=[{"opponent":r.get("name"),"reward_hero":names.get(str(r.get("hero_xp_id")),"Unavailable"),"refresh_nixons":r.get("refresh_cost"),"sfc_rating":r.get("pvp_rating")} for r in rows(data.get("hunting_targets"))],message="Current targets only; no opponent refreshed and no battle started.")
    add("arena-targets","Read the current Arena opponent, token hero and refresh price.",arena_targets)
    def reward_claim(service,args):
        from .reward_options import CLAIM_KINDS,reward_state,claim_plan,execute_claim_plan,bounded_dependency_claims
        kinds=CLAIM_KINDS if args["kind"]=="all" else (args["kind"],)
        plan=claim_plan(reward_state(service),kinds,int(service.clock()))
        if not args["execute"]:
            return Result(rows=plan,message="Preview only. Event milestone eligibility is checked by the game when claimed; no request was sent.")
        done=execute_claim_plan(service,[row for row in plan if row["kind"]!="quests"])
        if "quests" in kinds:done+=bounded_dependency_claims(service)
        skipped=sum(bool(row.get("unavailable")) for row in done)
        return Result(rows=done,message=f"Confirmed {len(done)-skipped} selected reward claim requests; {skipped} event reward(s) are currently unavailable. Already claimed/unearned rewards may produce no new items.")
    add("reward-claim","Preview or claim selected daily, completed quest and named event milestones using live IDs/cycles.",reward_claim,
        (Arg("kind",kind="choice",default="all",choices=("all","daily","quests","mega","swole","clash","ecmc","sfc")),Arg("execute",kind="bool",default=False)),True)
    def takeover_preview(service,args):
        from .takeover_quests import takeover_status
        status=takeover_status(service.snapshot(refresh=True),int(service.clock()))
        return Result(rows=status["quests"],message=status["message"],data={"status":status["status"]})
    add("takeover-status","Preview current Takeover quest progress and requirement availability without switching heroes or fighting.",takeover_preview)
    def play(service,args):
        from .arena_runner import ArenaRunner
        from .event_runner import EventBattleRunner
        if args["bypass_arena_turn_limit"] and args["mode"]!="arena":
            raise CommandInputError("Bypass Arena turn limit requires Arena mode.")
        if args["melee_character_first"] and args["mode"]!="melee":
            raise CommandInputError("Character-first battle logic requires Melee mode.")
        if args["melee_character_first"] and args["skip"]:
            raise CommandInputError("Turn off game-AI skip to use character-first Melee logic.")
        client=service.client;driver=ArenaRunner(move_delay=.1,event=service.emit,bypass_turn_limit=args["bypass_arena_turn_limit"]);driver._client=client
        driver._stop=getattr(service,"stop",threading.Event())
        if args["adventure_level"] and (args["mode"]!="adventure" or args["mission"]):raise CommandInputError("Adventure level requires Adventure mode and an empty mission ID.")
        if args["takeover_quest_mode"]:
            if args["mode"]!=args["takeover_quest_mode"]:raise CommandInputError("Takeover quest mode must match the selected Arena or PvP Challenge mode.")
            from .takeover_quests import run_takeover_battles
            return run_takeover_battles(service,args)
        wanted=None
        if args["hero_tokens"]:
            if args["mode"]=="pvp-challenge":raise CommandUnavailable("AT's public client exposes no PvP Challenge token-target selection or refresh request. No fight was started.")
            if args["mode"]!="arena":raise CommandInputError("Hero token hunting currently supports Arena mode.")
            from .hero_hunting import requested_heroes
            wanted=requested_heroes(service,args["hero_tokens"])
        if args["deck"]:service.execute("deck-select",{"deck":args["deck"]})
        if args["claim_daily"]:service.execute("user-claim",{"type":"daily"})
        if args["collect_research"]:service.execute("research-collect")
        if args["use_lootcrate"]:service.execute("item-use",{"item":"lootcrate","quantity":"all"})
        if args["buy_basic"]:service.execute("buy-basic",{"quantity":"3"})
        completed=[];refresh_spent=refresh_count=0;stop_reason="";unavailable_event=None
        for battle in range(1,args["count"]+1):
            if driver._stop.is_set():break
            live=service.snapshot(refresh=True);mode=args["mode"]
            if mode=="arena":
                energy,_=find_arena_energy(live)
                if energy is None:raise CommandUnavailable("Live Arena energy is unavailable; no fight was started.")
                if energy<=args["reserve"] or energy<args["min_energy"]:break
                if wanted:
                    from .hero_hunting import hunt_target
                    target,spent,refreshed=hunt_target(service,wanted,args["max_refreshes"]-refresh_count,args["max_refresh_nixons"]-refresh_spent)
                    refresh_spent+=spent;refresh_count+=refreshed
                    if target is None:break
                    response=service.call("startHuntingBattle",{"target_user_id":target["user_id"]})
                else:response=client.start_arena_battle()
            elif mode=="adventure":
                from .adventure_routes import resolve_route
                route=resolve_route(service,live,args["adventure_level"],args["mission"])
                if route is None:
                    stop_reason="All exposed missions in the selected Adventure route are complete.";break
                energy,_,_=find_adventure_energy(live)
                cost=route["energy_cost"]
                if energy is None or energy<cost+args["reserve"]:break
                response=service.call("startMission",{"mission_id":route["mission"],"auto_battle":"0"})
            elif mode=="rumble":
                from .protocol_config import load_profiles
                profile=load_profiles().get("rumble")
                if not profile:raise CommandUnavailable("The working Rumble start profile is unavailable.")
                response=client.call_profile(profile)
            elif mode=="siege":
                event=EventBattleRunner(mode="Siege");status,targets=event._siege_status_rows(client.get_guild_siege_status())
                target=event._choose_siege_target(targets)
                if not target:break
                response,state=event._try_siege_start(client,status,target)
                if not state:break
            else:
                if mode=="swole":
                    status=current_swole(live,now=service.clock())
                    if status is None:raise CommandUnavailable("Current Swole tickets and level were unavailable. No fight was started.")
                    cid=status["challenge_id"];level=status["level"];energy=status["current"]
                    if energy<status["cost"]+args["reserve"]:
                        stop_reason=f"Swole has {energy} tickets; the configured reserve and battle cost prevent another fight.";break
                    if args["swole_level"] is not None:
                        if args["swole_level"]>integer(level):raise CommandInputError("Swole farming level cannot exceed your unlocked level.")
                        level=args["swole_level"]
                elif mode=="pvp-challenge":
                    from .pvp_challenge import resolve_pvp_challenge
                    current=resolve_pvp_challenge(live,args["event"],now=service.clock())
                    if current is None:raise CommandUnavailable("No unambiguous current PvP Challenge with an explicit type-2 marker and fight state is available. Select its live event ID; no fight was started.")
                    if current["current"]<current["cost"]+args["reserve"]:
                        stop_reason="The current PvP Challenge energy and configured reserve prevent another fight.";break
                    cid=current["challenge_id"];level=current["level"]
                else:
                    event_id=args["event"]
                    if not event_id:raise CommandInputError("Challenge, Clash and Melee require the live event ID shown by play-status.")
                    current=current_challenge(live,str(event_id),now=service.clock())
                    if not current or current["current"]<current["cost"]+args["reserve"]:break
                    if args["melee_character_first"]:
                        from .strategy import is_melee_event,SingleComboRushPolicy
                        if not is_melee_event(current.get("event_name")):
                            raise CommandInputError("The selected live event is not a Melee; character-first logic was not applied.")
                        driver.policy=SingleComboRushPolicy(character_first=True)
                    cid=current["challenge_id"];level=current["level"]
                if args["max_level"] is not None and integer(level)>=args["max_level"]:break
                response=client.start_challenge(cid,level)
            state=client.extract_battle_data(response)
            if api_rejected(response) or not state or not state.get("battle_id"):
                from .diagnostic_privacy import private_literals,safe_text
                service.context.private.update(private_literals(response))
                reason=response.get("result_message") or response.get("error") if isinstance(response,dict) else None
                reason=safe_text(str(reason or "No battle state was returned."),service.context.private)[:350]
                if api_rejected(response) and mode in {"swole","challenge","pvp-challenge","clash","melee"} and re.search(r"(?<!\w)warning_126_text(?!\w)",reason):
                    stop_reason=f"AT reports {mode} challenge {cid} is inactive. The event was skipped; no start was retried."
                    unavailable_event={"event_id":status["event_id"] if mode=="swole" else current["event_id"],
                        "challenge_id":str(cid),"code":"warning_126_text","reason":stop_reason}
                    break
                selected=f" {mode} challenge {cid}, level {level}." if mode in {"swole","challenge","pvp-challenge","clash","melee"} else f" {mode}."
                raise CommandUnavailable("AT returned no valid battle."+selected+" "+reason+" The start was not retried.")
            if args["skip"]:
                last_response=service.call("playCard",{"battle_id":state["battle_id"],"host_id":state["host_id"],"skip":"1"})
                last_state=client.extract_battle_data(last_response)
                won=client.extract_outcome(last_response,last_state)
                if not client.battle_finished(last_response,last_state) or won is None and not (last_response.get("rewards") or (last_state or {}).get("results") or (last_state or {}).get("battle_finished")):
                    raise CommandUnavailable("The game did not confirm that the skip finished the battle. No skip was retried.")
                moves=0
            else:
                won,moves,last_response,last_state=driver._drive_one(state,battle,client.opponent_name(state))
            completed.append({"battle":battle,"mode":mode,"won":won,"moves":moves})
            service.context.invalidate()
            if won is None:break
        selected=[kind for kind in ("mega","swole","clash","ecmc","sfc") if args["claim_"+kind]]
        if args["collect_quests"]:selected.append("quests")
        claims=[]
        if selected and not driver._stop.is_set():
            from .reward_options import reward_state,claim_plan,execute_claim_plan,bounded_dependency_claims
            claims=execute_claim_plan(service,[row for row in claim_plan(reward_state(service),selected,int(service.clock())) if row["kind"]!="quests"])
            if "quests" in selected:claims+=bounded_dependency_claims(service)
        return Result(rows=completed,message=f"Completed {len(completed)} battle(s)."+(f" Refreshed {refresh_count} Arena opponents for {refresh_spent:,} Nixons within the block's total budget." if wanted else "")+(" "+stop_reason if stop_reason else "")+(f" Confirmed {len(claims)} selected reward claim requests." if selected else ""),data={"claims":claims,**({"unavailable_event":unavailable_event} if unavailable_event else {})})
    from .shop_rewards import api_rejected
    arena_turn_limit=Arg("bypass_arena_turn_limit",kind="bool",default=False,description="Arena only: continue past Commander's 30-turn cutoff until AT confirms win/loss. Stop and Pause remain available.")
    add("play","Run a bounded serial battle block with the established Item → Combo engine; Adventure accepts chapter-stage and scoped progress/mastery routes.",play,
        (Arg("mode",kind="choice",default="arena",choices=("arena","adventure","rumble","siege","swole","challenge","pvp-challenge","clash","melee")),Arg("count",kind="int",default=1,minimum=1,maximum=1000),Arg("reserve",kind="int",default=0,minimum=0),Arg("deck",kind="int",minimum=1),Arg("mission",kind="int",minimum=1),Arg("adventure_level",default=""),Arg("event",kind="int",minimum=1),Arg("min_energy",kind="int",default=0,minimum=0),Arg("max_level",kind="int",minimum=1),Arg("swole_level",kind="int",minimum=1),Arg("skip",kind="bool",default=False),arena_turn_limit,Arg("melee_character_first",kind="bool",default=False,description="Melee only: open a character lane, then add its item to form the combo. Requires Commander logic, with game-AI skip off."),Arg("collect_research",kind="bool",default=False),Arg("claim_daily",kind="bool",default=False),Arg("hero_tokens",default=""),Arg("max_refreshes",kind="int",default=0,minimum=0,maximum=100),Arg("max_refresh_nixons",kind="int",default=0,minimum=0),Arg("collect_quests",kind="bool",default=False),Arg("use_lootcrate",kind="bool",default=False),Arg("buy_basic",kind="bool",default=False),Arg("takeover_quest_mode",kind="choice",default="",choices=("","arena","pvp-challenge"),description="Requires verified live Takeover hero requirements; takeover-status explains current availability."),*(Arg("claim_"+kind,kind="bool",default=False) for kind in ("mega","swole","clash","ecmc","sfc"))),True,spends_resources=True,execution_mode="runner")

    def daily_upgrade(service,args):
        from .shop_rewards import daily_upgrade_progress,safe_upgrade_candidates,upgrade_confirmed
        live=service.snapshot(refresh=True);progress=daily_upgrade_progress(live)
        if progress is None:return Result(message="No verified Daily Upgrade Cards quest is exposed. No card was changed.")
        needed=min(progress[2],args["max_upgrades"])
        if not needed:return Result(message="Daily Upgrade Cards is already complete.")
        candidates=safe_upgrade_candidates(live,live,service.catalog("card"))
        if len(candidates)<needed:raise CommandUnavailable("There are too few safe loose cards. No upgrade was sent.")
        done=0;previous=progress[0]
        for card in candidates[:needed]:
            if getattr(service,"stop",threading.Event()).is_set():break
            response=service.call("upgradeUnit",{"unit_index":card["unit_index"]})
            confirmed=upgrade_confirmed(response,card["unit_index"],1)
            service.context.invalidate();fresh=service.snapshot(refresh=True)
            if not confirmed and not upgrade_confirmed(fresh,card["unit_index"],1):raise CommandUnavailable(f"Stopped after {done} confirmed upgrades. The latest outcome was not verified and was not retried.")
            done+=1;current=daily_upgrade_progress(fresh)
            if current is None or current[0]<=previous:raise CommandUnavailable(f"{done} card upgrades were confirmed, but quest progress did not advance. No further card was touched.")
            previous=current[0]
            if current[2]<=0:break
        return Result(message=f"Completed {done} verified safe Daily quest upgrades.")
    add("daily-upgrade","Finish only verified remaining Daily upgrades, protecting saved decks, foils, locked and Legendary/Mythic cards.",daily_upgrade,
        (Arg("max_upgrades",kind="int",default=3,minimum=1,maximum=3),),True,spends_resources=True)
    def skip(service,args):
        state=service.client.extract_battle_data(service.snapshot(refresh=True))
        if not state or not state.get("battle_id") or not state.get("host_id"):raise CommandUnavailable("No active battle was found to skip.")
        return result(service.call("playCard",{"battle_id":state["battle_id"],"host_id":state["host_id"],"skip":"1"}))
    add("play-skip","Skip the remaining plays of a current battle through the game's own skip operation.",skip,action=True,destructive=True)
