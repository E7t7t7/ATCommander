import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from atcommander.app import ATCommanderApp, BUILTIN_ACTIONS
from tests.test_signed_challenge_runners import swole_status


class Value:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class CombatUIIntegrationTests(unittest.TestCase):
    def test_challenge_start_needs_current_event_data_without_profile(self):
        app = object.__new__(ATCommanderApp)
        app.event_runner = None
        app._selected_challenge_row = Mock(return_value={"id": "900042", "challenge_id": "1070", "level": 0, "state": "Active", "name": "Fixture"})
        for attr, value in (("challenge_offense_deck", "unchanged"), ("challenge_offense_hero", "unchanged"),
                            ("delay_var", ".1"), ("battle_delay_var", "1.5"),
                            ("challenge_logic", "Item → Combo (default)"), ("auto_ads_var", False)):
            setattr(app, attr, Value(value))
        app.deck_rows = []; app.hero_rows = []; app.events = queue.Queue()
        app._selected_id = Mock(return_value=None); app._event_log = Mock()
        with patch("atcommander.app.load_profiles", side_effect=AssertionError("No profile allowed")), \
             patch("atcommander.app.ChallengeBattleRunner") as factory:
            app.start_challenge()
        factory.return_value.start.assert_called_once()
        self.assertEqual("1070", factory.call_args.args[1])
        self.assertEqual(0, factory.call_args.kwargs["level"])
        self.assertTrue({"swole_battle", "challenge_battle", "siege"} <= BUILTIN_ACTIONS)

    def test_swole_status_readiness_comes_from_live_event_not_profile(self):
        for level, expected in ((0, True), (None, False)):
            app = object.__new__(ATCommanderApp)
            app.swole_status = Value(""); app.events = queue.Queue()
            client = Mock(); client.get_swole_status.return_value = swole_status(level=level)
            workers = []
            with patch("atcommander.app.ATClient", return_value=client), \
                 patch("atcommander.app.load_credentials"), \
                 patch("atcommander.app.load_profiles", side_effect=AssertionError("No profile allowed")), \
                 patch("atcommander.app.threading.Thread", side_effect=lambda *, target, **kw: SimpleNamespace(start=lambda: workers.append(target))):
                app.refresh_swole_status(); workers.pop()()
            event = app.events.get_nowait()
            self.assertEqual("swole_status_live", event["kind"])
            self.assertEqual(expected, event["fight_ready"])


if __name__ == "__main__": unittest.main()
