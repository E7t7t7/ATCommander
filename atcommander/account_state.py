from __future__ import annotations
from typing import Any

NAME_KEYS=("deck_name","hero_name","commander_name","display_name","card_name","item_name","name","title","label")
ID_KEYS=("deck_id","hero_id","commander_id","island_id","unit_id","card_id","item_id","id","slot_id")

# Static commander names observed in AT's own hero-training metadata. Used only
# when the live static-file catalog is unavailable; getHeroSquad still decides
# which heroes are actually unlocked/selectable.
FALLBACK_HERO_NAMES = {
    "1001":"Peter","1002":"Stewie","1003":"Brian","1004":"Consuela","1005":"The Giant Chicken",
    "1501":"Quagmire","1502":"Mort Goldman","1503":"Herbert","1504":"Meg","1505":"Seamus",
    "2001":"Roger","2002":"Stan","2003":"Steve","2005":"Ricky Spanish",
    "2501":"Klaus","2502":"Bullock","2503":"Principal Lewis","2504":"Anti-Christ","2505":"Young Old Stan","2506":"Francine","2507":"Rapture Jesus",
    "3001":"Bob","3002":"Louise","3003":"Tina","3004":"Gene","3005":"Linda",
    "3501":"Teddy","3502":"Bad Tina","3503":"Mort","3504":"Mr. Fischoeder","3505":"Beefsquatch",
    "4001":"Hank","4002":"Bobby","4003":"Dale","4004":"John Redcorn",
    "4501":"Luanne","4502":"Peggy","4503":"Adult Bobby","4504":"Lady Bird",
    "5016":"Bender","5017":"Fry","5018":"Leela","5019":"Zapp Brannigan","5020":"Zoidberg",
    "5601":"Amy","5602":"Robot Devil","5603":"Hermes","5604":"Professor Farnsworth","5605":"Nixon's Head & Agnew","5606":"H.G. Blob",
    "7501":"Lana","7502":"Pam Poovey","7503":"Dr. Krieger","7504":"Cyril","7505":"Mitsuko Miyazumi","7606":"Barry Dylan","9901":"Archer"
}


def _clean_name(v:Any)->str:
    s=str(v or "").strip()
    if not s or s.lower() in {"none","null","unknown"} or s.isdigit(): return ""
    return s


def _num(v:Any):
    if isinstance(v,bool): return None
    if isinstance(v,(int,float)): return v
    if isinstance(v,str):
        try:return float(v) if "." in v else int(v)
        except:return None
    return None


def discover_id_name_catalog(data:dict[str,Any])->dict[str,str]:
    out={}
    def walk(obj,depth=0):
        if depth>9:return
        if isinstance(obj,dict):
            rid=next((obj.get(k) for k in ID_KEYS if obj.get(k) is not None),None)
            name=next((_clean_name(obj.get(k)) for k in NAME_KEYS if _clean_name(obj.get(k))),"")
            if rid is not None and name: out[str(rid)]=name
            for k,v in obj.items():
                if str(k).isdigit() and isinstance(v,str) and _clean_name(v): out[str(k)]=_clean_name(v)
                elif str(k).isdigit() and isinstance(v,dict):
                    nm=next((_clean_name(v.get(n)) for n in NAME_KEYS if _clean_name(v.get(n))),"")
                    if nm: out[str(v.get("id") or v.get("unit_id") or v.get("deck_id") or v.get("hero_id") or v.get("commander_id") or k)]=nm
                walk(v,depth+1)
        elif isinstance(obj,list):
            for v in obj:walk(v,depth+1)
    walk(data)
    return out


def exact_decks(data:dict[str,Any])->list[dict[str,str]]:
    """Read the authoritative saved-deck list returned by setActiveDeck/setDeckCommander."""
    raw=data.get("user_decks") or {}
    rows=[]
    if isinstance(raw,dict): it=raw.items()
    elif isinstance(raw,list): it=((str(i),v) for i,v in enumerate(raw))
    else: it=[]
    for key,row in it:
        if not isinstance(row,dict): continue
        rid=row.get("deck_id",key); name=_clean_name(row.get("name") or row.get("deck_name"))
        if rid is not None and name: rows.append({"id":str(rid),"name":name,"resolved":"1","source":"user_decks"})
    return sorted(rows,key=lambda r:int(r["id"]) if r["id"].isdigit() else r["id"])


