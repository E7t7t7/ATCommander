"""Native protocol for the first free Mom's Box pull only.

Public WebGL 1.181.0 state 0 dispatches raw29216 -> raw39207 -> raw38399,
using triggerMysteryBoxAd and AddAdHash raw38259 without rewarded-ad playback.
The native free branch is identified independently by free_pull_button tracking.
An additional ad pull still requires the real SDK playback/completion flow.

The request helper hashes the user ID, integer Unix second and public suffix
with UTF-8 lowercase MD5, then uses the common transport signatures. No input,
signature or signing material is logged or persisted by this module.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping

from .combat_signing import _unix_second, sign_combat_fields

FIRST_FREE_MOM_MESSAGE = "triggerMysteryBoxAd"
# Public client constant. Never include its contents in diagnostic output.
_PUBLIC_SUFFIX = 'KWYMsvnOpUVtJsuZGA0j'


def first_free_mom_available(state: Mapping[str, Any] | None) -> bool:
    """Require explicit unconsumed zero-cost state; missing values are ineligible.

The caller also checks current Mom's Box visibility and pending reward choices.
This deliberately excludes subsequent VIP/ad pulls even if their cost is zero.
"""
    if not isinstance(state, Mapping):
        return False
    for key in ("cost", "pulls"):
        value = state.get(key)
        if isinstance(value, bool) or value is None:
            return False
        if isinstance(value, int):
            if value != 0:
                return False
        elif isinstance(value, str):
            if value.strip() != "0":
                return False
        else:
            return False
    return True


def first_free_mom_fields(user_id: Any, *, now: float | int | None = None) -> dict[str, Any]:
    """Create fresh native fields; callers must enforce first-free eligibility.

This function performs no I/O and cannot report a completed ad. Timestamp and
all three signatures are generated together so the transport cannot mix times.
"""
    if user_id is None or isinstance(user_id, bool) or not str(user_id).strip():
        raise ValueError("User ID is required for the first free Mom's Box pull")
    identity = str(user_id)
    timestamp = _unix_second(time.time() if now is None else now)
    signature = hashlib.md5((identity + timestamp + _PUBLIC_SUFFIX).encode("utf-8")).hexdigest()
    return sign_combat_fields(
        {"adsig": signature}, FIRST_FREE_MOM_MESSAGE,
        user_id=identity, now=int(timestamp),
    )
