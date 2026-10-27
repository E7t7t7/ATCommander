"""Synthetic worker failures and scheduler ticks; no browser/account operations."""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
from queue import Queue
import threading
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from atcommander.research_center import ResearchCenterMixin,RESEARCH_NETWORK_RETRY_DELAYS


class ReadUnavailable(RuntimeError):
    network_retryable=True
    def __init__(self,queue_replaced=False):
        super().__init__("The research read remains temporarily unavailable.")
        self.queue_replaced=queue_replaced


class Controller(ResearchCenterMixin):
    def __init__(self):
        self._closing=False;self.research_auto=False;self.research_next=0
        self.command_busy=False;self.command_mutating=False;self.research_busy=False;self.research_thread=None
        self._research_generation=0;self._research_frozen_launch=None;self._research_pending_retry=None
        self.command_stop=threading.Event();self.command_run_button=Mock();self.research_status=Mock()
        self.command_schema=SimpleNamespace(store=object());self.events=Queue();self.research_log=object()
        self.root=Mock();self._append=Mock();self._research_render_rows=Mock();self._research_cancel_render=Mock()
        self.options={"tiers":[4,5],"ads":False,"speedups":True,"max_speedup_hours":24,"reserve_hours":100}
        self._research_values=Mock(side_effect=lambda:self.options)


def answer(state="waiting"):
    return SimpleNamespace(message="Synthetic research "+state,data={"status":state},rows=[])


