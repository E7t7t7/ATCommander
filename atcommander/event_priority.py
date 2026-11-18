"""Cooperative event priority. Checkpoints belong only between complete actions."""
import threading
import time
from .temporary_daily_hero import exact_active_commander

class EventPriority:
    def __init__(self):
        self._lock=threading.Condition();self.requested=False;self.blocked=False;self._parked=set()
    def request(self):
        with self._lock:self.requested=True
    def release(self):
        with self._lock:
            self.requested=False;self.blocked=False;self._lock.notify_all()
    def hold_after_error(self):
        with self._lock:self.requested=True;self.blocked=True
    def parked(self,owner):
        with self._lock:return owner in self._parked
    def has_parked(self):
        with self._lock:return bool(self._parked)
    def checkpoint(self,owner,stop,on_resume=None):
        waited=False
        with self._lock:
            try:
                while self.requested and not stop.is_set():
                    waited=True;self._parked.add(owner);self._lock.wait(.1)
            finally:self._parked.discard(owner)
        if stop.is_set():return False
        if waited and on_resume:on_resume()
        return True

def checkpoint(worker,stop,on_resume=None):
    gate=getattr(worker,'event_priority',None)
    if on_resume is None:on_resume=getattr(getattr(worker,'lifetime',None),'load',None)
    return gate.checkpoint(getattr(worker,'priority_owner','commands'),stop,on_resume)if gate is not None else not stop.is_set()

def bind(app,worker,owner):
    worker.event_priority=getattr(app,'event_priority',None);worker.priority_owner=owner
    return worker

def priority_wait(worker,stop,seconds):
    if getattr(worker,'event_priority',None)is None:return stop.wait(seconds)
    end=time.monotonic()+seconds
    while not stop.is_set():
        if not checkpoint(worker,stop):return True
        remaining=end-time.monotonic()
        if remaining<=0:return False
        stop.wait(min(.2,remaining))
    return True

def capture_selection(client,live):
    try:return exact_active_commander(live)
    except Exception:return exact_active_commander(client.get_exact_decks(live))

def restore_selection(client,original):
    """Restore at most once and verify; an uncertain write is never repeated."""
    live=client.get_exact_decks(client.init())
    current=exact_active_commander(live)
    original_view={**live,'user_data':{**live.get('user_data',{}),'active_deck':original.deck_id}}
    original_now=exact_active_commander(original_view)
    if original.unit_indices is not None and original_now.unit_indices!=original.unit_indices:
        raise ValueError('The original deck cards changed; regular tasks remain held for review.')
    if current.deck_id!=original.deck_id or current.commander_id!=original.commander_id:
        try:client.apply_selection(deck_id=original.deck_id,hero_id=original.commander_id)
        except Exception:pass
        current=exact_active_commander(client.get_exact_decks(client.init()))
    if current.deck_id!=original.deck_id or current.commander_id!=original.commander_id:
        raise ValueError('The original deck and hero could not be restored; regular tasks remain held for review.')
    if original.unit_indices is not None and current.unit_indices!=original.unit_indices:
        raise ValueError('The original deck cards changed; regular tasks remain held for review.')

def release_tasks(app):
    suspended=getattr(app,'_priority_suspended_event',None)
    if suspended is not None:app.event_runner=suspended;app._priority_suspended_event=None
    gate=getattr(app,'event_priority',None)
    if getattr(app,'_priority_suspended_job',None)is not None:
        app.remote_job=app._priority_suspended_job;app._priority_suspended_job=None
    if gate is not None:gate.release()
