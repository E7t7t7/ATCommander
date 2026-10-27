from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from atcommander import ad_ui
from atcommander import research_ad_ui as ads


PROMPT={"x":.39,"y":.86,"id":"research-blue-ad-prompt"}
WATCH={"x":.43,"y":.88,"id":"research-real-watch"}
CLOSE={"x":.92,"y":.12,"id":"own-ad-close"}
ENTRY={"x":.90,"y":.28,"id":"research-entry"}
CHAMBER={"x":.20,"y":.20,"id":"tv-chamber"}
CANVAS={"x":0,"y":0,"width":600,"height":400}


def snapshot():
    return {"result":True,"time":1000,
            "user_tasks":{"201":{"task_type":1,"slot_index":3,"task_index":201,
                                "task_id_1":22,"task_id_2":23,"end_time":4000}},
            "user_combos_compressed":{},
            "research_ad_speed_ups":[{"slot_index":3,"views":0,"max_views":3,"next_speed_up":2700}]}


def credited():
    state=snapshot();state["time"]=1020
    state["research_ad_speed_ups"][0]["views"]=1
    state["research_ad_speed_ups"][0]["next_speed_up"]=300
    state["user_tasks"]["201"]["end_time"]=1300
    return state


def popup(point=PROMPT):
    return {"research_time_title":{},"research_ad_button":point}


def watch_popup(point=WATCH):
    return {"research_boost_title":{},"research_boost_description":{},"research_watch_button":point}


