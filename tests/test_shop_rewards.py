import json
import unittest
import queue
from unittest.mock import patch

from atcommander.shop_rewards import (
    PurchaseInterrupted, choose_item_reward, daily_upgrade_progress,
    extract_item_choices, item_choice_confirmed, run_single_purchases,
    safe_upgrade_candidates, upgrade_confirmed,
)
from atcommander.protocol_recorder import _parse_post, _sanitize


class ChoiceTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            "20146": {"name": "Rogu", "card_type": "character", "rarity": "4", "power": 5, "health": 20},
            "100": {"name": "Other Character", "card_type": "character", "rarity": "5", "power": 30, "health": 90},
            "200": {"name": "Strong Object", "card_type": "item", "rarity": "5", "power": 35, "health": 110},
        }
        self.response = {"active_choices": [{"type": 1, "options": [
            {"id": "1", "give_card": [{"id": "200", "rarity": "5"}]},
            {"id": "2", "give_card": [{"id": "20146", "rarity": "4"}]},
            {"id": "3", "give_card": [{"id": "100", "rarity": "5"}]},
        ]}]}

    def test_rogu_beats_higher_rarity_and_uses_option_id(self):
        options = extract_item_choices(self.response, self.catalog)
        self.assertEqual(len(options), 3)
        selected = choose_item_reward(options)
        self.assertEqual(selected["id"], "2")
        self.assertEqual(selected["choice_type"], "1")
        self.assertEqual(selected["cards"][0]["card_id"], "20146")

    def test_character_beats_item_when_rogu_absent(self):
        response = {"active_choices": [{"type": 1, "options": [self.response["active_choices"][0]["options"][i] for i in (0, 2)]}]}
        self.assertEqual(choose_item_reward(extract_item_choices(response, self.catalog))["id"], "3")

    def test_unknown_competitor_does_not_get_silently_skipped(self):
        catalog = dict(self.catalog); del catalog["100"]
        with self.assertRaisesRegex(ValueError, "unresolved"):
            choose_item_reward(extract_item_choices(self.response, catalog))

    def test_rejection_never_clears_pending_choice(self):
        self.assertFalse(item_choice_confirmed({"result": False, "active_choices": []}, 1))
        self.assertFalse(item_choice_confirmed({}, 1))
        self.assertFalse(item_choice_confirmed(self.response, 1))
        self.assertTrue(item_choice_confirmed({"active_choices": []}, 1))

    def test_live_power_card_shape_uses_power_then_health_and_real_selector(self):
        catalog = {
            "130283": {"name": "Walkie Talkie Teddy", "card_type": "final_form", "rarity": 4, "power": 94, "health": 58},
            "170097": {"name": "Chokehold Cheryl", "card_type": "final_form", "rarity": 4, "power": 94, "health": 64},
            "130166": {"name": "Ligament Lesson Bob", "card_type": "final_form", "rarity": 4, "power": 83, "health": 41},
        }
        response = {"active_choices": [{"type": 1, "options": [
            {"id": str(i), "give_card": [{"id": card_id}]} for i, card_id in enumerate(catalog, 1)
        ]}]}
        options = extract_item_choices(response, catalog)
        self.assertTrue(all(row["kind"] == "PowerCard" for row in options))
        self.assertEqual(choose_item_reward(options)["id"], "2")
        self.assertEqual(options[0]["value"], 94)
        catalog["20146"] = self.catalog["20146"]
        response["active_choices"][0]["options"].append({"id": "4", "give_card": [{"id": "20146"}]})
        self.assertEqual(choose_item_reward(extract_item_choices(response, catalog))["id"], "4")

    def test_explicit_power_card_aliases_are_supported_without_guessing_combos(self):
        aliases = [{"card_type": value} for value in ("pc", "power_card", "Power Card", "PowerCard", "final_form")]
        aliases += [{"set": "3001"}, {"set_id": "3002"}]
        for descriptor in aliases:
            with self.subTest(descriptor=descriptor):
                catalog = {"100": {"name": "PC", "rarity": 4, "power": 90, **descriptor}}
                response = {"active_choices": [{"type": 1, "options": [{"id": "1", "give_card": [{"id": "100"}]}]}]}
                options = extract_item_choices(response, catalog)
                self.assertEqual(options[0]["kind"], "PowerCard")
                self.assertEqual(choose_item_reward(options)["id"], "1")
        combo = {"100": {"name": "Combo Definition", "card_type": "combo"}}
        self.assertEqual(extract_item_choices(response, combo)[0]["kind"], "Unknown")


