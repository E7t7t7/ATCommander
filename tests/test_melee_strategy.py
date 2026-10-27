"""Melee sequence and mode boundaries; synthetic battles spend no resources."""
import unittest
from unittest.mock import Mock, patch

from atcommander.api import ATClient
from atcommander.arena_runner import ArenaRunner
from atcommander.event_runner import ChallengeBattleRunner, EventBattleRunner
from atcommander.local_commands import CommandInputError
from atcommander.strategy import SingleComboRushPolicy, policy_for_label
from tests.test_signed_challenge_runners import event_payload
from tests import test_utility_commands as fixture


def recipe(character=10, item=11):
    return {"card_uid1": character, "card_uid2": item,
            "cards": {"card1": 300, "card2": 400}, "stats": {"attack": 30, "health": 90}}


def opening():
    return {"battle_id": "synthetic-battle", "host_id": "synthetic-host", "hand": [10, 11],
            "card_map": {"10": {"unit_id": 300, "attack": 5, "health": 60},
                         "11": {"unit_id": 400, "attack": 15, "health": 35}},
            "available_hand_combo_data": [recipe()]}


def continuing(*, character_board=True, history=True):
    board, hand = (100, 11) if character_board else (101, 10)
    character, item = (board, hand) if character_board else (hand, board)
    state = {"battle_id": "synthetic-battle", "host_id": "synthetic-host", "hand": [hand],
             "card_map": {str(character): {"unit_id": 300}, str(item): {"unit_id": 400}},
             "available_combo_data": [recipe(character, item)]}
    if history:
        state["turn"] = {"1": {"plays": [{"card1_uid": board}]}}
    return state


class MeleePolicyTests(unittest.TestCase):
    def test_default_opens_item_and_option_opens_character_from_same_hand(self):
        original = opening()
        self.assertEqual(SingleComboRushPolicy().choose(original).card_uid, 11)
        chosen = SingleComboRushPolicy(character_first=True).choose(original)
        self.assertEqual((chosen.action, chosen.card_uid, chosen.card_uid2), ("play", 10, None))
        self.assertEqual(original, opening())

    def test_character_identity_survives_reversed_uids_and_integer_card_map_keys(self):
        state = opening()
        state["available_hand_combo_data"] = [recipe(11, 10)]
        state["card_map"] = {int(uid): value for uid, value in state["card_map"].items()}
        self.assertEqual(SingleComboRushPolicy(character_first=True).choose(state).card_uid, 10)

    def test_character_first_completes_with_item_on_the_existing_character(self):
        policy = SingleComboRushPolicy(character_first=True)
        self.assertEqual(policy.choose(opening()).card_uid, 10)
        decision = policy.choose(continuing())
        self.assertEqual((decision.action, decision.card_uid, decision.card_uid2), ("combo", 11, 100))

    def test_hand_proves_combo_direction_even_without_turn_history(self):
        decision = SingleComboRushPolicy(character_first=True).choose(continuing(history=False))
        self.assertEqual((decision.card_uid, decision.card_uid2), (11, 100))

    def test_item_lane_from_fallback_still_completes_in_the_legal_direction(self):
        for history in (True, False):
            with self.subTest(history=history):
                decision = SingleComboRushPolicy(character_first=True).choose(
                    continuing(character_board=False, history=history))
                self.assertEqual((decision.card_uid, decision.card_uid2), (10, 101))

    def test_combo_has_priority_over_another_character_opening(self):
        state = continuing()
        state["available_hand_combo_data"] = opening()["available_hand_combo_data"]
        self.assertEqual(SingleComboRushPolicy(character_first=True).choose(state).action, "combo")

    def test_unknown_character_identity_uses_only_game_legal_fallback(self):
        state = opening()
        state["available_hand_combo_data"][0].pop("cards")
        state["auto_play"] = [11]
        decision = SingleComboRushPolicy(character_first=True).choose(state)
        self.assertEqual((decision.action, decision.card_uid), ("play", 11))
        self.assertIn("fallback", decision.reason)

    def test_style_tie_breakers_survive_character_first_option(self):
        for label, style in (("Item → Combo (default)", "default"), ("Aggressive", "aggressive"), ("Durable", "durable")):
            with self.subTest(label=label):
                policy = policy_for_label(label, character_first=True)
                self.assertEqual(policy.style, style)
                self.assertEqual(policy.choose(opening()).card_uid, 10)

    def test_arena_rumble_and_siege_policies_still_open_items(self):
        for runner in (ArenaRunner(), EventBattleRunner("Rumble"), EventBattleRunner("Siege")):
            with self.subTest(runner=type(runner).__name__):
                self.assertEqual(runner.policy.choose(opening()).card_uid, 11)


