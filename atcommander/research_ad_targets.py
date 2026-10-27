"""Pure exact-pair selection and verification for genuine research ads.

These helpers read fresh server snapshots only. They perform no playback or
requests. Unlock guards are separate from speedups and require a supplied exact
pair. Their caller must select those cards through the ordinary native picker
and obtain real SDK playback before asking these guards to verify its benefit.
"""
from __future__ import annotations

from .local_commands import CommandUnavailable
from .research_automation import ad_rows, number, pair_key, researched_pairs, research_tasks, rows
from .shop_rewards import api_rejected


_UNAVAILABLE = "A single exact running research ad speedup could not be verified."
_UNLOCK_UNAVAILABLE = "An exact owned research pair and empty ad chamber could not be verified."
_GEM_FIELDS = (("user_data", "free_hard_currency"),
               ("common_fields", "free_hard_currency_balance"),
               ("common_fields", "hard_currency_balance"))


def _unavailable():
    raise CommandUnavailable(_UNAVAILABLE) from None


def _pair(value):
    if not isinstance(value, str):
        return None
    parts = value.split("~")
    return pair_key(*parts) if len(parts) == 2 else None


def _state(snapshot):
    if not isinstance(snapshot, dict) or api_rejected(snapshot):
        _unavailable()
    now = number(snapshot.get("time"))
    if now is None or now <= 0:
        _unavailable()
    raw_tasks = snapshot.get("user_tasks")
    if not isinstance(raw_tasks, (dict, list)) or len(rows(raw_tasks)) != len(raw_tasks):
        _unavailable()
    raw_ads = snapshot.get("research_ad_speed_ups")
    if not isinstance(raw_ads, (dict, list)):
        _unavailable()
    parsed_rows = rows(raw_ads)
    if len(parsed_rows) != len(raw_ads):
        _unavailable()
    seen = set()
    for row in parsed_rows:
        slot = number(row.get("slot_index"))
        views = number(row.get("views"))
        maximum = number(row.get("max_views"))
        speedup = number(row.get("next_speed_up"))
        if (slot is None or slot < 0 or slot in seen or views is None or views < 0
                or maximum is None or maximum <= 0 or views > maximum
                or speedup is None or speedup < 0):
            _unavailable()
        seen.add(slot)
    try:
        tasks = research_tasks(snapshot)
        known = researched_pairs(snapshot)
    except CommandUnavailable:
        _unavailable()
    indices = [task["task_index"] for task in tasks.values()]
    if any(index <= 0 for index in indices) or len(set(indices)) != len(indices):
        _unavailable()
    return now, tasks, ad_rows(snapshot), known


def select_research_ad_target(snapshot, context=None):
    """Freeze one eligible exact task from a fresh snapshot, or fail safely.

    ``context`` is the optional frozen ``_commander_research`` mapping. When
    omitted, that field is read from the snapshot. A manual call without either
    context can select only a single eligible ad task. Returned fields contain
    public task metadata and no copied snapshot or account fields.
    """
    now, tasks, ads, known = _state(snapshot)
    if context is None and "_commander_research" in snapshot:
        context = snapshot["_commander_research"]
        if context is None:
            _unavailable()
    if context is not None:
        if not isinstance(context, dict):
            _unavailable()
        if context.get("phase") == "unlock":
            raise CommandUnavailable("Automatic research ad unlock requires a verified exact-card picker.") from None
        if context.get("phase") != "speedup":
            _unavailable()

    candidates = []
    for slot, ad in ads.items():
        task = tasks.get(slot)
        if task is None or task["end_time"] <= now or task["pair"] in known:
            continue
        views, maximum, speedup = (number(ad.get(key)) for key in ("views", "max_views", "next_speed_up"))
        if views >= maximum or speedup <= 0:
            continue
        # The same input pair in another chamber would make completion evidence
        # ambiguous, even though its own task and view row are exact.
        if sum(other["pair"] == task["pair"] for other in tasks.values()) != 1:
            continue
        target = {"phase": "speedup", "slot_index": slot, "pair": task["pair"],
                  "card1": number(task["task_id_1"]), "card2": number(task["task_id_2"]),
                  "task_index": task["task_index"], "before_end": task["end_time"],
                  "views": views, "max_views": maximum, "next_speed_up": speedup,
                  "server_time": now}
        if context is not None:
            if (number(context.get("slot_index")) != slot or _pair(context.get("pair")) != target["pair"]
                    or number(context.get("task_index")) != target["task_index"]
                    or number(context.get("before_end")) != target["before_end"]):
                continue
            if any(key in context and number(context[key]) != target[key]
                   for key in ("views", "max_views", "next_speed_up")):
                continue
            if any(key in context for key in ("card1", "card2")):
                if pair_key(context.get("card1"), context.get("card2")) != target["pair"]:
                    continue
        candidates.append(target)
    if len(candidates) != 1:
        _unavailable()
    return candidates[0]


