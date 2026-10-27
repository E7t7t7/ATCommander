"""Shared filters using the current card catalog's show/trait/skill fields."""
import re
from .cm_reviews import SKILL_NAMES, _traits, _skill_ids

SHOWS={"1":"Family Guy","2":"American Dad","3":"Bob's Burgers",
       "4":"King of the Hill","5":"Futurama","7":"Archer"}


def normalized(value):return re.sub(r"[^a-z0-9]","",str(value).casefold())


def any_text(text, query):
    return not query or any(all(word in str(text).casefold() for word in part.casefold().split())
                            for part in str(query).split("|") if part.strip())


def show_matches(row, query):
    if not query or str(query).casefold()=="all":return True
    value=str(row.get("type",row.get("show","")))
    name=SHOWS.get(value,value)
    aliases={normalized(name),normalized(value)}
    short={"1":"fg","2":"ad","3":"bb","4":"koth","5":"ft","7":"ar"}.get(value)
    if short:aliases.add(short)
    return any(normalized(part) in aliases for part in str(query).split("|"))


def skill_matches(row, query):
    if not query:return True
    skills=set(_skill_ids(row))
    names={normalized(s) for s in skills}|{normalized(SKILL_NAMES.get(s,s)) for s in skills}
    return any(normalized(part) in names for part in str(query).split("|"))


def card_matches(row, args):
    return (show_matches(row,args.get("show"))
            and (not args.get("trait") or any_text(" ".join(_traits(row)),args["trait"]))
            and skill_matches(row,args.get("skill"))
            and skill_matches(row,args.get("skill2")))
