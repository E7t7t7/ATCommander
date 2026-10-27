"""Resolve Adventure routes from current campaigns and real mission IDs."""
import re
from .local_commands import CommandInputError,CommandUnavailable
from .utility_commands import rows,integer
from .research_automation import number


def _index(source, fields, label):
    if not isinstance(source,(dict,list)):
        raise CommandUnavailable(f"Current {label} metadata is unavailable.")
    found={}
    for row in rows(source):
        identities=[number(row[key]) for key in fields if key in row]
        if not identities or any(value is None or value<=0 for value in identities) or len(set(identities))!=1:
            raise CommandUnavailable(f"Current {label} identities are invalid or conflicting.")
        key=str(identities[0])
        if key in found:raise CommandUnavailable(f"Current {label} identities are duplicated.")
        found[key]=row
    return found


def resolve_route(service,live,selection,explicit=None):
    if explicit is not None and selection:
        raise CommandInputError("Choose a mission ID or an Adventure level, rather than both.")
    current=_index(live.get("current_missions"),("id","mission_id"),"mission")
    progress=live.get("mission_completions")
    completed={}
    if isinstance(progress,(dict,list)):
        for key,row in _index(progress,("id","mission_id"),"mission completion").items():
            count=number(row.get("complete"))
            if count is None or count<0:raise CommandUnavailable("Current mission completion counts are unavailable or invalid.")
            completed[key]=count
    elif explicit is None:
        raise CommandUnavailable("Current mission completion counts are unavailable; route progression cannot be verified.")
    campaigns=service.catalog("campaign")
    if not isinstance(campaigns,dict):raise CommandUnavailable("The current campaign catalog is unavailable.")
    active=_index(live.get("current_campaigns"),("id","campaign_id"),"campaign")
    def campaign_ready(cid,meta):
        now=number(live.get("time")) or int(service.clock())
        row=active.get(str(cid)) or {}
        # Explicit current windows also apply to temporary campaigns. A stale
        # saved completion must not reopen an ended island.
        for key in ("start_time","end_time"):
            if key in row:
                value=number(row[key])
                if value is None or value<0:raise CommandUnavailable("The current campaign time window is invalid.")
                if key=="start_time" and value>now or key=="end_time" and value and now>=value:return False
        if str(cid) in active:return True
        requirement=str(meta.get("campaign_req") or "")
        return str(cid)=="1" or requirement in active and number(active[requirement].get("completed")) is not None and number(active[requirement]["completed"])>0
    def candidates(cid,meta):
        out=[];previous=None
        if not isinstance(meta.get("missions"),(list,tuple)):
            raise CommandUnavailable("The selected campaign's mission order is unavailable.")
        seen=set()
        for stage,mid in enumerate(meta.get("missions") or [],1):
            value=number(mid)
            if value is None or value<=0 or value in seen:raise CommandUnavailable("The campaign's real mission IDs are invalid or duplicated.")
            seen.add(value);mid=str(value)
            if mid not in current:break
            if previous is not None and completed.get(previous,0)<1:break
            out.append((cid,stage,mid,meta));previous=mid
        return out
    selection=str(selection or "").strip().casefold()
    scoped=re.fullmatch(r"(\d+)\s*-\s*(progress|mastery)",selection)
    if explicit is not None:
        mid=number(explicit)
        if mid is None or mid<=0:raise CommandInputError("Choose a positive real mission ID.")
        chosen=(None,None,str(mid),{})
    elif selection in {"progress","mastery"} or scoped:
        available=[]
        # Main-story chapters use low campaign IDs. Seasonal/tutorial/takeover
        # campaigns require an explicit selected chapter/mission.
        requested=str(int(scoped[1])) if scoped else None
        route_kind=scoped[2] if scoped else selection
        if requested is not None and requested not in campaigns:raise CommandInputError("That chapter is not in the current campaign catalog.")
        for cid,meta in campaigns.items():
            if not isinstance(meta,dict):continue
            if requested is not None and str(cid)!=requested:continue
            if requested is None and (not str(cid).isdigit() or not 0<int(cid)<1000):continue
            if campaign_ready(cid,meta):available.extend(candidates(cid,meta))
            elif requested is not None:raise CommandUnavailable("That chapter is not currently unlocked or active.")
        available.sort(key=lambda r:(integer(r[0]),r[1]))
        if route_kind=="mastery":
            available=[r for r in available if completed.get(r[2],0)<12]
            if not available:return None
            chosen=available[0]
        else:
            if not available:raise CommandUnavailable("No unlocked story mission was exposed.")
            available=[r for r in available if completed.get(r[2],0)<1]
            if not available:return None
            chosen=available[-1]
    else:
        match=re.fullmatch(r"(\d+)\s*-\s*(\d+)",str(selection or ""))
        if not match:raise CommandInputError("Choose a mission ID, chapter-stage such as 23-1, progress/mastery, or a scoped route such as 9040-progress.")
        cid,stage=match.groups();meta=campaigns.get(str(int(cid)))
        if not meta:raise CommandInputError("That chapter is not in the current campaign catalog.")
        if not campaign_ready(str(int(cid)),meta):raise CommandUnavailable("That chapter is not currently unlocked.")
        chosen=next((r for r in candidates(str(int(cid)),meta) if r[1]==int(stage)),None)
        if chosen is None:raise CommandUnavailable("That stage is not currently unlocked or exposed.")
    cid,stage,mid,meta=chosen
    dynamic=current.get(mid)
    if dynamic is None:raise CommandUnavailable("AT did not expose live state for that mission.")
    cost=number(dynamic.get("energy"))
    if cost is None or cost<0:raise CommandUnavailable("The current mission's energy cost could not be verified.")
    return {"mission":integer(mid),"chapter":cid,"stage":stage,"name":meta.get("name"),
            "energy_cost":cost,"level":dynamic.get("level"),"mastery_wins":completed.get(mid,0)}
