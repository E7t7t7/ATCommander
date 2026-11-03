import tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from atcommander.guild_orders import attack_order,defense_order,parse_orders,resolve_orders
from atcommander.guild_defense import apply_assigned_defense,membership_slot
from atcommander.event_defaults import validated_defaults,initial_defaults
from atcommander.command_store import CommandStore

SAMPLE=''' :show_fg: Family Guy Island: @OtherOne, @OtherTwo
:show_kh: King of the Hill Island: @OtherThree
:trait_toy: Toy Island: @Someone
:trait_food: Food Island: @DrMund0, @SomeoneElse
:trait_vehicle: Vehicle Island: @Another'''
ANNOUNCEMENT='@GBU2 Members Happy Siege Sunday Gang 🤠 Going Family Guy and Art Today 💪'

def message(text,at=1000000,bot=False,name='Leader',uid='123'):
 return {'text':text,'at':at,'bot':bot,'author_name':name,'author_id':uid,'url':'https://discord.com/channels/520905719516626945/1254172326413471834/1'}
def map_status(phase=1,assigned=False):
 return {'guild_siege_status':{'phase':phase,'enemy_locations':[{'slot_id':'3','hp':100,'data':{'name':'Art'}},{'slot_id':'5','hp':100,'data':{'name':'Family Guy'}}],
 'locations':[{'slot_id':'2','hp':100,'data':{'name':'Food'},'users':[{'user_id':'private-account'}]if assigned else[]}]}}

def plan(now=1000000):return parse_orders([message(ANNOUNCEMENT,now)],[message(SAMPLE,now,True,'Calculon')],owner_id='999',aliases=['DrMund0'],now=now)

class GuildParserTests(unittest.TestCase):
 def test_user_attack_example_preserves_priority(self):self.assertEqual(attack_order(ANNOUNCEMENT),['Family Guy','Art'])
 def test_user_calculon_example_finds_food_only(self):self.assertEqual(defense_order(SAMPLE,'999',['DrMund0']),'Food')
 def test_owner_discord_mention_beats_display_name(self):self.assertEqual(defense_order('Food Island: <@!999>, <@123>','999',[]),'Food')
 def test_does_not_match_other_similar_account_name(self):
  with self.assertRaises(ValueError):defense_order('Food Island: @DrMund00','999',['DrMund0'])
 def test_conditional_negative_and_questions_require_review(self):
  for text in ('Siege do not attack Family Guy; hit Art','Siege Going Art?','Siege attack Art unless Food falls'):
   with self.subTest(text=text),self.assertRaises(ValueError):attack_order(text)
 def test_conflicting_assignment_is_rejected(self):
  with self.assertRaises(ValueError):defense_order('Food Island: @DrMund0\nToy Island: @DrMund0','999',['DrMund0'])
 def test_old_attack_is_not_reused(self):self.assertIn('error',parse_orders([message(ANNOUNCEMENT,1)],[],owner_id='999',now=1000000)['attack'])
 def test_new_ambiguous_post_blocks_old_clear_post(self):
  result=parse_orders([message(ANNOUNCEMENT,999000),message('Siege update: do not attack Art',999999)],[],owner_id='999',now=1000000)
  self.assertIn('error',result['attack'])
 def test_human_cannot_impersonate_calculon(self):self.assertIn('error',parse_orders([],[message(SAMPLE,name='Calculon')],owner_id='999',aliases=['DrMund0'],now=1000000)['defense'])
 def test_leader_filter_ignores_other_people(self):self.assertIn('error',parse_orders([message(ANNOUNCEMENT)],[],owner_id='999',leader_ids=['456'],now=1000000)['attack'])
 def test_defense_can_arrive_five_days_before_attack(self):
  result=parse_orders([],[message(SAMPLE,1000000-5*86400,True,'Calculon')],owner_id='999',aliases=['DrMund0'],now=1000000)
  self.assertEqual(result['defense']['island'],'Food');self.assertIn('error',result['attack'])
  self.assertEqual(resolve_orders(result,map_status(),defense=True,now=1000000)['island_id'],'2')
 def test_current_map_resolves_names_not_hardcoded_slots(self):
  result=resolve_orders(plan(),map_status(),attack=True,defense=True,now=1000000)
  self.assertEqual(result,{'targets':['5','3'],'island_id':'2'})
 def test_missing_or_duplicate_map_islands_block_auto_use(self):
  live=map_status();live['guild_siege_status']['enemy_locations'].append({'slot_id':'6','data':{'name':'Art'}})
  with self.assertRaises(ValueError):resolve_orders(plan(),live,attack=True,now=1000000)
 def test_conflicting_calculon_batch_blocks_assignment(self):
  result=parse_orders([],[message(SAMPLE,1000000,True,'Calculon'),message('Toy Island: @DrMund0',999999,True,'Calculon')],owner_id='999',aliases=['DrMund0'],now=1000000)
  self.assertIn('error',result['defense'])
 def test_split_calculon_messages_support_owner_in_earlier_chunk(self):
  result=parse_orders([],[message(SAMPLE,999995,True,'Calculon'),message('Art Island: @Other',1000000,True,'Calculon')],owner_id='999',aliases=['DrMund0'],now=1000000)
  self.assertEqual(result['defense']['island'],'Food')

