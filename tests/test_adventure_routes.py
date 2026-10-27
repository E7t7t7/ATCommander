import unittest
from unittest.mock import patch
import test_utility_commands as fixture
from atcommander.local_commands import CommandUnavailable


class AdventureRouteTests(unittest.TestCase):
    setUp=fixture.UtilityTests.setUp

    def prepare(self):
        self.service.context._catalogs["campaign"]={"1":{"name":"One","missions":[101,102,103]},"2":{"name":"Two","campaign_req":"1","missions":[104,105,106]},"9040":{"name":"Event","missions":[9256]}}
        self.client.data.update(current_campaigns={"1":{"completed":1},"9040":{"completed":1}},current_missions={str(mid):{"energy":5,"level":1} for mid in (101,102,103,104,105,106,9256)},mission_completions={"101":{"mission_id":101,"complete":12},"102":{"mission_id":102,"complete":1},"103":{"mission_id":103,"complete":1}})

    def test_fixed_stage_uses_catalog_link(self):
        self.prepare()
        answer=self.service.execute("adventure-route",{"level":"1-2"})
        self.assertEqual(answer.rows[0]["mission"],102)
        self.assertEqual(answer.rows[0]["energy_cost"],5)
        self.assertNotIn("startMission",[name for name,_ in self.client.calls])

    def test_progress_selects_latest_unlocked_story_stage(self):
        self.prepare()
        answer=self.service.execute("adventure-route",{"level":"progress"})
        self.assertEqual(answer.rows[0]["mission"],104)
        self.assertNotEqual(answer.rows[0]["mission"],9256)

    def test_mastery_uses_first_incomplete_story_stage(self):
        self.prepare()
        self.assertEqual(self.service.execute("adventure-route",{"level":"mastery"}).rows[0]["mission"],102)

    def test_locked_previous_stage_cannot_be_skipped(self):
        self.prepare()
        with self.assertRaises(CommandUnavailable):self.service.execute("adventure-route",{"level":"2-2"})

    def test_missing_live_energy_cost_stops(self):
        self.prepare();self.client.data["current_missions"]["101"]={"level":1}
        with self.assertRaises(CommandUnavailable):self.service.execute("adventure-route",{"mission":101})

    def test_fully_mastered_story_has_no_target(self):
        self.prepare()
        self.client.data["mission_completions"]={str(mid):{"mission_id":mid,"complete":12} for mid in range(101,107)}
        answer=self.service.execute("adventure-route",{"level":"mastery"})
        self.assertEqual(answer.rows,[])
        self.assertIn("mastered",answer.message)

    def test_scoped_event_progress_uses_real_order_and_stops_when_complete(self):
        self.prepare()
        self.service.context._catalogs["campaign"]["9040"]["missions"]=[9256,9257]
        self.client.data["current_missions"]["9257"]={"energy":7,"level":1}
        self.assertEqual(self.service.execute("adventure-route",{"level":"9040-progress"}).rows[0]["mission"],9256)
        self.client.data["mission_completions"]["9256"]={"mission_id":9256,"complete":1}
        self.assertEqual(self.service.execute("adventure-route",{"level":"9040-progress"}).rows[0]["mission"],9257)
        self.client.data["mission_completions"]["9257"]={"mission_id":9257,"complete":1}
        self.assertEqual(self.service.execute("adventure-route",{"level":"9040-progress"}).rows,[])

    def test_scoped_event_mastery_does_not_redirect_to_story(self):
        self.prepare()
        self.assertEqual(self.service.execute("adventure-route",{"level":"9040-mastery"}).rows[0]["mission"],9256)
        self.client.data["mission_completions"]["9256"]={"mission_id":9256,"complete":12}
        self.assertEqual(self.service.execute("adventure-route",{"level":"9040-mastery"}).rows,[])

    def test_all_completed_story_progress_stops_instead_of_farming_terminal_stage(self):
        self.prepare()
        self.client.data["mission_completions"]={str(mid):{"mission_id":mid,"complete":1} for mid in range(101,107)}
        answer=self.service.execute("adventure-route",{"level":"progress"})
        self.assertEqual(answer.rows,[]);self.assertIn("completed",answer.message)

    def test_scoped_campaign_requires_current_unlock_and_time_window(self):
        self.prepare();self.client.data["time"]=1000
        for changes in ({"end_time":1000},{"start_time":1001},{"end_time":True}):
            with self.subTest(changes=changes):
                self.client.data["current_campaigns"]["9040"]={"completed":1,**changes}
                with self.assertRaises(CommandUnavailable):self.service.execute("adventure-route",{"level":"9040-progress"})
        self.client.data["current_campaigns"].pop("9040")
        with self.assertRaises(CommandUnavailable):self.service.execute("adventure-route",{"level":"9040-progress"})

    def test_malformed_completion_identity_or_energy_cannot_plan_fights(self):
        for corrupt in ({"energy":True},{"energy":5.5},{"id":999,"energy":5}):
            self.prepare();self.client.data["current_missions"]["101"]=corrupt
            with self.assertRaises(CommandUnavailable):self.service.execute("adventure-route",{"level":"1-1"})
        self.prepare();self.client.data["mission_completions"]["101"]["complete"]="1.5"
        with self.assertRaises(CommandUnavailable):self.service.execute("adventure-route",{"level":"progress"})

    def test_missing_completion_table_cannot_assume_unresearched_story(self):
        self.prepare();self.client.data.pop("mission_completions")
        with self.assertRaises(CommandUnavailable):self.service.execute("adventure-route",{"level":"progress"})

    def test_play_scoped_progress_rechecks_real_state_and_shared_battle_limit(self):
        self.prepare();self.client.data["user_data"]["energy"]=100
        self.service.context._catalogs["campaign"]["9040"]["missions"]=[9256,9257]
        self.client.data["current_missions"]["9257"]={"energy":5,"level":1}
        def call(name,args=None):
            self.client.calls.append((name,args or {}))
            if name!="startMission":raise AssertionError("Unexpected request")
            mid=args["mission_id"]
            self.client.data["mission_completions"][str(mid)]={"mission_id":mid,"complete":1}
            self.client.data["user_data"]["energy"]-=5
            return {"result":True,"battle_data":{"battle_id":"fixture","host_id":"fixture"}}
        self.client.call=call;self.client.extract_battle_data=lambda answer:answer.get("battle_data")
        self.client.opponent_name=lambda _:"Fixture"
        with patch("atcommander.arena_runner.ArenaRunner._drive_one",return_value=(True,1,{},{})):
            answer=self.service.execute("play",{"mode":"adventure","adventure_level":"9040-progress","count":5})
        starts=[args["mission_id"] for name,args in self.client.calls if name=="startMission"]
        self.assertEqual(starts,[9256,9257]);self.assertEqual(len(answer.rows),2)
        self.assertIn("route are complete",answer.message)
