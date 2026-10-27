"""Offline boundaries for the saved Edge session inside Commander."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from atcommander import ad_ui, automatic_ads, embedded_browser, learn_center
from atcommander.app import ATCommanderApp


TARGET = {"id": "synthetic-game", "type": "page",
          "url": learn_center.GAME_URL, "webSocketDebuggerUrl": "synthetic-local-cdp"}
CANVAS = {"x": 0, "y": 0, "width": 480, "height": 340}


class Scheduler:
    def __init__(self, events):
        self.events = events
        self.pending = []
        self.title = Mock()

    def after(self, delay, callback):
        self.pending.append((delay, callback))

    def step(self):
        delay, callback = self.pending.pop(0)
        callback()
        return delay

    def destroy(self):
        self.events.append("destroy")


class EmbeddedPreparationTests(unittest.TestCase):
    def setUp(self):
        self.browser_patch = patch.object(ad_ui, "LearningBrowser")
        self.browser = self.browser_patch.start()
        self.addCleanup(self.browser_patch.stop)
        self.browser.return_value.ensure_open.return_value = (True, "Synthetic browser ready.")
        self.sleep_patch = patch.object(ad_ui.time, "sleep")
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    def test_registered_attach_failure_stops_before_targets_play_and_geometry(self):
        with patch.object(embedded_browser, "get_embedded_state", return_value={"registered": True}), \
             patch.object(embedded_browser, "request_attach", return_value={"embedded": False}) as attach, \
             patch.object(ad_ui, "_stabilize_targets") as stabilize, \
             patch.object(ad_ui, "_fix_window_geometry") as geometry, \
             patch.object(ad_ui, "_eval") as read, \
             patch.object(ad_ui, "_click_play_control") as play:
            result = ad_ui.prepare_game_ui()
        self.assertFalse(result["ok"])
        attach.assert_called_once_with()
        stabilize.assert_not_called()
        geometry.assert_not_called()
        read.assert_not_called()
        play.assert_not_called()

    def test_actual_preparation_reports_successful_embed_and_reuses_existing_canvas(self):
        with patch.object(embedded_browser, "get_embedded_state", return_value={"registered": True}), \
             patch.object(embedded_browser, "request_attach", return_value={"embedded": True}) as attach, \
             patch.object(ad_ui, "_stabilize_targets", return_value=[TARGET]), \
             patch.object(ad_ui, "_eval", return_value={"canvas": dict(CANVAS)}) as read, \
             patch.object(ad_ui, "_click_play_control") as play:
            result = ad_ui.prepare_game_ui()
        self.assertTrue(result["ok"])
        self.assertIs(result["embedded"], True)
        self.assertFalse(result["play_clicked"])
        self.assertEqual(result["canvas"], CANVAS)
        self.assertEqual(result["target"], TARGET)
        attach.assert_called_once_with()
        read.assert_called_once_with(TARGET["webSocketDebuggerUrl"], ad_ui._CANVAS_JS)
        play.assert_not_called()

    def test_standalone_preparation_reports_no_embed_and_does_not_queue_attachment(self):
        with patch.object(embedded_browser, "get_embedded_state", return_value={"registered": False}), \
             patch.object(embedded_browser, "request_attach") as attach, \
             patch.object(ad_ui, "_stabilize_targets", return_value=[TARGET]), \
             patch.object(ad_ui, "_eval", return_value={"canvas": dict(CANVAS)}):
            result = ad_ui.prepare_game_ui()
        self.assertTrue(result["ok"])
        self.assertIs(result["embedded"], False)
        attach.assert_not_called()

    def test_stop_before_preparation_does_not_open_browser(self):
        with patch.object(embedded_browser, "request_attach") as attach:
            result = ad_ui.prepare_game_ui(should_stop=lambda: True)
        self.assertFalse(result["ok"])
        self.assertFalse(result["playback_started"])
        self.browser.assert_not_called()
        attach.assert_not_called()

    def test_browser_open_failure_stops_before_host_or_page_actions(self):
        self.browser.return_value.ensure_open.return_value = (False, "Synthetic unavailable.")
        with patch.object(embedded_browser, "request_attach") as attach, \
             patch.object(ad_ui, "_stabilize_targets") as stabilize:
            result = ad_ui.prepare_game_ui()
        self.assertFalse(result["ok"])
        attach.assert_not_called()
        stabilize.assert_not_called()

    def test_registered_attach_failure_blocks_actual_automatic_boost_boundary(self):
        client = Mock()
        with patch.object(embedded_browser, "get_embedded_state", return_value={"registered": True}), \
             patch.object(embedded_browser, "request_attach", return_value={"embedded": False}), \
             patch.object(automatic_ads, "_view") as view, \
             patch.object(automatic_ads, "_click") as click, \
             patch.object(ad_ui, "_stabilize_targets") as stabilize, \
             patch("atcommander.startup_popups.settle_startup_popups") as settle:
            result = ad_ui._automatic_boost_playback(lambda message: None, client=client, should_stop=lambda: False)
        self.assertFalse(result["ok"])
        view.assert_not_called()
        click.assert_not_called()
        stabilize.assert_not_called()
        settle.assert_not_called()
        client.assert_not_called()

    def test_verified_startup_settling_preserves_embedded_preparation(self):
        prep = {"ok": True, "embedded": True, "target": dict(TARGET), "canvas": dict(CANVAS)}
        client = Mock()
        service = Mock()
        stopped = lambda: False
        with patch.object(ad_ui, "prepare_game_ui", return_value=prep) as prepare, \
             patch("atcommander.utility_commands.build_service", return_value=service) as build, \
             patch("atcommander.startup_popups.settle_startup_popups",
                   return_value={"ok": True, "verified": True}) as settle:
            result = ad_ui.prepare_ad_game_ui(client=client, should_stop=stopped)
        self.assertIs(result, prep)
        prepare.assert_called_once_with(None, wait_for_canvas=60, should_stop=stopped)
        build.assert_called_once_with(client)
        settle.assert_called_once_with(service, prep, should_stop=stopped, log=None)


class EmbeddedGeometryTests(unittest.TestCase):
    def setUp(self):
        self.geometry_patch = patch.object(ad_ui, "_geometry_ready", set())
        self.geometry_patch.start()
        self.addCleanup(self.geometry_patch.stop)

    def test_registered_host_prevents_standalone_bounds_even_after_attach_failure(self):
        with patch.object(embedded_browser, "get_embedded_state",
                          return_value={"registered": True, "embedded": False}), \
             patch.object(ad_ui, "_cdp") as cdp:
            ad_ui._fix_window_geometry(TARGET)
            ad_ui._fix_window_geometry(TARGET)
        cdp.assert_not_called()

    def test_standalone_bounds_set_once_for_reused_target(self):
        with patch.object(embedded_browser, "get_embedded_state", return_value={"registered": False}), \
             patch.object(ad_ui, "_cdp", side_effect=[{"windowId": 7}, {}]) as cdp:
            for _ in range(5):
                ad_ui._fix_window_geometry(TARGET)
        self.assertEqual([call.args[1] for call in cdp.call_args_list],
                         ["Browser.getWindowForTarget", "Browser.setWindowBounds"])
        self.assertEqual(cdp.call_args_list[1].args[2]["bounds"], {
            "left": 24, "top": 24, "width": 640, "height": 720, "windowState": "normal"})

    def test_failed_bounds_are_not_cached_as_success(self):
        with patch.object(embedded_browser, "get_embedded_state", return_value={"registered": False}), \
             patch.object(ad_ui, "_cdp", side_effect=[
                 {"windowId": 7}, RuntimeError("synthetic-unavailable"), {"windowId": 7}, {}]) as cdp:
            ad_ui._fix_window_geometry(TARGET)
            ad_ui._fix_window_geometry(TARGET)
        self.assertEqual(cdp.call_count, 4)
        self.assertIn(TARGET["webSocketDebuggerUrl"], ad_ui._geometry_ready)

    def test_registered_show_and_background_use_host_without_websocket_or_desktop_move(self):
        browser = learn_center.LearningBrowser()
        with patch.object(embedded_browser, "get_embedded_state", return_value={"registered": True}), \
             patch.object(embedded_browser, "request_attach", return_value={"embedded": True}) as attach, \
             patch.object(browser, "_targets") as targets, \
             patch("websocket.create_connection") as socket:
            self.assertTrue(browser.show())
            self.assertTrue(browser.background())
        self.assertEqual(attach.call_count, 2)
        targets.assert_not_called()
        socket.assert_not_called()


class SavedBrowserLaunchTests(unittest.TestCase):
    def launch(self, registered):
        browser = learn_center.LearningBrowser()
        profile = Mock()
        profile.__str__ = Mock(return_value=r"C:\Synthetic\ATCommander\learn_browser_profile")
        with patch.object(browser, "is_running", side_effect=[False, True]), \
             patch.object(browser, "_edge", return_value=r"C:\Synthetic\msedge.exe"), \
             patch.object(browser, "_close_extra_tabs") as close_extra, \
             patch.object(learn_center, "PROFILE_DIR", profile), \
             patch.object(learn_center.time, "sleep"), \
             patch.object(learn_center.subprocess, "Popen") as launch, \
             patch.object(embedded_browser, "get_embedded_state", return_value={"registered": registered}):
            ok, message = browser.ensure_open()
        self.assertTrue(ok)
        profile.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        close_extra.assert_called_once_with()
        self.assertEqual(launch.call_count, 1)
        return launch.call_args.args[0]

    def test_registered_host_launches_offscreen_using_existing_dedicated_profile(self):
        command = self.launch(True)
        self.assertIn("--window-position=-10000,24", command)
        self.assertIn(r"--user-data-dir=C:\Synthetic\ATCommander\learn_browser_profile", command)
        self.assertIn("--remote-debugging-port=9223", command)
        self.assertIn("--app=" + learn_center.GAME_URL, command)
        self.assertIn("--disable-renderer-backgrounding", command)
        self.assertNotIn("--incognito", command)

    def test_standalone_launch_retains_ordinary_compact_bounds(self):
        command = self.launch(False)
        self.assertIn("--window-position=24,24", command)
        self.assertIn("--window-size=640,720", command)

    def test_already_running_saved_session_never_relaunches_or_changes_profile(self):
        browser = learn_center.LearningBrowser()
        with patch.object(browser, "is_running", return_value=True), \
             patch.object(browser, "_edge") as edge, \
             patch.object(learn_center.subprocess, "Popen") as launch, \
             patch.object(learn_center, "PROFILE_DIR") as profile:
            self.assertTrue(browser.ensure_open()[0])
        edge.assert_not_called()
        launch.assert_not_called()
        profile.mkdir.assert_not_called()


class EmbeddedShutdownTests(unittest.TestCase):
    def application(self, active=False):
        events = []
        root = Scheduler(events)
        worker = Mock()
        worker.is_alive.return_value = active
        browser = Mock()
        browser.close.side_effect = lambda: events.append("fallback_close")
        app = SimpleNamespace(
            _closing=False, root=root, research_busy=False, command_busy=False,
            research_thread=None, game_panel_thread=worker,
            arena_runner=None, event_runner=None, adventure_runner=None, swole_runner=None,
            learning_browser=browser, stop_all=lambda: events.append("stop"))
        return app, worker, events

    def test_close_waits_for_game_panel_worker_before_owned_close_and_destroy(self):
        app, worker, events = self.application(active=True)
        def close():
            events.append("owned_close")
            return {"embedded": False, "reason": "browser_closed"}
        def unregister(parent):
            self.assertIsNone(parent)
            self.assertTrue(app._game_panel_closed)
            events.append("unregister")
        with patch.object(embedded_browser, "close_owned_browser", side_effect=close) as owned, \
             patch.object(embedded_browser, "configure", side_effect=unregister), \
             patch("atcommander.ad_browser_focus.release_all_game_input_focus",
                   side_effect=lambda: events.append("release_focus")):
            ATCommanderApp.close_app(app)
            self.assertEqual(events, ["stop"])
            owned.assert_not_called()
            self.assertEqual(app.root.pending[0][0], 100)
            ATCommanderApp.close_app(app)
            self.assertEqual(events, ["stop"])
            worker.is_alive.return_value = False
            app.root.step()
        self.assertEqual(events, ["stop", "release_focus", "owned_close", "unregister", "destroy"])
        app.learning_browser.close.assert_not_called()

    def test_unknown_unattached_close_waits_for_confirmed_owned_close(self):
        app, worker, events = self.application()
        unknown = {"embedded": False, "reason": "browser_close_unconfirmed"}
        closed = {"embedded": False, "reason": "browser_closed"}
        with patch.object(embedded_browser, "close_owned_browser", side_effect=[unknown, unknown, closed]) as owned, \
             patch.object(embedded_browser, "configure", side_effect=lambda parent: events.append("unregister")) as configure, \
             patch("atcommander.ad_browser_focus.release_all_game_input_focus"):
            ATCommanderApp.close_app(app)
            self.assertEqual(events, ["stop", "fallback_close"])
            self.assertEqual(owned.call_count, 2)
            configure.assert_not_called()
            self.assertEqual(app.root.pending[0][0], 250)
            app.root.step()
        self.assertEqual(events, ["stop", "fallback_close", "unregister", "destroy"])
        self.assertTrue(app._game_panel_closed)

    def test_still_embedded_after_close_acknowledgement_is_never_destroyed(self):
        app, worker, events = self.application()
        with patch.object(embedded_browser, "close_owned_browser",
                          return_value={"embedded": True, "reason": "browser_closed"}), \
             patch.object(embedded_browser, "configure") as configure, \
             patch("atcommander.ad_browser_focus.release_all_game_input_focus"):
            ATCommanderApp.close_app(app)
        self.assertEqual(events, ["stop", "fallback_close"])
        configure.assert_not_called()
        self.assertEqual(app.root.pending[0][0], 250)


if __name__ == "__main__":
    unittest.main()