def verify_research_ad_target(target, fresh_snapshot):
    """Require exact-slot view credit plus an exact task benefit; return a bool.

    Absolute end-time reduction proves a timer change. Remaining time falling
    as the server clock advances does not. A newly researched pair can confirm
    completion only while no replacement task conflicts with the frozen target.
    """
    if not isinstance(target, dict) or target.get("phase") != "speedup":
        return False
    values = {key: number(target.get(key)) for key in (
        "slot_index", "task_index", "before_end", "views", "max_views", "next_speed_up", "server_time")}
    pair = _pair(target.get("pair"))
    if (pair is None or pair_key(target.get("card1"), target.get("card2")) != pair
            or any(value is None for value in values.values())
            or values["slot_index"] < 0 or values["task_index"] <= 0
            or values["server_time"] <= 0 or values["before_end"] <= values["server_time"]
            or values["views"] < 0 or values["views"] >= values["max_views"]
            or values["next_speed_up"] <= 0):
        return False
    try:
        now, tasks, ads, known = _state(fresh_snapshot)
    except CommandUnavailable:
        return False
    ad = ads.get(values["slot_index"])
    if (now < values["server_time"] or ad is None
            or number(ad.get("max_views")) != values["max_views"]
            or number(ad.get("views")) <= values["views"]):
        return False
    task = tasks.get(values["slot_index"])
    exact = (task is not None and task["task_index"] == values["task_index"] and task["pair"] == pair)
    conflicting = any(
        (other["slot_index"] == values["slot_index"] or other["task_index"] == values["task_index"]
         or other["pair"] == pair)
        and not (other["slot_index"] == values["slot_index"]
                 and other["task_index"] == values["task_index"] and other["pair"] == pair)
        for other in tasks.values()
    )
    if conflicting:
        return False
    # research_tasks deliberately omits other task kinds. A changed task type
    # claiming this frozen identity must not disappear from conflict checking.
    for row in rows(fresh_snapshot["user_tasks"]):
        if (number(row.get("slot_index")) == values["slot_index"]
                or number(row.get("task_index", row.get("id"))) == values["task_index"]):
            if number(row.get("task_type")) != 1:
                return False
    return bool(exact and task["end_time"] < values["before_end"] or pair in known)


def _unlock_unavailable():
    raise CommandUnavailable(_UNLOCK_UNAVAILABLE) from None


def _unlock_gems(snapshot):
    result = {}
    for container, key in _GEM_FIELDS:
        source = snapshot.get(container)
        if not isinstance(source, dict) or key not in source:
            continue
        value = number(source[key])
        if value is None or value < 0:
            _unlock_unavailable()
        result[container + "." + key] = value
    if not result:
        _unlock_unavailable()
    return result


def _owned_counts(snapshot, card1, card2):
    raw = snapshot.get("user_units")
    if not isinstance(raw, (dict, list)) or any(not isinstance(row, dict) for row in
                                               (raw.values() if isinstance(raw, dict) else raw)):
        _unlock_unavailable()
    counts = {str(card1): 0, str(card2): 0}
    for row in rows(raw):
        card = number(row.get("unit_id", row.get("card_id")))
        if card is None or card <= 0:
            _unlock_unavailable()
        if "unit_id" in row and "card_id" in row and number(row["card_id"]) != card:
            _unlock_unavailable()
        if str(card) in counts:
            counts[str(card)] += 1
    if any(count <= 0 for count in counts.values()):
        _unlock_unavailable()
    return counts


