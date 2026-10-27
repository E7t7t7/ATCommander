from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from typing import Any

from .config import APP_DIR
from .account_state import discover_id_name_catalog

SESSION_FILE = APP_DIR / "current_session.json"
SESSION_HISTORY_FILE = APP_DIR / "session_history.jsonl"

# Human-readable names confirmed from the user's live inventory and AT store data.
# Unknown IDs are intentionally not guessed; the UI hides them by default.
FALLBACK_ITEM_NAMES = {
    "51": "VIP Point",
    "204": "Card Inventory Expander",
    "208": "Watt Expander",
    "215": "Adventure Energy Upgrade",
    "216": "Research Accelerator",
    "1023": "Arena Refill +5",
    "1024": "Arena Refill +10",
    "1031": "Research Speedup +1",
    "3002": "Rare Pack",
    "4001": "Giggitywatts",
    "5066": "King of the Hill Season Pass",
    "5067": "Mad Science Season Pass",
    "5068": "Anniversary Season Pass",
    "21000": "Challenge Coin",
    "30001": "Ad Crate",
    "200002": "Epic Stone",
    "200003": "Legendary Stone",
    "200004": "Power Stone",
    "200005": "Golden Turd",
    "200008": "Mastery Stone",
    "200009": "Greater Epic Stone",
    "200010": "Super Mythic Stone",
    "200011": "Mythic Object Stone",
    "200012": "Quad Epic Stone",
    "200013": "Character Stone",
    "200014": "Super Legendary Stone",
    "200015": "Legendary Power Stone",
    "200016": "Elite Stone",
    "3900042": "Anniversary Challenge Swole Coin",
}

_RESOURCE_WORDS = (
    "gold", "gem", "coin", "stone", "energy", "token", "ticket", "refill",
    "currency", "point", "credit", "wharf", "nixons", "nixon", "challenge", "arena", "trophy", "sfc", "medal", "hero", "commander",
    "watt", "salvage", "turds", "turd", "hard_currency", "soft_currency", "cash",
)


def _number(v: Any) -> int | float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return float(v) if "." in v else int(v)
        except Exception:
            return None
    return None


def _name_from_row(row: dict[str, Any], fallback: str) -> str:
    for key in ("name", "display_name", "title", "card_name", "item_name"):
        val = row.get(key)
        if val:
            return str(val)
    return fallback


def _catalog_name(catalog: dict[str, dict[str, Any]], item_id: Any) -> str:
    row = catalog.get(str(item_id)) or {}
    return _name_from_row(row, "") if row else ""


def _quantity(row: dict[str, Any]) -> int:
    for key in ("quantity", "qty", "count", "num", "number", "amount", "owned"):
        n = _number(row.get(key))
        if n is not None:
            return max(0, int(n))
    return 1


def _iter_rows(value: Any):
    if isinstance(value, list):
        for row in value:
            if isinstance(row, dict):
                yield row
    elif isinstance(value, dict):
        for key, row in value.items():
            if isinstance(row, dict):
                if not any(k in row for k in ("id", "item_id", "unit_id")):
                    row = {**row, "id": key}
                yield row
            else:
                n = _number(row)
                if n is not None:
                    yield {"id": key, "quantity": n}


def _item_category(item_id: str, name: str) -> str:
    if item_id in {"21000", "200005", "3900042"}:
        return "Currency"
    if item_id in {"200002","200003","200004","200008","200009","200010","200011","200012","200013","200014","200015","200016"} or "Stone" in name:
        return "Stones"
    if item_id in {"1023","1024"} or "Refill" in name:
        return "Refills"
    if item_id == "1031" or "Speedup" in name:
        return "Research Speedups"
    if item_id in {"5066","5067","5068"} or "Season Pass" in name:
        return "Season Passes"
    return "Other"