class DefaultsTests(unittest.TestCase):
 def snapshot(self):return {'user_decks':{'20':{'deck_id':20,'name':'ArcherFood'},'24':{'deck_id':24,'name':'Disguised Combo'}}}
 def test_saved_named_deck_follows_correct_server_id(self):self.assertEqual(validated_defaults({'offense_deck_id':'24','offense_deck_name':'ArcherFood'},self.snapshot())['offense_deck_id'],'20')
 def test_renamed_or_missing_saved_deck_does_not_silently_run(self):
  with self.assertRaises(ValueError):validated_defaults({'offense_deck_id':'24','offense_deck_name':'Missing'},self.snapshot())
 def test_events_settings_are_snapshotted(self):
  settings={'siege_offense_deck_id':'20'};value=initial_defaults(settings,'Siege');settings['siege_offense_deck_id']='24'
  self.assertEqual(value['offense_deck_id'],'20')

class DefenseApplyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=CommandStore(Path(self.tmp.name)/'state.json');self.client=Mock();self.client.credentials=SimpleNamespace(user_id='private-account');self.client.set_siege_defense_island.return_value={'result':True};self.plan=plan(time.time())
 def test_early_defense_applied_once_and_confirmed(self):
  self.client.get_guild_siege_status.side_effect=[map_status(),map_status(assigned=True)]
  self.assertIn('verified',apply_assigned_defense(self.client,self.store,self.plan));self.client.set_siege_defense_island.assert_called_once_with('2');self.assertIsNone(self.store.read()['guild_defense_pending'])
 def test_battle_phase_never_changes_defense(self):
  self.client.get_guild_siege_status.return_value=map_status(phase=2)
  self.assertIn('outside',apply_assigned_defense(self.client,self.store,self.plan));self.client.set_siege_defense_island.assert_not_called()
 def test_already_assigned_does_not_resend(self):
  self.client.get_guild_siege_status.return_value=map_status(assigned=True)
  self.assertIn('already',apply_assigned_defense(self.client,self.store,self.plan));self.client.set_siege_defense_island.assert_not_called()
 def test_network_loss_keeps_pending_and_no_replay(self):
  self.client.get_guild_siege_status.return_value=map_status();self.client.set_siege_defense_island.side_effect=TimeoutError()
  with self.assertRaises(TimeoutError):apply_assigned_defense(self.client,self.store,self.plan)
  with self.assertRaises(ValueError):apply_assigned_defense(self.client,self.store,self.plan)
  self.client.set_siege_defense_island.assert_called_once()
 def test_pending_can_reconcile_from_membership_without_resend(self):
  self.client.get_guild_siege_status.return_value=map_status();self.client.set_siege_defense_island.side_effect=TimeoutError()
  with self.assertRaises(TimeoutError):apply_assigned_defense(self.client,self.store,self.plan)
  self.client.get_guild_siege_status.return_value=map_status(assigned=True)
  self.assertIn('already',apply_assigned_defense(self.client,self.store,self.plan));self.client.set_siege_defense_island.assert_called_once()

