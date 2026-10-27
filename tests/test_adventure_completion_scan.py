"""Pure Adventure planning: incomplete and unknown stages never mean 100%."""
from copy import deepcopy
import unittest

from atcommander.adventure_completion import (
    AdventureScanError, mission_progress, scan_adventure_islands,
)


def fixture():
    campaigns = {"1": {"id": 1, "name": "Story One", "track": "story",
                        "missions": [101, 102, 103], "campaign_req": 0}}
    missions = {str(mid): {"id": mid, "energy": 5} for mid in (101, 102, 103)}
    live = {"result": True, "time": 1000,
            "current_campaigns": {"1": {"campaign_id": 1, "completed": 1}},
            "current_missions": {str(mid): {"mission_id": mid, "level": 1, "energy": 5}
                                 for mid in (101, 102, 103)},
            "mission_completions": {}}
    return live, campaigns, missions


def master(live, mids=(101, 102, 103), campaign=1):
    for mid in mids:
        key = str(mid)
        if key in live["current_missions"]:
            live["current_missions"][key]["level"] = 13
        live["mission_completions"][key] = {
            "mission_id": mid, "campaign_id": campaign, "complete": 12}


def add_island(live, campaigns, missions, cid, mids, *, track="story", prerequisite=0,
               active=False, exposed=False):
    campaigns[str(cid)] = {"id": cid, "name": f"Island {cid}", "track": track,
                           "campaign_req": prerequisite, "missions": list(mids)}
    for mid in mids:
        missions[str(mid)] = {"id": mid, "energy": 5}
        if exposed:
            live["current_missions"][str(mid)] = {"mission_id": mid, "level": 1, "energy": 5}
    if active:
        live["current_campaigns"][str(cid)] = {"campaign_id": cid, "completed": 0}


