"""Read retries and outcome checks never retransmit a research mutation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from atcommander.api import ATNetworkError
from atcommander.local_commands import CommandUnavailable
from atcommander.research_automation import ResearchAutomation, ResearchOptions, ResearchReadUnavailable
from test_research_automation import FakeService


class ResearchNetworkTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/"queue.json"
        self.service=FakeService();self.waits=[];self.messages=[]
        self.service.emit=self.messages.append
        self.options=ResearchOptions(ads=False,speedups=True,max_speedup_hours=2,max_pairs=1)

    def engine(self,**kwargs):
        return ResearchAutomation(self.service,self.path,waiter=lambda delay:self.waits.append(delay) or False,**kwargs)

    def saved(self):
        return json.loads(self.path.read_text())["research"]

    def test_initial_read_recovers_without_resetting_the_queue_or_spending(self):
        snapshot=self.service.snapshot
        with patch.object(self.service,"snapshot",side_effect=[ATNetworkError("ReadTimeout"),snapshot()]) as read:
            answer=self.engine().run(self.options,resume=False,wait=False,max_actions=0)
        self.assertEqual(read.call_count,2);self.assertEqual(self.waits,[5])
        self.assertEqual(answer.data["status"],"waiting")
        self.assertEqual(self.service.mutations(),[])
        self.assertEqual(self.saved()["spent_seconds"],0)

    def test_exhausted_initial_reads_are_structured_and_leave_existing_ledger_unchanged(self):
        self.engine().run(self.options,wait=False,max_actions=0)
        previous=self.path.read_bytes()
        with patch.object(self.service,"snapshot",side_effect=ATNetworkError("ConnectionError")) as read:
            with self.assertRaises(ResearchReadUnavailable) as caught:
                self.engine().run(self.options,resume=False,wait=False)
        self.assertTrue(caught.exception.network_retryable)
        self.assertFalse(caught.exception.queue_replaced)
        self.assertEqual(read.call_count,3);self.assertEqual(self.waits,[5,15])
        self.assertEqual(self.path.read_bytes(),previous)
        self.assertEqual(self.service.mutations(),[])

    def test_catalog_transport_failure_retries_only_that_read(self):
        catalog=self.service.catalog;calls=[]
        def flaky(name):
            calls.append(name)
            if name=="combo" and calls.count("combo")==1:raise ATNetworkError("ReadTimeout")
            return catalog(name)
        with patch.object(self.service,"catalog",side_effect=flaky):
            result=self.engine().preview(self.options)
        self.assertEqual(result.data["status"],"preview")
        self.assertEqual(calls.count("combo"),2);self.assertEqual(self.waits,[5])
        self.assertEqual(self.service.mutations(),[]);self.assertFalse(self.path.exists())

    def test_post_start_read_exhaustion_preserves_request_and_marks_new_queue_saved(self):
        original=self.service.call
        def failing_reads(name,args=None):
            if name=="getUserAccount":
                self.service.calls.append((name,{}));raise ATNetworkError("ReadTimeout")
            return original(name,args)
        with patch.object(self.service,"call",side_effect=failing_reads):
            with self.assertRaises(ResearchReadUnavailable) as caught:
                self.engine().run(self.options,resume=False,wait=False)
        self.assertTrue(caught.exception.queue_replaced)
        self.assertEqual(self.saved()["pending_action"]["kind"],"start")
        self.assertEqual([name for name,_ in self.service.mutations()],["researchCombo"])
        result=self.engine().run(self.options,resume=True,wait=False,max_actions=6)
        self.assertEqual(result.data["completed"],1)
        self.assertEqual([name for name,_ in self.service.mutations()].count("researchCombo"),1)

    def test_applied_start_with_lost_reply_is_proved_from_fresh_state_without_retransmission(self):
        original=self.service.call
        def lost_reply(name,args=None):
            result=original(name,args)
            if name=="researchCombo":raise ATNetworkError("ConnectionError")
            return result
        with patch.object(self.service,"call",side_effect=lost_reply):
            result=self.engine().run(self.options,wait=False)
        self.assertEqual(result.data["status"],"waiting");self.assertEqual(result.data["started"],1)
        self.assertIsNone(self.saved()["pending_action"])
        self.assertEqual([name for name,_ in self.service.mutations()],["researchCombo"])
        self.assertEqual(self.engine().run(self.options,wait=False).data["completed"],1)
        self.assertEqual([name for name,_ in self.service.mutations()].count("researchCombo"),1)

    def test_unapplied_start_timeout_remains_uncertain_and_is_never_rescheduled_as_a_read(self):
        original=self.service.call
        def unapplied(name,args=None):
            if name=="researchCombo":
                self.service.calls.append((name,args));raise ATNetworkError("ReadTimeout")
            return original(name,args)
        with patch.object(self.service,"call",side_effect=unapplied):
            with self.assertRaises(CommandUnavailable) as caught:self.engine().run(self.options,wait=False)
        self.assertFalse(getattr(caught.exception,"network_retryable",False))
        self.assertEqual(self.saved()["pending_action"]["kind"],"start")
        with self.assertRaises(CommandUnavailable):self.engine().run(self.options,wait=False)
        self.assertEqual([name for name,_ in self.service.mutations()],["researchCombo"])

    def test_applied_speedup_timeout_keeps_exact_budget_and_confirms_only_once(self):
        original=self.service.call
        def lost_reply(name,args=None):
            result=original(name,args)
            if name=="useResearchSpeedUpItem":raise ATNetworkError("ReadTimeout")
            return result
        with patch.object(self.service,"call",side_effect=lost_reply):
            result=self.engine().run(self.options,wait=False)
        self.assertEqual(result.data["speedups_used_hours"],1)
        self.assertEqual(self.saved()["spent_seconds"],3600)
        self.assertEqual(self.saved()["confirmed_speedup_seconds"],3600)
        self.assertIsNone(self.saved()["pending_action"])
        self.engine().run(self.options,wait=False)
        self.assertEqual([name for name,_ in self.service.mutations()].count("useResearchSpeedUpItem"),1)

    def test_applied_collection_timeout_counts_completed_pair_once(self):
        original=self.service.call
        def lost_reply(name,args=None):
            result=original(name,args)
            if name=="completeResearch":raise ATNetworkError("ConnectionError")
            return result
        with patch.object(self.service,"call",side_effect=lost_reply):
            result=self.engine().run(self.options,wait=False)
        self.assertEqual(result.data["completed"],1)
        self.assertEqual(self.engine().run(self.options,wait=False).data["completed"],1)
        self.assertEqual([name for name,_ in self.service.mutations()].count("completeResearch"),1)

    def test_stop_during_backoff_prevents_another_read_or_action(self):
        engine=self.engine()
        def stop(delay):
            self.waits.append(delay);self.service.stop.set();return True
        engine.waiter=stop
        with patch.object(self.service,"snapshot",side_effect=ATNetworkError("ReadTimeout")) as read:
            answer=engine.run(self.options,wait=False)
        self.assertEqual(answer.data["status"],"paused")
        self.assertEqual(read.call_count,1);self.assertEqual(self.waits,[5])
        self.assertEqual(self.service.mutations(),[]);self.assertFalse(self.path.exists())

    def test_permanent_transport_failure_and_explicit_rejection_do_not_retry(self):
        with patch.object(self.service,"snapshot",side_effect=ATNetworkError("SSLError",retryable_read=False)) as read:
            with self.assertRaises(ATNetworkError):self.engine().run(self.options,wait=False)
        self.assertEqual(read.call_count,1);self.assertEqual(self.waits,[])
        with patch.object(self.service,"snapshot",return_value={"result":False}) as read:
            with self.assertRaises(CommandUnavailable):self.engine().run(self.options,wait=False)
        self.assertEqual(read.call_count,1);self.assertEqual(self.waits,[])

    def test_boost_callback_failure_is_not_treated_as_a_replayable_read(self):
        callback=Mock(side_effect=ATNetworkError("ReadTimeout"))
        with self.assertRaises(ATNetworkError) as caught:
            self.engine(boost_callback=callback).run(ResearchOptions(ads=True),wait=False)
        self.assertFalse(getattr(caught.exception,"network_retryable",False))
        self.assertEqual(callback.call_count,1);self.assertEqual(self.waits,[])
        self.assertEqual(self.service.mutations(),[])

    def test_verified_already_active_boost_does_not_download_account_again(self):
        self.service.live.setdefault("user_data",{})["boost_level"]=3
        callback=Mock(return_value={"ok":True,"boost_level":3,"attempts":0})
        self.engine(boost_callback=callback).run(ResearchOptions(ads=True,max_pairs=1),wait=False,max_actions=0)
        self.assertEqual(callback.call_count,1)
        self.assertFalse(any(name=="getUserAccount" for name,_ in self.service.calls))
        self.assertEqual(self.service.mutations(),[])

    def test_zero_attempts_does_not_skip_refresh_without_fresh_active_boost(self):
        self.service.live.setdefault("user_data",{})["boost_level"]=0
        self.engine(boost_callback=lambda *_:{"ok":True,"boost_level":3,"attempts":0}).run(
            ResearchOptions(ads=True,max_pairs=1),wait=False,max_actions=0)
        self.assertEqual([name for name,_ in self.service.calls].count("getUserAccount"),1)
        self.assertEqual(self.service.mutations(),[])

    def test_ad_playback_error_preserves_pending_action_without_replaying(self):
        self.service._start({"card1":22,"card2":23,"slot_index":3})
        self.service.live["research_ad_speed_ups"][0]["next_speed_up"]=45
        callback=Mock(side_effect=ATNetworkError("ReadTimeout"))
        callback.supported_phases={"speedup"}
        with self.assertRaises(CommandUnavailable) as caught:
            self.engine(boost_callback=lambda *_:{"ok":True,"boost_level":3},ad_callback=callback).run(
                ResearchOptions(ads=True,max_pairs=2),wait=False)
        self.assertFalse(getattr(caught.exception,"network_retryable",False))
        self.assertEqual(callback.call_count,1);self.assertEqual(self.saved()["pending_action"]["kind"],"ad")
        self.assertEqual(self.service.mutations(),[])


if __name__=="__main__":unittest.main()
