from queue import Queue
import threading
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import patch

from atcommander.research_center import ResearchCenterMixin


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Control:
    def configure(self, **settings):
        self.settings = settings


class ResearchControls(ResearchCenterMixin):
    def __init__(self):
        self.research_tiers = {tier: Value(tier >= 4) for tier in range(1, 6)}
        self.research_match = Value("either")
        self.research_speedups = Value(True)
        self.research_speedups_button = Control()
        self.research_ads = Value(False)
        self.research_rogu = Value(True)
        self.research_all_speedups = Value(False)
        self.research_hours = Value("100000")
        self.research_reserve = Value("0")
        self.research_pairs = Value("100000")
        self.research_hour_entries = [Control(), Control()]
        self.settings = {}


class ResearchSpeedupControlTests(unittest.TestCase):
    def setUp(self):
        self.app = ResearchControls()
        self.save = patch("atcommander.settings.save_settings").start()
        self.addCleanup(patch.stopall)

    def test_numeric_budget_above_old_ten_thousand_hour_cap_is_accepted(self):
        values = self.app._research_values()
        self.assertEqual(values["max_speedup_hours"], 100000)
        self.assertFalse(values["use_all_speedups"])
        self.save.assert_called_once_with(self.app.settings)

    def test_all_mode_ignores_numeric_budget_and_reserve_and_persists_explicitly(self):
        self.app.research_all_speedups.set(True)
        self.app.research_hours.set("not used")
        self.app.research_reserve.set("not used")
        self.app.research_speedups.set(False)
        values = self.app._research_values()
        self.assertTrue(values["use_all_speedups"])
        self.assertTrue(values["speedups"])
        self.assertEqual(values["max_speedup_hours"], 0)
        self.assertEqual(values["reserve_hours"], 0)
        self.assertEqual(self.app.settings["research_queue_options"], values)

    def test_all_mode_disables_hour_entries_and_enables_speedups(self):
        self.app.research_speedups.set(False)
        self.app.research_all_speedups.set(True)
        self.app._research_speedup_mode_changed()
        self.assertTrue(self.app.research_speedups.get())
        self.assertEqual(self.app.research_speedups_button.settings["state"], "disabled")
        self.assertTrue(all(entry.settings["state"] == "disabled" for entry in self.app.research_hour_entries))
        self.app.research_all_speedups.set(False)
        self.app._research_speedup_mode_changed()
        self.assertEqual(self.app.research_speedups_button.settings["state"], "normal")
        self.assertTrue(all(entry.settings["state"] == "normal" for entry in self.app.research_hour_entries))

    def test_all_mode_keeps_manual_limits_separately_for_next_launch(self):
        self.app.research_hours.set("99999")
        self.app.research_reserve.set("100")
        self.app.research_all_speedups.set(True)
        values = self.app._research_values()
        self.assertEqual(values["max_speedup_hours"], 0)
        self.assertEqual(values["reserve_hours"], 0)
        self.assertEqual(self.app.settings["research_manual_speedup_limits"], {"max_speedup_hours": 99999, "reserve_hours": 100})

    def test_numeric_upper_bound_and_combo_limits_still_validate(self):
        for attribute, value in (("research_hours", "1000001"), ("research_reserve", "-1"), ("research_pairs", "100001")):
            with self.subTest(attribute=attribute):
                app = ResearchControls()
                getattr(app, attribute).set(value)
                with self.assertRaises(ValueError):
                    app._research_values()
        self.save.assert_not_called()

    def test_all_mode_still_requires_selected_tiers_and_valid_combo_limit(self):
        self.app.research_all_speedups.set(True)
        for selected in self.app.research_tiers.values():
            selected.set(False)
        with self.assertRaisesRegex(ValueError, "input tier"):
            self.app._research_values()
        self.app.research_tiers[4].set(True)
        self.app.research_pairs.set("0")
        with self.assertRaises(ValueError):
            self.app._research_values()

    def test_check_saved_action_ignores_edited_options_and_never_arms_worker(self):
        app = self.app
        app.command_busy = False
        app.research_busy = False
        app.command_stop = threading.Event()
        app.command_run_button = Control()
        app.research_status = Value("")
        app.command_schema = SimpleNamespace(store=object())
        app.events = Queue()
        calls = []
        service = SimpleNamespace(execute=lambda name, args: calls.append((name, args)) or object())
        def immediate_thread(*, target, **kwargs):
            return SimpleNamespace(start=target)
        with patch.object(app, "_research_values", side_effect=AssertionError("recovery must ignore edited settings")), \
             patch("atcommander.research_center.CommandCenterMixin._browser_action_busy", return_value=False), \
             patch("atcommander.research_center.load_credentials", return_value=object()), \
             patch("atcommander.research_center.ATClient", return_value=object()), \
             patch("atcommander.research_center.build_service", return_value=service), \
             patch("atcommander.research_center.threading.Thread", side_effect=immediate_thread):
            app._research_launch(False, reconcile=True)
        self.assertEqual(calls, [("research-reconcile", {})])
        self.assertFalse(app.research_auto)
        self.assertFalse(app.command_mutating)
        self.assertNotIn("error", app.events.get_nowait())
        self.save.assert_not_called()


class ResearchWidgetIntegrationTests(unittest.TestCase):
    def test_reopening_all_mode_restores_manual_limits_and_locks_conflicting_controls(self):
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("Tk display unavailable")
        root.withdraw()
        self.addCleanup(root.destroy)
        app = ResearchControls()
        app.root = root
        app.research_tab = tk.Frame(root)
        app._logbox = lambda parent, **kwargs: tk.Text(parent)
        app.settings = {
            "research_queue_options": {"speedups": True, "use_all_speedups": True, "max_speedup_hours": 0, "reserve_hours": 0},
            "research_manual_speedup_limits": {"max_speedup_hours": 99000, "reserve_hours": 150},
        }
        app._build_research_center()
        self.assertTrue(app.research_speedups.get())
        self.assertTrue(app.research_speedups_button.instate(["disabled"]))
        self.assertTrue(all(entry.instate(["disabled"]) for entry in app.research_hour_entries))
        self.assertEqual(app.research_hours.get(), "99000")
        self.assertEqual(app.research_reserve.get(), "150")
        app.research_all_speedups.set(False)
        app._research_speedup_mode_changed()
        self.assertFalse(app.research_speedups_button.instate(["disabled"]))
        with patch("atcommander.settings.save_settings"):
            values = app._research_values()
        self.assertEqual(values["max_speedup_hours"], 99000)
        self.assertEqual(values["reserve_hours"], 150)


if __name__ == "__main__":
    unittest.main()
