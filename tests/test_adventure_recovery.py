"""Recover capped farming only from a fresh native no-resume account state."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atcommander.adventure_run_state import AdventureRunState
from atcommander.adventure_runner import AdventureRunner
from test_adventure_completion_runner import Client


class AdventureRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/"pending.json";self.events=[]
        self.client=Client(wins=13);self.client.data["time"]=2000
        self.runner=AdventureRunner(101,complete_islands=True,state_path=self.path,event=self.events.append)
        self.runner._client=self.client
        self.runner._journal=AdventureRunState(self.client.credentials.user_id,self.path)
        self.pending={"mission_id":101,"campaign_id":"1","wins_before":13,"phase":"move_pending",
                      "cost":2,"farming":True,"server_time":1000}
        self.runner._journal.save(self.pending)

    def reconcile(self):
        return self.runner._reconcile_pending(self.client.init())

    def test_ended_capped_farm_is_archived_without_replay_or_outcome_credit(self):
        self.assertTrue(self.reconcile())
        saved=json.loads(self.path.read_text())
        self.assertIsNone(saved["pending"])
        self.assertEqual(saved["recovery_count"],1)
        self.assertEqual(saved["recoveries"][0]["pending"],self.pending)
        self.assertEqual(saved["recoveries"][0]["reason"],"completed_farm_no_resumable_battle")
        self.assertFalse(self.client.starts)
        self.assertFalse(any(event["kind"] in {"battle_end","adventure_completion_verified"} for event in self.events))
        self.assertTrue(self.reconcile())
        self.assertEqual(json.loads(self.path.read_text())["recovery_count"],1)

    def test_accepted_before_first_move_can_recover_with_the_same_evidence(self):
        self.pending["phase"]="battle_ready";self.runner._journal.save(self.pending)
        self.assertTrue(self.reconcile());self.assertFalse(self.client.starts)

    def test_native_explicit_zero_resume_marker_is_supported(self):
        for marker in (0,"0"):
            with self.subTest(marker=marker):
                self.runner._journal.save(self.pending);self.client.data["battle_to_resume"]=marker
                self.assertTrue(self.reconcile())

    def test_advertised_or_malformed_resume_markers_preserve_the_intent(self):
        for marker in (1,"1",True,False,None,"unknown",{},[]):
            with self.subTest(marker=marker):
                self.client.data["battle_to_resume"]=marker
                self.assertFalse(self.reconcile())
                self.assertEqual(self.runner._journal.read(),self.pending)
        self.assertFalse(self.client.starts)

    def test_any_previous_battle_payload_even_null_or_empty_prevents_recovery(self):
        for value in (None,{},False,"",{"battle_id":"synthetic-private-battle"}):
            with self.subTest(value=value):
                self.client.data["active_battle_data"]=value
                self.assertFalse(self.reconcile())
        self.assertFalse(self.client.starts)

    def test_other_current_battle_envelopes_are_not_ignored(self):
        for field in ("battle_data","current_battle","battle_state","active_battle","battle","combat_state"):
            with self.subTest(field=field):
                self.client.data[field]={"battle_id":"synthetic-private-battle"}
                self.assertFalse(self.reconcile());del self.client.data[field]
        self.assertFalse(self.client.starts)

    def test_inflight_or_clock_regression_never_authorizes_recovery(self):
        for server_time in (1000,1119,999,True):
            with self.subTest(server_time=server_time):
                self.client.data["time"]=server_time
                self.assertFalse(self.reconcile())
        self.assertFalse(self.client.starts)

    def test_start_intent_is_not_discarded_even_for_a_mastered_stage(self):
        self.pending["phase"]="start_pending";self.runner._journal.save(self.pending)
        self.assertFalse(self.reconcile());self.assertFalse(self.client.starts)

    def test_incomplete_island_route_is_not_relabelled_as_farming(self):
        self.pending["farming"]=False;self.runner._journal.save(self.pending)
        self.assertFalse(self.reconcile())
        self.pending.update(farming=True,wins_before=11);self.runner._journal.save(self.pending)
        self.client.data["mission_completions"]["101"]["complete"]=11
        self.client.data["current_missions"]["101"]["level"]=12
        self.assertFalse(self.reconcile());self.assertFalse(self.client.starts)

    def test_unavailable_stage_or_regressed_progress_never_recovers(self):
        self.client.data["current_missions"].pop("101")
        self.assertFalse(self.reconcile())
        self.client.data["current_missions"]["101"]={"energy":2,"level":13}
        self.client.data["mission_completions"]["101"]["complete"]=12
        self.assertFalse(self.reconcile());self.assertFalse(self.client.starts)

    def test_terminal_unknown_farm_outcome_can_be_archived_without_outcome_credit(self):
        self.pending.update(phase="terminal",terminal=None);self.runner._journal.save(self.pending)
        self.assertTrue(self.reconcile())
        saved=json.loads(self.path.read_text())
        self.assertIsNone(saved["recoveries"][0]["pending"]["terminal"])
        self.assertFalse(any(event["kind"]=="battle_end" for event in self.events))

    def test_credited_progress_and_known_farm_outcomes_keep_established_reconciliation(self):
        self.pending.update(farming=False,wins_before=12);self.runner._journal.save(self.pending)
        self.assertTrue(self.reconcile())
        self.pending.update(farming=True,wins_before=13,phase="terminal",terminal=True)
        self.runner._journal.save(self.pending);self.assertTrue(self.reconcile())
        self.assertNotIn("recoveries",json.loads(self.path.read_text()))

    def test_partial_or_rejected_reads_cannot_prove_no_resume(self):
        progress={"wins":13,"level":13,"available":True}
        for field in ("user_data","current_campaigns","current_missions","mission_completions"):
            with self.subTest(field=field):
                live=self.client.init();del live[field]
                self.assertFalse(self.runner._completed_farm_has_no_resume(self.pending,live,progress))
        for result in (False,0,"0",{},None):
            live=self.client.init();live["result"]=result
            self.assertFalse(self.runner._completed_farm_has_no_resume(self.pending,live,progress))

    def test_full_native_init_without_result_marker_can_recover(self):
        del self.client.data["result"]
        self.assertTrue(self.reconcile())
        self.assertFalse(self.client.starts)
        self.assertIsNone(self.runner._journal.read())

    def test_pause_finished_event_carries_reason_for_unrecoverable_request(self):
        self.pending["phase"]="start_pending";self.runner._journal.save(self.pending)
        with patch("atcommander.adventure_runner.ad_preflight"):
            self.runner._run_completion(self.client.init())
        finished=[event for event in self.events if event["kind"]=="finished"][-1]
        self.assertTrue(finished["paused"])
        self.assertIn("unconfirmed",finished["pause_reason"])
        self.assertFalse(self.client.starts)

    def test_history_survives_later_intents_and_normal_clears(self):
        self.assertTrue(self.reconcile())
        self.runner._journal.save(self.pending);self.runner._journal.save(None)
        saved=json.loads(self.path.read_text())
        self.assertEqual(saved["recovery_count"],1)
        self.assertEqual(saved["recoveries"][0]["pending"],self.pending)

    def test_history_is_bounded_with_total_retained_and_no_private_battle_fields(self):
        for index in range(12):
            self.runner._journal.save(self.pending)
            self.runner._journal.archive_completed_farm(self.pending,2000+index)
        saved=json.loads(self.path.read_text())
        self.assertEqual(saved["recovery_count"],12);self.assertEqual(len(saved["recoveries"]),8)
        self.assertLess(self.path.stat().st_size,8192)
        self.assertNotIn("battle_id",self.path.read_text())
        self.assertIsNone(self.runner._journal.read())

    def test_newer_record_written_during_recovery_is_not_archived(self):
        archive=self.runner._journal.archive_completed_farm
        newer={**self.pending,"server_time":1950,"phase":"battle_ready"}
        def concurrent_change(expected,checked_at):
            self.runner._journal.save(newer)
            return archive(expected,checked_at)
        with patch.object(self.runner._journal,"archive_completed_farm",side_effect=concurrent_change):
            with self.assertRaises(RuntimeError):self.reconcile()
        self.assertEqual(self.runner._journal.read(),newer)
        self.assertNotIn("recoveries",json.loads(self.path.read_text()))
        self.assertFalse(self.client.starts)

    def test_archive_rechecks_age_even_when_the_expected_record_matches(self):
        recent={**self.pending,"server_time":1950}
        self.runner._journal.save(recent)
        with self.assertRaises(RuntimeError):
            self.runner._journal.archive_completed_farm(recent,2000)
        self.assertEqual(self.runner._journal.read(),recent)

    def test_corrupt_recovery_history_blocks_even_an_explicit_clear(self):
        self.assertTrue(self.reconcile())
        raw=json.loads(self.path.read_text());raw["recoveries"][0]["reason"]="unverified override"
        self.path.write_text(json.dumps(raw))
        with self.assertRaises(RuntimeError):self.runner._journal.save(None)


if __name__=="__main__":unittest.main()
