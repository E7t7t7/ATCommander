import csv
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atcommander.command_store import CommandStore
from atcommander import guild_config as guild


ROSTER=[{"name":"Amy","role":"member","last_seen":1000,"sfc_rating":2100,"user_id":"synthetic-private-account"},
        {"name":"Bender","role":"officer","sfc_rating":2400}]
PLAN=[{"member":"Amy","island":2,"deck":3,"notes":"Use an Armed defense."}]


class GuildTestSetup:
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.store=CommandStore(Path(self.folder.name)/"preferences.json")


class GuildPreferenceTests(GuildTestSetup,unittest.TestCase):
    def test_preferences_are_isolated_by_real_guild_and_omit_unrelated_data(self):
        self.store.update("unrelated_private",{"password":"synthetic-private-value"})
        guild.save_guild_preferences(self.store,77,{"name":"First guild","assignments":PLAN},roster=ROSTER)
        guild.save_guild_preferences(self.store,78,{"name":"Second guild"})
        self.assertEqual(guild.get_guild_preferences(self.store,77)["name"],"First guild")
        self.assertEqual(guild.get_guild_preferences(self.store,78)["assignments"],[])
        self.assertNotIn("synthetic-private-value",str(guild.get_guild_preferences(self.store,77)))

    def test_omitted_fields_are_preserved_and_no_argument_view_does_not_write(self):
        guild.save_guild_preferences(self.store,77,{"name":"Guild","defense_plan":"Old notes","assignments":PLAN},roster=ROSTER)
        changed=guild.save_guild_preferences(self.store,77,{"name":"New name","defense_plan":None})
        self.assertEqual(changed["defense_plan"],"Old notes")
        self.assertEqual(changed["assignments"],PLAN)
        with patch.object(self.store,"update") as update:
            shown=guild.save_guild_preferences(self.store,77,{"name":None})
        update.assert_not_called();self.assertEqual(shown,changed)

    def test_explicit_blank_and_empty_plan_clear_only_selected_settings(self):
        guild.save_guild_preferences(self.store,77,{"name":"Guild","defense_plan":"Notes","assignments":PLAN},roster=ROSTER)
        updated=guild.save_guild_preferences(self.store,77,{"defense_plan":"","assignments":[]},roster=ROSTER)
        self.assertEqual(updated["name"],"Guild")
        self.assertEqual(updated["defense_plan"],"")
        self.assertEqual(updated["assignments"],[])

    def test_invalid_assignment_does_not_partially_save_other_settings(self):
        original=guild.save_guild_preferences(self.store,77,{"name":"Guild"})
        with self.assertRaises(ValueError):
            guild.save_guild_preferences(self.store,77,{"name":"Changed","assignments":[{"member":"Unknown","island":2}]},roster=ROSTER)
        self.assertEqual(guild.get_guild_preferences(self.store,77),original)

    def test_columns_and_formats_are_validated_without_copying_private_keys(self):
        saved=guild.save_guild_preferences(self.store,77,{"columns":"name,cards,notes","report_format":"csv"})
        self.assertEqual(saved["columns"],["name","cards","notes"])
        for change in ({"columns":["name","name"]},{"columns":[["name"]]},
                       {"columns":["password"]},{"report_format":[]},{"unknown":"private"}):
            with self.subTest(change=change),self.assertRaises(ValueError):guild.save_guild_preferences(self.store,77,change)

    def test_corrupt_optional_saved_fields_fall_back_to_safe_public_defaults(self):
        self.store.update("guild_preferences_by_id",{"77":{"name":{"password":"synthetic-private-value"},"columns":[[1]],"report_format":[],"assignments":[{"password":"synthetic-private-value"}]}})
        saved=guild.get_guild_preferences(self.store,77)
        self.assertEqual(saved["name"],"")
        self.assertEqual(saved["columns"],list(guild.DEFAULT_COLUMNS))
        self.assertEqual(saved["assignments"],[])
        self.assertNotIn("synthetic-private-value",str(saved))

    def test_assignment_identity_is_exact_unique_and_never_a_member_substring(self):
        for plan,roster in (([{"member":"Am","island":2}],ROSTER),
                            ([{"member":"Amy","island":2},{"member":"AMY","island":3}],ROSTER),
                            ([{"member":"Amy","island":2}],[{"name":"Amy"},{"name":"AMY"}])):
            with self.subTest(plan=plan),self.assertRaises(ValueError):guild.validate_assignments(plan,roster)
        self.assertEqual(guild.validate_assignments([{**PLAN[0],"member":"amy"}],ROSTER)[0]["member"],"amy")

    def test_assignment_shape_numbers_and_limits_are_explicit(self):
        for plan in ([{**PLAN[0],"user_id":"private"}],[{**PLAN[0],"island":True}],
                     [{**PLAN[0],"island":0}],[{**PLAN[0],"deck":"3.5"}],PLAN*101):
            with self.subTest(plan=plan),self.assertRaises(ValueError):guild.validate_assignments(plan)

    def test_returned_preferences_and_validation_do_not_alias_inputs(self):
        original=deepcopy(PLAN)
        saved=guild.save_guild_preferences(self.store,77,{"assignments":PLAN},roster=ROSTER)
        saved["assignments"][0]["notes"]="Changed outside"
        self.assertEqual(PLAN,original)
        self.assertEqual(guild.get_guild_preferences(self.store,77)["assignments"],original)


