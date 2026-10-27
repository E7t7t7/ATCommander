"""Current public profile/deck data; no member account credentials are needed."""
from .local_commands import CommandUnavailable


def public_profile(service, player):
    cache = getattr(service.context, "_public_profiles", {})
    key = str(player)
    cached = cache.get(key)
    if cached and service.clock() - cached[0] < 300:return cached[1]
    response = service.call("getUserProfile", {"target_user_id":player})
    info = response.get("user_profile_info")
    if not isinstance(info, dict) or not info:
        raise CommandUnavailable("AT did not provide the selected player's public profile.")
    cache[key] = (service.clock(), info)
    service.context._public_profiles = cache
    return info


def profile_decks(info):
    data = info.get("decks_data") or {}
    raw = data.get("decks") or {}
    if isinstance(raw, dict):
        decks = [{"deck_id":key, **row} for key,row in raw.items() if isinstance(row,dict)]
    elif isinstance(raw, list):decks = [row for row in raw if isinstance(row,dict)]
    else:decks = []
    active = data.get("active_deck")
    return decks, active


def mapped_public_cards(deck, catalog):
    raw = deck.get("units") or []
    units = raw.values() if isinstance(raw,dict) else raw if isinstance(raw,list) else []
    cards = []
    for unit in units:
        if not isinstance(unit,dict):continue
        cid = unit.get("unit_id",unit.get("card_id"))
        meta = catalog.get(str(cid),{})
        # Public exports include card definitions, never private instance indices.
        cards.append({"card_id":cid,"name":meta.get("name","Unavailable"),
                      "level":unit.get("level"),"foil_id":unit.get("foil_id"),
                      "card_type":meta.get("card_type"),"rarity":meta.get("rarity")})
    return cards
