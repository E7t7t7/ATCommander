from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import re

OFFENSE={"berserk":13,"crazed":13,"strike":10,"punch":10,"burn":7,"gas":6,"bomb":5,"motivate":2,"cheer":1.8,"rally":1.8,"rallyall":1.8}
DEFENSE={"leech":4,"hijack":3,"bodyguard":3,"bunker":3,"sturdy":3,"armored":2.5,"shield":2,"barrier":2,"barrierall":2,"heal":2,"healall":2,"weaken":1.5,"weakenall":1.5,"counter":1.5,"outlast":1.5,"enlarge":1}

@dataclass(slots=True)
class PilotDecision:
    action:str; card_uid:int; card_uid2:int|None=None; score:float=0.0; reason:str=""

class SingleComboRushPolicy:
    """Complete combos immediately; choose an item or character to open a lane."""
    def __init__(self,style:str="default",*,character_first:bool=False):
        self.style=(style or "default").lower();self.character_first=bool(character_first)
    def choose(self,b:dict[str,Any])->PilotDecision|None:
        live=b.get("available_combo_data") or []
        if isinstance(live,list):
            opts=[]
            for c in live:
                if not isinstance(c,dict):continue
                u1=self._int(c.get("card_uid1"));u2=self._int(c.get("card_uid2"))
                if u1 is None or u2 is None:continue
                hand,board=self._split(c,b,u1,u2); opts.append(PilotDecision("combo",hand,board,self._combo_score(c),"complete available combo immediately"))
            if opts:return max(opts,key=lambda d:(d.score,-d.card_uid))
        hand=b.get("available_hand_combo_data") or []; cmap=b.get("card_map") or {}
        scores={}
        if isinstance(hand,list):
            for c in hand:
                if not isinstance(c,dict):continue
                uid=self._character_uid(c,cmap) if self.character_first else self._item_uid(c,cmap)
                if uid is None:continue
                card=cmap.get(str(uid)) or cmap.get(uid) or {}; s=self._card_score(card)+self._combo_score(c)*.18
                scores[uid]=max(scores.get(uid,-1e9),s)
        if scores:
            uid,s=max(scores.items(),key=lambda kv:(kv[1],-kv[0]));return PilotDecision("play",uid,None,s+250,"open strongest character lane" if self.character_first else "open strongest item lane")
        auto=b.get("auto_play")
        if isinstance(auto,(list,tuple)) and auto:
            uid=self._int(auto[0]); target=self._int(auto[1]) if len(auto)>1 else None
            if uid is not None:return PilotDecision("combo" if target not in (None,0) else "play",uid,None if target in (None,0) else target,-1,"AT legal-action fallback")
        return None
    def _weights(self):
        if "durable" in self.style or "survival" in self.style:return (2.4,2.2,0.65,1.65)
        if "aggressive" in self.style or "kill" in self.style:return (5.2,.55,1.45,.3)
        return (4.0,1.0,1.0,.65)
    def _skill(self,skills,off_mult=1,def_mult=1):
        if not isinstance(skills,dict):return 0.0
        total=0.0
        for n,v in skills.items():
            name=str(n).lower().replace(" ","_").replace("-","_"); val=self._num(v.get("x"),v.get("value"),1) if isinstance(v,dict) else self._num(v,1)
            total+=(OFFENSE.get(name,0)*off_mult+DEFENSE.get(name,.25)*def_mult)*max(1,val)
        return total
    def _combo_score(self,c):
        stats=c.get("stats") or {}; status=c.get("status") or {}; aw,hw,om,dm=self._weights(); a=self._num(status.get("attack"),stats.get("attack"));h=self._num(status.get("health"),stats.get("health"));p=self._num(stats.get("power"),c.get("power"));return a*aw+h*hw+p*.15+self._skill(stats.get("skills") or {},om,dm)
    def _card_score(self,c):
        aw,hw,om,dm=self._weights();return self._num(c.get("attack"))*aw*.8+self._num(c.get("health"))*hw+self._skill(c.get("skills") or {},om,dm)
    def _item_uid(self,c,cmap):
        u1=self._int(c.get("card_uid1"));u2=self._int(c.get("card_uid2"));
        if u1 is None or u2 is None:return None
        cards=c.get("cards") or {}; item=self._int(cards.get("card2")) if isinstance(cards,dict) else None
        if item is not None:
            for u in (u1,u2):
                card=cmap.get(str(u)) or cmap.get(u) or {}
                if self._int(card.get("unit_id"))==item:return u
        return u2
    def _character_uid(self,c,cmap):
        u1=self._int(c.get("card_uid1"));u2=self._int(c.get("card_uid2"))
        if u1 is None or u2 is None:return None
        cards=c.get("cards") or {};character=self._int(cards.get("card1")) if isinstance(cards,dict) else None
        # A native recipe's card1 is its character input. Resolve its actual
        # battle UID, since the two UID fields can be presented in either order.
        if character is not None:
            for uid in (u1,u2):
                card=cmap.get(str(uid)) or cmap.get(uid) or {}
                if self._int(card.get("unit_id"))==character:return uid
        return None
    def _split(self,c,b,u1,u2):
        played=self._played(b)
        if u1 in played and u2 not in played:return u2,u1
        if u2 in played and u1 not in played:return u1,u2
        if self.character_first:
            # Prefer explicit hand/board evidence over the opening preference.
            # This also handles a game fallback that had to open an item lane.
            raw=b.get("hand") or [];hand=set()
            if isinstance(raw,list):
                for card in raw:
                    uid=self._int(card.get("card_uid",card.get("uid"))) if isinstance(card,dict) else self._int(card)
                    if uid is not None:hand.add(uid)
            if u1 in hand and u2 not in hand:return u1,u2
            if u2 in hand and u1 not in hand:return u2,u1
            auto=b.get("auto_play")
            if isinstance(auto,(list,tuple)) and len(auto)>1:
                source,target=self._int(auto[0]),self._int(auto[1])
                if target not in (None,0) and {source,target}=={u1,u2}:return source,target
        cmap=b.get("card_map") or {}; cards=c.get("cards") or {}; item=self._int(cards.get("card2")) if isinstance(cards,dict) else None
        if item is not None:
            c1=cmap.get(str(u1)) or cmap.get(u1) or {};c2=cmap.get(str(u2)) or cmap.get(u2) or {}
            if self._int(c1.get("unit_id"))==item:return (u1,u2) if self.character_first else (u2,u1)
            if self._int(c2.get("unit_id"))==item:return (u2,u1) if self.character_first else (u1,u2)
        return u1,u2
    def _played(self,b):
        out=set(); turns=b.get("turn") or {}
        if isinstance(turns,dict):
            for p in turns.values():
                for x in (p.get("plays") or []) if isinstance(p,dict) else []:
                    if isinstance(x,dict):
                        for k in ("card1_uid","card2_uid"):
                            u=self._int(x.get(k));
                            if u is not None and u>=100:out.add(u)
        return out
    @staticmethod
    def _int(v):
        try:return None if v in (None,"") else int(v)
        except:return None
    @staticmethod
    def _num(*vs):
        for v in vs:
            try:
                if v is not None:return float(v)
            except:pass
        return 0.0

def is_melee_event(name:Any)->bool:
    return isinstance(name,str) and re.search(r"\bmelee\b",name,re.IGNORECASE) is not None

def policy_for_label(label:str,*,character_first:bool=False)->SingleComboRushPolicy:
    x=(label or "").lower()
    if "aggressive" in x or "fastest" in x:return SingleComboRushPolicy("aggressive",character_first=character_first)
    if "durable" in x or "survival" in x:return SingleComboRushPolicy("durable",character_first=character_first)
    return SingleComboRushPolicy("default",character_first=character_first)
