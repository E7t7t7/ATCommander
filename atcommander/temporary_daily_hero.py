"""Verified temporary commander selection around an existing daily play call.

The caller supplies the established API dispatcher and a fresh exact deck
reader. The helper never selects an active deck or starts a battle. Each hero
assignment is sent at most once and uncertain outcomes are reconciled by reads.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from .local_commands import CommandUnavailable
from .shop_rewards import api_rejected


def _id(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return str(int(value.strip())) if int(value.strip()) > 0 else None
    return None


def _units(row: Mapping[str, Any]) -> tuple[str, ...] | None:
    source = row.get("units")
    if isinstance(source, Mapping):
        entries = list(source.items())
    elif isinstance(source, list):
        entries = [(None, unit) for unit in source]
    else:
        return None
    values = []
    for key, unit in entries:
        if not isinstance(unit, Mapping):
            return None
        index = _id(unit.get("unit_index", unit.get("id", key)))
        if index is None:
            return None
        values.append(index)
    return tuple(sorted(values, key=int))


@dataclass(frozen=True)
class ExactCommanderState:
    deck_id: str
    commander_id: str
    unit_indices: tuple[str, ...] | None = None


def exact_active_commander(payload: Mapping[str, Any] | None) -> ExactCommanderState:
    """Read one exact active slot and commander, rejecting ambiguous identities."""
    if not isinstance(payload, Mapping) or api_rejected(payload):
        raise CommandUnavailable("The current active deck and commander could not be verified.")
    user = payload.get("user_data")
    active = _id(user.get("active_deck")) if isinstance(user, Mapping) else None
    source = payload.get("user_decks")
    if active is None or not isinstance(source, (Mapping, list)):
        raise CommandUnavailable("The current active deck and commander could not be verified.")
    entries = list(source.items()) if isinstance(source, Mapping) else [(None, row) for row in source]
    matches = []
    for key, row in entries:
        if not isinstance(row, Mapping):
            continue
        explicit = row.get("deck_id", row.get("id"))
        row_id = _id(explicit if explicit is not None else key)
        if _id(key) == active and explicit is not None and row_id != active:
            raise CommandUnavailable("The active saved-deck identity is inconsistent.")
        if row_id == active:
            matches.append(row)
    if len(matches) != 1:
        raise CommandUnavailable("The current active deck and commander could not be verified.")
    row = matches[0]
    commander = row.get("commander")
    values = [row["commander_id"]] if row.get("commander_id") is not None else []
    if isinstance(commander, Mapping):
        values.extend(commander[key] for key in ("unit_id", "commander_id", "hero_id") if commander.get(key) is not None)
    identities = [_id(value) for value in values]
    if not identities or None in identities or len(set(identities)) != 1:
        raise CommandUnavailable("The active deck's exact commander could not be verified.")
    return ExactCommanderState(active, identities[0], _units(row))


def _same_deck(state: ExactCommanderState, original: ExactCommanderState) -> bool:
    return state.deck_id == original.deck_id and (
        original.unit_indices is None or state.unit_indices == original.unit_indices
    )


def _read(reader: Callable[[], Mapping[str, Any]]) -> ExactCommanderState:
    try:
        return exact_active_commander(reader())
    except Exception:
        raise CommandUnavailable("The fresh active deck and commander could not be verified; no hero request was retried.") from None


def _confirmed_response(response: Any, original: ExactCommanderState, target: str) -> bool:
    try:
        state = exact_active_commander(response)
    except CommandUnavailable:
        return False
    return _same_deck(state, original) and state.commander_id == target


@contextmanager
def temporary_daily_commander(
    call: Callable[[str, dict[str, Any]], Mapping[str, Any]],
    fresh_exact_deck_reader: Callable[[], Mapping[str, Any]],
    hero_id: Any,
    *,
    expected_deck_id: Any = None,
    expected_commander_id: Any = None,
) -> Iterator[dict[str, Any]]:
    """Assign, verify, run the caller's body, then conditionally restore.

    The caller has already verified hero unlock/show eligibility and owns serial
    execution. Its reader must return fresh active-deck metadata and exact saved
    decks. It must not return a cached snapshot or reselect a stale active deck.
    The yielded report is updated during restoration, including when the body fails.
    An external deck/commander change is preserved and reported as an error.
    """
    target = _id(hero_id)
    expected = _id(expected_deck_id) if expected_deck_id is not None else None
    expected_commander = _id(expected_commander_id) if expected_commander_id is not None else None
    if (target is None or expected_deck_id is not None and expected is None
            or expected_commander_id is not None and expected_commander is None):
        raise CommandUnavailable("A verified temporary hero and active deck are required.")
    original = _read(fresh_exact_deck_reader)
    if expected is not None and original.deck_id != expected:
        raise CommandUnavailable("The active deck changed after daily planning; no hero was assigned.")
    if expected_commander is not None and original.commander_id != expected_commander:
        raise CommandUnavailable("The commander changed after daily planning; no hero was assigned.")
    report: dict[str, Any] = {
        "deck_id": original.deck_id,
        "original_commander_id": original.commander_id,
        "temporary_commander_id": target,
        "switch_attempted": False, "switch_verified": False,
        "restore_attempted": False, "restored": False,
        "restoration_status": "not_started",
    }
    if original.commander_id == target:
        report.update(switch_verified=True, restored=True, restoration_status="unchanged")
        yield report
        return

    report["switch_attempted"] = True
    response = None
    try:
        response = call("setDeckCommander", {"deck_id": original.deck_id, "commander_id": target})
    except Exception:
        # A rejected/timeout response may still have changed the account. Read
        # its state before deciding whether another (restore) action is safe.
        pass
    if not _confirmed_response(response, original, target):
        current = _read(fresh_exact_deck_reader)
        if not _same_deck(current, original) or current.commander_id not in {target, original.commander_id}:
            report["restoration_status"] = "external_change"
            raise CommandUnavailable("The active deck or commander changed unexpectedly; no battle started and no hero was overwritten.")
        if current.commander_id == original.commander_id:
            report.update(restored=True, restoration_status="already_original")
            raise CommandUnavailable("AT did not confirm the temporary daily hero; no battle started and no assignment was retried.")
    report["switch_verified"] = True

    try:
        yield report
    finally:
        try:
            current = _read(fresh_exact_deck_reader)
        except CommandUnavailable:
            report["restoration_status"] = "ambiguous"
            raise CommandUnavailable("The daily hero restoration could not be reconciled. No commander was blindly overwritten or request retried.") from None
        if not _same_deck(current, original):
            report["restoration_status"] = "external_change"
            raise CommandUnavailable("The active deck changed during daily play. Its current commander was preserved; the original hero was not restored.")
        if current.commander_id == original.commander_id:
            report.update(restored=True, restoration_status="already_original")
        elif current.commander_id != target:
            report["restoration_status"] = "external_change"
            raise CommandUnavailable("The commander changed during daily play. That external selection was preserved; the original hero was not restored.")
        else:
            report["restore_attempted"] = True
            response = None
            try:
                response = call("setDeckCommander", {"deck_id": original.deck_id, "commander_id": original.commander_id})
            except Exception:
                pass
            if _confirmed_response(response, original, original.commander_id):
                report.update(restored=True, restoration_status="restored")
            else:
                try:
                    restored = _read(fresh_exact_deck_reader)
                except CommandUnavailable:
                    report["restoration_status"] = "ambiguous"
                    raise CommandUnavailable("A daily hero restoration was sent, but its outcome could not be verified. It was not retried.") from None
                if _same_deck(restored, original) and restored.commander_id == original.commander_id:
                    report.update(restored=True, restoration_status="restored_after_reconciliation")
                else:
                    report["restoration_status"] = "unconfirmed"
                    raise CommandUnavailable("A daily hero restoration was sent, but the original selection was not confirmed. It was not retried.")