def unlocked_heroes(hero_status:dict[str,Any], hero_catalog:dict[str,dict[str,Any]]|None=None)->list[dict[str,str]]:
    """Only heroes returned by getHeroSquad and marked unlocked are selectable."""
    hero_catalog=hero_catalog or {}
    raw=hero_status.get("user_hero_squad") or []
    if isinstance(raw,dict): iterable=raw.values()
    elif isinstance(raw,list): iterable=raw
    else: iterable=[]
    rows=[]
    for row in iterable:
        if not isinstance(row,dict): continue
        hid=row.get("hero_id") or row.get("commander_id") or row.get("id")
        if hid is None: continue
        unlocked=row.get("unlocked")
        try: unlocked=int(unlocked or 0)==1
        except Exception: unlocked=bool(unlocked)
        if not unlocked: continue
        cat=hero_catalog.get(str(hid)) or {}
        name=_clean_name(row.get("name") or row.get("hero_name") or row.get("commander_name") or cat.get("name") or cat.get("hero_name") or cat.get("commander_name"))
        # Never borrow arbitrary names from guild/user/card metadata.
        if not name: name=FALLBACK_HERO_NAMES.get(str(hid), f"Hero {hid}")
        rows.append({"id":str(hid),"name":name,"resolved":"1","source":"getHeroSquad","training_level":str(row.get("training_level") or 0)})
    return sorted(rows,key=lambda r:r["name"].lower())

def _find_exact_paths(data:dict[str,Any], suffixes:tuple[str,...]):
    found=[]
    def walk(obj,path="",depth=0):
        if depth>8:return
        if isinstance(obj,dict):
            for k,v in obj.items():
                p=f"{path}.{k}" if path else str(k)
                if any(p.endswith(s) for s in suffixes):found.append((p,v))
                if isinstance(v,(dict,list)):walk(v,p,depth+1)
        elif isinstance(obj,list):
            for i,v in enumerate(obj):walk(v,f"{path}[{i}]",depth+1)
    walk(data);return found


def real_trophy_metric(data:dict[str,Any])->dict[str,Any]|None:
    """SFC trophies are rating, not rank or trophy_data badge labels."""
    for path,val in _find_exact_paths(data,("pvp_data.rating","common_fields.pvp_rating")):
        n=_num(val)
        if n is not None:return {"path":path,"name":"SFC Rating","value":n}
    return None


def find_arena_energy(data:dict[str,Any])->tuple[int|None,str|None]:
    for path,val in _find_exact_paths(data,("common_fields.current_pvp_energy","user_data.stamina")):
        n=_num(val)
        if n is not None:return int(n),path
    return None,None


def find_ad_state(data:dict[str,Any])->dict[str,Any]:
    level=end=None
    for path,val in _find_exact_paths(data,("user_data.boost_level","user_data.boost_end_time")):
        if path.endswith("boost_level") and level is None: level=_num(val)
        if path.endswith("boost_end_time") and end is None: end=_num(val)
    # bonus deliberately means verified AT ad boost state, not an inferred generic flag.
    return {"vip":None,"bonus":level,"boost_level":level,"boost_end_time":end,"raw":[("user_data.boost_level",level),("user_data.boost_end_time",end)]}


def _discover(data:dict[str,Any],kind:str)->list[dict[str,str]]:
    # Kept for Siege-island fallback/diagnostics only. Deck/hero UI no longer uses this.
    global_names=discover_id_name_catalog(data); found={}; unresolved=set()
    id_fields={"deck":("deck_id",),"hero":("hero_id","commander_id"),"island":("island_id",)}[kind]
    hints={"deck":("deck",),"hero":("hero","commander"),"island":("siege","island")}[kind]
    def put(rid,name,source):
        if rid in (None,""):return
        rid=str(rid); nm=_clean_name(name) or global_names.get(rid,"")
        if nm: found[rid]={"id":rid,"name":nm,"resolved":"1","source":source}
        else: unresolved.add(rid)
    def walk(obj,path="",depth=0):
        if depth>9:return
        if isinstance(obj,dict):
            relevant=any(h in path.lower() for h in hints); rid=next((obj.get(k) for k in id_fields if obj.get(k) is not None),None)
            if rid is not None: put(rid,next((_clean_name(obj.get(k)) for k in NAME_KEYS if _clean_name(obj.get(k))),""),path)
            if relevant:
                rid2=next((obj.get(k) for k in (id_fields+("id",)) if obj.get(k) is not None),None)
                if rid2 is not None:put(rid2,next((_clean_name(obj.get(k)) for k in NAME_KEYS if _clean_name(obj.get(k))),""),path)
            for k,v in obj.items():walk(v,f"{path}.{k}" if path else str(k),depth+1)
        elif isinstance(obj,list):
            for i,v in enumerate(obj):walk(v,f"{path}[{i}]",depth+1)
    walk(data)
    for rid in unresolved:
        if rid in global_names:put(rid,global_names[rid],"global-name-map")
    return sorted(found.values(),key=lambda r:r.get("name","").lower())


