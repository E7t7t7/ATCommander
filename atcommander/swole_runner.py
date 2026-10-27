from __future__ import annotations
import threading,time
from typing import Any,Callable
from .api import ATClient,ATApiError
from .credentials import load_credentials
from .preflight import ad_preflight
from .strategy import SingleComboRushPolicy
from .challenge_state import current_challenge
from .shop_rewards import api_rejected

class SwoleTrainingRunner:
    """Ordered Swole schedule: training milestones, waits and optional fight blocks."""
    def __init__(self, queue:list[dict[str,Any]], poll_seconds:int=30, auto_watch_ads:bool=False, event:Callable[[dict[str,Any]],None]|None=None):
        self.queue=[dict(x) for x in queue if isinstance(x,dict)]
        self.poll=max(5,int(poll_seconds)); self.auto_watch_ads=bool(auto_watch_ads)
        self.event=event or (lambda _e:None); self._stop=threading.Event(); self._thread=None
        self.policy=SingleComboRushPolicy(); self.c=None; self._last_mutation={}; self._fights_completed=0
    @property
    def running(self):return bool(self._thread and self._thread.is_alive())
    def start(self):
        if self.running:return
        self._stop.clear();self._thread=threading.Thread(target=self._run,daemon=True);self._thread.start()
    def stop(self):self._stop.set()
    def _emit(self,k,**kw):
        try:self.event({"kind":k,"mode":"Swole",**kw})
        except Exception:pass
    @staticmethod
    def _squad(status):
        raw=status.get("user_hero_squad") or []
        return list(raw.values()) if isinstance(raw,dict) else raw if isinstance(raw,list) else []
    @classmethod
    def _hero_level(cls,status,hero_id:str)->int|None:
        for x in cls._squad(status):
            if isinstance(x,dict) and str(x.get("hero_id"))==str(hero_id):
                try:return int(x.get("training_level") or 0)
                except:return 0
        return None
    @staticmethod
    def _active_task(status):
        tasks=status.get("user_tasks") or {}
        vals=tasks.values() if isinstance(tasks,dict) else tasks if isinstance(tasks,list) else []
        for t in vals:
            if not isinstance(t,dict) or t.get("task_index") is None:continue
            try:
                if t.get("task_type") is not None and int(t.get("task_type"))!=2:continue
            except Exception:pass
            if t.get("end_time") or t.get("start_time"):return t
        return None
    def _status(self):
        status=self.c.get_swole_status()
        # Immediately after a mutation, its response is often fresher than the next
        # compact account read. Keep its task container until the live read catches up.
        if not self._active_task(status) and isinstance(self._last_mutation,dict):
            tasks=self._last_mutation.get("user_tasks") or {}
            if tasks:status["user_tasks"]=tasks
        return status
    def _drive_battle(self,state,bno):
        bid=state.get("battle_id"); host=state.get("host_id"); cur=state
        for move in range(1,31):
            if self._stop.is_set():return None,move-1
            d=self.policy.choose(cur)
            if d is None:raise ATApiError("No legal Swole play could be derived from battle state.")
            self._emit("move",battle=bno,move=move,action=d.action,card_uid=d.card_uid,card_uid2=d.card_uid2,reason=d.reason)
            r=self.c.play_battlefield_card(battle_id=bid,host_id=host,card_uid=d.card_uid,card_uid2=d.card_uid2); nxt=self.c.extract_battle_data(r)
            if api_rejected(r):raise ATApiError("AT rejected a Swole card play. The move was not retried.")
            if not isinstance(nxt,dict) or str(nxt.get("battle_id",bid))!=str(bid):
                raise ATApiError("Swole returned no matching continuing battle. The move was not retried.")
            # Native ServiceCombatData uses the explicit winner field. A generic
            # results/result flag may describe another successful action.
            winner=nxt.get("winner")
            if isinstance(winner,bool):return winner,move
            if winner in (0,1,"0","1","true","false"):
                return winner in (1,"1","true"),move
            if "winner" in nxt or nxt.get("battle_finished"):
                raise ATApiError("Swole ended without a confirmed winner. The schedule is paused.")
            cur=nxt; host=cur.get("host_id",host);time.sleep(.10)
        raise ATApiError("Safety stop: Swole battle exceeded 30 moves.")
    @staticmethod
    def _ticket_state(status):
        ev=status.get("swole_event") or {}
        event_id=str(ev.get("event_id") or ev.get("id") or "")
        def timestamp(source):
            try:return int(source.get("time") or 0) if isinstance(source,dict) else 0
            except (ValueError,TypeError):return 0
        sources=sorted((status.get("account"),status.get("init")),key=timestamp,reverse=True)
        for source in (*sources,status):
            live=current_challenge(source,event_id,now=status.get("time"))
            if live is not None:return {**live,"active":True}
        def n(v):
            try:return int(v)
            except Exception:return None
        return {
            "event_id":event_id,
            "challenge_id":str(ev.get("challenge_id") or ""),
            "current":n(ev.get("energy")),
            "max":n(ev.get("max_energy")),
            "cost":n(ev.get("battle_cost")) or 1,
            "level":n(ev.get("level")),
            "points":n(ev.get("points")),
            "name":str(ev.get("display_name") or "Swole tickets"),
            "active":False,
        }
    def _do_fights(self,count:int)->bool:
        total=max(1,int(count));done=0;last_energy_marker=None;self._fights_completed=0
        while done<total and not self._stop.is_set():
            status=self._status(); tickets=self._ticket_state(status)
            if self._stop.is_set():return False
            cur=tickets["current"];mx=tickets["max"];cost=tickets["cost"]
            marker=(cur,mx,tickets["event_id"],tickets["challenge_id"],tickets.get("level"))
            if marker!=last_energy_marker:
                self._emit("swole_energy",current=cur,max=mx,cost=cost,event_id=tickets["event_id"],challenge_id=tickets["challenge_id"],level=tickets.get("level"))
                last_energy_marker=marker
            if not tickets["active"] or tickets.get("points") is None:
                self._emit("swole_fight_paused",message="Current active Swole event, challenge level, or tickets could not be confirmed. The remaining schedule is preserved; refresh status before resuming.")
                return False
            # If AT explicitly says there are no tickets, keep the FIGHT step at
            # the head of the schedule and monitor until they refill. Do not pop
            # the step or terminate the trainer.
            if cur is not None and cur<cost:
                self._emit("swole_wait",message=f"Swole tickets {cur}/{mx if mx is not None else '—'}; need {cost} per fight. Keeping this fight block queued and checking again in {self.poll}s.")
                self._stop.wait(self.poll);continue

            challenge_id=tickets["challenge_id"]
            # The challenge ID is distinct from the outer Swole event ID, and
            # AT advances the challenge level after fights. Use the fresh row.
            level=tickets.get("level")
            r=self.c.start_challenge(challenge_id,level,auto_battle=False)
            st=r.get("battle_data") if not api_rejected(r) else None
            if not isinstance(st,dict) or not st.get("battle_id"):
                # An ambiguous accepted start may already have spent a ticket.
                # Preserve the step instead of blindly retrying after a refill.
                warning=str(r.get("result_message") or "").casefold()
                reason=("AT rejected the Swole start (combat validation warning 157). Refresh the public client version before resuming."
                    if api_rejected(r) and "warning_157" in warning else
                    "AT rejected the Swole start; no accepted battle state was returned." if api_rejected(r) else
                    "Swole startChallenge returned no battle state; a ticket may already have been spent.")
                self._emit("swole_fight_paused",message=reason+" The schedule is preserved and the start will not be retried automatically.")
                return False
            opp=self.c.opponent_name(st);bno=done+1;self._emit("battle_start",number=bno,opponent=opp,level=level,points=tickets["points"])
            won,moves=self._drive_battle(st,bno)
            if won is None:return False
            done+=1;self._fights_completed=done
            if self._stop.is_set():
                self._emit("battle_end",number=bno,opponent=opp,won=won,moves=moves,level=level,
                    points_before=tickets["points"],points_after=None,progress_verified=False,post_status_verified=False)
                return False
            try:after=self._ticket_state(self._status())
            except Exception:after={}
            verified=(after.get("active") and after.get("event_id")==tickets["event_id"]
                and after.get("challenge_id")==challenge_id and after.get("points") is not None)
            progressed=bool(verified and after["points"]>tickets["points"] and after.get("level",-1)>level)
            self._emit("battle_end",number=bno,opponent=opp,won=won,moves=moves,level=level,
                points_before=tickets["points"],points_after=after.get("points") if verified else None,
                progress_verified=progressed,post_status_verified=bool(verified),tickets_after=after.get("current") if verified else None)
            if not verified or won and not progressed:
                self._emit("swole_fight_paused",message="Swole combat ended, but fresh server score progress could not be confirmed."+
                    (f" Saved score/floor: {tickets['points']}/{level} → {after['points']}/{after['level']}." if verified and won else "")+
                    " Remaining fights are preserved; no further ticket will be spent automatically.")
                return False
            if done<total:self._stop.wait(1)
        return done>=total
    def _run(self):
        q=list(self.queue)
        try:
            self.c=ATClient(load_credentials(),should_stop=self._stop.is_set); init=self.c.init(); ad_preflight(self.c,init,lambda m:self._emit("log",message=m), auto_watch=self.auto_watch_ads)
            self._emit("status",text="Swole schedule running")
            while q and not self._stop.is_set():
                status=self._status(); task=self._active_task(status); entry=q[0]; typ=entry.get("type","train")
                if typ=="label":
                    self._emit("log",message=str(entry.get("text") or "Schedule section"));q.pop(0);continue
                if typ=="wait_training":
                    if task:
                        end=int(task.get("end_time") or 0); now=int(status.get("time") or time.time())
                        if end and end<=now:
                            idx=task.get("task_index");self._emit("log",message=f"Training finished; claiming task {idx} before continuing.")
                            self._last_mutation=self.c.complete_swole_training(idx);self._stop.wait(1);continue
                        self._emit("swole_wait",message=f"WAIT TRAINING: active task still running; checking again in {self.poll}s.");self._stop.wait(self.poll);continue
                    self._emit("log",message="WAIT TRAINING: no active training task; continuing.");q.pop(0);continue
                if typ=="wait":
                    sec=max(1,int(entry.get("seconds") or 1));label=str(entry.get("label") or f"{sec}s")
                    self._emit("log",message=f"Schedule wait: {label}.");self._stop.wait(sec);q.pop(0);continue
                if typ=="fight":
                    requested=int(entry.get('count') or 1)
                    self._emit("log",message=f"Running {requested} scheduled Swole fight(s).")
                    try:completed=self._do_fights(requested)
                    finally:entry["count"]=max(0,requested-self._fights_completed)
                    if not completed:
                        if not entry["count"]:q.pop(0)
                        break
                    q.pop(0);continue
                if task:
                    end=int(task.get("end_time") or 0); now=int(status.get("time") or time.time())
                    if end and end<=now:
                        idx=task.get("task_index");self._emit("log",message=f"Completing finished Swole training task {idx}.")
                        self._last_mutation=self.c.complete_swole_training(idx);self._stop.wait(1);continue
                    hid=str(task.get("task_id_1") or "");self._emit("swole_wait",message=f"Training hero {hid} is active until {end}; next training step waits {self.poll}s (fight blocks can be placed before that step).");self._stop.wait(self.poll);continue
                hid=str(entry.get("id") or "");target=int(entry.get("target_level") or 1);cur=self._hero_level(status,hid)
                if cur is not None and cur>=target:
                    self._emit("log",message=f"{entry.get('name',hid)} already reached target {target}; advancing.");q.pop(0);continue
                self._emit("log",message=f"Starting {entry.get('name',hid)} training toward level {target}"+(f" (currently {cur})" if cur is not None else ""))
                response=self.c.start_swole_training(hid);self._last_mutation=response
                new_task=self._active_task(response)
                if not new_task:
                    raise ATApiError("startHeroTraining returned without an active training task. AT may have rejected the training because of Swole coins, timing, or event state.")
                self._emit("log",message=f"Training accepted by AT: task {new_task.get('task_index')} ends at {new_task.get('end_time')}.")
                # One requested level was successfully started. For higher milestones,
                # leave the same step in place so it resumes after claim and repeats.
                if cur is not None and target<=cur+1:q.pop(0)
                self._stop.wait(2)
            self._emit("finished",stopped=self._stop.is_set(),remaining_queue=q)
        except Exception as e:self._emit("fatal",message=str(e),remaining_queue=q)
