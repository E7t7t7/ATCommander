import json
import unittest
from unittest.mock import Mock, patch

from atcommander import ad_browser_focus as focus
from atcommander import ad_ui, automatic_ads, research_ad_ui


CANVAS = {"x": 20, "y": 40, "width": 600, "height": 400}
POINT = {"x": .5, "y": .5}


def prepared(**options):
    return {"target": {"webSocketDebuggerUrl": "synthetic-game"},
            "input_target": {"webSocketDebuggerUrl": "synthetic-parent"},
            "input_canvas": dict(CANVAS), **options}


class FocusFixture:
    def __init__(self, ws, cdp):
        cdp(ws, "Emulation.setFocusEmulationEnabled", {"enabled": True})
        self.check = Mock()
        self.close = Mock()


def fixture_factory(cdp):
    return lambda ws: FocusFixture(ws, cdp)


class AdBrowserFocusTests(unittest.TestCase):
    def test_standalone_raises_once_and_focuses_each_distinct_frame_once(self):
        prep = prepared()
        cdp = Mock()
        for _ in range(5):
            focus.ensure_game_input_focus(prep, cdp, session_factory=fixture_factory(cdp))
        self.assertEqual([call.args for call in cdp.call_args_list], [
            ("synthetic-parent", "Page.bringToFront", {}),
            ("synthetic-parent", "Emulation.setFocusEmulationEnabled", {"enabled": True}),
            ("synthetic-game", "Emulation.setFocusEmulationEnabled", {"enabled": True})])

    def test_embedded_keeps_focus_emulation_without_raising_or_geometry(self):
        prep = prepared(embedded=True)
        cdp = Mock()
        for _ in range(5):
            focus.ensure_game_input_focus(prep, cdp, session_factory=fixture_factory(cdp))
        self.assertEqual([call.args[1] for call in cdp.call_args_list],
                         ["Emulation.setFocusEmulationEnabled"] * 2)

    def test_same_page_and_game_frame_are_not_initialized_twice(self):
        prep = {"target": {"webSocketDebuggerUrl": "synthetic-one-page"}}
        cdp = Mock()
        focus.ensure_game_input_focus(prep, cdp, session_factory=fixture_factory(cdp))
        self.assertEqual([call.args[1] for call in cdp.call_args_list],
                         ["Page.bringToFront", "Emulation.setFocusEmulationEnabled"])

    def test_new_input_parent_is_initialized_without_repeating_game_focus(self):
        prep = prepared()
        cdp = Mock()
        focus.ensure_game_input_focus(prep, cdp, session_factory=fixture_factory(cdp))
        old_parent = prep["_commander_game_focus"]["sessions"]["synthetic-parent"]
        cdp.reset_mock()
        prep["input_target"] = {"webSocketDebuggerUrl": "synthetic-new-parent"}
        focus.ensure_game_input_focus(prep, cdp, session_factory=fixture_factory(cdp))
        old_parent.close.assert_called_once()
        self.assertEqual(len(prep["_commander_game_focus"]["sessions"]), 2)
        self.assertEqual([call.args for call in cdp.call_args_list], [
            ("synthetic-new-parent", "Page.bringToFront", {}),
            ("synthetic-new-parent", "Emulation.setFocusEmulationEnabled", {"enabled": True})])

    def test_switching_out_of_embedded_host_can_raise_once_without_refocusing(self):
        prep = prepared(embedded=True)
        cdp = Mock()
        focus.ensure_game_input_focus(prep, cdp, session_factory=fixture_factory(cdp))
        cdp.reset_mock()
        prep["embedded"] = False
        focus.ensure_game_input_focus(prep, cdp, session_factory=fixture_factory(cdp))
        focus.ensure_game_input_focus(prep, cdp, session_factory=fixture_factory(cdp))
        cdp.assert_called_once_with("synthetic-parent", "Page.bringToFront", {})

    def test_failed_frame_does_not_repeat_successful_window_or_frame_setup(self):
        prep = prepared()
        cdp = Mock(side_effect=[{}, {}, RuntimeError("synthetic-unavailable"), {}])
        with self.assertRaises(RuntimeError):
            focus.ensure_game_input_focus(prep, cdp, session_factory=fixture_factory(cdp))
        focus.ensure_game_input_focus(prep, cdp, session_factory=fixture_factory(cdp))
        self.assertEqual([call.args for call in cdp.call_args_list], [
            ("synthetic-parent", "Page.bringToFront", {}),
            ("synthetic-parent", "Emulation.setFocusEmulationEnabled", {"enabled": True}),
            ("synthetic-game", "Emulation.setFocusEmulationEnabled", {"enabled": True}),
            ("synthetic-game", "Emulation.setFocusEmulationEnabled", {"enabled": True})])

    def test_invalid_frame_fails_before_any_window_or_input_operation(self):
        for prep in (None, {}, prepared(target={}), prepared(input_target={"webSocketDebuggerUrl": None})):
            with self.subTest(prep=prep):
                cdp = Mock()
                with self.assertRaises(RuntimeError):
                    focus.ensure_game_input_focus(prep, cdp)
                cdp.assert_not_called()

    def test_new_preparation_does_not_inherit_stale_focus_acknowledgement(self):
        cdp = Mock()
        focus.ensure_game_input_focus(prepared(), cdp, session_factory=fixture_factory(cdp))
        focus.ensure_game_input_focus(prepared(), cdp, session_factory=fixture_factory(cdp))
        self.assertEqual(cdp.call_count, 6)

    def test_global_and_research_and_shared_mom_unlock_clicks_reuse_one_setup(self):
        prep = prepared()
        with patch.object(ad_ui, "_cdp") as cdp, \
             patch.object(focus, "_RetainedFocusSession", side_effect=lambda ws: FocusFixture(ws, cdp)), \
             patch.object(ad_ui, "_dispatch_click") as quick_click, \
             patch.object(research_ad_ui.time, "sleep"):
            automatic_ads._click(prep, CANVAS, POINT)
            research_ad_ui._click(prep, CANVAS, POINT)
            automatic_ads._click(prep, CANVAS, POINT)
            research_ad_ui._click(prep, CANVAS, POINT)
        methods = [call.args[1] for call in cdp.call_args_list]
        self.assertEqual(methods.count("Page.bringToFront"), 1)
        self.assertEqual(methods.count("Emulation.setFocusEmulationEnabled"), 2)
        self.assertEqual(quick_click.call_count, 2)
        self.assertEqual(methods.count("Input.dispatchMouseEvent"), 10)
        points = [call.args[2] for call in cdp.call_args_list if call.args[1] == "Input.dispatchMouseEvent"]
        self.assertTrue(all((point["x"], point["y"]) == (320.0, 240.0) for point in points))

    def test_embedded_clicks_still_send_normal_pointer_input_without_window_moves(self):
        prep = prepared(embedded=True)
        with patch.object(ad_ui, "_cdp") as cdp, \
             patch.object(focus, "_RetainedFocusSession", side_effect=lambda ws: FocusFixture(ws, cdp)), \
             patch.object(research_ad_ui.time, "sleep"):
            research_ad_ui._click(prep, CANVAS, POINT)
            research_ad_ui._click(prep, CANVAS, POINT)
        methods = [call.args[1] for call in cdp.call_args_list]
        self.assertNotIn("Page.bringToFront", methods)
        self.assertNotIn("Browser.setWindowBounds", methods)
        self.assertEqual(methods.count("Emulation.setFocusEmulationEnabled"), 2)
        self.assertEqual(methods.count("Input.dispatchMouseEvent"), 8)

    def test_focus_failure_prevents_global_or_research_pointer_click(self):
        for click in (automatic_ads._click, research_ad_ui._click):
            with self.subTest(click=click), patch.object(ad_ui, "_cdp", side_effect=RuntimeError("unavailable")) as cdp, \
                 patch.object(ad_ui, "_dispatch_click") as dispatch:
                with self.assertRaises(RuntimeError):
                    click(prepared(), CANVAS, POINT)
                dispatch.assert_not_called()
                self.assertEqual([call.args[1] for call in cdp.call_args_list], ["Page.bringToFront"])

    def test_lost_retained_focus_stops_before_pointer_input_without_replay(self):
        prep = prepared(embedded=True)
        cdp = Mock()
        focus.ensure_game_input_focus(prep, cdp, session_factory=fixture_factory(cdp))
        session = prep["_commander_game_focus"]["sessions"]["synthetic-parent"]
        session.check.side_effect = RuntimeError("synthetic-private-focus-error")
        with patch.object(ad_ui, "_cdp") as input_command, \
             patch.object(ad_ui, "_dispatch_click") as click:
            with self.assertRaises(RuntimeError):
                automatic_ads._click(prep, CANVAS, POINT)
        input_command.assert_not_called()
        click.assert_not_called()
        session.close.assert_called_once()
        self.assertNotIn("synthetic-parent", prep["_commander_game_focus"]["sessions"])

    def test_release_closes_every_active_connection_and_invalidates_preparation(self):
        prep = prepared()
        cdp = Mock()
        focus.ensure_game_input_focus(prep, cdp, session_factory=fixture_factory(cdp))
        sessions = list(prep["_commander_game_focus"]["sessions"].values())
        focus.release_game_input_focus(prep)
        focus.release_game_input_focus(prep)
        self.assertNotIn("_commander_game_focus", prep)
        for session in sessions:
            session.close.assert_called_once()


