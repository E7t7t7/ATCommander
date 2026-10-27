"""Build Adventure choices from actual catalog rows and explicit live links."""
from __future__ import annotations
from typing import Any

def _rows(raw:Any):
    return raw.values() if isinstance(raw,dict) else raw if isinstance(raw,list) else []

def _name(value:Any)->str:
    return str(value or "").strip()

def _parent(row:dict[str,Any])->str:
    for key in ("campaign_id","campaign","campaignId"):
        value=row.get(key)
        if isinstance(value,dict):value=value.get("campaign_id",value.get("id"))
        if value is not None and _name(value):return str(value)
    return ""

def find_adventure_campaign(rows:list[dict[str,Any]],label:str)->dict[str,Any]|None:
    """Resolve explicit campaign keys before same-name fallback.

    Anniversary has multiple Throwdown Celebration chapters. A name substring
    alone can silently bind the chosen chapter to the first same-named island.
    """
    for row in rows:
        rid=str(row.get("id") or "?")
        if any(token in label for token in (f"[Campaign {rid}]",f"[Key {rid}]")):return row
    for row in rows:
        name=_name(row.get("name"))
        if name and name in label:return row
    return None

def build_adventure_campaign_rows(campaign_catalog:dict[str,Any]|None,
                                 mission_catalog:dict[str,Any]|None,
                                 campaign_markers:list[dict[str,Any]]|None=None,
                                 active_events:Any=None,
                                 learned_event_missions:dict[str,Any]|None=None)->list[dict[str,Any]]:
    """Keep markers out of mission selectors unless a real catalog resolves them.

    Explicit campaign/mission links in live event metadata can activate a static
    island even when its internal name differs from the event label. They never
    create a guessed mission ID. Existing manually learned mission IDs remain
    available as a separate, clearly labelled fallback.
    """
    campaigns={str(k):v for k,v in (campaign_catalog or {}).items() if isinstance(v,dict)}
    missions={str(k):v for k,v in (mission_catalog or {}).items() if isinstance(v,dict)}
    markers=campaign_markers or []
    learned=learned_event_missions or {}
    groups={}
    for mid,row in missions.items():
        parent=_parent(row)
        if not parent:continue
        stage=next((row[k] for k in ("stage","stage_id","mission_stage","level") if row.get(k) not in (None,"")),None)
        island=next((row[k] for k in ("island","island_id","chapter") if row.get(k) not in (None,"")),None)
        try:order=int(row.get("order") or stage or mid)
        except (ValueError,TypeError):order=999999
        groups.setdefault(parent,[]).append({"id":mid,"name":_name(row.get("name") or row.get("title")),"order":order,"stage":stage,"island":island})
    # Native mission definitions do not carry a campaign parent. Their exact
    # ordered membership lives in campaign_data.missions; use that link before
    # requiring optional enriched mission fields or any manually learned map.
    for cid,campaign in campaigns.items():
        ordered=campaign.get("missions")
        if not isinstance(ordered,(list,tuple)):continue
        linked=[];seen=set()
        for index,identity in enumerate(ordered,1):
            mid=str(identity)
            if not mid.isdigit() or int(mid)<=0 or mid in seen:continue
            seen.add(mid);row=missions.get(mid)
            if row is None or _parent(row) not in ("",cid):continue
            linked.append({"id":mid,"name":_name(row.get("name") or row.get("title")),
                           "order":index,"stage":index,"island":None})
        groups[cid]=linked
    for rows in groups.values():
        rows.sort(key=lambda x:(x["order"],x["name"]))
        for index,row in enumerate(rows,1):
            label=(f"Stage {row['island']}-{row['stage']}" if row["island"] is not None and row["stage"] is not None else
                   f"Stage {row['stage']}" if row["stage"] is not None else f"Stage {index}")
            if row["name"] and not row["name"].lower().startswith("mission "):label+=f" — {row['name']}"
            row["display"]=label

    active_ids=set();names_by_id={};resolved_marker_names=set();event_names=set()
    for marker in markers:
        value=_name(marker.get("marker"));name=_name(marker.get("name"))
        parent=_parent(missions[value]) if value in missions else value if value in campaigns or value in groups else ""
        if parent:
            active_ids.add(parent);names_by_id.setdefault(parent,name);resolved_marker_names.add(name.lower())

    def event_links(obj:Any,depth=0)->set[str]:
        result=set()
        if depth>4:return result
        if isinstance(obj,dict):
            for key,value in obj.items():
                if key in ("campaign_id","campaignId","campaign") and not isinstance(value,(dict,list)):
                    cid=str(value)
                    if cid in campaigns or cid in groups:result.add(cid)
                elif key in ("mission_id","missionId") and str(value) in missions:
                    parent=_parent(missions[str(value)])
                    if parent:result.add(parent)
                if isinstance(value,(dict,list)):result.update(event_links(value,depth+1))
        elif isinstance(obj,list):
            for value in obj:result.update(event_links(value,depth+1))
        return result

    for event in _rows(active_events):
        if not isinstance(event,dict):continue
        links=event_links(event)
        relevant=str(event.get("type"))=="11" or bool(links)
        name=_name(event.get("name"))
        if not relevant or not name:continue
        event_names.add(name.lower())
        for cid in links:active_ids.add(cid);names_by_id.setdefault(cid,name)
        if links:resolved_marker_names.add(name.lower())

    result=[]
    for cid,row in campaigns.items():
        name=_name(row.get("name") or row.get("title")) or f"Campaign {cid}"
        active=cid in active_ids or name.lower() in event_names
        if groups.get(cid) or active:
            result.append({"id":cid,"name":name,"missions":groups.get(cid,[]),"source":"static","active":active})
    for cid,rows in groups.items():
        if cid not in campaigns and cid in active_ids:
            result.append({"id":cid,"name":names_by_id.get(cid) or f"Event Island {cid}","missions":rows,"source":"mission_data_dynamic","active":True})
    existing_names={row["name"].lower() for row in result}|resolved_marker_names
    for marker in markers:
        name=_name(marker.get("name"))
        if not name or name.lower() in existing_names:continue
        # Seasonal prefs survive after an event closes. Fresh active_events is
        # authoritative for unresolved live placeholders; old prefs must not
        # make every historical seasonal island appear ACTIVE indefinitely.
        if active_events is not None and name.lower() not in event_names:continue
        saved=learned.get(name.lower())
        mid=_name(saved.get("mission_id")) if isinstance(saved,dict) else _name(saved)
        mapped=[{"id":mid,"name":"Learned current event stage","display":f"Learned current stage — mission {mid}","learned_event":True}] if mid.isdigit() and int(mid)>0 else []
        result.append({"id":f"live:{name.lower()}","name":name,"missions":mapped,
                       "source":"learned_event_mission" if mapped else "player_prefs_live_marker",
                       "active":True,"marker":_name(marker.get("marker"))})
        existing_names.add(name.lower())
    for name in event_names:
        if name not in existing_names:
            result.append({"id":"","name":name.title(),"missions":[],"source":"active_event","active":True})
    return sorted(result,key=lambda row:(0 if row["active"] else 1,row["name"].lower()))
