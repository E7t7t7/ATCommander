"""Owner-only Discord requests and durable event timers, independent of Discord/Tk."""
from concurrent.futures import Future
from datetime import datetime,timedelta
from dataclasses import dataclass,field
import math
import queue
import threading
import time
import uuid

MODES=('Siege','Rumble')
DAYS=('Every day','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')

def allowed_time(now,weekday):
    if weekday is None:return now
    current=datetime.fromtimestamp(now)
    days=(weekday-current.weekday())%7
    if not days:return now
    return (current+timedelta(days=days)).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()

@dataclass
class RemoteRequest:
    action:str
    target:str=''
    arguments:str=''
    result:Future=field(default_factory=Future)

class RemoteInbox:
    def __init__(self,owner_id):
        if not str(owner_id).isdigit() or not 15<=len(str(owner_id))<=22:raise ValueError('Enter your Discord user ID first.')
        self.owner_id=int(owner_id);self.queue=queue.Queue(maxsize=16);self._seen=set();self._lock=threading.Lock();self.closed=False
    def submit(self,user_id,request_id,action,target='',arguments=''):
        if type(user_id)is not int or user_id!=self.owner_id:raise PermissionError('This Commander connection belongs to another Discord account.')
        if action not in {'status','run','command','stop','timer','help'}:raise ValueError('Unknown Commander action.')
        if not all(isinstance(s,str)and len(s)<=1800 for s in (target,arguments)):raise ValueError('The command is too long.')
        with self._lock:
            if self.closed:raise ValueError('Commander is disconnecting.')
            if request_id in self._seen:raise ValueError('This request was already received; it was not repeated.')
            request=RemoteRequest(action,target,arguments)
            try:self.queue.put_nowait(request)
            except queue.Full:raise ValueError('Commander is busy. Try again shortly.')from None
            if len(self._seen)>1000:self._seen.clear()
            self._seen.add(request_id)
            return request.result
    def close(self):
        with self._lock:
            self.closed=True
            while True:
                try:request=self.queue.get_nowait()
                except queue.Empty:break
                if not request.result.done():request.result.set_result('Commander disconnected before this request ran.')

class EventTimers:
    def __init__(self,store):
        self.store=store;raw=store.read().get('event_timers',{});self.rows={}
        for mode in MODES:
            row=raw.get(mode,{})if isinstance(raw,dict)else{}
            if not isinstance(row,dict):row={}
            interval=row.get('minutes',30);due=row.get('due',0)
            weekday=row.get('weekday',0 if mode=='Siege' else None)
            if weekday is not None and (type(weekday)is not int or not 0<=weekday<=6):weekday=None
            self.rows[mode]={'weekday':weekday,'enabled':row.get('enabled')is True,'minutes':interval if type(interval)is int and 1<=interval<=1440 else 30,
                'due':float(due)if type(due)in(int,float)and math.isfinite(due)and due>=0 else 0,'active':'','note':str(row.get('note',''))[:200]}
            if row.get('active'):
                self.rows[mode].update(enabled=False,note='Previous scheduled run was interrupted. Check its outcome, then arm again.')
        self.save()
    def save(self):self.store.update('event_timers',self.rows)
    def arm(self,mode,minutes,now,first=None,weekday="saved"):
        if mode not in MODES or type(minutes)is not int or not 1<=minutes<=1440:raise ValueError('Choose Siege/Rumble and a check interval from 1 to 1440 minutes.')
        if self.rows[mode]['active']:raise ValueError('Stop the current event run before changing its timer.')
        weekday=self.rows[mode]['weekday']if weekday=='saved'else weekday
        if weekday is not None and (type(weekday)is not int or not 0<=weekday<=6):raise ValueError('Choose a weekday or Every day.')
        due=max(now,float(first))if first is not None else now
        if first is not None and weekday is not None:
            date=datetime.fromtimestamp(due);due=(date+timedelta(days=(weekday-date.weekday())%7)).timestamp()
        else:due=allowed_time(due,weekday)
        self.rows[mode].update(enabled=True,minutes=minutes,weekday=weekday,due=due,active='',note='Armed')
        self.save()
    def disarm(self,mode):
        self.rows[mode].update(enabled=False,note='Disarmed');self.save()
    def due(self,now):
        changed=False
        for row in self.rows.values():
            if row['enabled']and not row['active']and row['due']<=now:
                next_allowed=allowed_time(now,row['weekday'])
                if next_allowed>now:row['due']=next_allowed;changed=True
        if changed:self.save()
        ready=[m for m in MODES if self.rows[m]['enabled']and not self.rows[m]['active']and self.rows[m]['due']<=now]
        return sorted(ready,key=lambda m:(self.rows[m]['due'],m))
    def claim(self,mode,now):
        row=self.rows[mode]
        if mode not in self.due(now):raise ValueError('This timer is not due.')
        row.update(active=uuid.uuid4().hex,due=allowed_time(now+row['minutes']*60,row['weekday']),note='Running');self.save()
    def finish(self,mode,failed=False):
        row=self.rows[mode];row.update(active='',note='Paused after an error; review before arming again.'if failed else 'Waiting for next check')
        if failed:row['enabled']=False
        self.save()


def remote_command(schema,name,arguments):
    """Use the existing registry; no shell, arbitrary programs or destructive jobs."""
    from .local_commands import parse_argument_text
    spec=schema.spec(name)
    if spec.destructive or spec.spends_resources or spec.sends_message:
        raise ValueError('Use Commander on the PC for this destructive/purchase/message command. Battle runs use /commander run.')
    if spec.execution_mode=='runner':raise ValueError('Start battle automation with /commander run.')
    values=spec.parse_args(parse_argument_text(spec,arguments))
    return spec.name,values
