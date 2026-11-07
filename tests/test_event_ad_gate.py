import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from atcommander.command_center import CommandCenterMixin,event_only_steps,SchemaClient
from atcommander.utility_commands import build_service

class EventAdGateTests(unittest.TestCase):
 def app(self):
  ad=Mock();ad.is_alive.return_value=True
  return SimpleNamespace(_closing=False,learn_session=None,ad_thread=ad,event_ignored_ad_thread=ad,command_busy=False,command_mutating=False)
 def test_identified_watch_ads_does_not_block_events(self):
  app=self.app();self.assertTrue(CommandCenterMixin._browser_action_busy(app));self.assertFalse(CommandCenterMixin._browser_action_busy(app,ignore_ads=True))
  self.assertFalse(CommandCenterMixin._command_blocks_runner(app,ignore_ads=True))
 def test_calibration_or_other_ad_thread_work_remains_exclusive(self):
  app=self.app();app.event_ignored_ad_thread=object()
  self.assertTrue(CommandCenterMixin._browser_action_busy(app,ignore_ads=True))
 def test_user_ad_command_is_ignored_only_for_events(self):
  app=self.app();app.ad_thread=None;app.command_busy=True;app.command_mutating=True;app.command_ad_only=True
  self.assertFalse(CommandCenterMixin._browser_action_busy(app,ignore_ads=True));self.assertTrue(CommandCenterMixin._browser_action_busy(app))
  self.assertFalse(CommandCenterMixin._command_blocks_runner(app,ignore_ads=True))
 def test_other_commands_and_deck_edits_remain_blocking(self):
  app=self.app();app.command_busy=True;app.command_mutating=True;app.command_ad_only=False
  self.assertTrue(CommandCenterMixin._browser_action_busy(app,ignore_ads=True))
  app.command_busy=False;app.deck_builder_thread=Mock();app.deck_builder_thread.is_alive.return_value=True
  self.assertTrue(CommandCenterMixin._browser_action_busy(app,ignore_ads=True))
 def test_only_rumble_and_siege_play_batches_get_exemption(self):
  schema=build_service(SchemaClient())
  self.assertTrue(event_only_steps([('play',{'mode':'siege'}),('play',{'mode':'rumble'})],schema))
  self.assertFalse(event_only_steps([('play',{'mode':'arena'})],schema));self.assertFalse(event_only_steps([('play',{'mode':'siege'}),('deck-list',{})],schema))
