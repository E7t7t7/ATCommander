"""Offline Stop tests for the legacy browser-scoped calibration replay."""
from contextlib import ExitStack
import threading
import unittest
from unittest.mock import Mock,patch

from atcommander import ad_ui,ad_browser_focus


class LegacyAdStopTests(unittest.TestCase):
    def replay_fixture(self,stack):
        prep={"ok":True,"target":{"webSocketDebuggerUrl":"fixture"}}
        prepare=stack.enter_context(patch.object(ad_ui,"prepare_game_ui",return_value=prep))
        focus=stack.enter_context(patch.object(ad_browser_focus,"ensure_game_input_focus"))
        evaluate=stack.enter_context(patch.object(ad_ui,"_eval",return_value={"innerWidth":100,"innerHeight":100}))
        click=stack.enter_context(patch.object(ad_ui,"_dispatch_click"))
        browser=stack.enter_context(patch.object(ad_ui,"LearningBrowser"))
        return prep,prepare,focus,evaluate,click,browser

    def test_stop_before_replay_has_no_browser_or_input_actions(self):
        with ExitStack() as stack:
            _,prepare,focus,evaluate,click,_=self.replay_fixture(stack)
            self.assertFalse(ad_ui._replay_sequence([{"delay":45}],Mock(),should_stop=lambda:True))
        prepare.assert_not_called();focus.assert_not_called();evaluate.assert_not_called();click.assert_not_called()

    def test_stop_after_preparation_or_focus_prevents_pointer_input(self):
        for boundary in ("preparation","focus"):
            stop=threading.Event()
            with self.subTest(boundary=boundary),ExitStack() as stack:
                prep,prepare,focus,evaluate,click,_=self.replay_fixture(stack)
                if boundary=="preparation":prepare.side_effect=lambda *_args,**_kwargs:(stop.set(),prep)[1]
                else:focus.side_effect=lambda *_args:stop.set()
                self.assertFalse(ad_ui._replay_sequence([{"delay":.6}],Mock(),should_stop=stop.is_set))
            evaluate.assert_not_called();click.assert_not_called()
            if boundary=="preparation":focus.assert_not_called()

    def test_focus_stop_exception_is_not_retried(self):
        with ExitStack() as stack:
            _,prepare,focus,_,click,_=self.replay_fixture(stack)
            focus.side_effect=ad_browser_focus.GameInputStopped("Fixture stopped")
            self.assertFalse(ad_ui._replay_sequence([{"delay":.6}],Mock(),should_stop=lambda:False))
        prepare.assert_called_once();focus.assert_called_once();click.assert_not_called()

    def test_stop_during_full_ad_wait_prevents_close_and_cleanup_inputs(self):
        stop=threading.Event();elapsed=[0.0];sleeps=[]
        def wait(delay):
            sleeps.append(delay);elapsed[0]+=delay
            if elapsed[0]>=1:stop.set()
        with ExitStack() as stack:
            prep,prepare,_,_,click,browser=self.replay_fixture(stack)
            stack.enter_context(patch.object(ad_ui.time,"sleep",side_effect=wait))
            result=ad_ui._replay_sequence([{"x":.1,"y":.2},{"delay":45,"x":.3,"y":.4}],Mock(),should_stop=stop.is_set)
        self.assertFalse(result);self.assertEqual(click.call_count,1)
        self.assertTrue(all(delay<=.25 for delay in sleeps));self.assertLess(elapsed[0],2)
        self.assertTrue(prep["_commander_should_stop"]());prepare.assert_called_once()
        browser.return_value._close_extra_tabs.assert_not_called()

    def test_stop_after_geometry_read_prevents_the_next_click(self):
        stop=threading.Event()
        with ExitStack() as stack:
            _,_,_,evaluate,click,_=self.replay_fixture(stack)
            def read(_ws,script):
                if script==ad_ui._CANVAS_JS:stop.set()
                return {"innerWidth":100,"innerHeight":100}
            evaluate.side_effect=read
            stack.enter_context(patch.object(ad_ui.time,"sleep",return_value=None))
            self.assertFalse(ad_ui._replay_sequence([{"x":.1,"y":.2}],Mock(),should_stop=stop.is_set))
        click.assert_not_called()

    def test_interruptible_wait_preserves_complete_calibrated_duration(self):
        sleeps=[]
        with ExitStack() as stack:
            _,_,_,_,click,browser=self.replay_fixture(stack)
            stack.enter_context(patch.object(ad_ui.time,"sleep",side_effect=sleeps.append))
            self.assertTrue(ad_ui._replay_sequence([{"x":.1,"y":.2},{"delay":45,"x":.3,"y":.4}],Mock(),should_stop=lambda:False))
        self.assertAlmostEqual(sum(sleeps),45.7);self.assertEqual(click.call_count,2)
        browser.return_value._close_extra_tabs.assert_called_once()

    def test_test_helper_forwards_stop_and_skips_account_read_after_stop(self):
        stop=threading.Event()
        with patch.object(ad_ui,"load_settings",return_value={"ad_click_sequence":[{},{}]}), \
             patch.object(ad_ui,"_replay_sequence",side_effect=lambda *_args,**_kwargs:(stop.set(),False)[1]) as replay, \
             patch.object(ad_ui,"ATClient") as client:
            result=ad_ui.test_calibrated_ad_ui(should_stop=stop.is_set)
        self.assertFalse(result["ok"]);self.assertIn("stopped",result["message"])
        self.assertTrue(replay.call_args.kwargs["should_stop"]());client.assert_not_called()

    def test_calibration_stop_before_or_after_preparation_never_installs_recorder(self):
        for boundary in ("before","after"):
            stop=threading.Event()
            if boundary=="before":stop.set()
            with self.subTest(boundary=boundary), \
                 patch.object(ad_ui,"prepare_game_ui",side_effect=lambda *_args,**_kwargs:(stop.set(),{"ok":True})[1]) as prepare, \
                 patch.object(ad_ui,"_stabilize_targets",return_value=[{"webSocketDebuggerUrl":"fixture"}]) as targets, \
                 patch.object(ad_ui,"_eval") as evaluate:
                result=ad_ui.begin_ad_click_calibration(should_stop=stop.is_set)
            self.assertFalse(result["ok"]);evaluate.assert_not_called();targets.assert_not_called()
            if boundary=="before":prepare.assert_not_called()
            else:self.assertTrue(prepare.call_args.kwargs["should_stop"]())

    def test_play_teaching_stop_prevents_browser_start_or_recorder_install(self):
        for boundary in ("before","after_open"):
            stop=threading.Event()
            if boundary=="before":stop.set()
            with self.subTest(boundary=boundary),patch.object(ad_ui,"LearningBrowser") as browser, \
                 patch.object(ad_ui,"_stabilize_targets",return_value=[{"webSocketDebuggerUrl":"fixture"}]) as targets, \
                 patch.object(ad_ui,"_eval") as evaluate,patch.object(ad_ui.time,"sleep",return_value=None):
                browser.return_value.ensure_open.side_effect=lambda:(stop.set(),(True,"Fixture ready"))[1]
                result=ad_ui.begin_play_click_calibration(should_stop=stop.is_set)
            self.assertFalse(result["ok"]);evaluate.assert_not_called();targets.assert_not_called()
            if boundary=="before":browser.assert_not_called()


if __name__=="__main__":unittest.main()