class GuildPlanImportExportTests(GuildTestSetup,unittest.TestCase):
    def test_csv_roundtrip_preserves_guild_assignment_and_multiline_notes(self):
        plan=[{**PLAN[0],"notes":"Armed, then Fighter\nReserve deck"}]
        prefs=guild.save_guild_preferences(self.store,77,{"assignments":plan},roster=ROSTER)
        artifact=guild.export_defense_plan(prefs,self.folder.name)
        restored=guild.parse_defense_plan(Path(artifact["path"]).read_text(encoding="utf-8-sig"),guild_id=77,roster=ROSTER)
        self.assertEqual(restored,plan)
        self.assertEqual(artifact["assignments"],1)

    def test_json_roundtrip_and_wrong_guild_rejection(self):
        prefs=guild.save_guild_preferences(self.store,77,{"assignments":PLAN},roster=ROSTER)
        artifact=guild.export_defense_plan(prefs,self.folder.name,plan_format="json")
        text=Path(artifact["path"]).read_text(encoding="utf-8")
        self.assertEqual(guild.parse_defense_plan(text,plan_format="json",guild_id=77,roster=ROSTER),PLAN)
        with self.assertRaisesRegex(ValueError,"different guild"):
            guild.parse_defense_plan(text,plan_format="json",guild_id=78,roster=ROSTER)

    def test_import_defaults_to_preview_then_explicit_apply_saves_locally(self):
        path=Path(self.folder.name)/"plan.csv";path.write_text("guild_id,member,island,deck,notes\n77,Amy,2,3,New notes\n",encoding="utf-8")
        preview=guild.import_defense_plan(self.store,77,path,roster=ROSTER)
        self.assertFalse(preview["applied"])
        self.assertEqual(guild.get_guild_preferences(self.store,77)["assignments"],[])
        applied=guild.import_defense_plan(self.store,77,path,roster=ROSTER,apply=True)
        self.assertTrue(applied["applied"])
        self.assertEqual(guild.get_guild_preferences(self.store,77)["assignments"],applied["assignments"])

    def test_malformed_or_extra_csv_columns_and_rows_are_rejected(self):
        for text in ("member,deck\nAmy,2\n","member,island,password\nAmy,2,private\n",
                     "member,island,island\nAmy,2,3\n","member,island\nAmy,2,extra\n",
                     "member,island,notes\nAmy,2\n"):
            with self.subTest(text=text),self.assertRaises(ValueError):guild.parse_defense_plan(text,roster=ROSTER)

    def test_csv_wrong_guild_and_non_roster_members_never_apply(self):
        for text in ("guild_id,member,island\n78,Amy,2\n","member,island\nUnknown,2\n"):
            path=Path(self.folder.name)/"plan.csv";path.write_text(text,encoding="utf-8")
            with self.subTest(text=text),self.assertRaises(ValueError):guild.import_defense_plan(self.store,77,path,roster=ROSTER,apply=True)
            self.assertEqual(guild.get_guild_preferences(self.store,77)["assignments"],[])

    def test_spreadsheet_safe_names_and_notes_roundtrip_including_apostrophes(self):
        roster=[{"name":"=FormulaName"},{"name":"'=LiteralName"}]
        plan=[{"member":"=FormulaName","island":2,"notes":"=HYPERLINK(\"bad\")"},
              {"member":"'=LiteralName","island":3,"notes":"'=literal note"}]
        artifact=guild.export_defense_plan({"guild_id":77,"assignments":plan},self.folder.name)
        text=Path(artifact["path"]).read_text(encoding="utf-8-sig")
        self.assertIn("'=FormulaName",text)
        self.assertEqual(guild.parse_defense_plan(text,guild_id=77,roster=roster),plan)

    def test_import_size_format_and_read_errors_do_not_expose_local_file_details(self):
        path=Path(self.folder.name)/"synthetic-private-path.csv"
        with self.assertRaises(ValueError) as error:guild.import_defense_plan(self.store,77,path,roster=ROSTER)
        self.assertNotIn("synthetic-private-path",str(error.exception))
        with self.assertRaises(ValueError):guild.parse_defense_plan("x"*(256*1024+1),roster=ROSTER)
        with self.assertRaises(ValueError):guild.parse_defense_plan("",plan_format="xlsx",roster=ROSTER)


