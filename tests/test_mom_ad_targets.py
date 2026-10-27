from copy import deepcopy
import unittest

from atcommander.local_commands import CommandUnavailable
from atcommander.mom_ad_targets import freeze_mom_ad_target, select_mom_ad_target, mom_ad_target_unchanged, verify_mom_ad_target


def snapshot():
    return {"result": True, "time": 1000, "slot_machine_data": {"pulls": 1, "cost": 100},
            "user_data": {"free_hard_currency": 500},
            "common_fields": {"free_hard_currency_balance": 500, "hard_currency_balance": 700}}


def store():
    return {"result": True, "store_items": [{"id": 71, "name": "Mystery Box", "num_available": 1}]}


class MomAdTargetTests(unittest.TestCase):
    def test_later_target_freezes_all_gem_mirrors_and_no_private_fields(self):
        live = snapshot()
        live.update(password="synthetic-private-value")
        original = deepcopy(live)
        target = select_mom_ad_target(live, store())
        self.assertEqual(target["before_pulls"], 1)
        self.assertEqual(target["before_cost"], 100)
        self.assertEqual(target["gem_balances"], {"user_data.free_hard_currency": 500,
                                                 "common_fields.free_hard_currency_balance": 500,
                                                 "common_fields.hard_currency_balance": 700})
        self.assertNotIn("synthetic-private-value", str(target))
        self.assertEqual(live, original)

    def test_first_free_is_never_selected_for_later_ad_flow(self):
        for cost in (0, 100):
            live = snapshot()
            live["slot_machine_data"].update(pulls=0, cost=cost)
            with self.subTest(cost=cost), self.assertRaises(CommandUnavailable):
                select_mom_ad_target(live, store())

    def test_later_zero_cost_offer_still_requires_real_ui_playback(self):
        live = snapshot()
        live["slot_machine_data"]["cost"] = 0
        self.assertEqual(select_mom_ad_target(live, store())["kind"], "mom_ad")

    def test_pending_choice_blocks_before_another_pull(self):
        live = snapshot()
        live["active_choices"] = {"1": {"options": []}}
        with self.assertRaises(CommandUnavailable):
            select_mom_ad_target(live, store())

    def test_missing_malformed_or_rejected_account_cannot_select(self):
        for live in (None, {}, {**snapshot(), "result": False},
                     {**snapshot(), "time": True}, {**snapshot(), "slot_machine_data": {}},
                     {**snapshot(), "slot_machine_data": {"pulls": True, "cost": 100}},
                     {**snapshot(), "slot_machine_data": {"pulls": 1, "cost": -1}},
                     {**snapshot(), "user_data": {}, "common_fields": {}}):
            with self.subTest(live=live), self.assertRaises(CommandUnavailable):
                select_mom_ad_target(live, store())

    def test_current_exact_store_identity_and_availability_are_required(self):
        for catalog in (None, {}, {**store(), "result": False},
                        {"store_items": [{"id": 70, "name": "Mystery Box", "num_available": 1}]},
                        {"store_items": [{"id": 71, "name": "Other offer", "num_available": 1}]},
                        {"store_items": [{"id": 71, "name": "Mystery Box"}]},
                        {"store_items": [{"id": 71, "name": "Mystery Box", "num_available": 0}]},
                        {"store_items": store()["store_items"] * 2},
                        {"store_items": [None]}):
            with self.subTest(catalog=catalog), self.assertRaises(CommandUnavailable):
                select_mom_ad_target(snapshot(), catalog)

    def test_known_string_metadata_and_zero_gem_balance_are_supported(self):
        live = snapshot()
        live.update(time="1000", slot_machine_data={"pulls": "1", "cost": "100"},
                    user_data={"free_hard_currency": "0"}, common_fields={})
        catalog = {"store_items": {"71": {"name": "Mystery Box", "num_available": "1"}}}
        self.assertEqual(select_mom_ad_target(live, catalog)["gem_balances"], {"user_data.free_hard_currency": 0})

    def test_unicode_digit_metadata_fails_with_generic_unavailable(self):
        for value in ("²", "١", "１"):
            live = snapshot()
            live["slot_machine_data"]["pulls"] = value
            with self.subTest(value=value), self.assertRaises(CommandUnavailable):
                select_mom_ad_target(live, store())
        target = select_mom_ad_target(snapshot(), store())
        self.assertFalse(verify_mom_ad_target({**target, "before_pulls": "²"}, snapshot()))

    def test_omitted_availability_needs_verified_native_watch_after_navigation(self):
        catalog = store()
        catalog["store_items"][0].pop("num_available")
        target = freeze_mom_ad_target(snapshot(), catalog)
        self.assertTrue(target["requires_native_watch"])
        with self.assertRaises(CommandUnavailable):
            select_mom_ad_target(snapshot(), catalog)
        self.assertFalse(mom_ad_target_unchanged(target, snapshot(), catalog))
        self.assertTrue(mom_ad_target_unchanged(target, snapshot(), catalog, native_watch_available=True))
        catalog["store_items"][0]["num_available"] = 0
        with self.assertRaises(CommandUnavailable):
            select_mom_ad_target(snapshot(), catalog, native_watch_available=True)

    def test_reset_time_changes_never_confirm_the_frozen_daily_offer(self):
        live = snapshot()
        live["slot_machine_data"]["reset_time"] = 5000
        target = select_mom_ad_target(live, store())
        after = deepcopy(live)
        after["slot_machine_data"].update(pulls=2, reset_time=6000)
        self.assertFalse(verify_mom_ad_target(target, after))
        self.assertFalse(mom_ad_target_unchanged(target, after, store()))
        live["slot_machine_data"]["reset_time"] = 1000
        with self.assertRaises(CommandUnavailable):
            freeze_mom_ad_target(live, store())

    def test_authoritative_ad_available_one_confirms_offer_and_zero_rejects_even_watch(self):
        live = snapshot()
        live["slot_machine_data"].update(ad_pull_available=1, reset_time=5000)
        catalog = store()
        catalog["store_items"][0].pop("num_available")
        target = select_mom_ad_target(live, catalog)
        self.assertEqual(target["ad_pull_available"], 1)
        after = deepcopy(live)
        after["slot_machine_data"].update(pulls=2, ad_pull_available=0)
        self.assertTrue(verify_mom_ad_target(target, after))
        self.assertFalse(mom_ad_target_unchanged(target, after, catalog, native_watch_available=True))
        live["slot_machine_data"]["ad_pull_available"] = 2
        self.assertEqual(select_mom_ad_target(live, catalog)["ad_pull_available"], 2)
        for unavailable in (0, 3, True, "bad"):
            live["slot_machine_data"]["ad_pull_available"] = unavailable
            with self.subTest(unavailable=unavailable), self.assertRaises(CommandUnavailable):
                select_mom_ad_target(live, catalog, native_watch_available=True)

    def test_current_offer_is_rechecked_without_accepting_changed_counters_or_gems(self):
        original = snapshot()
        target = select_mom_ad_target(original, store())
        unchanged = deepcopy(original)
        unchanged["time"] = 1010
        self.assertTrue(mom_ad_target_unchanged(target, unchanged, store()))
        for changed in ({"pulls": 2}, {"cost": 200}):
            live = deepcopy(unchanged)
            live["slot_machine_data"].update(changed)
            self.assertFalse(mom_ad_target_unchanged(target, live, store()))
        unchanged["user_data"]["free_hard_currency"] -= 1
        self.assertFalse(mom_ad_target_unchanged(target, unchanged, store()))
        self.assertFalse(mom_ad_target_unchanged({**target, "server_time": "bad"}, original, store()))

    def test_exactly_one_pull_and_all_unchanged_gems_confirm(self):
        target = select_mom_ad_target(snapshot(), store())
        after = snapshot()
        after["time"] = 1020
        after["slot_machine_data"].update(pulls=2, cost=200)
        self.assertTrue(verify_mom_ad_target(target, after))

    def test_clock_passage_without_pull_and_multiple_or_reset_pulls_fail(self):
        target = select_mom_ad_target(snapshot(), store())
        for pulls in (0, 1, 3, True):
            after = snapshot()
            after["time"] = 2000
            after["slot_machine_data"]["pulls"] = pulls
            with self.subTest(pulls=pulls):
                self.assertFalse(verify_mom_ad_target(target, after))

    def test_any_gem_balance_change_prevents_zero_spend_confirmation(self):
        target = select_mom_ad_target(snapshot(), store())
        for container, key in (("user_data", "free_hard_currency"),
                               ("common_fields", "free_hard_currency_balance"),
                               ("common_fields", "hard_currency_balance")):
            for delta in (-100, 100):
                after = snapshot()
                after["slot_machine_data"]["pulls"] = 2
                after[container][key] += delta
                with self.subTest(container=container, key=key, delta=delta):
                    self.assertFalse(verify_mom_ad_target(target, after))

    def test_changed_or_missing_gem_mirror_and_stale_time_fail(self):
        target = select_mom_ad_target(snapshot(), store())
        after = snapshot()
        after["slot_machine_data"]["pulls"] = 2
        after["common_fields"].pop("hard_currency_balance")
        self.assertFalse(verify_mom_ad_target(target, after))
        after = snapshot()
        after["slot_machine_data"]["pulls"] = 2
        after["time"] = 999
        self.assertFalse(verify_mom_ad_target(target, after))

    def test_pending_choice_after_real_pull_does_not_erase_verified_credit(self):
        target = select_mom_ad_target(snapshot(), store())
        after = snapshot()
        after["slot_machine_data"]["pulls"] = 2
        after["active_choices"] = {"1": {"options": []}}
        self.assertTrue(verify_mom_ad_target(target, after))
        with self.assertRaises(CommandUnavailable):
            select_mom_ad_target(after, store())

    def test_invalid_target_and_rejected_after_state_fail_safely(self):
        target = select_mom_ad_target(snapshot(), store())
        after = snapshot()
        after["slot_machine_data"]["pulls"] = 2
        for invalid in (None, {}, {**target, "kind": "first_free"}, {**target, "store_id": 70},
                        {**target, "before_pulls": 0}, {**target, "gem_balances": {"secret": 1}}):
            with self.subTest(invalid=invalid):
                self.assertFalse(verify_mom_ad_target(invalid, after))
        self.assertFalse(verify_mom_ad_target(target, {**after, "result": False}))


if __name__ == "__main__":
    unittest.main()