class MeleeRunnerTests(unittest.TestCase):
    def test_off_is_the_existing_melee_logic_and_nonmelee_does_not_enable_it(self):
        for name, enabled, expected in (("Archer Melee", False, 11), ("Archer Melee", True, 10),
                                        ("Hangover Clash", True, 11), ("Finger Cutter Challenge", True, 11)):
            with self.subTest(name=name, enabled=enabled):
                runner = ChallengeBattleRunner("1", "2", name, melee_character_first=enabled)
                self.assertEqual(runner.policy.choose(opening()).card_uid, expected)

    def test_fresh_event_name_controls_each_fight_instead_of_stale_gui_name(self):
        for old_name, live_name, expected in (("Archer Melee", "Hangover Clash", 11),
                                               ("Fixture Challenge", "Archer Melee", 10)):
            with self.subTest(live_name=live_name):
                client = Mock()
                client.init.side_effect = [event_payload(), event_payload(name=live_name), event_payload(energy=0)]
                client.start_challenge.return_value = {"result": True, "battle_data": opening()}
                client.extract_battle_data.side_effect = ATClient.extract_battle_data
                client.opponent_name.return_value = "Fixture"
                client.battle_finished.return_value = True
                client.extract_outcome.return_value = True
                events = []
                with patch("atcommander.event_runner.ATClient", return_value=client), \
                     patch("atcommander.event_runner.load_credentials"), \
                     patch("atcommander.event_runner.ad_preflight"), \
                     patch("atcommander.event_runner.LifetimeTracker"):
                    runner = ChallengeBattleRunner("900042", "1070", old_name,
                                                   melee_character_first=True, event=events.append)
                    runner._stop.wait = Mock(return_value=False)
                    runner._run()
                self.assertEqual(client.play_battlefield_card.call_args.kwargs["card_uid"], expected)
                self.assertEqual(events[-1]["kind"], "finished")


class MeleeCommandTests(unittest.TestCase):
    setUp = fixture.UtilityTests.setUp

    def test_other_modes_and_game_ai_skip_reject_option_before_game_calls(self):
        for args in ({"mode": "arena"}, {"mode": "clash"}, {"mode": "challenge"},
                     {"mode": "swole"}, {"mode": "rumble"}, {"mode": "siege"},
                     {"mode": "melee", "skip": True}):
            with self.subTest(args=args):
                self.client.calls.clear()
                with self.assertRaises(CommandInputError):
                    self.service.execute("play", {**args, "melee_character_first": True})
                self.assertFalse(self.client.calls)

    def configure_battle(self, name="Archer Melee"):
        self.client.data = event_payload(name=name)
        self.client.start_challenge = Mock(return_value={"result": True, "battle_data": opening()})
        self.client.extract_battle_data = ATClient.extract_battle_data
        self.client.battle_finished = ATClient.battle_finished
        self.client.extract_outcome = ATClient.extract_outcome
        self.client.opponent_name = ATClient.opponent_name
        self.client.play_battlefield_card = Mock(side_effect=[
            {"result": True, "battle_data": continuing()},
            {"result": True, "battle_data": {"battle_id": "synthetic-battle", "winner": 1}},
        ])

    def test_serial_melee_play_uses_character_then_item_combo(self):
        self.configure_battle()
        with patch("atcommander.arena_runner.time.sleep"):
            result = self.service.execute("play", {"mode": "melee", "event": 900042,
                                                    "melee_character_first": True})
        self.assertEqual(len(result.rows), 1)
        actions = [call.kwargs for call in self.client.play_battlefield_card.call_args_list]
        self.assertEqual([(call["card_uid"], call["card_uid2"]) for call in actions], [(10, None), (11, 100)])

    def test_clash_cannot_be_mislabeled_melee_to_apply_character_first(self):
        self.configure_battle(name="Hangover Clash")
        with self.assertRaises(CommandInputError):
            self.service.execute("play", {"mode": "melee", "event": 900042, "melee_character_first": True})
        self.client.start_challenge.assert_not_called()

    def test_option_is_off_in_the_command_schema(self):
        play = next(spec for spec in self.service.specs() if spec.name == "play")
        self.assertIs(next(arg for arg in play.args if arg.name == "melee_character_first").default, False)


if __name__ == "__main__":
    unittest.main()
