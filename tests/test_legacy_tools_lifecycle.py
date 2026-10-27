"""Legacy Tools lifetimes without launching browsers, recorders or game calls."""
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from atcommander.app import ATCommanderApp
from atcommander.command_center import CommandCenterMixin
from test_browser_action_overlap import application,alive_worker


def tools_app():
    app=application();app.learn_session=None;app.learn_key=None;app.settings={}
    app.learning_browser=Mock();app.learning_browser.ensure_open.return_value=(True,"Fixture ready")
    app.learn_browser_status=Mock();app.learn_status=Mock();app.siege_learn_btn=Mock()
    app._log=Mock();app._diag_log=Mock();app._event_log=Mock();app._save_settings=Mock()
    app._research_event=lambda _event:False;app._command_event=lambda _event:False
    app._refresh_protocol_status=Mock()
    app.learn_tree=Mock();app.learn_tree.selection.return_value=["fixture"]
    app.learn_tree.item.return_value=["rumble"]
    return app


def event_of(app,kind):
    event=app.events.get(timeout=1)
    while event["kind"]!=kind:event=app.events.get(timeout=1)
    return event


class LegacyToolsLifecycleTests(unittest.TestCase):
    def test_all_remaining_legacy_browser_launchers_guard_before_preparation(self):
        for field in ("ad_thread","game_panel_thread","research_thread","command_busy","arena_runner","learn_session"):
            for method,args in (("teach_play_now",()),("start_learning",()),("_run_swole_protocol_lab",()),
                                ("_arm_live_combat_capture",("swole_battle",)),("_resize_game_panel",())):
                app=tools_app()
                value=alive_worker() if field.endswith("thread") else SimpleNamespace(running=True) if field.endswith("runner") else True
                setattr(app,field,value)
                with self.subTest(field=field,method=method),patch("atcommander.app.threading.Thread") as create, \
                     patch("atcommander.app.messagebox.showinfo"),patch("atcommander.embedded_browser.resize_browser") as resize:
                    getattr(ATCommanderApp,method)(app,*args)
                create.assert_not_called();resize.assert_not_called();app.learning_browser.ensure_open.assert_not_called()

    def test_teach_play_keeps_worker_until_final_save_event(self):
        app=tools_app()
        with patch("atcommander.app.begin_play_click_calibration",return_value={"ok":True}) as arm, \
             patch("atcommander.app.finish_play_click_calibration",return_value={"ok":True,"click":{"x":.1,"y":.2}}), \
             patch("atcommander.app.messagebox.showinfo"):
            ATCommanderApp.teach_play_now(app)
            try:
                ready=event_of(app,"play_click_ready")
                self.assertTrue(app.ad_thread.is_alive());self.assertTrue(CommandCenterMixin._browser_action_busy(app))
                ATCommanderApp._handle_event(app,ready)
                self.assertFalse(ready["completion"].is_set());app._save_settings.assert_not_called()
                captured=event_of(app,"play_click_captured")
                ATCommanderApp._handle_event(app,captured);app.ad_thread.join(1)
                self.assertFalse(app.ad_thread.is_alive());app._save_settings.assert_called_once()
                self.assertEqual(app.settings["ad_play_click"],{"x":.1,"y":.2})
                self.assertFalse(arm.call_args.kwargs["should_stop"]())
            finally:app.ad_stop.set();app.ad_calibration_done.set();app.ad_thread.join(1)

    def test_stopped_teach_capture_is_not_saved_from_queued_event(self):
        app=tools_app();done=threading.Event();app.ad_calibration_done=done
        event={"kind":"play_click_captured","completion":done,"result":{"ok":True,"click":{"x":1,"y":2}}}
        for state in ("stopped","already_done","replaced"):
            app.ad_stop.clear();done.clear();app.ad_calibration_done=done
            if state=="stopped":app.ad_stop.set()
            if state=="already_done":done.set()
            if state=="replaced":app.ad_calibration_done=threading.Event()
            with self.subTest(state=state),patch("atcommander.app.messagebox.showinfo"):
                ATCommanderApp._handle_event(app,event)
            self.assertEqual(app.settings,{});app._save_settings.assert_not_called()

    def test_learning_start_tracks_startup_and_entire_recorder_session(self):
        app=tools_app();session=Mock();session.start.return_value="fixture.trace"
        with patch("atcommander.app.LearnSession",return_value=session) as create,patch("atcommander.app.install_profile") as install:
            ATCommanderApp.start_learning(app)
            try:
                ready=event_of(app,"learning_capture_ready")
                self.assertTrue(app.ad_thread.is_alive());create.assert_called_once_with("rumble")
                ATCommanderApp._handle_event(app,ready)
                self.assertIs(app.learn_session,session);self.assertEqual(app.learn_key,"rumble")
                self.assertTrue(CommandCenterMixin._browser_action_busy(app))
                with patch("atcommander.command_center.messagebox.showinfo"):
                    self.assertTrue(CommandCenterMixin._command_blocks_runner(app))
                ATCommanderApp.stop_all(app);app.ad_thread.join(1)
                self.assertFalse(app.ad_thread.is_alive());session.stop.assert_called_once()
                ATCommanderApp._handle_event(app,event_of(app,"learning_capture_stopped"))
                self.assertIsNone(app.learn_session);self.assertIsNone(app.learn_key);install.assert_not_called()
            finally:app.ad_stop.set();app.learn_capture_done.set();app.ad_thread.join(1)

    def test_stop_during_learning_browser_start_never_starts_recorder(self):
        app=tools_app();thread=Mock()
        app.learning_browser.ensure_open.side_effect=lambda:(app.ad_stop.set(),(True,"Fixture ready"))[1]
        with patch("atcommander.app.threading.Thread",return_value=thread) as worker,patch("atcommander.app.LearnSession") as session:
            ATCommanderApp.start_learning(app);worker.call_args.kwargs["target"]()
        self.assertIs(app.ad_thread,thread);session.assert_not_called();self.assertTrue(app.learn_capture_done.is_set())

    def test_normal_finish_stops_recorder_and_installs_once(self):
        app=tools_app();session=Mock();session.start.return_value="fixture.trace";session.stop.return_value="fixture.trace"
        with patch("atcommander.app.LearnSession",return_value=session), \
             patch("atcommander.app.install_profile",return_value=True) as install,patch("atcommander.app.messagebox.showinfo"):
            ATCommanderApp.start_learning(app)
            try:
                ATCommanderApp._handle_event(app,event_of(app,"learning_capture_ready"))
                ATCommanderApp.finish_learning(app);app.ad_thread.join(1)
                self.assertFalse(app.ad_thread.is_alive());session.stop.assert_called_once()
                install.assert_called_once_with("rumble","fixture.trace")
                self.assertIsNone(app.learn_session);self.assertTrue(app.learn_capture_done.is_set())
            finally:app.ad_stop.set();app.learn_capture_done.set();app.ad_thread.join(1)

    def test_stopped_finish_cannot_install_general_or_siege_capture(self):
        for siege in (False,True):
            app=tools_app();app.learn_session=Mock();app.learn_session.stop.return_value="fixture.trace"
            app.learn_key="siege" if siege else "rumble";app.learn_capture_done=threading.Event();app.ad_stop.set()
            with self.subTest(siege=siege),patch("atcommander.app.install_profile") as install, \
                 patch("atcommander.app.install_siege_flow_profile") as flow:
                (ATCommanderApp.toggle_siege_learn if siege else ATCommanderApp.finish_learning)(app)
            install.assert_not_called();flow.assert_not_called();self.assertIsNone(app.learn_session)
            self.assertTrue(app.learn_capture_done.is_set())

    def test_stale_learning_ready_event_never_publishes_recorder(self):
        app=tools_app();app.learn_capture_done=threading.Event();old=threading.Event();old.set()
        ATCommanderApp._handle_event(app,{"kind":"learning_capture_ready","ok":True,"session":Mock(),"key":"rumble","completion":old})
        self.assertIsNone(app.learn_session);app.learn_status.set.assert_not_called()

    def test_siege_learning_reuses_tracked_capture_start(self):
        app=tools_app();worker=Mock()
        with patch("atcommander.app.threading.Thread",return_value=worker):ATCommanderApp.toggle_siege_learn(app)
        self.assertIs(app.ad_thread,worker);worker.start.assert_called_once()

    def test_protocol_lab_is_tracked_and_passes_stop(self):
        app=tools_app();worker=Mock()
        with patch("atcommander.app.threading.Thread",return_value=worker) as create, \
             patch("atcommander.app.run_swole_signature_lab",return_value="fixture.report") as run:
            ATCommanderApp._run_swole_protocol_lab(app);self.assertIs(app.ad_thread,worker)
            create.call_args.kwargs["target"]()
        self.assertFalse(run.call_args.kwargs["should_stop"]());app.ad_stop.set();self.assertTrue(run.call_args.kwargs["should_stop"]())

    def test_repair_capture_cannot_save_a_result_after_stop(self):
        app=tools_app();worker=Mock()
        def capture(*_args,**_kwargs):
            app.ad_stop.set();return {"ok":True,"action_fields":{"challenge_id":123}}
        with patch("atcommander.app.threading.Thread",return_value=worker) as create, \
             patch("atcommander.app.capture_next_successful_api_request",side_effect=capture) as listen, \
             patch("atcommander.app.save_profiles") as save,patch("atcommander.app.messagebox.showinfo"):
            ATCommanderApp._arm_live_combat_capture(app,"swole_battle")
            self.assertIs(app.ad_thread,worker);create.call_args.kwargs["target"]()
        self.assertTrue(listen.call_args.kwargs["should_stop"]());save.assert_not_called()

    def test_repair_stop_before_work_never_opens_or_shows_browser(self):
        app=tools_app();worker=Mock()
        with patch("atcommander.app.threading.Thread",return_value=worker) as create,patch("atcommander.app.messagebox.showinfo"):
            ATCommanderApp._arm_live_combat_capture(app,"challenge_battle")
            app.ad_stop.set();create.call_args.kwargs["target"]()
        app.learning_browser.ensure_open.assert_not_called();app.learning_browser.show.assert_not_called()

    def test_repair_late_stop_before_profile_write_preserves_saved_template(self):
        app=tools_app();worker=Mock()
        def profiles():app.ad_stop.set();return {}
        with patch("atcommander.app.threading.Thread",return_value=worker) as create, \
             patch("atcommander.app.capture_next_successful_api_request",return_value={"ok":True,"action_fields":{"challenge_id":123}}), \
             patch("atcommander.app.load_profiles",side_effect=profiles),patch("atcommander.app.save_profiles") as save, \
             patch("atcommander.app.messagebox.showinfo"):
            ATCommanderApp._arm_live_combat_capture(app,"swole_battle");create.call_args.kwargs["target"]()
        save.assert_not_called()


if __name__=="__main__":unittest.main()
