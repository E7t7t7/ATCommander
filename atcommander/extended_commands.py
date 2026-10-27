"""Purchases, item use, local profiles, deck restore and ranking tools."""
from __future__ import annotations

import json
import math
import re
from collections import Counter

from .local_commands import ArgumentSpec as Arg, CommandSpec as Spec, CommandResult as Result
from .local_commands import CommandInputError, CommandUnavailable
from .utility_commands import rows, result, integer, section
from .shop_rewards import run_single_purchases, extract_item_choices, api_rejected


STONE_COSTS = {str(i):1000 for i in (200002,200003,200004,200009,200010,200011,200012,200013,200014,200015,200016)}
CHOICE_ITEMS = {"200010","200011","200014","200015","200016"}


def register_extended(s):
    def add(name, description, handler, args=(), action=False, **flags):
        s.register(Spec(name, name.split("-")[0].title(), description, tuple(args), action=action,
                        evidence="Current public client / existing Commander calls", **flags), handler)
    deck = Arg("deck", kind="int", required=True, minimum=1)
    quantity = Arg("quantity", default="1", description="A positive number or all")

    def exact(service):
        data=service.snapshot(refresh=True)
        return data if data.get("user_decks") else service.client.get_exact_decks(data)
    def deck_row(data, target):
        row = next((r for r in rows(data.get("user_decks")) if str(r.get("deck_id", r.get("id"))) == str(target)), None)
        if row is None:
            raise CommandInputError("That saved deck slot does not exist.")
        return row
    def set_units(service, target, saved, data):
        indices = [integer(row.get("unit_index", row.get("id")), -1) for row in rows(saved.get("units"))]
        if not 25 <= len(indices) <= 35 or any(i < 0 for i in indices):
            raise CommandInputError("The backup must contain 25–35 valid card instance indices.")
        owned = {integer(row.get("unit_index", row.get("id")), -1) for row in rows(data.get("user_units") or service.snapshot().get("user_units"))}
        if any(index not in owned for index in indices) or len(set(indices)) != len(indices):
            raise CommandUnavailable("Some backed-up cards are missing or duplicated. The deck was not changed.")
        hero = (saved.get("commander") or {}).get("unit_id") or saved.get("commander_id")
        if not hero:
            raise CommandUnavailable("The backup has no verified hero; no deck was changed.")
        # Public SetDeckCards builds '[' + comma-joined integer instances + ']'.
        response = service.call("setDeckUnits", {"deck_id":target,"commander_index":hero,"units":json.dumps(indices,separators=(",", ":"))})
        verified = response if response.get("user_decks") else service.client.get_exact_decks(data)
        actual = deck_row(verified, target)
        got = [integer(row.get("unit_index", row.get("id")), -1) for row in rows(actual.get("units"))]
        if Counter(indices) != Counter(got) or integer((actual.get("commander") or {}).get("unit_id")) != integer(hero):
            raise CommandUnavailable("AT did not confirm the restored deck and hero. No request was retried.")
        return response
    def restore(service, args):
        saved = service.store.read().get("decks", {}).get(args["name"])
        if not isinstance(saved, dict):
            raise CommandInputError("That local deck backup does not exist.")
        from .deck_swap_safety import restore_saved_deck
        verified=restore_saved_deck(service,args["deck"],saved)
        return Result(message="Deck cards and hero restored and verified.",data=verified)
    add("deck-restore", "Restore a local named backup into a saved deck slot; all cards must still be owned.", restore,
        (deck, Arg("name", required=True)), True, destructive=True)
    def swap(service, args):
        from .deck_swap_safety import swap_saved_decks
        verified=swap_saved_decks(service,args["deck"],args["other"])
        return Result(message="Swapped cards and heroes. Original decks remain in local backups.",data=verified)
    add("deck-swap", "Swap two saved decks' cards and heroes, keeping local recovery backups.", swap,
        (deck, Arg("other", kind="int", required=True, minimum=1)), True, destructive=True)

    def number(value, available):
        if str(value).casefold() == "all":
            return available
        if not str(value).isdigit() or int(value) < 1:
            raise CommandInputError("Quantity must be a positive integer or all.")
        requested = int(value)
        if requested > available:
            raise CommandInputError(f"Only {available} uses or purchases are currently affordable.")
        return requested
    def inventory(service, live=None):
        from .session_stats import build_inventory_snapshot
        return list(build_inventory_snapshot(live if live is not None else service.snapshot(refresh=True), item_catalog=service.catalog("item"))["entries"].values())
    def balance(entries, word):
        # The authoritative snapshot labels its scalar balance "Gem".
        lookup="gem" if word.casefold() in {"gem","gems"} else word.casefold()
        values = [integer(r.get("count")) for r in entries if lookup in str(r.get("name", "")).casefold()]
        if len(values) != 1:
            raise CommandUnavailable("The required live resource balance could not be resolved uniquely.")
        return values[0]
    def no_pending_shop_choice(service, live, completed=0):
        if live.get("active_choices"):
            raise CommandUnavailable(f"Purchased {completed} confirmed pulls. Resolve the pending reward choice before another purchase.")
    def settle_shop_choice(service, answer, completed):
        if not answer.get("active_choices"):return
        options=extract_item_choices(answer,service.catalog("card"))
        if not options:
            raise CommandUnavailable(f"Purchased {completed} confirmed pulls, but the reward choice could not be read. No further purchase was sent.")
        expected=len({(choice["group"],choice["choice_type"]) for choice in options})
        from .utility_commands import resolve_choice_rewards
        try:selected=resolve_choice_rewards(service,answer)
        except Exception as error:
            raise CommandUnavailable(f"Purchased {completed} confirmed pulls. The reward choice was not confirmed; no further purchase was sent.") from error
        if selected!=expected:
            raise CommandUnavailable(f"Purchased {completed} confirmed pulls. Reward selection stopped with a pending choice; no further purchase was sent.")
        service.context.invalidate()
    names = {"basic":("1","Nixon",1000),"rarebasic":("65","Nixon",5000),"epicbasic":("66","Nixon",50000),"converter":("82","Giggitywatts",1000),"turdpack":("63","Golden Turd",1000),"herocrate":("31001","Challenge Coin",1000),"sep":("","Challenge Coin",1000)}
    def buy(service, args, kind):
        item, currency, known_cost = names[kind]
        # Use the proven IDs only when the current store confirms the offer.
        store = service.call("getStoreData")
        offered = next((r for r in rows(store.get("store_items")) if str(r.get("id")) == item or (kind=="sep" and "super epic" in str(r.get("name","")).lower())), None)
        if offered is None:
            raise CommandUnavailable("This pack is not in the current live shop.")
        item=str(offered["id"])
        if kind!="turdpack":
            cost=offered.get("cost") if isinstance(offered.get("cost"),dict) else {}
            known_cost=integer(offered.get("sc_cost") or cost.get("num"))
            expected_item={"converter":"4001","herocrate":"21000","sep":"21000"}.get(kind)
            if known_cost<=0 or expected_item and str(cost.get("item"))!=expected_item:
                raise CommandUnavailable("The live pack's cost or required currency could not be verified.")
        if integer(offered.get("num_available"), 1) == 0:
            raise CommandUnavailable("This shop item is currently unavailable.")
        count = number(args["quantity"], balance(inventory(service), currency)//known_cost)
        if kind=="basic" and args.get("budget",0)>0:count=min(count,args["budget"]//known_cost)
        if count == 0:
            return Result(message="No packs are currently affordable.")
        if kind == "turdpack":
            from .settings import load_settings, save_settings
            try:
                learned = load_settings().get("golden_shop_interval_seconds", .1)
                initial = float(learned)
                if isinstance(learned, bool) or not math.isfinite(initial):
                    initial = .1
                initial = max(.1, min(4.25, initial))
            except (TypeError, ValueError):
                initial = .1
            def remember_pacing(state):
                if state.status not in {"rate limited", "complete", "stopped"}:
                    return
                # The GUI and command path share the learned interval. A local
                # save failure must not turn a confirmed purchase into a retry.
                try:
                    stored = load_settings()
                    stored["golden_shop_interval_seconds"] = state.interval
                    save_settings(stored)
                except OSError:
                    pass
                service.emit({"kind": "shop_pacing", "interval": state.interval})
            call_one = lambda: service.client.call("buyStoreItem", {"cost_type":"2","item_id":item,"quantity":"1"})
            stop = getattr(service,"stop",None)
            progress = run_single_purchases(count, call_one, initial_interval=initial, min_interval=.1,
                                           on_progress=remember_pacing,
                                           should_stop=stop.is_set if stop else None,
                                           sleep=stop.wait if stop else __import__('time').sleep)
            return Result(message=f"Purchased {progress.done} real Golden Packs.")
        # Reuse native stack purchasing. Explicit quantity rejection is surfaced
        # without changing the established Inventory bulk-buy implementation.
        answer = service.client.buy_store_item(item, known_cost, count)
        if api_rejected(answer):
            raise CommandUnavailable("AT rejected the pack stack; no retry was sent. Inventory retains the established paced bulk fallback.")
        if not answer:
            raise CommandUnavailable("The purchase outcome is unknown. No request was retried.")
        return Result(message=f"Purchased {count} {kind} pack(s).", details=json.dumps(answer, indent=2))
    for kind in names:
        add("buy-" + kind, "Purchase " + kind + " packs with a fixed quantity or all affordable packs.",
            lambda service, args, kind=kind: buy(service, args, kind),
            (quantity,Arg("budget",kind="int",default=0,minimum=0,description="Optional maximum Nixons; zero uses the existing quantity limit.")) if kind=="basic" else (quantity,), True, spends_resources=True)
    def buy_max(service,args):
        data=service.snapshot(refresh=True);cap=integer((data.get("user_data")or{}).get("caps",{}).get("max_cards"),-1)
        if cap<0:raise CommandUnavailable("The current card inventory capacity could not be verified.")
        space=max(0,cap-len(rows(data.get("user_units"))))
        count=min(space,balance(inventory(service),"Nixon")//1000)
        if not count:return Result(message="No affordable card inventory space remains.")
        return buy(service,{"quantity":str(count)},"basic")
    add("buy-max","Buy Basic Packs up to the live card inventory cap and available Nixons.",buy_max,action=True,spends_resources=True)
    def mom(service,args):
        completed=0;spent=0
        for _ in range(args["quantity"]):
            if getattr(service,"stop",None) is not None and service.stop.is_set():break
            data=service.snapshot(refresh=True)
            no_pending_shop_choice(service,data,completed)
            state=data.get("slot_machine_data") or {}
            if "cost" not in state:raise CommandUnavailable("Mom's Box current price is unavailable.")
            cost=integer(state["cost"],-1)
            if cost<=0:raise CommandUnavailable("The current Mom's Box pull is an ad/free offer. Use buy-momfree to complete its legitimate ad flow.")
            if spent+cost>args["max_gems"]:break
            if cost>balance(inventory(service,data),"Gems"):break
            try:answer=service.call("slotMachinePull")
            except Exception:
                raise CommandUnavailable(f"Purchased {completed} confirmed Mom's Box pulls; the latest outcome was not confirmed and was not retried.") from None
            if not answer:raise CommandUnavailable("The Mom's Box outcome is unknown; no pull was retried.")
            completed+=1;spent+=cost;service.context.invalidate()
            settle_shop_choice(service,answer,completed)
        return Result(message=f"Purchased {completed} Mom's Box pulls; spent {spent} gems within the selected budget.")
    add("buy-mombox","Buy Mom's Box pulls using the fresh price before every pull and an explicit gem budget.",mom,
        (Arg("quantity",kind="int",default=1,minimum=1,maximum=100),Arg("max_gems",kind="int",required=True,minimum=0)),True,spends_resources=True)
    def rewarded(service,args,kind):
        if kind=="mom":
            from .mom_free_protocol import FIRST_FREE_MOM_MESSAGE,first_free_mom_available,first_free_mom_fields
            live=service.snapshot(refresh=True)
            state=live.get("slot_machine_data") or {}
            if first_free_mom_available(state):
                no_pending_shop_choice(service,live,0)
                before_gems=balance(inventory(service,live),"Gems")
                store=service.call("getStoreData")
                if not any(str(row.get("id"))=="71" and "mystery" in str(row.get("name","")).casefold() and integer(row.get("num_available"),1)>0 for row in rows(store.get("store_items"))):
                    raise CommandUnavailable("The current shop did not confirm Mom's Mystery Box. No free/ad pull was sent.")
                try:answer=service.call(FIRST_FREE_MOM_MESSAGE,first_free_mom_fields(service.client.credentials.user_id))
                except Exception as exc:
                    from .diagnostic_privacy import safe_text
                    raise CommandUnavailable("The first free Mom's Box pull was not confirmed: "+safe_text(str(exc),service.context.private)+" No pull was retried.") from None
                service.context.invalidate()
                after=service.snapshot(refresh=True)
                current=after.get("slot_machine_data") or {}
                if integer(current.get("pulls"),-1)!=1 or balance(inventory(service,after),"Gems")!=before_gems:
                    raise CommandUnavailable("AT did not confirm the first free Mom's Box pull without gem spending. No pull was retried.")
                settle_shop_choice(service,answer,1)
                return Result(message="Claimed the native first free Mom's Box pull; spent 0 gems.",data={"pulls":1,"gems_spent":0,"first_free":True})
        if kind=='research':
            from .research_ad_ui import play_research_ad
            answer=play_research_ad(service.client,
                log=lambda message:service.emit({'kind':'log','message':message}),
                should_stop=lambda:getattr(service,'stop',None) is not None and service.stop.is_set())
        else:
            from .ad_ui import attempt_legitimate_rewarded_flow
            answer=attempt_legitimate_rewarded_flow(service.client,kind)
        if not answer.get("ok"):raise CommandUnavailable(answer.get("message") or "AT did not verify the legitimate rewarded ad.")
        return result(answer,message="AT verified the legitimate rewarded-ad benefit.")
    add("buy-momfree","Play the legitimately rewarded Mom's Box ad and verify the credited pull.",lambda service,args:rewarded(service,args,"mom"),action=True)
    add("research-ad","Play a legitimate research ad and verify its task or speedup benefit.",lambda service,args:rewarded(service,args,"research"),action=True)
    def shop_purchase(service,args):
        spent=0;done=0;currencies=set()
        for index in range(args["quantity"]):
            if getattr(service,"stop",None) is not None and service.stop.is_set():break
            live=service.snapshot(refresh=True)
            no_pending_shop_choice(service,live,done)
            data=service.call("getStoreData")
            offer=next((r for r in rows(data.get("store_items")) if str(r.get("id"))==str(args["store_id"])),None)
            if not offer or integer(offer.get("num_available"),1)==0:break
            hc=integer(offer.get("hc_cost"));sc=integer(offer.get("sc_cost"));item=offer.get("cost")or{}
            expected_item=args.get("expected_currency_item")
            if expected_item is not None and (hc>0 or sc>0 or not isinstance(item,dict)
                    or str(item.get("item"))!=str(expected_item) or integer(item.get("num"))<=0):
                raise CommandUnavailable("The offer no longer uses the required item currency. No purchase was sent.")
            if hc>0:price=hc;currency="Gems";cost_type="1"
            elif sc>0:price=sc;currency="Nixon";cost_type="2"
            elif isinstance(item,dict) and integer(item.get("num"))>0:
                price=integer(item["num"]);iid=str(item.get("item"));meta=service.catalog("item").get(iid,{})
                from .session_stats import FALLBACK_ITEM_NAMES
                currency=meta.get("name")or FALLBACK_ITEM_NAMES.get(iid);cost_type="2"
                if not currency:raise CommandUnavailable("This offer's required currency is unknown.")
            else:raise CommandUnavailable("This offer requires an ad, external payment or an unrecognized currency. No purchase was sent.")
            currencies.add(currency)
            if len(currencies)>1:raise CommandUnavailable("The offer's currency changed. The sequence stopped before another purchase.")
            if spent+price>args["budget"] or balance(inventory(service,live),currency)<price:break
            try:response=service.call("buyStoreItem",{"item_id":args["store_id"],"expected_cost":price,"cost_type":cost_type,"quantity":"1"})
            except Exception:
                raise CommandUnavailable(f"Purchased {done} confirmed offers; the latest outcome was not confirmed and was not retried.") from None
            if not response:raise CommandUnavailable("Purchase outcome is unknown; no request was retried.")
            spent+=price;done+=1;service.context.invalidate()
            settle_shop_choice(service,response,done)
            if index+1<args["quantity"]:
                import time
                time.sleep(4.1)
        return Result(message=f"Purchased {done} offer(s); spent {spent:,} {next(iter(currencies),'currency')} within budget.")
    add("shop-purchase","Purchase a live box, deal or CM offer by store ID. Re-read its price before each real single purchase.",shop_purchase,
        (Arg("store_id",kind="int",required=True,minimum=1),Arg("quantity",kind="int",default=1,minimum=1,maximum=1000),Arg("budget",kind="int",required=True,minimum=0),
         Arg("expected_currency_item",kind="int",minimum=1,description="Optional required currency item ID; reject gem, Nixon or other-currency offers before buying.")),True,spends_resources=True)

    aliases = {"legstone":"200003","powerstone":"200004","gepicstone":"200009","quadepicstone":"200012","charstone":"200013","superlegstone":"200014","sep":"200016","adcrate":"30001","lootcrate":"30002","arena5":"1023","arena10":"1024"}
    def use_item(service, args):
        iid = aliases.get(args["item"].casefold(), args["item"])
        live = service.snapshot(refresh=True)
        if args["item"].casefold() == "legpack":
            from .reward_options import vip_active
            iid = "30002" if vip_active(service,live) else "30001"
        if live.get("active_choices"):
            raise CommandUnavailable("Resolve the pending reward choice before opening another item.")
        entries = inventory(service,live)
        entry = next((r for r in entries if str(r.get("key")) == iid), None)
        if entry is None:
            matches = [r for r in entries if str(r.get("name", "")).casefold() == args["item"].casefold()]
            refill=re.fullmatch(r"(adventure|arena)(5|10|20|50)",args["item"].casefold())
            if refill:
                mode,amount=refill.groups()
                matches=[r for r in entries if "refill" in str(r.get("name","")).casefold()
                         and ("arena" in str(r.get("name","")).casefold())==(mode=="arena")
                         and re.search(r"(?<!\d)"+amount+r"(?!\d)",str(r.get("name","")))]
            if len(matches) == 1:
                entry = matches[0]; iid = str(entry["key"])
        if entry is None or entry.get("category") in {"Currency", "Core"} or iid in {"200005","200008","21000","4001"}:
            raise CommandInputError("Choose an owned consumable by its exact name, supported alias or item ID.")
        count = number(args["quantity"], integer(entry.get("count"))//STONE_COSTS.get(iid,1))
        if count == 0:
            return Result(message="No item uses are currently affordable.")
        meta=service.catalog("item").get(str(iid),{})
        if iid == "30001" or integer(meta.get("ad_locked")):
            raise CommandUnavailable("Ad Crate must use the legitimate rewarded-ad flow in Tools / Ads.")
        if iid == "30002":
            from .reward_options import vip_active
            if not vip_active(service,live):
                raise CommandUnavailable("Loot Crates require an active VIP membership. No crate was opened.")
            if str(meta.get("id"))!="30002" or str(meta.get("name","")).casefold()!="loot crate" or integer(meta.get("usable"))!=1:
                raise CommandUnavailable("The current Loot Crate definition was not verified. No crate was opened.")
        if count>1000:raise CommandInputError("Use at most 1,000 item pulls per run.")
        if iid in CHOICE_ITEMS:
            from .utility_commands import resolve_choice_rewards
            done=selected=0
            for index in range(count):
                if getattr(service,"stop",None) is not None and service.stop.is_set():break
                if index:
                    service.context.invalidate();fresh=service.snapshot(refresh=True)
                    if extract_item_choices(fresh,service.catalog("card")):raise CommandUnavailable(f"Opened {done} items. A choice is still pending, so no further item was opened.")
                    remaining=next((integer(r.get("count")) for r in inventory(service,fresh) if str(r.get("key"))==iid),0)
                    if remaining<STONE_COSTS.get(iid,1):break
                answer=service.client.use_item(iid,1)
                if not answer or api_rejected(answer):raise CommandUnavailable(f"Opened {done} confirmed items. The latest use was not confirmed and was not retried.")
                done+=1
                if not extract_item_choices(answer,service.catalog("card")):raise CommandUnavailable(f"Opened {done} items, but AT did not expose the choice. No extra item was opened.")
                try:selected+=resolve_choice_rewards(service,answer)
                except Exception as exc:raise CommandUnavailable(f"Opened {done} items. The latest choice was not confirmed; no further item was opened.") from exc
            return Result(message=f"Opened {done} of {count} items; resolved {selected} choice groups with Rogu → Characters → Items / Power Cards priority.")
        answer = service.client.use_item(iid, count)
        if not answer or api_rejected(answer):
            raise CommandUnavailable("Item use was not confirmed. No request was retried.")
        if extract_item_choices(answer, service.catalog("card")):
            service.context.invalidate(); service.execute("item-choose")
        return Result(message=f"Used {count} item pull(s).", details=json.dumps(answer, indent=2))
    add("item-use", "Use owned consumables; stone pulls consume 1,000 stones each. Choice items open one at a time.", use_item,
        (Arg("item", required=True), quantity), True, spends_resources=True)

    def rankings(service, ranking, index):
        return result(service.call("getRankings", {"ranking_id":ranking,"ranking_index":index}))
    # Current public wrappers specify these ranking IDs and fixed indexes.
    add("rumble-results", "Read Rumble match history exposed by the server.", lambda service, args: rankings(service,"event_guild_war",2))
    add("rumble-stats", "Read Rumble member/match rankings.", lambda service, args: rankings(service,"event_guild_war",3))
    add("rumble-rank", "Read guild rankings for a selected live Rumble event ID.", lambda service, args: rankings(service,"event_guild",args["event"]), (Arg("event", kind="int", required=True, minimum=1),))
    add("siege-rank", "Read the current Siege rankings.", lambda service, args: rankings(service,"event_guild_siege",2))
    def defense(service, args):
        data = service.client.get_guild_siege_status()
        islands = section(data, "locations")
        if not args["expected"]:
            return Result(rows=islands, message="Supply expected JSON to compare local island assignments.")
        try:
            expected = json.loads(args["expected"])
        except ValueError:
            raise CommandInputError("Expected assignments must be a JSON object mapping player IDs to island slots.") from None
        if not isinstance(expected, dict):
            raise CommandInputError("Expected assignments must be a JSON object.")
        actual = {}
        for island in islands:
            for member in rows(island.get("users")):
                pid = member.get("user_id", member.get("id"))
                actual[str(pid)] = str(island.get("slot_id", island.get("id")))
        return Result(rows=[{"player":pid,"expected":slot,"actual":actual.get(pid,"Missing"),"matches":str(slot)==actual.get(pid)} for pid,slot in expected.items()])
    add("siege-defense", "Compare live Siege defense assignments with a local JSON plan.", defense, (Arg("expected", default=""),))

    def account(service, args):
        from .account_registry import AccountRegistry
        registry = AccountRegistry()
        if args["alias"]:
            registry.activate(args["alias"])
            return Result(message="Active local account changed. Refresh Account to load its selectors.")
        return Result(rows=[{"alias":alias,**meta} for alias,meta in registry.list().items()])
    add("user-account", "List or activate a saved local account alias.", account, (Arg("alias", default=""),), True, destructive=True)
    def register(service, args, guild=False):
        from .account_registry import AccountRegistry
        from .api import ATClient
        from .credentials import Credentials
        credentials = Credentials(str(args["user_id"]), args["login_hash"])
        live = ATClient(credentials).init()
        if api_rejected(live) or not live.get("user_data"):
            raise CommandUnavailable("The account could not be verified; no profile was saved.")
        AccountRegistry().save(args["alias"], credentials, args.get("role", "player"))
        return Result(message="Verified local account saved in Windows Credential Manager.")
    registration = (Arg("alias", required=True), Arg("user_id", kind="int", required=True, minimum=1), Arg("login_hash", required=True))
    add("register", "Verify and save a local account alias in Windows Credential Manager.", register, registration, True, destructive=True)
    add("register-guild", "Save a verified local guild officer profile.", lambda service,args: register(service,args,True),
        registration + (Arg("role", kind="choice", default="officer", choices=("officer","leader")),), True, destructive=True)
    def unregister(service,args):
        from .account_registry import AccountRegistry
        AccountRegistry().remove(args["alias"])
        return Result(message="Removed local account profile(s); in-game accounts are unchanged.")
    add("unregister", "Remove a local account alias, or all local aliases.", unregister, (Arg("alias", required=True),), True, destructive=True)