class ResearchAdUITests(unittest.TestCase):
    def test_startup_settlement_precedes_frozen_research_target_read(self):
        events=[];client=SimpleNamespace(init=Mock(side_effect=lambda:events.append('freeze') or snapshot()))
        stop=lambda:False;log=Mock()
        with patch.object(ad_ui,"prepare_ad_game_ui",side_effect=lambda **_:events.append('settled') or {'ok':True}) as prepare, \
             patch.object(ads,"_view",side_effect=RuntimeError("synthetic unreadable")),patch.object(ads,"_click") as click:
            result=ads.play_research_ad(client,log=log,should_stop=stop)
        self.assertEqual(events,['settled','freeze'])
        prepare.assert_called_once_with(client=client,log=log,should_stop=stop,wait_for_canvas=60)
        self.assertFalse(result['ok']);click.assert_not_called()

    def test_unverified_startup_popup_prevents_target_read_and_ad_input(self):
        client=SimpleNamespace(init=Mock())
        with patch.object(ad_ui,"prepare_ad_game_ui",return_value={'ok':False,'message':'unverified reward'}), \
             patch.object(ads,"_view") as view,patch.object(ads,"_click") as click:
            result=ads.play_research_ad(client)
        self.assertFalse(result['ok']);client.init.assert_not_called();view.assert_not_called();click.assert_not_called()

    def test_stop_during_fresh_pre_watch_read_prevents_playback(self):
        stopped=[False]
        reads=[0]
        def fresh():
            reads[0]+=1
            if reads[0]==2:stopped[0]=True
            return snapshot()
        client=SimpleNamespace(init=Mock(side_effect=fresh))
        with patch.object(ad_ui,"prepare_ad_game_ui",return_value={"ok":True,"target":{}}), \
             patch.object(ads,"_view",return_value=(watch_popup(),CANVAS)), \
             patch.object(ads,"_click") as click, \
             patch.object(ads.time,"sleep"):
            result=ads.play_research_ad(client,should_stop=lambda:stopped[0])
        self.assertFalse(result['ok'])
        self.assertFalse(result['playback_started'])
        self.assertEqual(client.init.call_count,2)
        click.assert_not_called()

    def flow(self,read=None,*,fresh=None,after=None,input_error=None,stop=None,prepare=None,wait=60):
        clock=[0.0];clicks=[];frames=[];logs=[];reads=[]
        def init():
            reads.append(clock[0])
            value=snapshot() if len(reads)==1 else (fresh if len(reads)==2 else after)
            if value is None:value=snapshot() if len(reads)==2 else credited()
            if isinstance(value,Exception):raise value
            return deepcopy(value)
        client=SimpleNamespace(init=Mock(side_effect=init))
        def view(_prep):
            frames.append(clock[0])
            if read is not None:return read(clock[0],clicks,len(frames)),CANVAS
            ids=[point["id"] for _,point in clicks]
            if PROMPT["id"] not in ids:return popup(),CANVAS
            if WATCH["id"] not in ids:return watch_popup(),CANVAS
            return {"close":CLOSE,"destination":{"x":.5,"y":.5,"id":"advertiser"}},CANVAS
        def click(_prep,_canvas,point):
            clicks.append((clock[0],point))
            if input_error and input_error(point):raise RuntimeError("synthetic-private-input-detail")
        prepared=prepare or {"ok":True,"target":{"webSocketDebuggerUrl":"synthetic-dedicated-browser"}}
        stop_check=(lambda:stop(clock[0],len(frames),clicks)) if stop else None
        with patch.object(ad_ui,"prepare_ad_game_ui",return_value=prepared), \
             patch.object(ads,"_view",side_effect=view), \
             patch.object(ads,"_click",side_effect=click), \
             patch.object(ads.time,"monotonic",side_effect=lambda:clock[0]), \
             patch.object(ads.time,"sleep",side_effect=lambda duration:clock.__setitem__(0,clock[0]+duration)):
            result=ads.play_research_ad(client,log=logs.append,should_stop=stop_check,wait_seconds=wait)
        return result,clicks,client,logs,frames,reads

    def test_requires_both_research_title_and_specific_blue_ad_artwork(self):
        for controls in ({"research_time_title":{},"watch_button":WATCH},
                         {"research_ad_button":PROMPT},
                         {"research_time_title":{},"paid_button":{"x":.4,"y":.86,"id":"paid-gems"}}):
            with self.subTest(controls=controls):
                result,clicks,*_=self.flow(lambda *_:controls,wait=20)
                self.assertFalse(result["ok"])
                self.assertEqual(clicks,[])

    def test_watch_requires_two_stable_frames(self):
        def read(_time,clicks,_index):
            return watch_popup() if not clicks else {"close":CLOSE}
        result,clicks,_,_,frames,_=self.flow(read)
        self.assertTrue(result["ok"])
        self.assertEqual(clicks[0][1],WATCH)
        self.assertGreaterEqual(sum(frame<=clicks[0][0] for frame in frames),2)

    def test_moving_watch_control_never_gets_clicked(self):
        def read(_time,_clicks,index):
            return watch_popup({**WATCH,"x":.35 if index%2 else .45})
        result,clicks,*_=self.flow(read,wait=20)
        self.assertFalse(result["ok"])
        self.assertEqual(clicks,[])

    def test_missing_frame_breaks_stability(self):
        def read(_time,_clicks,index):return watch_popup() if index%2 else {}
        result,clicks,*_=self.flow(read,wait=20)
        self.assertFalse(result["ok"])
        self.assertEqual(clicks,[])

    def test_never_clicks_paid_or_advertiser_destination(self):
        def read(_time,clicks,_index):
            controls=watch_popup() if not clicks else {"close":CLOSE}
            return {**controls,"paid_button":{"x":.8,"y":.86,"id":"paid-gems"},
                    "destination":{"x":.5,"y":.5,"id":"advertiser"}}
        result,clicks,*_=self.flow(read)
        self.assertTrue(result["ok"])
        self.assertEqual([point["id"] for _,point in clicks],[WATCH["id"],CLOSE["id"]])

    def test_waits_twelve_seconds_before_own_ad_close(self):
        result,clicks,*_=self.flow()
        self.assertTrue(result["ok"])
        watched=next(stamp for stamp,point in clicks if point==WATCH)
        closed=next(stamp for stamp,point in clicks if point==CLOSE)
        self.assertGreaterEqual(closed-watched,12)
        self.assertEqual(sum(point==CLOSE for _,point in clicks),1)

    def test_task_change_stops_before_watch(self):
        changed=snapshot();changed["user_tasks"]["201"]["task_id_2"]=24
        result,clicks,client,*_=self.flow(fresh=changed)
        self.assertFalse(result["ok"])
        self.assertFalse(result["playback_started"])
        self.assertEqual([point for _,point in clicks],[PROMPT])
        self.assertEqual(client.init.call_count,2)
        self.assertIn("changed",result["message"])

    def test_fresh_selection_read_failure_stops_before_watch_safely(self):
        result,clicks,_,logs,*_=self.flow(fresh=RuntimeError("synthetic-private-read-detail"))
        self.assertFalse(result["ok"])
        self.assertFalse(result["playback_started"])
        self.assertEqual([point for _,point in clicks],[PROMPT])
        self.assertNotIn("synthetic-private-read-detail",str(result)+str(logs))

    def test_returned_watch_panel_is_not_replayed(self):
        result,clicks,*_=self.flow(lambda *_:watch_popup())
        self.assertFalse(result["ok"])
        self.assertTrue(result["playback_started"])
        self.assertEqual([point for _,point in clicks],[WATCH])

    def test_credit_requires_exact_task_timer_benefit(self):
        unchanged=credited();unchanged["user_tasks"]["201"]["end_time"]=4000
        result,clicks,client,*_=self.flow(after=unchanged)
        self.assertFalse(result["ok"])
        self.assertTrue(result["playback_started"])
        self.assertGreaterEqual(client.init.call_count,3)
        self.assertEqual([point for _,point in clicks],[PROMPT,WATCH,CLOSE])

    def test_timer_benefit_without_view_credit_is_not_success(self):
        unchanged=credited();unchanged["research_ad_speed_ups"][0]["views"]=0
        result,clicks,*_=self.flow(after=unchanged)
        self.assertFalse(result["ok"])
        self.assertEqual([point for _,point in clicks],[PROMPT,WATCH,CLOSE])

    def test_replacement_task_credit_is_not_success(self):
        changed=credited();changed["user_tasks"]["201"]["task_index"]=202
        result,clicks,*_=self.flow(after=changed)
        self.assertFalse(result["ok"])
        self.assertEqual([point for _,point in clicks],[PROMPT,WATCH,CLOSE])

    def test_verified_response_reports_frozen_pair_and_slot(self):
        result,_,client,_,_,reads=self.flow()
        self.assertEqual(result["pair"],"22~23")
        self.assertEqual(result["slot_index"],3)
        self.assertEqual(result["phase"],"speedup")
        self.assertTrue(result["verified"])
        self.assertEqual(client.init.call_count,3)
        self.assertGreaterEqual(reads[-1]-reads[1],15)

    def test_stopped_before_initial_read_or_browser_preparation(self):
        client=SimpleNamespace(init=Mock())
        with patch.object(ad_ui,"prepare_ad_game_ui") as prepare,patch.object(ads,"_view") as view:
            result=ads.play_research_ad(client,should_stop=lambda:True)
        self.assertFalse(result["ok"])
        client.init.assert_not_called();prepare.assert_not_called();view.assert_not_called()

    def test_stop_after_first_frame_prevents_fresh_read_and_input(self):
        result,clicks,client,*_=self.flow(stop=lambda _time,frames,_clicks:frames>=1)
        self.assertFalse(result["ok"])
        self.assertEqual(clicks,[])
        self.assertEqual(client.init.call_count,1)

    def test_initial_read_failure_after_startup_settlement_is_sanitized(self):
        client=SimpleNamespace(init=Mock(side_effect=RuntimeError("synthetic-private-init-detail")))
        with patch.object(ad_ui,"prepare_ad_game_ui",return_value={"ok":True}),patch.object(ads,"_click") as click:
            result=ads.play_research_ad(client)
        self.assertFalse(result["ok"])
        self.assertNotIn("synthetic-private-init-detail",str(result))
        click.assert_not_called()

    def test_prepare_exception_is_safe_and_sanitized(self):
        client=SimpleNamespace(init=Mock(return_value=snapshot()))
        with patch.object(ad_ui,"prepare_ad_game_ui",side_effect=RuntimeError("synthetic-private-prepare-detail")),patch.object(ads,"_view") as view:
            result=ads.play_research_ad(client)
        self.assertFalse(result["ok"])
        self.assertNotIn("synthetic-private-prepare-detail",str(result))
        view.assert_not_called()

    def test_control_read_failure_is_safe_and_never_replayed(self):
        def read(_time,_clicks,_index):raise RuntimeError("synthetic-private-view-detail")
        result,clicks,_,logs,*_=self.flow(read)
        self.assertFalse(result["ok"])
        self.assertEqual(clicks,[])
        self.assertNotIn("synthetic-private-view-detail",str(result)+str(logs))

    def test_read_failure_after_watch_keeps_uncertain_started_state(self):
        def read(_time,clicks,_index):
            if clicks:raise RuntimeError("synthetic-private-view-detail")
            return watch_popup()
        result,clicks,_,logs,*_=self.flow(read)
        self.assertFalse(result["ok"])
        self.assertTrue(result["playback_started"])
        self.assertEqual([point for _,point in clicks],[WATCH])
        self.assertNotIn("synthetic-private-view-detail",str(result)+str(logs))

    def test_watch_input_exception_keeps_uncertain_state_without_replay(self):
        result,clicks,_,logs,*_=self.flow(input_error=lambda point:point==WATCH)
        self.assertFalse(result["ok"])
        self.assertTrue(result["playback_started"])
        self.assertEqual([point for _,point in clicks],[PROMPT,WATCH])
        self.assertNotIn("synthetic-private-input-detail",str(result)+str(logs))

    def test_close_input_exception_requires_fresh_exact_credit(self):
        result,clicks,client,*_=self.flow(input_error=lambda point:point==CLOSE)
        self.assertTrue(result["ok"])
        self.assertEqual([point for _,point in clicks],[PROMPT,WATCH,CLOSE])
        self.assertEqual(client.init.call_count,3)

    def test_credit_read_exceptions_never_replay_or_leak_details(self):
        result,clicks,_,logs,*_=self.flow(after=RuntimeError("synthetic-private-credit-detail"))
        self.assertFalse(result["ok"])
        self.assertEqual([point for _,point in clicks],[PROMPT,WATCH,CLOSE])
        self.assertNotIn("synthetic-private-credit-detail",str(result)+str(logs))

    def test_research_entry_and_hub_route_each_click_once(self):
        def read(_time,clicks,_index):
            ids=[point["id"] for _,point in clicks]
            if ENTRY["id"] not in ids:return {"research_entry":ENTRY}
            if CHAMBER["id"] not in ids:return {"research_hub_marker":{},"research_ad_chamber":CHAMBER}
            if PROMPT["id"] not in ids:return popup()
            if WATCH["id"] not in ids:return watch_popup()
            return {"close":CLOSE}
        result,clicks,*_=self.flow(read)
        self.assertTrue(result["ok"])
        self.assertEqual([point for _,point in clicks],[ENTRY,CHAMBER,PROMPT,WATCH,CLOSE])

    def test_stale_chamber_control_is_not_clicked_twice(self):
        result,clicks,*_=self.flow(lambda *_:{"research_hub_marker":{},"research_ad_chamber":CHAMBER},wait=20)
        self.assertFalse(result["ok"])
        self.assertEqual([point for _,point in clicks],[CHAMBER])

    def test_blue_timer_button_opens_prompt_once_without_claiming_playback(self):
        result,clicks,client,*_=self.flow(lambda *_:popup(),wait=20)
        self.assertFalse(result["ok"])
        self.assertFalse(result["playback_started"])
        self.assertEqual([point for _,point in clicks],[PROMPT])
        self.assertLessEqual(client.init.call_count,2)

    def test_prompt_input_exception_stops_without_claiming_started_ad(self):
        result,clicks,_,logs,*_=self.flow(input_error=lambda point:point==PROMPT)
        self.assertFalse(result["ok"])
        self.assertFalse(result["playback_started"])
        self.assertEqual([point for _,point in clicks],[PROMPT])
        self.assertNotIn("synthetic-private-input-detail",str(result)+str(logs))

    def test_stop_after_close_prevents_credit_read(self):
        result,clicks,client,*_=self.flow(stop=lambda _time,_frames,clicks:any(point==CLOSE for _,point in clicks))
        self.assertFalse(result["ok"])
        self.assertTrue(result["playback_started"])
        self.assertEqual([point for _,point in clicks],[PROMPT,WATCH,CLOSE])
        self.assertEqual(client.init.call_count,2)

    def test_no_close_times_out_without_credit_read_or_replay(self):
        def read(_time,clicks,_index):return watch_popup() if not clicks else {}
        result,clicks,client,*_=self.flow(read,wait=20)
        self.assertFalse(result["ok"])
        self.assertTrue(result["playback_started"])
        self.assertEqual([point for _,point in clicks],[WATCH])
        self.assertEqual(client.init.call_count,2)

    def test_failed_credit_checks_keep_eight_second_read_pacing(self):
        result,_,_,_,_,reads=self.flow(after=snapshot())
        self.assertFalse(result["ok"])
        credit_reads=reads[2:]
        self.assertGreaterEqual(len(credit_reads),2)
        self.assertTrue(all(later-earlier>=8 for earlier,later in zip(credit_reads,credit_reads[1:])))

    def test_real_watch_requires_specific_research_description_and_artwork(self):
        for controls in ({"research_boost_description":{},"watch_button":WATCH},
                         {"research_watch_button":WATCH},
                         {"research_boost_title":{},"research_watch_button":WATCH},
                         {"research_boost_description":{},"paid_button":{"x":.5,"y":.88,"id":"paid"}}):
            with self.subTest(controls=controls):
                result,clicks,*_=self.flow(lambda *_:controls,wait=20)
                self.assertFalse(result["ok"])
                self.assertFalse(result["playback_started"])
                self.assertEqual(clicks,[])

    def test_native_prompt_close_never_confirms_completed_ad(self):
        def read(_time,clicks,_index):return {**watch_popup(),"close":CLOSE}
        result,clicks,client,*_=self.flow(read)
        self.assertFalse(result["ok"])
        self.assertEqual([point for _,point in clicks],[WATCH])
        self.assertEqual(client.init.call_count,2)

    def test_partial_native_research_title_inhibits_close_after_disappearance(self):
        def read(_time,clicks,index):
            if not clicks:return watch_popup()
            if index==3:return {}
            return {"research_time_title":{},"close":CLOSE}
        result,clicks,client,*_=self.flow(read,wait=30)
        self.assertFalse(result["ok"])
        self.assertEqual([point for _,point in clicks],[WATCH])
        self.assertEqual(client.init.call_count,2)

    def test_partial_native_boost_title_inhibits_close(self):
        def read(_time,clicks,_index):
            return watch_popup() if not clicks else {"research_boost_title":{},"close":CLOSE}
        result,clicks,client,*_=self.flow(read,wait=30)
        self.assertFalse(result["ok"])
        self.assertEqual([point for _,point in clicks],[WATCH])
        self.assertEqual(client.init.call_count,2)

    def test_parent_iframe_input_uses_observed_parent_canvas_offset(self):
        prep={"target":{"webSocketDebuggerUrl":"synthetic-child"},
              "input_target":{"webSocketDebuggerUrl":"synthetic-parent"},
              "input_canvas":{"x":28,"y":68,"width":557,"height":313}}
        with patch.object(ad_ui,"_cdp") as cdp,patch.object(ads.time,"sleep"), \
             patch("atcommander.ad_browser_focus._RetainedFocusSession"):
            ads._click(prep,CANVAS,WATCH)
        self.assertEqual(cdp.call_args_list[0].args,("synthetic-parent","Page.bringToFront",{}))
        events=[call.args for call in cdp.call_args_list if call.args[1]=="Input.dispatchMouseEvent"]
        self.assertEqual([event[2]["type"] for event in events],["mouseMoved","mousePressed","mouseMoved","mouseReleased"])
        for surface,_method,payload in events:
            self.assertEqual(surface,"synthetic-parent")
            self.assertAlmostEqual(payload["x"],28+557*WATCH["x"])
            self.assertAlmostEqual(payload["y"],68+313*WATCH["y"])
        self.assertEqual(events[-1][2]["buttons"],0)

    def test_view_uses_research_specific_artwork_instead_of_generic_watch(self):
        from atcommander import automatic_ads
        evaluate=Mock()
        def surface(prep,classifier):return classifier("synthetic-shot",evaluate),CANVAS
        with patch.object(automatic_ads,"_view",side_effect=surface),patch.object(automatic_ads,"classify_pixels",return_value={}) as classify:
            ads._view({"synthetic":True})
        templates=classify.call_args.kwargs["templates"]
        self.assertIn("research_boost_description",templates)
        self.assertIn("research_watch_button",templates)
        self.assertNotIn("watch_button",templates)

    def test_native_non_tv_slot_cannot_navigate_or_play(self):
        live=snapshot();live["user_tasks"]["201"]["slot_index"]=4
        live["research_ad_speed_ups"][0]["slot_index"]=4
        client=SimpleNamespace(init=Mock(return_value=live))
        with patch.object(ad_ui,"prepare_ad_game_ui",return_value={"ok":True}),patch.object(ads,"_click") as click:
            result=ads.play_research_ad(client)
        self.assertFalse(result["ok"])
        click.assert_not_called()


if __name__=="__main__":unittest.main()
