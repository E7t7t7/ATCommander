import threading
import unittest
from unittest.mock import patch
import test_utility_commands as fixture
from atcommander.local_commands import CommandUnavailable
from atcommander.reward_options import claim_plan, execute_claim_plan, vip_active


class RewardOptionsTests(unittest.TestCase):
    setUp = fixture.UtilityTests.setUp

    def test_completed_quests_only_never_forced(self):
        live={"user_achievements":[
            {"id":1,"name":"Ready","progress":3,"max_progress":3,"status":1},
            {"id":2,"progress":2,"max_progress":3,"status":0},
            {"id":3,"progress":3,"max_progress":3,"status":2},
            {"id":4,"progress":3,"max_progress":3,"status":1,"end_time":99},
        ]}
        self.assertEqual(claim_plan(live,{"quests"},100),[{"kind":"quests","name":"Ready","method":"completeAchievement","payload":{"achievement_id":1}}])

    def test_current_cycles_preserved_shop_packs_and_upcoming_excluded(self):
        live={"active_events":[
            {"id":1,"type":6,"name":"Clash","cycle":7,"start_time":90},
            {"id":2,"type":6,"name":"Melee","start_time":101},
            {"id":3,"type":5,"name":"Clash Pack"},
            {"id":4,"type":6,"name":"Melee","is_cycling":1},
        ]}
        plan=claim_plan(live,{"clash"},100)
        self.assertEqual(len(plan),1)
        self.assertEqual(plan[0]["payload"],{"event_id":1,"cycle":7})

    def test_ended_exposed_event_remains_in_claim_window(self):
        live={"active_events":[{"id":1,"type":6,"name":"Elite Card Master Challenge","start_time":50,"end_time":90}]}
        self.assertEqual(claim_plan(live,{"ecmc"},100)[0]["method"],"claimMilestoneRewards")

    def test_mega_uses_quest_id_and_excludes_expired_duplicate(self):
        live={"user_mega_quest":[{"id":9,"name":"Anniversary","end_time":200},{"id":9},{"id":10,"end_time":90}]}
        self.assertEqual(len(claim_plan(live,{"mega"},100)),1)
        self.assertEqual(claim_plan(live,{"mega"},100)[0]["payload"],{"mega_quest_id":9})

    def test_sfc_milestones_do_not_guess_season_rank(self):
        plan=claim_plan({"active_events":[{"id":6,"name":"SFC Season","type":12,"cycle":33}]},{"sfc"},100)
        self.assertEqual(plan[0]["payload"],{"event_id":6,"cycle":33})
        self.assertEqual(plan[0]["method"],"claimMilestoneRewards")

    def test_preview_does_not_claim(self):
        answer=self.service.execute("reward-claim",{"kind":"daily"})
        self.assertIn("Preview only",answer.message)
        self.assertNotIn("claimDailyBonus",[n for n,_ in self.client.calls])

    def test_unknown_claim_outcome_not_replayed(self):
        calls=[]
        self.client.call=lambda n,a=None:(calls.append(n) or {})
        with self.assertRaises(CommandUnavailable):
            execute_claim_plan(self.service,claim_plan({}, {"daily"},100))
        self.assertEqual(calls,["claimDailyBonus"])

    def test_stop_prevents_any_claim(self):
        self.service.stop=threading.Event();self.service.stop.set()
        self.assertEqual(execute_claim_plan(self.service,claim_plan({}, {"daily"},100)),[])
        self.assertEqual(self.client.calls,[])

    def test_explicit_unavailable_event_reward_is_not_retried(self):
        calls=[]
        def reject(name,args=None):
            calls.append(name)
            raise CommandUnavailable("You cannot claim any rewards for this event. warning_23_text")
        self.client.call=reject
        plan=[{"kind":"sfc","name":"SFC","method":"claimMilestoneRewards","payload":{"event_id":6}}]
        result=execute_claim_plan(self.service,plan)
        self.assertEqual(calls,["claimMilestoneRewards"])
        self.assertFalse(result[0]["confirmed"])
        self.assertTrue(result[0]["unavailable"])

    def test_other_event_errors_and_quest_rejection_still_stop(self):
        for method,message in (("claimMilestoneRewards","warning_16_text"),
                               ("claimMegaQuestMilestoneRewards","Unknown outcome"),
                               ("completeAchievement","warning_23_text")):
            with self.subTest(method=method,message=message):
                calls=[]
                def reject(name,args=None):
                    calls.append(name)
                    raise CommandUnavailable(message)
                self.client.call=reject
                with self.assertRaises(CommandUnavailable):
                    execute_claim_plan(self.service,[{"kind":"quests","name":"Test","method":method,"payload":{}}])
                self.assertEqual(calls,[method])

    def test_vip_string_zero_is_false_and_missing_is_unavailable(self):
        self.assertFalse(vip_active(self.service,{"common_fields":{"is_vip_active":"0"}}))
        with self.assertRaises(CommandUnavailable):vip_active(self.service,{})

    def crate_fixture(self,vip):
        self.client.data["common_fields"]={"is_vip_active":vip}
        self.client.data["user_items"]={"30002":{"number":2},"30001":{"number":1}}
        self.service.context._catalogs["item"]={
            "30002":{"id":"30002","name":"Loot Crate","usable":"1"},
            "30001":{"id":"30001","name":"Ad Crate","usable":"1","ad_locked":"1"},
        }
        self.client.use_item=lambda iid,count:(self.client.calls.append(("use_item",{"item":iid,"quantity":count})) or {"result":True})

    def test_lootcrate_requires_explicit_current_vip(self):
        self.crate_fixture("0")
        with self.assertRaises(CommandUnavailable):self.service.execute("item-use",{"item":"lootcrate","quantity":"1"})
        self.assertNotIn("use_item",[n for n,_ in self.client.calls])

    def test_legpack_alias_selects_verified_vip_lootcrate(self):
        self.crate_fixture("1")
        self.service.execute("item-use",{"item":"legpack","quantity":"all"})
        self.assertEqual(self.client.calls[-1],("use_item",{"item":"30002","quantity":2}))

    def test_nonvip_legpack_keeps_ad_requirement(self):
        self.crate_fixture("0")
        with self.assertRaises(CommandUnavailable):self.service.execute("item-use",{"item":"legpack"})
        self.assertNotIn("use_item",[n for n,_ in self.client.calls])

    def test_any_ad_locked_owned_item_requires_real_playback(self):
        self.crate_fixture("1")
        self.client.data["user_items"]["777"]={"number":1}
        self.service.context._catalogs["item"]["777"]={"id":"777","name":"Reward Crate","usable":"1","ad_locked":"1"}
        with self.assertRaises(CommandUnavailable):self.service.execute("item-use",{"item":"777"})
        self.assertNotIn("use_item",[n for n,_ in self.client.calls])

    def test_named_claims_run_after_no_energy_play(self):
        self.client.data["user_data"]["stamina"]=0
        self.client.data["active_events"]=[{"id":8,"name":"SFC Season","type":12,"cycle":7}]
        self.service.execute("play",{"claim_sfc":True})
        self.assertEqual(self.client.calls[-1],("claimMilestoneRewards",{"event_id":8,"cycle":7}))