class RetainedFocusTransportTests(unittest.TestCase):
    def socket(self, responses):
        sock = Mock()
        sock.recv.side_effect = [json.dumps(reply) for reply in responses]
        return sock

    def session(self, sock):
        with patch.object(focus.websocket, "create_connection", return_value=sock):
            return focus._RetainedFocusSession("ws://127.0.0.1/synthetic-focus")

    def test_retains_acknowledged_emulation_and_health_check_on_same_socket(self):
        sock = self.socket([
            {"method": "unrelated.event", "params": {}},
            {"id": 1, "result": {}},
            {"id": 2, "result": {"result": {"type": "boolean", "value": True}}},
            {"id": 3, "result": {"result": {"type": "boolean", "value": True}}}])
        session = self.session(sock)
        self.addCleanup(session.close)
        session.check()
        session.check()
        sock.close.assert_not_called()
        sent = [json.loads(call.args[0]) for call in sock.send.call_args_list]
        self.assertEqual([request["method"] for request in sent],
                         ["Emulation.setFocusEmulationEnabled", "Runtime.evaluate", "Runtime.evaluate"])
        self.assertTrue(all(request["params"]["expression"] == "true" for request in sent[1:]))

    def test_focus_api_rejection_closes_and_hides_private_payload(self):
        sock = self.socket([{"id": 1, "error": {"message": "session_id=synthetic-private"}}])
        with self.assertRaisesRegex(RuntimeError, "^The game focus connection is unavailable.$"):
            self.session(sock)
        sock.close.assert_called_once()
        self.assertEqual(sock.send.call_count, 1)

    def test_health_read_rejection_closes_without_refocus_or_input(self):
        for reply in ({"id": 2, "result": {}},
                      {"id": 2, "result": {"result": {"value": False}}},
                      {"id": 2, "result": {"exceptionDetails": {"text": "private"}}}):
            with self.subTest(reply=reply):
                sock = self.socket([{"id": 1, "result": {}}, reply])
                session = self.session(sock)
                with self.assertRaises(RuntimeError):
                    session.check()
                with self.assertRaises(RuntimeError):
                    session.check()
                sock.close.assert_called_once()
                self.assertEqual(sock.send.call_count, 2)

    def test_lost_socket_closes_without_resending_any_command(self):
        sock = self.socket([{"id": 1, "result": {}}])
        session = self.session(sock)
        sock.recv.side_effect = RuntimeError("synthetic-private-websocket-error")
        with self.assertRaisesRegex(RuntimeError, "^The game focus connection is unavailable.$"):
            session.check()
        sock.close.assert_called_once()
        self.assertEqual(sock.send.call_count, 2)

    def test_event_flood_is_bounded_and_closes_connection(self):
        sock = self.socket([{"method": "unrelated.event"}] * 256)
        with self.assertRaises(RuntimeError):
            self.session(sock)
        self.assertEqual(sock.recv.call_count, 256)
        sock.close.assert_called_once()

    def test_reply_deadline_updates_receive_timeout_and_fails_closed(self):
        sock = self.socket([{"method": "unrelated.event"}])
        with patch.object(focus.time, "monotonic", side_effect=[0.0, 1.0, 5.0]), \
             patch.object(focus.websocket, "create_connection", return_value=sock):
            with self.assertRaises(RuntimeError):
                focus._RetainedFocusSession("ws://127.0.0.1/synthetic-focus")
        sock.settimeout.assert_called_once_with(3.0)
        sock.close.assert_called_once()

    def test_explicit_close_releases_socket_once_without_background_commands(self):
        sock = self.socket([{"id": 1, "result": {}}])
        session = self.session(sock)
        session.close()
        session.close()
        sock.close.assert_called_once_with(timeout=0)
        self.assertEqual(sock.send.call_count, 1)

    def test_public_shutdown_hook_closes_all_owned_sessions_once(self):
        sockets=[self.socket([{"id":1,"result":{}}]) for _ in range(2)]
        sessions=[self.session(sock) for sock in sockets]
        focus.release_all_game_input_focus()
        focus.release_all_game_input_focus()
        for sock,session in zip(sockets,sessions):
            sock.close.assert_called_once_with(timeout=0)
            self.assertEqual(sock.send.call_count,1)
            with self.assertRaises(RuntimeError):session.check()


if __name__ == "__main__":
    unittest.main()
