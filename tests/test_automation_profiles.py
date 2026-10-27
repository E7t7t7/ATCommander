import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from atcommander.automation_profiles import (AutomationProfiles,BUILTIN_DAILY,DailyOptions,
    build_daily_plan,register_automation_profiles,run_daily_profile)
from atcommander.command_store import CommandStore
from atcommander.local_commands import (ArgumentSpec,CommandContext,CommandInputError,
    CommandResult,CommandSpec,CommandUnavailable,LocalCommandService)

NOW=1000


def quest(name,req,progress=0,target=3,**extra):
    return {"id":5010,"name":name,"req":req,"progress":progress,"max_progress":target,
            "end_time":2000,"status":0,**extra}


def account(*quests):
    return {"time":NOW,"user_achievements":list(quests),"user_data":{"money":100000,"stamina":20,"boost_level":3},
            "user_tasks":[],"user_mail":[],"user_units":{},"user_decks":{},"active_events":[],
            "user_items":[],"store_items":[{"id":"1","sc_cost":"1000","num_available":"99"}]}


def step(plan,identity):
    return next((row for row in plan.steps if row["id"]==identity),None)


class FakeRunner:
    def __init__(self,live,callback=None):
        self.live=live;self.clock=lambda:NOW;self.calls=[];self.stop=threading.Event();self.callback=callback
        self.daily_capabilities={"daily_login":True};self.catalogs={}
    def snapshot(self,refresh=False):return copy.deepcopy(self.live)
    def catalog(self,key):return self.catalogs.get(key,{})
    def call(self,name,args=None):raise AssertionError("Unexpected protocol read")
    def execute(self,name,args=None):
        args=args or {};self.calls.append((name,args))
        if self.callback:
            result=self.callback(self,name,args)
            if result is not None:return result
        return CommandResult(message="Confirmed test action.")


class ProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=CommandStore(Path(self.temp.name)/"commands.json")
        self.service=LocalCommandService(CommandContext(object(),snapshot=account(),snapshot_loader=account,clock=lambda:NOW))
        self.service.register(CommandSpec("play","Play","test",(ArgumentSpec("count",kind="int",default=1,minimum=1),ArgumentSpec("hero_tokens",default=""))),lambda *_:CommandResult())
        self.service.register(CommandSpec("danger","Guild","test",destructive=True,action=True),lambda *_:CommandResult())
        self.service.register(CommandSpec("message","Guild","test",sends_message=True,action=True),lambda *_:CommandResult())
        self.service.register(CommandSpec("private","Account","test",(ArgumentSpec("login_hash"),)),lambda *_:CommandResult())
        self.service.register(CommandSpec("options","Automation","test",(ArgumentSpec("options"),)),lambda *_:CommandResult())
        self.profiles=AutomationProfiles(self.store,self.service)
    def test_builtin_is_available_and_protected(self):
        self.assertEqual(self.profiles.names(),[BUILTIN_DAILY])
        self.assertEqual(self.profiles.load(BUILTIN_DAILY)[0]["command"],"daily-run")
        for action in (lambda:self.profiles.delete(BUILTIN_DAILY),lambda:self.profiles.save(BUILTIN_DAILY,[{"command":"play"}])):
            with self.assertRaises(CommandInputError):action()
    def test_profile_round_trip_preserves_other_preferences_and_normalizes_args(self):
        self.store.update("preferences",{"keep":True})
        self.profiles.save("  My   Daily  ",[{"command":"play","args":{"count":"2","hero_tokens":"Rogu"}}])
        self.assertEqual(self.profiles.names(),[BUILTIN_DAILY,"My Daily"])
        self.assertEqual(self.profiles.load("my daily")[0]["args"]["count"],2)
        self.assertEqual(self.store.read()["preferences"],{"keep":True})
        self.profiles.save("MY DAILY",[{"command":"play"}]);self.assertEqual(len(self.profiles.names()),2)
        self.profiles.delete("my daily");self.assertEqual(self.profiles.names(),[BUILTIN_DAILY])
    def test_unsafe_or_private_steps_never_persist(self):
        for command in ("danger","message","private","missing"):
            with self.assertRaises(CommandInputError):self.profiles.save("Unsafe",[{"command":command}])
        with self.assertRaises(CommandInputError):
            self.profiles.save("Nested",[{"command":"options","args":{"options":json.dumps({"credentials":{"secret":"test"}})}}])
        self.assertNotIn("automation_profiles",self.store.read())
    def test_corrupt_saved_profiles_are_hidden(self):
        self.store.update("automation_profiles",{"Bad":{"steps":[{"command":"missing"}]},"Broken":None})
        self.assertEqual(self.profiles.names(),[BUILTIN_DAILY])


