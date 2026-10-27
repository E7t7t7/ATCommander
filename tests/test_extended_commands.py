import json
import threading
import unittest
from unittest.mock import patch

import test_utility_commands as utility_fixture
from atcommander.local_commands import CommandUnavailable, CommandInputError
from atcommander.command_center import notification_digest
from atcommander.ad_ui import rewarded_benefit_changed


class ExtendedTests(unittest.TestCase):
    setUp = utility_fixture.UtilityTests.setUp
    def test_missing_research_uses_public_recipes_and_owned_deck(self):
        self.client.data["user_decks"]["1"]["units"].append({"unit_index":4})
        self.client.data["user_units"]["4"]={"unit_index":4,"unit_id":23}
        self.client.data["user_combos_compressed"]={}
        self.service.context._catalogs["combo"]={"22~23":"15001|","22~99":"15002|"}
        answer=self.service.execute("research-missing",{"deck":1})
        self.assertEqual([r["combo_id"] for r in answer.rows],["15001"])
        self.client.data["user_combos_compressed"]["23~22"]="15001|"
        self.assertEqual(self.service.execute("research-missing",{"deck":1}).rows,[])
    def test_restore_missing_cards_never_changes_game(self):
        self.store.update("decks",{"Backup":{"units":[{"unit_index":i} for i in range(25)],"commander":{"unit_id":1001}}})
        with self.assertRaises(CommandUnavailable):self.service.execute("deck-restore",{"deck":1,"name":"Backup"})
        self.assertNotIn("setDeckUnits",[name for name,_ in self.client.calls])
    def test_zero_price_mom_pull_is_not_forged(self):
        self.client.data["slot_machine_data"]={"cost":0}
        with self.assertRaises(CommandUnavailable):self.service.execute("buy-mombox",{"max_gems":1000})
        self.assertNotIn("slotMachinePull",[name for name,_ in self.client.calls])
    def test_first_free_mom_uses_normal_pull_once_and_verifies_no_gems(self):
        self.purchase_fixture(price=0)
        self.client.data["slot_machine_data"]["pulls"]=0
        def call(name,args=None):
            self.client.calls.append((name,args or {}))
            if name=="getStoreData":return {"store_items":[{"id":71,"name":"Mystery Box"}]}
            self.assertEqual(name,"triggerMysteryBoxAd")
            self.assertIn("adsig",args)
            self.client.data["slot_machine_data"]["pulls"]=1
            return {"result":True,"active_choices":[]}
        self.client.call=call
        answer=self.service.execute("buy-momfree")
        self.assertTrue(answer.data["first_free"])
        self.assertEqual([name for name,_ in self.client.calls if name!="init"],["getStoreData","triggerMysteryBoxAd"])
        self.assertEqual(self.client.data["user_data"]["free_hard_currency"],1000)
    def test_mom_ad_pull_cannot_reuse_first_free_protocol(self):
        self.purchase_fixture(price=0)
        self.client.data["slot_machine_data"]["pulls"]=1
        with patch("atcommander.ad_ui.attempt_legitimate_rewarded_flow",return_value={"ok":False,"message":"No legitimate ad playback"}):
            with self.assertRaises(CommandUnavailable):self.service.execute("buy-momfree")
        self.assertFalse(any(name!="init" for name,_ in self.client.calls))
    def test_unconfirmed_first_free_mom_is_never_retried(self):
        self.purchase_fixture(price=0)
        self.client.data["slot_machine_data"]["pulls"]=0
        def call(name,args=None):
            self.client.calls.append((name,args or {}))
            return {"store_items":[{"id":71,"name":"Mystery Box"}]} if name=="getStoreData" else {"result":True,"active_choices":[]}
        self.client.call=call
        with self.assertRaisesRegex(CommandUnavailable,"did not confirm"):
            self.service.execute("buy-momfree")
        self.assertEqual([name for name,_ in self.client.calls if name!="init"],["getStoreData","triggerMysteryBoxAd"])
    def test_first_free_mom_requires_current_mystery_offer(self):
        self.purchase_fixture(price=0)
        self.client.data["slot_machine_data"]["pulls"]=0
        with self.assertRaisesRegex(CommandUnavailable,"current shop"):
            self.service.execute("buy-momfree")
        self.assertFalse(any(name=="triggerMysteryBoxAd" for name,_ in self.client.calls))
    def test_daily_basic_pack_budget_caps_fresh_changed_price(self):
        self.client.data["user_data"]["money"]=100000
        self.client.call=lambda name,args=None:{"store_items":[{"id":1,"sc_cost":2000,"num_available":1}]}
        purchases=[]
        self.client.buy_store_item=lambda item,price,count:(purchases.append((item,price,count)) or {"result":True})
        self.service.execute("buy-basic",{"quantity":"3","budget":3000})
        self.assertEqual(purchases,[("1",2000,1)])

    def test_golden_command_reuses_and_saves_gui_pacing_after_explicit_rejection(self):
        from atcommander.shop_rewards import run_single_purchases
        self.client.data["user_items"] = [{"id": 200005, "number": 2000}]
        attempts = []
        clock = [0.0]
        def call(name, args=None):
            if name == "getStoreData":
                return {"store_items": [{"id": 63, "num_available": 1}]}
            self.assertEqual(args, {"cost_type": "2", "item_id": "63", "quantity": "1"})
            attempts.append(clock[0])
            return {"result": False, "result_message": "warning_16_text"} if len(attempts) == 1 else {"result": True}
        self.client.call = call
        stored = {"golden_shop_interval_seconds": 1.0, "other_preference": "keep"}
        def run(*args, **kwargs):
            kwargs['clock'] = lambda: clock[0]
            kwargs['sleep'] = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
            return run_single_purchases(*args, **kwargs)
        with patch("atcommander.settings.load_settings", side_effect=lambda: dict(stored)), \
             patch("atcommander.settings.save_settings", side_effect=lambda data: stored.update(data)), \
             patch("atcommander.extended_commands.run_single_purchases", side_effect=run):
            result = self.service.execute("buy-turdpack", {"quantity": "2"})
        self.assertEqual(len(attempts), 3)
        self.assertGreaterEqual(attempts[1] - attempts[0], 1.35)
        self.assertGreaterEqual(attempts[2] - attempts[1], 1.35)
        self.assertEqual(stored["golden_shop_interval_seconds"], 1.35)
        self.assertEqual(stored["other_preference"], "keep")
        self.assertIn("2 real Golden Packs", result.message)

    def test_required_stone_currency_rejects_changed_first_offer(self):
        for fields in ({"hc_cost":100},{"sc_cost":100},{"cost":{"item":"21000","num":100}}):
            with self.subTest(fields=fields):
                self.purchase_fixture();self.client.calls=[]
                self.client.call=lambda name,args=None:(self.client.calls.append((name,args or {})) or {"store_items":[{"id":77,**fields}]})
                with self.assertRaisesRegex(CommandUnavailable,"required item currency"):
                    self.service.execute("shop-purchase",{"store_id":77,"budget":1000,"expected_currency_item":200008})
                self.assertNotIn("buyStoreItem",[name for name,_ in self.client.calls])

    def test_required_stone_currency_is_rechecked_after_each_purchase(self):
        self.purchase_fixture();self.client.data["user_items"]=[{"id":200008,"number":6000}]
        bought=[]
        def call(name,args=None):
            self.client.calls.append((name,args or {}))
            if name=="getStoreData":
                price={"hc_cost":100} if bought else {"cost":{"item":"200008","num":1000}}
                return {"store_items":[{"id":77,**price}]}
            if name=="buyStoreItem":
                bought.append(args);return {"result":True,"active_choices":[]}
            raise AssertionError(name)
        self.client.call=call
        with patch("time.sleep",return_value=None),self.assertRaisesRegex(CommandUnavailable,"required item currency"):
            self.service.execute("shop-purchase",{"store_id":77,"quantity":2,"budget":3000,"expected_currency_item":200008})
        self.assertEqual(len(bought),1);self.assertEqual(bought[0]["expected_cost"],1000)
    def test_first_free_mom_blocks_pending_choice(self):
        self.purchase_fixture(price=0)
        self.client.data["slot_machine_data"]["pulls"]=0
        self.client.data["active_choices"]=self.choice_fixture()
        with self.assertRaises(CommandUnavailable):self.service.execute("buy-momfree")
        self.assertFalse(any(name!="init" for name,_ in self.client.calls))
    def test_no_energy_play_spends_nothing(self):
        self.client.data["user_data"]["stamina"]=0
        self.service.execute("play",{"mode":"arena","count":3})
        self.assertNotIn("startHuntingBattle",[name for name,_ in self.client.calls])
    def test_bad_play_defaults_not_saved(self):
        with self.assertRaises(CommandInputError):self.service.execute("play-defaults",{"mode":"set","options":'{"unrecognized":1}'})
        self.assertNotIn("play_defaults",self.store.read())
    def test_recycle_protects_saved_cards_and_high_rarity(self):
        self.client.data["user_units"].update({"4":{"unit_index":4,"unit_id":22,"level":1,"rarity":1},"5":{"unit_index":5,"unit_id":22,"level":1,"rarity":4},"6":{"unit_index":6,"unit_id":22,"level":2,"rarity":1}})
        answer=self.service.execute("recycle",{"execute":False})
        self.assertEqual(answer.rows,[{"unit_index":4}])
        self.assertNotIn("salvageUnitList",[name for name,_ in self.client.calls])
    def test_notification_ignores_transport_echo(self):
        one=json.dumps({"time":1,"request":{"client_time":1},"rating":2000})
        two=json.dumps({"time":2,"request":{"client_time":2},"rating":2000})
        self.assertEqual(notification_digest(one),notification_digest(two))
        self.assertNotEqual(notification_digest(one),notification_digest('{"rating":2001}'))
    def test_ad_benefit_requires_real_credit(self):
        self.assertFalse(rewarded_benefit_changed({"slot_machine_data":{"pulls":0}},{"slot_machine_data":{"pulls":0}},"mom"))
        self.assertTrue(rewarded_benefit_changed({"slot_machine_data":{"pulls":0}},{"slot_machine_data":{"pulls":1}},"mom"))
        self.assertFalse(rewarded_benefit_changed({"slot_machine_data":{"pulls":0}},{"result":False,"slot_machine_data":{"pulls":1}},"mom"))
    def test_research_ad_requires_task_type_and_shorter_timer(self):
        before={"user_tasks":{"1":{"task_index":1,"task_type":1,"end_time":1000}}}
        after={"user_tasks":{"1":{"task_index":1,"task_type":1,"end_time":500}}}
        self.assertTrue(rewarded_benefit_changed(before,after,"research"))
        after["user_tasks"]["1"]["task_type"]=2
        self.assertFalse(rewarded_benefit_changed(before,after,"research"))

    def purchase_fixture(self, *, price=100):
        self.client.data["user_data"]["free_hard_currency"]=1000
        self.client.data["slot_machine_data"]={"cost":price}
        self.client.data["active_choices"]=[]
        def call(name,args=None):
            self.client.calls.append((name,args or {}))
            if name=="getStoreData":return {"store_items":[{"id":77,"hc_cost":price,"num_available":1}]}
            return {"result":True,"active_choices":[]}
        self.client.call=call

    def choice_fixture(self):
        return [{"type":8,"options":[{"id":1,"give_card":[{"id":23}]},{"id":2,"give_card":[{"id":22}]}]}]

    def test_mom_and_shop_reject_preexisting_choice_before_purchase(self):
        for command,args in (("buy-mombox",{"quantity":2,"max_gems":1000}),("shop-purchase",{"store_id":77,"quantity":2,"budget":1000})):
            with self.subTest(command=command):
                self.purchase_fixture();self.client.calls=[]
                self.client.data["active_choices"]=self.choice_fixture()
                with self.assertRaises(CommandUnavailable):self.service.execute(command,args)
                self.assertFalse(any(name in {"slotMachinePull","buyStoreItem","confirmUserChoice"} for name,_ in self.client.calls))

    def test_paid_pulls_resolve_each_choice_before_next_purchase(self):
        for command,method,args in (("buy-mombox","slotMachinePull",{"quantity":2,"max_gems":250}),("shop-purchase","buyStoreItem",{"store_id":77,"quantity":2,"budget":250})):
            with self.subTest(command=command):
                self.purchase_fixture();self.client.calls=[]
                def call(name,args=None):
                    self.client.calls.append((name,args or {}))
                    if name=="getStoreData":return {"store_items":[{"id":77,"hc_cost":100,"num_available":1}]}
                    if name==method:
                        self.assertEqual(self.client.data["active_choices"],[])
                        self.client.data["active_choices"]=self.choice_fixture()
                        self.client.data["user_data"]["free_hard_currency"]-=100
                        return {"result":True,"active_choices":self.choice_fixture()}
                    if name=="confirmUserChoice":
                        self.assertEqual(args,{"type":"8","choice":"2"})
                        self.client.data["active_choices"]=[]
                        return {"result":True,"active_choices":[]}
                    raise AssertionError(name)
                self.client.call=call
                with patch("time.sleep",return_value=None):answer=self.service.execute(command,args)
                sequence=[name for name,_ in self.client.calls if name in {method,"confirmUserChoice"}]
                self.assertEqual(sequence,[method,"confirmUserChoice",method,"confirmUserChoice"])
                self.assertIn("200",answer.message)

    def test_unconfirmed_choice_stops_purchase_sequence_without_retry(self):
        for command,method,args in (("buy-mombox","slotMachinePull",{"quantity":3,"max_gems":1000}),("shop-purchase","buyStoreItem",{"store_id":77,"quantity":3,"budget":1000})):
            with self.subTest(command=command):
                self.purchase_fixture();self.client.calls=[]
                def call(name,args=None):
                    self.client.calls.append((name,args or {}))
                    if name=="getStoreData":return {"store_items":[{"id":77,"hc_cost":100}]}
                    if name==method:return {"result":True,"active_choices":self.choice_fixture()}
                    if name=="confirmUserChoice":return {"result":True,"active_choices":self.choice_fixture()}
                    raise AssertionError(name)
                self.client.call=call
                with self.assertRaisesRegex(CommandUnavailable,"1 confirmed pulls"):
                    self.service.execute(command,args)
                self.assertEqual([name for name,_ in self.client.calls].count(method),1)
                self.assertEqual([name for name,_ in self.client.calls].count("confirmUserChoice"),1)

    def test_unknown_choice_shape_stops_after_one_confirmed_purchase(self):
        self.purchase_fixture()
        self.client.call=lambda name,args=None:(self.client.calls.append((name,args or {})) or {"result":True,"active_choices":[{"type":8,"unknown_options":[]} ]})
        with self.assertRaisesRegex(CommandUnavailable,"could not be read"):
            self.service.execute("buy-mombox",{"quantity":3,"max_gems":1000})
        self.assertEqual([name for name,_ in self.client.calls].count("slotMachinePull"),1)
        self.assertNotIn("confirmUserChoice",[name for name,_ in self.client.calls])

    def test_uncertain_second_mom_pull_keeps_partial_count_and_no_retry(self):
        self.purchase_fixture()
        pulls=[]
        def call(name,args=None):
            self.client.calls.append((name,args or {}));pulls.append(name)
            if len(pulls)==2:raise TimeoutError("uncertain outcome")
            return {"result":True,"active_choices":[]}
        self.client.call=call
        with self.assertRaisesRegex(CommandUnavailable,"1 confirmed Mom's Box pulls"):
            self.service.execute("buy-mombox",{"quantity":3,"max_gems":1000})
        self.assertEqual(pulls,["slotMachinePull","slotMachinePull"])

    def test_uncertain_second_offer_keeps_partial_count_and_no_retry(self):
        self.purchase_fixture();pulls=[]
        def call(name,args=None):
            self.client.calls.append((name,args or {}))
            if name=="getStoreData":return {"store_items":[{"id":77,"hc_cost":100}]}
            pulls.append(name)
            if len(pulls)==2:raise TimeoutError("uncertain outcome")
            return {"result":True,"active_choices":[]}
        self.client.call=call
        with patch("time.sleep",return_value=None),self.assertRaisesRegex(CommandUnavailable,"1 confirmed offers"):
            self.service.execute("shop-purchase",{"store_id":77,"quantity":3,"budget":1000})
        self.assertEqual(pulls,["buyStoreItem","buyStoreItem"])

    def test_stop_during_returned_choice_prevents_another_pull(self):
        self.purchase_fixture();self.service.stop=threading.Event()
        def call(name,args=None):
            self.client.calls.append((name,args or {}));self.service.stop.set()
            return {"result":True,"active_choices":self.choice_fixture()}
        self.client.call=call
        with self.assertRaisesRegex(CommandUnavailable,"selection stopped"):
            self.service.execute("buy-mombox",{"quantity":3,"max_gems":1000})
        self.assertEqual([name for name,_ in self.client.calls].count("slotMachinePull"),1)
        self.assertNotIn("confirmUserChoice",[name for name,_ in self.client.calls])

    def test_mom_live_price_increase_respects_total_gem_budget(self):
        self.purchase_fixture(price=100)
        def call(name,args=None):
            self.client.calls.append((name,args or {}))
            self.client.data["slot_machine_data"]["cost"]=200
            self.client.data["user_data"]["free_hard_currency"]-=100
            return {"result":True,"active_choices":[]}
        self.client.call=call
        answer=self.service.execute("buy-mombox",{"quantity":3,"max_gems":250})
        self.assertEqual([name for name,_ in self.client.calls].count("slotMachinePull"),1)
        self.assertIn("spent 100",answer.message)
