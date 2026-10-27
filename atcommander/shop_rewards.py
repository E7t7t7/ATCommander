"""Pure shop/reward helpers; no credentials, HTTP, GUI, or account writes."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import time
from typing import Any, Callable


def api_rejected(response: Any) -> bool:
    """Recognize explicit rejection without mistaking a missing result for one."""
    if not isinstance(response, dict):
        return True
    result = response.get("result")
    return bool(response.get("error")) or result is False or result == 0 or isinstance(result, str) and result.lower() in {"false", "0"}


def _number(value: Any, default=0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _kind(row: dict[str, Any]) -> str:
    keys = ("card_type", "type_name", "unit_type", "category", "kind")
    hints = " ".join(str(row.get(key) or "") for key in keys).lower()
    types = {re.sub(r"[\s_-]+", "", str(row.get(key) or "").lower()) for key in keys}
    # Reuse the existing CM engine's explicit PC descriptors and AT PC sets;
    # a generic combo descriptor alone is not evidence of a playable Power Card.
    if row.get("is_pc") in {True, 1, "1", "true"} or types.intersection({"pc", "finalform", "powercard"}) or str(row.get("set") or row.get("set_id") or "") in {"3001", "3002"}:
        return "PowerCard"
    if "character" in hints or str(row.get("card_type") or "").lower() == "char":
        return "Character"
    if "item" in hints or "object" in hints:
        return "Item"
    return "Unknown"


def extract_item_choices(response: dict[str, Any], catalog: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Parse the real active_choices/options/give_card shape from the capture.

    The selectable id belongs to the option (e.g. 1/2/3), while give_card.id is
    only its reward's catalog id. They must never be substituted for each other.
    """
    if api_rejected(response):
        return []
    catalog = catalog or {}
    choices = response.get("active_choices")
    choices = list(choices.values()) if isinstance(choices, dict) else choices if isinstance(choices, list) else []
    found = []
    for group_index, group in enumerate(choices):
        if not isinstance(group, dict):
            continue
        choice_type = group.get("type")
        options = group.get("options")
        options = list(options.values()) if isinstance(options, dict) else options if isinstance(options, list) else []
        for ordinal, option in enumerate(options):
            if not isinstance(option, dict) or option.get("id") is None:
                continue
            rewards = option.get("give_card")
            rewards = list(rewards.values()) if isinstance(rewards, dict) else rewards if isinstance(rewards, list) else []
            cards = []
            for reward in rewards:
                if not isinstance(reward, dict):
                    continue
                card_id = reward.get("id") or reward.get("unit_id") or reward.get("card_id")
                if card_id is None:
                    continue
                meta = catalog.get(str(card_id)) or {}
                name = str(reward.get("name") or meta.get("name") or f"Card {card_id}")
                kind = _kind({**meta, **reward})
                cards.append({"card_id": str(card_id), "name": name, "kind": kind,
                              "rarity": int(_number(reward.get("rarity") or meta.get("rarity"))),
                              "value": _number(reward.get("value") or meta.get("value"), _number(reward.get("power") or meta.get("power"), _number(meta.get("attack")) + _number(meta.get("health")))),
                              "health": _number(reward.get("health") or meta.get("health"))})
            if cards:
                # Rank a multi-card option by its best explicitly classified card;
                # keep the complete reward list as reviewable context.
                best = min(cards, key=item_choice_priority)
                name = " + ".join(card["name"] for card in cards)
                kind, rarity, value, health = best["kind"], best["rarity"], best["value"], best["health"]
            else:
                name = str(option.get("name") or option.get("title") or f"Option {option['id']}")
                kind, rarity, value, health = _kind(option), int(_number(option.get("rarity"))), _number(option.get("value")), _number(option.get("health"))
            found.append({"id": str(option["id"]), "choice_type": str(choice_type) if choice_type is not None else None,
                          "group": group_index, "name": name, "kind": kind, "rarity": rarity,
                          "value": value, "health": health, "order": ordinal + 1, "cards": cards})
    return found


def item_choice_priority(row: dict[str, Any]):
    kind = str(row.get("kind") or "").lower()
    rogu = re.search(r"\brogu\b", str(row.get("name") or ""), re.I) and kind == "character"
    tier = 0 if rogu else 1 if kind == "character" else 2 if kind in {"item", "powercard"} else 3
    # Preserve AT ordering when type metadata is unknown rather than guessing.
    return (tier, -int(_number(row.get("rarity"))) if tier < 3 else 0,
            -_number(row.get("value")) if tier < 3 else 0,
            -_number(row.get("health")) if tier < 3 else 0, int(_number(row.get("order"), 9999)))


