"""Claim only explicitly available native monthly/weekly login rewards."""
from .local_commands import CommandResult,CommandSpec,CommandUnavailable

CALENDARS=("monthly_daily_bonus_data","weekly_daily_bonus_data")

def _integer(value):
    if isinstance(value,bool):return None
    if isinstance(value,int) and value>=0:return value
    if isinstance(value,str) and value.isascii() and value.isdigit():return int(value)
    return None

def login_bonus_status(live):
    pending=[];states={}
    for key in CALENDARS:
        row=live.get(key) if isinstance(live,dict) else None
        if not isinstance(row,dict):return {"known":False,"available":False,"reason":"Current login-calendar eligibility is unavailable."}
        flag=_integer(row.get("can_claim"));offered=_integer(row.get("num_bonuses"));claimed=_integer(row.get("num_bonuses_claimed"));cycle=_integer(row.get("cycle_id"))
        if flag not in {0,1} or None in (offered,claimed,cycle) or claimed>offered or bool(flag)!=(claimed<offered):
            return {"known":False,"available":False,"reason":"Current login-calendar counters or claim flags are incomplete or inconsistent."}
        states[key]={"cycle":cycle,"claimed":claimed,"offered":offered}
        if flag:pending.append(key)
    return {"known":True,"available":bool(pending),"pending":pending,"states":states,
            "reason":"Free login rewards are available." if pending else "Current monthly and weekly login rewards are already claimed."}

def claim_daily_login(service):
    before=login_bonus_status(service.snapshot(refresh=True))
    if not before["known"]:raise CommandUnavailable(before["reason"]+" No login claim was sent.")
    if not before["available"]:return CommandResult(message=before["reason"],data={"claimed":False,"already_claimed":True})
    if getattr(service,"stop",None) is not None and service.stop.is_set():
        return CommandResult(message="Stopped before the login claim.",data={"claimed":False})
    # Native live capture proved an empty, normally signed claimDailyBonus.
    # One request claims the available monthly and weekly rewards together.
    service.call("claimDailyBonus")
    service.context.invalidate()
    after=login_bonus_status(service.snapshot(refresh=True))
    if not after["known"] or after["available"]:
        raise CommandUnavailable("The login reward outcome was not verified. No claim was replayed.")
    for key in before["pending"]:
        old=before["states"][key];new=after["states"][key]
        if new["cycle"]!=old["cycle"] or new["claimed"]<=old["claimed"]:
            raise CommandUnavailable("The login reward counters did not confirm this claim. No request was replayed.")
    return CommandResult(message="Claimed the available free monthly/weekly login rewards and verified their counters.",data={"claimed":True,"calendars":len(before["pending"])})

def prepare_login_for_ads(client,should_stop=None):
    """A claim before cold game startup avoids the native login modal."""
    from .local_commands import LocalCommandService
    service=LocalCommandService(client)
    if should_stop:
        class Stop:
            def is_set(self):return bool(should_stop())
        service.stop=Stop()
    live=service.snapshot(refresh=True)
    status=login_bonus_status(live)
    if not status["known"]:raise CommandUnavailable(status["reason"])
    return claim_daily_login(service) if status["available"] else CommandResult(message=status["reason"])

def register_daily_login(service):
    service.register(CommandSpec("daily-login","Rewards","Claim explicitly available free login rewards and verify fresh calendar counters.",
        action=True,evidence="Native claimDailyBonus capture; monthly/weekly can_claim and bonus counters"),lambda current,_args:claim_daily_login(current))
