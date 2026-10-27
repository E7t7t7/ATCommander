import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from atcommander.api import ATClient
from atcommander.event_runner import ChallengeBattleRunner
from atcommander.swole_runner import SwoleTrainingRunner


def event_payload(level=17, energy=2, *, event_id="900042", challenge_id="1070", name="Fixture Challenge"):
    return {"result": True, "time": 1000, "active_events": [{
        "id": event_id, "type": 6, "name": name, "points": level, "start_time": 900, "end_time": 2000,
        "challenge_data": {"id": challenge_id, "level": level,
            "energy": {"current_value": energy, "max_value": 6, "battle_cost": 1}}}]}


def swole_status(level=82, energy=2):
    return {"time": 1000, "init": event_payload(level, energy, name="Fixture Swole"),
        "account": {}, "swole_event": {"event_id": "900042", "challenge_id": "1070",
            "level": level, "points": level, "energy": energy, "max_energy": 6, "battle_cost": 1},
        "user_tasks": {}, "user_hero_squad": {}}


class SignedRunnerTests(unittest.TestCase):
    def client(self):
        client = Mock()
        client.extract_battle_data.side_effect = ATClient.extract_battle_data
        client.battle_finished.side_effect = ATClient.battle_finished
        client.extract_outcome.side_effect = ATClient.extract_outcome
        client.opponent_name.side_effect = ATClient.opponent_name
        client.start_challenge.return_value = {"result": True, "battle_data": {
            "battle_id": "fixture-battle", "host_id": "fixture-host", "hand": []}}
        client.play_battlefield_card.return_value = {"result": True, "battle_data": {
            "battle_id": "fixture-battle", "host_id": "fixture-host", "winner": 1}}
        return client

    def configure_runner(self, runner):
        runner._stop = Mock()
        runner._stop.is_set.return_value = False
        runner.policy = SimpleNamespace(choose=lambda _state: SimpleNamespace(
            action="play", card_uid="fixture-card", card_uid2=None, reason="Fixture legal play"))
        return runner

    def challenge(self, payloads):
        client = self.client()
        client.init.side_effect = [event_payload(), *payloads]
        events = []
        with patch("atcommander.event_runner.ATClient", return_value=client), \
             patch("atcommander.event_runner.load_credentials"), \
             patch("atcommander.event_runner.ad_preflight"), \
             patch("atcommander.event_runner.LifetimeTracker") as tracker, \
             patch("atcommander.event_runner.load_profiles", side_effect=AssertionError("No profile allowed")), \
             patch("atcommander.browser_bridge.start_api_via_captured_game_click", side_effect=AssertionError("No browser allowed")):
            tracker.return_value.to_dict.return_value = {}
            runner = self.configure_runner(ChallengeBattleRunner("900042", "stale-id", "Fixture", level=1, event=events.append))
            runner._run()
        return runner, client, events

    def test_challenge_uses_fresh_id_and_advancing_level_without_profile_or_browser(self):
        _, client, events = self.challenge([event_payload(17), event_payload(18, 1, challenge_id="1071"), event_payload(19, 0)])
        self.assertEqual([call("1070", 17, auto_battle=False), call("1071", 18, auto_battle=False)], client.start_challenge.call_args_list)
        self.assertEqual(2, client.play_battlefield_card.call_count)
        self.assertEqual([True, True], [e["won"] for e in events if e["kind"] == "battle_end"])
        self.assertEqual("finished", events[-1]["kind"])

    def test_challenge_stops_before_start_on_unconfirmed_event(self):
        variants = []
        ended = event_payload(); ended["active_events"][0]["end_time"] = 1000; variants.append(ended)
        upcoming = event_payload(); upcoming["upcoming_events"] = upcoming.pop("active_events"); variants.append(upcoming)
        missing_level = event_payload(); del missing_level["active_events"][0]["challenge_data"]["level"]; variants.append(missing_level)
        missing_id = event_payload(); del missing_id["active_events"][0]["challenge_data"]["id"]; variants.append(missing_id)
        missing_energy = event_payload(energy=None); variants.append(missing_energy)
        rejected = event_payload(); rejected["result"] = False; variants.append(rejected)
        variants.append(RuntimeError("Fixture refresh failure"))
        for payload in variants:
            with self.subTest(payload_type=type(payload).__name__):
                _, client, events = self.challenge([payload])
                client.start_challenge.assert_not_called()
                self.assertEqual("finished", events[-1]["kind"])

    def test_challenge_refresh_failure_after_fight_does_not_reuse_last_level(self):
        _, client, _ = self.challenge([event_payload(), RuntimeError("Fixture refresh failure")])
        client.start_challenge.assert_called_once_with("1070", 17, auto_battle=False)

    def test_challenge_missing_battle_state_is_not_retried(self):
        client = self.client(); client.start_challenge.return_value = {"result": True}
        client.init.return_value = event_payload()
        events = []
        with patch("atcommander.event_runner.ATClient", return_value=client), \
             patch("atcommander.event_runner.load_credentials"), patch("atcommander.event_runner.ad_preflight"), \
             patch("atcommander.event_runner.LifetimeTracker"):
            runner = self.configure_runner(ChallengeBattleRunner("900042", "1070", "Fixture", event=events.append))
            runner._run()
        self.assertEqual(1, client.start_challenge.call_count)
        client.play_battlefield_card.assert_not_called()

    def swole(self, statuses, *, result=None, count=2):
        client = self.client()
        client.get_swole_status.side_effect = statuses
        if result is not None: client.start_challenge.return_value = result
        events = []
        runner = self.configure_runner(SwoleTrainingRunner([], event=events.append))
        runner.c = client
        with patch("atcommander.browser_bridge.start_api_via_captured_game_click", side_effect=AssertionError("No browser allowed")):
            completed = runner._do_fights(count)
        return runner, client, events, completed

    def test_swole_uses_current_challenge_level_for_each_fight(self):
        _, client, events, completed = self.swole([
            swole_status(82), swole_status(83, 1),
            swole_status(83, 1), swole_status(84, 0)])
        self.assertTrue(completed)
        self.assertEqual([call("1070", 82, auto_battle=False), call("1070", 83, auto_battle=False)], client.start_challenge.call_args_list)
        self.assertEqual(2, client.play_battlefield_card.call_count)
        self.assertEqual(4, client.get_swole_status.call_count)
        self.assertEqual(2, sum(e["kind"] == "battle_end" for e in events))

    def test_swole_zero_tickets_waits_then_uses_fresh_zero_level(self):
        runner, client, events, completed = self.swole([
            swole_status(0, 0), swole_status(0, 1), swole_status(1, 0)], count=1)
        self.assertTrue(completed)
        runner._stop.wait.assert_called_once_with(runner.poll)
        client.start_challenge.assert_called_once_with("1070", 0, auto_battle=False)
        self.assertTrue(any(e["kind"] == "swole_wait" for e in events))

    def test_swole_rejected_or_ended_current_data_cannot_use_summary(self):
        rejected = swole_status(); rejected["init"]["result"] = False
        ended = swole_status(); ended["init"]["active_events"][0]["end_time"] = 1000
        unknown = swole_status(); del unknown["init"]["active_events"][0]["challenge_data"]["level"]
        for status in (rejected, ended, unknown):
            with self.subTest(status=status):
                _, client, events, completed = self.swole([status], count=1)
                self.assertFalse(completed)
                client.start_challenge.assert_not_called()
                self.assertEqual("swole_fight_paused", events[-1]["kind"])

    def test_swole_missing_battle_state_preserves_step_without_retry(self):
        _, client, events, completed = self.swole([swole_status()], result={"result": True}, count=1)
        self.assertFalse(completed)
        self.assertEqual(1, client.start_challenge.call_count)
        client.play_battlefield_card.assert_not_called()
        self.assertEqual("swole_fight_paused", events[-1]["kind"])

    def test_swole_run_preserves_fight_and_training_queue_when_event_unknown(self):
        client = self.client(); client.init.return_value = {}
        bad = swole_status(); bad["init"]["result"] = False
        client.get_swole_status.side_effect = [copy.deepcopy(bad), copy.deepcopy(bad)]
        queue = [{"type": "fight", "count": 1}, {"type": "train", "id": "1", "target_level": 2}]
        events = []
        with patch("atcommander.swole_runner.ATClient", return_value=client), \
             patch("atcommander.swole_runner.load_credentials"), patch("atcommander.swole_runner.ad_preflight"):
            runner = self.configure_runner(SwoleTrainingRunner(queue, event=events.append))
            runner._run()
        self.assertEqual(queue, events[-1]["remaining_queue"])
        self.assertEqual("finished", events[-1]["kind"])
        client.start_challenge.assert_not_called()
        client.start_swole_training.assert_not_called()


if __name__ == "__main__": unittest.main()
