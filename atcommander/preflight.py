from __future__ import annotations
from typing import Any, Callable
from .account_state import find_ad_state
from .api import ATApiError, ATClient
from .protocol_config import load_profiles


def _truth(v: Any) -> bool | None:
    if isinstance(v, bool): return v
    if isinstance(v, (int, float)) and v in (0, 1): return bool(v)
    if isinstance(v, str):
        x=v.strip().lower()
        if x in {"1","true","yes","complete","completed","active"}: return True
        if x in {"0","false","no","incomplete","not_complete","inactive"}: return False
    return None


def ad_preflight(client: ATClient, init_data: dict[str, Any] | None = None, log: Callable[[str], None] | None = None, auto_watch: bool = False, *, should_stop: Callable[[],bool] | None = None) -> str:
    """Verify the legitimate AT ad boost before automation starts.

    getBoostLevel is the authoritative read. If auto_watch is enabled we only
    attempt AT's real browser UI and then re-check getBoostLevel. We never call
    recordAdBoost or a captured ad-completion request directly.
    """
    emit=log or (lambda _m:None)
    stopped=should_stop or (lambda:False)
    if stopped():return "stopped"

    def read_state():
        try:
            st=find_ad_state(client.get_boost_level()); emit("Ad preflight: checked getBoostLevel."); return st
        except Exception:
            data=init_data if isinstance(init_data,dict) else client.init(); return find_ad_state(data)

    state=read_state(); level=state.get("boost_level")
    if stopped():return "stopped"
    try: level_num=int(level) if level is not None else None
    except Exception: level_num=None
    if level_num is not None and level_num>0:
        emit(f"Ad preflight: active boost level {level_num} verified."); return "satisfied"

    if level_num==0 and auto_watch:
        emit("Ad preflight: boost inactive; attempting the legitimate AT browser ad UI.")
        try:
            from .ad_ui import attempt_legitimate_ad_ui
            options={"wait_seconds":45}
            if should_stop is not None:options.update(client=client,should_stop=stopped)
            result=attempt_legitimate_ad_ui(emit,**options)
            if result.get("ok"):
                emit(f"Ad preflight: active boost level {int(result.get('boost_level') or 1)} verified after UI attempt."); return "satisfied"
            emit("Ad preflight: legitimate UI attempt did not produce an active boost.")
        except Exception as exc:
            emit(f"Ad preflight: legitimate ad UI attempt unavailable: {exc}")

    if stopped():return "stopped"
    if level_num==0:
        raise ATApiError("Ad bonus is inactive. Use WATCH ADS or enable Auto-watch ads; AT Commander will only continue after getBoostLevel verifies the legitimate boost.")
    emit("Ad preflight: boost state could not be verified; continuing without fabricating ad completion.")
    return "unknown"
