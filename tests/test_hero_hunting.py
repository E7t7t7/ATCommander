import unittest
import test_utility_commands as fixture
from atcommander.hero_hunting import hunt_target,requested_heroes
from atcommander.local_commands import CommandInputError,CommandUnavailable
from types import SimpleNamespace
import threading


class HeroHuntingTests(unittest.TestCase):
    setUp=fixture.UtilityTests.setUp

    def setup_target(self,hero=1):
        self.client.call=lambda name,args=None:(self.client.calls.append((name,args)) or {"user_data":{"money":1000},"hunting_targets":[{"user_id":456,"hero_xp_id":hero,"refresh_cost":100}]})

    def test_matching_reward_needs_no_refresh(self):
        self.setup_target()
        target,spent,count=hunt_target(self.service,{"1"},0,0)
        self.assertEqual(target["hero_xp_id"],1)
        self.assertEqual((spent,count),(0,0))
        self.assertNotIn("refreshHuntingTargets",[name for name,_ in self.client.calls])

    def test_matching_opaque_opponent_id_needs_no_balance_or_refresh_price(self):
        service=SimpleNamespace(call=lambda name:{"hunting_targets":[{"user_id":"fixture-opponent","hero_xp_id":1001}]})
        target,spent,count=hunt_target(service,{"1001"},0,0)
        self.assertEqual(target["user_id"],"fixture-opponent")
        self.assertEqual((spent,count),(0,0))

    def test_default_zero_budget_cannot_refresh(self):
        self.setup_target()
        self.assertEqual(hunt_target(self.service,{"2"},1,0),(None,0,0))

    def test_confirmed_refresh_stays_inside_total_budget(self):
        answers=iter([{"user_data":{"money":1000},"hunting_targets":[{"user_id":456,"hero_xp_id":1,"refresh_cost":100}]},{"user_data":{"money":900},"hunting_targets":[{"user_id":789,"hero_xp_id":2,"refresh_cost":100}]}])
        self.client.call=lambda *_:next(answers)
        target,spent,count=hunt_target(self.service,{"2"},1,100)
        self.assertEqual((target["hero_xp_id"],spent,count),(2,100,1))

    def test_unconfirmed_refresh_never_repeats_or_starts_a_fight(self):
        answers=iter([{"user_data":{"money":1000},"hunting_targets":[{"user_id":456,"hero_xp_id":1,"refresh_cost":100}]},{"hunting_targets":[{"user_id":789,"hero_xp_id":2}]}])
        self.client.call=lambda name,args=None:(self.client.calls.append((name,args)) or next(answers))
        with self.assertRaises(CommandUnavailable):hunt_target(self.service,{"2"},10,1000)
        self.assertEqual([name for name,_ in self.client.calls],["getHuntingTargets","refreshHuntingTargets"])

    def test_confirmed_hero_names_need_no_static_hero_catalog(self):
        self.client.hero_catalog=lambda:(_ for _ in ()).throw(RuntimeError("Missing catalog"))
        self.assertEqual(requested_heroes(self.service,"bobby|tina|dale"),{"4002","3003","4003"})

    def test_malformed_refresh_limits_fail_before_any_read(self):
        self.setup_target()
        for limits in ((True,100),(1,False),(1.5,100),(1,"1.5"),(-1,100),(1,-1)):
            with self.subTest(limits=limits), self.assertRaises(CommandInputError):
                hunt_target(self.service,{"2"},*limits)
        self.assertEqual(self.client.calls,[])

    def test_malformed_live_price_or_balance_never_refreshes(self):
        for field,value in (("cost",True),("cost",100.5),("balance",False),("balance","1000.0")):
            with self.subTest(field=field,value=value):
                calls=[]
                data={"user_data":{"money":value if field=="balance" else 1000},
                      "hunting_targets":[{"user_id":456,"hero_xp_id":1,"refresh_cost":value if field=="cost" else 100}]}
                service=SimpleNamespace(call=lambda name:(calls.append(name) or data))
                with self.assertRaises(CommandUnavailable):hunt_target(service,{"2"},10,1000)
                self.assertEqual(calls,["getHuntingTargets"])

    def test_explicit_rejections_do_not_accept_matching_reward_or_cost(self):
        for rejected_read in (True,False):
            with self.subTest(rejected_read=rejected_read):
                calls=[]
                first={"result":not rejected_read,"user_data":{"money":1000},
                       "hunting_targets":[{"user_id":456,"hero_xp_id":2 if rejected_read else 1,"refresh_cost":100}]}
                second={"result":False,"user_data":{"money":900},
                        "hunting_targets":[{"user_id":789,"hero_xp_id":2,"refresh_cost":100}]}
                answers=iter([first,second])
                service=SimpleNamespace(call=lambda name:(calls.append(name) or next(answers)))
                with self.assertRaises(CommandUnavailable):hunt_target(service,{"2"},10,1000)
                self.assertEqual(calls,["getHuntingTargets"] if rejected_read else ["getHuntingTargets","refreshHuntingTargets"])

    def test_stop_arriving_during_target_read_prevents_matching_battle(self):
        stop=threading.Event()
        calls=[]
        def request(name):
            calls.append(name);stop.set()
            return {"hunting_targets":[{"user_id":456,"hero_xp_id":2}]}
        service=SimpleNamespace(call=request,stop=stop)
        self.assertEqual(hunt_target(service,{"2"},10,1000),(None,0,0))
        self.assertEqual(calls,["getHuntingTargets"])

    def test_malformed_opponent_identity_never_starts_or_spends(self):
        for user in (True,0,-1,456.5,"",None):
            with self.subTest(user=user):
                service=SimpleNamespace(call=lambda name:{"hunting_targets":[{"user_id":user,"hero_xp_id":2}]})
                self.assertEqual(hunt_target(service,{"2"},0,0),(None,0,0))
