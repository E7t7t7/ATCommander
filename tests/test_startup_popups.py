from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from atcommander import startup_popups as startup
from atcommander.local_commands import CommandUnavailable


PREP = {"ok": True, "target": {"webSocketDebuggerUrl": "synthetic-game"}}
CATALOG = {"22": {"name": "Rogu", "card_type": "character"},
           "23": {"name": "Other Hero", "card_type": "character"},
           "24": {"name": "Object", "card_type": "item"}}


def live(choices=None):
    return {"result": True, "active_choices": choices or [], "user_achievements": {},
            "user_data": {"free_hard_currency": 57},
            "common_fields": {"free_hard_currency_balance": 57, "hard_currency_balance": 57}}


def choice(type_=8):
    return {"type": type_, "options": [
        {"id": 1, "give_card": [{"id": 24}]},
        {"id": 2, "give_card": [{"id": 23}]},
        {"id": 3, "give_card": [{"id": 22}]}]}


def observation(action="clear", panel="none", revision=10, **extra):
    names = {"reward": "RewardPopup", "choice": "RewardChoicePopup", "reward_list": "RewardListPopup",
             "quest_complete": "QuestCompletePopup", "promotion": "SaleLastChanceOfferPopup", "unsupported": "LoadingPopup"}
    return {"ok": True, "verified_public_build": True, "stack_revision": revision,
            "visible_count": 0 if panel == "none" else 1, "popup_state": 2,
            **({"popup_index": 0, "public_popup_class": names.get(panel, "ShopPopup")} if panel not in {"none", "loading", "transition"} else {}),
            "panel": panel, "navigation_action": action, **extra}


def quest_display(**extra):
    return observation("reveal", "quest_complete", quest_id=202, quest_progress=3,
                       quest_max_progress=3, quest_status=1, quest_box_active=True,
                       quest_loading=False, quest_box_clicked=False, quest_auto_complete=False,
                       quest_received_display_verified=True, **extra)


class Service:
    def __init__(self, state=None):
        self.state = deepcopy(state or live())
        self.calls = []
        self.reads = 0
        self.invalidations = 0
        self.context = self
        self.on_call = None
        self.on_read = None

    def invalidate(self):
        self.invalidations += 1

    def snapshot(self, *, refresh=False):
        assert refresh
        self.reads += 1
        if self.on_read:
            self.on_read(self)
        return deepcopy(self.state)

    def catalog(self, name):
        assert name == "card"
        return CATALOG

    def clock(self):
        return 1000

    def call(self, name, data):
        self.calls.append((name, deepcopy(data)))
        if self.on_call:
            return self.on_call(self, name, data)
        assert name == "confirmUserChoice"
        self.state["active_choices"] = [row for row in self.state["active_choices"]
                                        if str(row["type"]) != data["type"]]
        return deepcopy(self.state)


