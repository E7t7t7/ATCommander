from queue import Queue
from types import SimpleNamespace
from pathlib import Path
import tempfile,threading,unittest,json,copy
from unittest.mock import Mock,patch
from atcommander.client_update_ui import ClientUpdateMixin
from atcommander.command_store import CommandStore

class Value:
 def set(self,value):self.value=value
 def get(self):return getattr(self,'value','')
class ClientUi(ClientUpdateMixin):
 def __init__(self,store):
  self._closing=False;self.client_update_thread=None;self.client_update_stop=threading.Event();self.client_update_status=Value();self.client_update_button=Mock();self.events=Queue();self.client_update_schedule=store;self.client_update_auto_due=0;self.client_update_auto_suspended=False

def immediate(*,target,**kwargs):return SimpleNamespace(start=target,is_alive=lambda:False)

class DailyClientCheckTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=CommandStore(Path(self.tmp.name)/'schedule.json');self.app=ClientUi(self.store)
 def test_one_daily_attempt_survives_restarts_and_advances_next_day(self):
  with patch('atcommander.client_update_ui.local_day',return_value='2026-11-10'),patch('atcommander.client_update_ui.threading.Thread',side_effect=immediate),patch('atcommander.client_update_ui.update_public_client_profile',return_value={'message':'Ready'})as check:
   self.app._client_update_tick();self.app._client_update_tick();ClientUi(self.store)._client_update_tick();self.assertEqual(check.call_count,1)
   with patch('atcommander.client_update_ui.local_day',return_value='2026-11-11'):ClientUi(self.store)._client_update_tick()
   self.assertEqual(check.call_count,2)
 def test_manual_check_remains_available_after_daily_attempt(self):
  self.store.update('last_attempt','2026-11-10')
  with patch('atcommander.client_update_ui.local_day',return_value='2026-11-10'),patch('atcommander.client_update_ui.threading.Thread',side_effect=immediate),patch('atcommander.client_update_ui.update_public_client_profile',return_value={'message':'Ready'})as check:
   self.app._start_client_update();check.assert_called_once()
 def test_daily_tick_never_cancels_a_running_manual_check(self):
  self.app.client_update_thread=SimpleNamespace(is_alive=lambda:True);self.app._client_update_tick();self.assertFalse(self.app.client_update_stop.is_set())
 def test_failed_check_is_not_retried_every_tick(self):
  with patch('atcommander.client_update_ui.threading.Thread',side_effect=immediate),patch('atcommander.client_update_ui.update_public_client_profile',side_effect=RuntimeError())as check:
   self.app._client_update_tick();self.app._client_update_tick();check.assert_called_once()
   event=self.app.events.get_nowait();self.app._client_update_event(event);self.assertNotIn('last_success',self.store.read())
 def test_cancelled_checks_do_not_record_success(self):
  self.app._client_update_event({'kind':'client_update_done','message':'Cancelled','cancelled':True});self.assertNotIn('last_success',self.store.read())
 def test_stop_all_suspension_prevents_background_start(self):
  self.app.client_update_auto_suspended=True
  with patch('atcommander.client_update_ui.threading.Thread',side_effect=AssertionError('No auto start')):self.app._client_update_tick()

class DailyRoutineUiTests(unittest.TestCase):
 def test_simple_layout_and_saved_custom_daily_limits(self):
  import tkinter as tk
  from atcommander.app import ATCommanderApp
  from atcommander.routine_page_ui import save_limits,toggle_details,run_selected
  root=tk.Tk();self.addCleanup(root.destroy)
  with tempfile.TemporaryDirectory()as temporary:
   store=CommandStore(Path(temporary)/'state.json')
   with patch.object(ATCommanderApp,'_refresh_account_metadata'),patch.object(ATCommanderApp,'_refresh_account_badge'),patch('atcommander.utility_commands.CommandStore',return_value=store),patch('atcommander.app.migrate_last_siege_browser_replay_metadata',return_value=False),patch('atcommander.app.migrate_last_native_replay_metadata',return_value=[]):app=ATCommanderApp(root)
   root.geometry('1340x760');app.nb.select(app.routines_tab);root.update_idletasks()
   self.assertTrue(app.routine_run_button.winfo_ismapped());self.assertGreater(app.routine_log.winfo_height(),30)
   self.assertFalse(app.routine_editor.winfo_ismapped());self.assertFalse(app.daily_extra.winfo_ismapped())
   app.routine_details.set(True);toggle_details(app);root.update_idletasks();self.assertTrue(app.routine_editor.winfo_ismapped())
   app.automation_profiles.save('My daily',copy.deepcopy(app.routine_steps));app.routine_profile.set('My daily');app._routine_load_profile()
   app.daily_vars['max_battles'].set('9');save_limits(app)
   saved=app.automation_profiles.load('My daily');self.assertEqual(json.loads(saved[0]['args']['options'])['max_battles'],9)
   with patch.object(app,'_routine_start')as start:
    run_selected(app);start.assert_called_once_with(False)
    app.routine_run_mode.set('Repeat');app.routine_repeat_minutes.set('15');run_selected(app);self.assertEqual(app.routine_interval.get(),'900')
   for callback in root.tk.call('after','info'):
    try:root.after_cancel(callback)
    except tk.TclError:pass
