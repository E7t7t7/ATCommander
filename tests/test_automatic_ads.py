import unittest
from unittest.mock import patch, Mock

from atcommander import automatic_ads as ads
from atcommander import ad_ui
from atcommander import ad_browser_focus


class AutomaticAdTests(unittest.TestCase):
    def test_iframe_input_uses_parent_coordinates(self):
        prep={"target":{"webSocketDebuggerUrl":"child"},
              "input_target":{"webSocketDebuggerUrl":"parent"},
              "input_canvas":{"x":28,"y":68,"width":557,"height":313}}
        with patch.object(ad_ui,"_dispatch_click") as click, patch.object(ad_ui,"_cdp") as cdp, \
             patch.object(ad_browser_focus,"_RetainedFocusSession") as retained:
            ads._click(prep,{"x":0,"y":0,"width":557,"height":313},{"x":.8,"y":.1})
        self.assertEqual(click.call_args.args,("parent",473.6,99.3))
        self.assertEqual(cdp.call_args_list[0].args,("parent","Page.bringToFront",{}))
        self.assertEqual([call.args for call in retained.call_args_list], [("parent",), ("child",)])
        self.assertEqual(cdp.call_args_list[1].args,("parent","Input.dispatchMouseEvent",{"type":"mouseMoved","x":473.6,"y":99.3}))

    def run_flow(self,read,limit=25,stop=None):
        clock=[0];clicks=[]
        def view(_prep):return read(clock[0]),{}
        with patch.object(ad_ui,"prepare_ad_game_ui",return_value={"ok":True,"target":{}}), \
             patch.object(ads,"_view",side_effect=view), \
             patch.object(ads,"_click",side_effect=lambda p,c,x:clicks.append((clock[0],x))), \
             patch.object(ads.time,"monotonic",side_effect=lambda:clock[0]), \
             patch.object(ads.time,"sleep",side_effect=lambda t:clock.__setitem__(0,clock[0]+t)):
            result=ads.play_boost_ad(timeout=limit,should_stop=(lambda:stop(clock[0],clicks)) if stop else None)
        return result,clicks

    def test_watch_requires_specific_boost_title(self):
        result,clicks=self.run_flow(lambda t:{"watch_button":{"id":"paid-green"}})
        self.assertFalse(result["ok"]);self.assertEqual(clicks,[])

    def test_wrapper_receives_existing_client_and_stop_with_named_arguments(self):
        client=Mock();stop=Mock(return_value=False);log=Mock()
        with patch.object(ad_ui,"prepare_ad_game_ui",return_value={"ok":False,"message":"unavailable"}) as prepare, \
             patch.object(ads,"_click") as click:
            result=ads.play_boost_ad(log,client=client,should_stop=stop)
        prepare.assert_called_once_with(client=client,log=log,should_stop=stop,wait_for_canvas=60)
        click.assert_not_called();self.assertFalse(result['ok'])

    def test_stop_before_preparation_sends_no_browser_or_account_actions(self):
        with patch.object(ad_ui,"prepare_ad_game_ui") as prepare,patch.object(ads,"_click") as click:
            result=ads.play_boost_ad(should_stop=lambda:True)
        self.assertTrue(result['cancelled']);self.assertFalse(result['playback_started'])
        prepare.assert_not_called();click.assert_not_called()

    def test_stop_during_control_read_prevents_watch_entry_or_popup_click(self):
        for matches in ({"boost_title":{},"watch_button":{}},{"boost_entry":{}},{"close":{}}):
            with self.subTest(matches=matches):
                stopped=[False]
                def view(_prep):stopped[0]=True;return matches,{}
                with patch.object(ad_ui,"prepare_ad_game_ui",return_value={"ok":True}), \
                     patch.object(ads,"_view",side_effect=view),patch.object(ads,"_click") as click:
                    result=ads.play_boost_ad(should_stop=lambda:stopped[0])
                self.assertTrue(result['cancelled']);self.assertFalse(result['playback_started'])
                click.assert_not_called()

    def test_stop_after_watch_preserves_pending_credit_and_sends_no_close(self):
        result,clicks=self.run_flow(lambda t:{"boost_title":{},"watch_button":{"id":"watch"}} if t==0 else {"close":{}},
                                   stop=lambda _time,clicks:bool(clicks))
        self.assertTrue(result['cancelled']);self.assertTrue(result['playback_started'])
        self.assertEqual(clicks,[(0,{"id":"watch"})])

    def test_preparation_exception_never_leaks_details_or_clicks(self):
        with patch.object(ad_ui,"prepare_ad_game_ui",side_effect=RuntimeError("session_id=synthetic-private")), \
             patch.object(ads,"_click") as click:
            result=ads.play_boost_ad()
        self.assertFalse(result['ok']);click.assert_not_called()
        self.assertNotIn("synthetic-private",str(result))

    def test_actual_wrapper_rejects_unverified_settlement_before_any_ad_controls(self):
        for settlement in (None,{}, {'ok':False,'verified':True}, {'ok':True,'verified':False},
                           {'ok':1,'verified':1}):
            with self.subTest(settlement=settlement), \
                 patch.object(ad_ui,"prepare_game_ui",return_value={'ok':True,'target':{}}), \
                 patch('atcommander.utility_commands.build_service'), \
                 patch('atcommander.startup_popups.settle_startup_popups',return_value=settlement), \
                 patch.object(ad_browser_focus,'release_game_input_focus') as release, \
                 patch.object(ads,'_view') as view,patch.object(ads,'_click') as click:
                result=ads.play_boost_ad(client=Mock())
            self.assertFalse(result['ok']);view.assert_not_called();click.assert_not_called()
            release.assert_called_once()

    def test_waits_for_ad_close_then_returns_to_boost(self):
        def frame(t):
            if t==0:return {"boost_entry":{"id":"boost"}}
            if t==1 or t>=17:return {"boost_title":{},"watch_button":{"id":"watch"}}
            if t>=16:return {"close":{"id":"close"}}
            return {}
        result,clicks=self.run_flow(frame)
        self.assertTrue(result["ok"])
        self.assertEqual(clicks,[(0,{"id":"boost"}),(1,{"id":"watch"}),(16,{"id":"close"})])

    def test_never_closes_early_or_clicks_destination(self):
        def frame(t):
            if t==0:return {"boost_title":{},"watch_button":{"id":"watch"}}
            if t>=14:return {"boost_title":{},"watch_button":{"id":"watch"}}
            return {"close":{"id":"close"},"destination":{"id":"play-now"}}
        result,clicks=self.run_flow(frame)
        self.assertTrue(result["ok"])
        self.assertEqual(clicks,[(0,{"id":"watch"}),(12,{"id":"close"})])

    def test_missing_ad_does_not_replay_watch(self):
        result,clicks=self.run_flow(lambda t:{"boost_title":{},"watch_button":{"id":"watch"}})
        self.assertFalse(result["ok"])
        self.assertEqual(len(clicks),1)

    def test_reward_display_after_real_close_requires_credit_before_recovery(self):
        def frame(t):
            if t==0:return {"boost_title":{},"watch_button":{"id":"watch"}}
            if t==12:return {"close":{"id":"close"}}
            return {}
        result,clicks=self.run_flow(frame)
        self.assertFalse(result["ok"]);self.assertTrue(result["closed_completed_ad"])
        self.assertEqual(clicks,[(0,{"id":"watch"}),(12,{"id":"close"})])

    def test_read_failure_stops_before_any_input(self):
        with patch.object(ad_ui,"prepare_ad_game_ui",return_value={"ok":True}), \
             patch.object(ads,"_view",side_effect=RuntimeError("unreadable")), \
             patch.object(ads,"_click") as click:
            result=ads.play_boost_ad()
        self.assertFalse(result["ok"]);click.assert_not_called()

    def test_control_read_failure_after_watch_retains_uncertain_playback_state(self):
        def frame(t):
            if t==0:return {"boost_title":{},"watch_button":{"id":"watch"}}
            raise RuntimeError("password=synthetic-private-value")
        result,clicks=self.run_flow(frame)
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("playback_started"))
        self.assertEqual(clicks,[(0,{"id":"watch"})])
        self.assertNotIn("synthetic-private-value",str(result))

    def test_watch_input_failure_stops_without_replay_or_private_exception_text(self):
        logs=[]
        with patch.object(ad_ui,"prepare_ad_game_ui",return_value={"ok":True,"target":{}}), \
             patch.object(ads,"_view",return_value=({"boost_title":{},"watch_button":{}},{})), \
             patch.object(ads,"_click",side_effect=RuntimeError("session_id=synthetic-private-value")) as click:
            result=ads.play_boost_ad(logs.append)
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("playback_started"))
        click.assert_called_once()
        self.assertNotIn("synthetic-private-value",str(result)+str(logs))

    def test_iframe_capture_adds_parent_offset_before_input_and_matching(self):
        prep={"target":{"type":"iframe","id":"fixture-frame","webSocketDebuggerUrl":"child"}}
        parent={"type":"page","url":"https://www.kongregate.com/en/games/throwdown/animation-throwdown","webSocketDebuggerUrl":"parent"}
        canvas={"x":5,"y":7,"width":557,"height":313}
        def cdp(ws,method,params):
            if method=="DOM.getFrameOwner":return {"backendNodeId":1}
            if method=="DOM.getBoxModel":return {"model":{"content":[20,60,577,60,577,373,20,373]}}
            if method=="Page.captureScreenshot":return {"data":"fixture-png"}
            raise AssertionError("Unexpected CDP method")
        with patch.object(ad_ui,"_eval",return_value={"canvas":canvas}), \
             patch.object(ad_ui,"_page_targets",return_value=[parent]), \
             patch.object(ad_ui,"_cdp",side_effect=cdp) as command, \
             patch.object(ads,"classify_pixels",return_value={"boost_entry":{}}):
            matches,found=ads._view(prep)
        self.assertEqual(found,canvas)
        self.assertEqual(matches,{"boost_entry":{}})
        self.assertEqual(prep["input_target"],parent)
        self.assertEqual(prep["input_canvas"],{"x":25.0,"y":67.0,"width":557.0,"height":313.0,"scale":1})
        self.assertEqual(command.call_args.args,("parent","Page.captureScreenshot",{"format":"png","captureBeyondViewport":False,"clip":prep["input_canvas"]}))

    def test_ambiguous_frame_fallback_never_captures_or_clicks_other_surface(self):
        prep={"target":{"type":"iframe","id":"fixture-frame","url":"https://game.example/game","webSocketDebuggerUrl":"child"}}
        parent={"type":"page","url":"https://www.kongregate.com/","webSocketDebuggerUrl":"parent"}
        frames=[{"src":"https://game.example/one","x":0,"y":0,"width":557,"height":313},
                {"src":"https://game.example/two","x":0,"y":0,"width":557,"height":313}]
        def evaluate(ws,expr):
            if ws=="child":return {"canvas":{"x":0,"y":0,"width":557,"height":313}}
            return frames
        with patch.object(ad_ui,"_eval",side_effect=evaluate), \
             patch.object(ad_ui,"_page_targets",return_value=[parent]), \
             patch.object(ad_ui,"_cdp",side_effect=RuntimeError("Unavailable frame owner")) as command, \
             patch.object(ads,"classify_pixels") as classify:
            with self.assertRaisesRegex(RuntimeError,"uniquely"):
                ads._view(prep)
        classify.assert_not_called()
        self.assertEqual([call.args[1] for call in command.call_args_list],["DOM.getFrameOwner"])


if __name__=="__main__":unittest.main()
