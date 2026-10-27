from copy import deepcopy
import unittest

from atcommander.local_commands import CommandUnavailable
from atcommander.research_ad_targets import select_research_ad_target, verify_research_ad_target


def task(slot=3, index=201, card1=22, card2=23, end=4000):
    return {"task_type": 1, "slot_index": slot, "task_index": index,
            "task_id_1": card1, "task_id_2": card2, "end_time": end}


def snapshot():
    return {"result": True, "time": 1000, "user_tasks": {"201": task()},
            "user_combos_compressed": {},
            "research_ad_speed_ups": [{"slot_index": 3, "views": 0,
                                        "max_views": 3, "next_speed_up": 2700}]}


def context(**changes):
    return {"phase": "speedup", "slot_index": 3, "pair": "22~23",
            "task_index": 201, "before_end": 4000, **changes}


class ResearchAdSelectionTests(unittest.TestCase):
    def test_manual_selection_freezes_exact_public_fields_only(self):
        live = snapshot()
        live.update(password="synthetic-private-value", user_data={"user_id": "synthetic-identity"})
        original = deepcopy(live)
        selected = select_research_ad_target(live)
        self.assertEqual(selected, {"phase": "speedup", "slot_index": 3, "pair": "22~23",
                                    "card1": 22, "card2": 23, "task_index": 201,
                                    "before_end": 4000, "views": 0, "max_views": 3,
                                    "next_speed_up": 2700, "server_time": 1000})
        self.assertNotIn("synthetic-private-value", str(selected))
        self.assertNotIn("synthetic-identity", str(selected))
        self.assertEqual(live, original)

    def test_frozen_engine_context_selects_exact_current_task(self):
        live = snapshot()
        live["_commander_research"] = context(card1=22, card2=23, combo_id="90",
                                               next_speed_up=2700, card1_name="Synthetic card")
        selected = select_research_ad_target(live)
        self.assertEqual(selected["task_index"], 201)
        self.assertNotIn("combo_id", selected)
        self.assertNotIn("card1_name", selected)

    def test_multiple_manual_targets_are_ambiguous_but_frozen_context_is_exact(self):
        live = snapshot()
        live["user_tasks"]["202"] = task(slot=4, index=202, card1=24, card2=25)
        live["research_ad_speed_ups"].append({"slot_index": 4, "views": 0, "max_views": 3, "next_speed_up": 300})
        with self.assertRaises(CommandUnavailable):
            select_research_ad_target(live)
        self.assertEqual(select_research_ad_target(live, context())["slot_index"], 3)

    def test_stale_or_wrong_frozen_identity_never_selects(self):
        for changes in ({"slot_index": 4}, {"pair": "22~24"}, {"task_index": 202},
                        {"before_end": 3999}, {"next_speed_up": 300}, {"views": 1},
                        {"max_views": 4}, {"card1": 22}, {"card1": 22, "card2": 24}):
            with self.subTest(changes=changes), self.assertRaises(CommandUnavailable):
                select_research_ad_target(snapshot(), context(**changes))

    def test_missing_frozen_identity_cannot_fall_back_to_manual_selection(self):
        for field in ("slot_index", "pair", "task_index", "before_end", "phase"):
            frozen = context()
            frozen.pop(field)
            with self.subTest(field=field), self.assertRaises(CommandUnavailable):
                select_research_ad_target(snapshot(), frozen)

    def test_unlock_and_unknown_contexts_are_explicitly_unsupported(self):
        for frozen in (context(phase="unlock"), context(phase="other"), {}, "synthetic-private-value"):
            with self.subTest(frozen=frozen), self.assertRaises(CommandUnavailable) as error:
                select_research_ad_target(snapshot(), frozen)
            self.assertNotIn("synthetic-private-value", str(error.exception))
        live = snapshot()
        live["_commander_research"] = None
        with self.assertRaises(CommandUnavailable):
            select_research_ad_target(live)

    def test_missing_or_invalid_server_time_never_uses_local_clock(self):
        for value in (None, 0, -1, True, 1000.1, "bad"):
            live = snapshot()
            live["time"] = value
            with self.subTest(value=value), self.assertRaises(CommandUnavailable):
                select_research_ad_target(live)

    def test_finished_exhausted_unoffered_and_already_known_tasks_are_ineligible(self):
        for changes in ({"end_time": 1000}, {"end_time": 999}):
            live = snapshot()
            live["user_tasks"]["201"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(CommandUnavailable):
                select_research_ad_target(live)
        for changes in ({"views": 3}, {"next_speed_up": 0}):
            live = snapshot()
            live["research_ad_speed_ups"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(CommandUnavailable):
                select_research_ad_target(live)
        live = snapshot()
        live["user_combos_compressed"]["22~23"] = 1
        with self.assertRaises(CommandUnavailable):
            select_research_ad_target(live)

    def test_invalid_view_counters_and_duplicate_ad_slots_fail_safely(self):
        for changes in ({"views": -1}, {"views": True}, {"views": 4}, {"max_views": 0},
                        {"max_views": "bad"}, {"next_speed_up": -1}, {"slot_index": True}):
            live = snapshot()
            live["research_ad_speed_ups"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(CommandUnavailable):
                select_research_ad_target(live)
        live = snapshot()
        live["research_ad_speed_ups"].append(deepcopy(live["research_ad_speed_ups"][0]))
        with self.assertRaises(CommandUnavailable):
            select_research_ad_target(live)

    def test_running_research_task_must_have_exact_positive_identity(self):
        for changes in ({"task_index": -1}, {"task_index": True}, {"task_type": None},
                        {"task_type": 2}, {"task_id_1": None}, {"slot_index": None}):
            live = snapshot()
            live["user_tasks"]["201"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(CommandUnavailable):
                select_research_ad_target(live)

    def test_duplicate_task_indices_or_input_pair_cannot_select(self):
        for added in (task(slot=4, index=201, card1=24, card2=25), task(slot=4, index=202)):
            live = snapshot()
            live["user_tasks"]["202"] = added
            with self.subTest(added=added), self.assertRaises(CommandUnavailable):
                select_research_ad_target(live)

    def test_missing_malformed_and_rejected_snapshots_fail_safely(self):
        for live in (None, {}, {**snapshot(), "result": False},
                     {**snapshot(), "user_tasks": [task(), None]},
                     {**snapshot(), "user_combos_compressed": None},
                     {**snapshot(), "research_ad_speed_ups": [None]},
                     {**snapshot(), "user_tasks": {}}):
            with self.subTest(live=live), self.assertRaises(CommandUnavailable):
                select_research_ad_target(live)

    def test_string_metadata_and_slot_zero_are_supported_without_guessing(self):
        live = snapshot()
        live["time"] = "1000"
        live["user_tasks"] = [{key: str(value) for key, value in task(slot=0).items()}]
        live["research_ad_speed_ups"] = {"row": {"slot_index": "0", "views": "0", "max_views": "3", "next_speed_up": "2700"}}
        self.assertEqual(select_research_ad_target(live)["slot_index"], 0)


class ResearchAdVerificationTests(unittest.TestCase):
    def setUp(self):
        self.before = snapshot()
        self.target = select_research_ad_target(self.before)
        self.after = deepcopy(self.before)
        self.after["time"] = 1100
        self.after["research_ad_speed_ups"][0]["views"] = 1
        self.after["user_tasks"]["201"]["end_time"] -= 2700

    def test_same_task_view_credit_and_absolute_timer_reduction_confirm(self):
        original = deepcopy(self.after)
        self.assertTrue(verify_research_ad_target(self.target, self.after))
        self.assertEqual(self.after, original)

    def test_elapsed_server_clock_and_view_credit_alone_do_not_confirm(self):
        self.after["time"] = 5000
        self.after["user_tasks"]["201"]["end_time"] = 4000
        self.assertFalse(verify_research_ad_target(self.target, self.after))

    def test_timer_reduction_without_exact_slot_credit_does_not_confirm(self):
        self.after["research_ad_speed_ups"][0]["views"] = 0
        self.assertFalse(verify_research_ad_target(self.target, self.after))
        self.after["research_ad_speed_ups"].append({"slot_index": 4, "views": 1, "max_views": 3, "next_speed_up": 300})
        self.assertFalse(verify_research_ad_target(self.target, self.after))

    def test_replacement_pair_or_task_index_never_confirms_even_if_old_pair_is_known(self):
        for changes in ({"task_index": 999}, {"task_id_2": 24}):
            after = deepcopy(self.after)
            after["user_tasks"]["201"].update(changes)
            for researched in (False, True):
                if researched:
                    after["user_combos_compressed"]["22~23"] = 1
                with self.subTest(changes=changes, researched=researched):
                    self.assertFalse(verify_research_ad_target(self.target, after))

    def test_completed_exact_pair_without_replacement_confirms_with_view_credit(self):
        self.after["user_tasks"] = {}
        self.after["user_combos_compressed"]["22~23"] = 1
        self.assertTrue(verify_research_ad_target(self.target, self.after))
        self.after["research_ad_speed_ups"][0]["views"] = 0
        self.assertFalse(verify_research_ad_target(self.target, self.after))

    def test_unrelated_researched_pair_or_missing_task_never_confirms(self):
        self.after["user_tasks"] = {}
        self.assertFalse(verify_research_ad_target(self.target, self.after))
        self.after["user_combos_compressed"]["22~24"] = 1
        self.assertFalse(verify_research_ad_target(self.target, self.after))

    def test_moved_task_or_other_task_for_target_pair_conflicts(self):
        for added in (task(slot=4, index=201, end=1200), task(slot=4, index=202, end=1200)):
            after = deepcopy(self.after)
            after["user_tasks"] = {"new": added}
            after["user_combos_compressed"]["22~23"] = 1
            with self.subTest(added=added):
                self.assertFalse(verify_research_ad_target(self.target, after))

    def test_changed_task_type_cannot_hide_a_replacement(self):
        self.after["user_tasks"]["201"]["task_type"] = 2
        self.after["user_combos_compressed"]["22~23"] = 1
        self.assertFalse(verify_research_ad_target(self.target, self.after))

    def test_unrelated_running_task_does_not_prevent_exact_pair_completion(self):
        self.after["user_tasks"] = {"202": task(slot=1, index=202, card1=24, card2=25)}
        self.after["user_combos_compressed"]["23~22"] = 1
        self.assertTrue(verify_research_ad_target(self.target, self.after))

    def test_regressed_clock_invalid_counters_and_duplicate_rows_fail(self):
        for changes in ({"views": True}, {"views": 4}, {"views": 0}, {"max_views": 4}):
            after = deepcopy(self.after)
            after["research_ad_speed_ups"][0].update(changes)
            with self.subTest(changes=changes):
                self.assertFalse(verify_research_ad_target(self.target, after))
        self.after["time"] = 999
        self.assertFalse(verify_research_ad_target(self.target, self.after))
        self.after["time"] = 1100
        self.after["research_ad_speed_ups"].append(deepcopy(self.after["research_ad_speed_ups"][0]))
        self.assertFalse(verify_research_ad_target(self.target, self.after))

    def test_exhausted_final_view_still_confirms_its_real_timer_benefit(self):
        before = snapshot()
        before["research_ad_speed_ups"][0]["views"] = 2
        target = select_research_ad_target(before)
        after = deepcopy(self.after)
        after["research_ad_speed_ups"][0].update(views=3, next_speed_up=0)
        self.assertTrue(verify_research_ad_target(target, after))

    def test_malformed_target_and_fresh_snapshot_fail_without_exposing_data(self):
        for target in (None, {}, {**self.target, "phase": "unlock"},
                       {**self.target, "pair": "synthetic-private-value"},
                       {**self.target, "task_index": -1}, {**self.target, "before_end": 1000}):
            with self.subTest(target=target):
                self.assertFalse(verify_research_ad_target(target, self.after))
        for after in (None, {}, {**self.after, "result": False},
                      {**self.after, "user_tasks": None}, {**self.after, "user_combos_compressed": None}):
            with self.subTest(after=after):
                self.assertFalse(verify_research_ad_target(self.target, after))


if __name__ == "__main__":
    unittest.main()
