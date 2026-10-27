import unittest
from unittest.mock import patch,Mock

from atcommander import ad_ui, ad_browser_focus


class AdTimingTests(unittest.TestCase):
    def setUp(self):
        login=patch("atcommander.login_bonus.prepare_login_for_ads",return_value=Mock(data={}))
        login.start();self.addCleanup(login.stop)
        sleep=patch.object(ad_ui.time,"sleep",return_value=None)
        sleep.start();self.addCleanup(sleep.stop)

    def test_cold_start_waits_for_delayed_play_control_and_clicks_once(self):
        clock=[0.0];clicks=[]
        def evaluate(ws,script):
            if script==ad_ui._CANVAS_JS:
                return {"canvas":{"width":557,"height":313}} if clicks and clock[0]>=6 else {"canvas":None}
            self.assertEqual(script,ad_ui._PLAY_JS)
            if clock[0]<4:return {"found":False}
            return {"found":True,"text":"Play Now","x":50,"y":60}
        with patch.object(ad_ui,"LearningBrowser") as browser, \
             patch.object(ad_ui,"_stabilize_targets",return_value=[{"type":"page","url":"https://www.kongregate.com/en/games/throwdown/animation-throwdown","webSocketDebuggerUrl":"fixture"}]), \
             patch.object(ad_ui,"_eval",side_effect=evaluate),patch.object(ad_ui,"_click_play_control",side_effect=lambda *_:clicks.append(clock[0])), \
             patch.object(ad_ui.time,"time",side_effect=lambda:clock[0]), \
             patch.object(ad_ui.time,"sleep",side_effect=lambda delay:clock.__setitem__(0,clock[0]+delay)):
            browser.return_value.ensure_open.return_value=(True,"Fixture browser ready")
            answer=ad_ui.prepare_game_ui(wait_for_canvas=10)
        self.assertTrue(answer["ok"]);self.assertTrue(answer["play_clicked"])
        self.assertEqual(len(clicks),1);self.assertGreaterEqual(clicks[0],4)

    def test_launcher_pointer_focus_precedes_trusted_input(self):
        events=[]
        def retained(ws):
            events.append("focus")
            return Mock(close=lambda:events.append("release"))
        with patch.object(ad_ui,"_cdp",side_effect=lambda ws,method,params:events.append(method)), \
             patch.object(ad_ui,"_dispatch_click",side_effect=lambda *_:events.append("click")) as click, \
             patch.object(ad_browser_focus,"_RetainedFocusSession",side_effect=retained), \
             patch("atcommander.embedded_browser.get_embedded_state",return_value={"embedded":False}):
            ad_ui._click_play_control({"webSocketDebuggerUrl":"fixture"},{"x":50,"y":60})
        self.assertEqual(events,["Page.bringToFront","focus","Input.dispatchMouseEvent","click","release"])
        click.assert_called_once_with("fixture",50.0,60.0)

    def test_calibration_preserves_complete_rewarded_ad_wait(self):
        clicks = [
            {"ts": 1000, "vx": .1, "vy": .2},
            {"ts": 46000, "vx": .3, "vy": .4},
        ]
        with patch.object(ad_ui, "_stabilize_targets", return_value=[{"webSocketDebuggerUrl": "fixture"}]), patch.object(ad_ui, "_eval", return_value=clicks):
            result = ad_ui.finish_ad_click_calibration()
        self.assertTrue(result["ok"])
        self.assertEqual(result["sequence"][1]["delay"], 45.0)

    def test_replay_waits_full_calibration_with_browser_scoped_clicks(self):
        sequence = [{"delay": .6, "x": .1, "y": .2}, {"delay": 45.0, "x": .3, "y": .4}]
        canvas = {"innerWidth": 100, "innerHeight": 100}
        with patch.object(ad_ui, "prepare_game_ui", return_value={"ok": True, "target": {"webSocketDebuggerUrl": "fixture"}}), \
             patch.object(ad_ui, "_fix_window_geometry") as geometry, \
             patch.object(ad_ui, "_cdp"),patch.object(ad_browser_focus,"_RetainedFocusSession"), \
             patch.object(ad_ui, "_eval", return_value=canvas), patch.object(ad_ui, "_dispatch_click") as click, \
             patch.object(ad_ui, "LearningBrowser") as browser, patch.object(ad_ui.time, "sleep") as sleep:
            self.assertTrue(ad_ui._replay_sequence(sequence, lambda message: None))
        sleep.assert_any_call(45.0)
        self.assertEqual(click.call_args_list[1].args, ("fixture", 30.0, 40.0))
        browser.return_value._close_extra_tabs.assert_called_once()
        geometry.assert_not_called()

    def test_invalid_timings_fail_before_any_ui_action(self):
        for delay in (float("nan"), float("inf"), 301, -5, "broken"):
            with self.subTest(delay=delay), patch.object(ad_ui, "prepare_game_ui") as prepare, patch.object(ad_ui, "_dispatch_click") as click:
                self.assertFalse(ad_ui._replay_sequence([{"delay": delay}], lambda message: None))
                prepare.assert_not_called()
                click.assert_not_called()

    def test_missing_or_rejected_initial_status_never_plays_an_ad(self):
        for response in ({},{"result":False,"user_data":{"boost_level":0}}):
            client=Mock();client.get_boost_level.return_value=response
            with patch.object(ad_ui,"load_settings",return_value={"ad_click_sequence":[{},{}]}),patch.object(ad_ui,"load_credentials"),patch.object(ad_ui,"ATClient",return_value=client),patch.object(ad_ui,"_automatic_boost_playback") as replay:
                answer=ad_ui.attempt_legitimate_ad_ui()
            self.assertFalse(answer["ok"]);self.assertIsNone(answer["boost_level"])
            replay.assert_not_called()

    def test_late_server_credit_uses_full_wait_and_paced_reads(self):
        clock=[0.0];read_times=[]
        def read():
            read_times.append(clock[0])
            return {"user_data":{"boost_level":1 if clock[0]>=32 else 0}}
        client=Mock();client.get_boost_level.side_effect=read
        with patch.object(ad_ui,"load_settings",return_value={"ad_click_sequence":[{},{}]}),patch.object(ad_ui,"load_credentials"),patch.object(ad_ui,"ATClient",return_value=client),patch.object(ad_ui,"_automatic_boost_playback",return_value={"ok":True}),patch.object(ad_ui.time,"monotonic",side_effect=lambda:clock[0]),patch.object(ad_ui.time,"time",side_effect=lambda:clock[0]),patch.object(ad_ui.time,"sleep",side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
            answer=ad_ui.attempt_legitimate_ad_ui(wait_seconds=45,target_level=1)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["boost_level"],1)
        self.assertTrue(all(b-a>=8 for a,b in zip(read_times,read_times[1:])))
        self.assertGreater(read_times[-1],15)

    def test_poll_failures_preserve_last_verified_level(self):
        clock=[0.0];client=Mock()
        client.get_boost_level.side_effect=[{"user_data":{"boost_level":1}}]+[{}]*8
        with patch.object(ad_ui,"load_settings",return_value={"ad_click_sequence":[{},{}]}),patch.object(ad_ui,"load_credentials"),patch.object(ad_ui,"ATClient",return_value=client),patch.object(ad_ui,"_automatic_boost_playback",return_value={"ok":True}) as replay,patch.object(ad_ui.time,"monotonic",side_effect=lambda:clock[0]),patch.object(ad_ui.time,"time",side_effect=lambda:clock[0]),patch.object(ad_ui.time,"sleep",side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
            answer=ad_ui.attempt_legitimate_ad_ui(wait_seconds=45,target_level=3)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["boost_level"],1)
        self.assertIn("unavailable",answer["message"])
        replay.assert_called_once()

    def test_maximum_verified_boost_needs_no_playback(self):
        client=Mock();client.get_boost_level.return_value={"user_data":{"boost_level":3}}
        with patch.object(ad_ui,"load_settings",return_value={"ad_click_sequence":[{},{}]}),patch.object(ad_ui,"load_credentials"),patch.object(ad_ui,"ATClient",return_value=client),patch.object(ad_ui,"_automatic_boost_playback") as replay:
            answer=ad_ui.attempt_legitimate_ad_ui()
        self.assertTrue(answer["ok"]);replay.assert_not_called()

    def test_uncertain_playback_checks_credit_once_without_starting_another_ad(self):
        clock=[0.0];read_times=[]
        def read():
            read_times.append(clock[0])
            return {"user_data":{"boost_level":0 if len(read_times)==1 else 1}}
        client=Mock();client.get_boost_level.side_effect=read
        with patch.object(ad_ui,"load_credentials"),patch.object(ad_ui,"ATClient",return_value=client), \
             patch.object(ad_ui,"_automatic_boost_playback",return_value={"ok":False,"playback_started":True,"message":"Fixture panel read unavailable"}) as replay, \
             patch.object(ad_ui.time,"monotonic",side_effect=lambda:clock[0]), \
             patch.object(ad_ui.time,"time",side_effect=lambda:clock[0]), \
             patch.object(ad_ui.time,"sleep",side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
            answer=ad_ui.attempt_legitimate_ad_ui(wait_seconds=45,target_level=3)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["boost_level"],1)
        self.assertEqual(answer["attempts"],1)
        replay.assert_called_once()
        self.assertEqual(read_times,[0,8])

    def test_ui_fail_before_watch_requires_no_credit_poll(self):
        client=Mock();client.get_boost_level.return_value={"user_data":{"boost_level":0}}
        with patch.object(ad_ui,"load_credentials"),patch.object(ad_ui,"ATClient",return_value=client), \
             patch.object(ad_ui,"_automatic_boost_playback",return_value={"ok":False,"playback_started":False,"message":"Fixture canvas unavailable"}) as replay:
            answer=ad_ui.attempt_legitimate_ad_ui()
        self.assertFalse(answer["ok"])
        client.get_boost_level.assert_called_once()
        replay.assert_called_once()

    def test_closed_reward_display_recovers_only_after_confirmed_credit(self):
        clock=[0.0];client=Mock()
        client.get_boost_level.side_effect=[{"user_data":{"boost_level":0}},{"user_data":{"boost_level":1}},{"user_data":{"boost_level":2}}]
        with patch.object(ad_ui,"_automatic_boost_playback",side_effect=[{"ok":False,"playback_started":True,"closed_completed_ad":True},{"ok":True}]) as play, \
             patch("atcommander.automatic_ads.reset_ad_game_view",return_value=True) as reset, \
             patch.object(ad_ui.time,"monotonic",side_effect=lambda:clock[0]),patch.object(ad_ui.time,"time",side_effect=lambda:clock[0]), \
             patch.object(ad_ui.time,"sleep",side_effect=lambda delay:clock.__setitem__(0,clock[0]+delay)):
            answer=ad_ui.attempt_legitimate_ad_ui(client=client,target_level=2)
        self.assertTrue(answer["ok"]);self.assertEqual(play.call_count,2);reset.assert_called_once()

    def test_closed_display_without_credit_is_not_refreshed_or_replayed(self):
        clock=[0.0];client=Mock();client.get_boost_level.return_value={"user_data":{"boost_level":0}}
        with patch.object(ad_ui,"_automatic_boost_playback",return_value={"ok":False,"playback_started":True,"closed_completed_ad":True}) as play, \
             patch("atcommander.automatic_ads.reset_ad_game_view") as reset, \
             patch.object(ad_ui.time,"monotonic",side_effect=lambda:clock[0]),patch.object(ad_ui.time,"time",side_effect=lambda:clock[0]), \
             patch.object(ad_ui.time,"sleep",side_effect=lambda delay:clock.__setitem__(0,clock[0]+delay)):
            answer=ad_ui.attempt_legitimate_ad_ui(client=client)
        self.assertFalse(answer["ok"]);play.assert_called_once();reset.assert_not_called()


if __name__ == "__main__":
    unittest.main()

