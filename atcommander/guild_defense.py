"""Apply a verified friendly-island assignment once, then read back membership."""
import hashlib
import time
from .guild_orders import resolve_orders
from .account_state import parse_siege_status
from .shop_rewards import api_rejected


def membership_slot(status,user_id):
    raw=(status or{}).get('guild_siege_status',status or{});locations=raw.get('locations',{})
    rows=locations.values()if isinstance(locations,dict)else locations if isinstance(locations,list)else[]
    matches=[]
    for row in rows:
        if not isinstance(row,dict):continue
        members=row.get('users',[]);values=list(members.items())if isinstance(members,dict)else[(None,m)for m in members]if isinstance(members,list)else[]
        for key,member in values:
            uid=member.get('user_id',member.get('id',key))if isinstance(member,dict)else member
            if str(uid)==str(user_id):matches.append(str(row.get('slot_id',row.get('id',''))))
    unique=set(matches)
    return next(iter(unique))if len(unique)==1 else None


def apply_assigned_defense(client,store,plan,*,should_stop=lambda:False):
    scope=hashlib.sha256(client.credentials.user_id.encode()).hexdigest()[:24]
    status=client.get_guild_siege_status()
    if not isinstance(status,dict)or api_rejected(status):raise ValueError('Could not read the current friendly Siege map.')
    pending=store.read().get('guild_defense_pending')
    current=membership_slot(status,client.credentials.user_id)
    if pending:
        if pending.get('scope')==scope and current==str(pending.get('slot')):
            store.update('guild_defense_pending',None)
        else:raise ValueError('A prior defense assignment remains unconfirmed. It was not repeated; check your island in the game.')
    target=resolve_orders(plan,status,defense=True)['island_id']
    if current==target:return 'Your Calculon defense assignment is already active.'
    if parse_siege_status(status)['phase']!=1:return 'Defense assignment found, but Siege is outside its setup phase. No defense change was sent.'
    if should_stop():return 'Defense setup stopped before sending a change.'
    store.update('guild_defense_pending',{'scope':scope,'slot':target,'at':time.time()})
    answer=client.set_siege_defense_island(target)
    if isinstance(answer,dict)and api_rejected(answer):
        store.update('guild_defense_pending',None);raise ValueError('The game rejected the defense assignment; it was not repeated.')
    after=client.get_guild_siege_status()
    if membership_slot(after,client.credentials.user_id)!=target:raise ValueError('Defense change is not yet confirmed by fresh membership. It was not repeated; check the game before another change.')
    store.update('guild_defense_pending',None)
    return 'Defense assignment verified: '+plan['defense']['island']+'.'
