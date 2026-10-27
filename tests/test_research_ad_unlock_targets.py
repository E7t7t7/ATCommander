from copy import deepcopy
import unittest

from atcommander.local_commands import CommandUnavailable
from atcommander.research_ad_targets import (
    research_unlock_target_unchanged, select_research_unlock_target, verify_research_unlock_target,
)
from tests.test_research_ad_targets import task


def context(**changes):
    return {"phase": "unlock", "slot_index": 3, "pair": "22~23", "card1": 22, "card2": 23, **changes}


def snapshot():
    return {"result": True, "time": 1000, "user_tasks": {}, "user_combos_compressed": {},
            "user_units": {"5": {"unit_id": 22, "unit_index": 5}, "6": {"unit_id": 23, "unit_index": 6}},
            "research_times_slot": [{"slot_index": 3, "research_times": {"4": 7200}}],
            "research_ad_speed_ups": [{"slot_index": 3, "views": 0, "max_views": 3, "next_speed_up": 0}],
            "user_data": {"free_hard_currency": 500},
            "common_fields": {"free_hard_currency_balance": 500, "hard_currency_balance": 700}}


def started():
    live = snapshot()
    live["time"] = 1100
    live["user_tasks"] = {"201": task(end=8300)}
    live["research_ad_speed_ups"][0].update(views=1, next_speed_up=2700)
    return live


