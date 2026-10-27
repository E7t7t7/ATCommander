from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any
import math, re, statistics, time

# Human-facing names. AT's static data uses the engine identifiers on the left.
SKILL_NAMES={
    "armored":"Sturdy","backslap":"Backslap","barrier":"Shield","barrierall":"Shield All",
    "berserk":"Craze","bodyguard":"Bodyguard","bunker":"Bunker","burn":"Burn","counter":"Payback",
    "enlarge":"Enlarge","enrage":"Enrage","give":"Give","giveall":"Give All","globalhealth":"Global Health",
    "heal":"Heal","healall":"Heal All","hijack":"Hijack","inspire":"Motivate","invigorate":"Boost",
    "leech":"Leech","outlast":"Recover","pierce":"Jab","poison":"Gas","rally":"Cheer","rallyall":"Cheer All",
    "shrapnel":"Bomb","strike":"Punch","weaken":"Cripple","weakenall":"Cripple All",
}

OFFENSE={"berserk","strike","burn","poison","pierce","shrapnel","rally","rallyall","inspire","invigorate"}
DEFENSE={"armored","barrier","barrierall","bodyguard","bunker","counter","hijack","weaken","weakenall","outlast","enlarge"}
SUSTAIN={"leech","outlast","heal","healall","bunker","armored","barrier","bodyguard"}
PREMIUM_SYNERGY=(
    ({"berserk","strike"},0.8,"Craze + Punch gives the combo real closing pressure."),
    ({"berserk","burn"},0.65,"Craze + Burn scales pressure while still pushing damage immediately."),
    ({"berserk","poison"},0.65,"Craze + Gas is a strong snowball/damage pairing."),
    ({"berserk","leech"},0.55,"Craze + Leech helps it snowball without giving up sustain."),
    ({"strike","poison"},0.45,"Punch + Gas gives it two useful damage channels."),
    ({"hijack","bunker"},0.45,"Hijack + Bunker is a useful anti-snowball defensive shell."),
    ({"hijack","armored"},0.35,"Hijack + Sturdy is a clean defensive pairing."),
    ({"weakenall","barrierall"},0.4,"Cripple All + Shield All gives broad board control."),
)

@dataclass
class CMReview:
    combo_id:str
    name:str
    trait:str
    score:float
    recommendation:str
    summary:str
    skills:list[str]
    reasons:list[str]
    cautions:list[str]
    pc_present:bool=False
    current_cm:int|None=None
    mastery_tokens:int|None=None
    currency:str=""
    cost:int|None=None
    source:str="Live AT static data"
    confidence:str="Medium"
    variant_count:int=1
    mastery_stones:int|None=None
    data_score:float|None=None
    community_score:float|None=None
    community_count:int=0
    community_confidence:str=""
    community_note:str=""
    community_sources:list[str]|None=None

    def as_dict(self)->dict[str,Any]:
        return asdict(self)

    def compact(self)->str:
        cost=""
        if self.cost is not None:
            cost=f" • {self.cost:,} {self.currency or 'currency'}"
        cm=f" • current CM{self.current_cm}" if self.current_cm is not None else ""
        balance=(f" • {self.mastery_stones:,} mastery stones on account" if self.mastery_stones is not None else "")
        pc=" • PC" if self.pc_present else ""
        skilltxt=", ".join(self.skills) if self.skills else "skills unavailable"
        why=" ".join(self.reasons[:3])
        caut=" ".join(self.cautions[:2])
        breadth=f" • {self.variant_count} static variant{'s' if self.variant_count!=1 else ''}"
        consensus=""
        if self.community_score is not None:
            base=self.data_score if self.data_score is not None else self.score
            consensus=(f"\nCurrent-data model: {base:.1f}/10 • Recent Reddit consensus: "
                       f"{self.community_score:.1f}/10 from {self.community_count} explicit rating"
                       f"{'s' if self.community_count!=1 else ''} ({self.community_confidence or 'low'} confidence)")
        return (f"{self.name} — {self.trait or 'Trait unknown'}{pc}{breadth}\n"
                f"Rating: {self.score:.1f}/10 • {self.recommendation}{cm}{cost}{balance}\n"
                f"Skills: {skilltxt}\n{self.summary}{consensus}\n{why}" + (f"\nCaution: {caut}" if caut else ""))


def _num(v:Any)->float|None:
    try:
        if v is None or v=="": return None
        return float(v)
    except Exception:return None


def _norm_name(v:Any)->str:
    return re.sub(r"\s+"," ",str(v or "").strip())


def _traits(row:dict[str,Any])->list[str]:
    raw=row.get("trait") or row.get("traits") or row.get("trait_ids") or []
    if isinstance(raw,str): raw=[x for x in re.split(r"[,/|]",raw) if x.strip()]
    if isinstance(raw,dict): raw=list(raw.values())
    out=[]
    if isinstance(raw,list):
        for x in raw:
            if isinstance(x,dict): x=x.get("name") or x.get("id")
            s=_norm_name(x)
            if s and s not in out: out.append(s)
    return out


