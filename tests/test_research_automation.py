import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest

from atcommander.local_commands import CommandInputError, CommandUnavailable
from atcommander.research_automation import (
    ResearchAutomation, ResearchOptions, build_plan, speedup_inventory,
    speedup_plan, slot_state,
)
from atcommander.research_commands import register_research_automation, research_state_path, research_daily_preflight


CARDS={"22":{"name":"Rogu","rarity":4},"23":{"name":"Object","rarity":3},
       "24":{"name":"Second Object","rarity":4},"25":{"name":"Common","rarity":1},
       "90":{"name":"First Combo","rarity":4,"power":10},
       "91":{"name":"Second Combo","rarity":4},"92":{"name":"Common Combo","rarity":3}}
RECIPES={"22~23":"90|public_recipe","23~22":"90|public_recipe","22~24":"91|public_recipe"}
ITEMS={str(i):{"name":f"Research Speedup +{h}","research_speed_up_time":str(h)}
       for i,h in ((1031,1),(1032,8),(1033,24))}


def snapshot():
    return {"time":1000,"result":True,
            "user_units":{str(i):{"unit_id":cid,"unit_index":i} for i,cid in enumerate((22,23,24,25),1)},
            "user_combos_compressed":{},"user_tasks":{},"user_items":{"1031":5,"1032":2,"1033":1},
            "research_times_slot":[{"slot_index":i,"research_times":{"4":1800}} for i in (1,2,3)],
            "research_ad_speed_ups":[{"slot_index":3,"views":0,"max_views":3,"next_speed_up":0}],
            "common_fields":{"is_vip_active":0}}