def _unlock_slot(snapshot):
    raw = snapshot.get("research_times_slot")
    if not isinstance(raw, (dict, list)) or not raw or any(not isinstance(row, dict) for row in
                                                          (raw.values() if isinstance(raw, dict) else raw)):
        _unlock_unavailable()
    result, seen = None, set()
    for row in rows(raw):
        slot = number(row.get("slot_index"))
        times = row.get("research_times")
        if slot is None or slot < 0 or slot in seen or not isinstance(times, dict) or not times:
            _unlock_unavailable()
        seen.add(slot)
        clean_times = {}
        for raw_tier, raw_time in times.items():
            tier, duration = number(raw_tier), number(raw_time)
            if tier is None or tier <= 0 or duration is None or duration < 0 or str(tier) in clean_times:
                _unlock_unavailable()
            clean_times[str(tier)] = duration
        if not any(clean_times.values()):
            _unlock_unavailable()
        clean = {"slot_index": slot, "research_times": clean_times}
        for flag in ("locked", "unlocked", "ad_required"):
            if flag in row:
                if not isinstance(row[flag], bool):
                    _unlock_unavailable()
                clean[flag] = row[flag]
        if slot == 3:
            result = clean
    if result is None:
        _unlock_unavailable()
    return result


def _unlock_state(snapshot, card1, card2):
    try:
        now, tasks, ads, known = _state(snapshot)
    except CommandUnavailable:
        _unlock_unavailable()
    return now, tasks, ads, known, _unlock_slot(snapshot), _owned_counts(snapshot, card1, card2), _unlock_gems(snapshot)


def _public_tasks(tasks):
    return [{"slot_index": task["slot_index"], "task_index": task["task_index"],
             "pair": task["pair"], "end_time": task["end_time"]}
            for task in sorted(tasks.values(), key=lambda item: item["slot_index"])]


def select_research_unlock_target(snapshot, context=None):
    """Freeze an explicit owned pair for the observed native TV chamber.

    This does not authorize a completion request or choose cards for the caller.
    A frozen target may also be supplied as context to recheck all its relevant
    fields immediately before real Watch. A later server clock is expected.
    """
    if context is None and isinstance(snapshot, dict):
        context = snapshot.get("_commander_research")
    if not isinstance(context, dict) or context.get("phase") != "unlock":
        _unlock_unavailable()
    card1, card2 = number(context.get("card1")), number(context.get("card2"))
    pair = pair_key(card1, card2)
    if (pair is None or card1 == card2 or _pair(context.get("pair")) != pair
            or number(context.get("slot_index")) != 3
            or any(key in context for key in ("task_index", "before_end"))):
        _unlock_unavailable()
    now, tasks, ads, known, slot, owned, gems = _unlock_state(snapshot, card1, card2)
    if (slot.get("locked") is True or slot.get("unlocked") is False or slot.get("ad_required") is False
            or 3 in tasks or pair in known or any(task["pair"] == pair for task in tasks.values())
            or any(number(row.get("slot_index")) == 3 for row in rows(snapshot["user_tasks"]))):
        _unlock_unavailable()
    ad = ads.get(3)
    if ad is None or any(number(ad.get(key)) != value for key, value in
                         (("views", 0), ("max_views", 3), ("next_speed_up", 0))):
        _unlock_unavailable()
    target = {"phase": "unlock", "slot_index": 3, "pair": pair, "card1": card1, "card2": card2,
              "views": 0, "max_views": 3, "next_speed_up": 0, "server_time": now,
              "gem_balances": gems, "owned_card_counts": owned, "slot_metadata": slot,
              "existing_tasks": _public_tasks(tasks), "view_credit": "increment"}
    for key in ("views", "max_views", "next_speed_up"):
        if key in context and number(context[key]) != target[key]:
            _unlock_unavailable()
    for key in ("gem_balances", "owned_card_counts", "slot_metadata", "existing_tasks", "view_credit"):
        if key in context and context[key] != target[key]:
            _unlock_unavailable()
    if "server_time" in context:
        before = number(context["server_time"])
        if before is None or before <= 0 or now < before:
            _unlock_unavailable()
    return target


