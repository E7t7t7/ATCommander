"""Offline Events checkbox and persistence checks; no battles or Tk windows."""
from contextlib import ExitStack
from pathlib import Path
from queue import Queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from atcommander import app as app_module,settings
from atcommander.app import ATCommanderApp


class Value:
    def __init__(self,value=None,**_kwargs):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value


class MeleeUISettingsTests(unittest.TestCase):
    def build_events(self,saved):
        app=object.__new__(ATCommanderApp);app.settings=saved;app.events_tab=Mock();app._logbox=Mock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(app_module.tk,"StringVar",side_effect=Value))
            stack.enter_context(patch.object(app_module.tk,"BooleanVar",side_effect=Value))
            for name in ("Frame","Label","Combobox","Button","Entry"):
                stack.enter_context(patch.object(app_module.ttk,name))
            checkbox=stack.enter_context(patch.object(app_module.ttk,"Checkbutton"))
            ATCommanderApp._build_events(app)
        melee=next(call for call in checkbox.call_args_list if call.kwargs.get("text")=="Character first (Melee only)")
        return app,melee

    def test_checkbox_is_visible_off_by_default_and_uses_immediate_save_callback(self):
        app,checkbox=self.build_events({})
        self.assertFalse(app.melee_character_first_var.get())
        self.assertIs(checkbox.kwargs["variable"],app.melee_character_first_var)
        self.assertEqual(checkbox.kwargs["command"],app._save_melee_character_first)

    def test_only_explicit_saved_true_enables_checkbox(self):
        for saved,expected in ((True,True),(False,False),("false",False),("true",False),(None,False),(1,False)):
            with self.subTest(saved=saved):
                app,_=self.build_events({"melee_character_first":saved})
                self.assertIs(app.melee_character_first_var.get(),expected)

    def test_checkbox_round_trip_preserves_other_user_preferences(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(settings,"APP_DIR",Path(directory)),patch.object(settings,"FILE",Path(directory)/"settings.json"):
            settings.save_settings({"fixture_preference":"keep","move_delay":.25,"arena_continuous":True})
            app=SimpleNamespace(settings=settings.load_settings(),melee_character_first_var=Value(True))
            ATCommanderApp._save_melee_character_first(app)
            saved=settings.load_settings()
            self.assertIs(saved["melee_character_first"],True);self.assertIs(app.settings["melee_character_first"],True)
            self.assertEqual(saved["fixture_preference"],"keep");self.assertEqual(saved["move_delay"],.25)
            self.assertIs(saved["arena_continuous"],True)
            rebuilt,_=self.build_events(saved);self.assertIs(rebuilt.melee_character_first_var.get(),True)
            app.melee_character_first_var.set(False);ATCommanderApp._save_melee_character_first(app)
            self.assertIs(settings.load_settings()["melee_character_first"],False)

    def test_old_or_missing_settings_keep_original_strategy(self):
        self.assertIs(settings.DEFAULTS["melee_character_first"],False)
        with tempfile.TemporaryDirectory() as directory,patch.object(settings,"FILE",Path(directory)/"settings.json"):
            self.assertIs(settings.load_settings()["melee_character_first"],False)
            settings.FILE.write_text('{"battle_delay": 2}',encoding="utf-8")
            saved=settings.load_settings()
            self.assertIs(saved["melee_character_first"],False);self.assertEqual(saved["battle_delay"],2)

    def challenge_app(self,enabled=None,name="Fixture Melee"):
        app=object.__new__(ATCommanderApp);app.event_runner=None;app.events=Queue()
        app._command_blocks_runner=Mock(return_value=False)
        app._selected_challenge_row=Mock(return_value={"id":"94042","challenge_id":"5502","level":14,"state":"Active","name":name})
        app._selected_id=Mock(side_effect=["deck-7","hero-2"]);app._event_log=Mock()
        app.deck_rows=[];app.hero_rows=[]
        for key,value in (("challenge_offense_deck","Deck seven"),("challenge_offense_hero","Hero two"),
                          ("delay_var",.1),("battle_delay_var",1.5),("challenge_logic","Item → Combo (default)"),
                          ("auto_ads_var",False)):
            setattr(app,key,Value(value))
        if enabled is not None:app.melee_character_first_var=Value(enabled)
        return app

    def test_gui_forwards_optional_preference_without_changing_existing_start_inputs(self):
        for enabled in (None,False,True):
            app=self.challenge_app(enabled)
            with self.subTest(enabled=enabled),patch.object(app_module,"ChallengeBattleRunner") as factory, \
                 patch.object(app_module,"ATClient") as client:
                ATCommanderApp.start_challenge(app)
            self.assertIs(factory.call_args.kwargs["melee_character_first"],enabled is True)
            self.assertEqual(factory.call_args.kwargs["level"],14)
            self.assertEqual(factory.call_args.args[:3],("94042","5502","Fixture Melee"))
            self.assertEqual(factory.call_args.args[3:9],(.1,1.5,"Item → Combo (default)","deck-7","hero-2",False))
            factory.return_value.start.assert_called_once();client.assert_not_called()

    def test_preference_remains_runner_guarded_when_selecting_other_event_modes(self):
        for name in ("Hangover Clash","Finger Cutter Challenge"):
            app=self.challenge_app(True,name)
            with self.subTest(name=name),patch.object(app_module,"ChallengeBattleRunner") as factory:
                ATCommanderApp.start_challenge(app)
            self.assertEqual(factory.call_args.args[2],name)
            self.assertIs(factory.call_args.kwargs["melee_character_first"],True)
            self.assertEqual(factory.call_args.args[5],"Item → Combo (default)")

    def test_full_settings_save_keeps_checkbox_value(self):
        app=object.__new__(ATCommanderApp);app.settings={};app.deck_rows=[];app.hero_rows=[]
        app._selected_id=Mock(return_value=None);app._selected_island_id=Mock(return_value=None)
        app._selected_siege_target_id=Mock(return_value=None)
        variables=("delay_var","battle_delay_var","limit_var","reserve_var","continuous_var","auto_ads_var",
            "arena_bypass_turn_limit_var","deck_var","hero_var","rumble_deck","rumble_offense_deck",
            "rumble_offense_hero","siege_deck","siege_offense_deck","siege_offense_hero","siege_island",
            "siege_target_a","siege_target_b","siege_target_c","siege_fallback_any","siege_poll",
            "challenge_offense_deck","challenge_offense_hero","rumble_logic","siege_logic","challenge_logic",
            "swole_poll","adventure_limit","adventure_reserve","adventure_continuous","auto_item_choice")
        for name in variables:setattr(app,name,Value(False))
        app.nixon_workers=Value(6);app.melee_character_first_var=Value(True)
        with patch.object(app_module,"load_settings",return_value={"fixture_preference":"keep"}), \
             patch.object(app_module,"save_settings") as save:
            ATCommanderApp._save_settings(app)
        self.assertIs(save.call_args.args[0]["melee_character_first"],True)
        self.assertEqual(save.call_args.args[0]["fixture_preference"],"keep")


if __name__=="__main__":unittest.main()
