"""Local pending-Adventure journal; no authentication/request data is stored."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile
from .config import APP_DIR


class AdventureRunState:
    def __init__(self, identity, path=None):
        self.owner = hashlib.sha256(str(identity).encode('utf-8')).hexdigest()
        self.path = Path(path) if path is not None else APP_DIR / ('adventure_pending_' + self.owner[:16] + '.json')

    def _read_document(self):
        if not self.path.exists():
            return {'schema':1,'owner':self.owner,'pending':None}
        try:
            if self.path.stat().st_size > 8192:
                raise ValueError()
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(value,dict) or type(value.get('schema')) is not int or value.get('schema') != 1 or value.get('owner') != self.owner:
                raise ValueError()
            if 'pending' not in value:raise ValueError()
            pending = value.get('pending')
            self._validate(pending)
            history=value.get('recoveries',[])
            count=value.get('recovery_count',0)
            if not isinstance(history,list) or len(history)>8 or type(count) is not int or count<len(history):
                raise ValueError()
            for entry in history:
                if not isinstance(entry,dict) or set(entry)!={'pending','reason','checked_at'}:
                    raise ValueError()
                self._validate(entry['pending'])
                if entry['pending'] is None or entry['reason']!='completed_farm_no_resumable_battle':
                    raise ValueError()
                if type(entry['checked_at']) is not int or entry['checked_at']<=entry['pending']['server_time']:
                    raise ValueError()
            return value
        except Exception:
            raise RuntimeError('The pending Adventure journal is unreadable; no new fight was started.') from None

    def read(self):
        return self._read_document()['pending']

    def save(self, pending):
        self._validate(pending)
        value=self._read_document()
        value['pending']=pending
        self._write_document(value)

    def archive_completed_farm(self, expected, checked_at):
        """Retire an unknown ended farm attempt without awarding an outcome."""
        value=self._read_document()
        pending=value['pending']
        if pending!=expected or pending is None:
            raise RuntimeError('The pending Adventure record changed during recovery; it was preserved.')
        if (pending['phase'] not in {'battle_ready','move_pending','terminal'} or not pending['farming']
                or pending['wins_before']<12 or type(checked_at) is not int
                or checked_at-pending['server_time']<120):
            raise RuntimeError('The pending Adventure fight is not eligible for completed-farm recovery.')
        entry={'pending':dict(pending),'reason':'completed_farm_no_resumable_battle','checked_at':checked_at}
        value['recoveries']=(value.get('recoveries',[])+[entry])[-8:]
        value['recovery_count']=value.get('recovery_count',0)+1
        value['pending']=None
        self._write_document(value)
        return True

    def _write_document(self,value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.adventure-', suffix='.tmp', delete=False) as stream:
                temporary = stream.name
                json.dump(value, stream, allow_nan=False)
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink()
                except OSError:
                    pass

    @staticmethod
    def _validate(pending):
        if pending is None:return
        if not isinstance(pending,dict) or pending.get('phase') not in {'start_pending','battle_ready','move_pending','terminal'}:
            raise RuntimeError('The pending Adventure journal is invalid; no new fight was started.')
        if set(pending)-{'mission_id','campaign_id','wins_before','phase','cost','farming','server_time','terminal'}:
            raise RuntimeError('The pending Adventure journal contains unsupported fields.')
        if ('terminal' in pending)!=(pending['phase']=='terminal'):
            raise RuntimeError('The pending Adventure journal has an inconsistent phase/outcome.')
        for key,minimum in (('mission_id',1),('wins_before',0),('cost',0),('server_time',1)):
            if type(pending.get(key)) is not int or pending[key]<minimum:
                raise RuntimeError('The pending Adventure journal is incomplete; no new fight was started.')
        if not isinstance(pending.get('farming'),bool) or not isinstance(pending.get('campaign_id'),str) or not pending['campaign_id'].isdigit() or int(pending['campaign_id'])<1:
            raise RuntimeError('The pending Adventure journal is incomplete; no new fight was started.')
        if pending.get('terminal') is not None and not isinstance(pending['terminal'],bool):
            raise RuntimeError('The pending Adventure journal has an invalid outcome.')
