"""Owned-card deck suggestions and guarded, single-request deck edits."""
from copy import deepcopy
import math
from .card_presentation import at_level
from .cm_reviews import _traits, _is_pc
from .local_commands import CommandInputError, CommandUnavailable
from .research_automation import rows, number, pair_key, researched_pairs
from .deck_swap_safety import (_fresh, _deck, _guard, _owned, _stop, _backup,
                               _apply_once, SavedDeck)
from .shop_rewards import api_rejected

RULES = ("Balanced recommendation", "One character + items", "BGE / trait combos", "Characters only")


def _check(stop):
    if stop and stop(): raise CommandUnavailable("Deck building stopped. No saved deck was changed.")


def _strength(card):
    skills = card.get("skill", card.get("skills", []))
    skills = skills.values() if isinstance(skills, dict) else skills if isinstance(skills, list) else []
    total = sum(math.sqrt(max(0, number(s.get("x", s.get("value", 0))) or 0)) for s in skills if isinstance(s, dict))
    return 2 * (number(card.get("attack")) or 0) + (number(card.get("health")) or 0) + 2 * total


def owned_cards(snapshot, catalog, *, should_stop=None):
    if not isinstance(snapshot.get("user_units"), (dict, list)):
        raise CommandUnavailable("The owned card pool is unavailable.")
    result = []; seen = set()
    for offset, unit in enumerate(rows(snapshot["user_units"])):
        if offset % 100 == 0: _check(should_stop)
        identity = number(unit.get("unit_index", unit.get("id")))
        cid = str(unit.get("unit_id", unit.get("card_id", "")))
        meta = catalog.get(cid)
        if identity is None or identity < 0 or not isinstance(meta, dict): continue
        if identity in seen: raise CommandUnavailable("The owned pool contains duplicated card instances.")
        seen.add(identity)
        kind = str(meta.get("card_type", "")).lower()
        if _is_pc(meta): kind = "final_form"
        if kind not in {"character", "object", "final_form"}: continue
        rarity = number(meta.get("rarity"))
        if rarity not in range(1, 6): continue
        try: current = at_level(meta, unit.get("level", 1))
        except ValueError: continue
        result.append({"unit_index": identity, "card_id": cid, "name": str(meta.get("name") or cid),
            "kind": kind, "rarity": rarity, "level": current["level"], "traits": _traits(current),
            "attack": number(current.get("attack")) or 0, "health": number(current.get("health")) or 0,
            "strength": _strength(current)})
    return sorted(result, key=lambda r: (-r["strength"], -r["rarity"], r["name"], r["unit_index"]))


def suggest_deck(snapshot, catalog, recipes, *, rule=RULES[0], character="", trait="", size=25,
                 min_rarity=3, power_cards=0, researched_only=True, should_stop=None):
    if rule not in RULES or isinstance(size, bool) or not isinstance(size, int) or size not in range(25, 36):
        raise CommandInputError("Choose a deck rule and a size from 25 to 35.")
    if isinstance(min_rarity, bool) or not isinstance(min_rarity, int) or min_rarity not in range(1, 6):
        raise CommandInputError("Choose an input rarity from 1 to 5.")
    if isinstance(power_cards, bool) or not isinstance(power_cards, int) or not 0 <= power_cards <= size:
        raise CommandInputError("Choose a power-card count between zero and the deck size.")
    if not isinstance(researched_only, bool): raise CommandInputError("Research filtering must be on or off.")
    if rule == "One character + items" and not character.strip(): raise CommandInputError("Choose the character to build around.")
    if rule == "BGE / trait combos" and not trait.strip(): raise CommandInputError("Choose the BGE / trait for combo results.")
    if rule in {"One character + items", "Characters only"} and power_cards:
        raise CommandInputError("Set power cards to zero for a character-focused rule.")
    if rule == "Characters only" and trait.strip(): raise CommandInputError("Clear the combo-trait filter for a characters-only deck.")
    pool = [r for r in owned_cards(snapshot, catalog, should_stop=should_stop) if r["rarity"] >= min_rarity]
    chars = [r for r in pool if r["kind"] == "character" and (not character or r["name"].casefold() == character.strip().casefold())]
    if rule == "Characters only":
        if len(chars) < size: raise CommandUnavailable(f"Only {len(chars)} matching character copies are owned; {size} are needed. The rule was not widened.")
        return {"cards": chars[:size], "note": "Characters only; this rule deliberately has no item combos.", "coverage": None}
    pcs = [r for r in pool if r["kind"] == "final_form" and (not trait or trait.casefold() in {v.casefold() for v in r["traits"]})]
    if len(pcs) < power_cards: raise CommandUnavailable("Not enough matching owned power cards for that count.")
    remaining = size - power_cards
    if not remaining: return {"cards": pcs[:size], "note": "Power-card-only draft.", "coverage": None}
    nc = (remaining + 1) // 2; ni = remaining - nc
    if not ni: raise CommandInputError("Leave at least two non-PC slots for a character/item pair, or use only PCs.")
    items = [r for r in pool if r["kind"] == "object"]
    if len(chars) < nc or len(items) < ni: raise CommandUnavailable("There are not enough owned character/item copies at the selected rarity. Lower the rarity or change the rule.")
    if not isinstance(recipes, dict) or not recipes: raise CommandUnavailable("The current recipe map is unavailable.")
    known = researched_pairs(snapshot) if researched_only else None
    char_ids = {r["card_id"] for r in chars}; item_ids = {r["card_id"] for r in items}
    edges = {}
    for index, (raw, value) in enumerate(recipes.items()):
        if index % 200 == 0: _check(should_stop)
        parts = str(raw).split("~")
        if len(parts) != 2: continue
        a, b = parts
        if a in item_ids and b in char_ids: a, b = b, a
        if a not in char_ids or b not in item_ids: continue
        if known is not None and pair_key(a, b) not in known: continue
        combo_id = str(value).split("|", 1)[0]; combo = catalog.get(combo_id)
        if not isinstance(combo, dict): continue
        if trait and trait.casefold() not in {t.casefold() for t in _traits(combo)}: continue
        previous = edges.get((a, b))
        if previous is not None and previous[0] != combo_id: raise CommandUnavailable("A recipe result is ambiguous; no deck was guessed.")
        edges[a, b] = (combo_id, _strength(combo))
    chars = [r for r in chars if any((r["card_id"], iid) in edges for iid in item_ids)]
    items = [r for r in items if any((cid, r["card_id"]) in edges for cid in char_ids)]
    if len(chars) < nc or len(items) < ni: raise CommandUnavailable("Not enough owned copies make matching researched combos under these rules. The rule was not widened.")
    potentials = {}
    for cid in {r["card_id"] for r in chars}:
        _check(should_stop)
        values = sorted((v[1] for (c, _), v in edges.items() if c == cid), reverse=True)[:5]
        potentials[cid] = sum(values) / len(values) if values else 0
    chars.sort(key=lambda r: (-(potentials[r["card_id"]] + r["strength"]), r["unit_index"]))
    chosen_chars = chars[:nc]
    for iteration in range(2):
        _check(should_stop)
        def item_rank(card):
            compatible = [edges[r["card_id"], card["card_id"]][1] for r in chosen_chars if (r["card_id"], card["card_id"]) in edges]
            return (len(compatible), sum(compatible) / len(compatible) + card["strength"] if compatible else 0, -card["unit_index"])
        chosen_items = sorted(items, key=item_rank, reverse=True)[:ni]
        selected_ids = {r["card_id"] for r in chosen_items}
        covered = [r for r in chars if any((r["card_id"], iid) in edges for iid in selected_ids)]
        if len(covered) < nc: raise CommandUnavailable("The available items cannot cover enough character copies for this rule.")
        chosen_chars = covered[:nc]
    if any(not any((c["card_id"], i["card_id"]) in edges for c in chosen_chars) for i in chosen_items):
        raise CommandUnavailable("The suggested deck has an unsupported item; change the rule and preview again.")
    count = sum((c["card_id"], i["card_id"]) in edges for c in chosen_chars for i in chosen_items)
    return {"cards": chosen_chars + chosen_items + pcs[:power_cards], "coverage": round(100 * count / (nc * ni), 1),
        "note": f"{nc} characters, {ni} items, {power_cards} PCs. Ranked by current owned-card stats and matching combo coverage; not a battle simulation."}


