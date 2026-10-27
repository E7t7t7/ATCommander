"""Exact static card levels and portable local stat sheets.

Card upgrades contain sparse deltas: absent fields inherit earlier levels.
Fusion comes from explicit owned fields or the client's CardLevel table; it is
never guessed from rarity or divided from the current level.
"""
from __future__ import annotations

from copy import deepcopy
from html import escape
from pathlib import Path
import uuid

from .cm_reviews import SKILL_NAMES, _traits

RARITIES = {1: "Common", 2: "Rare", 3: "Epic", 4: "Legendary", 5: "Mythic"}
FUSIONS = {0: "Single", 1: "Dual", 2: "Quad"}


def _int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def upgrades(card):
    raw = card.get("upgrade", card.get("upgrades", []))
    if isinstance(raw, dict):
        raw = [{"level": key, **row} for key, row in raw.items() if isinstance(row, dict)]
    if not isinstance(raw, list):
        return []
    return sorted((deepcopy(row) for row in raw if isinstance(row, dict) and (_int(row.get("level")) or 0) >= 1),
                  key=lambda row: _int(row["level"]))


def max_level(card):
    return max([_int(card.get("level")) or 1] + [_int(row["level"]) for row in upgrades(card)])


def at_level(card, level=None):
    """Return a copy with exactly the requested level's inherited stats."""
    base_level = _int(card.get("level")) or 1
    chosen = base_level if level is None else _int(level)
    if chosen is None or not base_level <= chosen <= max_level(card):
        raise ValueError("That level is not present in this card's current static data.")
    out = deepcopy(card)
    out.pop("upgrade", None)
    out.pop("upgrades", None)
    for delta in upgrades(card):
        if _int(delta["level"]) <= chosen:
            if "skill" in delta:
                out.pop("skills", None)
            elif "skills" in delta:
                out.pop("skill", None)
            out.update({key: deepcopy(value) for key, value in delta.items() if key != "level"})
    out["level"] = chosen
    out["max_level"] = max_level(card)
    return out


def card_level_definitions(source):
    """Find only authoritative {rarity, level, fusion_level} table rows."""
    found = {}
    def walk(obj, depth=0):
        if depth > 7:
            return
        if isinstance(obj, dict):
            if {"rarity", "level", "fusion_level"} <= obj.keys():
                rarity, level, fusion = (_int(obj[k]) for k in ("rarity", "level", "fusion_level"))
                if rarity in RARITIES and level and level > 0 and fusion in FUSIONS:
                    key = (rarity, level)
                    # Conflicting definitions must not silently choose a fusion.
                    found[key] = fusion if key not in found or found[key] == fusion else None
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)
        elif isinstance(obj, list):
            for value in obj:
                walk(value, depth + 1)
    walk(source)
    return found


def fusion_level(owned, card, definitions=None):
    for key in ("fusion_level", "fuse_level", "fusion"):
        if key in owned:
            fusion = _int(owned[key])
            if fusion in FUSIONS:
                return fusion
    return (definitions or {}).get((_int(card.get("rarity")), _int(owned.get("level", card.get("level", 1)))))


def skill_labels(card):
    raw = card.get("skill", card.get("skills", []))
    values = raw.values() if isinstance(raw, dict) else raw if isinstance(raw, list) else []
    labels = []
    for skill in values:
        if not isinstance(skill, dict):
            continue
        sid = str(skill.get("id", skill.get("skill_id", ""))).lower()
        label = SKILL_NAMES.get(sid, sid.replace("_", " ").title())
        if not label:
            continue
        value = next((skill[k] for k in ("x", "v", "value", "amount") if k in skill), None)
        labels.append(label + (f" {value}" if value is not None else ""))
    return labels


def summary(card):
    """A shareable presentation row with no private card-instance identifiers."""
    out = {key: card[key] for key in ("name", "level", "max_level", "attack", "health", "power", "card_type", "fusion_level") if key in card}
    out["card_id"] = str(card.get("unit_id", card.get("card_id", card.get("id", ""))))
    out["rarity"] = RARITIES.get(_int(card.get("rarity")), "Unknown")
    out["fusion"] = FUSIONS.get(_int(card.get("fusion_level")), "Unknown")
    out["skills"] = skill_labels(card)
    out["traits"] = _traits(card)
    for key in ("attack_multiplier", "health_multiplier", "stats_status", "recipes"):
        if key in card:
            out[key] = deepcopy(card[key])
    return out


def stat_sheet_svg(cards):
    """Generate a stat sheet, with escaped text and no scripts/external assets."""
    if not 1 <= len(cards) <= 50:
        raise ValueError("SVG stat sheets support 1–50 cards. Narrow the filters first.")
    columns = min(3, len(cards))
    width, height = columns * 340 + 24, ((len(cards) + columns - 1) // columns) * 285 + 24
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="#0b1020"/>']
    for index, card in enumerate(cards):
        data = summary(card)
        x, y = 12 + index % columns * 340, 12 + index // columns * 285
        lines = [str(data.get("name", "Unknown"))[:43],
                 f'Card {data["card_id"]} · {data["rarity"]}',
                 f'Level {data.get("level", "—")} · {data["fusion"]}',
                 f'Attack {data.get("attack", "—")}   Health {data.get("health", "—")}   Power {data.get("power", "—")}',
                 *data["skills"][:5]]
        if data.get("attack_multiplier") is not None or data.get("health_multiplier") is not None:
            lines.append(f'Combo scaling: ATK {data.get("attack_multiplier", "—")} / HP {data.get("health_multiplier", "—")}')
        if data.get("stats_status"):
            lines.append(str(data["stats_status"]))
        parts.append(f'<rect x="{x}" y="{y}" width="328" height="273" rx="16" fill="#182238" stroke="#435473"/>')
        for line_index, line in enumerate(lines[:10]):
            size = 18 if line_index == 0 else 13
            parts.append(f'<text x="{x + 16}" y="{y + 30 + line_index * 23}" font-family="Segoe UI,Arial,sans-serif" font-size="{size}" fill="#edf2ff">{escape(str(line))}</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def export_stat_sheet(cards, directory):
    svg = stat_sheet_svg(cards)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"card-stats-{uuid.uuid4().hex[:12]}.svg"
    target.write_text(svg, encoding="utf-8")
    return target.resolve()
