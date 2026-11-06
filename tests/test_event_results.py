import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from atcommander.event_results import event_results,notification_chunks
from atcommander.remote_center import RemoteCenterMixin

class EventResultsTests(unittest.TestCase):
 def test_opponents_and_run_totals(self):
  text=event_results('Rumble',[{'opponent':'X','won':True},{'opponent':'Y','won':False}])
  self.assertEqual(text,'Rumble completed\nVs X Win\nVs Y Loss\nTotal 1 wins 1 losses')
 def test_unknown_outcome_not_counted_as_loss(self):
  self.assertIn('Total 0 wins 0 losses · 1 unconfirmed',event_results('Siege',[{'opponent':'X','won':None}],failed=True))
 def test_long_result_preserves_every_line_and_final_total(self):
  text=event_results('Rumble',[{'opponent':'Opponent '+str(i)+'x'*70,'won':True}for i in range(100)])
  chunks=notification_chunks(text)
  self.assertTrue(all(len(c)<=1800 for c in chunks));self.assertEqual('\n'.join(chunks),text);self.assertTrue(chunks[-1].endswith('Total 100 wins 0 losses'))
 def test_observer_collects_actual_event_results_and_partial_error(self):
  app=SimpleNamespace(remote_job={'mode':'Siege','scheduled':True,'battles':0,'results':[]},event_timers=Mock(),_timers_render=Mock(),_remote_notify=Mock())
  for event in [{'kind':'battle_start','opponent':'X'},{'kind':'battle_end','opponent':'X','won':True},{'kind':'battle_start','opponent':'Y'},{'kind':'fatal'}]:RemoteCenterMixin._remote_observe(app,{'mode':'Siege',**event})
  message=app._remote_notify.call_args.args[0]
  self.assertIn('Vs X Win',message);self.assertIn('Vs Y Unconfirmed',message);self.assertIn('Total 1 wins 0 losses · 1 unconfirmed',message);self.assertIsNone(app.remote_job)
