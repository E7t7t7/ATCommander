"""Offline launch-order checks; no browser, account, or resource operations."""
from queue import Queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from contextlib import ExitStack

from atcommander.app import ATCommanderApp
from atcommander.command_center import CommandCenterMixin
from atcommander.research_center import ResearchCenterMixin


def alive_worker(alive=True):
    return SimpleNamespace(is_alive=lambda:alive)


def application():
    specs={"read":SimpleNamespace(action=False),"change":SimpleNamespace(action=True)}
    return SimpleNamespace(
        _closing=False,command_busy=False,command_mutating=False,research_busy=False,
        ad_thread=None,game_panel_thread=None,research_thread=None,
        arena_runner=None,event_runner=None,adventure_runner=None,swole_runner=None,
        command_schema=SimpleNamespace(spec=lambda name:specs[name]),command_stop=threading.Event(),
        command_run_button=Mock(),command_status=Mock(),events=Queue(),
        game_panel_attached=False,game_panel_stop=threading.Event(),game_panel_status=Mock(),
        _show_game_panel=Mock(),ad_stop=threading.Event(),research_auto=False,
        research_next=0,research_status=Mock(),_research_values=Mock(return_value={}),
        _routine_stop=Mock(),_research_pause=Mock())


class BrowserActionOverlapTests(unittest.TestCase):
    def busy_cases(self, *, commands=False, runners=False):
        cases=[]
        for name in ("game_panel_thread","ad_thread","research_thread"):
            app=application();setattr(app,name,alive_worker());cases.append((name,app))
        app=application();app.research_busy=True;cases.append(("research_busy",app))
        if commands:
            app=application();app.command_busy=True;cases.append(("command_busy",app))
        if runners:
            for name in ("arena_runner","event_runner","adventure_runner","swole_runner"):
                app=application();setattr(app,name,SimpleNamespace(running=True));cases.append((name,app))
        return cases

    def test_helper_tolerates_missing_optional_mixin_state(self):
        self.assertFalse(CommandCenterMixin._browser_action_busy(SimpleNamespace()))
        self.assertFalse(CommandCenterMixin._browser_action_busy(SimpleNamespace(
            ad_thread=alive_worker(False),game_panel_thread=alive_worker(False),research_thread=alive_worker(False))))
        self.assertTrue(CommandCenterMixin._browser_action_busy(SimpleNamespace(_closing=True)))

    def test_stop_flag_does_not_release_an_unsettled_browser_worker(self):
        app=application();app.ad_thread=alive_worker();app.ad_stop.set()
        self.assertTrue(CommandCenterMixin._browser_action_busy(app))
        app.ad_thread=alive_worker(False)
        self.assertFalse(CommandCenterMixin._browser_action_busy(app))

    def test_manual_ads_wait_for_all_existing_automation_and_startup_workers(self):
        for name,app in self.busy_cases(commands=True,runners=True):
            with self.subTest(worker=name),patch("atcommander.app.threading.Thread") as create, \
                 patch("atcommander.app.attempt_legitimate_ad_ui") as play, \
                 patch("atcommander.app.messagebox.showinfo"):
                ATCommanderApp.watch_ads(app)
            create.assert_not_called();play.assert_not_called()
            self.assertFalse(app.ad_stop.is_set())

    def test_showing_attached_panel_during_ad_never_prepares_or_settles_again(self):
        app=application();app.game_panel_attached=True;app.ad_thread=alive_worker()
        with patch("atcommander.app.prepare_game_ui") as prepare, \
             patch("atcommander.app.threading.Thread") as create, \
             patch("atcommander.startup_popups.settle_startup_popups") as settle:
            ATCommanderApp._open_game_panel(app)
        app._show_game_panel.assert_called_once_with()
        prepare.assert_not_called();create.assert_not_called();settle.assert_not_called()
        app.game_panel_status.set.assert_not_called()

    def test_showing_panel_during_preparation_or_playback_starts_no_second_worker(self):
        for name in ("game_panel_thread","ad_thread"):
            app=application();setattr(app,name,alive_worker())
            with self.subTest(worker=name),patch("atcommander.app.threading.Thread") as create:
                ATCommanderApp._open_game_panel(app)
            app._show_game_panel.assert_called_once_with();create.assert_not_called()
            app.game_panel_status.set.assert_not_called()

    def test_cold_panel_waits_for_account_and_battle_workers(self):
        for name,app in self.busy_cases(commands=True,runners=True):
            if name in {"game_panel_thread","ad_thread"}:continue
            with self.subTest(worker=name),patch("atcommander.app.threading.Thread") as create:
                ATCommanderApp._open_game_panel(app)
            create.assert_not_called()
            self.assertIn("Wait",app.game_panel_status.set.call_args.args[0])

    def test_idle_cold_panel_starts_one_tracked_worker(self):
        app=application();worker=Mock()
        with patch("atcommander.app.threading.Thread",return_value=worker):
            ATCommanderApp._open_game_panel(app)
        self.assertIs(app.game_panel_thread,worker);worker.start.assert_called_once()
        app._show_game_panel.assert_called_once_with()

    def test_mutating_commands_cannot_start_over_browser_research_or_runners(self):
        for name,app in self.busy_cases(runners=True):
            with self.subTest(worker=name),patch("atcommander.command_center.threading.Thread") as create, \
                 patch("atcommander.command_center.messagebox.showinfo"):
                self.assertFalse(CommandCenterMixin._command_launch(app,[("change",{})],False))
            create.assert_not_called();self.assertFalse(app.command_busy)
            app.command_run_button.configure.assert_not_called()

    def test_read_command_retains_availability_during_ad_or_runner(self):
        for name in ("ad_thread","arena_runner"):
            app=application();setattr(app,name,alive_worker() if name=="ad_thread" else SimpleNamespace(running=True))
            worker=Mock()
            with self.subTest(worker=name),patch("atcommander.command_center.threading.Thread",return_value=worker):
                self.assertTrue(CommandCenterMixin._command_launch(app,[("read",{})],False))
            worker.start.assert_called_once();self.assertFalse(app.command_mutating)

    def test_direct_command_launch_cannot_replace_busy_or_closing_worker(self):
        for field in ("command_busy","_closing"):
            app=application();setattr(app,field,True)
            with self.subTest(field=field),patch("atcommander.command_center.threading.Thread") as create:
                self.assertFalse(CommandCenterMixin._command_launch(app,[("read",{})],False))
            create.assert_not_called()

    def test_research_launch_checks_overlap_before_saving_settings(self):
        for name,app in self.busy_cases(commands=True,runners=True):
            for preview in (True,False):
                with self.subTest(worker=name,preview=preview),patch("atcommander.research_center.threading.Thread") as create, \
                     patch("atcommander.research_center.messagebox.showinfo"):
                    ResearchCenterMixin._research_launch(app,preview)
                create.assert_not_called();app._research_values.assert_not_called()
                self.assertFalse(app.research_auto)

    def test_research_auto_tick_defers_without_repeated_dialogs(self):
        app=application();app.research_auto=True;app.ad_thread=alive_worker();app._research_launch=Mock()
        with patch("atcommander.research_center.time.monotonic",return_value=100), \
             patch("atcommander.research_center.messagebox.showinfo") as prompt:
            ResearchCenterMixin._research_tick(app)
        self.assertEqual(app.research_next,105);app._research_launch.assert_not_called();prompt.assert_not_called()

    def test_repeating_mutating_routine_waits_for_browser_without_dialog(self):
        app=application();app.routine_enabled=True;app.routine_next=0;app.routine_steps=[{"command":"change","args":{}}]
        app.routine_state=Mock();app.subscription_busy=True;app.ad_thread=alive_worker();app._command_launch=Mock()
        with patch("atcommander.command_center.time.monotonic",return_value=100), \
             patch("atcommander.command_center.messagebox.showinfo") as prompt:
            CommandCenterMixin._command_tick(app)
        self.assertEqual(app.routine_next,110);app._command_launch.assert_not_called();prompt.assert_not_called()
        self.assertIn("Waiting",app.routine_state.set.call_args.args[0])

    def test_blocked_manual_routine_is_not_armed(self):
        app=application();app.routine_steps=[{"command":"change","args":{}}]
        app.routine_enabled=False;app.routine_interval=SimpleNamespace(get=lambda:"300")
        app._routine_sync_daily=Mock();app._routine_status=Mock();app._command_launch=Mock()
        app.command_schema.spec=lambda _name:SimpleNamespace(action=True,spends_resources=True,parse_args=lambda args:args)
        app.ad_thread=alive_worker()
        with patch("atcommander.command_center.messagebox.showinfo"), \
             patch("atcommander.command_center.messagebox.askyesno") as approve:
            CommandCenterMixin._routine_start(app,True)
        self.assertFalse(app.routine_enabled);app._command_launch.assert_not_called();approve.assert_not_called()

    def test_runner_blocks_browser_and_research_but_preserves_runner_concurrency(self):
        for name,app in self.busy_cases():
            with self.subTest(worker=name),patch("atcommander.command_center.messagebox.showinfo"):
                self.assertTrue(CommandCenterMixin._command_blocks_runner(app))
        app=application();app.arena_runner=SimpleNamespace(running=True)
        with patch("atcommander.command_center.messagebox.showinfo") as prompt:
            self.assertFalse(CommandCenterMixin._command_blocks_runner(app))
        prompt.assert_not_called()

    def test_mutating_command_blocks_runner_but_read_command_does_not(self):
        app=application();app.command_busy=True
        with patch("atcommander.command_center.messagebox.showinfo"):
            self.assertFalse(CommandCenterMixin._command_blocks_runner(app))
            app.command_mutating=True
            self.assertTrue(CommandCenterMixin._command_blocks_runner(app))

    def test_all_runner_entrypoints_stop_before_settings_or_api_on_browser_overlap(self):
        for method,args in (("start_run",()),("start_event",("Siege",)),("start_challenge",()),
                            ("start_swole",()),("start_adventure",())):
            app=application();app.game_panel_thread=alive_worker()
            app._command_blocks_runner=lambda:CommandCenterMixin._command_blocks_runner(app)
            app._save_settings=Mock()
            with self.subTest(method=method),patch("atcommander.command_center.messagebox.showinfo"):
                getattr(ATCommanderApp,method)(app,*args)
            app._save_settings.assert_not_called()

    def test_legacy_ad_tools_wait_for_active_workers_and_battles(self):
        for name,app in self.busy_cases(commands=True,runners=True):
            for method,args in (("test_ad_clicks",()),("calibrate_ads",("research",))):
                with self.subTest(worker=name,method=method),patch("atcommander.app.threading.Thread") as create, \
                     patch("atcommander.app.messagebox.showinfo"):
                    getattr(ATCommanderApp,method)(app,*args)
                create.assert_not_called()

    def test_legacy_test_worker_is_tracked_and_passes_stop(self):
        app=application();worker=Mock()
        with patch("atcommander.app.threading.Thread",return_value=worker) as create, \
             patch("atcommander.app.test_calibrated_ad_ui",return_value={"ok":False}) as play:
            ATCommanderApp.test_ad_clicks(app)
            self.assertIs(app.ad_thread,worker)
            create.call_args.kwargs["target"]()
        worker.start.assert_called_once()
        stop=play.call_args.kwargs["should_stop"]
        self.assertFalse(stop());ATCommanderApp.stop_all(app);self.assertTrue(stop())

    def test_close_learning_browser_never_cleans_up_active_work(self):
        for name,app in self.busy_cases(commands=True,runners=True):
            app.learn_session=None;app.learning_browser=Mock()
            with self.subTest(worker=name),patch("atcommander.embedded_browser.close_owned_browser") as close, \
                 patch("atcommander.ad_browser_focus.release_all_game_input_focus") as release, \
                 patch("atcommander.app.messagebox.showinfo"):
                ATCommanderApp._close_learning_browser(app)
            release.assert_not_called();close.assert_not_called();app.learning_browser.close.assert_not_called()
            app._show_game_panel.assert_not_called()

    def test_close_idle_learning_browser_preserves_profile_and_releases_focus(self):
        app=application();app.learn_session=None;app.learning_browser=Mock()
        app.learn_browser_status=Mock();app._diag_log=Mock();order=[]
        with patch("atcommander.embedded_browser.close_owned_browser",side_effect=lambda:order.append("browser")), \
             patch("atcommander.ad_browser_focus.release_all_game_input_focus",side_effect=lambda:order.append("focus")):
            ATCommanderApp._close_learning_browser(app)
        self.assertEqual(order,["focus","browser"]);app.learning_browser.close.assert_called_once()
        app._show_game_panel.assert_called_once_with(False)

    def metadata_fixture(self,app,stack):
        app.settings={};client=Mock()
        for method in ("init","get_user_account","get_exact_decks","get_hero_squad","get_boost_level",
                       "card_catalog","item_catalog","hero_catalog","campaign_catalog","mission_catalog",
                       "get_store_snapshot","get_guild_siege_status"):
            getattr(client,method).return_value={}
        stack.enter_context(patch("atcommander.app.ATClient",return_value=client))
        stack.enter_context(patch("atcommander.app.load_credentials"))
        stack.enter_context(patch("atcommander.app.discover_campaign_markers",return_value=[{"id":"9040"}]))
        stack.enter_context(patch("atcommander.app.build_adventure_campaign_rows",return_value=[]))
        return client

    def test_adventure_explicit_refresh_during_work_keeps_reads_and_defers_browser(self):
        for name,app in self.busy_cases(commands=True,runners=True):
            original=app.game_panel_thread
            with self.subTest(worker=name),ExitStack() as stack:
                client=self.metadata_fixture(app,stack);worker=Mock()
                create=stack.enter_context(patch("atcommander.app.threading.Thread",return_value=worker))
                prepare=stack.enter_context(patch("atcommander.app.prepare_game_ui"))
                ATCommanderApp._refresh_account_metadata(app,force_adventure_static=True)
                self.assertIs(app.game_panel_thread,original)
                create.call_args.kwargs["target"]()
                client.init.assert_called_once();prepare.assert_not_called()
            self.assertTrue(any(event["kind"]=="metadata" for event in list(app.events.queue)))

    def test_adventure_idle_explicit_refresh_reserves_and_checks_stop_before_browser(self):
        for stop in (False,True):
            app=application();worker=Mock();worker.is_alive.return_value=True
            with self.subTest(stop=stop),ExitStack() as stack:
                client=self.metadata_fixture(app,stack)
                create=stack.enter_context(patch("atcommander.app.threading.Thread",return_value=worker))
                prepare=stack.enter_context(patch("atcommander.app.prepare_game_ui",return_value={"ok":True}))
                ATCommanderApp._refresh_account_metadata(app,force_adventure_static=True)
                self.assertIs(app.game_panel_thread,worker)
                self.assertTrue(CommandCenterMixin._browser_action_busy(app))
                self.assertFalse(CommandCenterMixin._browser_action_busy(app,ignore_thread=worker))
                if stop:app.game_panel_stop.set()
                create.call_args.kwargs["target"]()
                if stop:prepare.assert_not_called()
                else:
                    prepare.assert_called_once()
                    self.assertFalse(prepare.call_args.kwargs["should_stop"]())
                    app.game_panel_stop.set();self.assertTrue(prepare.call_args.kwargs["should_stop"]())
            self.assertTrue(any(event["kind"]=="metadata" for event in list(app.events.queue)))

    def test_ordinary_metadata_refresh_remains_unreserved_and_never_prepares_browser(self):
        app=application();app.arena_runner=SimpleNamespace(running=True)
        with ExitStack() as stack:
            client=self.metadata_fixture(app,stack);worker=Mock()
            create=stack.enter_context(patch("atcommander.app.threading.Thread",return_value=worker))
            prepare=stack.enter_context(patch("atcommander.app.prepare_game_ui"))
            ATCommanderApp._refresh_account_metadata(app)
            self.assertIsNone(app.game_panel_thread);create.call_args.kwargs["target"]()
            prepare.assert_not_called();client.init.assert_called_once()

    def test_adventure_reserved_read_observes_late_work_before_browser_preparation(self):
        app=application();worker=Mock();worker.is_alive.return_value=True
        with ExitStack() as stack:
            self.metadata_fixture(app,stack)
            create=stack.enter_context(patch("atcommander.app.threading.Thread",return_value=worker))
            prepare=stack.enter_context(patch("atcommander.app.prepare_game_ui"))
            ATCommanderApp._refresh_account_metadata(app,force_adventure_static=True)
            # Read-only commands can still start; the optional browser startup
            # must defer rather than crossing over their in-flight account read.
            app.command_busy=True
            create.call_args.kwargs["target"]();prepare.assert_not_called()

    def test_calibration_reservation_survives_preparation_until_capture_finishes(self):
        app=application();app.settings={};app._log=Mock()
        app._research_event=lambda _event:False;app._command_event=lambda _event:False
        with patch("atcommander.app.begin_ad_click_calibration",return_value={"ok":True}) as arm, \
             patch("atcommander.app.finish_ad_click_calibration",return_value={"ok":True,"sequence":[{},{}],"clicks":2}) as finish, \
             patch("atcommander.app.save_settings") as save:
            ATCommanderApp.calibrate_ads(app,"research")
            try:
                ready=app.events.get(timeout=1)
                while ready["kind"]!="ad_calibration_ready":ready=app.events.get(timeout=1)
                self.assertTrue(app.ad_thread.is_alive());self.assertTrue(CommandCenterMixin._browser_action_busy(app))
                def dialog(*_args):
                    self.assertTrue(app.ad_thread.is_alive())
                    self.assertTrue(CommandCenterMixin._browser_action_busy(app))
                with patch("atcommander.app.messagebox.showinfo",side_effect=dialog):
                    ATCommanderApp._handle_event(app,ready)
                app.ad_thread.join(1)
                self.assertFalse(app.ad_thread.is_alive());save.assert_called_once();finish.assert_called_once()
                self.assertEqual(app.settings["rewarded_ad_sequences"]["research"],[{},{}])
                self.assertFalse(arm.call_args.kwargs["should_stop"]())
            finally:
                app.ad_stop.set();app.ad_calibration_done.set();app.ad_thread.join(1)

    def test_stopped_or_stale_calibration_never_saves_clicks(self):
        for phase in ("before_dialog","during_dialog","after_capture","stale"):
            app=application();app.settings={"ad_click_sequence":[{"old":True}]};app._log=Mock()
            app._research_event=lambda _event:False;app._command_event=lambda _event:False
            done=threading.Event();app.ad_calibration_done=done
            event={"kind":"ad_calibration_ready","result":{"ok":True},"completion":done}
            if phase=="before_dialog":app.ad_stop.set()
            if phase=="stale":event["completion"]=threading.Event();event["completion"].set()
            def dialog(*_args):
                if phase=="during_dialog":app.ad_stop.set()
            def capture():
                if phase=="after_capture":app.ad_stop.set()
                return {"ok":True,"sequence":[{},{}],"clicks":2}
            with self.subTest(phase=phase),patch("atcommander.app.messagebox.showinfo",side_effect=dialog) as prompt, \
                 patch("atcommander.app.finish_ad_click_calibration",side_effect=capture) as finish, \
                 patch("atcommander.app.save_settings") as save:
                ATCommanderApp._handle_event(app,event)
            save.assert_not_called();self.assertEqual(app.settings,{"ad_click_sequence":[{"old":True}]})
            if phase in {"before_dialog","stale"}:prompt.assert_not_called()
            if phase!="after_capture":finish.assert_not_called()

    def test_stop_releases_waiting_calibration_before_queued_dialog_can_run(self):
        app=application();app._research_event=lambda _event:False;app._command_event=lambda _event:False
        with patch("atcommander.app.begin_ad_click_calibration",return_value={"ok":True}):
            ATCommanderApp.calibrate_ads(app)
            try:
                ready=app.events.get(timeout=1)
                while ready["kind"]!="ad_calibration_ready":ready=app.events.get(timeout=1)
                ATCommanderApp.stop_all(app);app.ad_thread.join(1)
                self.assertFalse(app.ad_thread.is_alive());self.assertTrue(ready["completion"].is_set())
                # A later operation may clear Stop; the old completion token
                # must still prohibit opening an obsolete calibration dialog.
                app.ad_stop.clear()
                with patch("atcommander.app.messagebox.showinfo") as prompt:
                    ATCommanderApp._handle_event(app,ready)
                prompt.assert_not_called()
            finally:app.ad_stop.set();app.ad_calibration_done.set();app.ad_thread.join(1)


if __name__=="__main__":unittest.main()