def choose_item_reward(options: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose within one active choice; refuse ambiguous/unclassified rewards."""
    if not options:
        raise ValueError("AT did not expose a reward choice.")
    groups = {(row.get("group", 0), row.get("choice_type")) for row in options}
    if len(groups) != 1:
        raise ValueError("Several reward choices are pending; resolve one choice at a time.")
    ranked = sorted(options, key=item_choice_priority)
    selected = ranked[0]
    if selected.get("choice_type") is None:
        raise ValueError("AT did not expose the choice type needed to select safely.")
    if selected.get("kind") == "Unknown":
        raise ValueError("Card metadata is unavailable; review this choice manually.")
    # An unresolved competitor might be Rogu or another Character. Automatic
    # selection is safe only after all alternatives can be classified.
    if any(row.get("kind") == "Unknown" for row in options):
        raise ValueError("Some reward types are unresolved; review this choice manually.")
    return selected


def item_choice_confirmed(response: dict[str, Any], choice_type: str | int) -> bool:
    """The captured successful confirmUserChoice removes its active choice."""
    if api_rejected(response) or "active_choices" not in response:
        return False
    choices = response.get("active_choices")
    if not isinstance(choices, (dict, list)):
        return False
    choices = choices.values() if isinstance(choices, dict) else choices
    return not any(isinstance(row, dict) and str(row.get("type")) == str(choice_type) for row in choices)


def daily_upgrade_progress(data: dict[str, Any]) -> tuple[int, int, int] | None:
    """Return progress/target/remaining only from an explicit upgrade quest.

    Missing metadata means unknown, not three upgrades to perform. A matching
    req.upgrade_cards_num is stronger evidence than arbitrary achievement names.
    """
    if api_rejected(data):
        return None
    quests = data.get("user_achievements") or {}
    rows = quests.values() if isinstance(quests, dict) else quests if isinstance(quests, list) else []
    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        req = row.get("req") if isinstance(row.get("req"), dict) else {}
        target_value = req.get("upgrade_cards_num")
        explicit = target_value is not None
        if not explicit and "upgrade" not in str(row.get("name") or "").lower():
            continue
        if target_value is None:
            target_value = row.get("max_progress")
        if target_value is None or row.get("progress") is None:
            continue
        target = int(_number(target_value, -1)); progress = int(_number(row.get("progress"), -1))
        # The requested daily quest is three upgrades, never a lifetime upgrade
        # achievement (which may also have "upgrade" in its name).
        if target != 3 or progress < 0:
            continue
        candidates.append((progress, target, max(0, min(3, target - progress))))
    return candidates[0] if len(set(candidates)) == 1 else None


def safe_upgrade_candidates(data: dict[str, Any], deck_data: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Find loose level-one Common/Rare/Epic cards and protect every saved deck."""
    if api_rejected(data) or api_rejected(deck_data) or not catalog:
        raise ValueError("Live account, saved decks, and card metadata are required for safe upgrades.")
    decks = deck_data.get("user_decks")
    if not isinstance(decks, (dict, list)):
        raise ValueError("AT did not expose saved decks; safe card protection cannot be verified.")
    decks = decks.values() if isinstance(decks, dict) else decks
    protected = set()
    applied = data.get("applied_foils") or {}
    applied = applied.values() if isinstance(applied, dict) else applied if isinstance(applied, list) else []
    for foil in applied:
        if isinstance(foil, dict) and foil.get("unit_index") is not None:
            protected.add(str(foil["unit_index"]))
    for deck in decks:
        if not isinstance(deck, dict):
            continue
        units = deck.get("units") or []
        units = units.values() if isinstance(units, dict) else units if isinstance(units, list) else []
        for unit in units:
            if isinstance(unit, dict) and unit.get("unit_index") is not None:
                protected.add(str(unit["unit_index"]))
            elif isinstance(unit, (str, int)):
                protected.add(str(unit))
    units = data.get("user_units") or {}
    units = units.items() if isinstance(units, dict) else enumerate(units) if isinstance(units, list) else []
    found = []
    for index, unit in units:
        if not isinstance(unit, dict):
            continue
        idx = unit.get("unit_index", index); uid = unit.get("unit_id")
        if idx is None or uid is None or str(idx) in protected or int(_number(unit.get("level"))) != 1:
            continue
        # Foil state must be explicit. A malformed value cannot mean non-foil.
        if _number(unit.get("foil_id", 0), 1) != 0 or unit.get("locked") in {True, 1, "1", "true"}:
            continue
        meta = catalog.get(str(uid)) or {}
        rarity = int(_number(unit.get("rarity") or meta.get("rarity"), 9))
        if rarity not in {1, 2, 3}:
            continue
        found.append({"unit_index": str(idx), "unit_id": str(uid), "rarity": rarity,
                      "level": 1, "name": str(meta.get("name") or f"Card {uid}")})
    return sorted(found, key=lambda row: (row["rarity"], row["name"], row["unit_index"]))


def upgrade_confirmed(response: dict[str, Any], unit_index: str | int, old_level: int = 1) -> bool:
    if api_rejected(response):
        return False
    units = response.get("user_units")
    rows = units.values() if isinstance(units, dict) else units if isinstance(units, list) else []
    if isinstance(units, dict):
        unit = units.get(str(unit_index)) or units.get(unit_index)
        if isinstance(unit, dict) and _number(unit.get("level")) > old_level:
            return True
    return any(isinstance(row, dict) and str(row.get("unit_index")) == str(unit_index) and _number(row.get("level")) > old_level for row in rows)


@dataclass
class PurchaseProgress:
    done: int
    total: int
    elapsed: float
    warnings: int
    interval: float
    status: str

    @property
    def rate(self):
        return self.done / max(0.001, self.elapsed)


class PurchaseInterrupted(RuntimeError):
    def __init__(self, message: str, progress: PurchaseProgress):
        super().__init__(message)
        self.progress = progress


def _busy(response: dict[str, Any]) -> bool:
    message = response.get("result_message") or ""
    message = " ".join(map(str, message)) if isinstance(message, list) else str(message)
    return "warning_16" in message.lower() or "try that again in a moment" in message.lower() or "too fast" in message.lower()


def run_single_purchases(total: int, call_one: Callable[[], dict[str, Any]], *,
                         on_progress: Callable[[PurchaseProgress], None] | None = None,
                         initial_interval: float = .35, min_interval: float = .18,
                         max_interval: float = 4.25, max_consecutive_warnings: int = 8,
                         clock: Callable[[], float] = time.monotonic,
                         sleep: Callable[[float], None] = time.sleep,
                         should_stop: Callable[[], bool] | None = None) -> PurchaseProgress:
    """Persistent single requests, adaptive *start-to-start* pacing and counts.

    Transport failures are never replayed. Only an explicit warning_16 rejection
    retries the same pack, and the retry limit resets after a confirmed purchase.
    Callers may retain final interval for the next run; it contains no account data.
    """
    if int(total) < 1:
        raise ValueError("Purchase count must be positive.")
    total = int(total)
    interval = min(max_interval, max(min_interval, float(initial_interval)))
    start = clock(); next_at = start; done = warnings = consecutive = streak = 0

    def state(status):
        current = PurchaseProgress(done, total, max(0.0, clock() - start), warnings, interval, status)
        if on_progress:
            on_progress(current)
        return current

    state("starting")
    while done < total:
        if should_stop and should_stop():
            return state("stopped")
        delay = next_at - clock()
        if delay > 0:
            sleep(delay)
        if should_stop and should_stop():
            return state("stopped")
        sent_at = clock()
        try:
            response = call_one()
        except Exception as exc:
            # Never include arbitrary transport exception text: requests errors
            # can contain a URL with private query parameters.
            raise PurchaseInterrupted("Network failure; this purchase was not retried because its server outcome is unknown.", state("stopped")) from exc
        if not isinstance(response, dict) or not response:
            raise PurchaseInterrupted("AT did not confirm this purchase; automation stopped without replaying it.", state("stopped"))
        if api_rejected(response):
            if not _busy(response):
                raise PurchaseInterrupted("AT rejected the purchase; automation stopped.", state("stopped"))
            warnings += 1; consecutive += 1; streak = 0
            interval = min(max_interval, max(.55, interval * 1.35, interval + .18))
            if consecutive > max_consecutive_warnings:
                raise PurchaseInterrupted("AT is still rate limiting purchases after paced retries.", state("stopped"))
            state("rate limited")
            # Explicit rejection is known to have bought nothing. Cooldown is
            # measured from receipt here to avoid a tight loop on fast failures.
            next_at = clock() + interval
            continue
        result = response.get("result")
        rewards = response.get("rewards")
        explicit_success = result is True or result == 1 or isinstance(result, str) and result.lower() in {"true", "1"}
        if not explicit_success and not (isinstance(rewards, (dict, list)) and bool(rewards)):
            raise PurchaseInterrupted("AT returned no purchase confirmation; automation stopped without replaying the uncertain request.", state("stopped"))
        done += 1; consecutive = 0; streak += 1
        state("complete" if done == total else "purchased")
        if streak >= 8:
            interval = max(min_interval, interval - .04); streak = 0
        next_at = sent_at + interval
    return state("complete")
