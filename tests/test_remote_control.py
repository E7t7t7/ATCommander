from concurrent.futures import Future
from datetime import datetime,timedelta
from pathlib import Path
import importlib.util
import tempfile
import unittest
from unittest.mock import Mock,patch,AsyncMock
from atcommander.command_store import CommandStore
from atcommander.remote_control import RemoteInbox,EventTimers,RemoteRequest,remote_command
from atcommander.utility_commands import build_service
from atcommander.command_center import SchemaClient
from atcommander.remote_center import RemoteCenterMixin

OWNER=123456789012345678

def stamp(year,month,day,hour=0,minute=0):return datetime(year,month,day,hour,minute).timestamp()

class InboxTests(unittest.TestCase):
 def test_owner_only(self):
  inbox=RemoteInbox(OWNER)
  with self.assertRaises(PermissionError):inbox.submit(OWNER+1,1,'stop')
  self.assertTrue(inbox.queue.empty())
 def test_same_interaction_never_queued_twice(self):
  inbox=RemoteInbox(OWNER);inbox.submit(OWNER,1,'run','Siege')
  with self.assertRaises(ValueError):inbox.submit(OWNER,1,'run','Siege')
  self.assertEqual(inbox.queue.qsize(),1)
 def test_close_resolves_pending_without_execution(self):
  inbox=RemoteInbox(OWNER);future=inbox.submit(OWNER,1,'run','Rumble');inbox.close()
  self.assertIn('before',future.result())
  with self.assertRaises(ValueError):inbox.submit(OWNER,2,'run','Rumble')
 def test_cancelled_request_is_not_started(self):
  inbox=RemoteInbox(OWNER);future=inbox.submit(OWNER,1,'stop');future.cancel()
  self.assertFalse(inbox.queue.get_nowait().result.set_running_or_notify_cancel())
 def test_invalid_owner_and_requests(self):
  for owner in ('',True,'abc','123'):
   with self.assertRaises(ValueError):RemoteInbox(owner)
  inbox=RemoteInbox(OWNER)
  with self.assertRaises(ValueError):inbox.submit(OWNER,1,'shell','anything')
  with self.assertRaises(ValueError):inbox.submit(OWNER,2,'command','x'*1801)

class TimerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=CommandStore(Path(self.tmp.name)/'state.json');self.book=EventTimers(self.store)
 def test_siege_next_monday_not_tuesday(self):
  now=stamp(2026,11,3,14);self.book.arm('Siege',30,now)
  self.assertEqual(self.book.rows['Siege']['due'],stamp(2026,11,9))
  self.assertEqual(self.book.due(now),[])
  self.assertEqual(self.book.due(stamp(2026,11,9,9)),['Siege'])
 def test_monday_checks_repeat_until_window_passes(self):
  now=stamp(2026,11,9,23,50);self.book.arm('Siege',30,now);self.book.claim('Siege',now);self.book.finish('Siege')
  self.assertEqual(self.book.rows['Siege']['due'],stamp(2026,11,16))
 def test_missed_monday_advances_to_next_week(self):
  self.book.arm('Siege',30,stamp(2026,11,9,10))
  self.assertEqual(self.book.due(stamp(2026,11,10,10)),[])
  self.assertEqual(self.book.rows['Siege']['due'],stamp(2026,11,16))
 def test_repeated_checks_collapse_missed_intervals(self):
  now=stamp(2026,11,3);self.book.arm('Rumble',30,now)
  later=now+7200;self.book.claim('Rumble',later)
  self.assertEqual(self.book.rows['Rumble']['due'],later+1800)
  self.assertEqual(self.book.due(later),[])
 def test_interrupted_schedule_does_not_restart_automatically(self):
  now=stamp(2026,11,9);self.book.arm('Siege',30,now);self.book.claim('Siege',now)
  loaded=EventTimers(self.store)
  self.assertFalse(loaded.rows['Siege']['enabled']);self.assertIn('interrupted',loaded.rows['Siege']['note'])
 def test_idle_schedule_survives_restart(self):
  self.book.arm('Rumble',30,stamp(2026,11,3));self.assertTrue(EventTimers(self.store).rows['Rumble']['enabled'])
 def test_fatal_pauses_only_its_timer(self):
  now=stamp(2026,11,9);self.book.arm('Siege',30,now);self.book.arm('Rumble',30,now);self.book.claim('Siege',now);self.book.finish('Siege',True)
  self.assertFalse(self.book.rows['Siege']['enabled']);self.assertTrue(self.book.rows['Rumble']['enabled'])
 def test_weekday_can_be_changed(self):
  now=stamp(2026,11,3,10);self.book.arm('Siege',30,now,weekday=1)
  self.assertEqual(self.book.due(now),['Siege'])
  with self.assertRaises(ValueError):self.book.arm('Siege',30,now,weekday=9)
 def test_weekly_explicit_first_time_preserved(self):
  now=stamp(2026,11,3,10);self.book.arm('Siege',30,now,first=stamp(2026,11,3,16))
  self.assertEqual(self.book.rows['Siege']['due'],stamp(2026,11,9,16))
 def test_disarm_prevents_run(self):
  self.book.arm('Rumble',30,0);self.book.disarm('Rumble');self.assertEqual(self.book.due(10000),[])