def _skill_rows(row:dict[str,Any])->list[dict[str,Any]]:
    """Return the best/max-level skill rows we can infer from AT card_data.

    Static data has changed shape several times. Some rows expose the current
    skill list directly, while others keep skill changes in upgrade[]. We merge
    by skill id and keep the last/max-level occurrence instead of assuming one
    schema.
    """
    merged:dict[str,dict[str,Any]]={}
    def absorb(raw:Any):
        vals=raw.values() if isinstance(raw,dict) else raw if isinstance(raw,list) else []
        for s in vals:
            if not isinstance(s,dict):continue
            sid=str(s.get("id") or s.get("skill_id") or s.get("name") or "").strip().lower()
            if not sid:continue
            # Give can wrap another real skill in the s field.
            if sid in {"give","giveall"} and s.get("s"):
                sid=str(s.get("s")).strip().lower()
            merged[sid]=dict(s)
    absorb(row.get("skill") or row.get("skills"))
    ups=row.get("upgrade") or row.get("upgrades") or []
    if isinstance(ups,dict): ups=list(ups.values())
    if isinstance(ups,list):
        try: ups=sorted([u for u in ups if isinstance(u,dict)],key=lambda u:float(u.get("level") or 0))
        except Exception: pass
        for u in ups: absorb(u.get("skill") or u.get("skills"))
    return [{**v,"_id":k} for k,v in merged.items()]


def _skill_ids(row:dict[str,Any])->list[str]:
    return [str(s.get("_id") or "").lower() for s in _skill_rows(row) if s.get("_id")]


def _skill_value(s:dict[str,Any])->float|None:
    # Combo definitions often use v, normal cards often use x. Keep whichever
    # magnitude AT exposes instead of manufacturing a precise max-level value.
    for k in ("x","v","value","amount"):
        n=_num(s.get(k))
        if n is not None:return n
    return None


def _percentile_in(values:list[float],value:float|None)->float|None:
    if value is None or not values:return None
    a=sorted(x for x in values if x is not None)
    if not a:return None
    below=sum(1 for x in a if x<value);equal=sum(1 for x in a if x==value)
    return 100.0*(below+0.5*equal)/len(a)


def _skill_tier(percentile:float|None)->str:
    # These labels intentionally describe the value relative to other combos
    # carrying the SAME skill.  That is much more useful than calling a combo
    # strong merely because it has three desirable skill names.
    if percentile is None:return ""
    if percentile>=80:return "High"
    if percentile>=65:return "Medium/High"
    if percentile>=50:return "Medium"
    if percentile>=35:return "Medium/Low"
    return "Low"


def _is_combo(row:dict[str,Any])->bool:
    ct=str(row.get("card_type") or row.get("category") or "").lower()
    st=str(row.get("set") or row.get("set_id") or "")
    # Final-form/PC rows can live in numeric ranges that otherwise resemble old
    # combo IDs, so exclude them before the ID-band fallback.
    if ct in {"final_form","pc","power_card"} or st in {"3001","3002"}:return False
    if ct in {"combo","combined"}:return True
    if st in {"1001","1002"}:return True
    # The franchise combo ID bands are another fallback for old card_data.
    try:
        rid=int(row.get("id") or row.get("unit_id"))
        return any(lo<=rid<=hi for lo,hi in ((15000,19999),(25000,29999),(35000,39999),(45000,49999),(55000,59999),(75000,79999)))
    except Exception:return False


def _is_pc(row:dict[str,Any])->bool:
    ct=str(row.get("card_type") or row.get("category") or "").lower()
    st=str(row.get("set") or row.get("set_id") or "")
    return ct in {"final_form","pc","power_card"} or st in {"3001","3002"}


def _max_stat(row:dict[str,Any],key:str)->float|None:
    vals=[]
    n=_num(row.get(key))
    if n is not None:vals.append(n)
    ups=row.get("upgrade") or row.get("upgrades") or []
    if isinstance(ups,dict):ups=list(ups.values())
    if isinstance(ups,list):
        for u in ups:
            if isinstance(u,dict):
                n=_num(u.get(key))
                if n is not None:vals.append(n)
    return max(vals) if vals else None


def _power_metric(row:dict[str,Any])->float|None:
    p=_max_stat(row,"power")
    if p is not None and p>0:return p
    # Combo definitions use multipliers rather than literal stats. Treat their
    # skill v/x magnitudes as a relative power signal only.
    vals=[_skill_value(s) for s in _skill_rows(row)]
    vals=[x for x in vals if x is not None]
    mults=[_num(row.get("attack_multiplier")),_num(row.get("health_multiplier"))]
    mults=[x for x in mults if x is not None]
    if vals:
        # The combo XML exposes skill scaling (v) plus attack/health multipliers.
        # Blend both only as a *relative* current-pool signal; the displayed
        # review never pretends this number is an in-game card power stat.
        return statistics.mean(vals)+(10.0*statistics.mean(mults) if mults else 0.0)
    if mults:return 10.0*statistics.mean(mults)
    return None


