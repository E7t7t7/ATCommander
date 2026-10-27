"""Fresh, guarded saved-deck replacement; no retries or automatic rollback."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json

from .local_commands import CommandInputError, CommandUnavailable
from .research_automation import number
from .shop_rewards import api_rejected


def _identity(value, *, allow_zero=False):
    parsed=number(value)
    return str(parsed) if parsed is not None and parsed>=(0 if allow_zero else 1) else None


def _entries(value, label):
    if isinstance(value,dict):return list(value.items())
    if isinstance(value,list):return [(None,row) for row in value]
    raise CommandUnavailable(f"Current {label} metadata is unavailable. No deck request was sent.")


def _unit_indices(value):
    found=[]
    for key,row in _entries(value,"card instances"):
        if not isinstance(row,dict):raise CommandUnavailable("A card instance is malformed. No deck request was sent.")
        index=_identity(row.get("unit_index",row.get("id",key)),allow_zero=True)
        if index is None:raise CommandUnavailable("A card instance is malformed. No deck request was sent.")
        found.append(index)
    if len(set(found))!=len(found):raise CommandUnavailable("The deck contains duplicated card instances. No deck request was sent.")
    return tuple(found)


@dataclass(frozen=True)
class SavedDeck:
    deck_id:str
    commander_id:str
    units:tuple[str,...]

    def same(self,other):
        return self.commander_id==other.commander_id and set(self.units)==set(other.units)


def _saved(saved,deck_id,*,require_full=True):
    if not isinstance(saved,dict):raise CommandUnavailable("The saved deck payload is unavailable.")
    commander=saved.get("commander")
    values=[saved["commander_id"]] if "commander_id" in saved else []
    if isinstance(commander,dict):
        values.extend(commander[key] for key in ("unit_id","commander_id","hero_id") if key in commander)
    identities=[_identity(value) for value in values]
    if not identities or None in identities or len(set(identities))!=1:
        raise CommandUnavailable("The saved deck's exact commander is invalid or ambiguous. No deck request was sent.")
    units=_unit_indices(saved.get("units"))
    if not (25 if require_full else 0)<=len(units)<=35:raise CommandUnavailable("A replacement deck needs 25–35 unique owned card instances. No deck request was sent.")
    return SavedDeck(str(deck_id),identities[0],units)


def _deck(data,deck_id):
    matches=[]
    for key,row in _entries(data.get("user_decks"),"saved decks"):
        if not isinstance(row,dict):raise CommandUnavailable("Current saved-deck metadata is malformed.")
        rid=_identity(row.get("deck_id",row.get("id",key)))
        if _identity(key)==str(deck_id) and rid!=str(deck_id):
            raise CommandUnavailable("A saved-deck identity conflicts with its current slot.")
        if rid==str(deck_id):matches.append(row)
    if len(matches)!=1:raise CommandUnavailable("The requested saved-deck slot is missing or ambiguous.")
    return _saved(matches[0],deck_id,require_full=False),matches[0]


def _stop(service):
    stop=getattr(service,"stop",None)
    if stop is not None and stop.is_set():
        raise CommandUnavailable("Deck replacement stopped between requests. Recovery backups were preserved.")


def _fresh(service):
    service.context.invalidate()
    data=service.snapshot(refresh=True)
    if not isinstance(data,dict) or not data or api_rejected(data):
        raise CommandUnavailable("Fresh deck state could not be read. No deck request was replayed.")
    if _identity((data.get("user_data") or {}).get("active_deck")) is None:
        raise CommandUnavailable("The fresh active deck could not be verified.")
    if not isinstance(data.get("user_decks"),(dict,list)):
        # This established exact-deck request receives only the fresh current
        # active ID. Never pass a saved pre-swap snapshot to it.
        exact=service.client.get_exact_decks(data)
        if not isinstance(exact,dict) or api_rejected(exact):raise CommandUnavailable("The exact saved decks could not be read.")
        data={**data,**exact}
    return data


def _owned(data,desired):
    if not 25<=len(desired.units)<=35:raise CommandUnavailable("A replacement deck needs 25–35 unique owned card instances. No deck request was sent.")
    owned=set(_unit_indices(data.get("user_units")))
    if not set(desired.units)<=owned:
        raise CommandUnavailable("Some replacement card instances are no longer owned. No deck request was sent.")


def _guard(data,active,expected):
    if _identity((data.get("user_data") or {}).get("active_deck"))!=active:
        raise CommandUnavailable("The active deck changed during replacement. That selection was preserved; no further request was sent.")
    for deck_id,saved in expected.items():
        current,_=_deck(data,deck_id)
        if not current.same(saved):
            raise CommandUnavailable("A saved deck changed during replacement. The current state and recovery backups were preserved; no request was repeated.")


def _backup(row,saved):
    return {"deck_id":saved.deck_id,"name":str(row.get("name") or ""),
            "commander":{"unit_id":saved.commander_id},"units":[{"unit_index":int(index)} for index in saved.units]}


def _apply_once(service,desired,active,expected):
    _stop(service)
    before=_fresh(service);_guard(before,active,expected);_owned(before,desired)
    response=None
    try:
        response=service.call("setDeckUnits",{"deck_id":desired.deck_id,"commander_index":desired.commander_id,
            "units":json.dumps([int(index) for index in desired.units],separators=(",",":"))})
    except CommandUnavailable:
        raise CommandUnavailable("The deck write was rejected or unconfirmed. Its request was not replayed; recovery backups were preserved.") from None
    except Exception:
        # A timeout can have applied. The fresh read below is the only way to
        # reconcile it; this helper never repeats a write or blindly rolls back.
        pass
    if response is not None and (not isinstance(response,dict) or not response or api_rejected(response)):
        raise CommandUnavailable("AT did not confirm the deck write. No request was replayed; recovery backups were preserved.")
    expected={**expected,desired.deck_id:desired}
    after=_fresh(service)
    try:_guard(after,active,expected)
    except CommandUnavailable:
        raise CommandUnavailable("The deck write or another saved slot changed unexpectedly. No further write or rollback was sent; use the recovery backups after reviewing the fresh state.") from None
    return expected


def swap_saved_decks(service,deck,other):
    """Validate both originals, back up both, and verify each single write."""
    one_id,two_id=_identity(deck),_identity(other)
    if one_id is None or two_id is None or one_id==two_id:
        raise CommandInputError("Choose two different positive saved-deck slots.")
    _stop(service);data=_fresh(service)
    one,one_row=_deck(data,one_id);two,two_row=_deck(data,two_id)
    # Validate both directions before the first mutation, including ownership.
    first=SavedDeck(one_id,two.commander_id,two.units);second=SavedDeck(two_id,one.commander_id,one.units)
    _owned(data,first);_owned(data,second)
    backups=deepcopy(service.store.read().get("decks",{}))
    names=["Before swap slot "+one_id,"Before swap slot "+two_id]
    backups[names[0]]=_backup(one_row,one);backups[names[1]]=_backup(two_row,two)
    service.store.update("decks",backups)
    active=_identity(data["user_data"]["active_deck"])
    expected=_apply_once(service,first,active,{one_id:one,two_id:two})
    _apply_once(service,second,active,expected)
    return {"verified":True,"deck":int(one_id),"other":int(two_id),"backups":names}


def restore_saved_deck(service,deck,saved):
    """Restore once using current ownership and a fresh destination guard."""
    deck_id=_identity(deck)
    if deck_id is None:raise CommandInputError("Choose a positive saved-deck slot.")
    _stop(service);data=_fresh(service)
    original,row=_deck(data,deck_id);desired=_saved(saved,deck_id);_owned(data,desired)
    backups=deepcopy(service.store.read().get("decks",{}));name="Before restore slot "+deck_id
    backups[name]=_backup(row,original);service.store.update("decks",backups)
    active=_identity(data["user_data"]["active_deck"])
    _apply_once(service,desired,active,{deck_id:original})
    return {"verified":True,"deck":int(deck_id),"backup":name}
