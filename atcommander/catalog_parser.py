"""Normalize observed static catalog envelopes without confusing metadata with rows."""
from __future__ import annotations
from typing import Any

_ID_KEYS={
    "card_data":("id","unit_id","card_id"),
    "mission_data":("mission_id","id"),
    "campaign_data":("campaign_id","id"),
    "item_data":("item_id","id"),
    "hero_data":("hero_id","commander_id","id","unit_id"),
}
_ALIASES={
    "card_data":("card_data","cards","units","unit_data"),
    "mission_data":("mission_data","missions","mission"),
    "campaign_data":("campaign_data","campaigns","campaign"),
    "item_data":("item_data","items","item"),
    "hero_data":("hero_data","heroes","commanders","commander_data"),
}

def normalize_static_catalog(payload:Any,key:str)->dict[str,dict[str,Any]]:
    """Accept row lists/maps and recognized envelopes for the requested catalog.

    A JSON error/config/version response is not a catalog. Callers can therefore
    continue to another confirmed URL instead of caching an empty false success.
    Each list row must identify itself; numeric dictionary keys can identify
    rows lacking a redundant `id`, as in the game's common keyed-map form.
    """
    if not isinstance(payload,(dict,list)):return {}
    if isinstance(payload,dict) and (payload.get("error") or payload.get("result") is False):return {}
    idkeys=_ID_KEYS.get(key,("id",))
    out={}

    def row_id(row:dict[str,Any],mapkey:Any=None):
        explicit=next((row[k] for k in idkeys if row.get(k) is not None),None)
        value=explicit if explicit is not None else mapkey
        return str(value) if value is not None and str(value).isdigit() else None

    def absorb(obj:Any,depth:int=0):
        if depth>4:return
        if isinstance(obj,list):
            for row in obj:
                if not isinstance(row,dict):continue
                rid=row_id(row)
                if rid is not None:out[rid]=row
        elif isinstance(obj,dict):
            rid=row_id(obj)
            if rid is not None:
                out[rid]=obj
                return
            for mapkey,row in obj.items():
                if isinstance(row,dict) and str(mapkey).isdigit():
                    rid=row_id(row,mapkey)
                    if rid is not None:out[rid]=row
            # Only named envelopes are recursive. Traversing arbitrary nested
            # attributes would turn numeric recipe/skill tables into card rows.
            for wrapper in (*_ALIASES.get(key,(key,)),"data","catalog","rows"):
                child=obj.get(wrapper)
                if isinstance(child,(dict,list)):absorb(child,depth+1)

    absorb(payload)
    return out