class StartupSettlementTests(unittest.TestCase):
    def run_flow(self, service=None, views=None, dispatch=None, stop=None, **options):
        service = service or Service()
        clock = [0.0]
        reads = []
        actions = []
        view = views or (lambda _actions, _reads: observation())

        def inspect(_prep):
            result = view(actions, reads)
            reads.append(deepcopy(result))
            return result

        def advance(_prep, action):
            actions.append(deepcopy(action))
            if dispatch:
                return dispatch(service, actions)
            return {"ok": True, "action_sent": True}

        with patch.object(startup, "inspect_startup_popup", side_effect=inspect), \
             patch.object(startup, "advance_startup_popup", side_effect=advance), \
             patch.object(startup.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(startup.time, "sleep", side_effect=lambda n: clock.__setitem__(0, clock[0] + n)):
            result = startup.settle_startup_popups(service, PREP, should_stop=stop, **options)
        return result, service, actions, reads

    def test_clear_requires_second_fresh_server_read_and_no_actions(self):
        result, service, actions, _ = self.run_flow()
        self.assertTrue(result["verified"])
        self.assertFalse(result["playback_started"])
        self.assertEqual(result["gem_spend"], 0)
        self.assertEqual(service.reads, 2)
        self.assertEqual(service.invalidations, service.reads)
        self.assertEqual(service.calls, [])
        self.assertEqual(actions, [])

    def test_choices_are_fresh_and_use_real_option_id_rogu_priority(self):
        result, service, actions, _ = self.run_flow(Service(live([choice(), choice(9)])))
        self.assertEqual(result["resolved_choices"], 2)
        self.assertEqual(service.calls, [("confirmUserChoice", {"type": "8", "choice": "3"}),
                                        ("confirmUserChoice", {"type": "9", "choice": "3"})])
        self.assertEqual(actions, [])
        self.assertEqual(service.invalidations, service.reads)

    def test_character_then_item_priority_is_reused(self):
        for ids, expected in (([24, 23], "2"), ([24], "1")):
            row = {"type": 8, "options": [{"id": i + 1, "give_card": [{"id": id_}]}
                                          for i, id_ in enumerate(ids)]}
            _, service, _, _ = self.run_flow(Service(live([row])))
            self.assertEqual(service.calls[0][1]["choice"], expected)

    def test_malformed_or_unknown_choice_never_confirms_or_navigates(self):
        bad = [None, {"type": 8}, choice(True), {"type": 8, "options": [{"id": 1, "give_card": [{"id": 999}]}]},
               {"type": 8, "options": [None, {"id": 1, "give_card": [{"id": 22}]}]}]
        duplicate = choice(); duplicate["options"][1]["id"] = 1
        bad.append(duplicate)
        for row in bad:
            service = Service(live([row]))
            with self.subTest(row=row), self.assertRaises(CommandUnavailable):
                self.run_flow(service)
            self.assertEqual(service.calls, [])

    def test_missing_choices_and_duplicate_type_fail_closed(self):
        state = live(); state.pop("active_choices")
        for current in (state, live([choice(), choice()])):
            service = Service(current)
            with self.assertRaises(CommandUnavailable):
                self.run_flow(service)
            self.assertEqual(service.calls, [])

    def test_ambiguous_confirmation_reconciles_once_and_never_replays(self):
        for removed in (False, True):
            service = Service(live([choice()]))

            def call(service, _name, _data):
                if removed:
                    service.state["active_choices"] = []
                raise TimeoutError("private-session-detail")

            service.on_call = call
            if removed:
                result, *_ = self.run_flow(service)
                self.assertEqual(result["resolved_choices"], 1)
            else:
                with self.assertRaises(CommandUnavailable) as failure:
                    self.run_flow(service)
                self.assertNotIn("private-session-detail", str(failure.exception))
            self.assertEqual(len(service.calls), 1)

    def test_response_and_fresh_choice_removal_are_both_required(self):
        for response, remove in ((live(), False), ({"result": False, "active_choices": []}, True)):
            service = Service(live([choice()]))

            def call(service, _name, _data):
                if remove:
                    service.state["active_choices"] = []
                return response

            service.on_call = call
            with self.assertRaises(CommandUnavailable):
                self.run_flow(service)
            self.assertEqual(len(service.calls), 1)

    def test_new_choice_queued_during_clear_is_resolved_before_return(self):
        service = Service()

        def read(service):
            if service.reads == 2:
                service.state["active_choices"] = [choice()]

        service.on_read = read
        result, *_ = self.run_flow(service)
        self.assertEqual(result["resolved_choices"], 1)
        self.assertEqual(len(service.calls), 1)

    def test_two_stable_observations_before_one_navigation(self):
        result, _, actions, reads = self.run_flow(views=lambda actions, _: observation() if actions else observation("close", "reward"))
        self.assertEqual(result["navigation_actions"], 1)
        self.assertEqual(len(actions), 1)
        self.assertEqual([row["navigation_action"] for row in reads], ["close", "close", "clear"])

    def test_box_reveal_then_close_and_new_panel_revision_are_distinct(self):
        states = [observation("reveal", "reward"), observation("close", "reward"),
                  observation("continue", "reward", revision=11), observation()]
        result, _, actions, _ = self.run_flow(views=lambda actions, _: states[len(actions)])
        self.assertEqual(result["navigation_actions"], 3)
        self.assertEqual([row["navigation_action"] for row in actions], ["reveal", "close", "continue"])

    def test_unchanged_or_unknown_navigation_is_never_repeated(self):
        for dispatch in (None, lambda *_: {"ok": False, "reason": "private-account-detail"}):
            with self.assertRaises(CommandUnavailable) as failure:
                self.run_flow(views=lambda *_: observation("close", "reward"), dispatch=dispatch, wait_seconds=1)
            self.assertNotIn("private-account-detail", str(failure.exception))

    def test_unknown_quest_and_unresolved_native_choice_block_ad_preparation(self):
        for panel, action in (("unsupported", "blocked"), ("quest_complete", "blocked"), ("choice", "choice")):
            with self.subTest(panel=panel), self.assertRaises(CommandUnavailable):
                self.run_flow(views=lambda *_: observation(action, panel))

    def test_ordinary_ad_navigation_menus_are_preserved_without_input(self):
        for name in ("ResearchPopup", "ResearchProgressPopup", "AdBoostPopup", "WatchAdsPopup", "ShopPopup"):
            with self.subTest(menu=name):
                result, service, actions, _ = self.run_flow(views=lambda *_: observation(
                    "clear", "ordinary_menu", retained_menu=True, public_popup_class=name))
                self.assertEqual(result["retained_menu"], name)
                self.assertEqual(actions, [])
                self.assertEqual(service.calls, [])

    def test_loading_waits_boundedly_but_cannot_clear_or_dispatch(self):
        loading = observation("wait", "loading", visible_count=0,
                              verified_public_build=False, runtime_loading=True)
        result, _, actions, reads = self.run_flow(views=lambda _, reads: loading if len(reads) < 2 else observation())
        self.assertTrue(result["verified"])
        self.assertEqual(actions, [])
        with self.assertRaises(CommandUnavailable):
            self.run_flow(views=lambda *_: loading, wait_seconds=1)
        for panel, action in (("none", "close"), ("promotion", "clear"), ("loading", "clear"), ("unsupported", "clear")):
            with self.subTest(panel=panel), self.assertRaises(CommandUnavailable):
                self.run_flow(views=lambda *_: observation(action, panel))

    def test_received_quest_box_navigates_without_reclaiming(self):
        for present in (False, True):
            service = Service()
            if present:
                service.state["user_achievements"] = {"202": {"id": 202, "status": 2, "progress": 3, "max_progress": 3}}
            views = lambda actions, _: observation() if actions else quest_display()
            result, service, actions, _ = self.run_flow(service, views=views)
            self.assertEqual(result["navigation_actions"], 1)
            self.assertEqual(service.calls, [])
            self.assertEqual(actions[0]["quest_id"], 202)

    def test_visible_quest_ready_unrelated_claim_or_uncertain_metadata_do_not_claim(self):
        states = [{"202": {"id": 202, "status": 1, "progress": 3, "max_progress": 3}},
                  {"202": {"id": 202, "status": 2, "progress": 2, "max_progress": 3}},
                  {"202": {"id": 202, "status": True, "progress": 3, "max_progress": 3}},
                  [{"id": 202, "status": 2, "progress": 3, "max_progress": 3}] * 2,
                  None]
        for state in states:
            service = Service(); service.state["user_achievements"] = state
            with self.subTest(state=state), self.assertRaises(CommandUnavailable):
                self.run_flow(service, views=lambda *_: quest_display())
            self.assertEqual(service.calls, [])
        service = Service()
        service.state["user_achievements"] = {"203": {"id": 203, "status": 1, "progress": 3, "max_progress": 3}}
        _, service, *_ = self.run_flow(service, views=lambda actions, _: observation() if actions else quest_display())
        self.assertEqual(service.calls, [])

    def test_every_gem_mirror_must_not_decrease_after_choice_or_navigation(self):
        for section, key in startup._GEMS:
            service = Service(live([choice()]))

            def call(service, _name, _data):
                service.state["active_choices"] = []
                service.state[section][key] -= 1
                return deepcopy(service.state)

            service.on_call = call
            with self.subTest(section=section, key=key), self.assertRaises(CommandUnavailable):
                self.run_flow(service)
            self.assertEqual(len(service.calls), 1)
        service = Service()

        def dispatch(service, _actions):
            service.state["common_fields"]["hard_currency_balance"] -= 1
            return {"ok": True, "action_sent": True}

        with self.assertRaises(CommandUnavailable):
            self.run_flow(service, views=lambda *_: observation("close", "reward"), dispatch=dispatch)

    def test_free_reward_increase_is_allowed_but_missing_mirror_is_not(self):
        for missing in (False, True):
            service = Service()

            def dispatch(service, _actions):
                if missing:
                    service.state["common_fields"].pop("hard_currency_balance")
                else:
                    for section, key in startup._GEMS:
                        service.state[section][key] += 10
                return {"ok": True, "action_sent": True}

            views = lambda actions, _: observation() if actions else observation("close", "reward")
            if missing:
                with self.assertRaises(CommandUnavailable):
                    self.run_flow(service, views=views, dispatch=dispatch)
            else:
                result, *_ = self.run_flow(service, views=views, dispatch=dispatch)
                self.assertTrue(result["verified"])

    def test_stop_and_limits_prevent_input(self):
        service = Service(live([choice()]))
        with self.assertRaises(CommandUnavailable):
            self.run_flow(service, stop=lambda: True)
        self.assertEqual(service.reads, 0)
        for options in ({"max_choices": 0}, {"wait_seconds": True}, {"wait_seconds": float("nan")}, {"max_actions": 25}):
            service = Service(live([choice()]))
            with self.subTest(options=options), self.assertRaises(CommandUnavailable):
                self.run_flow(service, **options)
            self.assertEqual(service.calls, [])

    def test_native_inspector_strips_untrusted_extra_values_and_errors(self):
        with patch.object(startup, "_evaluate", return_value={**observation(), "pointer": 999, "private": "secret-value"}):
            answer = startup.inspect_startup_popup(PREP)
        self.assertNotIn("pointer", answer)
        self.assertNotIn("secret-value", str(answer))
        with patch.object(startup, "_evaluate", side_effect=RuntimeError("secret-value")):
            self.assertNotIn("secret-value", str(startup.inspect_startup_popup(PREP)))
        self.assertEqual(startup.inspect_startup_popup(None)["reason"], "browser_not_prepared")


NODE = shutil.which("node") or str(Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe")


@unittest.skipUnless(Path(NODE).exists(), "Bundled Node is unavailable")
class NativeStartupPopupTests(unittest.TestCase):
    def native(self, scenario=None, operation="inspect", expected=None, build_hash=None):
        build_hash = build_hash or startup.PUBLIC_WASM_SHA256
        binding = startup.PUBLIC_BUILD_BINDINGS[build_hash]
        script = NODE_FIXTURE.replace("__DEFINITIONS__", json.dumps(binding["types"]))
        script = script.replace("__BINDING__", json.dumps(binding))
        script = script.replace("__SCENARIO__", json.dumps(scenario or {}))
        script = script.replace("__EXPECTED_HASH__", json.dumps(build_hash))
        script = script.replace("__EXPRESSION__", json.dumps(startup._expression(operation, expected)))
        result = subprocess.run([NODE, "--input-type=module"], input=script, capture_output=True, text=True, timeout=20)
        if result.returncode:
            self.fail("Synthetic native popup fixture failed.")
        return json.loads(result.stdout)

    def test_scoped_reward_close_and_no_sdk_or_purchase_invocation(self):
        result = self.native()
        self.assertEqual(result["result"]["navigation_action"], "close")
        self.assertEqual(result["calls"], [])
        expected = observation("close", "reward")
        result = self.native(operation="advance", expected=expected)
        self.assertTrue(result["result"]["action_sent"])
        self.assertEqual(result["calls"], [34098])

    def test_continue_requires_visible_offer_and_absent_callback(self):
        for callback in (False, True):
            result = self.native({"button": 5, "callback": callback})
            self.assertEqual(result["result"]["navigation_action"], "blocked" if callback else "continue")
        result = self.native({"button": 5}, "advance", observation("continue", "reward"))
        self.assertEqual(result["calls"], [34099])

    def test_paused_box_reveal_uses_only_ordinary_press_release(self):
        result = self.native({"paused": True, "waiting": True})
        self.assertEqual(result["result"]["navigation_action"], "reveal")
        result = self.native({"paused": True, "waiting": True}, "advance", observation("reveal", "reward"))
        self.assertEqual(result["calls"], [34876, 34877])

    def test_active_choice_close_and_continue_are_display_only(self):
        for button, action, handler in ((2, "close", 34019), (5, "continue", 34020)):
            result = self.native({"panel": "choice", "button": button}, "advance", observation(action, "choice"))
            self.assertEqual(result["calls"], [handler])
        result = self.native({"panel": "choice", "button": 1})
        self.assertEqual(result["result"]["navigation_action"], "choice")

    def test_claim_buy_gems_and_quest_box_do_not_dispatch(self):
        for button in (0, 1, 3, 4, 6):
            result = self.native({"button": button})
            self.assertEqual(result["result"]["navigation_action"], "blocked")
            self.assertEqual(result["calls"], [])
        result = self.native({"panel": "quest_complete"})
        self.assertEqual(result["result"]["navigation_action"], "blocked")

    def test_quest_postclaim_box_requires_exact_received_display_closure(self):
        result = self.native({"panel": "quest_complete", "received_quest": True})
        self.assertTrue(result["result"]["quest_received_display_verified"])
        self.assertEqual(result["result"]["quest_id"], 202)
        self.assertEqual(result["result"]["navigation_action"], "reveal")
        result = self.native({"panel": "quest_complete", "received_quest": True}, "advance", quest_display())
        self.assertEqual(result["calls"], [33642])
        for extra in ({"wrong_display_handler": True}, {"wrong_display_metadata": True}, {"foreign_reward_owner": True},
                      {"unreceived_reward": True}, {"extra_delegate": True}, {"quest_auto_complete": True},
                      {"quest_box_clicked": True}, {"quest_box_hidden": True}, {"quest_loading": True},
                      {"after_rewards_callback": True}, {"hide_callback": True}):
            result = self.native({"panel": "quest_complete", "received_quest": True, **extra}, "advance", quest_display())
            self.assertEqual(result["calls"], [])
        wrong = quest_display(); wrong["quest_id"] = 203
        self.assertEqual(self.native({"panel": "quest_complete", "received_quest": True}, "advance", wrong)["calls"], [])

    def test_sale_offer_owned_visible_close_never_buy_or_callbacks(self):
        result = self.native({"panel": "promotion"})
        self.assertEqual(result["result"]["public_popup_class"], "SaleLastChanceOfferPopup")
        self.assertEqual(result["result"]["navigation_action"], "close")
        result = self.native({"panel": "promotion"}, "advance", observation("close", "promotion"))
        self.assertEqual(result["calls"], [33642])
        for scenario in ({"hidden_button": True}, {"hide_callback": True}, {"before_hide_callback": True}, {"can_close": False}):
            result = self.native({"panel": "promotion", **scenario}, "advance", observation("close", "promotion"))
            self.assertEqual(result["calls"], [])

    def test_native_ordinary_menu_clear_is_exact_and_preserves_stack(self):
        for name in ("ResearchPopup", "ResearchProgressPopup", "AdBoostPopup", "WatchAdsPopup", "ShopPopup"):
            result = self.native({"panel": name})
            self.assertEqual(result["result"]["panel"], "ordinary_menu")
            self.assertEqual(result["result"]["navigation_action"], "clear")
            self.assertEqual(result["calls"], [])
        result = self.native({"panel": "ShopPopup", "second_popup": True})
        self.assertNotEqual(result["result"].get("navigation_action"), "clear")

    def test_cold_native_runtime_wait_is_not_verified_clear(self):
        for scenario in ({"missing_runtime": True}, {"metadata_loading": True}, {"manager_loading": True}):
            result = self.native(scenario)
            self.assertEqual(result["result"]["panel"], "loading")
            self.assertEqual(result["result"]["navigation_action"], "wait")
            self.assertEqual(result["calls"], [])

    def test_hash_field_scope_and_owner_change_fail_before_dispatch(self):
        for scenario in ({"bad_hash": True}, {"bad_field": True}, {"bad_root": True},
                         {"foreign_scope": True}, {"hidden_button": True}, {"can_close": False},
                         {"tutorial": True}, {"buy_more": True}, {"second_popup": True}):
            result = self.native(scenario, "advance", observation("close", "reward"))
            self.assertEqual(result["calls"], [])
            self.assertNotEqual(result["result"].get("action_sent"), True)
        result = self.native(operation="advance", expected=observation("close", "reward", revision=9))
        self.assertEqual(result["calls"], [])
        for extra in ({"popup_index": 1, "visible_count": 2}, {"visible_count": 2}, {"public_popup_class": "SaleLastChanceOfferPopup"}):
            result = self.native(operation="advance", expected=observation("close", "reward", **extra))
            self.assertEqual(result["calls"], [])

    def test_no_visible_popup_is_clear_and_transition_is_wait(self):
        self.assertEqual(self.native({"empty": True})["result"]["navigation_action"], "clear")
        self.assertEqual(self.native({"hidden": True})["result"]["navigation_action"], "wait")

    def test_new_build_reward_choice_reveal_and_quest_use_their_exact_bindings(self):
        new = startup.PUBLIC_WASM_1182
        for scenario, expected, calls in (
            ({}, observation("close", "reward"), [35036]),
            ({"button": 5}, observation("continue", "reward"), [35037]),
            ({"paused": True, "waiting": True}, observation("reveal", "reward"), [35814, 35815]),
            ({"panel": "choice"}, observation("close", "choice"), [34949]),
            ({"panel": "choice", "button": 5}, observation("continue", "choice"), [34950]),
            ({"panel": "quest_complete", "received_quest": True}, quest_display(), [34572]),
            ({"panel": "promotion"}, observation("close", "promotion"), [34572]),
        ):
            with self.subTest(scenario=scenario):
                result = self.native(scenario, "advance", expected, build_hash=new)
                self.assertEqual(result["calls"], calls)

    def test_new_build_changed_fields_paid_buttons_and_foreign_callbacks_stay_blocked(self):
        new = startup.PUBLIC_WASM_1182
        for scenario in ({"bad_field": True}, {"bad_hash": True}, {"bad_root": True},
                         {"button": 3}, {"button": 4}, {"buy_more": True}, {"callback": True, "button": 5}):
            with self.subTest(scenario=scenario):
                result = self.native(scenario, "advance", observation("close", "reward"), build_hash=new)
                self.assertEqual(result["calls"], [])
        for extra in ({"wrong_display_handler": True}, {"wrong_display_metadata": True},
                      {"foreign_reward_owner": True}, {"unreceived_reward": True}):
            result = self.native({"panel": "quest_complete", "received_quest": True, **extra},
                                 "advance", quest_display(), build_hash=new)
            self.assertEqual(result["calls"], [])


class NativeStartup1183Tests(unittest.TestCase):
    # Reuse the synthetic memory runner, but discover only these new checks.
    __unittest_skip__ = not Path(NODE).exists()
    native = NativeStartupPopupTests.native

    def test_1183_visible_reward_navigation_uses_verified_ordinary_handlers(self):
        build=startup.PUBLIC_WASM_1183;slots=startup.PUBLIC_BUILD_BINDINGS[build]['slots']
        cases=[({},observation('close','reward'),[slots['reward_close']]),
            ({'button':5},observation('continue','reward'),[slots['reward_continue']]),
            ({'paused':True,'waiting':True},observation('reveal','reward'),[slots['reveal_press'],slots['reveal_release']]),
            ({'panel':'choice'},observation('close','choice'),[slots['choice_close']]),
            ({'panel':'quest_complete','received_quest':True},quest_display(),[slots['hide']]),
            ({'panel':'promotion'},observation('close','promotion'),[slots['hide']])]
        for scenario,expected,calls in cases:
            with self.subTest(scenario=scenario):
                result=self.native(scenario,'advance',expected,build_hash=build)
                self.assertEqual(result['calls'],calls)
                self.assertTrue(result['result']['action_sent'])

    def test_1183_wrong_build_paid_controls_foreign_scope_and_callbacks_stay_blocked(self):
        for scenario in ({'bad_hash':True},{'bad_field':True},{'bad_root':True},{'button':3},
                         {'button':4},{'buy_more':True},{'foreign_scope':True},{'callback':True,'button':5}):
            with self.subTest(scenario=scenario):
                result=self.native(scenario,'advance',observation('close','reward'),build_hash=startup.PUBLIC_WASM_1183)
                self.assertEqual(result['calls'],[])


NODE_FIXTURE = r'''
const definitions=__DEFINITIONS__,scenario=__SCENARIO__,hash=__EXPECTED_HASH__,binding=__BINDING__,slots=binding.slots;
const field=(name,label)=>definitions[name].fields.find(item=>item[1]===label)[2];
const buffer=new ArrayBuffer(16*1024*1024),h=new Uint32Array(buffer),b=new Uint8Array(buffer);
const put=(p,v)=>{h[p>>>2]=v;},word=(p,s)=>{for(let i=0;i<s.length;i++)b[p+i]=s.charCodeAt(i);b[p+s.length]=0;};
const base=0x200000;put(base,0xFAB11BAF);put(base+4,39);put(base+8,380);
let nextClass=0x4000,nextFields=0x100000,nextString=0x180000;
const classes={};
for(const [name,d] of Object.entries(definitions)){
 word(base+d.offset,name);word(base+d.namespace_offset,d.namespace);
 const klass=nextClass;nextClass+=1024;classes[name]=klass;
 put(klass+8,base+d.offset);put(klass+12,base+d.namespace_offset);put(klass+64,nextFields);
 for(const [index,field,offset] of d.fields){word(nextString,field);put(nextFields+index*20,nextString);put(nextFields+index*20+12,offset);nextString+=128;}
 nextFields+=4096;
}
if(scenario.bad_field){const entry=definitions.RewardPopup.fields.find(item=>item[1]==='OnDismiss');put(h[(classes.RewardPopup+64)>>>2]+entry[0]*20+12,entry[2]-4);}
for(const name of ['RewardPopup','RewardChoicePopup','QuestCompletePopup','RewardListPopup','SaleLastChanceOfferPopup','ResearchPopup','ResearchProgressPopup','AdBoostPopup','WatchAdsPopup','ShopPopup'])put(classes[name]+44,classes.Popup);
put(classes.ServiceAchievementData+44,classes.ServiceDeepLinkData);put(classes.MulticastDelegate+44,classes.Delegate);put(classes['Action`1']+44,classes.MulticastDelegate);
put(binding.managers,scenario.bad_root?classes.Popup:classes.Managers);put(classes.Managers+92,0x650000);put(0x650008,0x30000);
put(0x30000,classes.PopupManager);put(0x30000+52,0x31000);
const makeList=(p,array,count,revision=10)=>{put(p,classes['List`1']);put(p+8,array);put(p+12,count);put(p+16,revision);put(array,0x6000);put(array+12,count);};
makeList(0x31000,0x32000,scenario.empty?0:scenario.second_popup?2:1);
const popup=0x34000,kind=scenario.panel==='choice'?'RewardChoicePopup':scenario.panel==='quest_complete'?'QuestCompletePopup':scenario.panel==='promotion'?'SaleLastChanceOfferPopup':scenario.panel in classes?scenario.panel:'RewardPopup';
put(0x32000+16,scenario.foreign_scope?0x34500:popup);put(popup,classes[kind]);put(popup+8,0x600000);put(popup+56,2);b[popup+18]=scenario.can_close===false?0:1;
put(0x34500,classes.GameObject);put(0x34500+8,0x600000);
if(scenario.second_popup){put(0x32000+20,0x35000);put(0x35000,classes.RewardPopup);put(0x35008,0x600000);}
const active=new Map();
const game=(p,on=true)=>{put(p,classes.GameObject);put(p+8,0x600000);active.set(p,on);};
game(0x36000,!scenario.hidden);game(0x36500);game(0x37000,!scenario.hidden_button);
const components=new Map([[popup,0x36000],[0x35000,0x36500],[0x44000,0x37000]]);
const buttons=0x38000;put(buttons,classes.RewardPopupButtonsView);put(buttons+16,0x39000);makeList(0x39000,0x3a000,1);
put(0x3a000+16,0x3b000);put(0x3b000,classes.ButtonToGameObject);put(0x3b000+8,scenario.button??2);put(0x3b000+12,0x37000);
put(popup+field(kind==='RewardChoicePopup'?'RewardChoicePopup':'RewardPopup','_buttonsView'),buttons);
put(popup+field('RewardPopup','OnDismiss'),scenario.callback?0x3c000:0);b[popup+field('RewardPopup','<IsTutorialFlow>k__BackingField')]=scenario.tutorial?1:0;b[popup+field('RewardPopup','<BuyMoreClicked>k__BackingField')]=scenario.buy_more?1:0;b[popup+field('RewardPopup','_mustWaitShowRoutine')]=scenario.waiting?1:0;
put(popup+field('RewardChoicePopup','rewardData'),0x3d000);put(0x3d000,classes.ServiceRewardChoiceData);put(0x3d000+12,8);
if(kind==='SaleLastChanceOfferPopup'){put(popup+100,0x44000);put(0x44000,classes.tk2dUIItem);put(0x44008,0x600000);put(popup+80,scenario.hide_callback?0x3c000:0);put(popup+84,scenario.before_hide_callback?0x3c000:0);}
if(kind==='QuestCompletePopup'){
 put(popup+116,0x45000);put(0x45000,classes.ServiceAchievementData);put(0x45008,202);put(0x45000+68,3);put(0x45000+72,3);put(0x45000+76,1);
 put(popup+108,0x45100);game(0x45100,!!scenario.quest_loading);
 put(popup+112,0x45200);put(0x45200,classes.SkeletonAnimation);put(0x45208,0x600000);game(0x45300,!scenario.quest_box_hidden);components.set(0x45200,0x45300);
 b[popup+124]=scenario.quest_box_clicked?1:0;b[popup+125]=scenario.quest_auto_complete?1:0;put(popup+80,scenario.hide_callback?0x3c000:0);put(popup+120,scenario.after_rewards_callback?0x3c000:0);
 if(scenario.received_quest){
  put(popup+84,0x45400);put(0x45400,classes['Action`1']);put(0x45408,scenario.wrong_display_handler?slots.quest_received-9:slots.quest_received);put(0x45400+16,0x45500);put(0x45400+20,0x45600);put(0x45400+60,scenario.extra_delegate?0x3c000:0);
  put(binding.quest_received_method-4,0x45640);put(0x45640,slots.quest_received-1);put(binding.quest_received_method,0x45600);put(0x45600,scenario.wrong_display_metadata?slots.quest_received-1:slots.quest_received);put(0x45500,classes['<>c__DisplayClass15_0']);put(0x45508,scenario.foreign_reward_owner?0x34500:popup);put(0x4550c,0x45700);put(0x45700,classes.ServiceRewardData);b[0x45708]=scenario.unreceived_reward?0:1;
 }
}
if(scenario.paused){put(popup+field('RewardPopup','_openAnimationController'),0x43000);put(0x43000,classes.RewardPopupOpeningAnimationController);put(0x43010,0x44000);put(0x44000,classes.tk2dUIItem);put(0x44008,0x600000);b[0x43000+28]=1;}
const calls=[];
const table={get:slot=>{
 if(slot===slots.active)return (p)=>active.get(p)?1:0;
 if(slot===slots.component_object)return (p)=>components.get(p)||0;
 if(slot===slots.touch)return ()=>1;
 if([slots.reward_close,slots.reward_continue,slots.choice_close,slots.choice_continue,slots.hide,slots.reveal_press,slots.reveal_release].includes(slot))return ()=>{calls.push(slot);};
 throw Error('Forbidden native invocation');
}};
Object.defineProperty(globalThis,'crypto',{value:{subtle:{digest:async()=>Uint8Array.from(Buffer.from(scenario.bad_hash?'00'.repeat(32):hash,'hex')).buffer}},configurable:true});
globalThis.window={unityInstance:{Module:{HEAPU32:h,HEAPU8:b,wasmBinary:new Uint8Array([0,1,2,3,4,5,6,7]),asm:{__indirect_function_table:table}}}};
if(scenario.missing_runtime)globalThis.window={};if(scenario.metadata_loading)put(base,0);if(scenario.manager_loading)put(0x650008,0);
const result=await eval(__EXPRESSION__);process.stdout.write(JSON.stringify({result,calls}));
'''


if __name__ == "__main__":
    unittest.main()