def discover_decks(data): return exact_decks(data) or _discover(data,"deck")
def discover_heroes(data): return _discover(data,"hero")
def discover_siege_islands(data):
    rows=_discover(data,"island")
    return [r for r in rows if "island" in (r.get("source","")+r.get("name","")).lower()]



def parse_siege_status(data:dict[str,Any]|None)->dict[str,Any]:
    data=data or {}
    st=data.get("guild_siege_status") if isinstance(data,dict) else None
    if not isinstance(st,dict): st=data if isinstance(data,dict) else {}
    def rows(key):
        raw=st.get(key) or {}
        vals=raw.values() if isinstance(raw,dict) else raw if isinstance(raw,list) else []
        out=[]
        for r in vals:
            if not isinstance(r,dict): continue
            slot=r.get("slot_id") or r.get("id")
            if slot is None: continue
            meta=r.get("data") if isinstance(r.get("data"),dict) else {}
            try: hp=int(r.get("hp") or 0)
            except Exception: hp=0
            try: max_hp=int(r.get("max_hp") or 0)
            except Exception: max_hp=0
            out.append({
                "id":str(slot),"slot_id":str(slot),"location_id":str(r.get("location_id") or meta.get("id") or ""),
                "name":str(meta.get("name") or f"Island {slot}"),"hp":hp,"max_hp":max_hp,
                "alive":hp>0,"faction_id":str(r.get("faction_id") or ""),
            })
        return sorted(out,key=lambda x:int(x.get("slot_id") or 999))
    try: phase=int(st.get("phase")) if st.get("phase") is not None else None
    except Exception: phase=None
    try: attacks=int(st.get("num_attacks") or 0)
    except Exception: attacks=0
    try: max_attacks=int(st.get("max_attacks") or 0)
    except Exception: max_attacks=0
    return {"phase":phase,"phase_end_time":st.get("phase_end_time"),"num_attacks":attacks,"max_attacks":max_attacks,
            "friendly":rows("locations"),"enemy":rows("enemy_locations"),"raw":st}

def scan_progress_metrics(data:dict[str,Any])->list[dict[str,Any]]:
    trophy=real_trophy_metric(data)
    return [trophy] if trophy else []

def choose_trophy_metric(rows:list[dict[str,Any]])->dict[str,Any]|None:
    return next((r for r in rows if r and r.get("path") in {"pvp_data.rating","common_fields.pvp_rating"}),None)


def find_hero_medals(data:dict[str,Any], hero_rows:list[dict[str,str]]|None=None)->list[dict[str,Any]]:
    names={str(r.get("id")):r.get("name") for r in (hero_rows or []) if r.get("name")};out=[];seen=set()
    def walk(obj,path="",depth=0):
        if depth>8:return
        if isinstance(obj,dict):
            hid=obj.get("hero_id") or obj.get("commander_id"); count=None
            for k,v in obj.items():
                if any(t in str(k).lower() for t in ("medal","token")) and _num(v) is not None:count=_num(v);break
            if hid is not None and count is not None and (str(hid),path) not in seen:
                seen.add((str(hid),path));out.append({"hero":names.get(str(hid),f"Hero {hid}"),"hero_id":str(hid),"count":count,"path":path})
            for k,v in obj.items():walk(v,f"{path}.{k}" if path else str(k),depth+1)
        elif isinstance(obj,list):
            for i,v in enumerate(obj):walk(v,f"{path}[{i}]",depth+1)
    walk(data);return sorted(out,key=lambda r:str(r["hero"]).lower())


def find_adventure_energy(data:dict[str,Any])->tuple[int|None,int|None,int|None]:
    """Return current/max/recharge Adventure energy from confirmed user_data fields."""
    ud=(data or {}).get("user_data") or {}
    current=maxv=recharge=None
    for k in ("energy","current_energy","mission_energy","adventure_energy"):
        n=_num(ud.get(k))
        if n is not None:
            current=int(n);break
    for k in ("max_energy","mission_max_energy","adventure_max_energy"):
        n=_num(ud.get(k))
        if n is not None:
            maxv=int(n);break
    n=_num(ud.get("energy_recharge_time"))
    if n is not None: recharge=int(n)
    return current,maxv,recharge


