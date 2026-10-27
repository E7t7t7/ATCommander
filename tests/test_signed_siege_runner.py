import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch
from atcommander.api import ATClient
from atcommander.event_runner import EventBattleRunner


def status(phase, attacks=10, rejected=False, slot="1", round_id="fixture-round"):
    result = {"guild_siege_status": {"phase": phase, "num_attacks": attacks,
        "round_id": round_id, "event_id": "fixture-event", "phase_end_time": 2000,
        "enemy_locations": [{"slot_id": slot, "hp": 100, "data": {"name": "Fixture Island"}}]}}
    if rejected: result["result"] = False
    return result


class SignedSiegeTests(unittest.TestCase):
    def run_scenario(self, statuses, starts=None):
        events = []
        client = Mock()
        client.init.return_value = {}
        client.get_guild_siege_status.side_effect = statuses
        client.extract_battle_data.side_effect = ATClient.extract_battle_data
        client.battle_finished.side_effect = ATClient.battle_finished
        client.extract_outcome.side_effect = ATClient.extract_outcome
        client.opponent_name.side_effect = ATClient.opponent_name
        accepted = {"result": True, "battle_data": {"battle_id": "fixture-battle", "host_id": "fixture-host"}}
        client.start_siege.side_effect = starts
        client.start_siege.return_value = accepted
        client.play_battlefield_card.return_value = {"result": True, "battle_data": {"battle_id": "fixture-battle", "winner": 1}}
        with patch("atcommander.event_runner.LifetimeTracker") as tracker, \
             patch("atcommander.event_runner.ATClient", return_value=client), \
             patch("atcommander.event_runner.load_credentials"), \
             patch("atcommander.event_runner.ad_preflight"), \
             patch("atcommander.event_runner.load_profiles", side_effect=AssertionError("No profile allowed")), \
             patch("atcommander.ad_ui.prepare_game_ui", side_effect=AssertionError("No browser allowed")), \
             patch("atcommander.event_runner.time.sleep"):
            tracker.return_value.to_dict.return_value = {}
            runner = EventBattleRunner("Siege", event=events.append)
            runner._stop = Mock(); runner._stop.is_set.return_value = False
            runner._pause = Mock(); runner._pause.is_set.return_value = False
            runner.policy = SimpleNamespace(choose=lambda _state: SimpleNamespace(
                action="play", card_uid="fixture-card", card_uid2=None, reason="Fixture legal play"))
            runner._run()
        return runner, client, events

    def test_scoring_and_ended_phases_with_stale_attacks_never_start(self):
        for phase in (3, 4):
            with self.subTest(phase=phase):
                _, client, events = self.run_scenario([status(phase)])
                client.start_siege.assert_not_called()
                self.assertTrue(any("has finished" in event.get("message", "") for event in events))
                self.assertEqual("finished", events[-1]["kind"])

    def test_only_battle_phase_starts_after_optin_and_matchmaking(self):
        runner, client, events = self.run_scenario([status(0), status(1), status(2, 1), status(4)])
        client.start_siege.assert_called_once_with("1")
        client.play_battlefield_card.assert_called_once()
        self.assertEqual([call(60), call(60)], runner._stop.wait.call_args_list)
        self.assertTrue(any(event.get("won") is True for event in events if event["kind"] == "battle_end"))

    def test_unknown_phase_waits_without_attack(self):
        runner, client, _ = self.run_scenario([status(99), status(4)])
        client.start_siege.assert_not_called()
        runner._stop.wait.assert_called_once_with(60)

    def test_rejected_status_cannot_use_nested_stale_attack_data(self):
        runner, client, events = self.run_scenario([status(2, rejected=True), status(4)])
        client.start_siege.assert_not_called()
        runner._stop.wait.assert_called_once_with(60)
        self.assertTrue(any("status refresh was rejected" in event.get("message", "") for event in events))

    def test_no_attacks_or_invalid_slot_never_start(self):
        _, client, _ = self.run_scenario([status(2, attacks=0)])
        client.start_siege.assert_not_called()
        runner, client, _ = self.run_scenario([status(2, slot="0"), status(4)])
        client.start_siege.assert_not_called()
        runner._stop.wait.assert_called_once_with(60)

    def test_generic_rejection_and_ambiguous_start_are_blocked_per_round_target(self):
        for response in ({"result": False, "result_message": ["warning_157"]}, {"result": True}):
            with self.subTest(response=response):
                runner, client, _ = self.run_scenario([status(2), status(2), status(4)], starts=[response])
                client.start_siege.assert_called_once_with("1")
                client.play_battlefield_card.assert_not_called()
                self.assertEqual(2, runner._stop.wait.call_count)

    def test_warning16_rejection_retries_after_poll_with_fresh_status(self):
        accepted = {"result": True, "battle_data": {"battle_id": "fixture-battle", "host_id": "fixture-host"}}
        runner, client, _ = self.run_scenario([status(2), status(2), status(4)], starts=[
            {"result": False, "result_message": ["warning_16"]}, accepted])
        self.assertEqual([call("1"), call("1")], client.start_siege.call_args_list)
        runner._stop.wait.assert_called_once_with(60)
        client.play_battlefield_card.assert_called_once()

    def test_new_round_can_start_after_prior_round_target_rejected(self):
        accepted = {"result": True, "battle_data": {"battle_id": "fixture-battle", "host_id": "fixture-host"}}
        _, client, _ = self.run_scenario([status(2), status(2, round_id="next-round"), status(4)], starts=[
            {"result": False}, accepted])
        self.assertEqual(2, client.start_siege.call_count)
        client.play_battlefield_card.assert_called_once()


if __name__ == "__main__": unittest.main()
