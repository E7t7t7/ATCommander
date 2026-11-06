import asyncio
from concurrent.futures import Future
import importlib.util
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch,AsyncMock
from atcommander.discord_dm import parse_dm
from atcommander.remote_control import RemoteInbox

OWNER=123456789012345678

class DmGrammarTests(unittest.TestCase):
 def test_basic_plain_and_prefixed_commands(self):
  for text,expected in [('status',('status','','')),('!siege',('run','Siege','')),('rumble',('run','Rumble','')),('!commander stop',('stop','','')),('run siege',('run','Siege',''))]:
   self.assertEqual(parse_dm(text),expected)
 def test_pasted_calculon_roster_is_defense_input(self):
  text='Calculon: :trait_food: Food Island: @DrMund0, @Someone\nToy Island: @Other'
  self.assertEqual(parse_dm(text),('orders','defense',text))
 def test_pasted_sunday_post_is_attack_input(self):
  text='Happy Siege Sunday gang! Going Family Guy and Art today'
  self.assertEqual(parse_dm(text),('orders','attack',text))
 def test_explicit_orders_allow_prefix(self):self.assertEqual(parse_dm('attack Going Art today'),('orders','attack','Going Art today'))
 def test_weekday_timer(self):
  action,mode,args=parse_dm('timer siege Monday 30');self.assertEqual((action,mode),('timer','Siege'));self.assertEqual(json.loads(args),{'minutes':30,'weekday':0})
 def test_daily_timer_and_off(self):
  self.assertIsNone(json.loads(parse_dm('timer rumble daily 60')[2])['weekday']);self.assertEqual(parse_dm('timer siege off'),('timer','Siege','off'))
 def test_commands_are_parsed_not_executed(self):self.assertEqual(parse_dm('command play-status'),('command','play-status',''))
 def test_invalid_requests_cannot_chain_or_invoke_programs(self):
  for text in ('stop siege','stop\nsiege','powershell Get-Process','timer siege Monday 0','timer siege Monday 30 rumble',''):
   with self.subTest(text=text),self.assertRaises(ValueError):parse_dm(text)
 def test_rosters_can_fit_bounded_large_dm(self):
  text='Food Island: @DrMund0\n'+'Toy Island: @Other\n'*150
  self.assertEqual(parse_dm(text)[0],'orders')
  future=RemoteInbox(OWNER).submit(OWNER,1,'orders','defense',text);self.assertFalse(future.done())

class EventAdIndependenceTests(unittest.TestCase):
 def test_rumble_and_siege_never_call_ad_preflight(self):
  from atcommander.event_runner import EventBattleRunner
  for mode in ('Rumble','Siege'):
   with self.subTest(mode=mode):
    events=[];client=Mock();client.init.return_value={'ad_boost_level':0};client.get_guild_siege_status.return_value={'guild_siege_status':{'phase':3}}
    client.extract_battle_data.return_value=None
    with patch('atcommander.event_runner.ATClient',return_value=client),patch('atcommander.event_runner.load_credentials'),patch('atcommander.event_runner.LifetimeTracker'),patch('atcommander.event_runner.load_profiles',return_value={'rumble':{'message':'fixture'}}),patch('atcommander.event_runner.ad_preflight',side_effect=AssertionError('Ads must not gate events'))as ads:
     runner=EventBattleRunner(mode,auto_watch_ads=True,event=events.append);runner._run()
    ads.assert_not_called();self.assertEqual(events[-1]['kind'],'finished')

@unittest.skipUnless(importlib.util.find_spec('discord'),'Optional Discord library missing')
class DmHandlerTests(unittest.IsolatedAsyncioTestCase):
 async def exercise(self,owner=True,guild=False,text='siege'):
  import discord
  from atcommander.remote_discord import DiscordConnection
  inbox=RemoteInbox(OWNER);connection=DiscordConnection(inbox,Mock());future=Future();future.set_result('Accepted')
  message=SimpleNamespace(guild=object()if guild else None,author=SimpleNamespace(id=OWNER if owner else OWNER+1,bot=False),id=99,content=text,channel=SimpleNamespace(send=AsyncMock()))
  async def fake_start(token):await connection.client.on_message(message)
  with patch('atcommander.cm_discord_bot._load_token',return_value='synthetic-token'),patch.object(discord.Client,'start',side_effect=fake_start),patch.object(inbox,'submit',return_value=future)as submit:
   await connection._run()
  return submit,message
 async def test_owner_dm_is_queued_once(self):
  submit,message=await self.exercise();submit.assert_called_once_with(OWNER,'dm:99','run','Siege','');message.channel.send.assert_awaited_once()
 async def test_other_people_cannot_command_the_bot(self):
  submit,message=await self.exercise(owner=False);submit.assert_not_called();message.channel.send.assert_not_awaited()
 async def test_server_chat_does_not_trigger_phone_actions(self):
  submit,message=await self.exercise(guild=True);submit.assert_not_called();message.channel.send.assert_not_awaited()
 async def test_full_post_is_queued_without_a_prefix(self):
  text='Food Island: @DrMund0';submit,_=await self.exercise(text=text);self.assertEqual(submit.call_args.args[-3:],('orders','defense',text))

class OwnerOrderUiTests(unittest.TestCase):
 def test_pasted_orders_persist_as_names_without_guild_access_or_raw_roster(self):
  import tempfile,tkinter as tk,time
  from pathlib import Path
  from atcommander.app import ATCommanderApp
  from atcommander.command_store import CommandStore
  root=tk.Tk();self.addCleanup(root.destroy)
  with tempfile.TemporaryDirectory()as temporary:
   store=CommandStore(Path(temporary)/'prefs.json')
   with patch.object(ATCommanderApp,'_refresh_account_metadata'),patch.object(ATCommanderApp,'_refresh_account_badge'),patch('atcommander.utility_commands.CommandStore',return_value=store),patch('atcommander.app.migrate_last_siege_browser_replay_metadata',return_value=False),patch('atcommander.app.migrate_last_native_replay_metadata',return_value=[]):app=ATCommanderApp(root)
   app.discord_owner.set(str(OWNER));app.guild_alias.set('DrMund0');connection=Mock();connection.orders_config={'enabled':True};app.discord_connection=connection
   app._orders_from_owner('defense','Food Island: @DrMund0, @OtherPrivateAccount')
   self.assertFalse(app._orders_ready())
   app._orders_from_owner('attack','Happy Siege Sunday! Going Family Guy and Art today')
   self.assertEqual(app.guild_plan['attack']['targets'],['Family Guy','Art']);self.assertEqual(app.guild_plan['defense']['island'],'Food')
   self.assertEqual(app.guild_config['source_mode'],'dm');self.assertFalse(app.guild_config['enabled']);self.assertFalse(connection.orders_config['enabled'])
   self.assertNotIn('OtherPrivateAccount',json.dumps(store.read()))
   with patch('atcommander.siege_orders_ui.time.time',return_value=time.time()+601):self.assertTrue(app._orders_ready())
   connection.fetch_orders.assert_not_called()
   self.assertEqual(store.read()['guild_owner_orders']['defense']['source'],'owner_dm')
   for callback in root.tk.call('after','info'):
    try:root.after_cancel(callback)
    except tk.TclError:pass
