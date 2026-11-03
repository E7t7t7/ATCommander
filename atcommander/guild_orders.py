"""Deterministic guild-order parsing. Message text is data, never executable commands."""
import re
import time
from .account_state import parse_siege_status

GUILD_ID=520905719516626945
ATTACK_CHANNEL=1254172326413471834
DEFENSE_CHANNEL=559035124012941312
MAX_AGE=72*3600
DEFENSE_MAX_AGE=7*86400
ALIASES={
 'Family Guy':('family guy','fg'), 'King of the Hill':('king of the hill','koth','kh'),
 'American Dad':('american dad','ad'), 'Futurama':('futurama',),"Bob's Burgers":("bob's burgers",'bobs burgers','bob’s burgers','bb'),
 'Archer':('archer',),
 **{name:(name.lower(),)for name in ('Animal','Armed','Art','Athletic','Disguised','Drunk','Educated','Fighter','Food','Hyper','Musical','Rich','Toy','Vehicle','Addicted')}}

def names_in(text):
    text=re.sub(r'<a?:\w+:\d+>|:[A-Za-z_]+:',' ',str(text))
    found=[]
    for canonical,aliases in ALIASES.items():
        matches=[m for alias in aliases for m in re.finditer(r'(?<!\w)'+re.escape(alias)+r'(?!\w)',text,re.I)]
        if matches:found.append((min(m.start()for m in matches),canonical))
    return [name for _,name in sorted(found)]

def _fresh(row,now,max_age=MAX_AGE):
    stamp=row.get('at')
    return type(stamp)in(int,float)and 0<=now-stamp<=max_age

def attack_order(text):
    if '?'in text or re.search(r"\b(don['’]?t|do not|not|avoid|cancel|instead|ignore|except|unless)\b",text,re.I):
        raise ValueError('Attack wording is conditional or negative; review the latest post.')
    match=re.search(r'\b(?:going|target(?:ing)?|hit(?:ting)?|attack(?:ing)?|focus(?:ing)?|priorit(?:y|ize|ise))\b[:\s-]*(.*)',text,re.I|re.S)
    if not match:raise ValueError('No explicit attack direction was found.')
    targets=names_in(match.group(1))
    if not targets or len(targets)>3:raise ValueError('Could not identify one to three clear attack priorities.')
    return targets

def defense_order(text,owner_id,aliases):
    own=re.compile(r'<@!?'+re.escape(str(owner_id))+r'>')if str(owner_id).isdigit()else None
    def mine(line):
        if own and own.search(line):return True
        return any(re.search(r'(?<![\w])@?'+re.escape(a.strip())+r'(?![\w])',line,re.I)for a in aliases if a.strip())
    current=None;found=[]
    for line in str(text).splitlines():
        heading=re.search(r'^(.*?)\bisland\s*:\s*(.*)$',line,re.I)
        if heading:
            names=names_in(heading.group(1));current=names[0]if len(names)==1 else None
            content=heading.group(2)
        else:content=line
        if current and mine(content):found.append(current)
    distinct=list(dict.fromkeys(found))
    if len(distinct)!=1:raise ValueError('Your defense assignment is missing or conflicting in the latest Calculon post.')
    return distinct[0]

def parse_orders(attack,defense,*,owner_id,aliases=(),leader_ids=(),calculon_id='',now=None):
    now=time.time()if now is None else now;out={'fetched_at':now,'attack':{},'defense':{}}
    leaders=set(map(str,leader_ids))
    candidates=[r for r in attack if _fresh(r,now)and not r.get('bot')and(not leaders or str(r.get('author_id'))in leaders)
        and re.search(r'\bsiege\b',r.get('text',''),re.I)and re.search(r'\b(going|target|hit|attack|focus|priority|cancel|avoid|don.t)\w*\b',r.get('text',''),re.I)]
    if candidates:
        row=max(candidates,key=lambda r:r['at'])
        out['attack']={'at':row['at'],'url':row.get('url','')}
        try:out['attack']['targets']=attack_order(row.get('text',''))
        except ValueError as error:out['attack']['error']=str(error)
    else:out['attack']['error']='No recent, readable Siege attack announcement was found in the configured channel.'
    posts=[r for r in defense if _fresh(r,now,DEFENSE_MAX_AGE)and r.get('bot')and(str(r.get('author_id'))==str(calculon_id)if calculon_id else str(r.get('author_name','')).casefold()=='calculon')]
    if posts:
        newest=max(r['at']for r in posts);batch=[r for r in posts if newest-r['at']<=600]
        found=[];urls=[]
        for row in batch:
            try:found.append(defense_order(row.get('text',''),owner_id,aliases));urls.append(row.get('url',''))
            except ValueError:
                # An individual split post may omit the owner; conflicting owner
                # rows inside one post remain an explicit error below.
                if _has_owner(row.get('text',''),owner_id,aliases):found.append(None)
        out['defense']={'at':newest,'url':next(iter(urls),'')}
        if found and None not in found and len(set(found))==1:out['defense']['island']=found[0]
        else:out['defense']['error']='Your assignment is missing or conflicting in the latest Calculon message batch.'
    else:out['defense']['error']='No recent readable Calculon assignment was found in the configured channel.'
    return out

def _has_owner(text,owner_id,aliases):
    return bool(re.search(r'<@!?'+re.escape(str(owner_id))+r'>',text))or any(re.search(r'(?<!\w)@?'+re.escape(a.strip())+r'(?!\w)',text,re.I)for a in aliases if a.strip())

def match_island(name,rows):
    matches=[r for r in rows if names_in(r.get('name',''))==[name]]
    if len(matches)!=1:raise ValueError('The live Siege map does not uniquely identify '+str(name)+'.')
    return str(matches[0]['slot_id'])

def resolve_orders(plan,status,*,attack=False,defense=False,now=None):
    now=time.time()if now is None else now;result={};live=parse_siege_status(status)
    if attack:
        source=plan.get('attack',{})
        if source.get('error')or not source.get('targets')or not _fresh(source,now):raise ValueError('Fresh, unambiguous guild attack orders are required; no fallback order was guessed.')
        result['targets']=[match_island(name,live['enemy'])for name in source['targets']]
    if defense:
        source=plan.get('defense',{})
        if source.get('error')or not source.get('island')or not _fresh(source,now,DEFENSE_MAX_AGE):raise ValueError('A fresh, unique Calculon defense assignment is required.')
        result['island_id']=match_island(source['island'],live['friendly'])
    return result