class DailyUpgradeTests(unittest.TestCase):
    def test_unknown_progress_never_defaults_to_three(self):
        self.assertIsNone(daily_upgrade_progress({}))
        self.assertIsNone(daily_upgrade_progress({"user_achievements": [{"name": "Lifetime Upgrade", "progress": 0, "max_progress": 100}]}))
        self.assertIsNone(daily_upgrade_progress({"user_achievements": [{"name": "Upgrade Cards", "max_progress": 3}]}))
        self.assertEqual(daily_upgrade_progress({"user_achievements": [{"progress": 2, "req": {"upgrade_cards_num": 3}}]}), (2, 3, 1))

    def test_completed_daily_needs_no_card(self):
        self.assertEqual(daily_upgrade_progress({"user_achievements": [{"progress": 3, "req": {"upgrade_cards_num": 3}}]}), (3, 3, 0))

    def test_other_daily_with_target_three_is_not_an_upgrade_quest(self):
        current = {"user_achievements": {
            "buy": {"name": "Daily - Buy Packs", "progress": 0, "max_progress": "3", "req": {"store_item": "1"}},
            "cm": {"name": "Daily - Combo Mastery", "progress": 3, "max_progress": "10", "req": {"total_pvp_battles_num": "10"}},
        }}
        self.assertIsNone(daily_upgrade_progress(current))

    def test_all_decks_foils_locked_and_high_rarity_protected(self):
        units = {str(i): {"unit_id": str(i), "level": 1, "foil_id": 0} for i in range(1, 7)}
        units["3"]["foil_id"] = 42; units["4"]["locked"] = 1
        catalog = {str(i): {"name": f"Card {i}", "rarity": i if i < 5 else 1} for i in range(1, 7)}
        decks = {"user_decks": {"a": {"units": [{"unit_index": "1"}]}, "b": {"units": ["5"]}}}
        selected = safe_upgrade_candidates({"user_units": units}, decks, catalog)
        self.assertEqual([x["unit_index"] for x in selected], ["6", "2"])
        with self.assertRaises(ValueError):
            safe_upgrade_candidates({"user_units": units}, {}, catalog)

    def test_success_requires_actual_owned_unit_level_increase(self):
        self.assertFalse(upgrade_confirmed({"result": False, "user_units": {"8": {"level": 2}}}, "8"))
        self.assertFalse(upgrade_confirmed({"result": True}, "8"))
        self.assertFalse(upgrade_confirmed({"user_units": {"8": {"level": 1}}}, "8"))
        self.assertTrue(upgrade_confirmed({"user_units": {"8": {"level": 2}}}, "8"))

    def test_separate_applied_foils_table_is_protected(self):
        units = {"8": {"unit_id": "100", "level": 1}, "9": {"unit_id": "100", "level": 1}}
        selected = safe_upgrade_candidates({"user_units": units, "applied_foils": {"42": {"unit_index": 8, "foil_id": 42}}},
                                           {"user_decks": {}}, {"100": {"name": "Common", "rarity": 1}})
        self.assertEqual([row["unit_index"] for row in selected], ["9"])


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class PurchaseTests(unittest.TestCase):
    def test_request_time_itself_satisfies_pacing(self):
        clock = FakeClock(); starts = []
        def call():
            starts.append(clock.now); clock.now += .8
            return {"rewards": {"give_card": []}}
        result = run_single_purchases(5, call, clock=clock.clock, sleep=clock.sleep, initial_interval=.35)
        self.assertEqual(result.done, 5)
        self.assertEqual(clock.waits, [])
        self.assertAlmostEqual(result.elapsed, 4)

    def test_transport_failure_reports_partial_count_without_retry(self):
        clock = FakeClock(); calls = []
        def call():
            calls.append(1)
            if len(calls) == 3:
                raise ConnectionError("private query must never be printed")
            return {"result": True}
        with self.assertRaises(PurchaseInterrupted) as caught:
            run_single_purchases(10, call, clock=clock.clock, sleep=clock.sleep)
        self.assertEqual(caught.exception.progress.done, 2)
        self.assertEqual(len(calls), 3)
        self.assertNotIn("private query", str(caught.exception))

    def test_many_total_warnings_do_not_abort_a_long_successful_run(self):
        clock = FakeClock(); calls = [0]
        def call():
            calls[0] += 1
            return {"result": False, "result_message": "warning_16"} if calls[0] % 2 else {"result": True}
        result = run_single_purchases(30, call, clock=clock.clock, sleep=clock.sleep)
        self.assertEqual(result.done, 30)
        self.assertEqual(result.warnings, 30)

    def test_empty_outcome_is_not_a_purchase(self):
        clock = FakeClock()
        with self.assertRaises(PurchaseInterrupted) as caught:
            run_single_purchases(2, lambda: {}, clock=clock.clock, sleep=clock.sleep)
        self.assertEqual(caught.exception.progress.done, 0)

    def test_metadata_only_response_is_not_counted_or_retried(self):
        clock = FakeClock(); calls = []
        def call():
            calls.append(1)
            return {"time": 1, "version": "test"}
        with self.assertRaises(PurchaseInterrupted) as caught:
            run_single_purchases(2, call, clock=clock.clock, sleep=clock.sleep)
        self.assertEqual(caught.exception.progress.done, 0)
        self.assertEqual(len(calls), 1)


