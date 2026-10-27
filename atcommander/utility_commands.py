"""Local Bender-style account tools using the current client and public protocol.

No bot, Discord login, or third-party computer is involved. Reads are available
without initialization of a game window. Actions use fresh account state; an
ambiguous action is never retried here.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .local_commands import ArgumentSpec as Arg, CommandSpec as Spec, CommandResult as Result
from .local_commands import CommandInputError, CommandUnavailable, LocalCommandService
from .command_store import CommandStore
from .config import APP_DIR
from .account_state import exact_decks, unlocked_heroes
from .session_stats import build_inventory_snapshot
from .shop_rewards import extract_item_choices, choose_item_reward, item_choice_confirmed
from .card_presentation import at_level, card_level_definitions, fusion_level, summary as card_summary
from .card_gallery import OfficialArtCache, export_card_map, official_art_url
from .diagnostic_privacy import sanitize_report


def rows(value):
    if isinstance(value, dict):
        return [{"id": str(k), **v} if isinstance(v, dict) else {"id": str(k), "value": v}
                for k, v in value.items()]
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def section(data, key):
    found = []
    def walk(obj, depth=0):
        if depth > 7:
            return
        if isinstance(obj, dict):
            if key in obj:
                found.extend(rows(obj[key]))
            for k, val in obj.items():
                if k != key and isinstance(val, (dict, list)):
                    walk(val, depth + 1)
        elif isinstance(obj, list):
            for val in obj:
                walk(val, depth + 1)
    walk(data)
    return found


def result(data, *, message="", keys=()):
    selected = {k: data[k] for k in keys if k in data} if keys else data
    return Result(details=json.dumps(selected, indent=2, ensure_ascii=False), message=message,
                  rows=rows(selected) if not keys else [])


def integer(value, fallback=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return fallback


def guild_members(data):
    for key in ("faction", "guild_data", "faction_data"):
        value = data.get(key)
        if isinstance(value,dict) and "members" in value:
            return rows(value["members"])
    return section(data,"members")


def resolve_choice_rewards(service,current):
    pending=extract_item_choices(current,service.catalog("card"));groups={};count=0
    for choice in pending:groups.setdefault((choice["group"],choice["choice_type"]),[]).append(choice)
    for options in groups.values():
        if getattr(service,"stop",None) is not None and service.stop.is_set():break
        selected=choose_item_reward(options)
        response=service.call("confirmUserChoice",{"type":selected["choice_type"],"choice":selected["id"]})
        if not item_choice_confirmed(response,selected["choice_type"]):
            raise CommandUnavailable("The reward choice was not confirmed; no confirmation was retried.")
        count+=1
    return count


def card_presentation_args():
    """Shared export choices for local card and guild commands."""
    return (Arg("display",kind="choice",default="full",choices=("full","summary")),
            Arg("image_type",kind="choice",default="none",choices=("none","svg","html","png","jpeg"),
                description="Export a local card map with exact stats and available public game artwork."),
            Arg("layout",kind="choice",default="grid",choices=("grid","compact","list")),
            Arg("columns",kind="int",default=3,minimum=1,maximum=8))


def card_map_artifact(service,args,pictures,*,title="Card map"):
    """Render supplied exact levels, sanitizing before any file is written."""
    if args.get("image_type","none")=="none":return None
    stopped=lambda:getattr(service,"stop",None) is not None and service.stop.is_set()
    if stopped():raise CommandInputError("Card map export stopped before completion.")
    if not 1<=len(pictures)<=100:raise CommandInputError("Card maps support 1–100 cards. Narrow the filters first.")
    private=service.context.private
    cache=getattr(service,"card_art_cache",None)
    if cache is None:
        cache=OfficialArtCache(APP_DIR/"card_art_cache",
            base_url=getattr(service,"official_card_art_base_url",None))
        service.card_art_cache=cache
    # Real illustrations are named sprites in Unity bundles. Read the public
    # manifest once per command through the existing cached account context;
    # plain stat maps and explicit raster URLs need no additional game read.
    if isinstance(cache,OfficialArtCache):
        from .card_bundle_art import BundleArtProvider,card_bundle_key,public_bundle_manifest
        if any(card_bundle_key(row) and not official_art_url(row,cache.base_url) for row in pictures):
            try:
                manifest=public_bundle_manifest(service.snapshot())
                if manifest.urls:
                    if cache.bundle_provider is None:
                        cache.bundle_provider=BundleArtProvider(APP_DIR/"card_art_cache",manifest,should_stop=stopped)
                    else:cache.bundle_provider.configure(manifest,should_stop=stopped)
                else:cache.bundle_provider=None
            except Exception:
                # A public art read must not make an otherwise useful stat map
                # fail or expose a runtime/account error in an exported file.
                cache.bundle_provider=None
    try:
        return export_card_map(sanitize_report(pictures,private),APP_DIR/"card_exports",
            image_type=args["image_type"],layout=args.get("layout","grid"),columns=args.get("columns",3),
            title=sanitize_report(title,private),art_cache=cache,
            should_stop=stopped)
    except ValueError as exc:
        raise CommandInputError(str(exc)) from None
    except OSError:
        raise CommandUnavailable("The local card map could not be saved.") from None


def build_service(client, *, store=None, emit=None, **options):
    service = LocalCommandService(client, emit=emit, **options)
    service.store = store or CommandStore(APP_DIR / "command_preferences.json")
    register_utilities(service)
    from .extended_commands import register_extended
    register_extended(service)
    from .game_commands import register_game_commands
    register_game_commands(service)
    from .automation_profiles import register_automation_profiles
    from .login_bonus import register_daily_login,login_bonus_status
    register_daily_login(service)
    service.daily_capabilities={"boost":True,"mom_free":True,"daily_login":lambda live:login_bonus_status(live)["available"]}
    register_automation_profiles(service)
    def boost(current,_live):
        from .ad_ui import attempt_legitimate_ad_ui
        from .account_state import find_ad_state
        if find_ad_state(_live).get("boost_level")==3:return {"ok":True,"boost_level":3,"attempts":0}
        return attempt_legitimate_ad_ui(
            lambda message:current.emit({"kind":"log","message":message}),client=current.client,
            should_stop=lambda:getattr(current,"stop",None) is not None and current.stop.is_set())
    service.research_boost_callback=boost
    def research_ad(current,live):
        from .research_ad_ui import play_research_ad
        return play_research_ad(current.client,context=live.get('_commander_research'),
            log=lambda message:current.emit({'kind':'log','message':message}),
            should_stop=lambda:getattr(current,'stop',None) is not None and current.stop.is_set())
    research_ad.supported_phases={'speedup'}
    service.research_ad_callback=research_ad
    from .research_commands import register_research_automation
    register_research_automation(service)
    return service


def register_utilities(s):
    def add(name, description, handler, args=(), action=False, **flags):
        s.register(Spec(name, name.split("-")[0].title(), description, tuple(args), action=action,
                        evidence="AT web client 1.181.0 / Commander verified calls", **flags), handler)
    query = Arg("query", default="")
    deck = Arg("deck", kind="int", required=True, minimum=1)
    number = Arg("number", kind="int", default=1, minimum=1, maximum=10000)
    presentation_args = card_presentation_args()

    def presented(service,args,values,message,*,pictures=None):
        answer = Result(rows=[card_summary(row) for row in values] if args.get("display") == "summary" else values,
                        message=message)
        artifact=card_map_artifact(service,args,pictures if pictures is not None else values)
        if artifact is not None:
            answer.data["artifacts"]=[artifact]
            answer.details=f"Local card map: {artifact['path']}"
            if artifact["missing_artwork"]:
                answer.message+=f" Artwork unavailable for {artifact['missing_artwork']} card(s); exact available stats are shown."
        return answer

    def cards(service, args):
        catalog = service.catalog("card")
        text = str(args.get("query") or "").casefold()
        if "|" in text:
            parts = [part.strip() for part in text.split("|")]
            if len(parts)!=2 or not all(parts):raise CommandInputError("For a combo lookup, enter two card names separated by |.")
            inputs = [{cid for cid,row in catalog.items() if isinstance(row,dict) and word in (str(cid)+" "+str(row.get("name",""))).casefold()} for word in parts]
            combined = {}
            for pair,value in service.catalog("combo").items():
                pair_ids = str(pair).split("~")
                if len(pair_ids)!=2:continue
                first,second=pair_ids
                if not ((first in inputs[0] and second in inputs[1]) or (first in inputs[1] and second in inputs[0])):continue
                cid=str(value).split("|",1)[0]
                if cid not in catalog:continue
                combined.setdefault(cid,{"id":cid,**catalog[cid],"recipes":[]})["recipes"].append({"card1":first,"card1_name":catalog[first].get("name"),"card2":second,"card2_name":catalog[second].get("name")})
            matches=list(combined.values())
        else:
            words = text.split()
            matches = [{"id": cid, **row} for cid, row in catalog.items()
                       if isinstance(row, dict) and all(w in (str(cid) + " " + str(row.get("name", ""))).lower() for w in words)]
        if args.get("level") is not None or args.get("display") == "summary" or args.get("image_type","none") != "none":
            selected=[]
            snapshot=service.context._snapshot or getattr(service.client,"_init_snapshot",{}) or {}
            definitions=card_level_definitions(snapshot.get("card_level_data",{}))
            for row in matches:
                try:
                    current=at_level(row,args.get("level"))
                except ValueError:
                    continue
                fusion=fusion_level(current,current,definitions)
                if fusion is not None:current["fusion_level"]=fusion
                selected.append(current)
            if matches and not selected:
                raise CommandInputError("That level is not present in the matching cards' current static data.")
            matches=selected
        message=(f"{len(matches)} actual combos matched those input cards." if "|" in text else f"{len(matches):,} matching cards.")
        return presented(service,args,matches[:2000],message)

    add("card-info", "Read a card or item | character combo, select an exact static level, or export a local stat sheet.", cards,
        (query,Arg("level",kind="int",minimum=1,maximum=100),*presentation_args))
    def combos(service, args):
        from .cm_reviews import CMReviewEngine, _traits, _skill_ids
        from .card_filters import card_matches,any_text
        catalog=service.catalog("card"); engine = CMReviewEngine(catalog, {})
        matches={cid:row for cid,row in engine.combos.items() if any_text(str(cid)+" "+str(row.get("name","")),args["query"]) and card_matches(row,args)}
        out=[]
        for pair,value in service.catalog("combo").items():
            parts=str(pair).split("~");cid=str(value).split("|",1)[0]
            if len(parts)!=2 or cid not in matches:continue
            first=catalog.get(parts[0],{});second=catalog.get(parts[1],{})
            character,item=(first,second) if str(first.get("card_type")).casefold()=="character" else (second,first)
            if not any_text(character.get("name",""),args["character"]):continue
            if not any_text(item.get("name",""),args["item"]):continue
            if min(integer(first.get("rarity")),integer(second.get("rarity")))<args["min_rarity"]:continue
            out.append({"combo_id":cid,"combo":matches[cid].get("name"),"card1":parts[0],"card1_name":first.get("name","Unknown"),"card2":parts[1],"card2_name":second.get("name","Unknown"),"trait":_traits(matches[cid]),"skills":_skill_ids(matches[cid])})
        matched_ids={str(recipe["combo_id"]) for recipe in out}
        pictures=[at_level({"id":cid,**row}) for cid,row in matches.items() if cid in matched_ids]
        answer=presented(service,{**args,"display":"full"},out[:5000],f"{len(out):,} matching recipes across {len(matches)} combo definitions. Showing up to 5,000 recipes.",pictures=pictures)
        if args.get("display")=="summary":
            answer.data["combo_definitions"]=[card_summary(at_level(row)) for row in pictures]
        return answer
    add("card-combo", "Find current combo recipes by combo, character, item, trait, skill and input rarity.", combos,
        (query,Arg("character",default=""),Arg("item",default=""),Arg("trait",default=""),Arg("skill",default=""),Arg("skill2",default=""),Arg("show",default=""),Arg("min_rarity",kind="int",default=1,minimum=1,maximum=5),*presentation_args))

    def decks(service):
        data=service.snapshot(refresh=True)
        return data if data.get("user_decks") else service.client.get_exact_decks(data)
    def find_deck(data, target):
        candidates = rows(data.get("user_decks"))
        chosen = next((r for r in candidates if str(r.get("deck_id", r.get("id"))) == str(target)), None)
        if not chosen:
            raise CommandInputError("That saved deck slot does not exist.")
        return chosen
    def list_decks(service, args):
        data = decks(service)
        return Result(rows=exact_decks(data), details=json.dumps({"local_backups": list(service.store.read().get("decks", {}))}, indent=2))
    add("deck-list", "List in-game decks and local named backups.", list_decks)
    def map_deck(service, args):
        data = decks(service); saved = find_deck(data, args["deck"])
        units = {str(r.get("unit_index", r.get("id"))): r for r in rows(data.get("user_units") or service.snapshot().get("user_units"))}
        catalog = service.catalog("card")
        mapped = [];pictures=[]
        definitions=card_level_definitions(data.get("card_level_data",{}))
        for unit in rows(saved.get("units")):
            index = str(unit.get("unit_index", unit.get("id")))
            owned = units.get(index, unit); cid = str(owned.get("unit_id", owned.get("card_id", "")))
            mapped.append({"unit_index": index, "card_id": cid, "name": catalog.get(cid, {}).get("name", "Unknown"), **owned})
            meta=catalog.get(cid,{})
            try:current=at_level(meta,owned.get("level",1))
            except ValueError:
                current={k:v for k,v in meta.items() if k not in {"attack","health","power","skill","skills","upgrade","upgrades"}}
                current["stats_status"]="Current level stats unavailable"
            current={**current,**owned,"card_id":cid,"name":meta.get("name","Unknown")}
            fusion=fusion_level(owned,meta,definitions)
            if fusion is not None:current["fusion_level"]=fusion
            pictures.append(current)
        answer=presented(service,args,pictures if args.get("display")=="summary" else mapped,
                         f"Deck {args['deck']}",pictures=pictures)
        if args.get("image_type","none")=="none" and args.get("display","full")=="full":
            answer.details=json.dumps(saved,indent=2)
        return answer
    add("deck-map", "Map a saved deck to owned cards or export exact current card levels.", map_deck, (deck,*presentation_args))
    def select(service, args):
        find_deck(decks(service), args["deck"])
        response=service.call("setActiveDeck",{"deck_id":str(args["deck"])})
        active=(response.get("user_data")or{}).get("active_deck")
        if active is not None and str(active)!=str(args["deck"]):raise CommandUnavailable("AT did not confirm the selected deck. No request was retried.")
        return Result(message=f"Selected deck {args['deck']}.")
    add("deck-select", "Switch the active deck to an existing saved slot.", select, (deck,), True)
    def rename(service, args):
        find_deck(decks(service), args["deck"])
        return result(service.call("setDeckName", {"deck_id": args["deck"], "name": args["name"]}), message="Deck renamed.")
    add("deck-rename", "Rename an in-game saved deck.", rename, (deck, Arg("name", required=True)), True)
    def hero(service, args):
        find_deck(decks(service), args["deck"])
        heroes = unlocked_heroes(service.call("getHeroSquad"), service.context._catalogs.get("hero",{}))
        matches = [r for r in heroes if args["hero"].casefold() in {r["name"].casefold(), r["id"]}]
        if len(matches) != 1:
            raise CommandInputError("Choose an exact unlocked hero name or ID.")
        return result(service.call("setDeckCommander", {"deck_id": args["deck"], "commander_id": matches[0]["id"]}), message="Deck hero updated.", keys=("result", "result_message"))
    add("deck-hero", "Set a saved deck's hero using an exact unlocked name or ID.", hero, (deck, Arg("hero", required=True)), True)
    def save(service, args):
        saved = find_deck(decks(service), args["deck"])
        name = args["name"] or saved.get("name") or f"Deck {args['deck']}"
        backups = service.store.read().get("decks", {})
        backups[name] = {k: saved[k] for k in ("name", "units", "commander", "commander_id", "commander_index", "deck_id") if k in saved}
        service.store.update("decks", backups)
        return Result(message=f"Saved local backup: {name}")
    add("deck-save", "Save a local deck backup; existing names are replaced.", save, (deck, Arg("name", default="")), True)
    def drop(service, args):
        backups = service.store.read().get("decks", {})
        if args["name"] not in backups:
            raise CommandInputError("That local backup does not exist.")
        del backups[args["name"]]; service.store.update("decks", backups)
        return Result(message="Local backup removed.")
    add("deck-drop", "Remove a named local backup.", drop, (Arg("name", required=True),), True, destructive=True)
    def export_deck(service, args):
        data = find_deck(decks(service), args["deck"])
        catalog = service.catalog("card")
        card_rows = map_deck(service, args).rows
        public = {"name": data.get("name", ""), "cards": [{k: r.get(k) for k in ("card_id", "name", "level", "foil_id")} for r in card_rows]}
        return result(public, message="Copy this deck list or save the result to share it.")
    add("deck-share", "Create a shareable local deck list without account or card-instance identifiers.", export_deck, (deck,))
    def build_cards(service, args):
        owned = rows(service.snapshot(refresh=True).get("user_units")); catalog = service.catalog("card")
        out = [{**row, "name": catalog.get(str(row.get("unit_id")), {}).get("name", "Unknown")}
               for row in owned if args["query"].lower() in str(catalog.get(str(row.get("unit_id")), {}).get("name", "")).lower()]
        return Result(rows=out, message="Owned card pool for deck building.")
    add("deck-build", "Search the owned card pool for deck construction.", build_cards, (query,))

    def inventory(service, args):
        data = build_inventory_snapshot(service.snapshot(refresh=True), service.catalog("card"), service.catalog("item"))
        return Result(rows=list(data["entries"].values()), message="Live inventory refreshed.")
    add("item-list", "List current named items, resources and quantities.", inventory)
    def card_inventory(service,args):
        from .card_filters import card_matches,any_text
        catalog=service.catalog("card");data=service.snapshot()
        out=[];unknown_fusion=0;definitions=card_level_definitions(data.get("card_level_data",{}))
        for unit in rows(data.get("user_units")):
            meta=catalog.get(str(unit.get("unit_id")),{})
            if integer(meta.get("rarity",unit.get("rarity",0)))<args["min_rarity"]:continue
            if not any_text(meta.get("name",""),args["query"]):continue
            kind=str(meta.get("card_type","")).casefold()
            if kind=="item":kind="object"
            if args["type"]!="all" and kind!=args["type"]:continue
            fusion=fusion_level(unit,meta,definitions)
            if args.get("fuse_level") is not None:
                if fusion is None:unknown_fusion+=1;continue
                if fusion!=args["fuse_level"]:continue
            try:
                current=at_level(meta,unit.get("level",1))
            except ValueError:
                current={k:v for k,v in meta.items() if k not in {"attack","health","power","skill","skills","upgrade","upgrades"}}
                current["stats_status"]="Current level stats unavailable"
            if not card_matches(current,args):continue
            row={**current,**unit,"name":meta.get("name","Unknown")}
            if fusion is not None:row["fusion_level"]=fusion
            out.append(row)
        if args.get("fuse_level") is not None and not out and unknown_fusion:
            raise CommandUnavailable("AT did not expose fusion definitions for the matching cards. Fusion was not inferred from their levels.")
        message=f"{len(out)} owned cards matched. Save Result exports this local inventory."
        if unknown_fusion:message+=f" {unknown_fusion} cards with unknown fusion were omitted."
        return presented(service,args,out,message)
    add("user-inventory", "Filter and export your owned card inventory locally.", card_inventory,
        (query,Arg("min_rarity",kind="int",default=1,minimum=1,maximum=5),Arg("type",kind="choice",default="all",choices=("all","character","object","final_form")),Arg("trait",default=""),Arg("show",default=""),Arg("skill",default=""),Arg("skill2",default=""),Arg("fuse_level",kind="int",minimum=0,maximum=2,description="Exact fusion: 0 single, 1 dual, 2 quad; requires authoritative fusion data."),*presentation_args))
    def mastery(service, args):
        from .cm_reviews import CMReviewEngine,_current_mastery
        live=service.snapshot();engine=CMReviewEngine(service.catalog("card"),live);out=[]
        for cid,meta in engine.combos.items():
            level,count=_current_mastery(live,cid)
            if (level is not None or count) and (not args["query"] or args["query"].casefold() in str(meta.get("name","")).casefold()):
                out.append({"combo_id":cid,"name":meta.get("name","Unknown"),"cm_level":level,"loose_count":count})
        return Result(rows=out,message=f"{len(out)} combos with owned mastery or loose CM tokens.")
    add("user-mastery", "Read/filter your owned Combo Mastery levels and loose token counts.", mastery,(query,))
    add("user-heroes", "Read unlocked heroes, training levels and progress.",
        lambda service, args: result(service.client.get_hero_squad()))
    def squad(service,args):
        if args["mode"]=="train":
            if not args["hero"]:raise CommandInputError("Select an unlocked hero ID to train.")
            status=service.client.get_swole_status()
            from .swole_runner import SwoleTrainingRunner
            if SwoleTrainingRunner._active_task(status):raise CommandUnavailable("A Swole hero is already training. Collect completed training before starting another.")
            # The established training runner handles schedules and validation.
            from .account_state import unlocked_heroes
            heroes=unlocked_heroes(status,service.context._catalogs.get("hero",{}))
            if str(args["hero"]) not in {str(r["id"]) for r in heroes}:raise CommandInputError("This hero is not unlocked.")
            return result(service.call("startHeroTraining",{"hero_id":args["hero"]}))
        if args["mode"]=="collect":
            data=service.snapshot(refresh=True);done=0
            for task in rows(data.get("user_tasks")):
                if integer(task.get("task_type"))==2 and 0<integer(task.get("end_time"))<=int(service.clock()):
                    service.call("completeHeroTraining",{"task_index":task["task_index"]});done+=1
            return Result(message=f"Collected {done} completed Swole training tasks.")
        return result(service.client.get_swole_status())
    add("user-squad", "View Swole training, start an unlocked hero, or collect completed training.",squad,
        (Arg("mode",kind="choice",default="status",choices=("status","train","collect")),Arg("hero",kind="int",minimum=1)),True)
    def ad(service,args):
        if args["mode"]=="status":return result(service.call("getBoostLevel"))
        from .ad_ui import attempt_legitimate_ad_ui
        answer=attempt_legitimate_ad_ui(wait_seconds=45,target_level=3,client=service.client,
            should_stop=lambda:getattr(service,"stop",None) is not None and service.stop.is_set())
        if not answer.get("ok"):raise CommandUnavailable(answer.get("message") or "No rewarded ad was verified.")
        return result(answer,message="AT verified the reward boost.")
    add("user-ad", "Watch legitimate rewarded ads or inspect the current boost.",ad,
        (Arg("mode",kind="choice",default="watch",choices=("watch","status")),),True)
    def quests(service,args):
        data=service.snapshot();compact=service.call("getUserAccount")
        data={**data,**compact}
        selected={k:v for k,v in data.items() if any(term in k for term in ("quest","achievement","milestone"))}
        return result(selected,message="Current quest definitions and progress from the account; Swole/research timers are shown separately.")
    add("user-quest", "Read current achievements, daily quests and milestone progress.",quests)
    add("user-rank", "Read the current global SFC leaderboard.", lambda service, args: result(service.call("getRankings", {"ranking_id": "pvpGlobal"})))
    add("user-profile", "Read a player's public game profile by their player ID.",
        lambda service, args: result(service.call("getUserProfile", {"target_user_id": args["player"]})),
        (Arg("player", kind="int", required=True, minimum=1),))
    def mail(service, args):
        data = service.snapshot(refresh=True); more = service.call("getUserAccount")
        seen = {}; mode = args["mode"]
        for row in section(data, "user_mail") + section(more, "user_mail"):
            if row.get("mail_id") is not None:
                seen[str(row["mail_id"])] = row
        if mode == "read":
            return Result(rows=list(seen.values()), message=f"{len(seen)} mail messages.")
        done = 0; protected = 0
        for row in seen.values():
            reward = bool(row.get("reward") or row.get("rewards") or row.get("reward_data"))
            claimed = integer(row.get("status")) == 2
            payload = {"mail_id": row["mail_id"], "mail_type": row.get("type") or row.get("mail_type") or 1}
            if mode == "claim" and reward and not claimed:
                service.call("claimMessageRewards", payload); done += 1
            elif mode == "delete":
                if reward and not claimed:
                    protected += 1; continue
                from .protocol_config import load_profiles
                profile = load_profiles().get("mail_delete")
                if not profile:
                    raise CommandUnavailable("Safe mail deletion requires the existing confirmed Delete Mail profile.")
                response = service.client.call_profile(profile, payload)
                from .shop_rewards import api_rejected
                if api_rejected(response):
                    raise CommandUnavailable("AT rejected safe mail deletion; no retry was sent.")
                done += 1
        return Result(message=f"Mail {mode}: {done} completed; {protected} unclaimed reward messages preserved.")
    add("user-mail", "Read, claim, or delete safe mail. Unclaimed reward mail is always protected.", mail,
        (Arg("mode", kind="choice", default="read", choices=("read", "claim", "delete")),), True)
    def choices(service, args):
        if args["item"]:
            return service.execute("item-use",{"item":args["item"],"quantity":args["quantity"]})
        current = service.snapshot(refresh=True)
        pending = extract_item_choices(current, service.catalog("card"))
        if not pending:
            return Result(message="There is no pending choice reward.")
        count=resolve_choice_rewards(service,current)
        return Result(message=f"Selected {count} rewards with Rogu → Characters → Items / Power Cards priority.")
    add("item-choose", "Resolve pending choices, or open selected consumables and choose Rogu → Characters → Items.", choices,
        (Arg("item",default=""),Arg("quantity",default="1")),action=True,spends_resources=True)
    def claim(service, args):
        payload = {}
        method = {"daily": "claimDailyBonus", "event": "claimEventRewards", "milestone": "claimMilestoneRewards", "mega": "claimMegaQuestMilestoneRewards"}[args["type"]]
        if args["type"] != "daily":
            if not args["event"]:
                raise CommandInputError("Specify the live event or Mega Quest ID.")
            payload["mega_quest_id" if args["type"] == "mega" else "event_id"] = args["event"]
        return result(service.call(method, payload), message="Reward claim completed.", keys=("result", "result_message", "rewards"))
    add("user-claim", "Claim daily login, event rewards, event milestones, or a Mega Quest milestone.", claim,
        (Arg("type", kind="choice", default="daily", choices=("daily", "event", "milestone", "mega")), Arg("event", kind="int", minimum=1)), True)

    def research(service, args):
        data = service.call("getUserAccount")
        return Result(rows=section(data, "user_tasks"), details=json.dumps({k: v for k, v in service.snapshot(refresh=True).items() if "research" in k}, indent=2))
    add("research-info", "Read current research slots, tasks and completion times.", research)
    def collect_research(service, args):
        data = service.call("getUserAccount"); done = 0
        for row in section(data, "user_tasks"):
            kind = str(row.get("type", "")).lower()
            if integer(row.get("task_type")) != 1 and kind not in {"research", "combo_research"}:
                continue
            finish = integer(row.get("end_time") or row.get("finish_time"))
            index = row.get("task_index", row.get("id"))
            if finish > 0 and finish <= int(service.clock()) and index is not None:
                service.call("completeResearch", {"task_index": index}); done += 1
        return Result(message=f"Collected {done} completed research tasks.")
    add("research-collect", "Collect completed combo research only.", collect_research, action=True)
    add("research-start", "Start a specific combo in an available research slot.",
        lambda service, args: result(service.call("researchCombo", {"card1": args["card1"], "card2": args["card2"], "slot_index": args["slot"]})),
        (Arg("card1", kind="int", required=True, minimum=1), Arg("card2", kind="int", required=True, minimum=1), Arg("slot", kind="int", required=True, minimum=0)), True)
    add("research-speedup", "Use a selected research speedup item in a chosen slot.",
        lambda service, args: result(service.call("useResearchSpeedUpItem", {"item_id": args["item"], "slot_index": args["slot"], "quantity": args["number"]})),
        (Arg("item", kind="int", required=True, minimum=1), Arg("slot", kind="int", required=True, minimum=0), number), True, spends_resources=True)

    def event_list(service, args):
        data = service.snapshot(refresh=True)
        values = []
        for key, val in data.items():
            if "event" in key or "challenge" in key:
                values.extend({"source": key, **row} for row in rows(val))
        now = int(service.clock()); mode = args.get("mode", "all")
        def included(row):
            start = integer(row.get("start_time")); end = integer(row.get("end_time"))
            return mode == "all" or (mode == "upcoming" and start > now) or (mode == "current" and (not start or start <= now) and (not end or end >= now))
        return Result(rows=[r for r in values if included(r)], message="Live event definitions; unknown dates are retained in the complete list.")
    for mode in ("all", "current", "upcoming"):
        add(f"events-{mode}", f"List {mode} events exposed by the current account.", lambda service, args, mode=mode: event_list(service, {"mode": mode}))

    def guild_info(service, args):
        gid = args.get("guild")
        if not gid:
            data = service.snapshot(refresh=True)
            gid = (data.get("user_data") or {}).get("faction_id") or (data.get("common_fields") or {}).get("faction_id") or (data.get("common_fields") or {}).get("guild_id")
        if not gid:
            raise CommandUnavailable("No current guild was found. Supply a guild ID.")
        faction=data.get("faction") if not args.get("guild") else None
        if isinstance(faction,dict) and str(faction.get("faction_id"))==str(gid):return {"faction":faction}
        return service.call("getGuildInfo", {"faction_id": gid})
    guild_arg = Arg("guild", kind="int", minimum=1)
    add("guild-info", "Read public guild information by guild ID, or your current guild.", lambda service, args: result(guild_info(service, args)), (guild_arg,))
    def roster(service,args):
        members=guild_members(guild_info(service,args))
        return Result(rows=members,data={"members":members},message=f"{len(members)} current guild members.")
    add("guild-roster", "Read the current guild's roster and public metadata.",roster,(guild_arg,))
    add("guild-activity", "Read the current in-game guild activity feed.", lambda service, args: result(service.call("getGuildFeed")))
    def seen(service,args):
        return Result(rows=[{**r,"seen_seconds_ago":max(0,int(service.clock())-integer(r.get("last_update_time")))} for r in guild_members(guild_info(service,args))])
    add("guild-seen", "Read last-seen information exposed in the current guild roster.",seen,(guild_arg,))
    add("guild-search", "Search guilds by name.", lambda service, args: result(service.call("searchGuildName", {"name": args["name"]})), (Arg("name", required=True),))
    player = Arg("player", kind="int", required=True, minimum=1)
    for name, method in (("promote", "promoteMember"), ("demote", "demoteMember"), ("promoteleader", "assignGuildLeader"), ("invite", "sendGuildInvite"), ("kick", "kickGuildMember")):
        def change(service, args, method=method):
            if method=="sendGuildInvite" and not args["player"]:
                return Result(rows=section(guild_info(service,{}),"invites"),message="Current pending guild invitations.")
            payload = {"target_user_id": args["player"]}
            if method == "kickGuildMember":
                payload["ban"] = "0"
            return result(service.call(method, payload), message="Guild action completed.", keys=("result", "result_message"))
        target=Arg("player",kind="int",minimum=1) if name=="invite" else player
        add("guild-" + name, f"{name.title()} a guild member or invite a player by ID.", change, (target,), True, destructive=name in {"kick", "promoteleader", "demote"})
    for name, method in (("accept", "acceptGuildInvite"), ("reject", "rejectGuildInvite")):
        add("guild-" + name, name.title() + " a guild invitation by guild ID.",
            lambda service, args, method=method: result(service.call(method, {"faction_id": args["guild"]})),
            (Arg("guild", kind="int", required=True, minimum=1),), True)
    add("guild-leave", "Leave the current in-game guild.", lambda service, args: result(service.call("leaveGuild")), action=True, destructive=True)
    def compare(service, args):
        own = guild_info(service, {}); other = guild_info(service, args)
        return result({"your_guild": own, "comparison_guild": other})
    add("guild-compare", "Compare public information for your guild and another guild.", compare, (Arg("guild", kind="int", required=True, minimum=1),))

    add("rumble-match", "Read current Rumble match state and remaining participation.", lambda service, args: result(service.call("getGuildWarStatus")))
    add("rumble-opp", "Read current Rumble opponent and match state.", lambda service, args: result(service.call("getGuildWarStatus")))
    add("siege-match", "Read the current Siege phase, opponent and attack availability.", lambda service, args: result(service.client.get_guild_siege_status()))
    add("siege-islands", "Read live Siege islands and defensive state.", lambda service, args: result(service.client.get_guild_siege_status()))
    add("siege-opp", "Read the live Siege opponent and islands.", lambda service, args: result(service.client.get_guild_siege_status()))
    add("siege-history", "Read recent Siege history.", lambda service, args: result(service.call("getGuildSiegeHistory")))
    add("siege-deck", "Set your Siege defense deck.", lambda service, args: result(service.client.set_siege_defense_deck(args["deck"])), (deck,), True)
    add("siege-move", "Move your defense to a live Siege island slot.", lambda service, args: result(service.client.set_siege_defense_island(args["slot"])), (Arg("slot", kind="int", required=True, minimum=0),), True)

    def store(service):
        return service.call("getStoreData")
    for name, words in (("box", ("box",)), ("deal", ("deal", "offer")), ("mastery", ("mastery",))):
        def shop(service, args, words=words):
            data = store(service)
            if words == ("mastery",):
                from .cm_reviews import collect_live_review_data
                engine, offers, _ = collect_live_review_data(service.client)
                return Result(rows=offers, message=f"{len(offers)} live CM offers.")
            return result(data, message="Current shop data. Select a verified product and quantity in Inventory to purchase.")
        add("buy-" + name, "Read current " + name + " shop offers.", shop)
    def cost(service, args):
        start, increment = args["first_cost"], args["increment"]
        if start is None or increment is None:
            raise CommandInputError("Supply the live first purchase cost and per-purchase increment. Current shop offers can use different price schedules.")
        n = args["number"]
        return Result(rows=[{"purchase": i + 1, "cost": start + increment * i} for i in range(n)],
                      message=f"Total: {n * start + increment * n * (n - 1) // 2:,}. Confirm the live shop's current tier before buying.")
    add("buy-cost", "Calculate standard consecutive CM or Mom's Box costs; offers may override these prices.", cost,
        (Arg("type", kind="choice", default="cm", choices=("cm", "mb")), Arg("number", kind="int", default=1, minimum=1, maximum=100),Arg("first_cost",kind="int",minimum=0),Arg("increment",kind="int",minimum=0)))

    def defaults(service, args):
        value = service.store.read().get("play_defaults", {})
        if args["mode"] == "clear":
            value = {}; service.store.update("play_defaults", value)
        elif args["mode"] == "set":
            try:
                value = json.loads(args["options"])
            except ValueError:
                raise CommandInputError("Defaults must be a JSON object.") from None
            if not isinstance(value, dict):
                raise CommandInputError("Defaults must be a JSON object.")
            service.spec("play").parse_args(value)
            service.store.update("play_defaults", value)
        return result(value, message="Local play defaults.")
    add("play-defaults", "List, set or clear persistent local play defaults.", defaults,
        (Arg("mode", kind="choice", default="list", choices=("list", "set", "clear")), Arg("options", default="{}")), True)
    types = ("game-update", "guild-joins", "guild-leaves", "rumble-match", "rumble-stats", "shop-mastery", "siege-match")
    def subscribe(service, args, remove=False):
        saved = service.store.read().get("subscriptions", {})
        if remove:
            saved.pop(args["type"], None)
        else:
            saved[args["type"]] = {"enabled": True, "interval": args["interval"]}
        service.store.update("subscriptions", saved)
        return result(saved, message="Local subscriptions updated. Commander must remain open to poll and show changes.")
    add("subscribe", "Monitor game changes locally while Commander is open.", subscribe,
        (Arg("type", kind="choice", required=True, choices=types), Arg("interval", kind="int", default=300, minimum=60)), True)
    add("unsubscribe", "Remove a local game notification subscription.", lambda service, args: subscribe(service, args, True),
        (Arg("type", kind="choice", required=True, choices=types),), True)
    def channels(service, args, remove=False):
        saved = service.store.read().get("feeds", [])
        if remove:
            saved = [name for name in saved if name != args["name"]]
        elif args["name"] not in saved:
            saved.append(args["name"])
        service.store.update("feeds", saved)
        return Result(rows=[{"feed": name} for name in saved], message="Named local guild feed views updated.")
    add("channel-create", "Create a named local guild chat/feed view.", channels, (Arg("name", required=True),), True)
    add("channel-remove", "Remove a named local guild feed view.", lambda service, args: channels(service, args, True), (Arg("name", required=True),), True)
    def channel_read(service,args):
        if args["name"] not in service.store.read().get("feeds",[]):raise CommandInputError("Create this local feed first with channel-create.")
        return result(service.call("getGuildFeed"),message=f"Local guild feed: {args['name']}. Changes appear in Automation while Commander is open.")
    add("channel-read","Read a named local guild feed; created feeds poll for changes automatically.",channel_read,(Arg("name",required=True),))