class FakeService:
    def __init__(self):
        self.live=snapshot();self.calls=[];self.next_task=200;self.stop=threading.Event()
        self.client=SimpleNamespace(credentials=SimpleNamespace(user_id="123456"))
        self.context=SimpleNamespace(private=[],invalidate=lambda:None)
        self.emit=lambda message:None;self.now=1000;self.clock=lambda:self.now
        self.cards=copy.deepcopy(CARDS);self.recipes=copy.deepcopy(RECIPES);self.items=copy.deepcopy(ITEMS)
        self.timeout_start=False;self.apply_on_timeout=False;self.stop_after_start=False
        self.ignore_speedup=False;self.compact={};self.without_server_time=False;self.handlers={};self.specs={}

    def snapshot(self,refresh=False):
        self.live["time"]=self.now
        self.calls.append(("init",{}));return copy.deepcopy(self.live)

    def catalog(self,name):return {"card":self.cards,"combo":self.recipes,"item":self.items}[name]

    def _start(self,args):
        self.next_task+=1
        self.live["user_tasks"][str(self.next_task)]={"task_type":1,"task_index":self.next_task,
            "task_id_1":args["card1"],"task_id_2":args["card2"],"slot_index":args["slot_index"],"end_time":2800}

    def call(self,name,args=None):
        args=args or {};self.calls.append((name,copy.deepcopy(args)))
        if name=="getUserAccount":
            self.live["time"]=self.now
            answer=copy.deepcopy(self.compact or self.live)
            if self.without_server_time:answer.pop("time",None)
            return answer
        if name=="researchCombo":
            if self.timeout_start:
                if self.apply_on_timeout:self._start(args)
                raise TimeoutError("synthetic request timeout")
            self._start(args)
            if self.stop_after_start:self.stop.set()
        elif name=="useResearchSpeedUpItem" and not self.ignore_speedup:
            self.live["user_items"][args["item_id"]]-=args["quantity"]
            task=next(row for row in self.live["user_tasks"].values() if row["slot_index"]==args["slot_index"])
            task["end_time"]=max(1000,task["end_time"]-int(self.items[args["item_id"]]["research_speed_up_time"])*3600*args["quantity"])
        elif name=="completeResearch":
            task=self.live["user_tasks"].pop(str(args["task_index"]))
            self.live["user_combos_compressed"][f'{task["task_id_1"]}~{task["task_id_2"]}']=1
        return {"result":True}

    def register(self,spec,handler):self.specs[spec.name]=spec;self.handlers[spec.name]=handler

    def execute(self,name,args=None):return self.handlers[name](self,self.specs[name].parse_args(args))

    def mutations(self):return [call for call in self.calls if call[0] not in {"init","getUserAccount"}]


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"queue.json";self.s=FakeService()

    def engine(self,**kwargs):return ResearchAutomation(self.s,self.path,**kwargs)

    def opts(self,**kwargs):return ResearchOptions(ads=False,speedups=True,max_speedup_hours=2,**kwargs)

    def test_preview_filters_deduplicates_and_does_not_save_or_mutate(self):
        self.s.recipes["25~23"]="92|recipe"
        result=self.engine().preview({"tiers":[4],"tier_match":"either","ads":True})
        self.assertEqual(result.data["count"],2);self.assertEqual(result.rows[0]["card1_name"],"Rogu")
        self.assertEqual(len({frozenset((r["card1"],r["card2"])) for r in result.rows}),2)
        self.assertEqual(self.s.mutations(),[]);self.assertFalse(self.path.exists())
        both=build_plan(self.s.live,self.s.cards,self.s.recipes,{"tiers":[4],"tier_match":"both"})
        self.assertEqual([r["pair"] for r in both],["22~24"])

    def test_known_reverse_pairs_skip_and_current_task_remains_in_plan(self):
        self.s.live["user_combos_compressed"]={"23~22":1}
        self.s._start({"card1":22,"card2":24,"slot_index":1})
        plan=build_plan(self.s.live,self.s.cards,self.s.recipes)
        self.assertEqual([(r["pair"],r["status"]) for r in plan],[("22~24","running")])

    def test_unknown_owned_rarity_or_recipe_fails_before_mutation(self):
        self.s.cards["22"].pop("rarity")
        with self.assertRaises(CommandUnavailable):self.engine().run(self.opts(),wait=False)
        self.assertEqual(self.s.mutations(),[])

    def test_unavailable_recipe_catalog_does_not_masquerade_as_completed_queue(self):
        self.s.recipes={}
        with self.assertRaisesRegex(CommandUnavailable,"recipe map"):
            self.engine().run(self.opts(),wait=False)
        self.assertEqual(self.s.mutations(),[])

    def test_authoritatively_empty_owned_pool_is_a_no_action_result(self):
        self.s.live["user_units"]={};self.s.cards={};self.s.recipes={}
        result=self.engine().run(ResearchOptions(ads=True),wait=False)
        self.assertEqual(result.data["status"],"complete");self.assertEqual(result.data["count"],0)
        self.assertEqual(self.s.mutations(),[])

    def test_live_indices_and_ad_chamber_are_conservative(self):
        free,blocked=slot_state(self.s.live)
        self.assertEqual(free,[1,2]);self.assertEqual(blocked[0]["slot_index"],3)
        self.s.live["common_fields"]["is_vip_active"]=True
        self.assertEqual(slot_state(self.s.live)[0],[1,2,3])
        self.s.live["research_times_slot"][2]["locked"]=True
        self.assertEqual(slot_state(self.s.live)[0],[1,2])

    def test_hour_units_and_least_waste_bounded_selection(self):
        items=speedup_inventory(self.s.live,self.s.items)
        self.assertEqual([row["duration_seconds"] for row in items],[3600,28800,86400])
        selected=speedup_plan(9*3600,items,10*3600)
        self.assertEqual({r["item_id"]:r["quantity"] for r in selected},{"1032":1,"1031":1})
        self.assertEqual(speedup_plan(9*3600,items,8*3600),[])

    def test_unknown_duration_or_count_never_guesses(self):
        self.s.items["1031"]["research_speed_up_time"]=None
        with self.assertRaises(CommandUnavailable):self.engine().preview(self.opts())
        self.assertEqual(self.s.mutations(),[])

    def test_real_sequence_completes_pairs_without_regression_after_start(self):
        result=self.engine().run(self.opts(),wait=False,max_actions=6)
        self.assertEqual(result.data["status"],"complete");self.assertEqual(result.data["completed"],2)
        self.assertEqual(result.data["speedups_used_hours"],2)
        self.assertEqual([name for name,_ in self.s.mutations()],
            ["researchCombo","useResearchSpeedUpItem","completeResearch"]*2)
        self.assertEqual(len(self.s.live["user_tasks"]),0)

    def test_bounded_tick_persists_then_resumes_without_restarting_done_pair(self):
        first=self.engine().run(self.opts(),wait=False)
        self.assertEqual(first.data["status"],"waiting");self.assertEqual(first.data["completed"],1)
        self.assertEqual(len(self.s.mutations()),3)
        second=self.engine().run(self.opts(),wait=False)
        self.assertEqual(second.data["status"],"complete");self.assertEqual(len(self.s.mutations()),6)

    def test_inventory_reserve_and_total_budget_hold(self):
        self.s.live["user_items"]={"1031":3}
        options=ResearchOptions(ads=False,speedups=True,max_speedup_hours=10,reserve_hours=2)
        answer=self.engine().run(options,wait=False,max_actions=10)
        self.assertEqual(answer.data["speedups_used_hours"],1)
        self.assertEqual(self.s.live["user_items"]["1031"],2)
        self.assertEqual(answer.data["completed"],1);self.assertEqual(answer.data["status"],"waiting")

    def test_total_budget_limits_usage_even_with_plentiful_bank(self):
        result=self.engine().run(ResearchOptions(ads=False,speedups=True,max_speedup_hours=1),wait=False,max_actions=10)
        self.assertEqual(result.data["speedups_used_hours"],1);self.assertEqual(result.data["completed"],1)
        self.assertEqual(self.s.live["user_items"]["1031"],4)
        self.assertEqual([name for name,_ in self.s.mutations()].count("useResearchSpeedUpItem"),1)

    def test_known_empty_speedup_bank_fills_regular_chambers_without_spending(self):
        self.s.live["user_items"]={}
        result=self.engine().run(self.opts(),wait=False)
        self.assertEqual(result.data["status"],"waiting");self.assertEqual(result.data["started"],2)
        self.assertEqual(result.data["speedups_available_hours"],0)
        self.assertEqual([name for name,_ in self.s.mutations()],["researchCombo","researchCombo"])

    def test_timeout_not_applied_never_replays_on_resume(self):
        self.s.timeout_start=True
        with self.assertRaises(CommandUnavailable):self.engine().run(self.opts(),wait=False)
        self.s.timeout_start=False
        with self.assertRaisesRegex(CommandUnavailable,"not replayed"):
            self.engine().run(self.opts(),wait=False)
        self.assertEqual([name for name,_ in self.s.mutations()],["researchCombo"])

    def test_timeout_applied_reconciles_before_resuming(self):
        self.s.timeout_start=True;self.s.apply_on_timeout=True
        with self.assertRaises(CommandUnavailable):self.engine().run(self.opts(max_pairs=1),wait=False)
        self.s.timeout_start=False
        result=self.engine().run(self.opts(max_pairs=1),wait=False)
        self.assertEqual(result.data["status"],"complete");self.assertEqual(result.data["started"],1)
        self.assertEqual([name for name,_ in self.s.mutations()].count("researchCombo"),1)

    def test_stop_after_transmission_preserves_pending_then_resumes_once(self):
        self.s.stop_after_start=True
        result=self.engine().run(self.opts(max_pairs=1),wait=False)
        self.assertEqual(result.data["status"],"paused")
        self.assertEqual(json.loads(self.path.read_text())["research"]["pending_action"]["kind"],"start")
        self.s.stop.clear();self.s.stop_after_start=False
        result=self.engine().run(self.opts(max_pairs=1),wait=False)
        self.assertEqual(result.data["completed"],1)
        self.assertEqual([name for name,_ in self.s.mutations()].count("researchCombo"),1)

    def test_unconfirmed_speedup_reserves_budget_but_never_reports_consumed(self):
        self.s.ignore_speedup=True
        with self.assertRaises(CommandUnavailable):self.engine().run(self.opts(),wait=False)
        saved=json.loads(self.path.read_text())["research"]
        self.assertEqual(saved["spent_seconds"],3600);self.assertEqual(saved["confirmed_speedup_seconds"],0)
        with self.assertRaises(CommandUnavailable):self.engine().run(self.opts(),wait=False)
        self.assertEqual([name for name,_ in self.s.mutations()].count("useResearchSpeedUpItem"),1)

    def test_resume_settings_mismatch_requires_explicit_new_queue(self):
        old=ResearchOptions(ads=False,speedups=False,max_pairs=1)
        self.engine().run(old,wait=False)
        changed=ResearchOptions(ads=False,speedups=False,max_pairs=2)
        with self.assertRaises(CommandInputError):self.engine().run(changed,wait=False)
        result=self.engine().run(changed,resume=False,wait=False)
        self.assertEqual(result.data["count"],2)
        self.assertEqual([name for name,_ in self.s.mutations()].count("researchCombo"),2)

    def test_corrupt_queue_is_not_silently_replaced(self):
        self.path.write_text("broken JSON")
        with self.assertRaisesRegex(CommandUnavailable,"could not be read safely"):
            self.engine().run(self.opts(),wait=False)
        self.assertEqual(self.s.mutations(),[])

    def test_cross_account_queue_is_not_resumed(self):
        self.engine().run(ResearchOptions(ads=False),wait=False)
        self.s.client.credentials.user_id="654321"
        with self.assertRaisesRegex(CommandUnavailable,"another account"):
            self.engine().run(ResearchOptions(ads=False),wait=False)

    def test_missing_boost_verification_blocks_before_research_start(self):
        result=self.engine().run(ResearchOptions(),wait=False)
        self.assertEqual(result.data["status"],"blocked_ads");self.assertEqual(self.s.mutations(),[])

    def test_boost_callback_precedes_start_and_no_ad_spoof_request_exists(self):
        order=[]
        def boost(service,live):order.append("real boost");return {"ok":True,"boost_level":3}
        result=self.engine(boost_callback=boost).run(ResearchOptions(),wait=False)
        self.assertEqual(order,["real boost"]);self.assertEqual(result.data["started"],2)
        self.assertTrue(all(name=="researchCombo" for name,_ in self.s.mutations()))

    def test_real_ad_callback_started_pair_is_not_started_again(self):
        self.s.live["research_times_slot"]=self.s.live["research_times_slot"][2:]
        def ad(service,live):
            context=live["_commander_research"]
            service._start({"card1":context["card1"],"card2":context["card2"],"slot_index":context["slot_index"]})
            return {"ok":True,"verified":True,"unlocked":True,"slot_index":3}
        result=self.engine(boost_callback=lambda *_:{"ok":True,"boost_level":3},ad_callback=ad).run(
            ResearchOptions(max_pairs=1),wait=False)
        self.assertEqual(result.data["started"],1);self.assertEqual(result.data["status"],"waiting")
        self.assertEqual(self.s.mutations(),[])

    def test_occupied_ad_slot_is_not_unlocked_for_another_pair(self):
        self.s.live["research_times_slot"]=self.s.live["research_times_slot"][2:]
        self.s._start({"card1":22,"card2":23,"slot_index":3})
        calls=[]
        result=self.engine(boost_callback=lambda *_:{"ok":True,"boost_level":3},
            ad_callback=lambda *_:calls.append(1)).run(ResearchOptions(),wait=False)
        self.assertEqual(calls,[]);self.assertEqual(result.data["status"],"waiting")

    def test_partial_compact_state_falls_back_to_fresh_snapshot(self):
        self.s.compact={"user_tasks":{}}
        result=self.engine().run(self.opts(max_pairs=1),wait=False)
        self.assertEqual(result.data["completed"],1)
        self.assertGreater([name for name,_ in self.s.calls].count("init"),1)

    def test_natural_timer_advances_when_compact_reads_omit_server_time(self):
        self.s.without_server_time=True;waits=[]
        def advance(seconds):
            waits.append(seconds);self.s.now+=1800
            return len(waits)>1
        result=self.engine(waiter=advance).run(ResearchOptions(ads=False,max_pairs=1),wait=True)
        self.assertEqual(result.data["status"],"complete");self.assertEqual(waits,[8])
        self.assertEqual([name for name,_ in self.s.mutations()],["researchCombo","completeResearch"])

    def test_public_result_and_saved_queue_have_no_card_instance_fields(self):
        result=self.engine().run(ResearchOptions(ads=False),wait=False)
        text=json.dumps(result.data)+json.dumps(result.rows)+self.path.read_text()
        self.assertNotIn("unit_index",text);self.assertNotIn("credentials",text)
        self.assertNotIn("123456",self.path.read_text())

    def test_option_alias_and_unknown_setting_reject_before_reads(self):
        self.assertEqual(ResearchOptions.from_dict({"max_hours":8}).max_speedup_hours,8)
        with self.assertRaises(CommandInputError):self.engine().run({"ignored_option":1})
        self.assertEqual(self.s.calls,[])

    def test_independent_daily_queue_and_completed_daily_refresh(self):
        register_research_automation(self.s,state_dir=self.temp.name)
        options=json.dumps({"ads":False,"speedups":True,"max_speedup_hours":2,"max_pairs":1})
        daily=self.s.execute("research-run",{"options":options,"scope":"daily"})
        self.assertEqual(daily.data["status"],"complete")
        self.assertTrue(research_state_path(self.s,"daily",self.temp.name).exists())
        self.assertFalse(research_state_path(self.s,"user",self.temp.name).exists())
        next_daily=self.s.execute("research-run",{"options":options,"scope":"daily"})
        self.assertEqual(next_daily.data["completed"],1)
        self.assertEqual([name for name,_ in self.s.mutations()].count("researchCombo"),2)

    def test_command_invalid_json_does_not_read_account(self):
        register_research_automation(self.s,state_dir=self.temp.name)
        with self.assertRaises(CommandInputError):self.s.execute("research-plan",{"options":"not JSON"})
        self.assertEqual(self.s.calls,[])

    def test_daily_preflight_reuses_live_state_without_actions_or_ads(self):
        result=research_daily_preflight(self.s,{"ads":False},self.s.live,state_dir=self.temp.name)
        self.assertTrue(result["available"]);self.assertEqual(result["free_slots"],[1,2])
        self.assertEqual(self.s.calls,[])

    def test_daily_preflight_skips_active_settings_mismatch(self):
        register_research_automation(self.s,state_dir=self.temp.name)
        self.s.execute("research-run",{"options":json.dumps({"ads":False}),"scope":"daily"})
        before=list(self.s.calls)
        result=research_daily_preflight(self.s,{"ads":False,"max_pairs":1},self.s.live,state_dir=self.temp.name)
        self.assertFalse(result["available"]);self.assertIn("different saved settings",result["reason"])
        self.assertEqual(self.s.calls,before)

    def ad_task(self, callback):
        self.s.recipes={"22~23":"90|public_recipe"}
        self.s.live["research_times_slot"]=self.s.live["research_times_slot"][2:]
        self.s._start({"card1":22,"card2":23,"slot_index":3})
        self.s.live["research_ad_speed_ups"][0]["next_speed_up"]=300
        engine=self.engine(boost_callback=lambda *_:{"ok":True,"boost_level":3},ad_callback=callback)
        engine.run(ResearchOptions(max_pairs=1),wait=False,max_actions=0)
        engine._action_limit=None
        return engine

    def test_research_ad_freezes_exact_task_target_and_confirms_reduced_timer(self):
        observed={}
        def ad(service,live):
            observed.update(live["_commander_research"])
            saved=json.loads(self.path.read_text())["research"]["pending_action"]
            self.assertEqual(saved["pair"],"22~23");self.assertEqual(saved["task_index"],201)
            self.assertEqual(saved["before_end"],2800);self.assertEqual(saved["next_speed_up"],300)
            service.live["research_ad_speed_ups"][0]["views"]+=1
            service.live["user_tasks"]["201"]["end_time"]-=300
            return {"ok":True}
        engine=self.ad_task(ad)
        self.assertTrue(engine._research_ad(3,"speedup"))
        self.assertEqual(observed["card1"],22);self.assertEqual(observed["pair"],"22~23")
        self.assertIsNone(engine.state["pending_action"])

    def test_research_ad_view_credit_without_timer_change_cannot_replay(self):
        calls=[]
        def ad(service,live):
            calls.append(1);service.live["research_ad_speed_ups"][0]["views"]+=1
            return {"ok":True}
        engine=self.ad_task(ad)
        with self.assertRaises(CommandUnavailable):engine._research_ad(3,"speedup")
        with self.assertRaises(CommandUnavailable):engine.run(ResearchOptions(max_pairs=1),wait=False)
        self.assertEqual(calls,[1]);self.assertIsNotNone(engine.state["pending_action"])

    def test_research_ad_replaced_same_pair_task_is_not_confirmation(self):
        def ad(service,live):
            service.live["research_ad_speed_ups"][0]["views"]+=1
            row=service.live["user_tasks"].pop("201");row.update(task_index=999,end_time=1500)
            service.live["user_tasks"]["999"]=row
            return {"ok":True}
        engine=self.ad_task(ad)
        with self.assertRaises(CommandUnavailable):engine._research_ad(3,"speedup")

    def test_research_ad_replaced_different_pair_is_not_confirmation(self):
        def ad(service,live):
            service.live["research_ad_speed_ups"][0]["views"]+=1
            service.live["user_tasks"]["201"].update(task_id_2=24,end_time=1500)
            return {"ok":True}
        engine=self.ad_task(ad)
        with self.assertRaises(CommandUnavailable):engine._research_ad(3,"speedup")

    def test_research_ad_new_known_target_pair_can_confirm_speedup(self):
        def ad(service,live):
            service.live["research_ad_speed_ups"][0]["views"]+=1
            service.live["user_tasks"]={};service.live["user_combos_compressed"]["22~23"]=1
            return {"ok":True}
        engine=self.ad_task(ad)
        self.assertTrue(engine._research_ad(3,"speedup"));self.assertEqual(engine.state["completed"],1)

    def test_research_ad_clock_passage_alone_does_not_confirm_speedup(self):
        def ad(service,live):
            service.now=4000;service.live["research_ad_speed_ups"][0]["views"]+=1
            return {"ok":True}
        engine=self.ad_task(ad)
        with self.assertRaises(CommandUnavailable):engine._research_ad(3,"speedup")

    def test_research_ad_unlock_pair_already_running_elsewhere_skips_playback(self):
        calls=[];self.s.recipes={"22~23":"90|public_recipe"}
        self.s._start({"card1":22,"card2":23,"slot_index":1})
        engine=self.engine(boost_callback=lambda *_:{"ok":True,"boost_level":3},ad_callback=lambda *_:calls.append(1))
        engine.run(ResearchOptions(max_pairs=1),wait=False,max_actions=0)
        self.assertFalse(engine._research_ad(3,"unlock",engine.state["queue"][0]));self.assertEqual(calls,[])

    def test_research_ad_invalid_or_exhausted_counters_skip_playback(self):
        calls=[];engine=self.ad_task(lambda *_:calls.append(1))
        for views,maximum in ((-1,3),(0,0),(3,3),(True,3),(0,"bad")):
            self.s.live["research_ad_speed_ups"][0].update(views=views,max_views=maximum)
            engine._fresh(initial=True)
            self.assertFalse(engine._research_ad(3,"speedup"))
        self.assertEqual(calls,[])

    def test_research_ad_unlock_view_credit_alone_is_uncertain_and_not_replayed(self):
        calls=[];self.s.recipes={"22~23":"90|public_recipe"}
        self.s.live["research_times_slot"]=self.s.live["research_times_slot"][2:]
        def ad(service,live):
            calls.append(1);service.live["research_ad_speed_ups"][0]["views"]+=1
            return {"ok":True}
        engine=self.engine(boost_callback=lambda *_:{"ok":True,"boost_level":3},ad_callback=ad)
        engine.run(ResearchOptions(max_pairs=1),wait=False,max_actions=0)
        engine._action_limit=None
        with self.assertRaises(CommandUnavailable):engine._research_ad(3,"unlock",engine.state["queue"][0])
        with self.assertRaises(CommandUnavailable):engine.run(ResearchOptions(max_pairs=1),wait=False)
        self.assertEqual(calls,[1]);self.assertEqual(engine.state["ad_unlocks"],[])

    def test_ad_navigation_stop_before_watch_keeps_pending_pair_resumable(self):
        self.s.recipes={"22~23":"90|public_recipe"}
        self.s.live["research_times_slot"]=self.s.live["research_times_slot"][2:]
        def ad(_service,_live):
            engine.stop.set()
            return {"ok":False,"playback_started":False}
        engine=self.engine(boost_callback=lambda *_:{"ok":True,"boost_level":3},ad_callback=ad)
        engine.run(ResearchOptions(max_pairs=1),wait=False,max_actions=0)
        engine._action_limit=None
        from atcommander.research_automation import _Stopped
        with self.assertRaises(_Stopped):
            engine._research_ad(3,"unlock",engine.state["queue"][0])
        saved=json.loads(self.path.read_text())["research"]
        self.assertIsNone(saved["pending_action"])
        self.assertEqual(saved["queue"][0]["status"],"pending")
        self.assertEqual(saved["started"],0)

    def test_uncertain_watch_delivery_preserves_research_pending_action(self):
        self.s.recipes={"22~23":"90|public_recipe"}
        self.s.live["research_times_slot"]=self.s.live["research_times_slot"][2:]
        engine=self.engine(boost_callback=lambda *_:{"ok":True,"boost_level":3},
                           ad_callback=lambda *_:{"ok":False,"playback_started":True})
        engine.run(ResearchOptions(max_pairs=1),wait=False,max_actions=0)
        engine._action_limit=None
        with self.assertRaises(CommandUnavailable):
            engine._research_ad(3,"unlock",engine.state["queue"][0])
        self.assertEqual(engine.state["pending_action"]["kind"],"ad")

    def test_research_ad_unlock_wrong_chamber_does_not_confirm_target(self):
        self.s.recipes={"22~23":"90|public_recipe"}
        self.s.live["research_times_slot"]=self.s.live["research_times_slot"][2:]
        def ad(service,live):
            service._start({"card1":22,"card2":23,"slot_index":1})
            service.live["research_ad_speed_ups"][0]["views"]+=1
            return {"ok":True}
        engine=self.engine(boost_callback=lambda *_:{"ok":True,"boost_level":3},ad_callback=ad)
        engine.run(ResearchOptions(max_pairs=1),wait=False,max_actions=0)
        engine._action_limit=None
        with self.assertRaises(CommandUnavailable):engine._research_ad(3,"unlock",engine.state["queue"][0])
        self.assertEqual(engine.state["started"],0)


if __name__=="__main__":unittest.main()