class DailyPlanTests(unittest.TestCase):
    def test_options_validate_before_any_action(self):
        self.assertFalse(DailyOptions.parse({"watch_ads":"false"}).watch_ads)
        for options in ({"nixon_budget":3001},{"max_battles":-1},{"unexpected":True},{"cm_stones_budget":True},"[]"):
            with self.assertRaises(CommandInputError):DailyOptions.parse(options)
    def test_misnamed_combo_mastery_daily_is_arena_requirement(self):
        live=account(quest("Daily - Combo Mastery",{"total_pvp_battles_num":"10"},3,10))
        live["user_data"]["stamina"]=10
        plan=build_daily_plan(live,now=NOW)
        self.assertEqual(step(plan,"arena")["args"]["count"],5)
        self.assertIsNone(step(plan,"cm-purchase"));self.assertEqual(plan.unresolved,[])
        args=step(plan,"arena")["args"]
        self.assertEqual(args["hero_tokens"],"");self.assertEqual(args["max_refresh_nixons"],0)
        self.assertFalse(args["buy_basic"]);self.assertFalse(args["use_lootcrate"])
        self.assertIsNone(args['deck']);self.assertEqual(args['min_energy'],0)
        self.assertIsNone(args['max_level']);self.assertIsNone(args['swole_level'])
    def test_basic_purchase_only_remaining_pack_with_current_price_and_budget(self):
        live=account(quest("Daily - Buy Packs",{"store_item":{"id":"0","number":"3"}},2))
        self.assertEqual(step(build_daily_plan(live,now=NOW),"packs")["args"],{"quantity":"1","budget":3000})
        blocked=build_daily_plan(live,{"nixon_budget":0},NOW)
        self.assertIsNone(step(blocked,"packs"));self.assertEqual(len(blocked.unresolved),1)
        live["store_items"][0]["sc_cost"]=4000
        self.assertIsNone(step(build_daily_plan(live,now=NOW),"packs"))
    def test_unknown_daily_never_uses_quest_name_as_action_authority(self):
        live=account(quest("Daily - Combo Mastery",{"unknown_requirement":"3"}))
        plan=build_daily_plan(live,{"cm_stones_budget":5000},NOW)
        self.assertIsNone(step(plan,"cm-purchase"));self.assertIsNone(step(plan,"arena"))
        self.assertEqual(plan.unresolved[0]["remaining"],3)
    def test_missing_metadata_does_not_claim_coverage(self):
        live=account(quest("Daily - Research",{},end_time=None))
        plan=build_daily_plan(live,now=NOW)
        self.assertFalse(plan.quest_coverage);self.assertEqual(len(plan.unresolved),1)
        self.assertFalse(build_daily_plan(account(),now=NOW).quest_coverage)
    def test_completed_claimed_daily_can_verify_completion(self):
        live=account(quest("Daily - Packs",{},3,status=2))
        plan=build_daily_plan(live,now=NOW)
        self.assertTrue(plan.quest_coverage);self.assertEqual(plan.unresolved,[])

    def test_daily_child_cannot_extend_or_clear_expired_parent(self):
        for child_end in (0,2000):
            with self.subTest(child_end=child_end):
                child=quest("Daily Arena",{"total_pvp_battles_num":3},end_time=child_end)
                live=account({"name":"Daily group","end_time":999,"sub_quests":[child]})
                plan=build_daily_plan(live,now=NOW)
                self.assertIsNone(step(plan,"arena"));self.assertFalse(plan.quest_coverage)

    def test_daily_child_cannot_start_before_future_parent(self):
        child=quest("Daily Arena",{"total_pvp_battles_num":3},start_time=0)
        live=account({"name":"Daily group","start_time":1100,"end_time":2000,"sub_quests":[child]})
        plan=build_daily_plan(live,now=NOW)
        self.assertIsNone(step(plan,"arena"));self.assertFalse(plan.quest_coverage)

    def test_parent_window_bounds_the_effective_daily_identity_and_expiry(self):
        from atcommander.automation_profiles import _daily_quests
        child=quest("Daily Arena",{"total_pvp_battles_num":3},start_time=0,end_time=2000)
        live=account({"start_time":900,"end_time":1100,"sub_quests":[child]})
        found=_daily_quests(live,NOW)
        self.assertEqual((found[0]['start_time'],found[0]['end_time']),(900,1100))
        self.assertEqual(_daily_quests(live,1100),[])

    def test_malformed_parent_windows_cannot_be_repaired_by_child(self):
        for key in ("start_time","end_time"):
            for value in (True,None,1000.5,"2000.0",-1):
                with self.subTest(key=key,value=value):
                    child=quest("Daily Arena",{"total_pvp_battles_num":3})
                    live=account({key:value,"sub_quests":[child]})
                    plan=build_daily_plan(live,now=NOW)
                    self.assertIsNone(step(plan,"arena"));self.assertFalse(plan.quest_coverage)
                    self.assertEqual(len(plan.unresolved),1)

    def test_unrelated_malformed_sibling_does_not_poison_valid_daily(self):
        live=account({"name":"Unrelated","end_time":True},
                     {"sub_quests":[{"name":"Unrelated","start_time":None},
                                    quest("Daily Arena",{"total_pvp_battles_num":3})]})
        plan=build_daily_plan(live,now=NOW)
        self.assertEqual(step(plan,"arena")['args']['count'],3)
        self.assertTrue(plan.quest_coverage)

    def test_untimed_daily_container_can_use_a_verified_child_window(self):
        live=account({"name":"Daily group","sub_quests":[quest("Arena",{"total_pvp_battles_num":3})]})
        self.assertEqual(step(build_daily_plan(live,now=NOW),"arena")['args']['count'],3)

    def test_malformed_leaf_progress_cannot_authorize_a_daily_action(self):
        for changes in ({"progress":False},{"max_progress":True},{"status":False},{"progress":0.5}):
            with self.subTest(changes=changes):
                live=account(quest("Daily Arena",{"total_pvp_battles_num":3},**changes))
                plan=build_daily_plan(live,now=NOW)
                self.assertIsNone(step(plan,"arena"));self.assertFalse(plan.quest_coverage)

    def test_saved_arena_bypass_is_neutralized_for_daily_arena_and_event_steps(self):
        from atcommander.command_center import SchemaClient
        from atcommander.utility_commands import build_service
        live=account(quest("Daily Arena",{"total_pvp_battles_num":3}))
        live['active_events']=[{'id':94041,'name':'Scruffy Melee','start_time':900,'end_time':1500,
            'challenge_data':{'id':5501,'level':13,'energy':{'current_value':6,'battle_cost':1}}}]
        with tempfile.TemporaryDirectory() as temporary:
            store=CommandStore(Path(temporary)/'commands.json');store.update('play_defaults',{'bypass_arena_turn_limit':True,'melee_character_first':True})
            service=build_service(SchemaClient(),store=store,snapshot=live,snapshot_loader=lambda:live,clock=lambda:NOW)
            captured=[]
            service._handlers['play']=lambda current,args:(captured.append(copy.deepcopy(args)) or CommandResult())
            for row in build_daily_plan(live,now=NOW).steps:
                if row['command']=='play':service.execute('play',row['args'])
        self.assertEqual({args['mode'] for args in captured},{'arena','melee'})
        self.assertTrue(all(args['bypass_arena_turn_limit'] is False for args in captured))
        self.assertTrue(all(args['melee_character_first'] is False for args in captured))
    def test_protected_upgrade_candidates_only(self):
        live=account(quest("Daily - Upgrade Cards",{"upgrade_cards_num":"3"},2))
        live["user_units"]={"1":{"unit_id":22,"level":1},"2":{"unit_id":22,"level":1},"3":{"unit_id":22,"level":1,"foil_id":7}}
        live["user_decks"]={"1":{"units":[{"unit_index":1}]}}
        plan=build_daily_plan(live,now=NOW,catalogs={"card":{"22":{"name":"Loose Card","rarity":1}}})
        self.assertEqual(step(plan,"upgrade")["args"],{"max_upgrades":1})
        live.pop("user_decks")
        self.assertIsNone(step(build_daily_plan(live,now=NOW,catalogs={"card":{"22":{"rarity":1}}}),"upgrade"))
    def test_verified_stone_only_cm_offer_requires_explicit_budget(self):
        live=account(quest("Daily - Shop Pack",{"store_item":{"id":"42","number":"3"}},0))
        live["store_items"]=[{"id":"42","name":"Mastery Token Pack","tabs":{"mastery":"1"},"item":[{"id":2035365}],"cost":{"item":"200008","num":"1000"}}]
        live["user_items"]=[{"id":"200008","number":"6000"}]
        catalog={"card":{"35365":{"name":"Second Combo","set":"1001"}}}
        self.assertIsNone(step(build_daily_plan(live,now=NOW,catalogs=catalog),"cm-purchase:42"))
        result=step(build_daily_plan(live,{"cm_stones_budget":2000},NOW,catalog),"cm-purchase:42")
        self.assertEqual(result["args"],{"store_id":42,"quantity":2,"budget":2000,"expected_currency_item":200008})
        live["store_items"][0]["hc_cost"]="500"
        self.assertIsNone(step(build_daily_plan(live,{"cm_stones_budget":2000},NOW,catalog),"cm-purchase:42"))

    def test_overlapping_pack_quests_share_one_remaining_purchase(self):
        req={"store_item":{"id":"0","number":"3"}}
        live=account(quest("Daily Packs A",req,2),quest("Daily Packs B",req,0))
        plan=build_daily_plan(live,now=NOW)
        packs=[row for row in plan.steps if row["id"]=="packs"]
        self.assertEqual(len(packs),1)
        self.assertEqual(packs[0]["args"]["quantity"],"3")

    def test_cm_offer_with_nixon_alternative_is_not_stone_only(self):
        live=account(quest("Daily Shop",{"store_item":{"id":"42","number":"3"}}))
        live["store_items"]=[{"id":"42","name":"Mastery Token Pack","tabs":{"mastery":"1"},"item":[{"id":2035365}],"sc_cost":500,"cost":{"item":"200008","num":"1000"}}]
        live["user_items"]=[{"id":"200008","number":"6000"}]
        plan=build_daily_plan(live,{"cm_stones_budget":2000},NOW,{"card":{"35365":{"name":"Combo","set":"1001"}}})
        self.assertFalse(any(row["command"]=="shop-purchase" for row in plan.steps))
    def test_current_event_cost_and_reserve_and_closed_siege(self):
        live=account()
        live["active_events"]=[{"id":20,"name":"Swole", "start_time":900,"end_time":1500,"challenge_data":{"id":80,"level":6,"energy":{"current_value":5,"battle_cost":2}}},
            {"id":21,"name":"Clash","start_time":900,"end_time":999,"challenge_data":{"id":81,"level":6,"energy":{"current_value":5}}}]
        live["guild_siege_status"]={"phase":1,"num_attacks":10,"enemy_locations":[{"hp":100}]}
        plan=build_daily_plan(live,now=NOW)
        self.assertEqual(step(plan,"event:20")["args"]["count"],2)
        self.assertIsNone(step(plan,"event:21"));self.assertIsNone(step(plan,"siege"))
    def test_mom_first_free_requires_both_actual_zero_fields(self):
        live=account();live["slot_machine_data"]={"cost":0,"pulls":0}
        self.assertIsNotNone(step(build_daily_plan(live,now=NOW),"mom-free"))
        live["slot_machine_data"]["pulls"]=1
        self.assertIsNone(step(build_daily_plan(live,now=NOW),"mom-free"))


