"""Pure planning for the native daily commander_ip requirement.

The public client serializes Card.IP as the static card field ``type`` and
CardIP.KingOfTheHill is 4. A squad hero's exact ID joins to that static card.
Names and numeric hero ID ranges never determine its show. This module sends
no requests and cannot change a deck or start a fight.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .account_state import FALLBACK_HERO_NAMES


IP_NAMES = {
    1: "Family Guy", 2: "American Dad", 3: "Bob's Burgers",
    4: "King of the Hill", 5: "Futurama", 6: "Generic", 7: "Archer",
}


def _number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _identity(value: Any) -> str | None:
    number = _number(value)
    return str(number) if number is not None and number > 0 else None


def commander_ip_requirement(quest: Mapping[str, Any] | None) -> int | None:
    """Read only req.commander_ip; display names cannot imply this requirement."""
    if not isinstance(quest, Mapping) or not isinstance(quest.get("req"), Mapping):
        return None
    value = _number(quest["req"].get("commander_ip"))
    return value if value in IP_NAMES else None


def _catalog_row(catalog: Any, hero_id: str) -> Mapping[str, Any] | None:
    if not isinstance(catalog, Mapping):
        return None
    row = catalog.get(hero_id, catalog.get(int(hero_id)))
    if not isinstance(row, Mapping):
        return None
    explicit = next((row[key] for key in ("hero_id", "commander_id", "id", "unit_id") if row.get(key) is not None), None)
    if explicit is not None and _identity(explicit) != hero_id:
        return None
    return row


def plan_commander_ip(
    requirement: Any,
    hero_status: Mapping[str, Any] | None,
    card_catalog: Mapping[str, Any] | None = None,
    hero_catalog: Mapping[str, Any] | None = None,
    current_hero_id: Any = None,
) -> dict[str, Any]:
    """Select a confirmed unlocked matching hero without performing any action.

    The current matching hero is preferred. Otherwise choose the highest explicit
    battle level, with name and ID for stable ties. Training level never substitutes
    for battle level. Missing/conflicting static type metadata stays unresolved.
    """
    required = _number(requirement)
    result: dict[str, Any] = {
        "required_ip": required, "show": IP_NAMES.get(required, "Unknown show"),
        "candidates": [], "selected": None, "unresolved": [], "reason": "",
    }
    if required not in IP_NAMES:
        result["reason"] = "The current commander_ip requirement is unavailable or unsupported."
        return result
    if not isinstance(hero_status, Mapping) or hero_status.get("result") is False:
        result["reason"] = "A verified current unlocked hero squad is unavailable."
        return result
    squad = hero_status.get("user_hero_squad")
    if isinstance(squad, Mapping):
        entries = list(squad.items())
    elif isinstance(squad, list):
        entries = [(None, row) for row in squad]
    else:
        result["reason"] = "A verified current unlocked hero squad is unavailable."
        return result
    seen: set[str] = set()
    duplicate: set[str] = set()
    for map_id, row in entries:
        if not isinstance(row, Mapping):
            continue
        raw_id = next((row[key] for key in ("hero_id", "commander_id", "id") if row.get(key) is not None), map_id)
        hero_id = _identity(raw_id)
        if hero_id is None:
            continue
        if hero_id in seen:
            duplicate.add(hero_id)
            continue
        seen.add(hero_id)
        unlocked = row.get("unlocked")
        if unlocked is not True and _number(unlocked) != 1:
            continue
        catalogs = [("card_data.type", _catalog_row(card_catalog, hero_id)),
                    ("hero_data.type", _catalog_row(hero_catalog, hero_id))]
        known = [(source, meta, _number(meta.get("type")))
                 for source, meta in catalogs if meta is not None and meta.get("type") is not None]
        valid = [(source, meta, ip) for source, meta, ip in known if ip in IP_NAMES]
        if not valid or len(valid) != len(known):
            result["unresolved"].append({"hero_id": hero_id, "reason": "Static hero show metadata is unavailable or invalid."})
            continue
        if len({ip for _, _, ip in valid}) != 1:
            result["unresolved"].append({"hero_id": hero_id, "reason": "The exact hero ID has conflicting static show metadata."})
            continue
        if valid[0][2] != required:
            continue
        name = next((str(meta.get("name")).strip() for _, meta, _ in valid if meta.get("name")), "")
        name = str(row.get("name") or row.get("hero_name") or name or FALLBACK_HERO_NAMES.get(hero_id) or f"Hero {hero_id}")
        level = _number(row.get("level"))
        result["candidates"].append({
            "hero_id": hero_id, "name": name, "ip": required,
            "level": level if level is not None and level >= 0 else None,
            "source": "+".join(source for source, _, _ in valid),
        })
    if duplicate:
        result["candidates"] = [row for row in result["candidates"] if row["hero_id"] not in duplicate]
        result["unresolved"].extend({"hero_id": hero_id, "reason": "Duplicate squad rows prevent an unambiguous unlocked hero selection."} for hero_id in sorted(duplicate))
    result["candidates"].sort(key=lambda row: (-(row["level"] if row["level"] is not None else -1), row["name"].casefold(), int(row["hero_id"])))
    current = _identity(current_hero_id)
    if result["candidates"]:
        result["selected"] = next((row for row in result["candidates"] if row["hero_id"] == current), result["candidates"][0])
    else:
        result["reason"] = f"No unlocked {result['show']} hero has a verified matching static type."
    return result