def _recipe_ids(row:dict[str,Any])->set[str]:
    ids=set()
    keys=("character_id","character","input_1","input_2","card_id_1","card_id_2","card1","card2","unit_id_1","unit_id_2")
    for k in keys:
        v=row.get(k)
        if isinstance(v,(int,str)) and str(v).isdigit():ids.add(str(v))
    for k in ("recipe","recipes","combo_recipe","inputs","input_cards"):
        v=row.get(k)
        if isinstance(v,dict):v=list(v.values())
        if isinstance(v,list):
            for x in v:
                if isinstance(x,dict):
                    for kk in ("id","unit_id","card_id","input_id"):
                        z=x.get(kk)
                        if z is not None and str(z).isdigit():ids.add(str(z))
                elif str(x).isdigit():ids.add(str(x))
    return ids


def _current_mastery(init_data:dict[str,Any]|None,combo_id:str)->tuple[int|None,int|None]:
    data=init_data or {}; target=str(combo_id)
    containers=[]
    # AT has exposed mastery as both keyed dictionaries and row lists.  Collect
    # likely containers recursively instead of assuming one revision forever.
    def walk(o:Any,depth:int=0):
        if depth>7:return
        if isinstance(o,dict):
            for k,v in o.items():
                lk=str(k).lower()
                if "combo" in lk and "master" in lk:containers.append(v)
                if isinstance(v,(dict,list)):walk(v,depth+1)
        elif isinstance(o,list):
            for v in o:
                if isinstance(v,(dict,list)):walk(v,depth+1)
    walk(data)
    containers.insert(0,data.get("user_combo_mastery") or {})
    level=None;tok=None
    def consume(row:Any):
        nonlocal level,tok
        if isinstance(row,dict):
            for k in ("level","mastery_level","cm_level","mastery"):
                try:
                    if row.get(k) is not None:level=int(row.get(k));break
                except Exception:pass
            for k in ("tokens","token_count","mastery_tokens","count","quantity"):
                try:
                    if row.get(k) is not None:tok=int(row.get(k));break
                except Exception:pass
        elif row not in (None,""):
            try:level=int(row)
            except Exception:pass
    for cm in containers:
        if level is not None:break
        if isinstance(cm,dict):
            row=cm.get(target)
            if row is None and target.isdigit():row=cm.get(int(target))
            if row is not None:consume(row);continue
            vals=cm.values()
        elif isinstance(cm,list): vals=cm
        else: continue
        for row in vals:
            if not isinstance(row,dict):continue
            rid=None
            for k in ("combo_id","combo_card_id","card_id","unit_id","mastery_id","id"):
                if row.get(k) is not None:
                    rid=str(row.get(k));break
            if rid==target:
                consume(row);break
    # Loose CM tokens use item_id = '2' + combo_id in current init revisions.
    if tok is None:
        items=data.get("user_items") or {}
        try:token_id=f"2{int(target):06d}"
        except Exception:token_id="2"+target
        ir=None
        if isinstance(items,dict):
            ir=items.get(token_id)
            if ir is None and token_id.isdigit():ir=items.get(int(token_id))
        if isinstance(ir,dict):
            for k in ("number","count","quantity","amount"):
                try:
                    if ir.get(k) is not None:tok=int(ir.get(k));break
                except Exception:pass
        elif ir not in (None,""):
            try:tok=int(ir)
            except Exception:pass
    return level,tok


def _item_balance(init_data:dict[str,Any]|None,item_id:str)->int|None:
    items=(init_data or {}).get("user_items") or {}
    row=None
    if isinstance(items,dict):
        row=items.get(str(item_id))
        if row is None and str(item_id).isdigit():row=items.get(int(item_id))
    if isinstance(row,dict):
        for k in ("number","count","quantity","amount"):
            try:
                if row.get(k) is not None:return int(row.get(k))
            except Exception:pass
    elif row not in (None,""):
        try:return int(row)
        except Exception:pass
    return None


def _currency_label(raw:Any,cost:int|None,path:str="")->str:
    """Name a CM-shop currency only when the payload/price gives us evidence.

    ``cost_type`` is *not* globally stable across AT endpoints (for example the
    normal card shop uses type 2 for Nixons), so the CM reviewer must not blindly
    translate numeric type codes.  Daily CM offers have a very recognizable
    initial-price shape; we use that only inside a mastery/combo shop path and
    otherwise preserve the raw code.
    """
    txt=_norm_name(raw)
    low=txt.lower()
    if "gem" in low:return "Gems"
    if "stone" in low or "mastery" in low:return "Mastery Stones"
    p=(path or "").lower()
    if any(x in p for x in ("mastery","combo")):
        if cost==500:return "Gems"
        if cost in {1000,2000,4500}:return "Mastery Stones"
    if txt and not txt.isdigit():return txt
    return f"Type {txt}" if txt else ""


