"""Bounded Arena hero-reward hunting using the live target and Nixon cost."""
from .utility_commands import rows
from .local_commands import CommandInputError,CommandUnavailable
from .research_automation import number
from .shop_rewards import api_rejected


def _opponent_id(value):
    # The target's native ID is an opaque API value, not a commander/card ID.
    # Preserve nonempty string IDs while refusing malformed scalar containers.
    if isinstance(value,bool):return False
    if isinstance(value,int):return value>0
    if isinstance(value,str) and value.strip():
        numeric=number(value.strip())
        return numeric is None or numeric>0
    return False


def hero_names(service):
    from .account_state import FALLBACK_HERO_NAMES
    names=dict(FALLBACK_HERO_NAMES)
    # Reuse confirmed training names. The live manifest often omits hero_data;
    # it must not prevent reward matching against a real hero_xp_id.
    for cid,row in service.context._catalogs.get("hero",{}).items():
        if isinstance(row,dict) and row.get("name"):names[str(cid)]=str(row["name"])
    for row in rows(service.snapshot().get("user_hero_squad")):
        cid=row.get("hero_id",row.get("id"));name=row.get("name",row.get("hero_name"))
        if cid is not None and name:names[str(cid)]=str(name)
    return names


def requested_heroes(service,text):
    names=hero_names(service);wanted=set()
    for part in str(text).split("|"):
        part=part.strip().casefold()
        matches={cid for cid,name in names.items() if part and part in {cid.casefold(),name.casefold()}}
        if not matches and part.isdigit() and int(part)>0:matches={str(int(part))}
        if not matches:raise CommandInputError("Choose exact hero names or IDs from user-heroes, separated by |.")
        wanted.update(matches)
    return wanted


def hunt_target(service,wanted,max_refreshes,max_nixons):
    limits = (number(max_refreshes), number(max_nixons))
    if any(value is None or value < 0 for value in limits):
        raise CommandInputError("Opponent refresh limits must be nonnegative whole numbers.")
    max_refreshes, max_nixons = limits
    if getattr(service,"stop",None) is not None and service.stop.is_set():return None,0,0
    current=service.call("getHuntingTargets");spent=refreshes=0
    while True:
        if not isinstance(current,dict) or not current or api_rejected(current):
            raise CommandUnavailable("Current opponent rewards were unavailable. No refresh or fight was started.")
        if getattr(service,"stop",None) is not None and service.stop.is_set():return None,spent,refreshes
        targets=rows(current.get("hunting_targets"))
        match=next((r for r in targets if number(r.get("hero_xp_id")) is not None
                    and str(number(r.get("hero_xp_id"))) in wanted
                    and _opponent_id(r.get("user_id"))),None)
        if match:return match,spent,refreshes
        if getattr(service,"stop",None) is not None and service.stop.is_set():return None,spent,refreshes
        if refreshes>=max_refreshes or not targets:return None,spent,refreshes
        if len(targets)!=1:raise CommandUnavailable("The refresh cost was ambiguous across several targets. No opponent was refreshed.")
        cost=number(targets[0].get("refresh_cost"))
        user=current.get("user_data")
        balance=number(user.get("money")) if isinstance(user,dict) else None
        if cost is None or balance is None or cost<0 or balance<0:raise CommandUnavailable("The live refresh price/balance was unavailable. No opponent was refreshed.")
        if cost>balance or spent+cost>max_nixons:return None,spent,refreshes
        answer=service.call("refreshHuntingTargets")
        if not isinstance(answer,dict) or not answer or api_rejected(answer):
            raise CommandUnavailable("AT did not confirm the opponent refresh. No refresh was retried and no fight was started.")
        user=answer.get("user_data")
        after=number(user.get("money")) if isinstance(user,dict) else None
        if after is None or after<0 or balance-after!=cost:
            raise CommandUnavailable("An opponent refresh was sent, but its Nixon cost was not confirmed. No refresh was retried and no fight was started.")
        spent+=cost;refreshes+=1;current=answer
