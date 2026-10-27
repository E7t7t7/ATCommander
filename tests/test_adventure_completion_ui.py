"""Offline completion-mode GUI tests; no browser or Adventure resources."""
from contextlib import ExitStack
from pathlib import Path
from queue import Queue
import tempfile
import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace

from atcommander import app as app_module,settings
from atcommander.app import ATCommanderApp,ADVENTURE_MODES
from atcommander import adventure_completion


class Value:
    def __init__(self,value=None,**_kwargs):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value


def build_app(saved=None):
    app=object.__new__(ATCommanderApp);app.settings=saved or {};app.adventure_tab=Mock()
    app._logbox=Mock();app._append=Mock();app.events=Queue();app._closing=False
    app._research_event=lambda _event:False;app._command_event=lambda _event:False
    app.arena_runner=None;app.event_runner=None;app.adventure_runner=None;app.swole_runner=None
    with ExitStack() as stack:
        for name in ("StringVar","BooleanVar","IntVar"):
            stack.enter_context(patch.object(app_module.tk,name,side_effect=Value))
        for name in ("Frame","Label","Combobox","Button","Entry","Spinbox","Checkbutton","Treeview","Scrollbar"):
            stack.enter_context(patch.object(app_module.ttk,name))
        ATCommanderApp._build_adventure(app)
    app.adventure_island_tree.get_children.return_value=[]
    return app


def scan_fixture():
    stage={"mission":9258,"stage":1,"wins":9,"target_wins":12,"energy_cost":1,"level":9,"available":True,"complete":False}
    return {"target":"mastery","completed_stages":3,"total_stages":6,"progress_percent":75,
        "all_complete":False,"warnings":[],"next_mission":{**stage,"campaign_id":"9040","island":"Fixture Halloween"},
        "islands":[{"id":"1","name":"Old Island","state":"Complete","progress_percent":100,"completed_stages":3,"total_stages":3,"missions":[]},
                   {"id":"9040","name":"Fixture Halloween","state":"Ready","progress_percent":50,"completed_stages":0,"total_stages":3,"missions":[stage]}]}