class SanitizerTests(unittest.TestCase):
    def test_json_analytics_parsed_before_form_and_nested_session_redacted(self):
        sentinel = "DO_NOT_EXPOSE_SESSION"
        parsed = _parse_post(json.dumps({"events": [{"payload": {"session_id": sentinel, "event_type": "navigation"}}]}))
        self.assertIn("events", parsed)
        cleaned = json.dumps(_sanitize(parsed))
        self.assertNotIn(sentinel, cleaned)
        self.assertIn("navigation", cleaned)

    def test_legacy_json_key_and_encoded_json_value_redacted(self):
        sentinel = "DO_NOT_EXPOSE_TOKEN"
        old_row = {json.dumps({"kong_token": sentinel}): ""}
        encoded = {"opaque": json.dumps({"session_id": sentinel, "action": "start"})}
        for value in (old_row, encoded):
            self.assertNotIn(sentinel, json.dumps(_sanitize(value)))

    def test_form_fields_keep_learner_shape_without_secret_values(self):
        raw = "unit_index=17&password=DO_NOT_EXPOSE_PASSWORD&msgsig=DO_NOT_EXPOSE_SIGNATURE"
        cleaned = _sanitize(_parse_post(raw))
        self.assertEqual(cleaned["unit_index"], "17")
        self.assertEqual(cleaned["password"], "<redacted>")
        self.assertEqual(cleaned["msgsig"], "<redacted>")


