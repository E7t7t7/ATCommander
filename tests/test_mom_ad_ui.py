from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from atcommander import ad_ui
from atcommander import mom_ad_ui as ads
from tests.test_mom_ad_targets import snapshot, store


WATCH = {"x": .3, "y": .87, "id": "native-mom-watch"}
CLOSE = {"x": .92, "y": .08, "id": "own-ad-close"}
ENTRY = {"x": .9, "y": .4, "id": "native-mom-entry"}
SHOP = {"x": .7, "y": .7, "id": "native-shop-entry"}
TILE = {"x": .4, "y": .5, "id": "native-mom-tile"}
CANVAS = {"x": 0, "y": 0, "width": 600, "height": 400}


def credited():
    live = snapshot()
    live["time"] = 1020
    live["slot_machine_data"].update(pulls=2, cost=200)
    return live


def prompt(point=WATCH):
    return {"mom_title": {}, "mom_watch_button": point}


class MomAdUITests(unittest.TestCase):
    def flow(self, read=None, *, before=None, fresh=None, after=None, catalog=None,
             input_error=None, stop=None, prepare=None, wait=60):
        clock = [0.0]
        clicks, frames, logs = [], [], []
        answers = [snapshot() if before is None else before,
                   snapshot() if fresh is None else fresh,
                   credited() if after is None else after]
        def init():
            answer = answers.pop(0) if len(answers) > 1 else answers[0]
            if isinstance(answer, Exception):
                raise answer
            return deepcopy(answer)
        client = SimpleNamespace(init=Mock(side_effect=init), call=Mock(return_value=store() if catalog is None else catalog))
        def view(_prep):
            frames.append(clock[0])
            if read is not None:
                return read(clock[0], clicks, len(frames)), CANVAS
            if not any(point["id"] == WATCH["id"] for _, point in clicks):
                return prompt(), CANVAS
            return {"close": CLOSE, "destination": {"x": .5, "y": .5, "id": "advertiser"}}, CANVAS
        def click(_prep, _canvas, point):
            clicks.append((clock[0], point))
            if input_error and input_error(point):
                raise RuntimeError("synthetic-private-input-detail")
        prepared = {"ok": True, "target": {"webSocketDebuggerUrl": "synthetic-dedicated-browser"}} if prepare is None else prepare
        stopped = (lambda: stop(clock[0], clicks)) if stop else None
        with patch.object(ad_ui, "prepare_ad_game_ui", return_value=prepared), \
             patch.object(ads, "_view", side_effect=view), \
             patch.object(ads, "_click", side_effect=click), \
             patch.object(ads.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(ads.time, "sleep", side_effect=lambda duration: clock.__setitem__(0, clock[0] + duration)):
            answer = ads.play_mom_ad(client, log=logs.append, should_stop=stopped, wait_seconds=wait)
        return answer, clicks, client, logs, frames

    def test_one_real_watch_own_close_and_zero_gem_pull_are_verified(self):
        answer, clicks, client, logs, _ = self.flow()
        self.assertTrue(answer["ok"])
        self.assertTrue(answer["verified"])
        self.assertEqual(answer["gems_spent"], 0)
        self.assertEqual([point["id"] for _, point in clicks], [WATCH["id"], CLOSE["id"]])
        self.assertGreaterEqual(clicks[1][0] - clicks[0][0], 12)
        self.assertEqual([call.args[0] for call in client.call.call_args_list], ["getStoreData", "getStoreData"])
        self.assertTrue(all("synthetic-private" not in line for line in logs))

    def test_native_title_and_enabled_watch_artwork_are_both_required(self):
        for controls in ({"mom_title": {}, "paid_button": WATCH}, {"mom_watch_button": WATCH},
                         {"boost_title": {}, "watch_button": WATCH}, {"close": CLOSE}):
            with self.subTest(controls=controls):
                answer, clicks, *_ = self.flow(lambda *_: controls, wait=20)
                self.assertFalse(answer["ok"])
                self.assertEqual(clicks, [])

    def test_omitted_shop_availability_is_resolved_only_by_real_watch(self):
        catalog = store()
        catalog["store_items"][0].pop("num_available")
        answer, clicks, *_ = self.flow(catalog=catalog)
        self.assertTrue(answer["ok"])
        self.assertEqual(clicks[0][1], WATCH)
        answer, clicks, *_ = self.flow(lambda *_: {"mom_title": {}, "paid_button": WATCH}, catalog=catalog, wait=20)
        self.assertFalse(answer["ok"])
        self.assertEqual(clicks, [])

    def test_first_free_does_not_enter_later_ad_playback(self):
        live = snapshot()
        live["slot_machine_data"].update(pulls=0, cost=0)
        answer, clicks, *_ = self.flow(before=live)
        self.assertFalse(answer["ok"])
        self.assertFalse(answer["playback_started"])
        self.assertEqual(clicks, [])

    def test_home_and_shop_navigation_click_each_observed_stage_once(self):
        def read(_time, clicks, _frame):
            ids = [point["id"] for _, point in clicks]
            if SHOP["id"] not in ids:
                return {"shop_entry": SHOP}
            if TILE["id"] not in ids:
                return {"shop_title": {}, "mom_shop_tile": TILE}
            if WATCH["id"] not in ids:
                return prompt()
            return {"close": CLOSE}
        answer, clicks, *_ = self.flow(read)
        self.assertTrue(answer["ok"])
        self.assertEqual([point["id"] for _, point in clicks], [SHOP["id"], TILE["id"], WATCH["id"], CLOSE["id"]])

    def test_home_mom_shortcut_is_detected_without_coordinate_calibration(self):
        def read(_time, clicks, _frame):
            ids = [point["id"] for _, point in clicks]
            if ENTRY["id"] not in ids:
                return {"mom_entry": ENTRY}
            return prompt() if WATCH["id"] not in ids else {"close": CLOSE}
        answer, clicks, *_ = self.flow(read)
        self.assertTrue(answer["ok"])
        self.assertEqual([point["id"] for _, point in clicks], [ENTRY["id"], WATCH["id"], CLOSE["id"]])

    def test_watch_and_close_require_two_stable_frames(self):
        answer, clicks, _, _, frames = self.flow()
        self.assertTrue(answer["ok"])
        self.assertGreaterEqual(sum(frame <= clicks[0][0] for frame in frames), 2)
        def moving(_time, _clicks, frame):
            return prompt({**WATCH, "x": .3 if frame % 2 else .5})
        answer, clicks, *_ = self.flow(moving, wait=20)
        self.assertFalse(answer["ok"])
        self.assertEqual(clicks, [])

    def test_changed_offer_before_watch_stops_without_ad(self):
        for key, value in (("pulls", 2), ("cost", 200)):
            live = snapshot()
            live["slot_machine_data"][key] = value
            with self.subTest(key=key):
                answer, clicks, *_ = self.flow(fresh=live)
                self.assertFalse(answer["ok"])
                self.assertEqual(clicks, [])

    def test_unknown_watch_input_is_not_replayed_or_exposed(self):
        answer, clicks, *_ = self.flow(input_error=lambda point: point["id"] == WATCH["id"])
        self.assertFalse(answer["ok"])
        self.assertTrue(answer["playback_started"])
        self.assertEqual(len(clicks), 1)
        self.assertNotIn("synthetic-private-input-detail", str(answer))

    def test_uncertain_own_close_is_reconciled_without_repeating_input(self):
        answer, clicks, *_ = self.flow(input_error=lambda point: point["id"] == CLOSE["id"])
        self.assertTrue(answer["ok"])
        self.assertEqual([point["id"] for _, point in clicks], [WATCH["id"], CLOSE["id"]])

    def test_unavailable_sdk_return_never_repeats_watch(self):
        answer, clicks, *_ = self.flow(lambda *_: prompt())
        self.assertFalse(answer["ok"])
        self.assertEqual([point["id"] for _, point in clicks], [WATCH["id"]])

    def test_missing_own_close_never_clicks_ad_destination(self):
        def read(_time, clicks, _frame):
            return prompt() if not clicks else {"destination": {"x": .5, "y": .5, "id": "advertiser"}}
        answer, clicks, *_ = self.flow(read, wait=20)
        self.assertFalse(answer["ok"])
        self.assertEqual([point["id"] for _, point in clicks], [WATCH["id"]])

    def test_spent_gems_or_uncredited_pull_stop_after_one_playback(self):
        for after in (snapshot(), credited()):
            if after["slot_machine_data"]["pulls"] == 2:
                after["user_data"]["free_hard_currency"] -= 1
            with self.subTest(after=after):
                answer, clicks, *_ = self.flow(after=after)
                self.assertFalse(answer["ok"])
                self.assertTrue(answer["closed_completed_ad"])
                self.assertEqual([point["id"] for _, point in clicks], [WATCH["id"], CLOSE["id"]])

    def test_stop_before_and_after_watch_sends_no_extra_input(self):
        answer, clicks, *_ = self.flow(stop=lambda *_: True)
        self.assertFalse(answer["playback_started"])
        self.assertEqual(clicks, [])
        answer, clicks, *_ = self.flow(stop=lambda _time, clicks: bool(clicks))
        self.assertTrue(answer["playback_started"])
        self.assertEqual([point["id"] for _, point in clicks], [WATCH["id"]])

    def test_failed_preparation_and_read_errors_have_safe_messages(self):
        answer, clicks, client, *_ = self.flow(prepare={"ok": False, "message": "synthetic-private-detail"})
        self.assertFalse(answer["ok"])
        self.assertEqual(clicks, [])
        client.init.assert_not_called()
        client.call.assert_not_called()
        self.assertNotIn("synthetic-private-detail", str(answer))
        answer, clicks, *_ = self.flow(before=RuntimeError("synthetic-private-detail"))
        self.assertFalse(answer["ok"])
        self.assertEqual(clicks, [])
        self.assertNotIn("synthetic-private-detail", str(answer))


if __name__ == "__main__":
    unittest.main()
