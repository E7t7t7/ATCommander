import json
from pathlib import Path
import tempfile
import unittest

from atcommander.command_store import CommandStore
from atcommander.credentials import Credentials
from atcommander.local_commands import CommandInputError
from atcommander.utility_commands import build_service


class Client:
    credentials = Credentials("test-user-private", "test-password-private")
    def __init__(self):
        self.calls = []
        self.data = {"user_data": {"active_deck": 1, "faction_id": 9}, "user_decks": {"1": {"name": "One", "deck_id": 1, "units": [{"unit_index": 3}], "commander": {"unit_id":1001}}}, "user_units": {"3": {"unit_id": 22, "unit_index": 3, "level": 1}}}
    def init(self):
        self.calls.append(("init", {})); return self.data
    def get_exact_decks(self, data):
        return data
    def get_user_account(self):
        return self.data
    def card_catalog(self):
        return {"22": {"name": "Rogu", "card_type": "character"}, "23": {"name": "Object", "card_type": "item"}}
    def item_catalog(self):
        return {}
    def hero_catalog(self):
        return {}
    def get_hero_squad(self):
        return {"user_hero_squad": [{"hero_id":1001,"unlocked":1,"name":"Peter"}]}
    def apply_selection(self, **args):
        self.calls.append(("selection", args))
    def call(self, name, args=None):
        self.calls.append((name, args or {}))
        return {"result": True, "active_choices": [], "result_message": "ok"}


class UtilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.client = Client(); self.store = CommandStore(Path(self.temp.name)/"state.json")
        self.service = build_service(self.client, store=self.store)
    def test_invalid_args_never_call_api(self):
        with self.assertRaises(CommandInputError):
            self.service.execute("deck-rename", {"deck":1,"name":"New","unexpected":"x"})
        self.assertEqual(self.client.calls, [])
    def test_unknown_deck_is_not_mutated(self):
        with self.assertRaises(CommandInputError):
            self.service.execute("deck-rename", {"deck":7,"name":"New"})
        self.assertNotIn("setDeckName", [x[0] for x in self.client.calls])
    def test_rename_uses_current_shape(self):
        self.service.execute("deck-rename", {"deck":1,"name":"New"})
        self.assertEqual(self.client.calls[-1], ("setDeckName", {"deck_id":1,"name":"New"}))
    def test_deck_map_joins_inventory(self):
        answer = self.service.execute("deck-map", {"deck":1})
        self.assertEqual(answer.rows[0]["name"], "Rogu")
    def test_share_has_no_card_instance_ids(self):
        answer = self.service.execute("deck-share", {"deck":1})
        self.assertNotIn("unit_index", answer.details)
    def test_local_backup_does_not_change_game(self):
        self.service.execute("deck-save", {"deck":1,"name":"Backup"})
        self.assertEqual(self.store.read()["decks"]["Backup"]["commander"]["unit_id"], 1001)
        self.assertEqual([x[0] for x in self.client.calls], ["init"])
    def test_mail_claim_skips_claimed_and_rewardless(self):
        self.client.data["user_mail"] = [{"mail_id":1,"status":0,"reward":{"gold":1}}, {"mail_id":2,"status":2,"reward":{"gold":1}}, {"mail_id":3,"status":0}]
        self.service.execute("user-mail", {"mode":"claim"})
        self.assertEqual([call for call in self.client.calls if call[0]=="claimMessageRewards"], [("claimMessageRewards", {"mail_id":1,"mail_type":1})])
    def test_choice_uses_option_id_and_type(self):
        self.client.data["active_choices"] = [{"type":8,"options":[{"id":1,"give_card":[{"id":23}]},{"id":2,"give_card":[{"id":22}]}]}]
        self.service.execute("item-choose")
        self.assertEqual(self.client.calls[-1], ("confirmUserChoice", {"type":"8","choice":"2"}))
    def test_no_choice_sends_no_confirmation(self):
        self.service.execute("item-choose")
        self.assertNotIn("confirmUserChoice", [x[0] for x in self.client.calls])
    def test_guild_target_is_validated_before_network(self):
        with self.assertRaises(CommandInputError):
            self.service.execute("guild-kick", {"player":0})
        self.assertEqual(self.client.calls, [])
    def test_guild_kick_does_not_ban(self):
        self.service.execute("guild-kick", {"player":123})
        self.assertEqual(self.client.calls[-1], ("kickGuildMember", {"target_user_id":123,"ban":"0"}))
    def test_daily_claim_does_not_need_event(self):
        self.service.execute("user-claim")
        self.assertEqual(self.client.calls[-1], ("claimDailyBonus", {}))
    def test_event_claim_requires_id(self):
        with self.assertRaises(CommandInputError):
            self.service.execute("user-claim", {"type":"event"})
        self.assertEqual(self.client.calls, [])
    def test_result_redacts_unlabelled_known_credentials(self):
        self.client.call = lambda *_args: {"result_message":"test-password-private test-user-private"}
        answer = self.service.execute("user-rank")
        self.assertNotIn("test-password-private", answer.details)
        self.assertNotIn("test-user-private", answer.details)
    def test_subscription_is_local(self):
        self.service.execute("subscribe", {"type":"shop-mastery"})
        self.assertTrue(self.store.read()["subscriptions"]["shop-mastery"]["enabled"])
        self.assertEqual(self.client.calls, [])
    def test_defaults_must_be_object(self):
        with self.assertRaises(CommandInputError):
            self.service.execute("play-defaults", {"mode":"set","options":"[]"})
        self.assertEqual(self.store.read(), {})
