"""Earned reward collection, with fresh eligibility and a no-replay claim ledger."""
from copy import deepcopy
import hashlib
import json
import re
from .local_commands import CommandUnavailable
from .research_automation import rows,number
from .reward_options import claim_plan,reward_state
from .shop_rewards import api_rejected

KINDS=('quests','mega','swole','clash','ecmc','sfc')


def collection_steps(live,now):
    result=[]
    level=number((live.get('user_data')or{}).get('level'))
    for collection in rows(live.get('user_collections')):
        cid=number(collection.get('id'));claimed=number(collection.get('milestone_claimed'))
        collected=collection.get('card_ids_collected');definitions=collection.get('milestone')
        if cid is None or cid<=0 or claimed is None or claimed<0 or not isinstance(collected,list) or not isinstance(definitions,(dict,list)):continue
        unlock=number(collection.get('unlock_level'))
        if unlock and (level is None or level<unlock):continue
        release=number(collection.get('release_time'))
        if release and release>now:continue
        if any(number(card) is None or number(card)<=0 for card in collected) or len(set(map(str,collected)))!=len(collected):continue
        milestones=sorted(rows(definitions),key=lambda r:number(r.get('id'))or 0)
        for milestone in milestones:
            mid=number(milestone.get('id'));required=number(milestone.get('points_req'))
            if mid is None or mid<=claimed:continue
            if required is None or required<=0 or len(collected)<required:break
            result.append({'kind':'collection','name':str(collection.get('name')or'Collection'),
                'method':'claimCollectionMilestone','payload':{'collection_id':cid,'milestone_id':mid,'cards':'[]'}})
            break
    return result


def _source(live,step):
    kind=step['kind'];payload=step['payload']
    if kind=='collection':section,identity='user_collections',payload['collection_id']
    elif kind=='mega':section,identity='user_mega_quest',payload['mega_quest_id']
    elif kind=='quests':
        found=[]
        def walk(value,depth=0):
            if depth>12:return
            if isinstance(value,dict):
                if number(value.get('id'))==payload['achievement_id']:found.append(value)
                for child in value.values():
                    if isinstance(child,(dict,list)):walk(child,depth+1)
            elif isinstance(value,list):
                for child in value:walk(child,depth+1)
        for quest in rows(live.get('user_achievements')):walk(quest)
        return found[0]if len(found)==1 else {}
    else:section,identity='active_events',payload['event_id']
    found=[r for r in rows(live.get(section))if number(r.get('id'))==number(identity)
           and ('cycle'not in payload or number(r.get('cycle'))==payload['cycle'])]
    return found[0]if len(found)==1 else {}


def _cursor(live,step):
    row=_source(live,step)
    if step['kind']=='collection':return number(row.get('milestone_claimed'))
    if step['kind']=='quests':return number(row.get('status'))
    if step['kind']=='mega':return [number(row.get('milestone_progress')),number(row.get('premium_milestone_progress'))]
    return sorted(str(r.get('tier',r.get('id')))for r in rows(row.get('milestone_reward'))if number(r.get('is_claimed'))==1)


def _marker(live,step):
    row=_source(live,step)
    data={k:row.get(k)for k in ('points','max_earned','milestone_progress','premium_milestone_progress','milestone_claimed','progress','status','has_claimed','is_claimed')}
    data['cursor']=_cursor(live,step)
    if step['kind']=='collection':data['collected_count']=len(row.get('card_ids_collected')or[])
    if step['kind']=='mega':
        iid=str(row.get('points_item_id',''))
        data['points_inventory']=[r.get('count',r.get('quantity'))for r in rows(live.get('user_items'))if str(r.get('item_id',r.get('id')))==iid]
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _advanced(live,step,before,accepted=False):
    after=_cursor(live,step)
    if step['kind']=='collection':return after is not None and after>=step['payload']['milestone_id']
    if step['kind']=='quests':return after==2 or (accepted and isinstance(live.get('user_achievements'),(dict,list)) and bool(live['user_achievements']) and not _source(live,step))
    if step['kind']=='mega':return isinstance(before,list)and any(isinstance(a,int)and isinstance(b,int)and a>b for a,b in zip(after,before))
    return isinstance(before,list)and set(after)>set(before)