class AdventureCompletionScanTests(unittest.TestCase):
    def test_partial_exposure_retains_all_catalog_stages_and_cannot_complete(self):
        live, campaigns, missions = fixture()
        master(live, (101, 102))
        del live["current_missions"]["103"]
        result = scan_adventure_islands(live, campaigns, missions)
        self.assertFalse(result["all_complete"])
        self.assertEqual(result["completed_stages"], 2)
        self.assertEqual(result["total_stages"], 3)
        self.assertEqual(result["islands"][0]["state"], "Locked")
        self.assertIsNone(result["next_mission"])

    def test_missing_entire_dynamic_table_rows_never_authorizes_fallback(self):
        live, campaigns, missions = fixture()
        live["current_missions"] = {}
        result = scan_adventure_islands(live, campaigns, missions)
        self.assertFalse(result["all_complete"])
        self.assertEqual(result["progress_percent"], 0)
        self.assertIsNone(result["next_mission"])

    def test_absent_counts_level_one_means_zero_but_level_thirteen_means_mastered(self):
        live, campaigns, missions = fixture()
        self.assertEqual(mission_progress(live, 101)["wins"], 0)
        for row in live["current_missions"].values():
            row["level"] = 13
        self.assertEqual(mission_progress(live, 101)["wins"], 12)
        result = scan_adventure_islands(live, campaigns, missions)
        self.assertTrue(result["all_complete"])
        self.assertEqual(result["progress_percent"], 100)

    def test_normalized_boss_mastery_uses_complete_twelve_not_raw_number_four(self):
        live, campaigns, missions = fixture()
        master(live)
        live["mission_completions"]["103"]["number"] = 4
        self.assertEqual(mission_progress(live, 103, 1)["wins"], 12)
        self.assertTrue(scan_adventure_islands(live, campaigns, missions)["all_complete"])

    def test_raw_boss_count_cannot_override_normalized_incomplete_progress(self):
        live, campaigns, missions = fixture()
        live["current_missions"]["101"]["level"] = 5
        live["mission_completions"]["101"] = {"mission_id": 101, "complete": 4, "number": 12}
        result = scan_adventure_islands(live, campaigns, missions)
        self.assertFalse(result["all_complete"])
        self.assertEqual(result["next_mission"]["wins"], 4)

    def test_saved_verified_mastery_can_survive_absent_dynamic_completed_stage(self):
        live, campaigns, missions = fixture()
        master(live)
        del live["current_missions"]["103"]
        result = scan_adventure_islands(live, campaigns, missions)
        self.assertTrue(result["all_complete"])
        self.assertFalse(result["islands"][0]["missions"][2]["available"])

    def test_first_clear_campaign_flag_does_not_authorize_mastery(self):
        live, campaigns, missions = fixture()
        result = scan_adventure_islands(live, campaigns, missions)
        self.assertFalse(result["all_complete"])
        self.assertEqual(result["islands"][0]["remaining_wins"], 36)
        self.assertEqual(result["next_mission"]["mission"], 101)

    def test_progress_and_mastery_use_distinct_verified_goals(self):
        live, campaigns, missions = fixture()
        for mid in (101, 102, 103):
            live["current_missions"][str(mid)]["level"] = 2
            live["mission_completions"][str(mid)] = {"mission_id": mid, "complete": 1}
        self.assertTrue(scan_adventure_islands(live, campaigns, missions, target="progress")["all_complete"])
        self.assertFalse(scan_adventure_islands(live, campaigns, missions, target="mastery")["all_complete"])

    def test_stage_order_uses_public_catalog_order_not_numeric_sort(self):
        live, campaigns, missions = fixture()
        campaigns["1"]["missions"] = [103, 101, 102]
        result = scan_adventure_islands(live, campaigns, missions)
        self.assertEqual(result["next_mission"]["mission"], 103)
        self.assertEqual([r["mission"] for r in result["islands"][0]["missions"]], [103, 101, 102])

    def test_later_exposed_stage_cannot_skip_first_locked_incomplete_stage(self):
        live, campaigns, missions = fixture()
        del live["current_missions"]["101"]
        result = scan_adventure_islands(live, campaigns, missions)
        self.assertIsNone(result["next_mission"])
        self.assertEqual(result["islands"][0]["state"], "Locked")

    def test_locked_islands_in_open_track_are_kept_until_fresh_unlock(self):
        live, campaigns, missions = fixture()
        master(live)
        add_island(live, campaigns, missions, 2, [201, 202], prerequisite=1)
        before = scan_adventure_islands(live, campaigns, missions)
        self.assertFalse(before["all_complete"])
        self.assertEqual(before["total_stages"], 5)
        self.assertIsNone(before["next_mission"])
        live["current_campaigns"]["2"] = {"campaign_id": 2, "completed": 0}
        live["current_missions"]["201"] = {"mission_id": 201, "level": 1, "energy": 7}
        after = scan_adventure_islands(live, campaigns, missions)
        self.assertEqual(after["next_mission"]["mission"], 201)
        self.assertEqual(after["next_mission"]["energy_cost"], 7)

    def test_explicit_prerequisites_precede_child_even_if_catalog_is_reversed(self):
        live, campaigns, missions = fixture()
        add_island(live, campaigns, missions, 2, [201], prerequisite=1, active=True, exposed=True)
        campaigns = {"2": campaigns["2"], "1": campaigns["1"]}
        result = scan_adventure_islands(live, campaigns, missions)
        self.assertEqual([r["id"] for r in result["islands"]], ["1", "2"])
        self.assertEqual(result["next_mission"]["mission"], 101)

    def test_three_level_prerequisite_chain_and_stable_unrelated_order(self):
        live, campaigns, missions = fixture()
        add_island(live, campaigns, missions, 2, [201], prerequisite=1)
        add_island(live, campaigns, missions, 3, [301], prerequisite=2)
        add_island(live, campaigns, missions, 4, [401])
        campaigns = {k: campaigns[k] for k in ("3", "4", "2", "1")}
        result = scan_adventure_islands(live, campaigns, missions)
        self.assertEqual([r["id"] for r in result["islands"]], ["1", "2", "3", "4"])

    def test_prerequisite_cycle_fails_closed(self):
        live, campaigns, missions = fixture()
        add_island(live, campaigns, missions, 2, [201], prerequisite=1)
        campaigns["1"]["campaign_req"] = 2
        with self.assertRaises(AdventureScanError):
            scan_adventure_islands(live, campaigns, missions)

    def test_closed_track_is_not_reopened_by_saved_mastery_or_catalog_presence(self):
        live, campaigns, missions = fixture()
        add_island(live, campaigns, missions, 90, [901], track="closed", active=True, exposed=True)
        live["current_campaigns"]["90"]["end_time"] = 1000
        result = scan_adventure_islands(live, campaigns, missions)
        self.assertEqual([r["id"] for r in result["islands"]], ["1"])

    def test_static_expired_or_future_island_in_open_track_is_excluded(self):
        for window in ({"end_time": 1000}, {"start_time": 1001}):
            with self.subTest(window=window):
                live, campaigns, missions = fixture()
                add_island(live, campaigns, missions, 2, [201], active=True, exposed=True)
                campaigns["2"].update(window)
                result = scan_adventure_islands(live, campaigns, missions)
                self.assertEqual([r["id"] for r in result["islands"]], ["1"])

    def test_explicit_current_window_cannot_reintroduce_closed_island_in_open_track(self):
        for window in ({"end_time": 1000}, {"start_time": 1001}):
            with self.subTest(window=window):
                live, campaigns, missions = fixture()
                master(live)
                add_island(live, campaigns, missions, 2, [201], active=True, exposed=True)
                live["current_campaigns"]["2"].update(window)
                result = scan_adventure_islands(live, campaigns, missions)
                self.assertEqual([r["id"] for r in result["islands"]], ["1"])
                self.assertTrue(result["all_complete"])

    def test_identity_conflicts_fail_closed_for_every_public_table(self):
        for table in ("current_campaigns", "current_missions", "mission_completions", "campaigns", "missions"):
            with self.subTest(table=table):
                live, campaigns, missions = fixture()
                if table == "campaigns":
                    campaigns["1"]["id"] = 2
                elif table == "missions":
                    missions["101"]["id"] = 102
                elif table == "current_campaigns":
                    live[table]["1"]["campaign_id"] = 2
                elif table == "current_missions":
                    live[table]["101"]["mission_id"] = 102
                else:
                    live[table]["101"] = {"id": 102, "mission_id": 102, "complete": 12}
                with self.assertRaises(AdventureScanError):
                    scan_adventure_islands(live, campaigns, missions)

    def test_list_id_aliases_must_agree_and_duplicates_are_rejected(self):
        for value in ([{"id": 101, "mission_id": 102, "energy": 5, "level": 1}],
                      [{"id": 101, "energy": 5, "level": 1}] * 2):
            live, campaigns, missions = fixture()
            live["current_missions"] = value
            with self.assertRaises(AdventureScanError):
                scan_adventure_islands(live, campaigns, missions)

    def test_missing_or_duplicate_catalog_stage_mapping_fails_closed(self):
        for change in ("missing", "duplicate", "no-order", "empty-order"):
            with self.subTest(change=change):
                live, campaigns, missions = fixture()
                if change == "missing":
                    del missions["103"]
                elif change == "duplicate":
                    campaigns["1"]["missions"] = [101, 102, 101]
                elif change == "no-order":
                    del campaigns["1"]["missions"]
                else:
                    campaigns["1"]["missions"] = []
                with self.assertRaises(AdventureScanError):
                    scan_adventure_islands(live, campaigns, missions)

    def test_mission_cannot_belong_to_two_scoped_islands(self):
        live, campaigns, missions = fixture()
        add_island(live, campaigns, missions, 2, [101], prerequisite=1)
        with self.assertRaises(AdventureScanError):
            scan_adventure_islands(live, campaigns, missions)

    def test_progress_belonging_to_different_island_is_not_used(self):
        live, campaigns, missions = fixture()
        master(live)
        live["mission_completions"]["101"]["campaign_id"] = 2
        with self.assertRaises(AdventureScanError):
            scan_adventure_islands(live, campaigns, missions)

    def test_live_level_and_saved_count_disagreement_fails_closed(self):
        for level, count in ((1, 12), (13, 4), (4, 1)):
            live, campaigns, missions = fixture()
            live["current_missions"]["101"]["level"] = level
            live["mission_completions"]["101"] = {"mission_id": 101, "complete": count}
            with self.assertRaises(AdventureScanError):
                scan_adventure_islands(live, campaigns, missions)

    def test_unknown_energy_and_malformed_levels_or_counts_cannot_plan_fights(self):
        for field, values in (("energy", (None, True, -1, 1.5, "1.5")),
                              ("level", (None, True, 0, 14, 1.5)),
                              ("complete", (None, True, -1, 1.5, "1.5"))):
            for value in values:
                with self.subTest(field=field, value=value):
                    live, campaigns, missions = fixture()
                    if field == "complete":
                        live["mission_completions"]["101"] = {"mission_id": 101, "complete": value}
                    else:
                        live["current_missions"]["101"][field] = value
                    with self.assertRaises(AdventureScanError):
                        scan_adventure_islands(live, campaigns, missions)

    def test_dynamic_energy_is_authoritative_over_static_base_cost(self):
        live, campaigns, missions = fixture()
        missions["101"]["energy"] = 1
        missions["101"]["energy_per_level"] = 2
        live["current_missions"]["101"]["energy"] = 7
        result = scan_adventure_islands(live, campaigns, missions)
        self.assertEqual(result["next_mission"]["energy_cost"], 7)

    def test_missing_live_sections_and_server_time_fail_closed(self):
        for key in ("current_missions", "mission_completions", "current_campaigns", "time"):
            live, campaigns, missions = fixture()
            del live[key]
            with self.assertRaises(AdventureScanError):
                scan_adventure_islands(live, campaigns, missions)

    def test_rejected_account_cannot_authorize_all_complete(self):
        for rejected in (False, 0, "0", "false"):
            with self.subTest(rejected=rejected):
                live, campaigns, missions = fixture()
                master(live)
                live["result"] = rejected
                with self.assertRaises(AdventureScanError):
                    scan_adventure_islands(live, campaigns, missions)

    def test_progress_reconciliation_cannot_use_rejected_snapshot(self):
        for rejected in (False, 0, "0", "false"):
            with self.subTest(rejected=rejected):
                live, _campaigns, _missions = fixture()
                master(live)
                live["result"] = rejected
                with self.assertRaises(AdventureScanError):
                    mission_progress(live, 101, 1)

    def test_limited_adventure_expiry_removes_closed_track_even_with_saved_rows(self):
        live, campaigns, missions = fixture()
        add_island(live, campaigns, missions, 90, [901], track="seasonal", active=True, exposed=True)
        live["limited_adventure_data"] = {
            "limited_adventure_track": "seasonal", "limited_adventure_end_time": 1000}
        result = scan_adventure_islands(live, campaigns, missions)
        self.assertEqual([r["id"] for r in result["islands"]], ["1"])

    def test_unlocked_current_limited_track_stays_and_includes_locked_sequel(self):
        live, campaigns, missions = fixture()
        master(live)
        add_island(live, campaigns, missions, 90, [901], track="seasonal", active=True, exposed=True)
        add_island(live, campaigns, missions, 91, [902], track="seasonal", prerequisite=90)
        live["limited_adventure_data"] = {
            "limited_adventure_track": "seasonal", "limited_adventure_end_time": 1001}
        live["limited_adventure_island_unlock"] = True
        result = scan_adventure_islands(live, campaigns, missions)
        self.assertEqual([r["id"] for r in result["islands"]], ["1", "90", "91"])
        self.assertEqual(result["next_mission"]["mission"], 901)
        self.assertFalse(result["all_complete"])

    def test_explicit_limited_track_lock_does_not_authorize_seasonal_battle(self):
        for locked in (False, 0, "0", "false"):
            with self.subTest(locked=locked):
                live, campaigns, missions = fixture()
                add_island(live, campaigns, missions, 90, [901], track="seasonal", active=True, exposed=True)
                live["limited_adventure_data"] = {
                    "limited_adventure_track": "seasonal", "limited_adventure_end_time": 1001}
                live["limited_adventure_island_unlock"] = locked
                result = scan_adventure_islands(live, campaigns, missions)
                self.assertEqual([r["id"] for r in result["islands"]], ["1"])

    def test_contradictory_explicit_mission_parent_mapping_fails_closed(self):
        for table in ("catalog", "live"):
            with self.subTest(table=table):
                live, campaigns, missions = fixture()
                row = missions["101"] if table == "catalog" else live["current_missions"]["101"]
                row["campaign_id"] = 90
                with self.assertRaises(AdventureScanError):
                    scan_adventure_islands(live, campaigns, missions)

    def test_empty_optional_parent_fields_are_absent_not_invalid_campaigns(self):
        live, campaigns, missions = fixture()
        master(live)
        for row in missions.values():
            row["campaign_id"] = ""
        for row in live["current_missions"].values():
            row["campaign_id"] = ""
        for row in live["mission_completions"].values():
            row["campaign_id"] = ""
        self.assertEqual(mission_progress(live, 101, 1)["wins"], 12)
        self.assertTrue(scan_adventure_islands(live, campaigns, missions)["all_complete"])

    def test_nonempty_malformed_optional_parent_fields_still_fail_closed(self):
        for table in ("catalog", "live", "saved"):
            for parent in (True, "unknown", "-1", 0):
                with self.subTest(table=table, parent=parent):
                    live, campaigns, missions = fixture()
                    master(live)
                    row = (missions["101"] if table == "catalog" else live["current_missions"]["101"]
                           if table == "live" else live["mission_completions"]["101"])
                    row["campaign_id"] = parent
                    with self.assertRaises(AdventureScanError):
                        scan_adventure_islands(live, campaigns, missions)

    def test_independent_ready_track_can_advance_while_another_track_is_locked(self):
        live, campaigns, missions = fixture()
        master(live)
        add_island(live, campaigns, missions, 2, [201], prerequisite=1)
        add_island(live, campaigns, missions, 90, [901, 902], track="seasonal", active=True, exposed=True)
        result = scan_adventure_islands(live, campaigns, missions)
        self.assertFalse(result["all_complete"])
        self.assertEqual(result["next_mission"]["mission"], 901)
        self.assertEqual(result["next_mission"]["stage"], 1)

    def test_open_limited_track_missing_public_map_prevents_all_complete(self):
        live, campaigns, missions = fixture()
        master(live)
        live["limited_adventure_data"] = {
            "limited_adventure_track": "unmapped", "limited_adventure_end_time": 1001}
        with self.assertRaises(AdventureScanError):
            scan_adventure_islands(live, campaigns, missions)

    def test_open_unlocked_limited_track_with_missing_dynamic_island_cannot_fallback(self):
        live, campaigns, missions = fixture()
        master(live)
        add_island(live, campaigns, missions, 90, [901], track="seasonal")
        live["limited_adventure_data"] = {
            "limited_adventure_track": "seasonal", "limited_adventure_end_time": 1001}
        live["limited_adventure_island_unlock"] = True
        try:
            result = scan_adventure_islands(live, campaigns, missions)
        except AdventureScanError:
            return  # Missing availability may fail closed rather than create a route.
        self.assertFalse(result["all_complete"])
        self.assertIsNone(result["next_mission"])
        self.assertEqual(result["islands"][-1]["state"], "Locked")

    def test_invalid_current_window_or_limited_expiry_fails_closed(self):
        for location in ("current", "limited"):
            live, campaigns, missions = fixture()
            if location == "current":
                live["current_campaigns"]["1"]["end_time"] = True
            else:
                live["limited_adventure_data"] = {
                    "limited_adventure_track": "story", "limited_adventure_end_time": "tomorrow"}
            with self.assertRaises(AdventureScanError):
                scan_adventure_islands(live, campaigns, missions)

    def test_unknown_account_campaigns_warn_without_fabricating_missions(self):
        live, campaigns, missions = fixture()
        live["current_campaigns"]["999"] = {"id": 999, "completed": 0}
        result = scan_adventure_islands(live, campaigns, missions)
        self.assertEqual(result["total_stages"], 3)
        self.assertEqual(len(result["warnings"]), 1)

    def test_planning_does_not_mutate_live_or_public_catalogs(self):
        live, campaigns, missions = fixture()
        before = deepcopy((live, campaigns, missions))
        scan_adventure_islands(live, campaigns, missions)
        self.assertEqual((live, campaigns, missions), before)


if __name__ == "__main__":
    unittest.main()
