from __future__ import annotations
import threading, time
from typing import Any, Callable
from .api import ATClient, ATApiError
from .credentials import load_credentials
from .strategy import SingleComboRushPolicy
from .preflight import ad_preflight
from .browser_bridge import browser_call_profile
from .adventure_completion import scan_adventure_islands,mission_progress,read_adventure_energy
from .adventure_run_state import AdventureRunState
from .shop_rewards import api_rejected

class AdventureRunner:
    """Farm an explicit Adventure mission ID with the proven Item→Combo policy."""
    def __init__(self, mission_id:int, battle_limit:int=1, move_delay:float=.10,
                 between_battles_delay:float=1.5, reserve_energy:int=0,
                 continuous:bool=False, auto_watch_ads:bool=False, event:Callable[[dict[str,Any]],None]|None=None,
                 *,complete_islands:bool=False,completion_target:str="mastery",state_path=None,energy_budget:int|None=None):
        self.mission_id=int(mission_id); self.battle_limit=max(0,int(battle_limit))
        self.move_delay=max(.05,float(move_delay)); self.battle_delay=max(.1,float(between_battles_delay))
        self.reserve=max(0,int(reserve_energy)); self.continuous=bool(continuous); self.auto_watch_ads=bool(auto_watch_ads)
        self.event=event or (lambda _e:None); self.policy=SingleComboRushPolicy(); self._stop=threading.Event(); self._thread=None; self._client=None
        self.complete_islands=bool(complete_islands);self.completion_target=completion_target;self.state_path=state_path
        self.energy_budget=energy_budget;self._journal=None;self._pending=None;self._catalogs=None;self._catalog_stamp=None;self._pause_reason=None
        if energy_budget is not None and (type(energy_budget) is not int or energy_budget<0):
            raise ValueError("Adventure energy budget must be a nonnegative whole number.")
    @property
    def running(self): return bool(self._thread and self._thread.is_alive())
    def start(self):
        if self.running:return
        self._stop.clear(); self._thread=threading.Thread(target=self._run,daemon=True); self._thread.start()
    def stop(self): self._stop.set()
    def pause(self, paused=True): pass
    def _emit(self,kind,**kw):
        if kind=="adventure_paused":self._pause_reason=kw.get("message") or "Adventure needs a verified current battle state."
        elif kind in {"adventure_target","battle_start"}:self._pause_reason=None
        elif kind=="finished" and not kw.get("stopped") and self._pause_reason:
            kw.update(paused=True,pause_reason=self._pause_reason)
        try:self.event({"kind":kind,"mode":"Adventure",**kw})
        except Exception:pass
    @staticmethod
    def _energy(init):
        ud=(init or {}).get("user_data") or {}
        current=maxv=None
        for k in ("energy","current_energy","mission_energy","adventure_energy"):
            v=ud.get(k)
            try:
                if v is not None: current=int(v);break
            except:pass
        for k in ("max_energy","mission_max_energy","adventure_max_energy"):
            v=ud.get(k)
            try:
                if v is not None: maxv=int(v);break
            except:pass
        return current,maxv
    def _drive(self,state,battle_no):
        bid=state.get("battle_id"); hid=state.get("host_id"); cur=state; last={}
        for move in range(1,31):
            if self._stop.is_set(): return None,move-1
            d=self.policy.choose(cur)
            if d is None: raise ATApiError("No legal Adventure play could be derived from battle state.")
            self._emit("move",battle=battle_no,move=move,action=d.action,card_uid=d.card_uid,card_uid2=d.card_uid2,reason=d.reason)
            r=self._client.play_battlefield_card(battle_id=bid,host_id=hid,card_uid=d.card_uid,card_uid2=d.card_uid2); last=r
            nxt=self._client.extract_battle_data(r)
            if self._client.battle_finished(r,nxt): return self._client.extract_outcome(r,nxt),move
            cur=nxt or cur; bid=cur.get("battle_id",bid); hid=cur.get("host_id",hid); time.sleep(self.move_delay)
        raise ATApiError("Safety stop: Adventure battle exceeded 30 moves.")
    def _run(self):
        try:
            self._client=ATClient(load_credentials(),timeout=25); init=self._client.init()
            if self.complete_islands:
                self._run_completion(init);return
            ad_preflight(self._client,init,lambda m:self._emit("log",message=m), auto_watch=self.auto_watch_ads)
            n=0; self._emit("status",text=f"Farming mission {self.mission_id}")
            while not self._stop.is_set() and (self.battle_limit==0 or n<self.battle_limit):
                try:
                    cur=self._client.init(); en,max_en=self._energy(cur)
                    if en is not None:
                        self._emit("adventure_energy",current=en,max=max_en,reserve=self.reserve)
                        if en<=self.reserve:
                            if self.continuous:self._stop.wait(60); continue
                            self._emit("log",message=f"Adventure energy {en} reached reserve {self.reserve}."); break
                    r=self._client.call("startMission",{"mission_id":str(self.mission_id)})
                except ATApiError as exc:
                    if any(x in str(exc).lower() for x in ("energy","not enough","mission")):
                        self._emit("log",message=f"Adventure stopped: {exc}"); break
                    raise
                # startMission itself returns authoritative current/max Adventure energy.
                en2,max2=self._energy(r)
                if en2 is not None:self._emit("adventure_energy",current=en2,max=max2,reserve=self.reserve)
                state=self._client.extract_battle_data(r)
                if not state or not state.get("battle_id"):
                    self._emit("log",message=f"Adventure standalone startMission for {self.mission_id} returned no battle state; retrying from the live game browser context.")
                    try:
                        from .ad_ui import prepare_game_ui
                        prepare_game_ui(lambda m:self._emit("log",message=f"Adventure browser: {m}"),wait_for_canvas=20)
                    except Exception as exc:
                        self._emit("log",message=f"Adventure browser prep warning: {exc}")
                    prof={"message":"startMission","data":{"mission_id":"{mission_id}"}}
                    br=browser_call_profile(prof,{"mission_id":self.mission_id},self._client.credentials,timeout=15.0)
                    if br.get("ok"):
                        r=br.get("response") or {}; state=self._client.extract_battle_data(r)
                    if not state or not state.get("battle_id"):
                        raw=(br.get("message") if isinstance(br,dict) and not br.get("ok") else (r.get("result_message") if isinstance(r,dict) else None))
                        raise ATApiError(f"startMission did not return usable battle_data (browser fallback: {raw or 'no battle state'}).")
                n+=1; opp=self._client.opponent_name(state); self._emit("battle_start",number=n,opponent=opp)
                won,moves=self._drive(state,n); self._emit("battle_end",number=n,opponent=opp,won=won,moves=moves)
                if self.battle_limit==0 or n<self.battle_limit:self._stop.wait(self.battle_delay)
            self._emit("finished",stopped=self._stop.is_set())
        except Exception as e:self._emit("fatal",message=str(e))

    def _scan(self,live):
        limited=live.get("limited_adventure_data") or {}
        stamp=(live.get("campaign_data_version"),live.get("mission_data_version"),
               limited.get("limited_adventure_track") if isinstance(limited,dict) else None)
        if self._catalogs is None or stamp!=self._catalog_stamp:
            self._catalogs=(self._client.campaign_catalog(),self._client.mission_catalog());self._catalog_stamp=stamp
        scan=scan_adventure_islands(live,*self._catalogs,target=self.completion_target)
        self._emit("adventure_scan",scan=scan)
        return scan

    def _save_pending(self):self._journal.save(self._pending)

    def _reconcile_pending(self,live):
        pending=self._journal.read()
        if not pending:return True
        after=mission_progress(live,pending["mission_id"],pending.get("campaign_id"))
        before=int(pending["wins_before"])
        terminal=pending.get("terminal")
        if terminal is False and after["wins"]>before:
            self._emit("adventure_paused",message="The prior Adventure loss conflicts with saved progress. Its outcome still needs review; no fight was replayed.")
            return False
        if after["wins"]>before or terminal is False and after["wins"]==before:
            self._journal.save(None)
            self._emit("log",message="The previous Adventure fight was reconciled from fresh stage progress; scanning again.")
            return True
        if terminal is True and pending.get("farming") is True and after["wins"]>=before:
            self._journal.save(None);return True
        if self._completed_farm_has_no_resume(pending,live,after):
            self._journal.archive_completed_farm(pending,live["time"]);self._pending=None
            self._emit("log",message="The old farm attempt has no resumable battle in fresh game state. Its unknown outcome was archived without crediting a win/loss or replaying a request.")
            return True
        self._emit("adventure_paused",message="A previous Adventure start or move has an unconfirmed outcome. Finish/check that fight in the game before resuming; it will not be replayed.")
        return False

    @staticmethod
    def _completed_farm_has_no_resume(pending,live,progress):
        # Native InitResponse reads battle_to_resume with default 0 and only
        # stores previous combat data when active_battle_data is present.
        # A completed farm's capped counter cannot establish an old outcome.
        if (pending["phase"] not in {"battle_ready","move_pending","terminal"} or not pending["farming"]
                or pending["wins_before"]<12 or progress["wins"]<pending["wins_before"]
                or progress["level"]!=13 or not progress["available"]):
            return False
        if not isinstance(live,dict) or api_rejected(live):
            return False
        marker=live.get("result")
        # Successful native init payloads may omit the result marker; their
        # full account/progress fields still establish a complete read.
        if "result" in live and not (marker is True or type(marker) is int and marker==1
                or isinstance(marker,str) and marker.casefold() in {"1","true"}):
            return False
        if any(not isinstance(live.get(key),dict) for key in
               ("user_data","current_campaigns","current_missions","mission_completions")):
            return False
        # This delay is a local guard against an in-flight request, not a
        # claimed server battle expiry time.
        if type(live.get("time")) is not int or live["time"]-pending["server_time"]<120:
            return False
        resume=live.get("battle_to_resume",0)
        if not (type(resume) is int and resume==0 or isinstance(resume,str) and resume=="0"):
            return False
        if "active_battle_data" in live:
            return False
        if any(key in live for key in ("battle_data","current_battle","battle_state","active_battle","battle","combat_state")):
            return False
        return True

    def _drive_completion(self,state,battle_no):
        bid=state.get("battle_id");host=state.get("host_id");cur=state;stop_reported=False
        for move in range(1,31):
            if self._stop.is_set() and not stop_reported:
                # Finish the charged fight before stopping; no following start
                # is authorized. This preserves its outcome on a normal Stop.
                self._emit("log",message="Stop requested: finishing the current Adventure fight, then stopping before another start.");stop_reported=True
            decision=self.policy.choose(cur)
            if decision is None:raise ATApiError("No legal Adventure play was available; the pending fight was preserved.")
            self._emit("move",battle=battle_no,move=move,action=decision.action,card_uid=decision.card_uid,card_uid2=decision.card_uid2,reason=decision.reason)
            self._pending["phase"]="move_pending";self._save_pending()
            response=self._client.play_battlefield_card(battle_id=bid,host_id=host,card_uid=decision.card_uid,card_uid2=decision.card_uid2)
            if api_rejected(response):raise ATApiError("AT rejected an Adventure move; it was not retried.")
            nxt=response.get("battle_data") if isinstance(response,dict) else None
            if isinstance(nxt,dict) and str(nxt.get("battle_id",bid))!=str(bid):
                raise ATApiError("Adventure returned a different battle; the move was not repeated.")
            if isinstance(nxt,dict) and nxt.get("host_id") is not None and str(nxt["host_id"])!=str(host):
                raise ATApiError("Adventure returned a different battle owner; the move was not repeated.")
            if self._client.battle_finished(response,nxt):
                won=self._client.extract_outcome(response,nxt)
                self._pending.update(phase="terminal",terminal=won if isinstance(won,bool) else None);self._save_pending()
                return won,move
            if not isinstance(nxt,dict) or not nxt.get("battle_id"):
                raise ATApiError("Adventure returned no continuing battle state; the move was not repeated.")
            if nxt==cur:
                raise ATApiError("Adventure returned unchanged battle state; the move was not repeated.")
            cur=nxt;host=cur.get("host_id",host);self._pending["phase"]="battle_ready";self._save_pending();time.sleep(self.move_delay)
        raise ATApiError("Adventure exceeded 30 moves; its pending fight was preserved.")

    def _run_completion(self,initial):
        self._pause_reason=None
        if self._stop.is_set():self._emit("finished",stopped=True);return
        self._journal=AdventureRunState(self._client.credentials.user_id,self.state_path)
        if not self._reconcile_pending(initial):self._emit("finished",stopped=False,paused=True);return
        if self._stop.is_set():self._emit("finished",stopped=True);return
        ad_preflight(self._client,initial,lambda message:self._emit("log",message=message),auto_watch=self.auto_watch_ads,should_stop=self._stop.is_set)
        n=0;spent=0;losses=0;farm_announced=False
        self._emit("status",text="Completing Adventure islands")
        while not self._stop.is_set() and (self.battle_limit==0 or n<self.battle_limit):
            live=self._client.init()
            if self._stop.is_set():break
            scan=self._scan(live)
            if self._stop.is_set():break
            farming=scan["all_complete"]
            target=scan["next_mission"]
            if farming:
                progress=mission_progress(live,self.mission_id)
                campaign=next((island for island in scan["islands"] if any(stage["mission"]==self.mission_id for stage in island["missions"])),None)
                if campaign is None or not progress["available"]:
                    self._emit("adventure_paused",message="All islands are complete, but the saved default farm stage is unavailable. The default was preserved.");break
                target={"mission":self.mission_id,"campaign_id":campaign["id"],"island":campaign["name"],
                        "stage":next(stage["stage"] for stage in campaign["missions"] if stage["mission"]==self.mission_id),
                        "target_wins":12 if self.completion_target=="mastery" else 1,**progress}
                if not farm_announced:
                    self._emit("adventure_farm_resume",mission_id=self.mission_id);farm_announced=True
            elif target is None:
                self._emit("adventure_paused",message="Incomplete island stages remain locked or unconfirmed. The default farm stage will wait until the full route is verified complete.")
                if self.continuous:self._stop.wait(60);continue
                break
            energy,maximum=read_adventure_energy(live)
            cost=target["energy_cost"]
            if energy is None or cost is None:
                self._emit("adventure_paused",message="Current Adventure energy or exact stage cost is unavailable; no fight was started.");break
            self._emit("adventure_energy",current=energy,max=maximum,reserve=self.reserve)
            if energy<cost+self.reserve or self.energy_budget is not None and spent+cost>self.energy_budget:
                self._emit("log",message=f"Adventure needs {cost} energy and reserve {self.reserve}; keeping the completion route for the next run.")
                if self.continuous and self.energy_budget is None:self._stop.wait(60);continue
                break
            if self._stop.is_set():break
            self._emit("adventure_target",mission_id=target["mission"],campaign_id=target["campaign_id"],island=target["island"],
                       stage=target["stage"],wins=target["wins"],target_wins=target["target_wins"],energy_cost=cost,farming=farming)
            self._pending={"mission_id":target["mission"],"campaign_id":target["campaign_id"],"wins_before":target["wins"],
                           "phase":"start_pending","cost":cost,"farming":farming,"server_time":live["time"]}
            self._save_pending()
            if self._stop.is_set():self._journal.save(None);break
            response=self._client.call("startMission",{"mission_id":str(target["mission"]),"auto_battle":"0"})
            marker=response.get("result") if isinstance(response,dict) else None
            accepted=(marker is True or type(marker) is int and marker==1 or isinstance(marker,str) and marker.casefold() in {"true","1"})
            state=response.get("battle_data") if isinstance(response,dict) and accepted and not api_rejected(response) else None
            if not isinstance(state,dict) or not state.get("battle_id"):
                refused=marker is False or type(marker) is int and marker==0 or isinstance(marker,str) and marker.casefold() in {"false","0"}
                if refused and not response.get("battle_data"):
                    self._journal.save(None)
                self._emit("adventure_paused",message="Adventure start returned no accepted battle state. No browser replay or second start was attempted.");break
            n+=1;spent+=cost;self._pending["phase"]="battle_ready";self._save_pending()
            if state.get("host_id") in (None,""):
                self._emit("adventure_paused",message="The accepted Adventure battle has no confirmed owner; the pending fight was preserved.");break
            opponent=self._client.opponent_name(state);self._emit("battle_start",number=n,opponent=opponent)
            won,moves=self._drive_completion(state,n)
            after=mission_progress(self._client.init(),target["mission"],target["campaign_id"])
            before=target["wins"];advanced=after["wins"]>before
            if after["wins"]<before or won is False and advanced or not farming and (won is True and not advanced or won is None and not advanced):
                self._emit("adventure_paused",message="Adventure ended without consistent fresh stage progress. The pending fight is preserved; no further energy was spent.");break
            if won is None and advanced:won=True
            if not isinstance(won,bool):
                self._emit("adventure_paused",message="The Adventure outcome could not be confirmed; no following fight was started.");break
            self._journal.save(None);self._pending=None
            self._emit("battle_end",number=n,opponent=opponent,won=won,moves=moves)
            self._emit("adventure_completion_verified",mission_id=target["mission"],wins_before=before,wins_after=after["wins"],
                       target_wins=target["target_wins"],island=target["island"],stage=target["stage"])
            losses=0 if won else losses+1
            if losses>=3:self._emit("adventure_paused",message="Three consecutive Adventure losses; pausing the completion route to preserve energy.");break
            if not self._stop.is_set() and (self.battle_limit==0 or n<self.battle_limit):self._stop.wait(self.battle_delay)
        if not self._stop.is_set() and (self._pending is None or self._journal.read() is None):
            try:self._scan(self._client.init())
            except Exception:pass
        self._emit("finished",stopped=self._stop.is_set(),completed_battles=n,energy_spent=spent)