class DailyRunnerTests(unittest.TestCase):
    def test_multiple_cm_offers_share_one_daily_stone_budget(self):
        live=account(*(quest("Daily Shop "+str(sid),{"store_item":{"id":str(sid),"number":"3"}}) for sid in (42,43)))
        live["store_items"]=[{"id":str(sid),"name":"Mastery Token Pack","tabs":{"mastery":"1"},"item":[{"id":2035365}],"cost":{"item":"200008","num":"1000"}} for sid in (42,43)]
        live["user_items"]=[{"id":"200008","number":"6000"}]
        service=FakeRunner(live);service.catalogs={"card":{"35365":{"name":"Combo","set":"1001"}}}
        run_daily_profile(service,{"cm_stones_budget":2500,"include_events":False,"research_pairs":0})
        purchases=[args for name,args in service.calls if name=="shop-purchase"]
        self.assertEqual([row["budget"] for row in purchases],[2000,500])
        self.assertTrue(all(row["expected_currency_item"]==200008 for row in purchases))

    def test_duplicate_daily_pack_requirements_do_not_reset_cap(self):
        req={"store_item":{"id":"0","number":"3"}}
        service=FakeRunner(account(quest("Daily A",req,2),quest("Daily B",req,0)))
        run_daily_profile(service,{"include_events":False,"research_pairs":0})
        purchases=[args for name,args in service.calls if name=="buy-basic"]
        self.assertEqual(purchases,[{"quantity":"3","budget":3000}])
    def test_stale_quest_is_rechecked_before_battles(self):
        live=account(quest("Daily - Arena",{"total_pvp_battles_num":3},0))
        def callback(service,name,args):
            if name=="daily-login":service.live["user_achievements"][0]["progress"]=2
            if name=="play":
                service.live["user_achievements"][0]["progress"]=3
                return CommandResult(rows=[{"won":True}],message="1 fight")
        service=FakeRunner(live,callback)
        answer=run_daily_profile(service,{"watch_ads":False})
        self.assertEqual(next(args["count"] for name,args in service.calls if name=="play"),1)
        self.assertIn("are complete",answer.message)
        self.assertIn("remaining",answer.details)
    def test_unknown_action_outcome_stops_without_next_action_or_retry(self):
        live=account(quest("Daily - Arena",{"total_pvp_battles_num":3},0))
        def callback(service,name,args):
            if name=="play":raise CommandUnavailable("Outcome unknown")
        service=FakeRunner(live,callback)
        with self.assertRaises(CommandUnavailable):run_daily_profile(service)
        self.assertEqual([name for name,_ in service.calls],["daily-login","play"])
    def test_explicit_inactive_event_is_skipped_and_later_daily_step_runs(self):
        live=account()
        live['active_events']=[{'id':94041,'name':'Scruffy Melee','start_time':900,'end_time':1500,
            'challenge_data':{'id':5501,'level':13,'energy':{'current_value':6,'battle_cost':1}}},
            {'id':94042,'name':'Jimmy Melee','start_time':900,'end_time':1500,
            'challenge_data':{'id':5502,'level':14,'energy':{'current_value':1,'battle_cost':1}}}]
        def callback(service,name,args):
            if name=='play' and args.get('event')==94041:return CommandResult(message='Challenge event not active.',data={
                'unavailable_event':{'code':'warning_126_text','reason':'Challenge event not active.'}})
            if name=='play':return CommandResult(rows=[{'won':True}],message='Verified second event battle.')
        service=FakeRunner(live,callback)
        answer=run_daily_profile(service,{'watch_ads':False,'research_pairs':0})
        self.assertEqual(sum(name=='play' for name,_ in service.calls),2)
        self.assertTrue(any('Challenge event not active' in row['reason'] for row in answer.data['skipped']))
        self.assertTrue(any('Verified second event battle' in row['message'] for row in answer.data['completed']))
    def test_unverified_mom_and_boost_skip_without_stopping_dailies(self):
        live=account();live["slot_machine_data"]={"cost":0,"pulls":0};live["user_data"]["boost_level"]=0
        service=FakeRunner(live);answer=run_daily_profile(service)
        self.assertNotIn("buy-momfree",[name for name,_ in service.calls])
        self.assertNotIn("user-ad",[name for name,_ in service.calls])
        self.assertGreaterEqual(len(answer.data["skipped"]),2)
        self.assertIn("no all-complete claim",answer.message)
    def test_pending_unconfirmed_choice_blocks_next_action(self):
        live=account();live["active_choices"]=[{"type":"2"}]
        service=FakeRunner(live)
        with self.assertRaises(CommandUnavailable):run_daily_profile(service)
        self.assertNotIn("daily-login",[name for name,_ in service.calls])
        self.assertEqual([name for name,_ in service.calls],["item-choose"])
    def test_unresolved_requirement_is_visible_in_final_details(self):
        service=FakeRunner(account(quest("Daily - Research",{"unknown":1})))
        answer=run_daily_profile(service)
        self.assertIn("remain incomplete",answer.message)
        self.assertIn("no verified automation mapping",answer.details)
    def test_global_battle_limit_covers_multiple_modes(self):
        live=account(quest("Daily - Arena",{"total_pvp_battles_num":2},0,2))
        live["active_events"]=[{"id":20,"name":"Swole","start_time":900,"end_time":1500,"challenge_data":{"id":80,"level":1,"energy":{"current_value":10,"battle_cost":1}}}]
        def callback(service,name,args):
            if name=="play":return CommandResult(rows=[{"won":True} for _ in range(args["count"])])
        service=FakeRunner(live,callback);run_daily_profile(service,{"max_battles":3})
        self.assertEqual(sum(args["count"] for name,args in service.calls if name=="play"),3)
    def test_readonly_preview_does_not_execute_actions_and_shows_reasons(self):
        service=LocalCommandService(CommandContext(object(),snapshot=account(quest("Daily - Research",{"unknown":1})),snapshot_loader=account,clock=lambda:NOW))
        register_automation_profiles(service)
        self.assertFalse(service.spec("daily-plan").action)
        self.assertTrue(service.spec("daily-run").spends_resources)
        answer=service.execute("daily-plan")
        self.assertIn("No action was sent",answer.message)
        self.assertIn("unresolved",answer.details)
    def test_preview_lists_unverified_flows_as_skips(self):
        live=account();live["slot_machine_data"]={"cost":0,"pulls":0};live["user_data"]["boost_level"]=0
        service=LocalCommandService(CommandContext(object(),snapshot=live,snapshot_loader=account,clock=lambda:NOW))
        register_automation_profiles(service);answer=service.execute("daily-plan")
        self.assertFalse(any(row["id"] in {"login","mom-free","boost"} for row in answer.rows))
        self.assertGreaterEqual(len(answer.data["skipped"]),3)
    def test_daily_research_uses_separate_bounded_queue_without_default_speedups(self):
        live=account();live["user_combos_compressed"]={};live["research_times_slot"]=[{"slot_index":1}]
        service=FakeRunner(live)
        with patch("atcommander.research_commands.research_daily_preflight",return_value={"available":True}):
            run_daily_profile(service)
        args=next(args for name,args in service.calls if name=="research-run")
        self.assertEqual(args["scope"],"daily");self.assertTrue(args["resume"])
        settings=json.loads(args["options"])
        self.assertEqual(settings["max_pairs"],3);self.assertFalse(settings["speedups"])
        self.assertEqual(settings["max_speedup_hours"],0)
        self.assertEqual(settings["tiers"],[1,2,3,4,5])
    def test_blocked_research_metadata_skips_without_hiding_other_dailies(self):
        live=account(quest("Daily - Packs",{"store_item":{"id":"0","number":"3"}},2))
        live["user_combos_compressed"]={};live["research_times_slot"]=[{"slot_index":1}]
        service=FakeRunner(live)
        with patch("atcommander.research_commands.research_daily_preflight",return_value={"available":False,"reason":"Active daily queue uses different settings."}):
            answer=run_daily_profile(service)
        self.assertNotIn("research-run",[name for name,_ in service.calls])
        self.assertIn("buy-basic",[name for name,_ in service.calls])
        self.assertIn("different settings",answer.details)


if __name__=="__main__":unittest.main()