def _recommendation(score:float,currency:str,cost:int|None,current_cm:int|None)->str:
    """Translate the 0-10 score into the practical CM-buy rubric.

    This follows the long-running community CM-review *decision framework*, not
    any one reviewer's prose: elite scores may justify a 500-gem CM1; 7-8 is a
    strong mastery-stone buy; 5-6 is a normal CM1/value buy; 3-4 is niche; lower
    scores are passes.  Current CM depth is shown separately so a good combo does
    not automatically imply buying every escalating token pack.
    """
    cur=(currency or "").lower()
    gem="gem" in cur
    stones="stone" in cur
    if gem:
        if score>=9 and (cost is None or cost<=500):rec="ELITE • 500-GEM CM1 CONSIDER"
        elif score>=9:rec="ELITE COMBO • GEM PRICE IS EXPENSIVE"
        else:rec="PASS FOR GEMS"
    elif stones:
        if score>=9:rec="PREMIUM STONE BUY • DEEP CM CANDIDATE"
        elif score>=7:rec="STRONG STONE BUY"
        elif score>=5:rec="CM1 / VALUE BUY"
        elif score>=3:rec="NICHE / ACCOUNT-DEPENDENT"
        else:rec="PASS"
    else:
        if score>=9:rec="TOP-TIER CM TARGET"
        elif score>=7:rec="STRONG CM TARGET"
        elif score>=5:rec="GOOD / CM1 VALUE"
        elif score>=3:rec="NICHE / ACCOUNT-DEPENDENT"
        else:rec="PASS"
    if current_cm is not None and current_cm>=3 and score<9:
        rec+=" • ALREADY DEEP CM"
    return rec


