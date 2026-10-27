from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from atcommander.card_presentation import at_level, card_level_definitions, fusion_level, summary, stat_sheet_svg
from atcommander.command_store import CommandStore
from atcommander.local_commands import CommandInputError, CommandUnavailable
from atcommander.utility_commands import build_service
from tests.test_utility_commands import Client


def card():
    return {"id":"22","name":"Rogu","rarity":"4","card_type":"character","attack":"10","health":"24",
            "skill":[{"id":"outlast","x":"4"}],
            "upgrade":[{"level":"2","health":"25","skill":[{"id":"outlast","x":"5"}]},
                       {"level":"3","health":"27","attack":"11"},
                       {"level":"4","skill":[{"id":"berserk","x":"9"}]}]}


class CardPresentationTests(unittest.TestCase):
    def test_sparse_deltas_inherit_previous_stats_and_skills(self):
        source=card();before=deepcopy(source)
        level=at_level(source,3)
        self.assertEqual((level["attack"],level["health"]),("11","27"))
        self.assertEqual(summary(level)["skills"],["Recover 5"])
        self.assertEqual(source,before)
        self.assertNotIn("upgrade",level)

    def test_skill_replacement_and_alias_are_exact(self):
        source=card();source["upgrade"][2]={"level":"4","skills":[]}
        self.assertEqual(summary(at_level(source,4))["skills"],[])
        self.assertEqual(summary(at_level(card(),1))["skills"],["Recover 4"])
        with self.assertRaises(ValueError):at_level(card(),5)

    def test_fusion_uses_authoritative_table_not_level_guess(self):
        source={"4":{"3":{"rarity":"4","level":"3","fusion_level":1}}}
        definitions=card_level_definitions(source)
        self.assertEqual(fusion_level({"level":3},card(),definitions),1)
        self.assertIsNone(fusion_level({"level":18},card(),definitions))
        self.assertEqual(fusion_level({"level":18,"fusion_level":0},card(),definitions),0)
        self.assertIsNone(fusion_level({"level":18,"fusion_level":True},card(),definitions))

    def test_conflicting_fusion_definitions_are_unknown(self):
        definitions=card_level_definitions([{"rarity":4,"level":3,"fusion_level":0},
                                            {"rarity":4,"level":3,"fusion_level":1}])
        self.assertIsNone(fusion_level({"level":3},card(),definitions))

    def test_shareable_summary_has_no_instance_account_or_auth_fields(self):
        row={**at_level(card(),3),"unit_index":"private-instance","user_id":"private-account","clisig":"synthetic-digest"}
        text=str(summary(row))
        for value in ("private-instance","private-account","synthetic-digest"):
            self.assertNotIn(value,text)

    def test_svg_escapes_content_and_contains_no_external_assets(self):
        row=at_level(card(),3);row["name"]='<script>alert("bad")</script> & text'
        svg=stat_sheet_svg([row]);root=ET.fromstring(svg)
        self.assertEqual(root.tag,"{http://www.w3.org/2000/svg}svg")
        self.assertFalse(any(node.tag.endswith("script") or node.tag.endswith("image") for node in root.iter()))
        self.assertIn("Recover 5",svg)
        with self.assertRaises(ValueError):stat_sheet_svg([row]*51)


class CardCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.client=Client()
        self.client.card_catalog=lambda:{"22":card()}
        self.client.data["user_units"]={"3":{"unit_id":22,"unit_index":3,"level":3}}
        self.client.data["card_level_data"]={"4":{"3":{"rarity":"4","level":"3","fusion_level":1}}}
        self.service=build_service(self.client,store=CommandStore(Path(self.temp.name)/"state.json"))

    def test_inventory_shows_owned_level_and_current_skill_filter(self):
        answer=self.service.execute("user-inventory",{"display":"summary","fuse_level":1})
        self.assertEqual((answer.rows[0]["attack"],answer.rows[0]["health"]),("11","27"))
        self.assertEqual(answer.rows[0]["fusion"],"Dual")
        self.assertNotIn("unit_index",answer.rows[0])
        self.assertEqual(self.service.execute("user-inventory",{"skill":"Craze"}).rows,[])
        self.assertEqual([call[0] for call in self.client.calls],["init"])

    def test_unknown_fusion_is_explicitly_unavailable(self):
        del self.client.data["card_level_data"]
        with self.assertRaisesRegex(CommandUnavailable,"fusion definitions"):
            self.service.execute("user-inventory",{"fuse_level":2})
        self.assertEqual(len(self.service.execute("user-inventory").rows),1)

    def test_selected_card_level_and_invalid_level(self):
        answer=self.service.execute("card-info",{"query":"Rogu","level":3,"display":"summary"})
        self.assertEqual(answer.rows[0]["skills"],["Recover 5"])
        with self.assertRaises(CommandInputError):self.service.execute("card-info",{"query":"Rogu","level":5})

    def test_combo_summary_and_image_preserve_real_recipe(self):
        self.client.card_catalog=lambda:{"22":card(),"23":{"id":"23","name":"Object","card_type":"item","rarity":4},
            "25001":{"id":"25001","name":"Actual Combo","card_type":"combo","attack_multiplier":"1.5","health_multiplier":"2.0","skill":[{"id":"berserk","v":"0.25"}]}}
        self.client.combo_catalog=lambda:{"22~23":"25001|1"}
        with patch("atcommander.utility_commands.APP_DIR",Path(self.temp.name)):
            answer=self.service.execute("card-combo",{"query":"Actual Combo","display":"summary","image_type":"svg"})
        self.assertEqual(answer.rows[0]["card1_name"],"Rogu")
        self.assertEqual(answer.data["combo_definitions"][0]["attack_multiplier"],"1.5")
        svg=Path(answer.data["artifacts"][0]["path"]).read_text(encoding="utf-8")
        self.assertIn("Combo scaling: ATK 1.5 / HP 2.0",svg)
        self.assertEqual(self.client.calls,[])

    def test_export_is_local_sanitized_and_mutation_free(self):
        self.client.card_catalog=lambda:{"22":{**card(),"name":"Rogu test-password-private"}}
        with patch("atcommander.utility_commands.APP_DIR",Path(self.temp.name)):
            answer=self.service.execute("user-inventory",{"display":"summary","image_type":"svg"})
        artifact=Path(answer.data["artifacts"][0]["path"])
        self.assertTrue(artifact.is_file())
        svg=artifact.read_text(encoding="utf-8")
        self.assertNotIn("test-password-private",svg)
        self.assertNotIn("unit_index",svg)
        self.assertEqual([call[0] for call in self.client.calls],["init"])


if __name__=="__main__":unittest.main()