class ResearchUnlockSelectionTests(unittest.TestCase):
    def test_exact_pair_freezes_current_public_metadata_without_instances_or_secrets(self):
        live = snapshot()
        live.update(password="synthetic-private-detail")
        original = deepcopy(live)
        target = select_research_unlock_target(live, context(card1_name="Synthetic name", combo_id=90))
        self.assertEqual(target["phase"], "unlock")
        self.assertEqual(target["pair"], "22~23")
        self.assertEqual(target["owned_card_counts"], {"22": 1, "23": 1})
        self.assertEqual(target["gem_balances"], {"user_data.free_hard_currency": 500,
                                                   "common_fields.free_hard_currency_balance": 500,
                                                   "common_fields.hard_currency_balance": 700})
        self.assertEqual(target["slot_metadata"], {"slot_index": 3, "research_times": {"4": 7200}})
        for private in ("synthetic-private-detail", "Synthetic name", "unit_index", "combo_id"):
            self.assertNotIn(private, str(target))
        self.assertEqual(live, original)

    def test_embedded_context_is_explicit_and_missing_context_never_chooses_a_pair(self):
        live = snapshot()
        live["_commander_research"] = context()
        self.assertEqual(select_research_unlock_target(live)["card1"], 22)
        for value in (None, {}, context(phase="speedup"), context(phase="other"), "synthetic-private-detail"):
            with self.subTest(value=value), self.assertRaises(CommandUnavailable) as error:
                select_research_unlock_target(snapshot(), value)
            self.assertNotIn("synthetic-private-detail", str(error.exception))

    def test_exact_card_id_pair_slot_and_context_phase_are_required(self):
        for changes in ({"slot_index": 2}, {"slot_index": True}, {"card1": True}, {"card2": 0},
                        {"card1": 22, "card2": 22, "pair": "22~22"}, {"pair": "22~24"},
                        {"task_index": 201}, {"before_end": 5000}):
            with self.subTest(changes=changes), self.assertRaises(CommandUnavailable):
                select_research_unlock_target(snapshot(), context(**changes))
        for key in ("phase", "slot_index", "pair", "card1", "card2"):
            frozen = context()
            frozen.pop(key)
            with self.subTest(key=key), self.assertRaises(CommandUnavailable):
                select_research_unlock_target(snapshot(), frozen)

    def test_reverse_canonical_pair_and_known_string_ids_are_supported(self):
        live = snapshot()
        live["user_units"] = [{"card_id": "22"}, {"unit_id": "23", "card_id": 23}]
        selected = select_research_unlock_target(live, context(card1="23", card2="22", pair="23~22"))
        self.assertEqual((selected["card1"], selected["card2"], selected["pair"]), (23, 22, "22~23"))

    def test_missing_or_malformed_ownership_never_invents_owned_cards(self):
        for owned in (None, {}, [], {"5": 22}, [{"unit_id": 22}], [{"unit_id": True}, {"unit_id": 23}],
                      [{"unit_id": 22, "card_id": 24}, {"unit_id": 23}],
                      [{"unit_id": 22}, {"unit_id": 23}, None]):
            live = snapshot()
            live["user_units"] = owned
            with self.subTest(owned=owned), self.assertRaises(CommandUnavailable):
                select_research_unlock_target(live, context())

    def test_any_gem_mirror_must_be_valid_and_at_least_one_must_exist(self):
        for changes in ({"user_data": {}, "common_fields": {}},
                        {"user_data": {"free_hard_currency": True}},
                        {"common_fields": {"free_hard_currency_balance": -1}}):
            with self.subTest(changes=changes), self.assertRaises(CommandUnavailable):
                select_research_unlock_target({**snapshot(), **changes}, context())
        live = snapshot()
        live.update(user_data={"free_hard_currency": "0"}, common_fields={})
        self.assertEqual(select_research_unlock_target(live, context())["gem_balances"],
                         {"user_data.free_hard_currency": 0})

    def test_exact_current_tv_slot_and_valid_duration_metadata_are_required(self):
        for slots in (None, [], [{"slot_index": 2, "research_times": {"4": 7200}}],
                      [{"slot_index": 3, "research_times": {}}],
                      [{"slot_index": 3, "research_times": {"4": 0}}],
                      [{"slot_index": 3, "research_times": {"4": True}}],
                      [{"slot_index": 3, "research_times": {"4": 7200, "04": 7200}}],
                      snapshot()["research_times_slot"] * 2,
                      [*snapshot()["research_times_slot"], None]):
            live = snapshot()
            live["research_times_slot"] = slots
            with self.subTest(slots=slots), self.assertRaises(CommandUnavailable):
                select_research_unlock_target(live, context())

    def test_explicit_gem_locked_or_nonad_slot_never_selects(self):
        for flag, value in (("locked", True), ("unlocked", False), ("ad_required", False), ("locked", "true")):
            live = snapshot()
            live["research_times_slot"][0][flag] = value
            with self.subTest(flag=flag, value=value), self.assertRaises(CommandUnavailable):
                select_research_unlock_target(live, context())
        live = snapshot()
        live["research_times_slot"][0].update(locked=False, unlocked=True, ad_required=True)
        self.assertEqual(select_research_unlock_target(live, context())["slot_metadata"]["ad_required"], True)

    def test_native_zero_common_time_and_other_positive_tier_times_are_supported(self):
        live = snapshot()
        times = {"1": "0", "2": "60", "3": "300", "4": "3600", "5": "10800"}
        live["research_times_slot"] = [{"slot_index": slot, "research_times": dict(times)} for slot in (1, 2, 3)]
        target = select_research_unlock_target(live, context())
        self.assertEqual(target["slot_metadata"]["research_times"],
                         {"1": 0, "2": 60, "3": 300, "4": 3600, "5": 10800})
        self.assertTrue(research_unlock_target_unchanged(target, live))
        after = started()
        after["research_times_slot"] = deepcopy(live["research_times_slot"])
        self.assertTrue(verify_research_unlock_target(target, after))

    def test_exact_public_mythic_ids_and_expired_uncollected_regular_tasks_are_supported(self):
        live = snapshot()
        first, second = 1400003, 1400021
        live["user_units"] = [{"unit_id": first}, {"unit_id": second}]
        live["user_tasks"] = {"199": task(slot=1, index=199, card1=24, card2=25, end=900),
                              "200": task(slot=2, index=200, card1=26, card2=27, end=900)}
        selected = select_research_unlock_target(live, context(card1=first, card2=second, pair=f"{first}~{second}"))
        self.assertEqual(selected["owned_card_counts"], {str(first): 1, str(second): 1})
        self.assertTrue(research_unlock_target_unchanged(selected, live))
        after = deepcopy(live)
        after["time"] = 1100
        after["user_tasks"]["201"] = task(card1=first, card2=second, end=11900)
        after["research_ad_speed_ups"][0].update(views=1, next_speed_up=2700)
        self.assertTrue(verify_research_unlock_target(selected, after))

    def test_empty_ad_row_requires_observed_zero_views_max_three_and_zero_speedup(self):
        for changes in ({"slot_index": 2}, {"views": 1}, {"views": True}, {"max_views": 4},
                        {"max_views": 0}, {"next_speed_up": 2700}):
            live = snapshot()
            live["research_ad_speed_ups"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(CommandUnavailable):
                select_research_unlock_target(live, context())
        for ads in ([], [None], snapshot()["research_ad_speed_ups"] * 2):
            live = snapshot()
            live["research_ad_speed_ups"] = ads
            with self.subTest(ads=ads), self.assertRaises(CommandUnavailable):
                select_research_unlock_target(live, context())

    def test_known_pair_or_active_pair_anywhere_and_any_tv_task_block_unlock(self):
        for added in (task(), task(card1=24, card2=25), task(slot=1),
                      {**task(card1=24, card2=25), "task_type": 2}):
            live = snapshot()
            live["user_tasks"] = {"201": added}
            with self.subTest(added=added), self.assertRaises(CommandUnavailable):
                select_research_unlock_target(live, context())
        live = snapshot()
        live["user_combos_compressed"]["23~22"] = 1
        with self.assertRaises(CommandUnavailable):
            select_research_unlock_target(live, context())

    def test_unrelated_research_is_frozen_without_copying_raw_task_fields(self):
        live = snapshot()
        live["user_tasks"] = {"199": {**task(slot=1, index=199, card1=24, card2=25),
                                       "private_field": "synthetic-private-detail"}}
        selected = select_research_unlock_target(live, context())
        self.assertEqual(selected["existing_tasks"], [{"slot_index": 1, "task_index": 199,
                                                       "pair": "24~25", "end_time": 4000}])
        self.assertNotIn("synthetic-private-detail", str(selected))

    def test_before_watch_recheck_accepts_clock_progress_and_rejects_changed_frozen_fields(self):
        selected = select_research_unlock_target(snapshot(), context())
        fresh = snapshot()
        fresh["time"] = 1010
        self.assertTrue(research_unlock_target_unchanged(selected, fresh))
        for changes in ({"user_data": {"free_hard_currency": 499}},
                        {"user_units": {"5": {"unit_id": 22}}},
                        {"user_tasks": {"199": task(slot=1, index=199, card1=24, card2=25)}},
                        {"time": 999}, {"research_times_slot": [{"slot_index": 3, "research_times": {"4": 7100}}]}):
            with self.subTest(changes=changes):
                self.assertFalse(research_unlock_target_unchanged(selected, {**fresh, **changes}))
        for key in ("gem_balances", "owned_card_counts", "slot_metadata", "existing_tasks", "view_credit", "server_time",
                    "views", "max_views", "next_speed_up"):
            invalid = dict(selected)
            invalid.pop(key)
            with self.subTest(key=key):
                self.assertFalse(research_unlock_target_unchanged(invalid, fresh))

    def test_explicit_stale_context_counters_cannot_select(self):
        for changes in ({"views": 1}, {"max_views": 4}, {"next_speed_up": 2700},
                        {"server_time": 1001}, {"gem_balances": {}}, {"owned_card_counts": {}},
                        {"slot_metadata": {}}, {"existing_tasks": [task()]}, {"view_credit": "reset"}):
            with self.subTest(changes=changes), self.assertRaises(CommandUnavailable):
                select_research_unlock_target(snapshot(), context(**changes))

    def test_missing_malformed_and_rejected_snapshots_fail_with_safe_message(self):
        for live in (None, {}, {**snapshot(), "result": False}, {**snapshot(), "time": True},
                     {**snapshot(), "user_tasks": [None]}, {**snapshot(), "user_combos_compressed": None}):
            with self.subTest(live=live), self.assertRaises(CommandUnavailable) as error:
                select_research_unlock_target(live, context())
            self.assertEqual(str(error.exception), "An exact owned research pair and empty ad chamber could not be verified.")


class ResearchUnlockVerificationTests(unittest.TestCase):
    def setUp(self):
        self.target = select_research_unlock_target(snapshot(), context())
        self.after = started()

    def test_exact_new_future_tv_task_view_credit_and_unchanged_gems_confirm(self):
        original = deepcopy(self.after)
        self.assertTrue(verify_research_unlock_target(self.target, self.after))
        self.assertEqual(self.after, original)

    def test_clock_or_unrelated_view_credit_without_exact_task_never_confirms(self):
        for tasks in ({}, {"201": task(card2=24)}, {"201": task(slot=1)}):
            after = deepcopy(self.after)
            after["user_tasks"] = tasks
            with self.subTest(tasks=tasks):
                self.assertFalse(verify_research_unlock_target(self.target, after))

    def test_no_or_multiple_views_and_changed_maximum_never_confirm(self):
        for changes in ({"views": 0}, {"views": 2}, {"views": True}, {"max_views": 4}, {"slot_index": 2}):
            after = deepcopy(self.after)
            after["research_ad_speed_ups"][0].update(changes)
            with self.subTest(changes=changes):
                self.assertFalse(verify_research_unlock_target(self.target, after))

    def test_finished_task_invalid_identity_type_or_regressed_clock_fail(self):
        for changes in ({"end_time": 1100}, {"end_time": 1099}, {"task_index": -1},
                        {"task_index": True}, {"task_type": 2}):
            after = deepcopy(self.after)
            after["user_tasks"]["201"].update(changes)
            with self.subTest(changes=changes):
                self.assertFalse(verify_research_unlock_target(self.target, after))
        self.after["time"] = 999
        self.assertFalse(verify_research_unlock_target(self.target, self.after))

    def test_any_gem_mirror_changed_added_or_removed_prevents_zero_spend_confirmation(self):
        for container, key in (("user_data", "free_hard_currency"),
                               ("common_fields", "free_hard_currency_balance"),
                               ("common_fields", "hard_currency_balance")):
            for delta in (-1, 1):
                after = deepcopy(self.after)
                after[container][key] += delta
                with self.subTest(container=container, key=key, delta=delta):
                    self.assertFalse(verify_research_unlock_target(self.target, after))
            after = deepcopy(self.after)
            after[container].pop(key)
            self.assertFalse(verify_research_unlock_target(self.target, after))
        before = snapshot()
        before["common_fields"] = {}
        target = select_research_unlock_target(before, context())
        self.assertFalse(verify_research_unlock_target(target, self.after))

    def test_consumed_or_removed_selected_owned_card_never_confirms(self):
        self.after["user_units"].pop("6")
        self.assertFalse(verify_research_unlock_target(self.target, self.after))

    def test_target_pair_in_multiple_tasks_or_already_known_pair_conflicts(self):
        self.after["user_tasks"]["202"] = task(slot=1, index=202)
        self.assertFalse(verify_research_unlock_target(self.target, self.after))
        self.after["user_tasks"].pop("202")
        self.after["user_combos_compressed"]["23~22"] = 1
        self.assertFalse(verify_research_unlock_target(self.target, self.after))

    def test_new_task_may_not_reuse_any_frozen_existing_task_index(self):
        before = snapshot()
        before["user_tasks"] = {"201": task(slot=1, card1=24, card2=25)}
        target = select_research_unlock_target(before, context())
        self.assertFalse(verify_research_unlock_target(target, self.after))
        self.after["user_tasks"] = {"202": task(index=202, end=8300)}
        self.assertTrue(verify_research_unlock_target(target, self.after))

    def test_unrelated_task_does_not_prevent_exact_new_unlock_confirmation(self):
        self.after["user_tasks"]["200"] = task(slot=1, index=200, card1=24, card2=25)
        self.assertTrue(verify_research_unlock_target(self.target, self.after))

    def test_conflicting_raw_task_type_cannot_disappear_from_tv_occupancy(self):
        self.after["user_tasks"]["202"] = {**task(index=202, card1=24, card2=25), "task_type": 2}
        self.assertFalse(verify_research_unlock_target(self.target, self.after))

    def test_changed_slot_metadata_duplicate_rows_and_rejected_fresh_state_fail(self):
        after = deepcopy(self.after)
        after["research_times_slot"][0]["research_times"]["4"] = 7100
        self.assertFalse(verify_research_unlock_target(self.target, after))
        after = deepcopy(self.after)
        after["research_ad_speed_ups"] *= 2
        self.assertFalse(verify_research_unlock_target(self.target, after))
        for after in (None, {}, {**self.after, "result": False}, {**self.after, "user_units": None}):
            with self.subTest(after=after):
                self.assertFalse(verify_research_unlock_target(self.target, after))

    def test_invalid_frozen_metadata_fails_without_throwing_or_exposing_secrets(self):
        for target in (None, {}, {**self.target, "phase": "speedup"},
                       {**self.target, "pair": "synthetic-private-detail"},
                       {**self.target, "gem_balances": {"private": 1}},
                       {**self.target, "gem_balances": {"user_data.free_hard_currency": True}},
                       {**self.target, "owned_card_counts": {"22": 1}},
                       {**self.target, "slot_metadata": {**self.target["slot_metadata"], "locked": True}},
                       {**self.target, "existing_tasks": [None]},
                       {**self.target, "existing_tasks": [{"slot_index": 3, "task_index": 201, "pair": "24~25", "end_time": 3000}]},
                       {**self.target, "server_time": 0}, {**self.target, "view_credit": "reset"}):
            with self.subTest(target=target):
                self.assertFalse(verify_research_unlock_target(target, self.after))


if __name__ == "__main__":
    unittest.main()
