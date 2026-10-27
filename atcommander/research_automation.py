"""Persistent, bounded research of real recipe pairs from owned cards.

Only the established researchCombo/useResearchSpeedUpItem/completeResearch
requests are sent. Every mutation is recorded before transmission and verified
from fresh account state. An unresolved request is never replayed on resume.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import threading

from .command_store import CommandStore
from .local_commands import CommandInputError, CommandResult, CommandUnavailable
from .shop_rewards import api_rejected
from .api import ATNetworkError


class ResearchReadUnavailable(CommandUnavailable):
    """Only an exhausted safe read permits a delayed Research read retry."""
    network_retryable=True
    def __init__(self,label,*,queue_replaced=False):
        self.queue_replaced=bool(queue_replaced)
        super().__init__(f"Research connection unavailable while reading {label}. Saved progress and pending requests are preserved.")


def number(value):
    if isinstance(value, bool):
        return None
    try:
        text=str(value)
        return int(text) if text.lstrip("-").isdigit() else None
    except (TypeError, ValueError):
        return None


def rows(value):
    if isinstance(value, dict):
        return [{"id":str(key),**row} if isinstance(row,dict) else {"id":str(key),"count":row}
                for key,row in value.items()]
    return [row for row in value if isinstance(row,dict)] if isinstance(value,list) else []


def pair_key(card1,card2):
    values=(number(card1),number(card2))
    if any(value is None or value<=0 for value in values):
        return None
    return "~".join(map(str,sorted(values)))


@dataclass(frozen=True)
class ResearchOptions:
    tiers: tuple[int,...] = (3,4,5)
    tier_match: str = "either"
    prioritize_rogu: bool = True
    priority: str = "rarity"
    speedups: bool = False
    max_speedup_hours: int = 9000
    use_all_speedups: bool = False
    reserve_hours: int = 0
    ads: bool = True
    max_pairs: int = 5000
    poll_seconds: int = 8

    def __post_init__(self):
        if not isinstance(self.tiers,(list,tuple)) or not self.tiers or any(isinstance(tier,bool) or not isinstance(tier,int) or tier not in (1,2,3,4,5) for tier in self.tiers):
            raise CommandInputError("Choose one or more input rarity tiers from 1–5.")
        tiers=tuple(sorted(set(self.tiers)))
        object.__setattr__(self,"tiers",tiers)
        if self.tier_match not in {"either","both"} or self.priority not in {"rarity","power"}:
            raise CommandInputError("Research matching must be either/both; priority must be rarity/power.")
        for name in ("prioritize_rogu","speedups","use_all_speedups","ads"):
            if not isinstance(getattr(self,name),bool):raise CommandInputError(f"{name} must be a boolean.")
        for name,maximum in (("max_speedup_hours",1000000),("reserve_hours",1000000),("max_pairs",100000),("poll_seconds",60)):
            value=getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,int) or value<0 or value>maximum:
                raise CommandInputError(f"{name} is outside its allowed range.")
        if not self.max_pairs or self.poll_seconds<8:
            raise CommandInputError("Research needs a positive pair limit and a polling interval of at least eight seconds.")

    @classmethod
    def from_dict(cls,value=None):
        if value is None:return cls()
        if isinstance(value,cls):return value
        if not isinstance(value,dict):raise CommandInputError("Research options must be a JSON object.")
        value=dict(value)
        if "max_hours" in value:
            if "max_speedup_hours" in value and value["max_hours"]!=value["max_speedup_hours"]:
                raise CommandInputError("The research speedup budget aliases disagree.")
            value["max_speedup_hours"]=value.pop("max_hours")
        if set(value)-set(cls.__dataclass_fields__):raise CommandInputError("Research options contain an unsupported setting.")
        try:return cls(**value)
        except (TypeError,ValueError) as exc:
            if isinstance(exc,CommandInputError):raise
            raise CommandInputError("The research options could not be read.") from None

    def serialized(self):
        result=asdict(self);result["tiers"]=list(self.tiers);return result

    @property
    def max_hours(self):return self.max_speedup_hours


def researched_pairs(snapshot):
    known=snapshot.get("user_combos_compressed")
    if not isinstance(known,dict):raise CommandUnavailable("AT did not expose current researched recipe pairs.")
    return {key for raw in known for parts in [str(raw).split("~")] if len(parts)==2
            for key in [pair_key(*parts)] if key}


def research_tasks(snapshot):
    if not isinstance(snapshot.get("user_tasks"),(dict,list)):
        raise CommandUnavailable("AT did not expose current research tasks; slot occupancy is unknown.")
    result={}
    for row in rows(snapshot["user_tasks"]):
        kind=number(row.get("task_type"))
        if kind is None:raise CommandUnavailable("A current task's type is unknown; no research slot was assumed free.")
        if kind!=1:continue
        slot=number(row.get("slot_index"));end=number(row.get("end_time"));index=number(row.get("task_index",row.get("id")))
        key=pair_key(row.get("task_id_1"),row.get("task_id_2"))
        if slot is None or slot<0 or end is None or end<0 or not index or not key or slot in result:
            raise CommandUnavailable("Current research task metadata is incomplete or conflicting.")
        result[slot]={**row,"slot_index":slot,"end_time":end,"task_index":index,"pair":key}
    return result


def build_plan(snapshot,catalog,recipes,options=None,*,should_stop=None):
    def checkpoint():
        if should_stop is not None and should_stop():raise _Stopped()
    checkpoint()
    options=ResearchOptions.from_dict(options)
    if not isinstance(snapshot.get("user_units"),(dict,list)):
        raise CommandUnavailable("AT did not expose the owned card pool.")
    known=researched_pairs(snapshot);tasks=research_tasks(snapshot)
    active={task["pair"] for task in tasks.values()}
    owned={str(row.get("unit_id",row.get("card_id",""))) for row in rows(snapshot["user_units"])}
    owned.discard("")
    for index,cid in enumerate(owned):
        if index%128==0:checkpoint()
        if not isinstance(catalog.get(cid),dict) or number(catalog[cid].get("rarity")) not in (1,2,3,4,5):
            raise CommandUnavailable("Some owned cards have unknown current rarity metadata. The queue was not guessed.")
    if not isinstance(recipes,dict) or owned and not recipes:raise CommandUnavailable("AT did not expose its current combo recipe map.")
    planned={}
    for index,(raw,value) in enumerate(recipes.items()):
        if index%128==0:checkpoint()
        parts=str(raw).split("~")
        if len(parts)!=2 or not all(part in owned for part in parts):continue
        key=pair_key(*parts)
        if not key or key in known:continue
        cid=str(value).split("|",1)[0]
        if not cid.isdigit() or cid not in catalog:
            raise CommandUnavailable("An owned recipe's combo definition is missing; it was not invented.")
        first,second=(catalog[part] for part in parts)
        rarities=(number(first["rarity"]),number(second["rarity"]))
        selected=[rarity in options.tiers for rarity in rarities]
        if not (all(selected) if options.tier_match=="both" else any(selected)):continue
        previous=planned.get(key)
        if previous and previous["combo_id"]!=cid:
            raise CommandUnavailable("The public recipe map contains conflicting results for an owned input pair.")
        combo=catalog[cid];power=None
        try:
            candidate=float(combo.get("power"))
            if math.isfinite(candidate) and candidate>0:power=candidate
        except (TypeError,ValueError):pass
        planned[key]={"pair":key,"card1":int(parts[0]),"card2":int(parts[1]),"card1_name":first.get("name","Unknown"),
                      "card2_name":second.get("name","Unknown"),"combo_id":cid,"name":combo.get("name","Unknown"),
                      "rarity1":rarities[0],"rarity2":rarities[1],"model_power":power,
                      "rogu":any(str(card.get("name","")).casefold()=="rogu" for card in (first,second)),
                      "status":"running" if key in active else "pending"}
    ordered=0
    def order(row):
        nonlocal ordered
        if ordered%128==0:checkpoint()
        ordered+=1
        rogu=-int(row["rogu"]) if options.prioritize_rogu else 0
        power=-(row["model_power"] or 0) if options.priority=="power" else 0
        return (rogu,power,-max(row["rarity1"],row["rarity2"]),-min(row["rarity1"],row["rarity2"]),row["name"],row["pair"])
    result=sorted(planned.values(),key=order);checkpoint();return result


def speedup_inventory(snapshot,item_catalog):
    """The public research_speed_up_time field is hours, including 1/8/24."""
    containers=[snapshot[key] for key in ("user_items","item_inventory","user_inventory","inventory_items","items_owned")
                if key in snapshot and isinstance(snapshot[key],(dict,list))]
    if not containers:raise CommandUnavailable("The current speedup inventory is unknown.")
    found={}
    for container in containers:
        for row in rows(container):
            iid=str(row.get("item_id",row.get("id",row.get("unit_id",""))))
            meta=item_catalog.get(iid,{})
            speed=meta.get("research_speed_up_time") if isinstance(meta,dict) else None
            relevant=speed is not None or iid in {"1031","1032","1033"} or "research speedup" in str(meta.get("name","")).casefold()
            if not relevant:continue
            try:
                hours=Decimal(str(speed));seconds=hours*3600
                duration=int(seconds) if seconds==seconds.to_integral_value() else 0
            except (InvalidOperation,ValueError,TypeError,OverflowError):duration=0
            quantity=next((number(row[key]) for key in ("quantity","qty","count","num","number","amount","owned") if key in row),None)
            if duration<=0 or quantity is None or quantity<0:
                raise CommandUnavailable("A research speedup's exact duration or current quantity is unknown.")
            if iid in found and found[iid]["quantity"]!=quantity:
                raise CommandUnavailable("Current speedup inventory mirrors disagree.")
            found[iid]={"item_id":iid,"duration_seconds":duration,"hours":float(hours),"quantity":quantity,"name":meta.get("name",iid)}
    return list(found.values())


def speedup_plan(remaining_seconds,items,budget_seconds):
    """Bounded exact knapsack: least applied time, then fewest items."""
    if remaining_seconds<=0 or budget_seconds<=0:return []
    usable=[item for item in items if item["quantity"]>0 and item["duration_seconds"]<=budget_seconds]
    if not usable:return []
    cap=min(budget_seconds,remaining_seconds+max(item["duration_seconds"] for item in usable))
    possible={0:{}}
    for item in usable:
        duration=item["duration_seconds"];count=min(item["quantity"],cap//duration);chunk=1
        while count:
            quantity=min(chunk,count);count-=quantity;chunk*=2
            for amount,selection in list(possible.items()):
                applied=amount+duration*quantity
                if applied>cap:continue
                candidate={**selection,item["item_id"]:selection.get(item["item_id"],0)+quantity}
                previous=possible.get(applied)
                if previous is None or sum(candidate.values())<sum(previous.values()):possible[applied]=candidate
            if len(possible)>20000:raise CommandUnavailable("Speedup metadata is too granular to choose a bounded exact plan safely.")
    choices=[amount for amount in possible if amount>=remaining_seconds]
    if not choices:return []
    selected=possible[min(choices)]
    return [{**item,"quantity":selected[item["item_id"]]} for item in sorted(usable,key=lambda row:-row["duration_seconds"])
            if selected.get(item["item_id"])]


def ad_rows(snapshot):
    return {number(row.get("slot_index")):row for row in rows(snapshot.get("research_ad_speed_ups"))
            if number(row.get("slot_index")) is not None}


def slot_state(snapshot,unlocked_ads=()):
    raw=snapshot.get("research_times_slot")
    if not isinstance(raw,(dict,list)) or not raw:raise CommandUnavailable("AT did not expose authoritative research slots.")
    available=[];blocked=[];ads=ad_rows(snapshot)
    regular_count=number((snapshot.get("user_data") or {}).get("research_slots"))
    if regular_count is not None and regular_count<0:
        raise CommandUnavailable("The current unlocked chamber count is invalid.")
    indices=[];explicit_locked=set();explicit_no_ad=set()
    for row in rows(raw):
        slot=number(row.get("slot_index"))
        if slot is None or slot<0 or slot in indices or not isinstance(row.get("research_times"),dict):
            raise CommandUnavailable("Research slot metadata is incomplete or conflicting.")
        indices.append(slot)
        if row.get("locked") is True or row.get("unlocked") is False:explicit_locked.add(slot)
        if row.get("ad_required") is False:explicit_no_ad.add(slot)
    regular=sorted(slot for slot in indices if slot not in ads or slot in explicit_no_ad)
    if regular_count is not None:regular=regular[:regular_count]
    # Read only the current explicit status; an absent field grants no bypass.
    vip=str((snapshot.get("common_fields") or {}).get("is_vip_active", "")).casefold() in {"1","true"}
    for slot in sorted(indices):
        if slot in explicit_locked:
            blocked.append({"slot_index":slot,"reason":"Research chamber is explicitly locked"})
        elif slot in ads and slot not in explicit_no_ad and slot not in unlocked_ads and vip is not True:
            blocked.append({"slot_index":slot,"reason":"Requires verified legitimate research ad unlock"})
        elif slot in regular or slot in unlocked_ads or slot in ads and vip is True:available.append(slot)
        else:blocked.append({"slot_index":slot,"reason":"Research chamber is locked"})
    occupied=research_tasks(snapshot)
    return [slot for slot in available if slot not in occupied],blocked


class _Stopped(Exception):pass
class _Yield(Exception):pass
class _ActionReconciled(Exception):pass


class ResearchAutomation:
    def __init__(self,service,state_path,*,boost_callback=None,ad_callback=None,emit=None,waiter=None):
        self.service=service;self.store=CommandStore(Path(state_path));self.boost_callback=boost_callback;self.ad_callback=ad_callback
        self.emit=emit or service.emit;self.stop=getattr(service,"stop",threading.Event());self.waiter=waiter or self.stop.wait
        uid=str(getattr(getattr(service.client,"credentials",None),"user_id",""))
        if not uid:raise CommandUnavailable("The research queue needs a verified account identity.")
        self.binding=hashlib.sha256(uid.encode("utf-8")).hexdigest();self.state={};self.live=None;self._boost_at=None
        self._server_time=None;self._server_time_at=None
        self.catalogs={};self._action_count=0;self._action_limit=None;self._queue_by_pair={}
        self._last_reconciliation=None
        self._queue_replaced=False

    def _read(self,label,loader):
        """Retry only this explicit read, with cancellable bounded backoff."""
        delays=(5,15)
        for attempt in range(len(delays)+1):
            self._check()
            try:
                return loader()
            except ATNetworkError as error:
                if not error.retryable_read:raise
                if attempt==len(delays):
                    raise ResearchReadUnavailable(label,queue_replaced=self._queue_replaced) from None
                delay=delays[attempt]
                self.emit({"message":f"Research connection interrupted while reading {label}; retrying in {delay} seconds ({attempt+1}/{len(delays)})."})
                if self.waiter(delay):raise _Stopped()
                self._check()

    def _catalog(self,name):
        if name not in self.catalogs:
            self._check();self.catalogs[name]=self._read(f"the {name} catalog",lambda:self.service.catalog(name))
        return self.catalogs[name]

    def _check(self):
        if self.stop.is_set():raise _Stopped()

    def _fresh(self,initial=False):
        self._check()
        if initial or self.live is None:
            self.service.context.invalidate();value=self._read("the account",lambda:self.service.snapshot(refresh=True))
            observed_time=number(value.get("time")) if isinstance(value,dict) else None
        else:
            compact=self._read("research status",lambda:self.service.call("getUserAccount"))
            required={"user_tasks","user_combos_compressed","user_items"}
            # Omitted mirrors/counters must not survive as stale confirmation
            # evidence beside the compact response's fresh tasks and items.
            required.update(key for key in ("item_inventory","user_inventory","inventory_items","items_owned") if key in self.live)
            if (self.state.get("pending_action") or {}).get("kind")=="ad":
                required.add("research_ad_speed_ups")
            if not isinstance(compact,dict) or api_rejected(compact):
                raise CommandUnavailable("Current research state could not be read.")
            if required<=compact.keys():
                value={**self.live,**compact}
                observed_time=number(compact.get("time"))
                for key in ("user_data","common_fields"):
                    value[key]={**(self.live.get(key)or{}),**(compact.get(key)or{})}
            else:
                self._check();self.service.context.invalidate();value=self._read("the full account",lambda:self.service.snapshot(refresh=True))
                observed_time=number(value.get("time")) if isinstance(value,dict) else None
        if not isinstance(value,dict) or not value or api_rejected(value):raise CommandUnavailable("Current research state could not be read.")
        if observed_time is not None and observed_time>0:
            self._server_time=observed_time;self._server_time_at=self.service.clock()
        self.live=value;return value

    def _now(self):
        if self._server_time is not None:
            return self._server_time+int(max(0,self.service.clock()-self._server_time_at))
        return int(self.service.clock())

    def _save(self):self.store.update("research",self.state)

    def _load(self):
        # CommandStore's forgiving reads are suitable for preferences, but an
        # unreadable mutation ledger must never silently become a new queue.
        if not self.store.path.exists():return {}
        try:
            saved=json.loads(self.store.path.read_text(encoding="utf-8"))["research"]
            if not isinstance(saved,dict) or saved.get("schema")!=1 or not isinstance(saved.get("queue"),list):raise ValueError()
            if any(number(saved.get(key)) is None or saved[key]<0 for key in ("started","completed","spent_seconds","confirmed_speedup_seconds")):raise ValueError()
            if any(not isinstance(row,dict) or row.get("pair")!=pair_key(row.get("card1"),row.get("card2")) or row.get("status") not in {"pending","running","completed"} for row in saved["queue"]):raise ValueError()
            if len({row["pair"] for row in saved["queue"]})!=len(saved["queue"]):raise ValueError()
            if not isinstance(saved.get("options"),dict):raise ValueError()
            saved["options"]=ResearchOptions.from_dict(saved["options"]).serialized()
            action=saved["pending_action"]
            if action is not None:
                if not isinstance(action,dict) or action.get("kind") not in {"start","collect","speedup","ad"}:raise ValueError()
                parts=str(action.get("pair","")).split("~")
                if len(parts)!=2 or action["pair"]!=pair_key(*parts) or number(action.get("slot")) is None or action["slot"]<0:raise ValueError()
                if action["pair"] not in {row["pair"] for row in saved["queue"]}:raise ValueError()
                if action["kind"]=="speedup":
                    for field,minimum in (("quantity",1),("before_count",0),("before_end",1),("seconds",1)):
                        if number(action.get(field)) is None or action[field]<minimum:raise ValueError()
                    if not isinstance(action.get("item_id"),str) or not action["item_id"].isdigit():raise ValueError()
                    if action.get("task_index") is not None and (number(action["task_index"]) is None or action["task_index"]<=0):raise ValueError()
                if action["kind"]=="ad" and action.get("phase") not in {"unlock","speedup"}:raise ValueError()
            archive=saved.get("settled_uncertain_actions",[])
            if not isinstance(archive,list) or len(archive)>100 or any(not isinstance(record,dict) for record in archive):raise ValueError()
            for field in ("settled_uncertain_speedup_seconds","settled_uncertain_count"):
                if field in saved and (type(saved[field]) is not int or saved[field]<0):raise ValueError()
        except (OSError,ValueError,KeyError,TypeError):
            raise CommandUnavailable("The saved research queue could not be read safely. Its requests were not replayed.") from None
        return saved

    def _known(self):return researched_pairs(self.live)

    def _mark_done(self,key):
        row=self._queue_by_pair.get(key)
        if row is None:
            row=next((row for row in self.state["queue"] if row["pair"]==key),None)
        if row and row["status"]!="completed":row["status"]="completed";self.state["completed"]+=1

    def _begin(self,action,call):
        self._check()
        if self._action_limit is not None and self._action_count>=self._action_limit:raise _Yield()
        self._action_count+=1
        if action["kind"]=="speedup":self.state["spent_seconds"]+=action["seconds"]
        self.state["pending_action"]=action;self._save()
        if self.stop.is_set():
            # This intent was persisted, but invocation has not begun. Only
            # this known unsent reservation may be released automatically.
            self.state["pending_action"]=None
            if action["kind"]=="speedup":self.state["spent_seconds"]-=action["seconds"]
            self._save();raise _Stopped()
        try:
            response=call()
            if not isinstance(response,dict) or not response or api_rejected(response):raise CommandUnavailable("Research action was not confirmed.")
        except Exception as error:
            self.state["status"]="uncertain";self._save()
            if isinstance(error,ATNetworkError) and error.retryable_read and action["kind"] in {"start","speedup","collect"}:
                # The request may have applied. Check fresh evidence; never
                # call its mutation lambda again, even after a transport error.
                self.emit({"message":"Research action reply was interrupted; checking its saved outcome from fresh game state."})
                self._fresh(initial=True);self._reconcile()
                raise _ActionReconciled() from None
            raise CommandUnavailable("The latest research request has an uncertain outcome and was not retried. Resume checks its live state before doing anything else.") from None
        return response

    def _settle_completed_speedup(self,action):
        fields=("kind","pair","slot","item_id","quantity","before_count","before_end","seconds","task_index","before_time")
        record={field:action[field] for field in fields if field in action}
        record.update(reason="target_researched_spend_unverified",settled_at=self._now())
        self.state["settled_uncertain_actions"]=(self.state.get("settled_uncertain_actions",[])+[record])[-100:]
        self.state["settled_uncertain_speedup_seconds"]=self.state.get("settled_uncertain_speedup_seconds",0)+action["seconds"]
        self.state["settled_uncertain_count"]=self.state.get("settled_uncertain_count",0)+1
        self._last_reconciliation="settled_unverified"

    def _reconcile(self):
        action=self.state.get("pending_action")
        if not action:return
        known=self._known();tasks=research_tasks(self.live);kind=action["kind"];key=action.get("pair")
        task=next((task for task in tasks.values() if task["pair"]==key and task["slot_index"]==action.get("slot")),None)
        confirmed=False
        if kind=="start":
            confirmed=key in known or task is not None
            if confirmed:
                self.state["started"]+=1
                if key in known:self._mark_done(key)
                else:
                    row=next(row for row in self.state["queue"] if row["pair"]==key);row["status"]="running"
        elif kind=="collect":
            confirmed=key in known
            if confirmed:self._mark_done(key)
        elif kind=="speedup":
            items={row["item_id"]:row for row in speedup_inventory(self.live,self._catalog("item"))}
            item=items.get(action["item_id"])
            remaining_count=item["quantity"] if item else 0
            balance_ok=action["before_count"]-remaining_count==action["quantity"]
            index=number(action.get("task_index"))
            before_time=number(action.get("before_time"))
            # New intents freeze the exact task and dispatch time. Clock
            # passage or a replacement task never proves a speedup was used.
            reduced=(task is not None and index is not None and task["task_index"]==index
                     and task["end_time"]<action["before_end"]
                     and before_time is not None
                     and task["end_time"]<=max(self._now(),action["before_end"]-action["seconds"]))
            same_pair_tasks=[current for current in tasks.values() if current["pair"]==key]
            completed=key in known and not same_pair_tasks
            time_ok=completed or reduced
            confirmed=balance_ok and time_ok
            if confirmed:self.state["confirmed_speedup_seconds"]+=action["seconds"]
            elif completed:
                # Completion permits forward progress, not a claim that the
                # old items were consumed. Keep the reservation and its audit.
                self._settle_completed_speedup(action);confirmed=True
            if completed:self._mark_done(key)
        elif kind=="ad":
            fresh=ad_rows(self.live).get(action["slot"],{})
            views=number(fresh.get("views"));old=action.get("views")
            credited=views is not None and old is not None and views>old
            if action.get("phase")=="speedup":
                before=number(action.get("before_end"));index=number(action.get("task_index"))
                # Clock passage cannot prove that the selected task received
                # an ad benefit. A replaced task is also not confirmation.
                reduced=task is not None and index is not None and task["task_index"]==index and before is not None and task["end_time"]<before
                confirmed=credited and (key in known or reduced)
                if confirmed and key in known:self._mark_done(key)
            elif action.get("phase")=="unlock":
                # A bare view increment says nothing about which chamber or
                # pair was selected by the native client.
                if task is not None:
                    confirmed=True;self.state["started"]+=1
                    row=next(row for row in self.state["queue"] if row["pair"]==key);row["status"]="running"
                elif key in known:
                    confirmed=True;self.state["started"]+=1;self._mark_done(key)
                elif action.get("verified_unlock"):
                    confirmed=True;self.state["ad_unlocks"]=sorted(set(self.state.get("ad_unlocks",[])+[action["slot"]]))
        if not confirmed:
            self.state["status"]="uncertain";self._save()
            raise CommandUnavailable("The saved research request is still unconfirmed. It was not replayed; no further resource was used.")
        self.state["pending_action"]=None;self._save()

    def _boost(self,options):
        if not options.ads:return True
        if self._boost_at is not None and self.service.clock()-self._boost_at<120:return True
        if self.boost_callback is None:return False
        self._check()
        response=self.boost_callback(self.service,self.live)
        self._check()
        if not isinstance(response,dict) or response.get("ok") is not True or number(response.get("boost_level"))!=3:return False
        self._boost_at=self.service.clock()
        from .account_state import find_ad_state
        already_active=(number(response.get("attempts"))==0
                        and number(find_ad_state(self.live).get("boost_level"))==3)
        if not already_active:self._fresh()
        return True

    def _research_ad(self,slot,phase,pair=None):
        if self.ad_callback is None:return False
        if phase not in {"unlock","speedup"}:return False
        supported=getattr(self.ad_callback,'supported_phases',None)
        if supported is not None and phase not in supported:return False
        tasks=research_tasks(self.live);task=tasks.get(slot)
        old=ad_rows(self.live).get(slot,{})
        views,max_views=number(old.get("views")),number(old.get("max_views"))
        if views is None or max_views is None or views<0 or max_views<=0 or views>=max_views:return False
        action={"kind":"ad","slot":slot,"views":views,"phase":phase}
        if phase=="speedup":
            next_speed=number(old.get("next_speed_up"))
            if task is None or task["end_time"]<=self._now() or next_speed is None or next_speed<=0:return False
            pair=next((row for row in self.state["queue"] if row["pair"]==task["pair"]),None)
            if pair is None:return False
            action.update(pair=task["pair"],task_index=task["task_index"],before_end=task["end_time"],next_speed_up=next_speed)
        else:
            if task is not None or not isinstance(pair,dict) or pair.get("pair")!=pair_key(pair.get("card1"),pair.get("card2")) or not pair.get("pair"):return False
            if pair["pair"] in self._known() or any(current["pair"]==pair["pair"] for current in tasks.values()):return False
            action["pair"]=pair["pair"]
        context={**self.live,"_commander_research":{"slot_index":slot,"phase":phase,"pair":pair["pair"],
                 **{key:pair[key] for key in ("card1","card2","combo_id","card1_name","card2_name")},
                 **({key:action[key] for key in ("task_index","before_end","next_speed_up")} if phase=="speedup" else {})}}
        answer=self._begin(action,lambda:self.ad_callback(self.service,context))
        if answer.get("ok") is not True:
            if answer.get("playback_started") is False:
                # The native adapter records this only before delivering Watch.
                # Navigation/selection may have stopped, but no research or
                # rewarded action was sent. Keep the queue resumable on Pause.
                self.state["pending_action"]=None
                self.state["status"]="waiting";self._save();self._check()
                return False
            self.state["status"]="uncertain";self._save();raise CommandUnavailable("The legitimate research ad benefit was not confirmed; playback was not repeated.")
        if phase=="unlock" and answer.get("verified") is True and answer.get("unlocked") is True and number(answer.get("slot_index"))==slot:
            self.state["pending_action"]["verified_unlock"]=True;self._save()
        self._fresh();self._reconcile()
        return phase!="unlock" or slot in self.state.get("ad_unlocks",[]) or any(task["pair"]==pair["pair"] for task in research_tasks(self.live).values()) or pair["pair"] in self._known()

    def _output(self,options,status,plan=None,*,row_limit=5000):
        queue=plan if plan is not None else self.state.get("queue",[])
        free,blocked=slot_state(self.live,self.state.get("ad_unlocks",[]))
        items=speedup_inventory(self.live,self.catalogs["item"]) if options.speedups and "item" in self.catalogs else None
        data={"count":len(queue),"started":self.state.get("started",0),"completed":self.state.get("completed",0),
              "speedups_used_hours":self.state.get("confirmed_speedup_seconds",0)/3600,
              "speedups_reserved_hours":(self.state.get("spent_seconds",0)-self.state.get("confirmed_speedup_seconds",0))/3600,
              "speedups_settled_unverified_hours":self.state.get("settled_uncertain_speedup_seconds",0)/3600,
              "settled_uncertain_count":self.state.get("settled_uncertain_count",0),
              "speedups_available_hours":sum(item["duration_seconds"]*item["quantity"] for item in items)/3600 if items is not None else None,
              "status":status,"free_slots":free,"blocked_slots":blocked,"options":options.serialized(),
              "displayed_count":min(len(queue),row_limit),"rows_truncated":len(queue)>row_limit}
        public=[{key:row[key] for key in ("card1","card2","card1_name","card2_name","combo_id","name","rarity1","rarity2","model_power","status") if key in row} for row in queue[:row_limit]]
        message=f'Research {status}: {data["completed"]}/{data["count"]} pairs completed; {data["speedups_used_hours"]:g} speedup hours confirmed.'
        if data["speedups_settled_unverified_hours"]:
            message+=f' {data["speedups_settled_unverified_hours"]:g} historical speedup hours remain unverified and were not replayed.'
        return CommandResult(rows=public,data=data,message=message).safe(self.service.context.private)

    def reconcile_only(self):
        """Read current evidence and settle the local ledger; send no action."""
        self._last_reconciliation=None
        try:
            self._fresh(initial=True)
            saved=self._load()
            if not saved:
                return CommandResult(message="No saved research queue needs reconciliation. No research action was sent.",
                                     data={"status":"empty","reconciled":True,"pending_kind":None})
            if saved.get("account_binding")!=self.binding:
                raise CommandUnavailable("This research queue belongs to another account.")
            self.state=saved;self._queue_by_pair={row["pair"]:row for row in saved["queue"]}
            try:
                self._reconcile()
            except CommandUnavailable:
                if not self.state.get("pending_action"):raise
                return self._recovery_output("uncertain",False)
            self.state["status"]="paused";self._save()
            return self._recovery_output("reconciled",True)
        except _Stopped:
            return CommandResult(message="Research reconciliation paused. No research action was sent.",
                                 data={"status":"paused","reconciled":False})

    def _recovery_output(self,status,reconciled):
        pending=self.state.get("pending_action") or {}
        data={"status":status,"reconciled":reconciled,"pending_kind":pending.get("kind"),
              "count":len(self.state["queue"]),"started":self.state["started"],"completed":self.state["completed"],
              "speedups_used_hours":self.state["confirmed_speedup_seconds"]/3600,
              "speedups_reserved_hours":(self.state["spent_seconds"]-self.state["confirmed_speedup_seconds"])/3600,
              "speedups_settled_unverified_hours":self.state.get("settled_uncertain_speedup_seconds",0)/3600,
              "settled_uncertain_count":self.state.get("settled_uncertain_count",0),"options":self.state["options"]}
        if not reconciled:
            message="The saved research request is still unconfirmed. No action was replayed and its reservation is unchanged."
        elif self._last_reconciliation=="settled_unverified":
            message="AT confirms the target pair is researched. The old speedup action was archived without claiming item consumption; its reserved hours remain unverified. No research action was sent."
        else:
            message="Saved research state reconciled. The queue remains paused; no research action was sent."
        return CommandResult(message=message,data=data).safe(self.service.context.private)

    def preview(self,options=None):
        options=ResearchOptions.from_dict(options)
        try:
            self._fresh(initial=True)
            if options.speedups:self._catalog("item")
            plan=build_plan(self.live,self._catalog("card"),self._catalog("combo"),options,should_stop=self.stop.is_set)
            result=self._output(options,"preview",plan[:options.max_pairs]);result.data["matching_count"]=len(plan)
            return result
        except _Stopped:
            return CommandResult(message="Research preview paused. No research was started.",data={"status":"paused"})

    def run(self,options=None,*,resume=True,wait=True,max_actions=3):
        options=ResearchOptions.from_dict(options)
        self._last_reconciliation=None
        self._queue_replaced=False
        self._action_count=0;self._action_limit=None if wait else max_actions
        try:
            self._fresh(initial=True)
            if options.speedups:self._catalog("item")
            saved=self._load()
            if saved and saved.get("account_binding")!=self.binding:
                raise CommandUnavailable("This research queue belongs to another account.")
            if saved:
                # Reconcile using the old queue before comparing replacement
                # settings. New Queue must not strand a confirmable old intent.
                self.state=saved;self._queue_by_pair={row["pair"]:row for row in saved["queue"]}
                self._reconcile()
            if saved and resume:
                if saved["options"]!=options.serialized():raise CommandInputError("Resume uses the saved research settings. Start a new queue explicitly to change them.")
            else:
                queue=build_plan(self.live,self._catalog("card"),self._catalog("combo"),options,should_stop=self.stop.is_set)[:options.max_pairs]
                self.state={"schema":1,"account_binding":self.binding,"options":options.serialized(),"queue":queue,"started":0,"completed":0,
                            "spent_seconds":0,"confirmed_speedup_seconds":0,"pending_action":None,"ad_unlocks":[],"status":"running",
                            "settled_uncertain_actions":saved.get("settled_uncertain_actions",[]),
                            "settled_uncertain_speedup_seconds":saved.get("settled_uncertain_speedup_seconds",0),
                            "settled_uncertain_count":saved.get("settled_uncertain_count",0)}
                self._save()
                self._queue_replaced=True
            self._queue_by_pair={row["pair"]:row for row in self.state["queue"]}
            while True:
                self._check();self._reconcile();known=self._known();tasks=research_tasks(self.live)
                for index,row in enumerate(self.state["queue"]):
                    if index%128==0:self._check()
                    if row["pair"] in known:self._mark_done(row["pair"])
                remaining=[row for row in self.state["queue"] if row["status"]!="completed"]
                if not remaining:self.state["status"]="complete";self._save();return self._output(options,"complete")
                # Re-check ownership/metadata on resume and each fresh cycle.
                current={row["pair"] for row in build_plan(self.live,self._catalog("card"),self._catalog("combo"),options,should_stop=self.stop.is_set)}
                current.update(task["pair"] for task in tasks.values());current.update(known)
                if any(row["pair"] not in current for row in remaining):raise CommandUnavailable("A queued input card or its verified recipe is no longer available.")
                if any(row["status"]=="running" and row["pair"] not in {task["pair"] for task in tasks.values()} and row["pair"] not in known for row in remaining):
                    raise CommandUnavailable("Previously started research disappeared without a researched-pair confirmation. No start was repeated.")
                if not self._boost(options):self.state["status"]="blocked_ads";self._save();return self._output(options,"blocked_ads")
                tasks=research_tasks(self.live);progress=False
                wanted={row["pair"] for row in remaining}
                for slot,task in tasks.items():
                    if task["pair"] not in wanted:continue
                    if task["end_time"]<=self._now():
                        self._begin({"kind":"collect","pair":task["pair"],"slot":slot},lambda task=task:self.service.call("completeResearch",{"task_index":task["task_index"]}))
                        self._fresh();self._reconcile();progress=True;break
                    ad=ad_rows(self.live).get(slot,{})
                    if options.ads and number(ad.get("next_speed_up")) is not None and self._research_ad(slot,"speedup"):
                        progress=True;break
                    if options.speedups:
                        items=speedup_inventory(self.live,self._catalog("item"))
                        available=sum(item["duration_seconds"]*item["quantity"] for item in items)
                        spendable=available-options.reserve_hours*3600
                        budget=spendable if options.use_all_speedups else min(options.max_hours*3600-self.state["spent_seconds"],spendable)
                        selected=speedup_plan(task["end_time"]-self._now(),items,budget)
                        if selected:
                            item=selected[0];seconds=item["duration_seconds"]*item["quantity"]
                            action={"kind":"speedup","pair":task["pair"],"slot":slot,"item_id":item["item_id"],"quantity":item["quantity"],
                                    "before_count":next(row["quantity"] for row in items if row["item_id"]==item["item_id"]),"before_end":task["end_time"],"seconds":seconds,
                                    "task_index":task["task_index"],"before_time":self._now()}
                            self._begin(action,lambda:self.service.call("useResearchSpeedUpItem",{"item_id":item["item_id"],"slot_index":slot,"quantity":item["quantity"]}))
                            self._fresh();self._reconcile();progress=True;break
                if progress:continue
                free,blocked=slot_state(self.live,self.state.get("ad_unlocks",[]))
                active={task["pair"] for task in tasks.values()}
                pending=next((row for row in remaining if row["pair"] not in active),None)
                if pending and not free and options.ads:
                    for blocked_slot in blocked:
                        slot=blocked_slot["slot_index"]
                        if slot not in tasks and slot in ad_rows(self.live) and self._research_ad(slot,"unlock",pending):
                            # The real client may start the selected pair as
                            # part of its ad callback. Never start it twice.
                            active_now={task["pair"] for task in research_tasks(self.live).values()}
                            if pending["pair"] in active_now or pending["pair"] in self._known():progress=True
                            else:free,_=slot_state(self.live,self.state.get("ad_unlocks",[]))
                            break
                if progress:continue
                if pending and free:
                    slot=free[0]
                    self.state["ad_unlocks"]=[value for value in self.state.get("ad_unlocks",[]) if value!=slot]
                    self._begin({"kind":"start","pair":pending["pair"],"slot":slot},lambda:self.service.call("researchCombo",{"card1":pending["card1"],"card2":pending["card2"],"slot_index":slot}))
                    self._fresh();self._reconcile();pending["status"]="running";continue
                if not tasks:
                    self.state["status"]="blocked_slots";self._save();return self._output(options,"blocked_slots")
                self.state["status"]="waiting";self._save()
                if not wait:return self._output(options,"waiting")
                if self.waiter(options.poll_seconds):raise _Stopped()
                self._fresh()
        except _Stopped:
            if self.state:self.state["status"]="paused";self._save()
            return self._output(options,"paused",row_limit=0) if self.state else CommandResult(
                message="Research paused before opening its saved queue. Saved progress and budgets are unchanged.",data={"status":"paused"})
        except _Yield:
            self.state["status"]="waiting";self._save();return self._output(options,"waiting")
        except _ActionReconciled:
            self.state["status"]="waiting";self._save();return self._output(options,"waiting")