def deck_stamp(data, target):
    saved, row = _deck(data, str(target))
    return {"deck_id": saved.deck_id, "name": str(row.get("name") or ""),
            "commander": saved.commander_id, "units": sorted(saved.units)}


def _expected(data, target, expected):
    if not isinstance(expected, dict) or deck_stamp(data, target) != expected:
        raise CommandUnavailable("That saved deck changed since it was loaded. Refresh it before editing; no write was sent.")


def rename_deck(service, target, name, expected):
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 40 or any(ord(c) < 32 for c in name):
        raise CommandInputError("Enter a deck name of 1–40 characters without line breaks.")
    name = name.strip(); _stop(service); data = _fresh(service); _expected(data, target, expected)
    original, row = _deck(data, str(target)); active = str(data["user_data"]["active_deck"])
    if row.get("name") == name: return {"snapshot": data, "message": "This deck already has that name."}
    backups = deepcopy(service.store.read().get("decks", {})); backups[f"Before rename slot {target}"] = _backup(row, original)
    service.store.update("decks", backups); _stop(service)
    response = None
    try: response = service.call("setDeckName", {"deck_id": str(target), "name": name})
    except Exception: pass
    if response is not None and (not isinstance(response, dict) or api_rejected(response)):
        raise CommandUnavailable("The rename was rejected. It was not retried.")
    after = _fresh(service); _guard(after, active, {str(target): original})
    if deck_stamp(after, target)["name"] != name:
        raise CommandUnavailable("The new name was not confirmed. The rename was not repeated; refresh before editing again.")
    return {"snapshot": after, "message": f"Slot {target} renamed to {name}. All deck selectors will use the new name."}


def save_draft(service, target, cards, expected):
    _stop(service); data = _fresh(service); _expected(data, target, expected)
    original, row = _deck(data, str(target)); active = str(data["user_data"]["active_deck"])
    identities = [number(r.get("unit_index")) for r in cards if isinstance(r, dict)]
    if len(identities) != len(cards) or any(i is None or i < 0 for i in identities) or len(set(identities)) != len(identities):
        raise CommandInputError("The draft contains missing or duplicated owned card instances.")
    desired = SavedDeck(str(target), original.commander_id, tuple(str(i) for i in identities)); _owned(data, desired)
    if original.same(desired): return {"snapshot": data, "message": "This slot already contains the draft."}
    backups = deepcopy(service.store.read().get("decks", {})); backups[f"Before builder slot {target}"] = _backup(row, original)
    service.store.update("decks", backups)
    _apply_once(service, desired, active, {str(target): original})
    after = _fresh(service); _guard(after, active, {str(target): desired})
    if deck_stamp(after, target)["name"] != expected["name"]:
        raise CommandUnavailable("The cards were written but the slot name changed unexpectedly. No further write was sent; refresh before editing.")
    return {"snapshot": after, "message": f"Saved and verified {len(cards)} cards in slot {target}. Its hero and name were preserved; the previous deck is in local backups."}
