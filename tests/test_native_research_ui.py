from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch

from atcommander import native_research_ui as picker


PAIR = (1400003, 1400021)
PREP = {"ok": True, "target": {"webSocketDebuggerUrl": "synthetic-prepared-game"}}


def state(selected=(0, 0), visible=True, **extra):
    return {"ok": True, "verified_public_build": True, "verified_build": True,
            "chamber": 3, "ad_locked": True, "selected": list(selected),
            "selected_card_ids": list(selected), "exact_visible": visible,
            "library_card_ids": list(PAIR), "stack_count": 2,
            "library_ids_truncated": False, **extra}


class NativeResearchPickerTests(unittest.TestCase):
    def run_flow(self, hook=None, stop=None, **kwargs):
        clock = [0.0]
        selected = [0, 0]
        calls = []

        def evaluate(_prep, operation, pair, slot):
            calls.append((operation, tuple(pair) if pair else None, slot, tuple(selected)))
            if hook:
                changed = hook(operation, slot, selected, calls)
                if isinstance(changed, Exception):
                    raise changed
                if changed is not None:
                    return deepcopy(changed)
            if operation == "read":
                return state(selected)
            if operation == "select":
                selected[slot - 1] = pair[slot - 1]
                return {"ok": True, "selection_started": True}
            if operation == "scroll":
                return {"ok": True, "scrolled": True}
            raise AssertionError("Unexpected operation")

        with patch.object(picker, "_evaluate", side_effect=evaluate), \
             patch.object(picker.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(picker.time, "sleep", side_effect=lambda amount: clock.__setitem__(0, clock[0] + amount)):
            result = picker.select_native_research_pair(
                PREP, kwargs.pop("pair", PAIR), should_stop=(lambda: stop(calls)) if stop else None,
                **kwargs)
        return result, calls

    def test_selects_each_card_once_with_fresh_second_and_final_proof(self):
        result, calls = self.run_flow()
        self.assertTrue(result["ok"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["selected_card_ids"], list(PAIR))
        self.assertFalse(result["resource_action_started"])
        actions = [call for call in calls if call[0] == "select"]
        self.assertEqual([call[2] for call in actions], [1, 2])
        self.assertEqual(actions[1][3], (PAIR[0], 0))
        self.assertEqual(calls[-1][0], "read")
        self.assertEqual(calls[-1][3], PAIR)

    def test_explicit_order_must_match_canonical_pair(self):
        result, calls = self.run_flow(pair=sorted(PAIR), card1=PAIR[1], card2=PAIR[0])
        self.assertTrue(result["ok"])
        self.assertEqual(result["pair"], sorted(PAIR))
        self.assertEqual(result["selected_card_ids"], list(reversed(PAIR)))
        self.assertTrue(all(call[1] == tuple(reversed(PAIR)) for call in calls))
        for options in ({"card1": PAIR[0]}, {"card1": PAIR[0], "card2": 99}):
            with self.subTest(options=options):
                result, calls = self.run_flow(**options)
                self.assertFalse(result["ok"])
                self.assertEqual(calls, [])

    def test_numeric_strings_are_safe_and_injection_or_ambiguous_ids_rejected(self):
        result, _ = self.run_flow(pair=[str(id_) for id_ in PAIR])
        self.assertTrue(result["ok"])
        for pair in ([True, PAIR[1]], [1.0, PAIR[1]], [0, PAIR[1]],
                     [5000000, PAIR[1]], [PAIR[0], PAIR[0]],
                     ["1); fetch('private')", PAIR[1]], ["1e4", PAIR[1]],
                     [" 1400003 ", PAIR[1]], [PAIR[0]], None):
            with self.subTest(pair=pair):
                result, calls = self.run_flow(pair=pair)
                self.assertEqual(result["reason"], "invalid_pair")
                self.assertEqual(calls, [])

    def test_build_or_chamber_or_unknown_selection_stops_before_input(self):
        for bad in (state(verified_public_build=False), state(chamber=1),
                    state(ad_locked=False), state(selected_card_ids=[None, 0]),
                    state(selected_card_ids=[True, 0]), {"ok": False}):
            with self.subTest(bad=bad):
                result, calls = self.run_flow(lambda *_: bad)
                self.assertFalse(result["ok"])
                self.assertEqual([call[0] for call in calls], ["read"])

    def test_existing_different_card_is_never_replaced(self):
        result, calls = self.run_flow(lambda *_: state((99, 0)))
        self.assertEqual(result["reason"], "selection_changed")
        self.assertEqual([call[0] for call in calls], ["read"])

    def test_same_complete_pair_needs_no_input_but_fresh_final_check(self):
        result, calls = self.run_flow(lambda *_: state(PAIR, visible=False,
                                                      library_card_ids=[], stack_count=0))
        self.assertTrue(result["ok"])
        self.assertFalse(result["selection_changed"])
        self.assertEqual([call[0] for call in calls], ["read", "read", "read"])

    def test_second_library_is_reloaded_after_first_selection(self):
        seen_second = [0]

        def hook(operation, slot, selected, _calls):
            if operation == "read" and slot == 2 and selected[1] == 0:
                seen_second[0] += 1
                return state(selected, visible=seen_second[0] > 1)

        result, calls = self.run_flow(hook)
        self.assertTrue(result["ok"])
        self.assertEqual([(call[0], call[2]) for call in calls if call[0] != "read"],
                         [("select", 1), ("scroll", 2), ("select", 2)])

    def test_pending_native_library_refresh_is_read_again_before_any_input(self):
        pending = [2]
        def hook(operation, _slot, selected, _calls):
            if operation == "read" and pending[0]:
                pending[0] -= 1
                return state(selected, library_ready=False)
        result, calls = self.run_flow(hook)
        self.assertTrue(result["ok"])
        self.assertEqual([call[0] for call in calls[:4]], ["read", "read", "read", "select"])
        self.assertEqual(sum(call[0] == "select" for call in calls), 2)

    def test_library_that_never_finishes_refreshing_stops_without_input(self):
        result, calls = self.run_flow(lambda *_: state(library_ready=False), wait_seconds=1)
        self.assertEqual(result["reason"], "selection_timeout")
        self.assertFalse(result["unknown_outcome"])
        self.assertFalse(result["selection_changed"])
        self.assertTrue(all(call[0] == "read" for call in calls))

    def test_scrolls_are_bounded_and_never_select_stale_offscreen_card(self):
        result, calls = self.run_flow(
            lambda operation, _slot, selected, _calls: state(selected, visible=False) if operation == "read" else None,
            max_scrolls=2)
        self.assertEqual(result["reason"], "scroll_limit")
        self.assertEqual(sum(call[0] == "scroll" for call in calls), 2)
        self.assertFalse(any(call[0] == "select" for call in calls))

    def test_failed_scroll_or_unknown_input_is_never_retried(self):
        for failure in ({"ok": False, "reason": "private-session-value"},
                        RuntimeError("private-session-value")):
            with self.subTest(failure=failure):
                def hook(operation, _slot, selected, _calls):
                    return state(selected, visible=False) if operation == "read" else failure
                result, calls = self.run_flow(hook)
                self.assertTrue(result["unknown_outcome"])
                self.assertEqual([call[0] for call in calls], ["read", "scroll"])
                self.assertNotIn("private-session-value", str(result))

    def test_unknown_selector_outcome_never_resends_it(self):
        result, calls = self.run_flow(
            lambda operation, *_: RuntimeError("private-request-detail") if operation == "select" else None)
        self.assertFalse(result["ok"])
        self.assertTrue(result["selection_changed"])
        self.assertTrue(result["unknown_outcome"])
        self.assertEqual([call[0] for call in calls], ["read", "select"])
        self.assertNotIn("private-request-detail", str(result))

    def test_coroutine_may_settle_later_without_repeat(self):
        pending = [None]
        polls = [0]

        def hook(operation, slot, selected, _calls):
            if operation == "select":
                pending[0] = slot
                polls[0] = 0
                return {"ok": True, "selection_started": True}
            if operation == "read" and pending[0]:
                polls[0] += 1
                if polls[0] >= 3:
                    selected[pending[0] - 1] = PAIR[pending[0] - 1]
                    pending[0] = None

        result, calls = self.run_flow(hook)
        self.assertTrue(result["ok"])
        self.assertEqual(sum(call[0] == "select" for call in calls), 2)

    def test_unchanged_pending_selection_times_out_without_retry(self):
        result, calls = self.run_flow(
            lambda operation, *_: {"ok": True, "selection_started": True} if operation == "select" else None,
            wait_seconds=1)
        self.assertEqual(result["reason"], "selection_timeout")
        self.assertTrue(result["unknown_outcome"])
        self.assertEqual(sum(call[0] == "select" for call in calls), 1)

    def test_first_card_changed_during_second_read_stops(self):
        def hook(operation, slot, selected, _calls):
            if operation == "read" and slot == 2:
                return state((99, selected[1]))
        result, calls = self.run_flow(hook)
        self.assertEqual(result["reason"], "selection_changed")
        self.assertEqual([call[2] for call in calls if call[0] == "select"], [1])

    def test_final_verification_failure_does_not_start_research(self):
        def hook(operation, _slot, selected, calls):
            if operation == "read" and selected == list(PAIR):
                if sum(call[0] == "read" and call[3] == PAIR for call in calls) >= 2:
                    return state((PAIR[0], 99))
        result, calls = self.run_flow(hook)
        self.assertEqual(result["reason"], "selection_unverified")
        self.assertFalse(result["resource_action_started"])
        self.assertTrue(all(call[0] in {"read", "select", "scroll"} for call in calls))

    def test_stop_before_read_and_between_read_and_selection(self):
        for stop in (lambda _calls: True, lambda calls: len(calls) >= 1):
            with self.subTest(stop=stop):
                result, calls = self.run_flow(stop=stop)
                self.assertEqual(result["reason"], "stopped")
                self.assertFalse(any(call[0] != "read" for call in calls))

    def test_stop_after_first_selection_prevents_second(self):
        result, calls = self.run_flow(stop=lambda calls: any(call[0] == "select" for call in calls))
        self.assertEqual(result["reason"], "stopped")
        self.assertTrue(result["selection_changed"])
        self.assertEqual([call[2] for call in calls if call[0] == "select"], [1])

    def test_invalid_limits_never_access_browser(self):
        for options in ({"wait_seconds": True}, {"wait_seconds": float("nan")},
                        {"wait_seconds": 0}, {"wait_seconds": 61},
                        {"max_scrolls": True}, {"max_scrolls": -1}, {"max_scrolls": 129}):
            with self.subTest(options=options):
                result, calls = self.run_flow(**options)
                self.assertEqual(result["reason"], "invalid_limit")
                self.assertEqual(calls, [])

    def test_prepared_browser_is_required(self):
        for prep in (None, {}, {"ok": True}, {**PREP, "ok": 1},
                     {"ok": True, "target": {"webSocketDebuggerUrl": None}}):
            with self.subTest(prep=prep), patch.object(picker, "_evaluate") as evaluate:
                result = picker.select_native_research_pair(prep, PAIR)
                self.assertEqual(result["reason"], "browser_not_prepared")
                evaluate.assert_not_called()

    def test_inspection_returns_whitelisted_identity_fields_only(self):
        value = state(PAIR, secret="private-value", pointer=1234567)
        with patch.object(picker, "_evaluate", return_value=value) as evaluate:
            result = picker.read_native_research_selection(PREP)
        self.assertTrue(result["ok"])
        self.assertEqual(result["selected_card_ids"], list(PAIR))
        self.assertEqual(result["library_card_ids"], list(PAIR))
        self.assertNotIn("private-value", str(result))
        self.assertNotIn("pointer", result)
        evaluate.assert_called_once_with(PREP, "inspect", None, 1)

    def test_inspection_empty_filtered_library_can_verify_selected_slots(self):
        with patch.object(picker, "_evaluate", return_value=state(PAIR, library_card_ids=[], stack_count=0)):
            result = picker.read_native_research_selection(PREP)
        self.assertTrue(result["ok"])
        self.assertEqual(result["selected_card_ids"], list(PAIR))

    def test_inspection_rejects_unverified_or_malformed_catalog(self):
        for bad in (state(verified_public_build=False), state(library_card_ids=[True]),
                    state(library_card_ids=[0]), state(stack_count=True),
                    state(library_card_ids=[1] * 2049), state(stack_count=50001)):
            with self.subTest(bad=bad), patch.object(picker, "_evaluate", return_value=bad):
                self.assertFalse(picker.read_native_research_selection(PREP)["ok"])

    def test_inspection_exposes_only_valid_tab_refresh_and_card_identity_details(self):
        safe = state(native_tab_index=2, native_tab_count=3, library_ready=True,
                     source_card_ids=list(PAIR), source_count=2, visible_card_ids=[PAIR[0]],
                     source_ids_truncated=False, private_detail="private-value")
        with patch.object(picker, "_evaluate", return_value=safe):
            result = picker.read_native_research_selection(PREP)
        self.assertEqual(result["native_tab_index"], 2)
        self.assertTrue(result["library_ready"])
        self.assertEqual(result["source_card_ids"], list(PAIR))
        self.assertEqual(result["visible_card_ids"], [PAIR[0]])
        self.assertNotIn("private-value", str(result))
        for bad in ({"native_tab_index": True, "native_tab_count": 3},
                    {"native_tab_index": 3, "native_tab_count": 3},
                    {"native_tab_index": 2}, {"library_ready": 1},
                    {"source_card_ids": [False]}, {"visible_card_ids": [0]},
                    {"source_count": True}):
            with self.subTest(bad=bad), patch.object(picker, "_evaluate", return_value=state(**bad)):
                self.assertFalse(picker.read_native_research_selection(PREP)["ok"])
    def test_inspection_preserves_only_fixed_native_guard_codes(self):
        for reason in ("wrong_chamber", "build_changed", "library_not_eligible", "card_unavailable"):
            with self.subTest(reason=reason), \
                 patch.object(picker, "_evaluate", return_value={"ok": False, "reason": reason}):
                result = picker.read_native_research_selection(PREP)
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], reason)
                self.assertFalse(result["verified_public_build"])
        for reason in ("private-session-value", {"private": "value"}, None):
            with self.subTest(kind=type(reason).__name__), \
                 patch.object(picker, "_evaluate", return_value={"ok": False, "reason": reason}):
                result = picker.read_native_research_selection(PREP)
                self.assertEqual(result["reason"], "native_ui_unavailable")
                self.assertNotIn("private", str(result))

    def test_selection_reports_missing_native_card_before_any_input(self):
        result, calls = self.run_flow(lambda *_: {"ok": False, "reason": "card_unavailable"})
        self.assertEqual(result["reason"], "card_unavailable")
        self.assertFalse(result["selection_changed"])
        self.assertFalse(result["unknown_outcome"])
        self.assertFalse(result["resource_action_started"])
        self.assertEqual([call[0] for call in calls], ["read"])

    def test_selection_unknown_action_keeps_safe_underlying_guard_and_never_replays(self):
        result, calls = self.run_flow(
            lambda operation, *_: {"ok": False, "reason": "selection_unknown"} if operation == "select" else None)
        self.assertEqual(result["reason"], "ui_action_unverified")
        self.assertEqual(result["native_reason"], "selection_unknown")
        self.assertTrue(result["unknown_outcome"])
        self.assertTrue(result["selection_changed"])
        self.assertFalse(result["resource_action_started"])
        self.assertEqual([call[0] for call in calls], ["read", "select"])

    def test_read_transport_failure_codes_are_safe_and_do_not_authorize_native_input(self):
        for response, reason in (
            ({"exceptionDetails": {"text": "private-detail"}}, "native_operation_failed"),
            ({"result": {"value": None}}, "native_result_unavailable"),
            ({"result": []}, "native_result_unavailable"),
            (None, "native_result_unavailable"),
        ):
            with self.subTest(reason=reason), patch.object(picker, "_cdp_native", return_value=response):
                result = picker.read_native_research_selection(PREP)
                self.assertEqual(result["reason"], reason)
                self.assertNotIn("private", str(result))
                self.assertFalse(result["verified_public_build"])
        with patch.object(picker, "_cdp_native", side_effect=RuntimeError("private-transport-detail")):
            result = picker.read_native_research_selection(PREP)
        self.assertEqual(result["reason"], "native_transport_unavailable")
        self.assertNotIn("private", str(result))

    def test_typed_transport_failure_cannot_supply_arbitrary_report_reason(self):
        error = picker._NativeUIError("private-session-detail", "private-message")
        with patch.object(picker, "_evaluate", side_effect=error):
            result = picker.read_native_research_selection(PREP)
            selected = picker.select_native_research_pair(PREP, PAIR)
        self.assertEqual(result["reason"], "native_transport_unavailable")
        self.assertEqual(selected["reason"], "ui_read_unavailable")
        self.assertEqual(selected["native_reason"], "native_transport_unavailable")
        self.assertNotIn("private", str(result) + str(selected))

    def test_cdp_exception_details_and_non_value_response_are_rejected(self):
        for response in ({"exceptionDetails": {"text": "private-detail"}},
                         {"result": {"value": None}}):
            with self.subTest(response=response), patch.object(picker, "_cdp_native", return_value=response):
                with self.assertRaises(RuntimeError) as error:
                    picker._evaluate(PREP, "read", PAIR, 1)
                self.assertNotIn("private-detail", str(error.exception))

    def test_transport_waits_for_its_id_and_uses_bounded_long_receive_timeout(self):
        import websocket
        socket = Mock()
        socket.recv.side_effect = [json.dumps({"method": "Runtime.consoleAPICalled"}),
                                   json.dumps({"id": 99, "result": {"private": "ignore"}}),
                                   json.dumps({"id": 867531, "result": {"result": {"value": state()}}})]
        with patch.object(websocket, "create_connection", return_value=socket) as connect, \
             patch.object(picker.time, "monotonic", side_effect=[0, 1, 3, 4]):
            result = picker._evaluate(PREP, "read", PAIR, 1)
        self.assertTrue(result["ok"])
        connect.assert_called_once_with("synthetic-prepared-game", timeout=20.0, suppress_origin=True)
        self.assertEqual([call.args[0] for call in socket.settimeout.call_args_list], [19, 17, 16])
        sent = json.loads(socket.send.call_args.args[0])
        self.assertEqual(sent["id"], 867531)
        self.assertEqual(sent["method"], "Runtime.evaluate")
        self.assertTrue(sent["params"]["awaitPromise"])
        socket.close.assert_called_once()

    def test_transport_failure_is_closed_and_private_details_are_suppressed(self):
        import websocket
        for response in ({"id": 867531, "error": {"message": "private-session"}},
                         RuntimeError("private-session")):
            with self.subTest(response=response):
                socket = Mock()
                socket.recv.side_effect = [json.dumps(response)] if isinstance(response, dict) else response
                with patch.object(websocket, "create_connection", return_value=socket), \
                     self.assertRaises(RuntimeError) as error:
                    picker._evaluate(PREP, "select", PAIR, 1)
                self.assertNotIn("private-session", str(error.exception))
                socket.close.assert_called_once()
                self.assertEqual(socket.send.call_count, 1)

    def test_transport_deadline_does_not_wait_or_resend_after_expiry(self):
        import websocket
        socket = Mock()
        with patch.object(websocket, "create_connection", return_value=socket), \
             patch.object(picker.time, "monotonic", side_effect=[0, 21]), \
             self.assertRaises(RuntimeError):
            picker._evaluate(PREP, "scroll", PAIR, 1)
        socket.recv.assert_not_called()
        self.assertEqual(socket.send.call_count, 1)
        socket.close.assert_called_once()


