import copy
import unittest
from unittest.mock import patch

from tests import test_utility_commands as fixture
from tests.test_pvp_challenge import event
from atcommander.local_commands import CommandInputError, CommandUnavailable
from atcommander.takeover_quests import takeover_status


class PlayPvPTakeoverTests(unittest.TestCase):
    setUp = fixture.UtilityTests.setUp

    def prepare_pvp(self, *, level=0, energy=2):
        self.client.data.update(time=1000, active_events=[event(level=level, energy=energy)])
        self.service.context.clock = lambda: 1000
        self.client.extract_battle_data = lambda answer: answer.get("battle_data")
        self.client.opponent_name = lambda state: "Fixture opponent"

        def start(cid, current_level):
            self.client.calls.append(("start_challenge", {"id": cid, "level": current_level}))
            return {"result": True, "battle_data": {"battle_id": "fixture", "host_id": "fixture-host"}}
        self.client.start_challenge = start

    def drive(self, runner, state, battle, opponent):
        challenge = self.client.data["active_events"][0]["challenge_data"]
        challenge["level"] += 1
        challenge["energy"]["current_value"] -= 1
        return True, 3, {"result": True}, {"battle_finished": True}

    def starts(self):
        return [payload for name, payload in self.client.calls if name == "start_challenge"]

    def test_pvp_uses_fresh_inner_id_level_and_established_driver_each_fight(self):
        self.prepare_pvp()
        calls = []

        def drive(runner, state, battle, opponent):
            calls.append((battle, opponent))
            return self.drive(runner, state, battle, opponent)
        with patch("atcommander.arena_runner.ArenaRunner._drive_one", new=drive):
            answer = self.service.execute("play", {"mode": "pvp-challenge", "count": 2})
        self.assertEqual(self.starts(), [{"id": "741", "level": 0}, {"id": "741", "level": 1}])
        self.assertEqual(calls, [(1, "Fixture opponent"), (2, "Fixture opponent")])
        self.assertEqual([row["mode"] for row in answer.rows], ["pvp-challenge"] * 2)
        self.assertEqual(len([name for name, _ in self.client.calls if name == "init"]), 2)

    def test_pvp_reserve_and_maximum_level_prevent_starts(self):
        for options in ({"reserve": 2}, {"max_level": 1}):
            with self.subTest(options=options):
                self.prepare_pvp(level=1)
                self.client.calls.clear()
                answer = self.service.execute("play", {"mode": "pvp-challenge", **options})
                self.assertEqual(answer.rows, [])
                self.assertEqual(self.starts(), [])

    def test_pvp_explicit_selection_does_not_redirect_to_another_event(self):
        self.prepare_pvp()
        self.client.data["active_events"].append(event("42", challenge_type=1))
        with self.assertRaises(CommandUnavailable):
            self.service.execute("play", {"mode": "pvp-challenge", "event": 42})
        self.assertEqual(self.starts(), [])

    def test_ambiguous_pvp_requires_id_before_fighting(self):
        self.prepare_pvp()
        self.client.data["active_events"].append(event("42"))
        with self.assertRaises(CommandUnavailable):
            self.service.execute("play", {"mode": "pvp-challenge"})
        self.assertEqual(self.starts(), [])

    def test_rejected_pvp_start_is_not_replayed(self):
        self.prepare_pvp()
        self.client.start_challenge = lambda cid, level: (
            self.client.calls.append(("start_challenge", {"id": cid, "level": level}))
            or {"result": False, "result_message": "Fixture rejection"})
        with patch("atcommander.arena_runner.ArenaRunner._drive_one") as driver:
            with self.assertRaises(CommandUnavailable):
                self.service.execute("play", {"mode": "pvp-challenge", "count": 3})
            driver.assert_not_called()
        self.assertEqual(len(self.starts()), 1)

    def test_melees_preserve_real_inner_ids_and_safe_rejection_reason(self):
        self.prepare_pvp()
        self.client.data["active_events"]=[{**event("94041"),"challenge_data":{**event()["challenge_data"],"id":5501,"level":13}},
                                         {**event("94042"),"challenge_data":{**event()["challenge_data"],"id":5502,"level":14}}]
        def start(cid,level):
            self.client.calls.append(("start_challenge",{"id":cid,"level":level}))
            return {"result":False,"result_message":"Fixture rejection test-password-private"}
        self.client.start_challenge=start
        for event_id,cid,level in ((94041,"5501",13),(94042,"5502",14)):
            self.client.calls.clear()
            with self.assertRaises(CommandUnavailable) as caught:self.service.execute("play",{"mode":"melee","event":event_id,"count":5})
            self.assertEqual(self.starts(),[{"id":cid,"level":level}])
            self.assertIn("Fixture rejection",str(caught.exception));self.assertIn(f"challenge {cid}, level {level}",str(caught.exception))
            self.assertNotIn("test-password-private",str(caught.exception))

    def test_explicit_inactive_melee_skips_once_and_returns_current_event_identity(self):
        self.prepare_pvp()
        self.client.data["active_events"]=[{**event("94041"),"challenge_data":{**event()["challenge_data"],"id":5501,"level":13}}]
        self.client.start_challenge=lambda cid,level:(self.client.calls.append(("start_challenge",{"id":cid,"level":level})) or {"result":False,"result_message":["Challenge event not active.","warning_126_text"]})
        with patch("atcommander.arena_runner.ArenaRunner._drive_one") as driver:
            answer=self.service.execute("play",{"mode":"melee","event":94041,"count":5})
            driver.assert_not_called()
        self.assertEqual(self.starts(),[{"id":"5501","level":13}]);self.assertEqual(answer.rows,[])
        self.assertEqual(answer.data["unavailable_event"]["event_id"],"94041")
        self.assertEqual(answer.data["unavailable_event"]["code"],"warning_126_text")
        self.assertIn("event was skipped",answer.message)

    def test_unrecognized_or_non_rejected_inactive_text_still_stops_as_error(self):
        for response in ({"result":False,"result_message":"warning_126_text_other"},
                         {"result":True,"result_message":"warning_126_text"},
                         {"result":False,"result_message":"Challenge event not active."}):
            self.prepare_pvp();self.client.calls.clear()
            self.client.start_challenge=lambda cid,level,response=response:(self.client.calls.append(("start_challenge",{"id":cid,"level":level})) or response)
            with self.assertRaises(CommandUnavailable):self.service.execute("play",{"mode":"pvp-challenge","count":5})
            self.assertEqual(len(self.starts()),1)

    def test_pvp_token_selection_remains_explicitly_unavailable_without_requests(self):
        self.prepare_pvp()
        with self.assertRaisesRegex(CommandUnavailable, "token-target"):
            self.service.execute("play", {"mode": "pvp-challenge", "hero_tokens": "Peter"})
        self.assertEqual(self.client.calls, [])

    def test_arena_token_targeting_keeps_existing_endpoint_and_no_refresh(self):
        self.prepare_pvp()
        self.client.data["user_data"]["stamina"] = 2
        def call(name, payload=None):
            self.client.calls.append((name, payload or {}))
            if name == "getHuntingTargets":
                return {"hunting_targets": [{"user_id": "fixture-opponent", "hero_xp_id": 1001}]}
            return {"result": True, "battle_data": {"battle_id": "fixture", "host_id": "fixture-host"}}
        self.client.call = call
        with patch("atcommander.arena_runner.ArenaRunner._drive_one", return_value=(True, 3, {}, {})):
            answer = self.service.execute("play", {"mode": "arena", "hero_tokens": "1001"})
        self.assertEqual(len(answer.rows), 1)
        self.assertIn(("startHuntingBattle", {"target_user_id": "fixture-opponent"}), self.client.calls)
        self.assertNotIn("refreshHuntingTargets", [name for name, _ in self.client.calls])

    def test_absent_takeover_is_checked_before_any_optional_mutations(self):
        self.client.data["user_achievements"] = []
        with self.assertRaisesRegex(CommandUnavailable, "No current Takeover"):
            self.service.execute("play", {"takeover_quest_mode": "arena", "claim_daily": True,
                                           "buy_basic": True, "deck": 1})
        self.assertEqual([name for name, _ in self.client.calls], ["init"])

    def test_takeover_mode_mismatch_fails_before_reading_account(self):
        with self.assertRaises(CommandInputError):
            self.service.execute("play", {"mode": "swole", "takeover_quest_mode": "arena"})
        self.assertEqual(self.client.calls, [])

    def test_takeover_preview_reports_unverified_requirement_without_raw_values(self):
        self.client.data["user_achievements"] = [{"id": "1", "name": "Takeover", "status": 0,
            "progress": 1, "max_progress": 5, "end_time": 2000,
            "req": {"hero_requirement": {"hero_id": 1001}, "password": "fixture-private-value"}}]
        self.service.context.clock = lambda: 1000
        answer = self.service.execute("takeover-status")
        self.assertEqual(answer.data["status"], "unsupported")
        self.assertEqual(answer.rows[0]["requirement_fields"], ["hero_requirement", "hero_requirement.hero_id"])
        self.assertNotIn("fixture-private-value", str(answer))
        self.assertEqual([name for name, _ in self.client.calls], ["init"])

    def test_complete_takeover_stops_without_hero_or_battle_changes(self):
        self.client.data["user_achievements"] = [{"name": "Takeover", "status": 1,
            "progress": 5, "max_progress": 5, "end_time": 2000}]
        self.service.context.clock = lambda: 1000
        answer = self.service.execute("play", {"takeover_quest_mode": "arena", "claim_daily": True})
        self.assertEqual(answer.data["takeover_status"], "complete")
        self.assertEqual([name for name, _ in self.client.calls], ["init"])