class IntegrationTests(unittest.TestCase):
    """Exercise the GUI worker boundary without opening a UI or sending HTTP."""
    def test_choice_worker_submits_real_option_selector_and_verifies_removal(self):
        from atcommander.app import ATCommanderApp
        app = ATCommanderApp.__new__(ATCommanderApp); app.events = queue.Queue()
        catalog = {"20146": {"name": "Rogu", "card_type": "character", "rarity": "4"}}
        live = {"active_choices": [{"type": 1, "options": [{"id": "2", "give_card": [{"id": "20146"}]}]}]}
        row = extract_item_choices(live, catalog)[0]
        class Client:
            def init(self): return live
            def call(self, message, data):
                self.request = (message, data)
                return {"active_choices": []}
        client = Client()
        app._confirm_item_choice_worker(client, row, catalog)
        self.assertEqual(client.request, ("confirmUserChoice", {"choice": "2", "type": "1"}))
        self.assertEqual(app.events.get()["kind"], "item_choice_done")

    def test_rejected_choice_emits_no_completion(self):
        from atcommander.app import ATCommanderApp
        app = ATCommanderApp.__new__(ATCommanderApp); app.events = queue.Queue()
        live = {"active_choices": [{"type": 1, "options": [{"id": "1", "name": "Known", "card_type": "character"}]}]}
        row = extract_item_choices(live)[0]
        class Client:
            def init(self): return live
            def call(self, *_): return {"result": False, "active_choices": []}
        with self.assertRaisesRegex(ValueError, "preserved"):
            app._confirm_item_choice_worker(Client(), row)
        self.assertTrue(app.events.empty())

    def test_daily_worker_does_nothing_when_quest_missing(self):
        from atcommander.app import ATCommanderApp
        app = ATCommanderApp.__new__(ATCommanderApp); app.events = queue.Queue()
        class ImmediateThread:
            def __init__(self, target, **_): self.target = target
            def start(self): self.target()
        class Client:
            def init(self): return {"user_units": {"8": {"unit_id": "100", "level": 1}}}
            def call(self, *_): raise AssertionError("A missing quest must never upgrade a card")
        with patch("atcommander.app.messagebox.askyesno", return_value=True), patch("atcommander.app.ATClient", return_value=Client()), patch("atcommander.app.load_credentials"), patch("atcommander.app.threading.Thread", ImmediateThread):
            app._do_three_upgrades()
        event = app.events.get()
        self.assertEqual(event["kind"], "itemresult")
        self.assertIn("No card was touched", event["message"])

    def test_auto_choice_worker_uses_previously_captured_gui_values(self):
        from atcommander.app import ATCommanderApp
        app = ATCommanderApp.__new__(ATCommanderApp); app.events = queue.Queue(); app.refresh_inventory = lambda: None
        phase = [False]
        class Variable:
            def __init__(self, value): self.value = value
            def get(self):
                if phase[0]: raise AssertionError("Worker accessed Tk variable")
                return self.value
        class Tree:
            def selection(self): return ["chosen"]
            def item(self, *_): return ("Item", "Choice Stones", "1000", "200010")
        app.inventory_tree = Tree(); app.auto_item_choice = Variable(True); app.nixon_workers = Variable(6)
        live = {"active_choices": [{"type": 1, "options": [{"id": "2", "give_card": [{"id": "20146"}]}]}]}
        class Client:
            def __init__(self): self.reads = 0; self.requests = []
            def card_catalog(self): return {"20146": {"name": "Rogu", "card_type": "character", "rarity": "4"}}
            def init(self):
                self.reads += 1
                return {"active_choices": [], "user_items": {}} if self.reads == 1 else live
            def use_item(self, item, quantity):
                self.requests.append(("useItem", item, quantity)); return live
            def call(self, message, data):
                self.requests.append((message, data)); return {"active_choices": []}
        class ImmediateThread:
            def __init__(self, target, **_): self.target = target
            def start(self):
                phase[0] = True
                try: self.target()
                finally: phase[0] = False
        client = Client()
        with patch("atcommander.app.messagebox.askyesno", return_value=True), patch("atcommander.app.simpledialog.askinteger", return_value=1), patch("atcommander.app.ATClient", return_value=client), patch("atcommander.app.load_credentials"), patch("atcommander.app.threading.Thread", ImmediateThread):
            app._spend_selected()
        self.assertEqual(client.requests, [("useItem", "200010", 1), ("confirmUserChoice", {"choice": "2", "type": "1"})])
        events = list(app.events.queue)
        self.assertFalse(any(event["kind"] == "error" for event in events))
        self.assertTrue(any(event["kind"] == "item_choice_done" for event in events))


if __name__ == "__main__":
    unittest.main()
