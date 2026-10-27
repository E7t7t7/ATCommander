from __future__ import annotations
import threading, time
from typing import Any, Callable
from .api import ATClient, ATApiError
from .credentials import load_credentials
from .strategy import policy_for_label,is_melee_event
from .protocol_config import load_profiles
from .persistent_stats import LifetimeTracker
from .preflight import ad_preflight
from .challenge_state import current_challenge

class EventBattleRunner:
    """Rumble profile driver and Siege driver with direct signed starts."""
    def __init__(self, mode: str, move_delay: float=.10, battle_delay: float=1.5,
                 defense_deck_id: str|None=None, island_id: str|None=None, logic: str="Item → Combo (default)",
                 offense_deck_id: str|None=None, offense_hero_id: str|None=None,
                 auto_watch_ads: bool=False, event: Callable[[dict[str,Any]],None]|None=None,
                 siege_target_slots: list[str]|None=None, siege_fallback_any: bool=True, siege_poll_seconds: int=60):
        self.mode=mode
        self.move_delay=max(.05,float(move_delay)); self.battle_delay=max(.1,float(battle_delay))
        self.defense_deck_id=defense_deck_id or None; self.island_id=island_id or None
        self.offense_deck_id=offense_deck_id or None; self.offense_hero_id=offense_hero_id or None; self.auto_watch_ads=bool(auto_watch_ads)
        self.siege_target_slots=[str(x) for x in (siege_target_slots or []) if str(x)]
        self.siege_fallback_any=bool(siege_fallback_any); self.siege_poll_seconds=max(10,int(siege_poll_seconds or 60))
        self.event=event or (lambda _e:None); self.policy=policy_for_label(logic); self.logic=logic; self.lifetime=LifetimeTracker()
        self._stop=threading.Event(); self._pause=threading.Event(); self._thread=None; self._client=None; self._siege_rejected_targets=set()
    @property
    def running(self): return bool(self._thread and self._thread.is_alive())
    def start(self):
        if self.running:return
        self._stop.clear(); self._thread=threading.Thread(target=self._run,daemon=True); self._thread.start()
    def stop(self): self._stop.set(); self._pause.clear()
    def pause(self,p=True): self._pause.set() if p else self._pause.clear()
    def _emit(self,k,**kw):
        try:self.event({"kind":k,"mode":self.mode,**kw})
        except Exception:pass
    def _wait(self):
        while self._pause.is_set() and not self._stop.is_set(): time.sleep(.1)
        return not self._stop.is_set()
    @staticmethod
    def _siege_status_rows(response):
        st=(response or {}).get("guild_siege_status") or response or {}
        raw=st.get("enemy_locations") or {}
        rows=raw.values() if isinstance(raw,dict) else raw if isinstance(raw,list) else []
        out=[]
        for r in rows:
            if not isinstance(r,dict): continue
            slot=str(r.get("slot_id") or r.get("id") or "")
            if not slot: continue
            meta=r.get("data") if isinstance(r.get("data"),dict) else {}
            try: hp=int(r.get("hp") or 0)
            except Exception: hp=0
            users=r.get("users") if isinstance(r.get("users"),list) else []
            out.append({"slot_id":slot,"location_id":str(r.get("location_id") or meta.get("id") or ""),"name":str(meta.get("name") or f"Island {slot}"),"hp":hp,
                        "faction_id":str(r.get("faction_id") or ""),"battleground_id":str(meta.get("battleground_id") or ""),"users":users})
        return st,out

    def _choose_siege_target(self, rows):
        alive={str(r.get("slot_id")):r for r in rows if int(r.get("hp") or 0)>0}
        for slot in self.siege_target_slots:
            if str(slot) in alive:return alive[str(slot)]
        if self.siege_fallback_any and alive:
            return sorted(alive.values(), key=lambda r:int(r.get("slot_id") or 999))[0]
        return None

    def _try_siege_start(self,c,st,target):
        """Use the signed slot request only with fresh attack-phase target data."""
        if st.get("result") is False:return {},None
        slot=str(target.get("slot_id") or "")
        try:
            phase=int(st.get("phase"));attacks=int(st.get("num_attacks") or 0)
            valid_slot=int(slot)>0
        except (TypeError,ValueError):return {},None
        _,rows=self._siege_status_rows(st)
        current=next((row for row in rows if row["slot_id"]==slot and row["hp"]>0),None)
        if phase!=2 or attacks<=0 or not valid_slot or current is None:
            return {},None
        round_key=(str(st.get("event_id") or st.get("id") or ""),str(st.get("round_id") or ""),
                   str(st.get("phase_end_time") or ""),str(st.get("enemy_faction_id") or ""),slot)
        if round_key in self._siege_rejected_targets:return {},None
        response=c.start_siege(slot)
        state=c.extract_battle_data(response) if isinstance(response,dict) and response.get("result") is not False else None
        if state and state.get("battle_id"):
            return response,state
        # warning_16 is a rejected duplicate/refresh response. A fresh status and
        # the normal poll cooldown can recover it; other uncertain starts must
        # not spend a second attack against the same round/target.
        def warning16(value):
            if isinstance(value,str):return "warning_16" in value.lower()
            if isinstance(value,dict):return any(warning16(item) for item in value.values())
            if isinstance(value,list):return any(warning16(item) for item in value)
            return False
        cooldown_retry=isinstance(response,dict) and response.get("result") is False and warning16(response.get("result_message"))
        if cooldown_retry:
            self._emit("log",message="Siege start refresh was rejected; waiting for the next status poll before retrying.")
        else:
            self._siege_rejected_targets.add(round_key)
            self._emit("log",message=f"Signed Siege start returned no battle state for slot {slot}; further starts against this round/target are paused.")
        return response,None


    def _run(self):
        try:
            c=ATClient(load_credentials()); self._client=c; init=c.init(); ad_preflight(c, init, lambda m:self._emit("log",message=m), auto_watch=self.auto_watch_ads); key=self.mode.lower()
            if self.offense_deck_id or self.offense_hero_id:
                c.apply_selection(deck_id=self.offense_deck_id, hero_id=self.offense_hero_id)
                self._emit("log",message=f"{self.mode} offense deck/hero applied before battle run.")
            if key=="siege":
                if self.defense_deck_id:
                    try:
                        c.set_siege_defense_deck(self.defense_deck_id); self._emit("log",message="Siege defense deck applied with confirmed setDeckType call.")
                    except Exception as exc:
                        self._emit("log",message=f"Siege defense deck could not be changed (possibly already live): {exc}. Attack monitor will continue.")
                if self.island_id:
                    try:
                        c.set_siege_defense_island(self.island_id); self._emit("log",message=f"Siege defense island {self.island_id} selected.")
                    except Exception as exc:
                        self._emit("log",message=f"Siege defense island could not be changed (possibly already live): {exc}. Attack monitor will continue.")
                n=0; last_wait=""; self._emit("status",text="Siege armed / monitoring")
                while not self._stop.is_set():
                    if not self._wait():break
                    status=c.get_guild_siege_status()
                    if not isinstance(status,dict) or status.get("result") is False:
                        msg="Siege status refresh was rejected or unavailable; waiting for a fresh status before considering an attack."
                        if msg!=last_wait:self._emit("log",message=msg);last_wait=msg
                        self._stop.wait(self.siege_poll_seconds);continue
                    st, enemy=self._siege_status_rows(status)
                    if st.get("result") is False:
                        self._stop.wait(self.siege_poll_seconds);continue
                    try: phase=int(st.get("phase")) if st.get("phase") is not None else None
                    except Exception: phase=None
                    try: attacks=int(st.get("num_attacks") or 0)
                    except Exception: attacks=0
                    target=self._choose_siege_target(enemy)
                    # The production phase enum identifies phase 2 as Battle,
                    # phase 3 as Scoring and phase 4 as Ended.
                    if phase in (3,4):
                        self._emit("log",message="Siege battle phase has finished; armed run complete.");break
                    if phase!=2:
                        msg=f"Siege armed: phase {phase}; waiting for the attack phase."
                        if msg!=last_wait:self._emit("log",message=msg);last_wait=msg
                        self._stop.wait(self.siege_poll_seconds);continue
                    if attacks<=0:
                        self._emit("log",message="Siege is live but no attacks remain; run complete.");break
                    if not target:
                        self._emit("log",message="Siege is live but A/B/C are down and no fallback target is available; run complete.");break
                    last_wait=""
                    slot=str(target.get("slot_id"))
                    self._emit("log",message=f"Siege target: {target.get('name')} (slot {slot}, HP {target.get('hp')}).")
                    response,state=self._try_siege_start(c,st,target)
                    if not state or not state.get("battle_id"):
                        self._stop.wait(self.siege_poll_seconds);continue
                    n+=1;opp=c.opponent_name(state);self._emit("battle_start",number=n,opponent=opp)
                    won,moves=self._drive(c,state,n,opp);self.lifetime.record_battle(self.mode,won);self._emit("battle_end",number=n,opponent=opp,won=won,moves=moves,lifetime=self.lifetime.to_dict());time.sleep(self.battle_delay)
                self._emit("finished",stopped=self._stop.is_set(),lifetime=self.lifetime.to_dict());return

            profiles=load_profiles(); start_profile=profiles.get(key)
            if not start_profile: raise ATApiError(f"{self.mode} protocol has not been learned yet.")
            if self.defense_deck_id:
                p=profiles.get(f"{key}_defense")
                if not p: raise ATApiError(f"{self.mode} defense-deck protocol has not been learned yet.")
                c.call_profile(p,{"deck_id":self.defense_deck_id,"defense_deck_id":self.defense_deck_id})
                self._emit("log",message=f"{self.mode} defense deck applied.")
            n=0; self._emit("status",text=f"{self.mode} running")
            while not self._stop.is_set():
                if not self._wait():break
                response=c.call_profile(start_profile,{"deck_id":self.defense_deck_id or ""})
                state=c.extract_battle_data(response)
                if not state or not state.get("battle_id"):
                    self._emit("log",message=f"No more {self.mode} battle_data returned — event run complete."); break
                n+=1; opp=c.opponent_name(state); self._emit("battle_start",number=n,opponent=opp)
                won,moves=self._drive(c,state,n,opp); self.lifetime.record_battle(self.mode,won)
                self._emit("battle_end",number=n,opponent=opp,won=won,moves=moves,lifetime=self.lifetime.to_dict())
                time.sleep(self.battle_delay)
            self._emit("finished",stopped=self._stop.is_set(),lifetime=self.lifetime.to_dict())
        except Exception as e:self._emit("fatal",message=str(e),lifetime=self.lifetime.to_dict())
    def _drive(self,c,state,n,opp):
        bid=state.get("battle_id"); hid=state.get("host_id"); cur=state
        for move in range(1,31):
            if not self._wait(): return None,move-1
            d=self.policy.choose(cur)
            if not d: raise ATApiError("No legal event battle play could be derived.")
            self._emit("move",number=n,move=move,opponent=opp,action=d.action,card_uid=d.card_uid,card_uid2=d.card_uid2,reason=d.reason)
            r=c.play_battlefield_card(battle_id=bid,host_id=hid,card_uid=d.card_uid,card_uid2=d.card_uid2)
            nxt=c.extract_battle_data(r)
            if c.battle_finished(r,nxt): return c.extract_outcome(r,nxt),move
            cur=nxt or cur; bid=cur.get("battle_id",bid); hid=cur.get("host_id",hid); time.sleep(self.move_delay)
        raise ATApiError("Event battle exceeded 30 moves.")