class RemoteCommandTests(unittest.TestCase):
 def setUp(self):self.schema=build_service(SchemaClient())
 def test_safe_registered_read_is_supported(self):
  self.assertEqual(remote_command(self.schema,'play-status','')[0],'play-status')
 def test_purchase_and_destructive_specs_stay_on_pc(self):
  names=[s.name for s in self.schema.specs()if s.destructive or s.spends_resources or s.sends_message]
  self.assertTrue(names)
  for name in names:
   with self.subTest(name=name),self.assertRaises(ValueError):remote_command(self.schema,name,'')
 def test_unknown_command_cannot_execute_shell(self):
  with self.assertRaises(ValueError):remote_command(self.schema,'powershell','Get-Process')

@unittest.skipUnless(importlib.util.find_spec('discord'),'Optional discord.py is not installed')
class DiscordDefinitionsTests(unittest.IsolatedAsyncioTestCase):
 async def test_slash_commands_build_without_any_discord_or_game_connection(self):
  import discord
  from atcommander.remote_discord import DiscordConnection
  connection=DiscordConnection(RemoteInbox(OWNER),Mock())
  with patch('atcommander.cm_discord_bot._load_token',return_value='synthetic-not-a-token'),patch.object(discord.Client,'start',new_callable=AsyncMock)as start:
   await connection._run()
  start.assert_awaited_once()
  self.assertIsNotNone(connection.client.setup_hook)

class ScheduledSiegeTests(unittest.TestCase):
 def test_inactive_siege_does_not_wait_change_decks_or_launch_ads(self):
  from atcommander.event_runner import EventBattleRunner
  events=[];client=Mock();client.init.return_value={};client.get_guild_siege_status.return_value={'guild_siege_status':{'phase':1,'num_attacks':10}}
  with patch('atcommander.event_runner.ATClient',return_value=client),patch('atcommander.event_runner.load_credentials'),patch('atcommander.event_runner.ad_preflight')as ads,patch('atcommander.event_runner.LifetimeTracker'):
   runner=EventBattleRunner('Siege',offense_deck_id='20',siege_once=True,event=events.append);runner._run()
  client.apply_selection.assert_not_called();client.start_siege_battle.assert_not_called();ads.assert_not_called()
  self.assertEqual(events[-1]['kind'],'finished')

class RemoteDispatchTests(unittest.TestCase):
 def test_busy_scheduler_does_not_claim_a_due_job(self):
  from types import SimpleNamespace
  app=SimpleNamespace(_closing=False,discord_connection=None,event_timers=Mock())
  with patch('atcommander.remote_center.CommandCenterMixin._browser_action_busy',return_value=True):RemoteCenterMixin._remote_tick(app)
  app.event_timers.claim.assert_not_called()
 def test_cancelled_phone_request_never_dispatches(self):
  from types import SimpleNamespace
  inbox=RemoteInbox(OWNER);future=inbox.submit(OWNER,1,'stop');future.cancel()
  app=SimpleNamespace(_closing=False,discord_connection=SimpleNamespace(inbox=inbox),event_timers=Mock(),_remote_dispatch=Mock())
  with patch('atcommander.remote_center.CommandCenterMixin._browser_action_busy',return_value=True):RemoteCenterMixin._remote_tick(app)
  app._remote_dispatch.assert_not_called()
 def test_timer_waits_for_saved_decks_to_load_before_claiming(self):
  from types import SimpleNamespace
  app=SimpleNamespace(_closing=False,discord_connection=None,event_timers=Mock(),deck_rows=[])
  with patch('atcommander.remote_center.CommandCenterMixin._browser_action_busy',return_value=False):RemoteCenterMixin._remote_tick(app)
  app.event_timers.claim.assert_not_called()
