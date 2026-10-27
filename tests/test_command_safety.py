import threading
import json
from types import SimpleNamespace
import unittest
import test_utility_commands as fixture
from unittest.mock import patch
from atcommander.account_registry import AccountRegistry
from atcommander.local_commands import CommandUnavailable
from atcommander.shop_rewards import run_single_purchases
from atcommander.command_center import CommandCenterMixin,daily_result_text


class CommandSafetyTests(unittest.TestCase):
    setUp=fixture.UtilityTests.setUp

    def test_read_commands_reuse_recent_snapshot(self):
        self.service.execute("deck-list")
        self.service.execute("deck-map",{"deck":1})
        self.assertEqual([name for name,_ in self.client.calls].count("init"),1)

    def test_action_uses_fresh_snapshot_after_read(self):
        self.service.execute("deck-list")
        self.service.execute("deck-rename",{"deck":1,"name":"Fresh"})
        self.assertEqual([name for name,_ in self.client.calls].count("init"),2)

    def test_uncertain_mutation_is_never_replayed(self):
        self.client.call=lambda *_: (_ for _ in ()).throw(TimeoutError("unknown outcome"))
        with self.assertRaises(TimeoutError):self.service.execute("deck-rename",{"deck":1,"name":"Fresh"})

    def test_inventory_returns_owned_cards(self):
        self.client.card_catalog=lambda:{"22":{"name":"Rogu","rarity":4,"card_type":"character"}}
        answer=self.service.execute("user-inventory",{"query":"Rogu","min_rarity":4})
        self.assertEqual(len(answer.rows),1)
        self.assertEqual(answer.rows[0]["unit_id"],22)

    def test_recipe_filters_use_current_pair_map(self):
        self.service.context._catalogs["card"]={"22":{"name":"Rogu","rarity":4,"card_type":"character"},"23":{"name":"Object","rarity":4},"15001":{"id":15001,"name":"Rogu Combo","set":1001,"trait":["armed"],"skill":[{"id":"punch","x":10}]}}
        self.service.context._catalogs["combo"]={"22~23":"15001|"}
        answer=self.service.execute("card-combo",{"query":"Rogu Combo","character":"Rogu","item":"Object","min_rarity":4})
        self.assertEqual(answer.rows[0]["card1"],"22")
        self.assertEqual(answer.rows[0]["card2_name"],"Object")

    def test_blank_account_alias_is_rejected(self):
        for value in ("   "," all ",""):
            with self.assertRaises(ValueError):AccountRegistry.alias(value)

    def test_golden_stop_keeps_confirmed_purchase_count(self):
        calls=[]
        progress=run_single_purchases(5,lambda:(calls.append(1) or {"result":True}),
            should_stop=lambda:len(calls)>=2,sleep=lambda _:None)
        self.assertEqual(progress.done,2)
        self.assertEqual(progress.status,"stopped")

    def test_repeat_waits_for_running_battle(self):
        class Value:
            def set(self,text):self.text=text
        app=type("App",(),{})();app.routine_enabled=True;app.command_busy=False;app.routine_next=0
        app.command_schema=self.service;app.routine_steps=[{"command":"deck-select","args":{"deck":1}}]
        app.arena_runner=type("Runner",(),{"running":True})();app.event_runner=app.adventure_runner=app.swole_runner=None
        app.routine_state=Value();app.subscription_busy=True;app._command_launch=lambda *_:self.fail("overlapping launch")
        CommandCenterMixin._command_tick(app)
        self.assertIn("Waiting",app.routine_state.text)

    def test_builtin_profile_cannot_be_edited_through_routine_buttons(self):
        app=SimpleNamespace(routine_profile=SimpleNamespace(get=lambda:"Best Daily"),routine_steps=[{"command":"daily-run"}])
        app._routine_editable=lambda:CommandCenterMixin._routine_editable(app)
        with patch("atcommander.command_center.messagebox.showinfo"):
            for action in ("_routine_add","_routine_remove","_routine_clear","_routine_daily"):
                getattr(CommandCenterMixin,action)(app)
        self.assertEqual(app.routine_steps,[{"command":"daily-run"}])

    def test_builtin_reload_discards_any_appended_command_before_run(self):
        app=SimpleNamespace(routine_profile=SimpleNamespace(get=lambda:"Best Daily"),
            routine_steps=[{"command":"shop-purchase","args":{"budget":100000}}],
            automation_profiles=SimpleNamespace(load=lambda name:[{"command":"daily-run","args":{}}]),
            _routine_daily_options=lambda:{"nixon_budget":1000},_routine_render=lambda:None)
        CommandCenterMixin._routine_sync_daily(app)
        self.assertEqual([row["command"] for row in app.routine_steps],["daily-run"])
        self.assertEqual(json.loads(app.routine_steps[0]["args"]["options"]),{"nixon_budget":1000})

    def test_custom_profile_remains_editable(self):
        app=SimpleNamespace(routine_profile=SimpleNamespace(get=lambda:"My routine"))
        with patch("atcommander.command_center.messagebox.showinfo") as prompt:
            self.assertTrue(CommandCenterMixin._routine_editable(app))
        prompt.assert_not_called()

    def test_daily_display_preserves_incomplete_and_unavailable_reasons(self):
        text=daily_result_text({"steps":[{"label":"Complete required Arena battles","args":{"internal_field":1}}],
            "skipped":[{"label":"Login reward","reason":"Eligibility unavailable"}],
            "unresolved":[{"quest":"Daily Research","remaining":2,"reason":"Requirement not mapped"}]})
        self.assertIn("Complete required Arena battles",text)
        self.assertIn("Eligibility unavailable",text)
        self.assertIn("Daily Research (2 remaining): Requirement not mapped",text)
        self.assertNotIn("internal_field",text)

    def test_new_account_secrets_are_only_in_vault(self):
        vault=type("Vault",(),{"set_password":lambda obj,*args:setattr(obj,"saved",args)})()
        registry=AccountRegistry(store=self.store,vault=vault)
        registry.save("Alt",self.client.credentials)
        self.assertNotIn("test-password-private",str(self.store.read()))
        self.assertIn("test-password-private",vault.saved[2])

    def test_empty_action_stops_without_confirmation(self):
        self.client.call=lambda *_:{}
        with self.assertRaises(CommandUnavailable):self.service.execute("deck-rename",{"deck":1,"name":"Unknown"})

    def test_daily_upgrade_without_daily_quest_touches_nothing(self):
        answer=self.service.execute("daily-upgrade")
        self.assertIn("No card was changed",answer.message)
        self.assertNotIn("upgradeUnit",[name for name,_ in self.client.calls])

    def test_research_ad_checks_current_chamber_views(self):
        from atcommander.ad_ui import rewarded_benefit_changed
        old={"research_ad_speed_ups":[{"slot_index":0,"views":1,"max_views":3}]}
        new={"research_ad_speed_ups":[{"slot_index":0,"views":2,"max_views":3}]}
        self.assertTrue(rewarded_benefit_changed(old,new,"research"))

    def test_all_observed_bender_names_have_handlers(self):
        names='''buy-basic buy-box buy-converter buy-cost buy-deal buy-epicbasic buy-herocrate buy-mastery buy-max buy-mombox buy-momfree buy-rarebasic buy-sep buy-turdpack card-combo card-info channel-create channel-remove deck-build deck-drop deck-hero deck-list deck-map deck-rename deck-restore deck-save deck-select deck-share deck-swap events-all events-current events-upcoming guild-accept guild-activity guild-compare guild-configure guild-deck guild-demote guild-info guild-inventory guild-invite guild-kick guild-leave guild-mastery guild-promote guild-promoteleader guild-reject guild-roster guild-seen guild-set guild-top item-choose item-list item-use play play-skip play-status recycle register register-guild research-ad research-collect research-deck research-info research-missing rumble-deck rumble-match rumble-opp rumble-rank rumble-results rumble-stats siege-deck siege-defense siege-islands siege-join siege-match siege-move siege-opp siege-rank subscribe unregister unsubscribe user-account user-ad user-claim user-heroes user-inventory user-mail user-mastery user-profile user-quest user-rank user-squad'''.split()
        self.assertEqual(len(names),93)
        for name in names:self.assertEqual(self.service.spec(name).name,name)

    def test_mastery_reads_owned_levels_and_loose_items(self):
        self.service.context._catalogs["card"]={"15001":{"id":15001,"name":"Rogu Combo","set":1001}}
        self.client.data["user_combo_mastery"]={"15001":{"level":2}}
        self.client.data["user_items"]={"2015001":{"number":7}}
        answer=self.service.execute("user-mastery")
        self.assertEqual(answer.rows[0]["cm_level"],2)
        self.assertEqual(answer.rows[0]["loose_count"],7)
        self.assertNotIn("getComboMasteries",[name for name,_ in self.client.calls])

    def test_card_info_resolves_two_inputs_in_either_order(self):
        self.service.context._catalogs["card"]={"22":{"name":"Peter"},"23":{"name":"Pantry Guns"},"15001":{"name":"Actual Combo","set":1001}}
        self.service.context._catalogs["combo"]={"22~23":"15001|"}
        for query in ("pantry guns | peter","Peter | Pantry Guns"):
            answer=self.service.execute("card-info",{"query":query})
            self.assertEqual(answer.rows[0]["name"],"Actual Combo")
            self.assertEqual(answer.rows[0]["recipes"][0]["card1"],"22")

    def test_public_guild_deck_needs_no_registered_accounts(self):
        self.client.data["faction"]={"members":[{"name":"Guildmate","user_id":456}]}
        self.client.call=lambda name,args:{"user_profile_info":{"decks_data":{"active_deck":0,"decks":{"0":{"name":"Public","units":[{"unit_id":22,"unit_index":123,"level":3}]}}}}}
        answer=self.service.execute("guild-deck",{"map":True})
        self.assertEqual(answer.rows[0]["cards"][0]["name"],"Rogu")
        self.assertNotIn("unit_index",str(answer.rows))

    def test_public_profile_is_reused_between_guild_tools(self):
        from atcommander.public_profiles import public_profile
        self.client.call=lambda name,args:(self.client.calls.append((name,args)) or {"user_profile_info":{"pvp_data":{"rating":2213}}})
        public_profile(self.service,456);public_profile(self.service,456)
        self.assertEqual(len(self.client.calls),1)

    def test_command_reads_are_paced_but_mutations_are_not(self):
        self.service.context._read_interval=8
        with patch("atcommander.local_commands.time.monotonic",side_effect=[100,102,108]),patch("atcommander.local_commands.time.sleep") as sleep:
            self.service.call("getGuildFeed")
            self.service.call("setDeckName",{"deck_id":1,"name":"Exact"})
            self.service.call("getUserProfile",{"target_user_id":456})
        sleep.assert_called_once_with(6)

    def test_inventory_matches_show_and_two_display_skill_names(self):
        self.service.context._catalogs["card"]={"22":{"name":"Rogu","type":"2","rarity":4,"skill":[{"id":"strike","x":5},{"id":"berserk","x":5}]}}
        args={"show":"American Dad","skill":"Punch","skill2":"Craze"}
        self.assertEqual(len(self.service.execute("user-inventory",args).rows),1)
        self.assertEqual(self.service.execute("user-inventory",{**args,"show":"Family Guy"}).rows,[])

    def test_inventory_object_filter_accepts_item_catalog_rows(self):
        self.service.context._catalogs["card"]={"22":{"name":"Object","card_type":"item","rarity":4}}
        self.assertEqual(len(self.service.execute("user-inventory",{"type":"object"}).rows),1)

    def test_guild_inventory_includes_current_account(self):
        self.service.context._catalogs["card"]={"22":{"name":"Rogu","rarity":4}}
        with patch("atcommander.account_registry.AccountRegistry.list",return_value={}):
            answer=self.service.execute("guild-inventory",{"query":"Rogu"})
        self.assertEqual(answer.rows[0]["alias"],"Current account")
        self.assertEqual(answer.rows[0]["data"][0]["name"],"Rogu")

    def test_repeated_choice_items_confirm_before_next_open(self):
        self.client.data["user_items"]={"200014":{"number":3000}}
        def use(iid,count):
            self.client.calls.append(("useItem",{"item_id":iid,"quantity":count}))
            self.client.data["user_items"][iid]["number"]-=1000
            return {"result":True,"active_choices":[{"type":8,"options":[{"id":1,"give_card":[{"id":23}]},{"id":2,"give_card":[{"id":22}]}]}]}
        self.client.use_item=use
        answer=self.service.execute("item-choose",{"item":"superlegstone","quantity":"3"})
        sequence=[name for name,_ in self.client.calls if name!="init"]
        self.assertEqual(sequence,["useItem","confirmUserChoice"]*3)
        self.assertIn("resolved 3",answer.message)
        self.assertTrue(all(args["choice"]=="2" for name,args in self.client.calls if name=="confirmUserChoice"))

    def test_adventure_refill_alias_uses_current_inventory_name(self):
        self.client.data["user_items"]={"1002":{"number":2}}
        self.service.context._catalogs["item"]={"1002":{"name":"Energy Refill +5"}}
        self.client.use_item=lambda iid,count:(self.client.calls.append(("useItem",{"item_id":iid,"quantity":count})) or {"result":True})
        self.service.execute("item-use",{"item":"adventure5","quantity":"1"})
        self.assertEqual(self.client.calls[-1],("useItem",{"item_id":"1002","quantity":1}))

    def test_swole_status_uses_fresh_account_tickets(self):
        from atcommander.challenge_state import current_swole
        self.client.data["time"]=100
        self.client.data["active_events"]={"9":{"id":9,"name":"Swole","start_time":1,"end_time":200,"challenge_data":{"id":7,"level":92,"energy":{"current_value":1,"battle_cost":1}}}}
        self.assertEqual(current_swole(self.client.data)["current"],1)
        answer=self.service.execute("play-status")
        self.assertEqual(answer.rows[-1]["mode"],"Swole")
        self.assertEqual(answer.rows[-1]["energy"],1)

    def test_deck_hero_validates_live_unlock_without_static_catalog(self):
        self.client.hero_catalog=lambda:(_ for _ in ()).throw(RuntimeError("Missing catalog"))
        def call(name,args=None):
            self.client.calls.append((name,args))
            if name=="getHeroSquad":return {"user_hero_squad":[{"hero_id":1001,"unlocked":1}]}
            return {"result":True}
        self.client.call=call
        self.service.execute("deck-hero",{"deck":1,"hero":"Peter"})
        self.assertEqual(self.client.calls[-1],("setDeckCommander",{"deck_id":1,"commander_id":"1001"}))
