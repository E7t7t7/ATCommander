import unittest
from unittest.mock import patch

import requests

from atcommander.api import ATClient, ATApiError
from atcommander.credentials import Credentials
from atcommander.combat_signing import (DEFAULT_CLIENT_VERSION, DEFAULT_CLIENT_VERSION_FULL,
                                       DEFAULT_PLATFORM, DEFAULT_UNITY, generate_signatures)


class Response:
    content = b'{}'
    text = '{}'

    def __init__(self, response=None):
        self.response = {"result": True} if response is None else response

    def raise_for_status(self):
        pass

    def json(self):
        return self.response


class Session:
    def __init__(self):
        self.requests = []
        self.responses = []

    def post(self, url, **kwargs):
        self.requests.append(kwargs)
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return Response(response)
        return Response()


class SignedTransportTests(unittest.TestCase):
    def setUp(self):
        self.profile = dict(client_version=DEFAULT_CLIENT_VERSION, client_version_full=DEFAULT_CLIENT_VERSION_FULL,
                            platform=DEFAULT_PLATFORM, unity=DEFAULT_UNITY)
        resolver = patch("atcommander.api.resolve_public_client_profile", return_value=dict(self.profile))
        self.resolve = resolver.start()
        self.addCleanup(resolver.stop)
        self.client = ATClient(Credentials("12345", "synthetic_password"))
        self.session = self.client.session = Session()

    def test_challenge_replaces_stale_capture_fields_and_signs_every_start(self):
        stale = {"challenge_id": "1070", "level": "92", "auto_battle": "0",
                 "client_time": "1", "msgsig": "stale", "clisig": "stale", "client_version": "old"}
        for timestamp in (2000000000, 2000000001):
            with patch("atcommander.combat_signing.time.time", return_value=timestamp):
                self.client.call("startChallenge", stale)
            sent = self.session.requests[-1]
            payload = sent["data"]
            self.assertEqual(sent["params"]["message"], "startChallenge")
            self.assertEqual(payload["client_time"], str(timestamp))
            self.assertEqual(payload["client_version"], DEFAULT_CLIENT_VERSION)
            self.assertEqual(payload["platform"], "Web")
            expected = generate_signatures("12345", "startChallenge", timestamp)
            self.assertEqual({key: payload[key] for key in expected}, expected)
            self.assertEqual(payload["auto_battle"], "0")
        self.assertEqual(stale["msgsig"], "stale")
        self.assertNotEqual(self.session.requests[0]["data"]["msgsig"], self.session.requests[1]["data"]["msgsig"])

    def test_proven_arena_path_keeps_its_payload(self):
        self.client.call("startBattle", {"auto_battle": "0", "client_time": "222"})
        payload = self.session.requests[-1]["data"]
        self.assertEqual(payload["client_time"], "222")
        self.assertNotIn("msgsig", payload)
        self.assertNotIn("clisig", payload)

    def test_challenge_requires_live_id_and_level_but_allows_level_zero(self):
        for challenge_id, level in (("", 1), ("abc", 1), ("1070", None), ("1070", -1)):
            with self.assertRaises(ATApiError):
                self.client.start_challenge(challenge_id, level)
        self.assertEqual(self.session.requests, [])
        self.client.start_challenge("1070", 0)
        self.assertEqual(self.session.requests[-1]["data"]["level"], "0")

    def test_siege_uses_public_slot_only_shape_and_fresh_signatures(self):
        with patch("atcommander.combat_signing.time.time", return_value=2000000000):
            self.client.start_siege("2")
        sent = self.session.requests[-1]
        self.assertEqual(sent["params"]["message"], "fightGuildSiege")
        self.assertEqual(sent["data"]["slot_id"], "2")
        self.assertNotIn("combat_data", sent["data"])
        expected = generate_signatures("12345", "fightGuildSiege", 2000000000)
        self.assertEqual({key: sent["data"][key] for key in expected}, expected)

    def accept_signed_battle(self, message="startChallenge", battle_id=7001, *, wrapped=False):
        state = {"battle_id": battle_id, "host_id": "12345", "hand": []}
        self.session.responses.append({"result": True, **(
            {"data": {"battle_data": state}} if wrapped else {"battle_data": state})})
        self.client.call(message, {"challenge_id": "1070", "level": "92"}
                         if message == "startChallenge" else {"slot_id": "2"})

    def assert_fresh_signature(self, message, timestamp):
        payload = self.session.requests[-1]["data"]
        expected = generate_signatures("12345", message, timestamp)
        self.assertEqual({key: payload[key] for key in expected}, expected)
        self.assertEqual(payload["client_time"], str(timestamp))
        self.assertEqual(payload["client_version"], DEFAULT_CLIENT_VERSION)

    def test_accepted_challenge_signs_each_move_with_normalized_battle_id(self):
        self.accept_signed_battle(battle_id=7001)
        for timestamp, battle_id in ((2000000000, "7001"), (2000000001, 7001)):
            with patch("atcommander.combat_signing.time.time", return_value=timestamp):
                self.client.play_battlefield_card(
                    battle_id=battle_id, host_id="12345", card_uid="12", card_uid2="13")
            self.assert_fresh_signature("playBattlefieldCard", timestamp)
            payload = self.session.requests[-1]["data"]
            self.assertEqual(payload["card_uid"], "12")
            self.assertEqual(payload["card_uid2"], "13")
        self.assertNotEqual(self.session.requests[-2]["data"]["msgsig"],
                            self.session.requests[-1]["data"]["msgsig"])

    def test_accepted_siege_start_signs_moves(self):
        self.accept_signed_battle("fightGuildSiege")
        with patch("atcommander.combat_signing.time.time", return_value=2000000000):
            self.client.play_battlefield_card(battle_id=7001, host_id="12345", card_uid="12")
        self.assert_fresh_signature("playBattlefieldCard", 2000000000)

    def test_wrapped_historical_start_state_does_not_promote_moves(self):
        self.accept_signed_battle(wrapped=True)
        self.client.play_battlefield_card(battle_id=7001, host_id="12345", card_uid="12")
        self.assertNotIn("msgsig", self.session.requests[-1]["data"])

    def test_signed_skip_replaces_stale_signatures_and_preserves_input(self):
        self.accept_signed_battle()
        fields = {"battle_id": "7001", "host_id": "12345", "skip": "1",
                  "client_time": "1", "msgsig": "stale", "clisig": "stale"}
        with patch("atcommander.combat_signing.time.time", return_value=2000000000):
            self.client.call("playCard", fields)
        self.assert_fresh_signature("playCard", 2000000000)
        self.assertEqual(self.session.requests[-1]["data"]["skip"], "1")
        self.assertEqual(fields["msgsig"], "stale")
        self.assertEqual(fields["client_time"], "1")

    def test_untracked_battle_moves_keep_established_unsigned_transport(self):
        self.accept_signed_battle()
        for message in ("playBattlefieldCard", "playCard"):
            self.client.call(message, {"battle_id": "7002", "host_id": "12345",
                                       "card_uid": "12", "client_time": "222"})
            payload = self.session.requests[-1]["data"]
            self.assertNotIn("msgsig", payload)
            self.assertNotIn("clisig", payload)
            self.assertEqual(payload["client_time"], "222")

    def test_other_messages_are_unsigned_even_with_tracked_battle_id(self):
        self.accept_signed_battle()
        for message in ("getUserAccount", "startBattle", "fightGuildWar", "getBattleResults"):
            self.client.call(message, {"battle_id": "7001", "client_time": "222"})
            payload = self.session.requests[-1]["data"]
            self.assertNotIn("msgsig", payload)
            self.assertNotIn("clisig", payload)
            self.assertEqual(payload["client_time"], "222")

    def test_refused_start_never_promotes_its_battle_id(self):
        for rejection in (False, 0, "0", "false"):
            with self.subTest(rejection=rejection):
                self.session.responses.append({"result": rejection, "battle_data": {
                    "battle_id": "7001", "host_id": "12345", "hand": []}})
                self.client.start_challenge("1070", 92)
                self.client.play_battlefield_card(battle_id="7001", host_id="12345", card_uid="12")
                self.assertNotIn("msgsig", self.session.requests[-1]["data"])

    def test_missing_battle_id_start_does_not_sign_later_moves(self):
        self.session.responses.append({"result": True, "battle_data": {"hand": []}})
        self.client.start_challenge("1070", 92)
        self.client.play_battlefield_card(battle_id="7001", host_id="12345", card_uid="12")
        self.assertNotIn("msgsig", self.session.requests[-1]["data"])

    def test_start_requires_explicit_accepted_result_to_promote_battle(self):
        for marker in (None, "unknown", 2):
            with self.subTest(marker=marker):
                self.session.responses.append({"result": marker, "battle_data": {
                    "battle_id": "7001", "host_id": "12345", "hand": []}})
                self.client.start_challenge("1070", 92)
                self.client.play_battlefield_card(battle_id="7001", host_id="12345", card_uid="12")
                self.assertNotIn("msgsig", self.session.requests[-1]["data"])
        self.session.responses.append({"battle_data": {
            "battle_id": "7001", "host_id": "12345", "hand": []}})
        self.client.start_challenge("1070", 92)
        self.client.play_battlefield_card(battle_id="7001", host_id="12345", card_uid="12")
        self.assertNotIn("msgsig", self.session.requests[-1]["data"])

    def test_supported_explicit_acceptance_markers_authorize_matching_moves(self):
        for marker, battle_id in ((True, 7001), (1, 7002), ("true", 7003), ("1", 7004)):
            with self.subTest(marker=marker):
                self.session.responses.append({"result": marker, "battle_data": {
                    "battle_id": battle_id, "host_id": "12345", "hand": []}})
                self.client.start_challenge("1070", 92)
                with patch("atcommander.combat_signing.time.time", return_value=2000000000):
                    self.client.play_battlefield_card(battle_id=battle_id, host_id="12345", card_uid="12")
                self.assert_fresh_signature("playBattlefieldCard", 2000000000)

    def test_network_failed_start_never_promotes_later_move(self):
        self.session.responses.append(requests.ConnectionError("synthetic transport failure"))
        with self.assertRaises(ATApiError):
            self.client.start_challenge("1070", 92)
        self.client.play_battlefield_card(battle_id="7001", host_id="12345", card_uid="12")
        self.assertNotIn("msgsig", self.session.requests[-1]["data"])

    def test_arena_and_rumble_start_battle_data_do_not_promote_moves(self):
        for message, battle_id in (("startBattle", 7001), ("fightGuildWar", 7002)):
            self.session.responses.append({"result": True, "battle_data": {
                "battle_id": battle_id, "host_id": "12345", "hand": []}})
            self.client.call(message, {"auto_battle": "0"})
            self.client.play_battlefield_card(battle_id=battle_id, host_id="12345", card_uid="12")
            self.assertNotIn("msgsig", self.session.requests[-1]["data"])

    def test_battle_scope_belongs_to_each_client(self):
        self.accept_signed_battle()
        other = ATClient(Credentials("12345", "synthetic_password"))
        other.session = Session()
        other.play_battlefield_card(battle_id="7001", host_id="12345", card_uid="12")
        self.assertNotIn("msgsig", other.session.requests[-1]["data"])

    def test_failed_signed_move_is_not_replayed_and_redacts_echoed_fields(self):
        self.accept_signed_battle()
        signatures = generate_signatures("12345", "playBattlefieldCard", 2000000000)
        private = ["12345", "synthetic_password", *signatures.values()]
        self.session.responses.append({"error": {"message":
            "warning_157 Invalid combat data: " + " / ".join(private)}})
        before = len(self.session.requests)
        with patch("atcommander.combat_signing.time.time", return_value=2000000000):
            with self.assertRaises(ATApiError) as caught:
                self.client.play_battlefield_card(battle_id="7001", host_id="12345", card_uid="12")
        self.assertEqual(len(self.session.requests), before + 1)
        self.assertIn("warning_157", str(caught.exception))
        for value in private:
            self.assertNotIn(value, str(caught.exception))

    def test_tracking_cache_is_bounded_without_discarding_new_battle(self):
        for battle_id in range(7001, 7131):
            self.accept_signed_battle(battle_id=battle_id)
        self.client.play_battlefield_card(battle_id=7001, host_id="12345", card_uid="12")
        self.assertNotIn("msgsig", self.session.requests[-1]["data"])
        with patch("atcommander.combat_signing.time.time", return_value=2000000000):
            self.client.play_battlefield_card(battle_id=7130, host_id="12345", card_uid="12")
        self.assert_fresh_signature("playBattlefieldCard", 2000000000)

    def newer_profile(self):
        return {**self.profile, "client_version": "1182000", "client_version_full": "1.182.0"}

    def test_current_public_profile_is_used_for_start_and_all_moves(self):
        self.resolve.return_value = self.newer_profile()
        self.accept_signed_battle()
        self.resolve.return_value = dict(self.profile)
        self.client.play_battlefield_card(battle_id=7001, host_id="12345", card_uid="12")
        for sent in self.session.requests:
            self.assertEqual(sent["data"]["client_version"], "1182000")
            self.assertEqual(sent["data"]["client_version_full"], "1.182.0")
        self.assertEqual(self.resolve.call_count, 1)

    def test_explicit_validation_rejection_retries_once_only_after_verified_version_change(self):
        self.resolve.side_effect = [dict(self.profile), self.newer_profile()]
        self.session.responses.extend([
            {"result": False, "result_message": ["warning_157_text"]},
            {"result": True, "battle_data": {"battle_id": 7001, "host_id": "12345", "hand": []}},
        ])
        result = self.client.start_challenge("1070", 104)
        self.assertTrue(result["result"])
        self.assertEqual(len(self.session.requests), 2)
        self.resolve.assert_called_with(force_refresh=True)
        self.assertEqual(self.session.requests[-1]["data"]["client_version"], "1182000")
        self.assertEqual(self.session.requests[-1]["data"]["level"], "104")
        self.assertEqual(self.client._signed_battle_ids["7001"], self.newer_profile())

    def test_same_version_validation_rejection_is_not_retried(self):
        self.session.responses.append({"result": False, "result_message": ["warning_157_text"]})
        self.client.start_challenge("1070", 104)
        self.assertEqual(len(self.session.requests), 1)
        self.assertEqual(self.resolve.call_count, 2)

    def test_second_rejected_start_is_not_retried_again(self):
        self.resolve.side_effect = [dict(self.profile), self.newer_profile()]
        self.session.responses.extend([{"result": False, "result_message": ["warning_157_text"]}] * 2)
        self.client.start_challenge("1070", 104)
        self.assertEqual(len(self.session.requests), 2)
        self.assertEqual(self.resolve.call_count, 2)
        self.assertFalse(self.client._signed_battle_ids)

    def test_ambiguous_or_other_rejection_never_refreshes_or_replays_start(self):
        for response in ({"result": True}, {}, {"result": False, "result_message": ["warning_other"]},
                         {"result": False, "result_message": ["warning_157_text"], "battle_data": {"battle_id": 7001}}):
            with self.subTest(response=response):
                self.session.requests.clear()
                self.resolve.reset_mock()
                self.session.responses.append(response)
                self.client.start_challenge("1070", 104)
                self.assertEqual(len(self.session.requests), 1)
                self.assertEqual(self.resolve.call_count, 1)

    def test_unverified_public_build_fails_before_resource_request(self):
        from atcommander.public_client_version import PublicClientVersionError
        self.resolve.side_effect = PublicClientVersionError("The current public game build is unsupported.", reason="unsupported_build")
        with self.assertRaisesRegex(ATApiError, "unsupported"):
            self.client.start_challenge("1070", 104)
        self.assertFalse(self.session.requests)

    def test_arena_does_not_need_public_build_lookup(self):
        self.resolve.side_effect = RuntimeError("unneeded lookup")
        self.client.call("startBattle", {"auto_battle": "0"})
        self.resolve.assert_not_called()

    def test_refreshed_start_error_redacts_both_attempts_signatures(self):
        self.resolve.side_effect = [dict(self.profile), self.newer_profile()]
        original = generate_signatures("12345", "startChallenge", 2000000000)
        current = generate_signatures("12345", "startChallenge", 2000000000, "1182000")
        self.session.responses.extend([
            {"result": False, "result_message": ["warning_157_text"]},
            {"error": {"message": "Rejected echoed forms: " + " / ".join([*original.values(), *current.values()])}},
        ])
        with patch("atcommander.combat_signing.time.time", return_value=2000000000):
            with self.assertRaises(ATApiError) as caught:
                self.client.start_challenge("1070", 104)
        for value in [*original.values(), *current.values()]:
            self.assertNotIn(value, str(caught.exception))
        self.assertEqual(len(self.session.requests), 2)

    def test_contradictory_or_missing_acceptance_marker_with_warning_is_never_replayed(self):
        for response in ({"result": True, "error": "warning_157_text"}, {"error": "warning_157_text"}):
            with self.subTest(response=response):
                self.session.requests.clear()
                self.resolve.reset_mock()
                self.resolve.return_value = self.newer_profile()
                self.session.responses.append(response)
                with self.assertRaises(ATApiError):
                    self.client.start_challenge("1070", 104)
                self.assertEqual(len(self.session.requests), 1)
                self.assertEqual(self.resolve.call_count, 1)

    def test_stop_during_public_version_lookup_prevents_resource_request(self):
        stopped = [False]
        self.client._should_stop = lambda: stopped[0]
        def resolve(**_kwargs):
            stopped[0] = True
            return self.newer_profile()
        self.resolve.side_effect = resolve
        with self.assertRaisesRegex(ATApiError, "stopped before sending"):
            self.client.start_challenge("1070", 104)
        self.assertFalse(self.session.requests)

    def test_stop_during_rejection_refresh_prevents_second_start(self):
        stopped = [False]
        self.client._should_stop = lambda: stopped[0]
        def resolve(**kwargs):
            if kwargs.get("force_refresh"):
                stopped[0] = True
                return self.newer_profile()
            return dict(self.profile)
        self.resolve.side_effect = resolve
        self.session.responses.append({"result": False, "result_message": ["warning_157_text"]})
        with self.assertRaisesRegex(ATApiError, "stopped before sending"):
            self.client.start_challenge("1070", 104)
        self.assertEqual(len(self.session.requests), 1)

    def test_stopped_signed_move_is_not_sent_or_replayed(self):
        self.accept_signed_battle()
        self.client._should_stop = lambda: True
        with self.assertRaisesRegex(ATApiError, "stopped before sending"):
            self.client.play_battlefield_card(battle_id=7001, host_id="12345", card_uid="12")
        self.assertEqual(len(self.session.requests), 1)


if __name__ == "__main__":
    unittest.main()
