"""Frozen scheduled-event defaults, separate from transient Events controls."""
from copy import deepcopy
from .account_state import exact_decks
from .deck_selection import resolve_deck

def initial_defaults(settings,mode):
    prefix=mode.lower()
    return {'offense_deck_id':str(settings.get(prefix+'_offense_deck_id')or''),'offense_deck_name':str(settings.get(prefix+'_offense_deck_name')or''),
        'offense_hero_id':str(settings.get(prefix+'_offense_hero_id')or''),'defense_deck_id':str(settings.get(prefix+'_defense_deck_id')or''),
        'defense_deck_name':str(settings.get(prefix+'_defense_deck_name')or''),'island_id':str(settings.get('siege_island_id')or'')if mode=='Siege'else'',
        'targets':[str(settings[k])for k in ('siege_target_a_id','siege_target_b_id','siege_target_c_id')if settings.get(k)]if mode=='Siege'else[],
        'fallback':settings.get('siege_fallback_any',True)is True,'logic':str(settings.get(prefix+'_logic')or'Item → Combo (default)')}

def validated_defaults(value,snapshot):
    result=deepcopy(value);decks=exact_decks(snapshot)
    for prefix in ('offense','defense'):
        identity=value.get(prefix+'_deck_id')
        if identity:
            name=value.get(prefix+'_deck_name');resolved=resolve_deck(identity,name,decks)
            if resolved is None:raise ValueError('The scheduled '+prefix+' deck no longer exists or is ambiguous. Review timer defaults.')
            # A changed ID must be backed by the saved exact name; an unrelated
            # deck cannot inherit a scheduled job merely by retaining its key.
            row=next(r for r in decks if str(r['id'])==resolved)
            if name and str(row.get('name','')).casefold()!=str(name).casefold():raise ValueError('The scheduled '+prefix+' deck was renamed; review its timer defaults.')
            result[prefix+'_deck_id']=resolved
    hero=result.get('offense_hero_id','')
    if hero and(not str(hero).isdigit()or int(hero)<=0):raise ValueError('Invalid scheduled hero ID.')
    return result


def verify_loadout(value,snapshot):
    active=str((snapshot.get('user_data')or{}).get('active_deck',''))
    target=value.get('offense_deck_id')
    if target and active!=str(target):raise ValueError('The scheduled offense deck switch was not confirmed. No fight was started.')
    if value.get('offense_hero_id'):
        from .deck_builder import deck_stamp
        if not active or str(deck_stamp(snapshot,active)['commander'])!=str(value['offense_hero_id']):raise ValueError('The scheduled hero switch was not confirmed. No fight was started.')
