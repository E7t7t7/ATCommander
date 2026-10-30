"""Keep a chosen deck name associated with its real server key, not list position."""
import re

DECK_BINDINGS=(('deck_var','arena_deck_id'),('rumble_deck','rumble_defense_deck_id'),
 ('siege_deck','siege_defense_deck_id'),('rumble_offense_deck','rumble_offense_deck_id'),
 ('siege_offense_deck','siege_offense_deck_id'),('challenge_offense_deck','challenge_offense_deck_id'))
OFFENSE_VARIABLES=('deck_var','rumble_offense_deck','siege_offense_deck','challenge_offense_deck')


def label_parts(label):
    match=re.fullmatch(r'(.*?)\s+\[ID\s+([^\]]+)\]',str(label or''))
    return (match.group(2),match.group(1).strip())if match else(None,None)


def resolve_deck(identity,name,rows):
    current=next((r for r in rows if str(r.get('id'))==str(identity)),None)
    if name:
        matches=[r for r in rows if str(r.get('name','')).casefold()==str(name).strip().casefold()]
        if current in matches:return str(current['id'])
        if len(matches)==1:return str(matches[0]['id'])
        if len(matches)>1:return None
    return str(current['id'])if current else None


def saved_name_key(key):return key[:-3]+'_name'
