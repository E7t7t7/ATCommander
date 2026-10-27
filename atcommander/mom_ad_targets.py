"""Strict read-only guards for later genuinely rewarded Mom's Box pulls.

The first free native pull has its own protocol. These guards do not send a
pull, fabricate completion or substitute a paid purchase for SDK playback.
"""
from __future__ import annotations

from .local_commands import CommandUnavailable
from .shop_rewards import api_rejected


_MESSAGE = "A current later Mom's Box ad offer could not be verified."
_GEM_FIELDS = (
    ("user_data", "free_hard_currency"),
    ("common_fields", "free_hard_currency_balance"),
    ("common_fields", "hard_currency_balance"),
)


def _integer(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        value = value.strip()
        if value.isascii() and value.isdigit():
            return int(value)
    return None


def _unavailable():
    raise CommandUnavailable(_MESSAGE) from None


def _gems(snapshot):
    result = {}
    for container, key in _GEM_FIELDS:
        source = snapshot.get(container)
        if not isinstance(source, dict) or key not in source:
            continue
        value = _integer(source[key])
        if value is None or value < 0:
            _unavailable()
        result[container + "." + key] = value
    if not result:
        _unavailable()
    return result


def _state(snapshot):
    if not isinstance(snapshot, dict) or api_rejected(snapshot):
        _unavailable()
    current = snapshot.get("slot_machine_data")
    if not isinstance(current, dict):
        _unavailable()
    pulls, cost, now = (_integer(current.get("pulls")), _integer(current.get("cost")),
                        _integer(snapshot.get("time")))
    if pulls is None or pulls < 0 or cost is None or cost < 0 or now is None or now <= 0:
        _unavailable()
    reset = _integer(current.get("reset_time")) if "reset_time" in current else None
    if "reset_time" in current and (reset is None or reset <= now):
        _unavailable()
    ad_available = _integer(current.get("ad_pull_available")) if "ad_pull_available" in current else None
    if "ad_pull_available" in current and ad_available not in {0, 1, 2}:
        _unavailable()
    return pulls, cost, now, _gems(snapshot), reset, ad_available


def freeze_mom_ad_target(snapshot, store_data):
    """Freeze later-pull metadata for navigation, without authorizing Watch.

    Store availability can be omitted by the native response. Such a provisional
    target requires an authoritative enabled native Watch offer before playback.
    """
    pulls, cost, now, gems, reset, ad_available = _state(snapshot)
    if pulls < 1 or snapshot.get("active_choices") or ad_available is not None and ad_available not in {1, 2}:
        _unavailable()
    if not isinstance(store_data, dict) or api_rejected(store_data):
        _unavailable()
    items = store_data.get("store_items")
    if isinstance(items, dict):
        entries = [{"id": key, **row} for key, row in items.items() if isinstance(row, dict)]
    elif isinstance(items, list):
        entries = [row for row in items if isinstance(row, dict)]
    else:
        _unavailable()
    if len(entries) != len(items):
        _unavailable()
    matching = [row for row in entries if _integer(row.get("id")) == 71]
    if len(matching) != 1:
        _unavailable()
    item = matching[0]
    available = _integer(item.get("num_available"))
    if ("mystery" not in str(item.get("name") or "").casefold()
            or "num_available" in item and (available is None or available <= 0)):
        _unavailable()
    return {"kind": "mom_ad", "store_id": 71, "before_pulls": pulls,
            "before_cost": cost, "gem_balances": gems, "server_time": now,
            "reset_time": reset, "ad_pull_available": ad_available,
            "requires_native_watch": available is None and ad_available is None}


def select_mom_ad_target(snapshot, store_data, *, native_watch_available=False):
    """Require explicit shop availability or a verified enabled native Watch."""
    target = freeze_mom_ad_target(snapshot, store_data)
    if target["requires_native_watch"] and native_watch_available is not True:
        _unavailable()
    return target


def mom_ad_target_unchanged(target, snapshot, store_data, *, native_watch_available=False):
    """Recheck the frozen offer immediately before selecting the real Watch control."""
    if not isinstance(target, dict) or target.get("kind") != "mom_ad":
        return False
    started = _integer(target.get("server_time"))
    if started is None or started <= 0:
        return False
    try:
        current = select_mom_ad_target(snapshot, store_data, native_watch_available=native_watch_available)
    except CommandUnavailable:
        return False
    return (current["server_time"] >= started
            and all(current[key] == target.get(key) for key in
                    ("store_id", "before_pulls", "before_cost", "gem_balances"))
            and current["reset_time"] == target.get("reset_time")
            and current["ad_pull_available"] == target.get("ad_pull_available"))


def verify_mom_ad_target(target, fresh_snapshot):
    """Require exactly one fresh pull and unchanged gems after actual ad Close."""
    if not isinstance(target, dict) or target.get("kind") != "mom_ad" or target.get("store_id") != 71:
        return False
    before, started = _integer(target.get("before_pulls")), _integer(target.get("server_time"))
    balances = target.get("gem_balances")
    if before is None or before < 1 or started is None or started <= 0 or not isinstance(balances, dict) or not balances:
        return False
    valid_keys = {container + "." + key for container, key in _GEM_FIELDS}
    if not set(balances) <= valid_keys or any(_integer(value) is None or _integer(value) < 0 for value in balances.values()):
        return False
    try:
        pulls, _cost, now, gems, reset, _ad_available = _state(fresh_snapshot)
    except CommandUnavailable:
        return False
    return now >= started and pulls == before + 1 and gems == balances and reset == target.get("reset_time")