class LoadoutVerificationTests(unittest.TestCase):
 def test_scheduled_hero_and_active_deck_are_verified(self):
  from atcommander.event_defaults import verify_loadout
  data={'user_data':{'active_deck':20},'user_decks':{'20':{'deck_id':20,'name':'ArcherFood','units':[],'commander':{'unit_id':1001}}}}
  verify_loadout({'offense_deck_id':'20','offense_hero_id':'1001'},data)
  with self.assertRaises(ValueError):verify_loadout({'offense_deck_id':'24'},data)
  with self.assertRaises(ValueError):verify_loadout({'offense_hero_id':'1002'},data)

class DefaultsUiTests(unittest.TestCase):
 def test_defaults_are_saved_and_used_without_replacing_events_selections(self):
  import tkinter as tk
  from unittest.mock import patch
  from atcommander.app import ATCommanderApp
  root=tk.Tk();self.addCleanup(root.destroy)
  with tempfile.TemporaryDirectory()as temporary:
   store=CommandStore(Path(temporary)/'prefs.json')
   with patch.object(ATCommanderApp,'_refresh_account_metadata'),patch.object(ATCommanderApp,'_refresh_account_badge'),patch('atcommander.utility_commands.CommandStore',return_value=store),patch('atcommander.app.migrate_last_siege_browser_replay_metadata',return_value=False),patch('atcommander.app.migrate_last_native_replay_metadata',return_value=[]):app=ATCommanderApp(root)
   app._apply_account_selectors([{'id':'20','name':'ArcherFood'},{'id':'24','name':'Disguised Combo'}],[{'id':'1001','name':'Peter'}])
   app.default_vars['Siege']['offense_deck'].set('ArcherFood  [ID 20]');app.default_vars['Siege']['offense_hero'].set('Peter  [ID 1001]')
   app.siege_offense_deck.set('Disguised Combo  [ID 24]');app._defaults_save('Siege')
   self.assertEqual(store.read()['scheduled_event_defaults']['Siege']['offense_deck_id'],'20')
   self.assertEqual(app.siege_offense_deck.get(),'Disguised Combo  [ID 24]')
   with patch('atcommander.remote_center.CommandCenterMixin._browser_action_busy',return_value=False),patch.object(app,'start_event',return_value=True)as start:
    app._remote_begin_event('Siege')
   self.assertEqual(start.call_args.kwargs['defaults']['offense_hero_id'],'1001')
   for callback in root.tk.call('after','info'):
    try:root.after_cancel(callback)
    except tk.TclError:pass

class GuildChannelReaderTests(unittest.IsolatedAsyncioTestCase):
 async def test_only_configured_channels_and_independent_defense_read(self):
  import asyncio
  from datetime import datetime,timezone
  from atcommander.remote_discord import DiscordConnection
  from atcommander.remote_control import RemoteInbox
  from atcommander.guild_orders import GUILD_ID,ATTACK_CHANNEL,DEFENSE_CHANNEL
  def channel(cid):
   if cid==ATTACK_CHANNEL:raise PermissionError()
   async def records():
    yield SimpleNamespace(content='',embeds=[SimpleNamespace(title='Assignments',description='',fields=[SimpleNamespace(name='Food Island',value='<@999>')])],created_at=datetime.now(timezone.utc),author=SimpleNamespace(id=2,name='Calculon',bot=True),jump_url='https://discord.com/channels/520905719516626945/559035124012941312/1')
   return SimpleNamespace(guild=SimpleNamespace(id=GUILD_ID),history=Mock(side_effect=lambda **kwargs:records()))
  connection=DiscordConnection(__import__('atcommander.remote_control',fromlist=['RemoteInbox']).RemoteInbox(123456789012345678),Mock(),{'enabled':True})
  connection.loop=asyncio.get_running_loop();connection.ready=True;connection.client=SimpleNamespace(get_channel=Mock(side_effect=channel))
  result=await asyncio.wrap_future(connection.fetch_orders())
  self.assertTrue(result['attack_error']);self.assertEqual(len(result['defense']),1)
  self.assertIn('Food Island:',result['defense'][0]['text'])
  self.assertEqual([c.args[0]for c in connection.client.get_channel.call_args_list],[ATTACK_CHANNEL,DEFENSE_CHANNEL])
