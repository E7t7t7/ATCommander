from queue import Queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from atcommander.app_update_ui import AppUpdateMixin
from atcommander.app_updater import UpdateError


class Value:
    def __init__(self): self.value = ""
    def set(self, value): self.value = value
    def get(self): return self.value


class Control:
    def configure(self, **kwargs): self.settings = kwargs


class UI(AppUpdateMixin):
    def __init__(self):
        self._closing = False; self.app_update_thread = None
        self.app_update_stop = threading.Event(); self.app_update_status = Value()
        self.app_update_button = Control(); self.events = Queue()


def immediate(*, target, **kwargs): return SimpleNamespace(start=target, is_alive=lambda: False)


class AppUpdateUiTests(unittest.TestCase):
    def test_worker_only_prepares_and_main_thread_handles_result(self):
        app = UI()
        with patch("atcommander.app_update_ui.threading.Thread", side_effect=immediate), \
             patch("atcommander.app_update_ui.prepare_update", return_value={"ready": True, "message": "Ready to reopen."}) as prepare:
            app._start_app_update()
        prepare.assert_called_once()
        self.assertEqual(app.app_update_status.get(), "Checking Commander on GitHub…")
        app._app_update_event(app.events.get_nowait())
        self.assertEqual(app.app_update_status.get(), "Ready to reopen.")
        self.assertEqual(app.app_update_button.settings["text"], "Update Commander")

    def test_repeat_click_cancels_without_second_worker(self):
        app = UI(); app.app_update_thread = SimpleNamespace(is_alive=lambda: True)
        with patch("atcommander.app_update_ui.prepare_update", side_effect=AssertionError()): app._start_app_update()
        self.assertTrue(app.app_update_stop.is_set())

    def test_close_blocks_new_work_and_ignores_late_events(self):
        app = UI(); app._closing = True
        with patch("atcommander.app_update_ui.threading.Thread", side_effect=AssertionError()): app._start_app_update()
        self.assertTrue(app._app_update_event({"kind": "app_update_done", "message": "Late"}))
        self.assertEqual(app.app_update_status.get(), "")

    def test_expected_failure_is_presented_without_raw_exception_data(self):
        app = UI()
        with patch("atcommander.app_update_ui.threading.Thread", side_effect=immediate), \
             patch("atcommander.app_update_ui.prepare_update", side_effect=ConnectionError("private request data")):
            app._start_app_update()
        event = app.events.get_nowait()
        self.assertNotIn("private request", event["message"])

    def test_safe_updater_error_reaches_the_user(self):
        app = UI()
        with patch("atcommander.app_update_ui.threading.Thread", side_effect=immediate), \
             patch("atcommander.app_update_ui.prepare_update", side_effect=UpdateError("Update cancelled.")):
            app._start_app_update()
        app._app_update_event(app.events.get_nowait())
        self.assertEqual(app.app_update_status.get(), "Update cancelled.")

    def test_other_events_are_not_consumed(self):
        self.assertFalse(UI()._app_update_event({"kind": "log"}))


if __name__ == "__main__": unittest.main()