class ResearchNetworkUITests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.app=Controller();self.clock=100.;self.jobs=[]
        self.service=Mock();self.service.context.private=()
        self.stack.enter_context(patch("atcommander.research_center.time.monotonic",side_effect=lambda:self.clock))
        self.client=self.stack.enter_context(patch("atcommander.research_center.ATClient"))
        self.stack.enter_context(patch("atcommander.research_center.load_credentials",return_value=object()))
        self.stack.enter_context(patch("atcommander.research_center.build_service",return_value=self.service))
        self.thread=self.stack.enter_context(patch("atcommander.research_center.threading.Thread",side_effect=self.thread_factory))
        self.prompt=self.stack.enter_context(patch("atcommander.research_center.messagebox.showinfo"))

    def thread_factory(self,*,target,**_kwargs):
        return SimpleNamespace(start=lambda:self.jobs.append(target),is_alive=lambda:False)

    def perform_job(self):
        self.jobs.pop(0)()
        return self.app.events.get_nowait()

    def launch_error(self,*,resume=True,queue_replaced=False,preview=False,reconcile=False):
        self.service.execute.side_effect=ReadUnavailable(queue_replaced)
        self.assertTrue(self.app._research_launch(preview,resume=resume,reconcile=reconcile))
        event=self.perform_job();self.assertTrue(event["network_retryable"])
        self.app._research_event(event)
        return event

    def due(self):
        self.clock=self.app.research_next;self.app._research_tick()

    def test_new_queue_false_and_original_options_survive_failure_before_replacement(self):
        original=deepcopy(self.app.options);self.launch_error(resume=False)
        self.assertFalse(self.app._research_pending_retry["resume"])
        self.assertEqual(self.app.research_next,115.)
        self.app.options["tiers"].append(3);self.app.options["max_speedup_hours"]=99999
        self.app._research_values.side_effect=AssertionError("Retry must not re-read controls")
        self.due();self.perform_job()
        args=self.service.execute.call_args.args[1]
        self.assertFalse(args["resume"]);self.assertEqual(json.loads(args["options"]),original)
        self.assertEqual(self.app._research_values.call_count,1)

    def test_replacement_switches_to_resume_once_and_never_replaces_again(self):
        self.service.execute.side_effect=[ReadUnavailable(False),ReadUnavailable(True),ReadUnavailable(False),answer(),answer()]
        self.app._research_launch(False,resume=False)
        for _ in range(3):
            self.app._research_event(self.perform_job());self.due()
        self.app._research_event(self.perform_job())
        self.assertEqual([call.args[1]["resume"] for call in self.service.execute.call_args_list],[False,False,True,True])
        self.assertEqual(self.app._research_frozen_launch["retries"],0)
        self.due();self.app._research_event(self.perform_job())
        self.assertTrue(self.service.execute.call_args.args[1]["resume"])

    def test_normal_automatic_cycles_keep_frozen_budget_and_switch_new_queue_to_resume(self):
        original=deepcopy(self.app.options);self.service.execute.side_effect=[answer(),answer()]
        self.app._research_launch(False,resume=False);self.app._research_event(self.perform_job())
        self.app.options.update(max_speedup_hours=1000000,reserve_hours=0)
        self.app._research_values.side_effect=AssertionError("Automatic cycle must retain saved options")
        self.due();self.app._research_event(self.perform_job())
        calls=self.service.execute.call_args_list
        self.assertFalse(calls[0].args[1]["resume"]);self.assertTrue(calls[1].args[1]["resume"])
        self.assertEqual(json.loads(calls[1].args[1]["options"]),original)
        self.assertEqual(self.app._research_values.call_count,1)

    def test_at_most_three_delayed_retries_then_disarmed(self):
        self.service.execute.side_effect=ReadUnavailable()
        self.app._research_launch(False)
        for index,delay in enumerate(RESEARCH_NETWORK_RETRY_DELAYS,1):
            now=self.clock;self.app._research_event(self.perform_job())
            self.assertEqual(self.app.research_next,now+delay)
            self.assertEqual(self.app._research_pending_retry["retries"],index)
            self.due()
        self.app._research_event(self.perform_job())
        self.assertFalse(self.app.research_auto);self.assertIsNone(self.app._research_pending_retry)
        self.clock+=1000;self.app._research_tick()
        self.assertEqual(self.service.execute.call_count,4);self.assertFalse(self.jobs)
        self.assertIn("3 automatic read retries",self.app.research_status.set.call_args.args[0])

    def test_success_resets_consecutive_network_retry_budget(self):
        self.service.execute.side_effect=[ReadUnavailable(),answer(),ReadUnavailable()]
        self.app._research_launch(False);self.app._research_event(self.perform_job());self.due()
        self.app._research_event(self.perform_job());self.due()
        self.app._research_event(self.perform_job())
        self.assertEqual(self.app._research_pending_retry["retries"],1)
        self.assertEqual(self.app.research_next,self.clock+15)

    def test_preview_and_saved_action_never_retry_even_if_exception_is_tagged(self):
        for preview,reconcile,command in ((True,False,"research-plan"),(False,True,"research-reconcile")):
            self.app=Controller();self.service.execute.reset_mock()
            with self.subTest(command=command):
                self.launch_error(preview=preview,reconcile=reconcile)
                self.assertFalse(self.app.research_auto);self.assertIsNone(self.app._research_pending_retry)
                self.clock+=1000;self.app._research_tick();self.assertFalse(self.jobs)
                self.assertEqual(self.service.execute.call_args.args[0],command)
                if reconcile:self.app._research_values.assert_not_called()

    def test_plain_timeout_text_and_ambiguous_mutation_error_are_not_retried(self):
        for failure in (RuntimeError("ReadTimeout ConnectionError"),RuntimeError("The research action is uncertain and was not replayed.")):
            self.app=Controller();self.service.execute.side_effect=failure
            with self.subTest(failure=type(failure).__name__):
                self.app._research_launch(False);event=self.perform_job()
                self.assertFalse(event["network_retryable"]);self.app._research_event(event)
                self.assertFalse(self.app.research_auto);self.assertIsNone(self.app._research_pending_retry)

    def test_retry_requires_literal_true_flag_and_known_run_context(self):
        event=self.launch_error()
        for flag in (1,"true",False):
            self.app=Controller();self.app.research_auto=True
            copied={**event,"network_retryable":flag,"launch":{**event["launch"],"generation":0}}
            self.app._research_event(copied)
            self.assertFalse(self.app.research_auto);self.assertIsNone(self.app._research_pending_retry)
        self.app=Controller();self.app.research_auto=True
        self.app._research_event({"kind":"research_result","error":"Synthetic read error","network_retryable":True})
        self.assertFalse(self.app.research_auto);self.assertIsNone(self.app._research_pending_retry)

    def test_pause_cancels_backoff_and_frozen_callback_cannot_restart_it(self):
        self.launch_error(resume=False);stale=dict(self.app._research_pending_retry)
        self.app._research_pause();self.assertFalse(self.app.research_auto)
        self.assertIsNone(self.app._research_pending_retry)
        self.clock+=1000;self.app._research_tick()
        self.assertFalse(self.app._research_launch(False,_frozen=stale))
        self.assertFalse(self.jobs);self.assertEqual(self.service.execute.call_count,1)
        self.app.root.after.assert_not_called()

    def test_pause_before_error_event_arrives_cannot_rearm_retries(self):
        self.service.execute.side_effect=ReadUnavailable();self.app._research_launch(False)
        event=self.perform_job();self.app._research_pause();self.app._research_event(event)
        self.assertFalse(self.app.research_auto);self.assertIsNone(self.app._research_pending_retry)

    def test_stop_before_worker_starts_avoids_client_and_service_requests(self):
        self.app._research_launch(False,resume=False);self.app._research_pause()
        event=self.perform_job();self.assertTrue(event["cancelled"])
        self.app._research_event(event)
        self.client.assert_not_called();self.service.execute.assert_not_called();self.assertFalse(self.app.research_auto)

    def test_stop_all_signal_or_close_prevents_due_retry(self):
        for closing in (False,True):
            self.app=Controller();self.launch_error()
            if closing:self.app._closing=True
            else:self.app.command_stop.set()
            with self.subTest(closing=closing):self.due()
            self.assertFalse(self.jobs)

    def test_overlap_defers_retry_quietly_without_changing_queue_intent(self):
        self.launch_error(resume=False);expected=deepcopy(self.app._research_pending_retry)
        self.app.ad_thread=SimpleNamespace(is_alive=lambda:True)
        self.due();self.assertFalse(self.jobs);self.assertEqual(self.app._research_pending_retry,expected)
        self.assertEqual(self.app.research_next,self.clock+5);self.prompt.assert_not_called()

    def test_manual_preview_supersedes_retry_and_stale_result_cannot_release_new_worker(self):
        old=self.launch_error()
        self.service.execute.side_effect=None;self.service.execute.return_value=answer("preview")
        self.app._research_launch(True)
        self.assertTrue(self.app.research_busy);self.assertFalse(self.app.research_auto)
        self.app._research_event(old)
        self.assertTrue(self.app.research_busy);self.assertFalse(self.app.research_auto)
        self.app._research_event(self.perform_job());self.assertFalse(self.app.research_busy)
        self.assertIsNone(self.app._research_pending_retry)

    def test_research_client_timeout_is_45_and_separate_manual_run_can_select_new_options(self):
        self.service.execute.return_value=answer("complete")
        self.app._research_launch(False);self.app._research_event(self.perform_job())
        self.assertEqual(self.client.call_args.kwargs,{"timeout":45})
        self.app.options["max_speedup_hours"]=72
        self.app._research_launch(False,resume=False);self.app._research_event(self.perform_job())
        self.assertEqual(json.loads(self.service.execute.call_args.args[1]["options"])["max_speedup_hours"],72)
        self.assertEqual(self.app._research_values.call_count,2)

    def test_real_engine_saved_new_queue_read_failure_resumes_without_repeating_start_or_resetting_budget(self):
        from atcommander.api import ATNetworkError
        from atcommander.research_automation import ResearchAutomation,ResearchOptions
        from test_research_automation import FakeService
        game=FakeService();game.stop=self.app.command_stop
        self.app.options.update(max_speedup_hours=2,reserve_hours=0,max_pairs=1)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"synthetic-research.json";fail_reads=[True];original=game.call
            def call(name,args=None):
                if name=="getUserAccount" and fail_reads[0]:raise ATNetworkError("ReadTimeout")
                return original(name,args)
            def execute(name,args):
                self.assertEqual(name,"research-run")
                return ResearchAutomation(game,path,waiter=lambda _delay:False).run(
                    ResearchOptions.from_dict(json.loads(args["options"])),resume=args["resume"],wait=False,max_actions=3)
            self.service.execute.side_effect=execute
            with patch.object(game,"call",side_effect=call):
                self.app._research_launch(False,resume=False);failure=self.perform_job()
                self.assertTrue(failure["network_retryable"]);self.assertTrue(failure["queue_replaced"])
                saved=json.loads(path.read_text())["research"]
                self.assertEqual(saved["pending_action"]["kind"],"start")
                self.app._research_event(failure)
                self.assertTrue(self.app._research_pending_retry["resume"])
                self.app.options["max_speedup_hours"]=1000000
                self.app._research_values.side_effect=AssertionError("Retry must preserve the authorized budget")
                fail_reads[0]=False;self.due();self.app._research_event(self.perform_job())
            saved=json.loads(path.read_text())["research"]
        self.assertEqual(saved["options"]["max_speedup_hours"],2)
        self.assertEqual(saved["spent_seconds"],3600);self.assertEqual(saved["confirmed_speedup_seconds"],3600)
        self.assertIsNone(saved["pending_action"]);self.assertEqual(saved["completed"],1)
        self.assertEqual([name for name,_args in game.mutations()].count("researchCombo"),1)
        self.assertEqual([name for name,_args in game.mutations()].count("useResearchSpeedUpItem"),1)


if __name__=="__main__":unittest.main()