class GuildReportTests(GuildTestSetup,unittest.TestCase):
    def test_report_whitelists_fields_and_joins_current_decks_and_local_plan(self):
        roster=deepcopy(ROSTER);roster[0]["password"]="synthetic-private-value"
        rows=guild.guild_report_rows(roster,deck_rows=[{"member":"Amy","deck":9,"card_count":25,"active":True,"cards":[{"unit_index":"private-instance"}]}],preferences={"assignments":PLAN})
        self.assertEqual((rows[0]["cards"],rows[0]["defense_island"],rows[0]["deck"]),(25,2,3))
        self.assertEqual(rows[0]["notes"],PLAN[0]["notes"])
        for value in ("synthetic-private-value","synthetic-private-account","private-instance"):
            self.assertNotIn(value,str(rows))

    def test_html_is_dark_local_filterable_and_escapes_member_and_notes(self):
        roster=[{"name":"<script>privateScript()</script>","notes":"<img src=x>"}]
        artifact=guild.export_guild_report(roster,self.folder.name,preferences={"defense_plan":"<script>note()</script>"})
        text=Path(artifact["path"]).read_text(encoding="utf-8")
        self.assertIn("background:#0b1020",text)
        self.assertIn('id="filter"',text)
        self.assertNotIn("<script>privateScript()",text)
        self.assertNotIn("<script>note()",text)
        self.assertNotIn("<img src=x>",text)
        self.assertNotIn("https://",text)

    def test_csv_export_is_safe_for_text_formulas_and_preserves_numeric_statistics(self):
        artifact=guild.export_guild_report([{"name":"=FormulaName","sfc_rating":2400,"notes":"@danger"}],self.folder.name,report_format="csv")
        with Path(artifact["path"]).open(encoding="utf-8-sig",newline="") as handle:rows=list(csv.DictReader(handle))
        self.assertEqual(rows[0]["name"],"'=FormulaName")
        self.assertEqual(rows[0]["notes"],"'@danger")
        self.assertEqual(rows[0]["sfc_rating"],"2400")

    def test_json_report_uses_selected_public_columns_only(self):
        artifact=guild.export_guild_report(ROSTER,self.folder.name,report_format="json",preferences={"columns":["name","sfc_rating"]})
        value=json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
        self.assertEqual(set(value["members"][0]),{"name","sfc_rating"})
        self.assertNotIn("synthetic-private-account",str(value))

    def test_empty_or_invalid_report_does_not_create_artifact(self):
        for roster,prefs in (([],{}),(ROSTER,{"columns":["password"]}),(ROSTER,{"columns":[[1]]})):
            with self.subTest(roster=roster),self.assertRaises(ValueError):guild.export_guild_report(roster,self.folder.name,preferences=prefs)
        self.assertEqual(list(Path(self.folder.name).glob("guild-report-*")),[])


if __name__=="__main__":unittest.main()