class CMReviewEngine:
    """Deterministic, current-data CM review engine.

    The philosophy follows the long-running community CM-review format: score
    the *purchase value now*, not nostalgia or raw card text alone. The wording
    and formula are original to AT Commander so the same result can be reused by
    the desktop UI and Discord bot.
    """
    def __init__(self,catalog:dict[str,dict[str,Any]],init_data:dict[str,Any]|None=None,user_units:Any=None):
        self.catalog={str(k):v for k,v in (catalog or {}).items() if isinstance(v,dict)}
        self.init=init_data or {}
        self.user_units=user_units if user_units is not None else self.init.get("user_units")
        self.combos={k:v for k,v in self.catalog.items() if _is_combo(v)}
        self.pcs={k:v for k,v in self.catalog.items() if _is_pc(v)}
        # One named combo can have many static recipe/result rows across object
        # generations. Reviews are about the named CM purchase, not one arbitrary
        # row. Group by name and pick the richest/current-strength row as the
        # representative while retaining group size as an input-breadth signal.
        self.combo_groups:dict[str,list[tuple[str,dict[str,Any]]]]={}
        for cid,row in self.combos.items():
            key=_norm_name(row.get("name")).lower()
            if key:self.combo_groups.setdefault(key,[]).append((cid,row))
        self._canonical:dict[str,tuple[str,dict[str,Any]]]={}
        self._id_to_group:dict[str,list[tuple[str,dict[str,Any]]]]={}
        for key,rows in self.combo_groups.items():
            def quality(item):
                cid,row=item
                skills=len(_skill_ids(row)); metric=_power_metric(row) or -1
                try:num=int(cid)
                except Exception:num=0
                return (skills,metric,num)
            best=max(rows,key=quality)
            self._canonical[key]=best
            for cid,_ in rows:self._id_to_group[cid]=rows
        self._combo_power=[p for _cid,row in self._canonical.values() if (p:=_power_metric(row)) is not None]
        # Same-skill magnitude pools are the core 1.7.19 benchmark.  Comparing
        # Punch to Punch, Craze to Craze, etc. catches power creep that a generic
        # three-skill checklist misses badly (Wingnut Amy is the canonical case).
        self._skill_value_pool:dict[str,list[float]]={}
        self._attack_mult_pool=[];self._health_mult_pool=[]
        for _cid,crow in self._canonical.values():
            for sr in _skill_rows(crow):
                sid=str(sr.get("_id") or "").lower(); val=_skill_value(sr)
                if sid and val is not None and val>=0:self._skill_value_pool.setdefault(sid,[]).append(float(val))
            am=_num(crow.get("attack_multiplier"));hm=_num(crow.get("health_multiplier"))
            if am is not None:self._attack_mult_pool.append(float(am))
            if hm is not None:self._health_mult_pool.append(float(hm))
        self._owned_ids=self._owned_unit_ids(self.user_units)
        self.mastery_stones=_item_balance(self.init,"200008")

    @staticmethod
    def _owned_unit_ids(raw:Any)->set[str]:
        out=set(); vals=raw.values() if isinstance(raw,dict) else raw if isinstance(raw,list) else []
        for r in vals:
            if isinstance(r,dict):
                uid=r.get("unit_id") or r.get("card_id") or r.get("id")
                if uid is not None:out.add(str(uid))
        return out

    def find(self,query:str)->list[tuple[str,dict[str,Any]]]:
        q=_norm_name(query).lower()
        if not q:return []
        exact=[];prefix=[];contains=[]
        for key,(cid,row) in self._canonical.items():
            nm=_norm_name(row.get("name"));low=nm.lower()
            group=self.combo_groups.get(key) or []
            ids={x[0] for x in group}
            if q in ids or low==q:exact.append((cid,row))
            elif low.startswith(q):prefix.append((cid,row))
            elif q in low:contains.append((cid,row))
        return exact+prefix+contains

    def _percentile(self,value:float|None)->float|None:
        if value is None or not self._combo_power:return None
        a=sorted(self._combo_power)
        below=sum(1 for x in a if x<value); equal=sum(1 for x in a if x==value)
        return 100.0*(below+0.5*equal)/len(a)

    def _group_for(self,combo_id:str)->tuple[str,dict[str,Any],list[tuple[str,dict[str,Any]]]]:
        cid=str(combo_id)
        row=self.catalog.get(cid) or self.combos.get(cid)
        if not isinstance(row,dict):raise KeyError(f"Unknown combo id {cid}")
        key=_norm_name(row.get("name")).lower()
        group=self.combo_groups.get(key) or [(cid,row)]
        canonical=self._canonical.get(key) or (cid,row)
        return canonical[0],canonical[1],group

    def review(self,combo_id:str,*,currency:str="",cost:int|None=None)->CMReview:
        cid,row,group=self._group_for(str(combo_id))
        name=_norm_name(row.get("name")) or f"Combo {cid}"
        all_traits=[]
        for _gid,grow in group:
            for t in _traits(grow):
                if t not in all_traits:all_traits.append(t)
        traits=_traits(row) or all_traits;trait="/".join(x.title() for x in traits) if traits else ""
        srows=_skill_rows(row);sids=[str(s.get("_id") or "") for s in srows]
        useful=[s for s in sids if s]
        score=4.0;reasons=[];cautions=[]

        # Skill COUNT is only a prerequisite now, not a strength bonus.  Modern
        # three-skill combos are normal; awarding +1 simply for having three
        # names is what let badly power-crept combos score far too high.
        if len(useful)==2:
            score-=0.75;cautions.append("Only two meaningful skills lowers the ceiling versus the modern three-skill baseline.")
        elif len(useful)==1:
            score-=1.75;cautions.append("A one-skill package is severely behind the current combo baseline.")
        elif len(useful)==0:
            score-=2.25;cautions.append("No usable skill package was resolved from current static data.")

        if not traits:
            score-=1.25;cautions.append("No normal BGE trait was detected, so it can miss the trait-wide boost that keeps comparable combos relevant.")

        # Practical recipe breadth matters, but only a little.  It should never
        # rescue weak combat values.
        variant_count=max(1,len(group))
        if variant_count>=8:
            score+=0.35;reasons.append(f"Broad input coverage is confirmed ({variant_count} detected static variants).")
        elif variant_count>=4:
            score+=0.20;reasons.append(f"Solid input coverage is confirmed ({variant_count} detected static variants).")
        elif variant_count>=2:
            score+=0.10;reasons.append(f"More than one static recipe/result variant is available ({variant_count} detected).")

        restricted=0
        for sr in srows:
            y=sr.get("y")
            if y not in (None,"",0,"0"):restricted+=1
        if restricted:
            cautions.append(f"{restricted} listed skill{'s are' if restricted!=1 else ' is'} trait/show-restricted, reducing universal value.")
            score-=min(.50,.20*restricted)

        # Benchmark every skill magnitude against OTHER combos carrying the same
        # skill.  This is the key anti-power-creep signal.
        benches=[]
        for sr in srows:
            sid=str(sr.get("_id") or "").lower(); val=_skill_value(sr)
            pct=_percentile_in(self._skill_value_pool.get(sid,[]),val) if sid else None
            tier=_skill_tier(pct)
            benches.append((sid,val,pct,tier))
        pcts=[p for _sid,_v,p,_t in benches if p is not None]
        median_pct=statistics.median(pcts) if pcts else None
        min_pct=min(pcts) if pcts else None
        strong_count=sum(1 for p in pcts if p>=70)
        low_count=sum(1 for p in pcts if p<35)

        if median_pct is not None:
            if median_pct>=90:score+=2.25
            elif median_pct>=80:score+=1.75
            elif median_pct>=70:score+=1.25
            elif median_pct>=60:score+=0.80
            elif median_pct>=50:score+=0.35
            elif median_pct>=40:score+=0.0
            elif median_pct>=30:score-=0.55
            elif median_pct>=20:score-=1.10
            else:score-=1.65
            if len(pcts)>=3 and low_count==len(pcts):
                score-=0.75;cautions.append("Every resolved skill magnitude is in the low end of its same-skill benchmark; the package is good on paper but badly power-crept in practice.")
            if min_pct is not None and min_pct<10:
                score-=0.25;cautions.append("At least one skill value is near the bottom of the live same-skill pool.")
            if len(pcts)>=3 and strong_count==len(pcts):
                score+=0.45;reasons.append("All three skill magnitudes benchmark strongly against current combos using those same skills.")
            elif strong_count>=2:
                score+=0.20;reasons.append("At least two skill magnitudes benchmark strongly against current same-skill combos.")
            tier_text=[]
            for sid,val,pct,tier in benches:
                nm=SKILL_NAMES.get(sid,sid.replace("_"," ").title())
                if tier:tier_text.append(f"{tier} {nm}")
                else:tier_text.append(nm)
            if tier_text:
                reasons.append("Current skill-value benchmark: "+", ".join(tier_text)+".")
        else:
            # Old/odd schemas with no magnitudes get a lower-confidence fallback.
            metric=_power_metric(row);pct=self._percentile(metric)
            if pct is not None:
                if pct>=85:score+=0.75
                elif pct<30:score-=0.75
            cautions.append("Exact same-skill value benchmarking was unavailable for this static row, so the rating has lower confidence.")

        # Good skill PAIRINGS only deserve full credit when the numbers behind
        # them are competitive.  Low Craze + Low Punch should not receive the
        # same synergy bonus as modern high-value Craze + Punch.
        gate=0.45 if median_pct is None else max(0.0,min(1.0,(median_pct-20.0)/65.0))
        for need,bonus,text in PREMIUM_SYNERGY:
            if need.issubset(set(useful)):
                if gate>=0.45:
                    score+=bonus*gate;reasons.append(text)
                else:
                    cautions.append(text.replace(" gives "," would normally give ")+" The current skill values are too low for that pairing to carry the rating.")
        off=len(set(useful)&OFFENSE);defn=len(set(useful)&DEFENSE);sus=len(set(useful)&SUSTAIN)
        if off>=2:score+=0.45*gate
        if defn>=2:score+=0.35*gate
        if sus>=2 and off>=1:score+=0.25*gate

        # Attack/health multiplier quality is a secondary tiebreaker, never a
        # substitute for actual skill values.
        mp=[]
        am=_num(row.get("attack_multiplier"));hm=_num(row.get("health_multiplier"))
        ap=_percentile_in(self._attack_mult_pool,am);hp=_percentile_in(self._health_mult_pool,hm)
        if ap is not None:mp.append(ap)
        if hp is not None:mp.append(hp)
        if mp:
            m=statistics.mean(mp)
            if m>=80:score+=0.45;reasons.append("Its attack/health scaling is strong relative to the current combo catalog.")
            elif m>=65:score+=0.20
            elif m<25:score-=0.45;cautions.append("Its attack/health scaling is also in the low end of the current combo catalog.")
            elif m<40:score-=0.20

        group_ids={str(gid) for gid,_grow in group}
        pc_row=next((r for r in self.pcs.values() if str(r.get("combo_card_id") or "") in group_ids),None)
        if pc_row is None:
            pc_row=next((r for r in self.pcs.values() if _norm_name(r.get("name")).lower()==name.lower()),None)
        pc_present=pc_row is not None
        if pc_present:
            # Presence is useful context but no longer inflates the COMBO score.
            reasons.append("A matching PC exists; the rating above remains combo-focused instead of granting free points just for PC availability.")

        recipes=_recipe_ids(row)
        if recipes:
            owned=len(recipes & self._owned_ids)
            if owned:
                score+=min(.25,.10+.15*(owned/max(1,len(recipes))))
                reasons.append(f"Your account owns {owned}/{len(recipes)} detected recipe input(s), adding a small practical-use bonus.")
            else:
                cautions.append("Commander found recipe IDs but none were detected in your current inventory snapshot.")

        cm,tokens=_current_mastery(self.init,cid)
        if cm is None:
            for gid,_grow in group:
                cm,tokens=_current_mastery(self.init,gid)
                if cm is not None:break
        cur=(currency or "").lower()
        if "gem" in cur and score<9:
            cautions.insert(0,"This is a gem offer. Gems should normally be reserved for truly elite mastery targets.")
        elif "gem" in cur and score>=9:
            reasons.append("At this score, a first 500-gem mastery buy can be defensible under the classic CM value rubric.")

        score=max(0.0,min(10.0,round(score*2)/2))
        recommendation=_recommendation(score,currency,cost,cm)
        if score>=8:summary="High-end current mastery value; the combat numbers as well as the skill package justify planning around this combo."
        elif score>=6:summary="Good current combo with a legitimate reason to spend stones when it fits your deck, but it is not being treated as an automatic top-tier buy."
        elif score>=4:summary="Usable but situational. Current combat values do not justify treating the skill names alone as premium mastery value."
        else:summary="Low current mastery value. The present-day skill magnitudes/power context do not justify chasing CM under normal circumstances."

        # Display the qualitative value tier beside each skill so the user can
        # immediately see WHY an attractive-looking package was downgraded.
        display_skills=[]
        for sid,val,pct,tier in benches:
            nm=SKILL_NAMES.get(sid,sid.replace("_"," ").title())
            display_skills.append((tier+" " if tier else "")+nm)
        if not display_skills:display_skills=[SKILL_NAMES.get(s,s.replace("_"," ").title()) for s in sids]
        confidence="High" if len(pcts)>=max(1,len(useful)-1) and useful else "Medium" if useful else "Low"
        out=CMReview(cid,name,trait,score,recommendation,summary,display_skills,reasons,cautions,pc_present,cm,tokens,currency,cost,"Live AT static/account data",confidence,variant_count,self.mastery_stones)
        out.data_score=score
        return out

    def apply_community_consensus(self,review:CMReview,consensus:Any)->CMReview:
        """Blend recent explicit community scores as a bounded secondary check.

        Current card values remain the foundation.  Reddit can move the final
        rating only modestly, with aggressive recency decay, so a six-month-old
        review cannot erase obvious present-day power creep.
        """
        if review.data_score is None:review.data_score=review.score
        # A refreshed lookup can become unavailable/stale. Reapplying evidence
        # must start from the current-data result and remove the prior nudge.
        review.score=float(review.data_score)
        review.recommendation=_recommendation(review.score,review.currency,review.cost,review.current_cm)
        review.community_score=None;review.community_count=0;review.community_confidence=""
        review.reasons=[reason for reason in review.reasons if not reason.startswith("Recent explicit Reddit ratings nudged")]
        review.cautions=[caution for caution in review.cautions if not caution.startswith(("Dated Reddit reviews differ","Recent/community ratings disagree"))]
        cs=getattr(consensus,'score',None) if consensus is not None else None
        ratings=list(getattr(consensus,'ratings',[]) or []) if consensus is not None else []
        review.community_note=str(getattr(consensus,'note','') or '') if consensus is not None else ''
        review.community_sources=[]
        if cs is None or not ratings or getattr(consensus,'status','matched') not in {'matched','stale'}:
            return review
        review.community_score=float(cs);review.community_count=len(ratings)
        review.community_confidence=str(getattr(consensus,'confidence','') or '')
        for rr in ratings[:5]:
            author=str(getattr(rr,'author','') or 'unknown')
            date=str(getattr(rr,'rating_date','') or 'date unknown')
            score=float(getattr(rr,'score',0) or 0)
            title=str(getattr(rr,'title','') or '')
            url=str(getattr(rr,'url','') or '')
            review.community_sources.append(f"{date} • {author} • {score:.1f}/10 • {title}" + (f" • {url}" if url else ""))
        gap=float(cs)-float(review.data_score)
        if getattr(consensus,'status','matched')=='stale':
            review.score=float(review.data_score)
            return review
        if abs(gap)>=2.5:
            review.score=float(review.data_score)
            review.cautions.append(f"Dated Reddit reviews differ from the current card-value model by {abs(gap):.1f} points. Both are shown; the current-data score is retained.")
            return review
        ages=[getattr(rr,'age_days',None) for rr in ratings if getattr(rr,'age_days',None) is not None]
        latest=min(ages) if ages else 9999
        count=len(ratings)
        if latest<=45:factor=.30
        elif latest<=120:factor=.20
        elif latest<=240:factor=.10
        elif latest<=365:factor=.05
        else:factor=.02
        if count>=3:factor*=1.15
        delta=(float(cs)-float(review.data_score))*factor
        delta=max(-1.0,min(1.0,delta))
        final=max(0.0,min(10.0,round((float(review.data_score)+delta)*2)/2))
        review.score=final
        review.recommendation=_recommendation(final,review.currency,review.cost,review.current_cm)
        gap=float(cs)-float(review.data_score)
        if abs(gap)>=2.5:
            review.cautions.append(f"Recent/community ratings disagree with the current card-value model by {abs(gap):.1f} points; power creep, deck context, or older review timing may explain the gap.")
        if abs(delta)>=.25:
            direction="up" if delta>0 else "down"
            review.reasons.append(f"Recent explicit Reddit ratings nudged the final score {direction} by {abs(delta):.1f}; current AT card values still carry most of the weight.")
        return review

    def review_name(self,query:str,**kwargs)->CMReview:
        rows=self.find(query)
        if not rows:raise KeyError(f"No combo matched '{query}'.")
        return self.review(rows[0][0],**kwargs)

    def discover_shop(self,*payloads:Any)->list[dict[str,Any]]:
        """Best-effort discovery of live CM offers across current AT payloads.

        AT has moved store/mastery containers more than once. We therefore only
        accept rows from mastery/shop/offer paths and require a combo id/name plus
        a price/currency signal. This avoids mistaking user_combo_mastery for the
        live shop.
        """
        found=[];seen=set()
        name_to_id={key:cid for key,(cid,_row) in self._canonical.items()}
        idkeys=("combo_id","mastery_id","unit_id","card_id","combo","id")
        costkeys=("cost","price","expected_cost","amount")
        curkeys=("currency","currency_type","cost_type","price_type")

        def add_offer(cid:str,cost:int|None,cur:str,path:str,store_id:Any=None):
            sig=(cid,cost,cur)
            if sig not in seen:
                seen.add(sig)
                found.append({"combo_id":cid,"name":_norm_name(self.combos[cid].get("name")),
                              "cost":cost,"currency":cur,"path":path,"store_id":str(store_id or "")})

        def native_mastery_offer(row:dict[str,Any],path:str):
            # The real setDeckCommander capture exposes daily CM offers as
            # Mastery Token Pack store rows. Their price belongs to the parent;
            # the chosen combo is a token item nested under singular `item`.
            # `items.item` is only the random-pool template, never today's offer.
            tabs=row.get("tabs") or {}
            fmt=str((row.get("extra_data") or {}).get("shop_format") or "") if isinstance(row.get("extra_data"),dict) else ""
            mastery_tab=isinstance(tabs,dict) and str(tabs.get("mastery") or "").lower() in {"1","true"}
            if not mastery_tab and "mastery" not in fmt.lower():return
            hc=_num(row.get("hc_cost"))
            cost_obj=row.get("cost")
            if hc is not None and hc>0:
                cost,cur=int(hc),"Gems"
            elif isinstance(cost_obj,dict) and str(cost_obj.get("item"))=="200008":
                amount=_num(cost_obj.get("num"))
                if amount is None or amount<0:return
                cost,cur=int(amount),"Mastery Stones"
            else:return
            rewards=row.get("item") or []
            if isinstance(rewards,dict):
                rewards=[rewards] if rewards.get("id") is not None else list(rewards.values())
            if not isinstance(rewards,list):return
            for reward in rewards:
                if not isinstance(reward,dict):continue
                token=str(reward.get("id") or reward.get("item_id") or "")
                # Existing account-token decoding uses exactly this encoding.
                # Require a catalog match rather than assuming every 2xxxxxx
                # inventory item identifies a combo.
                if not re.fullmatch(r"2\d{6}",token):continue
                cid=str(int(token[1:]))
                if cid in self.combos:add_offer(cid,cost,cur,path,row.get("id"))

        def walk(o:Any,path:str="",depth:int=0):
            if depth>10:return
            if isinstance(o,dict):
                lowpath=path.lower()
                if any(x in lowpath for x in ("user_combo_mastery","user_items","user_mastery")):return
                native_mastery_offer(o,path)
                looks=any(x in lowpath for x in ("mastery","combo","shop","store","offer"))
                cid=None
                for k in idkeys:
                    v=o.get(k)
                    if v is not None and str(v) in self.combos:cid=str(v);break
                nm=_norm_name(o.get("name") or o.get("combo_name") or o.get("title"))
                if cid is None and nm:cid=name_to_id.get(nm.lower())
                cost=None
                for k in costkeys:
                    n=_num(o.get(k))
                    if n is not None:cost=int(n);break
                cur=""
                for k in curkeys:
                    if o.get(k) not in (None,""):cur=str(o.get(k));break
                # Do not globally map numeric cost_type codes: AT reuses those
                # codes for unrelated currencies in other stores.  Infer only
                # from explicit names or the recognizable CM initial prices.
                cur=_currency_label(cur,cost,path)
                if looks and cid and (cost is not None or cur):
                    add_offer(cid,cost,cur,path)
                for k,v in o.items():
                    if isinstance(v,(dict,list)):walk(v,f"{path}.{k}" if path else str(k),depth+1)
            elif isinstance(o,list):
                for i,v in enumerate(o):
                    if isinstance(v,(dict,list)):walk(v,f"{path}[{i}]",depth+1)
        for p in payloads:walk(p)
        return found

    def review_shop(self,offers:list[dict[str,Any]])->list[CMReview]:
        out=[]
        for o in offers:
            try:out.append(self.review(str(o.get("combo_id")),currency=str(o.get("currency") or ""),cost=o.get("cost")))
            except Exception:pass
        return sorted(out,key=lambda r:(-r.score,r.name.lower()))