class ChallengeBattleRunner:
    """Drive a selected Challenge/Melee/Clash through fresh signed API starts."""
    def __init__(self, event_id:str, challenge_id:str, name:str, move_delay:float=.10, battle_delay:float=1.5,
                 logic:str="Item → Combo (default)", offense_deck_id:str|None=None, offense_hero_id:str|None=None,
                 auto_watch_ads:bool=False, event:Callable[[dict[str,Any]],None]|None=None, level:int|None=None,
                 melee_character_first:bool=False):
        self.event_id=str(event_id);self.challenge_id=str(challenge_id or event_id);self.name=name;self.level=int(level) if level is not None else None
        self.logic=logic;self.melee_character_first=bool(melee_character_first)
        self.move_delay=max(.05,float(move_delay));self.battle_delay=max(.1,float(battle_delay));self.policy=policy_for_label(logic,character_first=self.melee_character_first and is_melee_event(name))
        self.offense_deck_id=offense_deck_id or None;self.offense_hero_id=offense_hero_id or None;self.auto_watch_ads=bool(auto_watch_ads)
        self.event=event or (lambda _e:None);self._stop=threading.Event();self._thread=None;self.lifetime=LifetimeTracker()
    @property
    def running(self):return bool(self._thread and self._thread.is_alive())
    def start(self):
        if self.running:return
        self._stop.clear();self._thread=threading.Thread(target=self._run,daemon=True);self._thread.start()
    def stop(self):self._stop.set()
    def _emit(self,k,**kw):
        try:self.event({"kind":k,"mode":"Challenge",**kw})
        except Exception:pass
    def _drive(self,c,state,n):
        bid=state.get("battle_id");hid=state.get("host_id");cur=state
        for move in range(1,31):
            if self._stop.is_set():return None,move-1
            d=self.policy.choose(cur)
            if not d:raise ATApiError("No legal challenge play could be derived.")
            self._emit("move",number=n,move=move,action=d.action,card_uid=d.card_uid,card_uid2=d.card_uid2,reason=d.reason)
            r=c.play_battlefield_card(battle_id=bid,host_id=hid,card_uid=d.card_uid,card_uid2=d.card_uid2);nxt=c.extract_battle_data(r)
            if c.battle_finished(r,nxt):return c.extract_outcome(r,nxt),move
            cur=nxt or cur;bid=cur.get("battle_id",bid);hid=cur.get("host_id",hid);time.sleep(self.move_delay)
        raise ATApiError("Challenge battle exceeded 30 moves.")
    def _run(self):
        try:
            c=ATClient(load_credentials());init=c.init();ad_preflight(c,init,lambda m:self._emit("log",message=m),auto_watch=self.auto_watch_ads)
            if self.offense_deck_id or self.offense_hero_id:
                c.apply_selection(deck_id=self.offense_deck_id,hero_id=self.offense_hero_id);self._emit("log",message="Challenge offense deck/hero applied.")
            n=0;self._emit("status",text=f"{self.name} running")
            while not self._stop.is_set():
                # Each fight must use the current event's own challenge ID and
                # level. A failed refresh must never reuse the previous fight.
                try:
                    live=current_challenge(c.init(),self.event_id)
                except Exception:live=None
                if live is None:
                    self._emit("log",message=f"{self.name}: current active event, challenge level, or energy could not be confirmed; no fight started.");break
                if self.melee_character_first:
                    self.policy=policy_for_label(self.logic,character_first=is_melee_event(live.get("event_name")))
                self.challenge_id=live["challenge_id"];self.level=live["level"]
                cur=live["current"];cost=live["cost"]
                if cur<cost:
                    self._emit("log",message=f"{self.name}: challenge energy {cur}/{live['max'] if live['max'] is not None else '—'}; no fight started.");break
                r=c.start_challenge(self.challenge_id,self.level,auto_battle=False)
                st=c.extract_battle_data(r) if isinstance(r,dict) and r.get("result") is not False else None
                if not st or not st.get("battle_id"):
                    self._emit("log",message=f"{self.name}: signed startChallenge returned no battle state; stopped without retrying the start.");break
                n+=1;opp=c.opponent_name(st);self._emit("battle_start",number=n,opponent=opp)
                won,moves=self._drive(c,st,n);self.lifetime.record_battle("Challenge",won);self._emit("battle_end",number=n,opponent=opp,won=won,moves=moves,lifetime=self.lifetime.to_dict())
                self._stop.wait(self.battle_delay)
            self._emit("finished",stopped=self._stop.is_set(),lifetime=self.lifetime.to_dict())
        except Exception as e:self._emit("fatal",message=str(e),lifetime=self.lifetime.to_dict())