def discover_challenge_events(data:dict[str,Any])->list[dict[str,Any]]:
    """Read live/upcoming Challenge/Melee/Clash event cards from AT's event payload.

    Type 6 is what the user's live capture uses for Finger Cutter Challenge,
    Brian's Crush Challenge, Hangover Clash, Melees, etc. Swole is also carried
    through the challenge system, so Swole-named rows are intentionally excluded
    from this Events-tab list and handled by the dedicated Swole tab.
    """
    try: now=int((data or {}).get("time") or 0)
    except Exception: now=0
    out=[];seen=set()
    for container_name,state in (("active_events","Active"),("upcoming_events","Upcoming")):
        raw=(data or {}).get(container_name) or {}
        rows=raw.values() if isinstance(raw,dict) else raw if isinstance(raw,list) else []
        for row in rows:
            if not isinstance(row,dict):continue
            try: typ=int(row.get("type"))
            except Exception: continue
            if typ!=6:continue
            name=_clean_name(row.get("name")) or f"Challenge {row.get('id')}"
            if "swole" in name.lower():continue
            rid=str(row.get("id") or row.get("challenge") or "")
            sig=(rid,name)
            if not rid or sig in seen:continue
            seen.add(sig)
            try:start=int(row.get("start_time") or 0)
            except Exception:start=0
            try:end=int(row.get("end_time") or 0)
            except Exception:end=0
            actual_state=state
            if now and start and now<start:actual_state="Upcoming"
            elif now and end and now>=end:actual_state="Ended"
            else:actual_state="Active"
            cdata=row.get("challenge_data") if isinstance(row.get("challenge_data"),dict) else {}
            cenergy=cdata.get("energy") if isinstance(cdata.get("energy"),dict) else {}
            try: current_energy=int(cenergy.get("current_value")) if cenergy.get("current_value") is not None else None
            except Exception: current_energy=None
            try: max_energy=int(cenergy.get("max_value")) if cenergy.get("max_value") is not None else None
            except Exception: max_energy=None
            out.append({
                "id":rid,
                "challenge_id":str(row.get("challenge") or cdata.get("id") or row.get("id") or ""),
                "name":name,
                "state":actual_state,
                "start_time":start,
                "end_time":end,
                "type":str(row.get("type") or ""),
                "energy":current_energy,
                "max_energy":max_energy,
                "level":_num(cdata.get("level")),
                "battle_cost":_num(cenergy.get("battle_cost")) or 1,
            })
    # Some init payloads repeat the same event in active_events and
    # upcoming_events during a boundary window. Keep the Active copy.
    dedup={}
    for r in out:
        key=(str(r.get("challenge_id") or r.get("id") or ""),str(r.get("name") or ""))
        prev=dedup.get(key)
        if prev is None or (r.get("state")=="Active" and prev.get("state")!="Active"):
            dedup[key]=r
    out=list(dedup.values())
    out.sort(key=lambda r:(0 if r.get("state")=="Active" else 1,int(r.get("start_time") or 0),str(r.get("name")).lower()))
    return out


def discover_campaign_markers(data:dict[str,Any])->list[dict[str,str]]:
    """Return human-readable Adventure campaign markers from player prefs.

    AT has exposed these values in more than one shape: depending on the event
    they may identify the current mission or the campaign itself. Callers must
    resolve the marker against both mission_data and campaign_data.
    """
    prefs=(data or {}).get("player_prefs_data") or {}
    if not isinstance(prefs,dict):return []
    labels={
        "newyear":"New Year","valentine":"Valentine","fullthrottle":"Full Throttle",
        "spring":"Spring","grillpocalypse":"Grillpocalypse","villains":"Villains",
        "themepark":"Theme Park","kothtakeover":"King of the Hill Takeover",
        "madscience":"Mad Science","anniversary":"Anniversary",
    }
    out=[]
    for k,v in prefs.items():
        sk=str(k)
        if not sk.startswith("current_campaign_"):continue
        suffix=sk[len("current_campaign_"):]
        name=labels.get(suffix.lower()) or suffix.replace("_"," ").strip().title()
        if isinstance(v,dict):v=v.get("value")
        out.append({"key":sk,"name":name,"marker":str(v)})
    return sorted(out,key=lambda r:r["name"].lower())