def cleanup_earned_rewards(service,*,limit=100):
    """Claim only free earned reward sources; never buy tiers or open inventory items."""
    if isinstance(limit,bool)or not isinstance(limit,int)or not 1<=limit<=100:raise CommandUnavailable('Reward cleanup needs a limit from 1 to 100.')
    credentials=getattr(getattr(service,'client',None),'credentials',None)
    account=str(getattr(credentials,'user_id','local'))
    scope=hashlib.sha256(account.encode()).hexdigest()[:24]
    root=deepcopy(service.store.read().get('reward_cleanup',{}))
    state=deepcopy(root.get(scope,{'pending':{},'confirmed':{}}))
    if not isinstance(state.get('pending'),dict)or not isinstance(state.get('confirmed'),dict):raise CommandUnavailable('The reward claim journal needs review. No request was sent.')
    confirmed=[];advances=0;unavailable=0;seen=set()
    def save():
        if len(state['confirmed'])>256:state['confirmed']=dict(list(state['confirmed'].items())[-256:])
        root[scope]=state;service.store.update('reward_cleanup',root)
    def fresh():
        service.context.invalidate();return reward_state(service)
    def stopped():return getattr(service,'stop',None)is not None and service.stop.is_set()
    def key(step):return json.dumps([step['method'],step['payload'],int(service.clock())//86400 if step['kind']=='quests' else None],sort_keys=True,separators=(',',':'))
    if stopped():return {'confirmed':0,'verified_advances':0,'unavailable':0,'rows':[]}
    live=fresh()
    for identity,pending in list(state['pending'].items()):
        step=pending.get('step')
        if isinstance(step,dict) and step.get('kind')=='quests' and not _source(live,step) and not pending.get('accepted'):
            seen.add(identity);continue
        if not isinstance(step,dict)or not _advanced(live,step,pending.get('before_cursor'),pending.get('accepted',False)):
            raise CommandUnavailable('A previous reward claim has an unconfirmed outcome. It was not replayed; fresh progress must confirm it before cleanup resumes.')
        state['pending'].pop(identity);state['confirmed'][identity]=_marker(live,step);save()
    while len(confirmed)<limit and not stopped():
        if live.get('active_choices'):
            service.execute('item-choose');live=fresh()
            if live.get('active_choices'):raise CommandUnavailable('A pending reward choice was not confirmed. Cleanup stopped without another claim.')
        plan=collection_steps(live,int(service.clock()))+claim_plan(live,KINDS,int(service.clock()))
        eligible=[]
        for step in plan:
            identity=key(step)
            if identity in seen or state['confirmed'].get(identity)==_marker(live,step):continue
            eligible.append(step)
        if not eligible:break
        step=eligible[0];identity=key(step);seen.add(identity)
        state['pending'][identity]={'step':step,'before_cursor':_cursor(live,step)};save()
        before=state['pending'][identity]['before_cursor'];answer=None;error=None
        try:answer=service.call(step['method'],step['payload'])
        except Exception as exc:error=exc
        explicit_unavailable=error is not None and isinstance(error,CommandUnavailable)and bool(re.search(r'\bwarning_23_text\b',str(error)))
        accepted=isinstance(answer,dict)and bool(answer)and not api_rejected(answer)
        if accepted:
            state['pending'][identity]['accepted']=True;save()
        after=fresh()
        progressed=_advanced(after,step,before,accepted)
        if not (accepted or progressed or explicit_unavailable):
            raise CommandUnavailable('The reward claim could not be confirmed. Its saved request was not replayed; check again to reconcile fresh progress.')
        if step['kind']in {'collection','quests'}and not(progressed or explicit_unavailable):
            raise CommandUnavailable('The claimed reward progress did not advance. Cleanup paused without repeating its saved request.')
        state['pending'].pop(identity);state['confirmed'][identity]=_marker(after,step);save()
        confirmed.append({'kind':step['kind'],'name':step['name'],'confirmed':accepted or progressed,'progress_advanced':progressed,'unavailable':explicit_unavailable})
        advances+=int(progressed);unavailable+=int(explicit_unavailable);live=after
        if getattr(service,'emit',None):service.emit({'kind':'log','message':('Reward progress verified: 'if progressed else 'Reward source checked: ')+step['name']})
    return {'confirmed':sum(r['confirmed']for r in confirmed),'verified_advances':advances,'unavailable':unavailable,'pending':len(state['pending']),'rows':confirmed}