def collect_live_review_data(client)->tuple[CMReviewEngine,list[dict[str,Any]],dict[str,Any]]:
    """Load the minimum live data needed by both GUI and Discord.

    Only confirmed/read-safe calls are used here.  We do not invent a CM-shop
    endpoint just to make REVIEW LIVE SHOP appear successful.  ``init`` often
    carries the account/mastery state, and the already-confirmed active-deck
    no-op may expose additional store metadata.  If neither payload exposes the
    CM offers cleanly, individual combo reviews still work and the UI says the
    live shop could not be mapped.
    """
    init=client.init()
    if not isinstance(init,dict) or init.get("result") is False:
        raise RuntimeError("AT did not provide a fresh account snapshot for CM reviews; wait briefly and refresh again.")
    catalog=client.card_catalog()
    if not catalog:
        raise RuntimeError("AT did not provide the current card catalog for CM reviews; refresh again after the account connection recovers.")
    payloads=[init]
    # The learner already classifies getComboMasteries as read-only/noise traffic.
    # Pull it opportunistically because some client revisions expose current CM
    # state/shop metadata there. Failure is harmless and never blocks reviews.
    if "user_combo_mastery" not in init:
        try:
            cm_payload=client.call("getComboMasteries")
            payloads.append(cm_payload)
            if isinstance(cm_payload,dict):
                for key in ("user_combo_mastery","user_items"):
                    if key not in init and key in cm_payload:
                        init[key]=cm_payload[key]
        except Exception:
            pass
    eng=CMReviewEngine(catalog,init)
    offers=eng.discover_shop(*payloads)
    if offers:
        # Current init already contains the real four daily mastery store rows.
        # Avoid redundant no-op deck/commander writes when no discovery fallback
        # is needed; normal review refresh then performs only read requests.
        return eng,offers,init
    try:
        deck=client.get_exact_decks(init);payloads.append(deck)
        try:payloads.append(client.get_store_snapshot(deck))
        except Exception:pass
    except Exception:pass
    return eng,eng.discover_shop(*payloads),init