@unittest.skipUnless(shutil.which("node"), "Node is unavailable for the isolated public-ABI heap fixture")
class NativeResearchJavaScriptFixtureTests(unittest.TestCase):
    def native(self, operation="read", *, slot=1, **scenario):
        expression = picker._expression(operation, PAIR if operation != "inspect" else None, slot)
        harness = Path(__file__).with_name("native_research_ui_fixture.js")
        completed = subprocess.run([shutil.which("node"), str(harness)],
                                   input=json.dumps({"expression": expression,
                                                     "types": picker.PUBLIC_TYPES,
                                                     "hash": (picker.CURRENT_PUBLIC_WASM_SHA256
                                                              if scenario.get("build") == "current"
                                                              else picker.PUBLIC_WASM_SHA256),
                                                     "scenario": scenario}),
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_changed_public_build_never_resolves_or_invokes_native_controls(self):
        result = self.native("select", digest="00" * 32)
        self.assertEqual(result["result"], {"ok": False, "reason": "build_changed"})
        self.assertEqual(result["calls"], [])

    def test_current_public_build_uses_its_independent_card_layout_and_selector_slots(self):
        result = self.native("select", build="current")
        self.assertTrue(result["result"]["selection_started"])
        self.assertEqual(result["selected"], [PAIR[0], 0])
        self.assertEqual([row[0] for row in result["calls"] if row[0] in {34812, 5957}], [34812, 5957])
        self.assertFalse(any(row[0] in {33882, 4514, 2825, 5538} for row in result["calls"]))
        self.assertTrue(result["unchanged_outside_native_handlers"])

    def test_current_hash_does_not_accept_the_old_metadata_layout(self):
        result = self.native("select", digest=picker.CURRENT_PUBLIC_WASM_SHA256)
        self.assertEqual(result["result"]["reason"], "metadata_changed")
        self.assertEqual(result["calls"], [])

    def test_current_build_scroll_uses_only_its_public_normal_handlers(self):
        result = self.native("scroll", build="current", offscreen=True, exported_allocator=True)
        self.assertTrue(result["result"]["scrolled"])
        self.assertEqual([row[0] for row in result["calls"] if row[0] in {29952, 29953, 29954}],
                         [29952, 29953, 29953, 29954])
        self.assertEqual(result["allocations"], [48])
        self.assertEqual(len(result["frees"]), 1)
        self.assertTrue(result["unchanged_outside_native_handlers"])

    def test_both_builds_report_scoped_tab_source_filtered_and_visible_card_ids(self):
        for build in ("old", "current"):
            with self.subTest(build=build):
                result = self.native("inspect", build=build)["result"]
                self.assertTrue(result["library_ready"])
                self.assertEqual(result["native_tab_index"], 2)
                self.assertEqual(result["native_tab_count"], 3)
                self.assertEqual(result["source_card_ids"], [*PAIR, 103])
                self.assertEqual(result["library_card_ids"], [*PAIR, 103])
                self.assertEqual(result["visible_card_ids"], list(PAIR))

    def test_both_builds_wait_for_refresh_without_selecting_stale_stack_data(self):
        for build in ("old", "current"):
            for pending in ({"pending_reload": True}, {"pending_filter": True}):
                with self.subTest(build=build, pending=pending):
                    result = self.native("read", build=build, **pending)
                    self.assertTrue(result["result"]["ok"])
                    self.assertFalse(result["result"]["library_ready"])
                    self.assertFalse(any(row[0] in {33882, 4514, 34812, 5957} for row in result["calls"]))
                    result = self.native("select", build=build, **pending)
                    self.assertEqual(result["result"]["reason"], "library_refresh_pending")
                    self.assertFalse(any(row[0] in {33882, 4514, 34812, 5957} for row in result["calls"]))

    def test_unknown_native_tab_cannot_authorize_selection_in_either_build(self):
        for build in ("old", "current"):
            with self.subTest(build=build):
                result = self.native("select", build=build, wrong_tab=True)
                self.assertEqual(result["result"]["reason"], "tabs_unavailable")
                self.assertFalse(any(row[0] in {33882, 4514, 34812, 5957} for row in result["calls"]))
    def test_read_only_inspection_reports_wrong_chamber_before_any_selector(self):
        result = self.native("inspect", chamber=1)
        self.assertEqual(result["result"], {"ok": False, "reason": "wrong_chamber"})
        self.assertFalse(any(row[0] in {33882, 4514, 29040, 29041, 29042} for row in result["calls"]))

    def test_metadata_or_field_offset_changes_fail_before_native_selection(self):
        for scenario in ({"metadata_missing": True}, {"field_changed": True}):
            with self.subTest(scenario=scenario):
                result = self.native("select", **scenario)
                self.assertFalse(result["result"]["ok"])
                self.assertFalse(any(row[0] in {33882, 4514} for row in result["calls"]))

    def test_offscreen_card_is_not_mistaken_for_recycled_visible_view(self):
        result = self.native("select", offscreen=True)
        self.assertEqual(result["result"]["reason"], "card_not_visible")
        self.assertFalse(any(row[0] in {33882, 4514} for row in result["calls"]))

    def test_selector_uses_only_ordinary_factory_and_scheduler_for_exact_source(self):
        result = self.native("select")
        self.assertTrue(result["result"]["selection_started"])
        self.assertEqual(result["selected"], [PAIR[0], 0])
        self.assertEqual([row[0] for row in result["calls"] if row[0] in {33882, 4514}], [33882, 4514])
        self.assertTrue(result["unchanged_outside_native_handlers"])

    def test_horizontal_scrolling_uses_normal_allocations_and_handlers_without_field_writes(self):
        result = self.native("scroll", offscreen=True)
        self.assertTrue(result["result"]["scrolled"])
        sequence = [row[0] for row in result["calls"] if row[0] in {29040, 29041, 29042}]
        self.assertEqual(sequence, [29040, 29041, 29041, 29042])
        positions = [row[1] for row in result["calls"] if row[0] in {29040, 29041, 29042}]
        self.assertTrue(all(position[1] == positions[0][1] for position in positions))
        self.assertEqual(result["allocations"], [48])
        self.assertEqual(len(result["frees"]), 1)
        self.assertTrue(result["unchanged_outside_native_handlers"])

    def test_hidden_or_ambiguous_popup_never_selects(self):
        for scenario in ({"hidden": True}, {"second_popup": True}):
            with self.subTest(scenario=scenario):
                result = self.native("select", **scenario)
                self.assertFalse(result["result"]["ok"])
                self.assertFalse(any(row[0] in {33882, 4514} for row in result["calls"]))

    def test_visible_card_data_id_mismatch_never_selects(self):
        result = self.native("select", source_mismatch=True)
        self.assertEqual(result["result"]["reason"], "card_not_visible")
        self.assertFalse(any(row[0] in {33882, 4514} for row in result["calls"]))

    def test_complete_selected_pair_can_be_inspected_after_empty_library_filter(self):
        result = self.native("inspect", selected=list(PAIR), empty_library=True)
        self.assertTrue(result["result"]["ok"])
        self.assertEqual(result["result"]["selected_card_ids"], list(PAIR))
        self.assertEqual(result["result"]["library_card_ids"], [])
        self.assertFalse(any(row[0] in {33882, 4514, 29040, 29041, 29042} for row in result["calls"]))

    def test_missing_allocator_does_not_attempt_direct_scroll_field_write(self):
        result = self.native("scroll", offscreen=True, no_allocator=True)
        self.assertEqual(result["result"]["reason"], "scroll_unavailable")
        self.assertFalse(any(row[0] in {29040, 29041, 29042} for row in result["calls"]))
        self.assertTrue(result["unchanged_outside_native_handlers"])

    def test_verified_ordinary_wasm_exports_work_without_module_wrappers(self):
        result = self.native("scroll", offscreen=True, exported_allocator=True)
        self.assertTrue(result["result"]["scrolled"])
        self.assertEqual(result["allocations"], [48])
        self.assertEqual(len(result["frees"]), 1)
        self.assertTrue(result["unchanged_outside_native_handlers"])


if __name__ == "__main__":
    unittest.main()
