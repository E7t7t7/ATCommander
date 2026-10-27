"""Offline Swole credit and resumable-schedule checks using public fixtures."""
import copy
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from atcommander.api import ATApiError, ATClient
from atcommander.swole_runner import SwoleTrainingRunner
from tests.test_signed_challenge_runners import swole_status


class SwoleProgressTests(unittest.TestCase):
    def runner(self, statuses, *, responses=None, start=None, queue=None):
        client = Mock()
        client.get_swole_status.side_effect = copy.deepcopy(statuses)
        client.init.return_value = {"result": True}
        client.extract_battle_data.side_effect = ATClient.extract_battle_data
        client.battle_finished.side_effect = ATClient.battle_finished
        client.extract_outcome.side_effect = ATClient.extract_outcome
        client.opponent_name.side_effect = ATClient.opponent_name
        client.start_challenge.return_value = start if start is not None else {
            "result": True, "battle_data": self.state(hand=[])}
        client.play_battlefield_card.side_effect = responses or [self.response(winner=1)]
        events = []
        runner = SwoleTrainingRunner(queue or [], event=events.append)
        runner.c = client
        runner._stop = threading.Event()
        runner._stop.wait = Mock(return_value=False)
        runner.policy = SimpleNamespace(choose=lambda _state: SimpleNamespace(
            action="play", card_uid="public-card", card_uid2=None, reason="Public legal-play fixture"))
        return runner, client, events

    @staticmethod
    def state(**fields):
        return {"battle_id": "12345", "host_id": "67890", **fields}

    @classmethod
    def response(cls, **fields):
        return {"result": True, "battle_data": cls.state(**fields)}

    def fight(self, runner, count=1):
        with patch("atcommander.swole_runner.time.sleep"):
            return runner._do_fights(count)

    def run_schedule(self, runner, client):
        with patch("atcommander.swole_runner.ATClient", return_value=client), \
             patch("atcommander.swole_runner.load_credentials"), \
             patch("atcommander.swole_runner.ad_preflight"), \
             patch("atcommander.swole_runner.time.sleep"):
            runner._run()

    @staticmethod
    def ends(events):
        return [event for event in events if event["kind"] == "battle_end"]

    def test_win_requires_fresh_server_progress_and_reports_credit(self):
        runner, client, events = self.runner([swole_status(92, 2), swole_status(93, 1)])
        self.assertTrue(self.fight(runner))
        client.start_challenge.assert_called_once_with("1070", 92, auto_battle=False)
        self.assertEqual(client.get_swole_status.call_count, 2)
        result = self.ends(events)[0]
        self.assertTrue(result["won"])
        self.assertEqual((92, 93, 1), (result["points_before"], result["points_after"], result["tickets_after"]))
        self.assertTrue(result["progress_verified"])
        self.assertTrue(result["post_status_verified"])

    def test_true_combat_win_without_credit_pauses_before_next_ticket(self):
        runner, client, events = self.runner([swole_status(92, 2), swole_status(92, 1)])
        self.assertFalse(self.fight(runner, 2))
        self.assertEqual(client.start_challenge.call_count, 1)
        self.assertEqual(client.play_battlefield_card.call_count, 1)
        self.assertEqual(runner._fights_completed, 1)
        result = self.ends(events)[0]
        self.assertTrue(result["won"])
        self.assertFalse(result["progress_verified"])
        self.assertTrue(result["post_status_verified"])
        self.assertEqual(events[-1]["kind"], "swole_fight_paused")

    def test_higher_score_with_unchanged_floor_cannot_replay_old_floor(self):
        after = swole_status(92, 1)
        after["init"]["active_events"][0]["points"] = 93
        runner, client, events = self.runner([swole_status(92, 2), after])
        self.assertFalse(self.fight(runner, 2))
        self.assertEqual(client.start_challenge.call_count, 1)
        result = self.ends(events)[0]
        self.assertEqual(result["points_after"], 93)
        self.assertTrue(result["post_status_verified"])
        self.assertFalse(result["progress_verified"])

    def test_loss_can_leave_score_unchanged_with_fresh_verified_status(self):
        runner, client, events = self.runner([swole_status(92, 2), swole_status(92, 1)],
                                              responses=[self.response(winner=0)])
        self.assertTrue(self.fight(runner))
        result = self.ends(events)[0]
        self.assertFalse(result["won"])
        self.assertFalse(result["progress_verified"])
        self.assertTrue(result["post_status_verified"])
        self.assertEqual(runner._fights_completed, 1)

    def test_stop_arriving_during_status_read_cannot_start_a_fight(self):
        runner, client, _events = self.runner([swole_status(92, 2)])
        def read():
            runner.stop()
            return swole_status(92, 2)
        client.get_swole_status.side_effect = read
        self.assertFalse(self.fight(runner))
        client.start_challenge.assert_not_called()
        self.assertEqual(runner._fights_completed, 0)

    def test_validation_warning_is_distinguished_from_ambiguous_missing_state(self):
        runner, client, events = self.runner([swole_status(92, 2)],
            start={"result": False, "result_message": ["warning_157_text"]})
        self.assertFalse(self.fight(runner))
        self.assertIn("validation warning 157", events[-1]["message"])
        client.start_challenge.assert_called_once()
        self.assertEqual(runner._fights_completed, 0)

    def test_unverified_postfight_identity_or_status_pauses_even_on_loss(self):
        variants = []
        rejected = swole_status(92, 1); rejected["init"]["result"] = False; variants.append(rejected)
        absent = swole_status(92, 1); absent["init"]["active_events"] = []; variants.append(absent)
        changed = swole_status(92, 1); changed["init"]["active_events"][0]["challenge_data"]["id"] = "1071"; variants.append(changed)
        missing_points = swole_status(92, 1); del missing_points["init"]["active_events"][0]["points"]; variants.append(missing_points)
        ended = swole_status(92, 1); ended["init"]["active_events"][0]["end_time"] = 1000; variants.append(ended)
        variants.append(RuntimeError("Public read fixture failed"))
        for after in variants:
            with self.subTest(after_type=type(after).__name__):
                runner, client, events = self.runner([swole_status(92, 2), after],
                    responses=[self.response(winner=0)])
                self.assertFalse(self.fight(runner, 2))
                self.assertEqual(client.start_challenge.call_count, 1)
                self.assertFalse(self.ends(events)[0]["post_status_verified"])
                self.assertEqual(events[-1]["kind"], "swole_fight_paused")

    def test_missing_prefight_score_prevents_start(self):
        status = swole_status(92, 2); del status["init"]["active_events"][0]["points"]
        runner, client, _ = self.runner([status])
        self.assertFalse(self.fight(runner))
        client.start_challenge.assert_not_called()

    def test_rejected_play_cannot_reuse_cached_winner(self):
        response = self.response(winner=1); response["result"] = False
        runner, client, events = self.runner([swole_status(92, 2)], responses=[response])
        with self.assertRaisesRegex(ATApiError, "rejected"):
            self.fight(runner)
        self.assertEqual(client.play_battlefield_card.call_count, 1)
        self.assertEqual(client.get_swole_status.call_count, 1)
        self.assertEqual(runner._fights_completed, 0)
        self.assertEqual(self.ends(events), [])

    def test_changed_battle_does_not_count_win_or_replay_move(self):
        response = self.response(winner=1); response["battle_data"]["battle_id"] = "54321"
        runner, client, events = self.runner([swole_status(92, 2)], responses=[response])
        with self.assertRaisesRegex(ATApiError, "matching"):
            self.fight(runner)
        self.assertEqual(client.play_battlefield_card.call_count, 1)
        self.assertEqual(runner._fights_completed, 0)
        self.assertEqual(self.ends(events), [])

    def test_unknown_explicit_terminal_never_counts_a_fight(self):
        runner, client, _ = self.runner([swole_status(92, 2)],
            responses=[self.response(winner="unrecognized", battle_finished=True)])
        with self.assertRaisesRegex(ATApiError, "confirmed winner"):
            self.fight(runner)
        self.assertEqual(runner._fights_completed, 0)
        self.assertEqual(client.get_swole_status.call_count, 1)

    def test_generic_results_do_not_override_next_explicit_loss(self):
        runner, client, events = self.runner([swole_status(92, 2), swole_status(92, 1)],
            responses=[self.response(results=[{"result": 1}], hand=[]), self.response(winner=0)])
        self.assertTrue(self.fight(runner))
        self.assertEqual(client.play_battlefield_card.call_count, 2)
        self.assertFalse(self.ends(events)[0]["won"])

    def test_historical_nested_start_state_is_not_treated_as_current(self):
        start = {"result": True, "cached": {"battle_data": self.state(hand=[])}}
        runner, client, _ = self.runner([swole_status(92, 2)], start=start)
        self.assertFalse(self.fight(runner))
        client.play_battlefield_card.assert_not_called()

    def test_partial_block_resume_preserves_only_unplayed_fights(self):
        queue = [{"type": "fight", "count": 3}, {"type": "train", "id": "1", "target_level": 2}]
        statuses = [swole_status(92, 3), swole_status(92, 3), swole_status(93, 2),
                    swole_status(93, 2), swole_status(93, 1)]
        runner, client, events = self.runner(statuses, queue=queue,
            responses=[self.response(winner=1), self.response(winner=1)])
        self.run_schedule(runner, client)
        self.assertEqual(events[-1]["kind"], "finished")
        self.assertEqual(events[-1]["remaining_queue"], [
            {"type": "fight", "count": 1}, queue[1]])
        self.assertEqual(client.start_challenge.call_args_list, [
            call("1070", 92, auto_battle=False), call("1070", 93, auto_battle=False)])
        client.start_swole_training.assert_not_called()

    def test_fatal_second_move_preserves_remaining_count(self):
        queue = [{"type": "fight", "count": 3}, {"type": "train", "id": "1", "target_level": 2}]
        statuses = [swole_status(92, 3), swole_status(92, 3), swole_status(93, 2), swole_status(93, 2)]
        rejected = self.response(winner=1); rejected["result"] = False
        runner, client, events = self.runner(statuses, queue=queue,
            responses=[self.response(winner=1), rejected])
        self.run_schedule(runner, client)
        self.assertEqual(events[-1]["kind"], "fatal")
        self.assertEqual(events[-1]["remaining_queue"][0]["count"], 2)
        self.assertEqual(len(self.ends(events)), 1)
        client.start_swole_training.assert_not_called()

    def test_final_uncredited_fight_does_not_replay_or_start_training(self):
        queue = [{"type": "fight", "count": 1}, {"type": "train", "id": "1", "target_level": 2}]
        runner, client, events = self.runner([
            swole_status(92, 2), swole_status(92, 2), swole_status(92, 1)], queue=queue)
        self.run_schedule(runner, client)
        self.assertEqual(events[-1]["remaining_queue"], [queue[1]])
        client.start_swole_training.assert_not_called()
        self.assertEqual(client.start_challenge.call_count, 1)

    def test_stop_before_card_keeps_whole_fight_and_training_block(self):
        queue = [{"type": "fight", "count": 2}, {"type": "train", "id": "1", "target_level": 2}]
        runner, client, events = self.runner([swole_status(92, 2), swole_status(92, 2)], queue=queue)
        def stopped_start(*_args, **_kwargs):
            runner.stop()
            return {"result": True, "battle_data": self.state(hand=[])}
        client.start_challenge.side_effect = stopped_start
        self.run_schedule(runner, client)
        self.assertEqual(events[-1]["remaining_queue"], queue)
        client.play_battlefield_card.assert_not_called()
        client.start_swole_training.assert_not_called()

    def test_stop_during_known_terminal_counts_spent_fight_without_postread(self):
        queue = [{"type": "fight", "count": 2}, {"type": "train", "id": "1", "target_level": 2}]
        runner, client, events = self.runner([swole_status(92, 2), swole_status(92, 2)], queue=queue)
        def terminal_with_stop(**_kwargs):
            runner.stop()
            return self.response(winner=1)
        client.play_battlefield_card.side_effect = terminal_with_stop
        self.run_schedule(runner, client)
        self.assertEqual(events[-1]["remaining_queue"], [
            {"type": "fight", "count": 1}, queue[1]])
        self.assertEqual(client.get_swole_status.call_count, 2)
        self.assertEqual(client.play_battlefield_card.call_count, 1)
        self.assertTrue(self.ends(events)[0]["won"])
        self.assertFalse(self.ends(events)[0]["post_status_verified"])
        client.start_swole_training.assert_not_called()


if __name__ == "__main__":
    unittest.main()