def research_unlock_target_unchanged(target, fresh_snapshot):
    """Recheck the frozen exact pair, empty slot, ownership and gem mirrors."""
    if not isinstance(target, dict):
        return False
    required = {"phase", "slot_index", "pair", "card1", "card2", "views", "max_views", "next_speed_up",
                "server_time", "gem_balances", "owned_card_counts", "slot_metadata", "existing_tasks", "view_credit"}
    if not required <= target.keys():
        return False
    gems, owned = target["gem_balances"], target["owned_card_counts"]
    gem_keys = {container + "." + key for container, key in _GEM_FIELDS}
    if (not isinstance(gems, dict) or not gems or not set(gems) <= gem_keys
            or any(number(value) is None or number(value) < 0 for value in gems.values())
            or not isinstance(owned, dict) or any(number(value) is None or number(value) <= 0 for value in owned.values())):
        return False
    try:
        select_research_unlock_target(fresh_snapshot, target)
    except CommandUnavailable:
        return False
    return True


def verify_research_unlock_target(target, fresh_snapshot):
    """Require a new exact future task, exact-slot ad credit and zero gem use.

    This guard currently requires one added view. A reset-view variant is not
    accepted without observed native evidence of that specific unlock behavior.
    """
    if not isinstance(target, dict) or target.get("phase") != "unlock":
        return False
    card1, card2 = number(target.get("card1")), number(target.get("card2"))
    pair, started = pair_key(card1, card2), number(target.get("server_time"))
    old_tasks, old_gems, old_owned, old_slot = (target.get(key) for key in
                                              ("existing_tasks", "gem_balances", "owned_card_counts", "slot_metadata"))
    if (pair is None or card1 == card2 or _pair(target.get("pair")) != pair
            or number(target.get("slot_index")) != 3 or started is None or started <= 0
            or any(number(target.get(key)) != value for key, value in
                   (("views", 0), ("max_views", 3), ("next_speed_up", 0)))
            or target.get("view_credit") != "increment" or not isinstance(old_tasks, list)
            or not isinstance(old_gems, dict) or not old_gems or not isinstance(old_owned, dict)
            or not isinstance(old_slot, dict) or number(old_slot.get("slot_index")) != 3):
        return False
    gem_keys = {container + "." + key for container, key in _GEM_FIELDS}
    if (not set(old_gems) <= gem_keys or any(number(value) is None or number(value) < 0 for value in old_gems.values())
            or set(old_owned) != {str(card1), str(card2)}
            or any(number(value) is None or number(value) <= 0 for value in old_owned.values())):
        return False
    try:
        clean_slot = _unlock_slot({"research_times_slot": [old_slot]})
    except CommandUnavailable:
        return False
    if (clean_slot.get("locked") is True or clean_slot.get("unlocked") is False
            or clean_slot.get("ad_required") is False):
        return False
    previous_indices = set()
    previous_slots = set()
    for old in old_tasks:
        if not isinstance(old, dict):
            return False
        index, slot, end = number(old.get("task_index")), number(old.get("slot_index")), number(old.get("end_time"))
        if (index is None or index <= 0 or index in previous_indices or slot is None or slot < 0
                or slot == 3 or slot in previous_slots or end is None or end < 0
                or _pair(old.get("pair")) is None or _pair(old.get("pair")) == pair):
            return False
        previous_indices.add(index)
        previous_slots.add(slot)
    try:
        now, tasks, ads, known, slot, owned, gems = _unlock_state(fresh_snapshot, card1, card2)
    except CommandUnavailable:
        return False
    ad, task = ads.get(3), tasks.get(3)
    if (now < started or gems != old_gems or owned != old_owned or pair in known
            or slot["research_times"] != old_slot.get("research_times")
            or ad is None or number(ad.get("views")) != 1 or number(ad.get("max_views")) != 3
            or task is None or task["pair"] != pair or task["task_index"] in previous_indices
            or task["end_time"] <= now or sum(other["pair"] == pair for other in tasks.values()) != 1):
        return False
    return all(number(row.get("task_type")) == 1 for row in rows(fresh_snapshot["user_tasks"])
               if number(row.get("slot_index")) == 3)
