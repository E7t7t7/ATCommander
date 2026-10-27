import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from atcommander.app import ATCommanderApp


class Value:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class EarlySelectorTests(unittest.TestCase):
    def app(self):
        app = object.__new__(ATCommanderApp)
        app.settings = {}; app.events = queue.Queue()
        app.current_snapshot = {"fixture-resource": 42}
        app.deck_rows = []; app.hero_rows = []
        bindings = (("deck_combo", "deck_var"), ("rumble_deck_combo", "rumble_deck"),
            ("siege_deck_combo", "siege_deck"), ("rumble_offense_deck_combo", "rumble_offense_deck"),
            ("siege_offense_deck_combo", "siege_offense_deck"), ("challenge_offense_deck_combo", "challenge_offense_deck"),
            ("hero_combo", "hero_var"), ("rumble_offense_hero_combo", "rumble_offense_hero"),
            ("siege_offense_hero_combo", "siege_offense_hero"), ("challenge_offense_hero_combo", "challenge_offense_hero"),
            ("swole_target_combo", "swole_target"))
        for widget, variable in bindings:
            setattr(app, widget, {})
            setattr(app, variable, Value("Select hero…" if variable == "swole_target" else "Current / unchanged"))
        return app

    def test_authoritative_selector_event_is_queued_before_catalogs(self):
        app = self.app()
        client = Mock()
        client.init.return_value = {"user_data": {}, "time": 1000}
        client.get_user_account.return_value = {}
        client.get_exact_decks.return_value = {"user_decks": {"7": {"deck_id": "7", "name": "Fixture Deck"}}}
        client.get_hero_squad.return_value = {"user_hero_squad": {
            "a": {"hero_id": "1001", "unlocked": 1, "training_level": 4},
            "b": {"hero_id": "5016", "unlocked": 0}}}
        client.get_boost_level.return_value = {}; client.get_store_snapshot.return_value = {}
        client.get_guild_siege_status.return_value = {}
        workers = []; seen = []
        def catalog():
            event = app.events.get_nowait(); seen.append(event)
            self.assertEqual("selectors", event["kind"])
            self.assertEqual("7", event["decks"][0]["id"])
            self.assertEqual(["1001"], [row["id"] for row in event["heroes"]])
            self.assertEqual("Peter", event["heroes"][0]["name"])
            app._handle_event(event)
            self.assertIn("Peter  [ID 1001]", app.swole_target_combo["values"])
            self.assertEqual({"fixture-resource": 42}, app.current_snapshot)
            return {}
        client.card_catalog.side_effect = catalog
        for name in ("item_catalog", "hero_catalog", "campaign_catalog", "mission_catalog"):
            getattr(client, name).return_value = {}
        with patch("atcommander.app.ATClient", return_value=client), patch("atcommander.app.load_credentials"), \
             patch("atcommander.app.threading.Thread", side_effect=lambda *, target, **kw: SimpleNamespace(start=lambda: workers.append(target))):
            app._refresh_account_metadata(); workers.pop()()
        self.assertEqual(1, len(seen))
        client.get_exact_decks.assert_called_once()
        client.get_hero_squad.assert_called_once()
        self.assertEqual("4", app.hero_rows[0]["training_level"])

    def test_later_name_enrichment_preserves_selected_hero_and_deck_ids(self):
        app = self.app()
        app._handle_event({"kind": "selectors", "decks": [{"id": "7", "name": "Fixture Deck"}],
                          "heroes": [{"id": "1001", "name": "Hero 1001"}]})
        app.deck_var.set("Fixture Deck  [ID 7]")
        app.hero_var.set("Hero 1001  [ID 1001]")
        app.swole_target.set("Hero 1001  [ID 1001]")
        app._apply_account_selectors([{"id": "7", "name": "Renamed Deck"}], [{"id": "1001", "name": "Peter"}])
        self.assertEqual("Renamed Deck  [ID 7]", app.deck_var.get())
        self.assertEqual("Peter  [ID 1001]", app.hero_var.get())
        self.assertEqual("Peter  [ID 1001]", app.swole_target.get())
        self.assertEqual("7", app._selected_id(app.deck_var.get(), app.deck_rows))
        self.assertEqual("1001", app._selected_id(app.swole_target.get(), app.hero_rows))
        self.assertEqual({"fixture-resource": 42}, app.current_snapshot)

    def test_hero_only_selector_update_preserves_deck_list(self):
        app = self.app(); app.deck_rows = [{"id": "7", "name": "Fixture Deck"}]
        app._handle_event({"kind": "selectors", "heroes": [{"id": "1001", "name": "Peter"}]})
        self.assertEqual("7", app.deck_rows[0]["id"])
        self.assertEqual({"fixture-resource": 42}, app.current_snapshot)

    def test_renamed_slot_refreshes_all_battle_and_builder_selectors_by_stable_id(self):
        app=self.app();app.builder_slot_combo={};app.builder_slot=Value('Old name  [ID 24]')
        variables=('deck_var','rumble_deck','siege_deck','rumble_offense_deck','siege_offense_deck','challenge_offense_deck','builder_slot')
        for name in variables:getattr(app,name).set('Old name  [ID 24]')
        app._apply_account_selectors([{'id':'24','name':'My new name','resolved':'1'}])
        for name in variables:self.assertEqual(getattr(app,name).get(),'My new name  [ID 24]')


if __name__ == "__main__": unittest.main()