class AdventureCompletionUITests(unittest.TestCase):
    def test_old_settings_default_to_original_farm_and_preserve_main_controls(self):
        app=build_app()
        self.assertEqual(app.adventure_mode.get(),ADVENTURE_MODES[0])
        self.assertEqual(app.adventure_mission_id.get(),"107")
        self.assertEqual(app.adventure_kind.get(),"Main Adventure")
        self.assertEqual(app.adventure_island_num.get(),3);self.assertEqual(app.adventure_stage_num.get(),1)
        self.assertEqual(app.adventure_completion_goal.get(),"100% mastery")

    def test_saved_completion_mode_and_default_mission_restore_independently(self):
        app=build_app({"adventure_complete_islands":True,"adventure_completion_target":"progress",
            "adventure_farm_kind":"Custom / Event mission ID","adventure_mission_id":"9258",
            "adventure_island":23,"adventure_stage":2})
        self.assertEqual(app.adventure_mode.get(),ADVENTURE_MODES[1])
        self.assertEqual(app.adventure_completion_goal.get(),"Stage completion")
        self.assertEqual(app.adventure_mission_id.get(),"9258")
        self.assertEqual(app.adventure_kind.get(),"Custom / Event mission ID")
        self.assertEqual(app.adventure_island_num.get(),23);self.assertEqual(app.adventure_stage_num.get(),2)

    def test_malformed_mode_or_goal_never_changes_default_farm_strategy(self):
        app=build_app({"adventure_complete_islands":"true","adventure_completion_target":"unknown",
                       "adventure_island":"invalid","adventure_stage":9})
        self.assertEqual(app.adventure_mode.get(),ADVENTURE_MODES[0])
        self.assertEqual(app.adventure_completion_goal.get(),"100% mastery")
        self.assertEqual(app.adventure_island_num.get(),3);self.assertEqual(app.adventure_stage_num.get(),1)

    def test_mode_round_trip_preserves_farm_and_other_preferences(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(settings,"APP_DIR",Path(directory)),patch.object(settings,"FILE",Path(directory)/"settings.json"):
            settings.save_settings({"adventure_mission_id":"180","fixture_preference":"keep","adventure_farm_kind":"Custom / Event mission ID"})
            app=build_app(settings.load_settings());app.adventure_mode.set(ADVENTURE_MODES[1])
            app.adventure_completion_goal.set("Stage completion")
            ATCommanderApp._save_adventure_completion_options(app)
            saved=settings.load_settings()
            self.assertIs(saved["adventure_complete_islands"],True)
            self.assertEqual(saved["adventure_completion_target"],"progress")
            self.assertEqual(saved["adventure_mission_id"],"180");self.assertEqual(saved["fixture_preference"],"keep")
            self.assertEqual(app.adventure_mission_id.get(),"180")
            app.adventure_mode.set(ADVENTURE_MODES[0]);ATCommanderApp._save_adventure_completion_options(app)
            self.assertIs(settings.load_settings()["adventure_complete_islands"],False)

    def test_invalid_goal_cannot_be_saved_or_authorize_runner(self):
        app=build_app();app.adventure_completion_goal.set("unknown")
        with patch.object(app_module,"save_settings") as save:
            with self.assertRaisesRegex(ValueError,"Choose"):
                ATCommanderApp._save_adventure_completion_options(app)
        save.assert_not_called()

    def test_catalog_refresh_retains_saved_default_mission_with_new_stage_labels(self):
        app=build_app({"adventure_farm_kind":"Seasonal / Event: Fixture Halloween [Campaign 9040]",
                       "adventure_mission_id":"9259"})
        app.adventure_campaign_rows=[{"id":"9040","name":"Fixture Halloween","missions":[
            {"id":"9258","display":"New Stage 1"},{"id":"9259","display":"New Stage 2"}]}]
        app.adventure_mission_choice.set("Old Stage 2")
        ATCommanderApp._refresh_adventure_campaign_missions(app)
        self.assertEqual(app.adventure_mission_id.get(),"9259")
        self.assertEqual(app.adventure_mission_choice.get(),"New Stage 2")

    def test_read_only_scan_uses_fresh_state_and_catalogs_without_changing_farm(self):
        app=build_app({"adventure_mission_id":"180","adventure_farm_kind":"Custom / Event mission ID"})
        app.arena_runner=SimpleNamespace(running=True);app.swole_runner=SimpleNamespace(running=True)
        client=Mock();client.init.return_value={"result":True,"time":100}
        client.campaign_catalog.return_value={"fixture":"campaign"};client.mission_catalog.return_value={"fixture":"mission"}
        worker=Mock();worker.is_alive.return_value=False
        before=(app.adventure_kind.get(),app.adventure_mission_id.get(),app.adventure_mode.get())
        with patch.object(app_module,"threading") as threads,patch.object(app_module,"ATClient",return_value=client), \
             patch.object(app_module,"load_credentials"),patch.object(app_module,"prepare_game_ui") as browser, \
             patch.object(app_module,"save_settings") as save, \
             patch.object(adventure_completion,"scan_adventure_islands",return_value=scan_fixture()) as plan:
            threads.Thread.return_value=worker
            ATCommanderApp.scan_adventure_islands(app)
            self.assertIs(app.adventure_scan_thread,worker);worker.start.assert_called_once()
            threads.Thread.call_args.kwargs["target"]()
            plan.assert_called_once_with(client.init.return_value,client.campaign_catalog.return_value,client.mission_catalog.return_value,target="mastery")
            events=list(app.events.queue);scan=next(event for event in events if event["kind"]=="adventure_scan")
            ATCommanderApp._handle_event(app,scan)
        client.init.assert_called_once();client.call.assert_not_called();client.get_player_prefs_snapshot.assert_not_called()
        client.apply_selection.assert_not_called();browser.assert_not_called();save.assert_not_called()
        self.assertEqual(before,(app.adventure_kind.get(),app.adventure_mission_id.get(),app.adventure_mode.get()))
        self.assertIn("Fixture Halloween",app.adventure_scan_status.get())
        rows=app.adventure_island_tree.insert.call_args_list
        self.assertEqual(rows[0].kwargs["values"][0],"Fixture Halloween")
        self.assertEqual(rows[0].kwargs["tags"],("pending",));self.assertEqual(rows[1].kwargs["tags"],("complete",))

    def test_duplicate_scan_and_shutdown_cannot_launch_extra_reads(self):
        for state in ("busy","closing"):
            app=build_app()
            if state=="busy":app.adventure_scan_thread=SimpleNamespace(is_alive=lambda:True)
            else:app._closing=True
            with self.subTest(state=state),patch.object(app_module.threading,"Thread") as thread, \
                 patch.object(app_module,"ATClient") as client:
                ATCommanderApp.scan_adventure_islands(app)
            thread.assert_not_called();client.assert_not_called()

    def test_scan_stop_after_state_read_prevents_catalog_requests_and_old_results(self):
        app=build_app();client=Mock();worker=Mock();worker.is_alive.return_value=False
        def read():app.adventure_scan_stop.set();return {"result":True,"time":100}
        client.init.side_effect=read
        with patch.object(app_module.threading,"Thread",return_value=worker) as thread, \
             patch.object(app_module,"ATClient",return_value=client),patch.object(app_module,"load_credentials"), \
             patch.object(adventure_completion,"scan_adventure_islands") as plan:
            ATCommanderApp.scan_adventure_islands(app);thread.call_args.kwargs["target"]()
        client.campaign_catalog.assert_not_called();client.mission_catalog.assert_not_called();plan.assert_not_called()
        self.assertFalse(any(event["kind"]=="adventure_scan" for event in list(app.events.queue)))
        app.adventure_scan_status.set("current")
        ATCommanderApp._handle_event(app,{"kind":"adventure_scan","scan":scan_fixture(),"token":object()})
        self.assertEqual(app.adventure_scan_status.get(),"current")
        ATCommanderApp._handle_event(app,{"kind":"adventure_scan","scan":scan_fixture(),"token":app._adventure_scan_token})
        self.assertEqual(app.adventure_scan_status.get(),"current")

    def test_rejected_scan_read_never_authorizes_completion_preview(self):
        for rejected in (False,0,"0","false"):
            app=build_app();client=Mock();client.init.return_value={"result":rejected};worker=Mock()
            with self.subTest(rejected=rejected),patch.object(app_module.threading,"Thread",return_value=worker) as thread, \
                 patch.object(app_module,"ATClient",return_value=client),patch.object(app_module,"load_credentials"), \
                 patch.object(adventure_completion,"scan_adventure_islands") as plan:
                ATCommanderApp.scan_adventure_islands(app);thread.call_args.kwargs["target"]()
            client.campaign_catalog.assert_not_called();plan.assert_not_called()
            self.assertTrue(any(event["kind"]=="adventure_scan_failed" for event in list(app.events.queue)))

    def test_completion_runner_keeps_original_farm_id_and_passes_goal(self):
        for complete,target,label in ((False,"mastery","100% mastery"),(True,"mastery","100% mastery"),(True,"progress","Stage completion")):
            app=build_app({"adventure_mission_id":"180","adventure_farm_kind":"Custom / Event mission ID"})
            app.adventure_mode.set(ADVENTURE_MODES[int(complete)]);app.adventure_completion_goal.set(label)
            app._command_blocks_runner=Mock(return_value=False);app._adventure_mission_changed=Mock();app._save_settings=Mock()
            app.delay_var=Value(.1);app.battle_delay_var=Value(1.5);app.auto_ads_var=Value(False)
            with self.subTest(complete=complete,target=target),patch.object(app_module,"AdventureRunner") as runner:
                ATCommanderApp.start_adventure(app)
            self.assertEqual(runner.call_args.args[0],180)
            self.assertEqual(runner.call_args.kwargs,{"complete_islands":complete,"completion_target":target})
            runner.return_value.start.assert_called_once();self.assertEqual(app.adventure_mission_id.get(),"180")

    def test_running_target_and_verified_progress_never_edit_default_farm_controls(self):
        app=build_app({"adventure_mission_id":"180","adventure_farm_kind":"Custom / Event mission ID"})
        before=(app.adventure_kind.get(),app.adventure_mission_id.get(),app.adventure_mode.get())
        for event in ({"kind":"adventure_target","mission_id":9258,"island":"Halloween","stage":1,"wins":9,"target_wins":12,"energy_cost":1,"farming":False},
                      {"kind":"adventure_completion_verified","mission_id":9258,"island":"Halloween","stage":1,"wins_before":9,"wins_after":10,"target_wins":12},
                      {"kind":"adventure_farm_resume","mission_id":180},
                      {"kind":"adventure_target","mission_id":180,"farming":True},
                      {"kind":"adventure_paused","message":"Fresh progress unavailable"}):
            ATCommanderApp._handle_event(app,event)
            self.assertEqual(before,(app.adventure_kind.get(),app.adventure_mission_id.get(),app.adventure_mode.get()))
        self.assertIn("paused",app.adventure_completion.get())

    def test_stop_all_cancels_preview_and_close_waits_for_read_worker(self):
        app=build_app();pending=[];order=[];worker=Mock();worker.is_alive.return_value=True;app.adventure_scan_thread=worker
        app.root=SimpleNamespace(title=Mock(),after=lambda _delay,fn:pending.append(fn),destroy=lambda:order.append("destroy"))
        app.learning_browser=Mock()
        with patch("atcommander.embedded_browser.close_owned_browser",return_value={"embedded":False,"reason":"browser_closed"}) as close, \
             patch("atcommander.embedded_browser.configure"),patch("atcommander.ad_browser_focus.release_all_game_input_focus"):
            ATCommanderApp.close_app(app)
            self.assertTrue(app.adventure_scan_stop.is_set());close.assert_not_called()
            worker.is_alive.return_value=False;pending.pop()()
        close.assert_called_once();self.assertEqual(order,["destroy"])


if __name__=="__main__":unittest.main()
