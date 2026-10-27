"""Saved-action recovery never sends a replacement resource request."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from atcommander.local_commands import CommandInputError, CommandUnavailable
from atcommander.research_automation import ResearchAutomation, ResearchOptions
from atcommander.research_commands import (
    register_research_automation, research_daily_preflight, research_state_path,
)
from test_research_automation import FakeService


class ResearchRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/"queue.json"
        self.service=FakeService()
        self.options=ResearchOptions(ads=False,speedups=True,max_speedup_hours=24)

    def engine(self):
        return ResearchAutomation(self.service,self.path,boost_callback=Mock(side_effect=AssertionError("No boost playback")),
                                  ad_callback=Mock(side_effect=AssertionError("No ad playback")))

    def seed(self,pending=None,options=None):
        engine=self.engine();engine.run(options or self.options,wait=False,max_actions=0)
        if pending is not None:
            engine.state.update(pending_action=deepcopy(pending),status="uncertain")
            if pending["kind"]=="speedup":
                engine.state.update(spent_seconds=14*3600,confirmed_speedup_seconds=12*3600)
            engine._save()
        self.service.calls.clear()
        return engine

    def speedup(self,**changes):
        return {"kind":"speedup","pair":"22~23","slot":1,"item_id":"1031","quantity":2,
                "before_count":5,"before_end":10000,"seconds":7200,**changes}

    def known(self):
        self.service.live["user_combos_compressed"]["22~23"]=1

    def saved(self):
        return json.loads(self.path.read_text(encoding="utf-8"))["research"]

    def test_replenished_bank_completed_target_is_archived_without_claiming_spend(self):
        self.seed(self.speedup());self.known();self.service.live["user_items"]["1031"]=7
        result=self.engine().reconcile_only();saved=self.saved()
        self.assertTrue(result.data["reconciled"])
        self.assertIsNone(saved["pending_action"])
        self.assertEqual(saved["spent_seconds"],14*3600)
        self.assertEqual(saved["confirmed_speedup_seconds"],12*3600)
        self.assertEqual(result.data["speedups_reserved_hours"],2)
        self.assertEqual(result.data["speedups_settled_unverified_hours"],2)
        self.assertEqual(saved["settled_uncertain_actions"][0]["reason"],"target_researched_spend_unverified")
        self.assertEqual(saved["completed"],1)
        self.assertEqual(self.service.mutations(),[])
        self.engine().reconcile_only()
        self.assertEqual(self.saved()["settled_uncertain_count"],1)

    def test_new_queue_reconciles_old_completed_target_and_retains_unverified_audit(self):
        self.seed(self.speedup());self.known();self.service.live["user_items"]["1031"]=7
        changed=ResearchOptions(ads=False,speedups=True,use_all_speedups=True,max_speedup_hours=0)
        answer=self.engine().run(changed,resume=False,wait=False,max_actions=0)
        saved=self.saved()
        self.assertIsNone(saved["pending_action"])
        self.assertEqual(saved["spent_seconds"],0)
        self.assertEqual(saved["confirmed_speedup_seconds"],0)
        self.assertEqual(saved["settled_uncertain_actions"][0]["seconds"],7200)
        self.assertEqual(saved["settled_uncertain_speedup_seconds"],7200)
        self.assertEqual(answer.data["speedups_settled_unverified_hours"],2)
        self.assertTrue(saved["options"]["use_all_speedups"])
        self.assertEqual(self.service.mutations(),[])

    def test_reconciliation_precedes_changed_resume_settings_without_running_actions(self):
        self.seed(self.speedup());self.known();self.service.live["user_items"]["1031"]=7
        with self.assertRaises(CommandInputError):
            self.engine().run(ResearchOptions(ads=False,speedups=False),wait=False)
        self.assertIsNone(self.saved()["pending_action"])
        self.assertEqual(self.saved()["settled_uncertain_count"],1)
        self.assertEqual(self.service.mutations(),[])

    def test_unknown_active_speedup_does_not_clear_or_replay_with_changed_bank(self):
        self.service._start({"card1":22,"card2":23,"slot_index":1})
        action=self.speedup(task_index=201,before_time=1000)
        self.seed(action);self.service.live["user_items"]["1031"]=7
        result=self.engine().reconcile_only()
        self.assertFalse(result.data["reconciled"])
        self.assertEqual(result.data["status"],"uncertain")
        self.assertEqual(self.saved()["pending_action"],action)
        self.assertEqual(self.saved()["confirmed_speedup_seconds"],12*3600)
        with self.assertRaises(CommandUnavailable):
            self.engine().run(ResearchOptions(ads=False,use_all_speedups=True),resume=False,wait=False)
        self.assertEqual(self.service.mutations(),[])

    def test_known_pair_with_live_same_pair_task_cannot_retire_unknown_spend(self):
        self.service._start({"card1":22,"card2":23,"slot_index":2})
        self.seed(self.speedup());self.known();self.service.live["user_items"]["1031"]=7
        result=self.engine().reconcile_only()
        self.assertFalse(result.data["reconciled"])
        self.assertIsNotNone(self.saved()["pending_action"])
        self.assertEqual(self.service.mutations(),[])

    def test_exact_balance_and_completed_target_confirm_only_once(self):
        self.seed(self.speedup());self.known();self.service.live["user_items"]["1031"]=3
        self.engine().reconcile_only();self.engine().reconcile_only()
        self.assertEqual(self.saved()["confirmed_speedup_seconds"],14*3600)
        self.assertEqual(self.saved().get("settled_uncertain_count",0),0)
        self.assertEqual(self.service.mutations(),[])

    def test_elapsed_clock_and_unchanged_task_do_not_confirm_item_use(self):
        self.service._start({"card1":22,"card2":23,"slot_index":1})
        self.seed(self.speedup(before_end=2800,task_index=201,before_time=1000))
        self.service.live["user_items"]["1031"]=3;self.service.now=20000
        result=self.engine().reconcile_only()
        self.assertFalse(result.data["reconciled"])
        self.assertEqual(self.saved()["confirmed_speedup_seconds"],12*3600)

    def test_replaced_same_pair_task_does_not_confirm_item_use(self):
        self.service._start({"card1":22,"card2":23,"slot_index":1})
        self.seed(self.speedup(task_index=999,before_time=1000))
        self.service.live["user_tasks"]["201"]["end_time"]=1000
        self.service.live["user_items"]["1031"]=3
        self.assertFalse(self.engine().reconcile_only().data["reconciled"])

    def test_legacy_active_task_without_identity_stays_uncertain_until_completed(self):
        self.service._start({"card1":22,"card2":23,"slot_index":1})
        self.seed(self.speedup());self.service.live["user_items"]["1031"]=3
        self.service.live["user_tasks"]["201"]["end_time"]=1000
        self.assertFalse(self.engine().reconcile_only().data["reconciled"])

    def test_exact_frozen_task_reduction_and_item_count_confirm_without_actions(self):
        self.service._start({"card1":22,"card2":23,"slot_index":1})
        self.seed(self.speedup(quantity=1,seconds=3600,task_index=201,before_time=1000))
        self.service.live["user_tasks"]["201"]["end_time"]=6400
        self.service.live["user_items"]["1031"]=4
        result=self.engine().reconcile_only()
        self.assertTrue(result.data["reconciled"])
        self.assertEqual(self.saved()["confirmed_speedup_seconds"],13*3600)
        self.assertEqual(self.service.mutations(),[])

    def test_completed_timer_can_clamp_to_fresh_server_time_after_dispatch(self):
        self.service._start({"card1":22,"card2":23,"slot_index":1})
        self.seed(self.speedup(quantity=1,seconds=3600,before_end=2800,task_index=201,before_time=1000))
        self.service.now=1001
        self.service.live["user_tasks"]["201"]["end_time"]=1001
        self.service.live["user_items"]["1031"]=4
        result=self.engine().reconcile_only()
        self.assertTrue(result.data["reconciled"])
        self.assertEqual(self.saved()["confirmed_speedup_seconds"],13*3600)
        self.assertEqual(self.service.mutations(),[])

    def test_applied_pending_start_reconciles_before_new_queue_replacement(self):
        self.seed({"kind":"start","pair":"22~23","slot":1})
        self.service._start({"card1":22,"card2":23,"slot_index":1})
        changed=ResearchOptions(ads=False,speedups=False,max_pairs=1)
        self.engine().run(changed,resume=False,wait=False,max_actions=0)
        self.assertIsNone(self.saved()["pending_action"])
        self.assertEqual(self.service.mutations(),[])

    def test_unapplied_start_is_never_discarded_by_new_queue(self):
        self.seed({"kind":"start","pair":"22~23","slot":1})
        with self.assertRaises(CommandUnavailable):
            self.engine().run(ResearchOptions(ads=False,max_pairs=1),resume=False,wait=False)
        self.assertEqual(self.saved()["pending_action"]["kind"],"start")
        self.assertEqual(self.service.mutations(),[])

    def test_same_engine_reconciliation_updates_loaded_rows_without_double_count(self):
        engine=self.seed({"kind":"collect","pair":"22~23","slot":1});self.known()
        answer=engine.run(self.options,wait=False,max_actions=0)
        self.assertEqual(answer.data["completed"],1)
        self.assertEqual(self.saved()["completed"],1)

    def test_old_options_missing_new_flag_resume_and_daily_preflight_compatibly(self):
        self.seed();raw=json.loads(self.path.read_text());del raw["research"]["options"]["use_all_speedups"]
        self.path.write_text(json.dumps(raw))
        self.engine().run(self.options,wait=False,max_actions=0)
        daily=research_state_path(self.service,"daily",self.directory.name)
        raw=json.loads(self.path.read_text());del raw["research"]["options"]["use_all_speedups"]
        daily.write_text(json.dumps(raw))
        result=research_daily_preflight(self.service,self.options.serialized(),self.service.live,state_dir=self.directory.name)
        self.assertTrue(result["available"])
        self.assertEqual(self.service.mutations(),[])

    def test_stop_during_speedup_intent_save_sends_nothing_and_refunds_only_unsent_intent(self):
        self.service._start({"card1":22,"card2":23,"slot_index":1})
        engine=self.seed();save=engine._save
        def stop_on_intent():
            save()
            if (engine.state.get("pending_action") or {}).get("kind")=="speedup":
                self.service.stop.set()
        with patch.object(engine,"_save",side_effect=stop_on_intent):
            result=engine.run(self.options,wait=False)
        self.assertEqual(result.data["status"],"paused")
        self.assertEqual(self.saved()["spent_seconds"],0)
        self.assertIsNone(self.saved()["pending_action"])
        self.assertEqual(self.service.mutations(),[])

    def test_compact_refresh_cannot_retain_stale_inventory_mirror(self):
        self.service.live["item_inventory"]=deepcopy(self.service.live["user_items"])
        engine=self.engine();engine._fresh(initial=True)
        self.service.live["user_items"]["1031"]=4
        self.service.live["item_inventory"]["1031"]=4
        self.service.compact={key:deepcopy(self.service.live[key]) for key in ("user_tasks","user_combos_compressed","user_items")}
        engine._fresh()
        self.assertEqual(engine.live["item_inventory"]["1031"],4)
        self.assertEqual([name for name,_ in self.service.calls].count("init"),2)

    def test_compact_refresh_without_ad_credit_falls_back_to_full_read(self):
        engine=self.engine();engine._fresh(initial=True)
        engine.state={"pending_action":{"kind":"ad"}}
        self.service.live["research_ad_speed_ups"][0]["views"]=1
        self.service.compact={key:deepcopy(self.service.live[key]) for key in ("user_tasks","user_combos_compressed","user_items")}
        engine._fresh()
        self.assertEqual(engine.live["research_ad_speed_ups"][0]["views"],1)
        self.assertEqual([name for name,_ in self.service.calls].count("init"),2)

    def test_conservative_history_is_bounded_and_totals_survive_record_rotation(self):
        engine=self.seed(self.speedup());self.known();self.service.live["user_items"]["1031"]=7
        engine._fresh(initial=True)
        for _ in range(105):
            engine.state["pending_action"]=self.speedup()
            engine._reconcile()
        saved=self.saved()
        self.assertEqual(len(saved["settled_uncertain_actions"]),100)
        self.assertEqual(saved["settled_uncertain_count"],105)
        self.assertEqual(saved["settled_uncertain_speedup_seconds"],105*7200)

    def test_malformed_pending_action_cannot_be_treated_as_no_pending_action(self):
        self.seed();raw=json.loads(self.path.read_text());raw["research"]["pending_action"]={}
        self.path.write_text(json.dumps(raw))
        with self.assertRaises(CommandUnavailable):self.engine().reconcile_only()
        self.assertEqual(self.service.mutations(),[])

    def test_missing_pending_action_key_cannot_authorize_queue_replacement(self):
        self.seed();raw=json.loads(self.path.read_text());del raw["research"]["pending_action"]
        self.path.write_text(json.dumps(raw))
        with self.assertRaises(CommandUnavailable):self.engine().reconcile_only()
        with self.assertRaises(CommandUnavailable):
            self.engine().run(ResearchOptions(ads=False,use_all_speedups=True),resume=False,wait=False)
        self.assertEqual(self.service.mutations(),[])

    def test_reconcile_command_only_reads_and_returns_recovery_status(self):
        path=research_state_path(self.service,"user",self.directory.name)
        self.path=path;self.seed(self.speedup());self.known();self.service.live["user_items"]["1031"]=7
        register_research_automation(self.service,state_dir=self.directory.name)
        answer=self.service.execute("research-reconcile",{"scope":"user"})
        self.assertEqual(answer.data["status"],"reconciled")
        self.assertFalse(self.service.specs["research-reconcile"].spends_resources)
        self.assertEqual(self.service.mutations(),[])


class UseAllSpeedupTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/"queue.json";self.service=FakeService()

    def run_queue(self,**options):
        return ResearchAutomation(self.service,self.path).run(
            ResearchOptions(ads=False,speedups=True,**options),wait=False,max_actions=10)

    def test_use_all_ignores_numeric_cap_instead_of_treating_zero_as_no_spend(self):
        result=self.run_queue(use_all_speedups=True,max_speedup_hours=0,max_pairs=1)
        self.assertEqual(result.data["completed"],1)
        self.assertEqual(result.data["speedups_used_hours"],1)

    def test_disabled_use_all_keeps_numeric_zero_budget(self):
        result=self.run_queue(use_all_speedups=False,max_speedup_hours=0,max_pairs=1)
        self.assertEqual(result.data["speedups_used_hours"],0)
        self.assertFalse(any(name=="useResearchSpeedUpItem" for name,_ in self.service.mutations()))

    def test_use_all_preserves_current_inventory_reserve(self):
        self.service.live["user_items"]={"1031":3,"1032":0,"1033":0}
        result=self.run_queue(use_all_speedups=True,max_speedup_hours=0,reserve_hours=2)
        self.assertEqual(result.data["speedups_used_hours"],1)
        self.assertEqual(self.service.live["user_items"]["1031"],2)

    def test_boolean_validation_is_strict_and_old_option_default_is_false(self):
        self.assertFalse(ResearchOptions.from_dict({}).use_all_speedups)
        for value in (0,1,"true",None):
            with self.subTest(value=value),self.assertRaises(CommandInputError):
                ResearchOptions(use_all_speedups=value)


if __name__=="__main__":unittest.main()