def build_inventory_snapshot(
    init_data: dict[str, Any],
    card_catalog: dict[str, dict[str, Any]] | None = None,
    item_catalog: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a Bender-style account snapshot: rarity totals + named resources.

    Individual cards are intentionally omitted. Unknown item IDs remain present in
    the snapshot for diagnostics, but the normal UI hides them.
    """
    card_catalog = card_catalog or {}
    item_catalog = item_catalog or {}
    embedded_names = discover_id_name_catalog(init_data)
    entries: dict[str, dict[str, Any]] = {}

    def add(category: str, key: str, name: str, count: int | float) -> None:
        ekey = f"{category}:{key}"
        existing = entries.get(ekey)
        if existing:
            existing["count"] = count  # authoritative sources should not double-count
        else:
            entries[ekey] = {"category": category, "key": str(key), "name": name, "count": count}

    ud = init_data.get("user_data") or {}
    cf = init_data.get("common_fields") or {}

    # Cards: aggregate only, matching the user's preferred compact presentation.
    rarity_names = {"1":"Common","2":"Rare","3":"Epic","4":"Legendary","5":"Mythic"}
    rarity_counts = ud.get("cards_by_rarity") or init_data.get("cards_by_rarity") or {}
    if isinstance(rarity_counts, dict) and rarity_counts:
        for rid, count in rarity_counts.items():
            n = _number(count)
            if n is not None and n > 0:
                add("Cards", str(rid), rarity_names.get(str(rid), f"Rarity {rid}"), n)
    else:
        units = init_data.get("user_units") or []
        total = sum(_quantity(row) for row in _iter_rows(units))
        if total:
            add("Cards", "total", "Total Cards", total)

    # Core currencies are scalar account fields, not user_items. Prefer one
    # authoritative field for each so the same balance is not shown twice.
    money = _number(ud.get("money"))
    if money is None: money = _number(cf.get("soft_currency_balance"))
    if money is not None: add("Currency", "nixon", "Nixon Coin", money)

    gems = _number(ud.get("free_hard_currency"))
    if gems is None: gems = _number(cf.get("free_hard_currency_balance"))
    if gems is None: gems = _number(cf.get("hard_currency_balance"))
    if gems is not None: add("Currency", "gem", "Gem", gems)

    watts = _number(ud.get("salvage"))
    if watts is None: watts = _number(cf.get("soft_currency_2_balance"))
    if watts is not None: add("Currency", "4001", "Giggitywatts", watts)

    # Consumables / materials / event currencies.
    seen_containers: set[int] = set()
    for field in ("user_items", "item_inventory", "user_inventory", "inventory_items", "items_owned"):
        value = init_data.get(field)
        if not value or id(value) in seen_containers:
            continue
        seen_containers.add(id(value))
        for row in _iter_rows(value):
            iid = row.get("item_id") or row.get("id") or row.get("unit_id")
            if iid is None:
                continue
            sid = str(iid)
            # AT mirrors Giggitywatts as item 4001 and user_data.salvage; keep one row.
            if sid == "4001" and watts is not None:
                continue
            nm = (_name_from_row(row, "") or _catalog_name(item_catalog, iid)
                  or FALLBACK_ITEM_NAMES.get(sid) or embedded_names.get(sid)
                  or f"Unknown item (ID {iid})")
            add(_item_category(sid, nm), sid, nm, _quantity(row))

    return {"captured_at": datetime.now(timezone.utc).isoformat(), "entries": entries}


def diff_snapshots(start: dict[str, Any] | None, current: dict[str, Any] | None) -> list[dict[str, Any]]:
    a = (start or {}).get("entries") or {}
    b = (current or {}).get("entries") or {}
    keys = set(a) | set(b)
    rows: list[dict[str, Any]] = []
    for key in keys:
        aa = a.get(key) or {}; bb = b.get(key) or {}
        av = _number(aa.get("count")) or 0; bv = _number(bb.get("count")) or 0
        rows.append({
            "category": bb.get("category") or aa.get("category") or "Other",
            "name": bb.get("name") or aa.get("name") or key,
            "start": av, "current": bv, "change": bv-av, "key": key,
        })
    rows.sort(key=lambda r:(str(r["category"]), -abs(float(r["change"])), str(r["name"]).lower()))
    return rows


@dataclass
class SessionTotals:
    battles: int = 0
    wins: int = 0
    losses: int = 0
    unknown: int = 0

    @property
    def win_rate(self) -> float:
        decided = self.wins + self.losses
        return (self.wins / decided * 100.0) if decided else 0.0


class SessionTracker:
    def __init__(self):
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.finished_at: str | None = None
        self.start_snapshot: dict[str, Any] | None = None
        self.current_snapshot: dict[str, Any] | None = None
        self.totals = SessionTotals()
        self.battles: list[dict[str, Any]] = []

    def start(self, snapshot: dict[str, Any]) -> None:
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.finished_at = None
        self.start_snapshot = snapshot
        self.current_snapshot = snapshot
        self.totals = SessionTotals()
        self.battles = []
        self.save()

    def record_battle(self, opponent: str, won: bool | None, moves: int) -> None:
        self.totals.battles += 1
        if won is True:
            self.totals.wins += 1
        elif won is False:
            self.totals.losses += 1
        else:
            self.totals.unknown += 1
        self.battles.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "opponent": opponent,
            "won": won,
            "moves": moves,
        })
        self.save()

    def update_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.current_snapshot = snapshot
        self.save()

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()
        self.save()
        try:
            with SESSION_HISTORY_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(self.to_dict(), ensure_ascii=False) + "\n")
        except Exception:
            pass

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "totals": asdict(self.totals),
            "start_snapshot": self.start_snapshot,
            "current_snapshot": self.current_snapshot,
            "battles": self.battles,
        }

    def save(self) -> None:
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            SESSION_FILE.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        except Exception:
            pass