class TakeoverEvidenceTests(unittest.TestCase):
    def test_missing_rejected_and_expired_quests_are_distinct(self):
        self.assertEqual(takeover_status({}, 1000)["status"], "unavailable")
        self.assertEqual(takeover_status({"result": False, "user_achievements": []}, 1000)["status"], "unavailable")
        quest = {"name": "Takeover", "status": 0, "progress": 0, "max_progress": 3}
        self.assertEqual(takeover_status({"user_achievements": [quest]}, 1000)["status"], "unavailable")
        self.assertEqual(takeover_status({"user_achievements": [{**quest, "end_time": 999}]}, 1000)["status"], "no_match")

    def test_takeover_child_inherits_parent_label_and_expiry_without_changing_input(self):
        live = {"user_achievements": [{"name": "Show Takeover", "end_time": 2000, "sub_quests": {
            "1": {"name": "First stage", "status": 0, "progress": 1, "max_progress": 3, "req": {"unknown_hero_shape": "unverified"}}
        }}]}
        before = copy.deepcopy(live)
        answer = takeover_status(live, 1000)
        self.assertEqual(answer["status"], "unsupported")
        self.assertEqual(answer["quests"][0]["quest"], "1")
        self.assertEqual(answer["quests"][0]["progress"], 1)
        self.assertEqual(live, before)


if __name__ == "__main__":
    unittest.main()
