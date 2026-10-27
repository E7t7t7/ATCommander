import unittest
from atcommander.adventure_catalog import build_adventure_campaign_rows, find_adventure_campaign

class AdventureCatalogTests(unittest.TestCase):
    def test_native_campaign_mission_list_maps_parentless_halloween_definitions(self):
        rows=build_adventure_campaign_rows({"9041":{"name":"Costume Party","missions":[9261,9262,9263]}},
            {str(mid):{"id":mid,"energy":1} for mid in (9261,9262,9263)},
            [{"name":"Halloween","marker":"9041"}])
        self.assertTrue(rows[0]["active"])
        self.assertEqual(["9261","9262","9263"],[row["id"] for row in rows[0]["missions"]])
        self.assertEqual([1,2,3],[row["stage"] for row in rows[0]["missions"]])

    def test_declared_stage_order_wins_over_numeric_mission_order(self):
        rows=build_adventure_campaign_rows({"9":{"name":"Native order","missions":[103,101,102]}},
            {str(mid):{"id":mid,"campaign_id":9,"order":mid} for mid in (101,102,103)})
        self.assertEqual(["103","101","102"],[row["id"] for row in rows[0]["missions"]])

    def test_missing_or_conflicting_definitions_never_fabricate_a_selector_id(self):
        rows=build_adventure_campaign_rows({"9":{"name":"Known map","missions":[101,102,999]}},
            {"101":{"id":101},"102":{"id":102,"campaign_id":10}})
        self.assertEqual(["101"],[row["id"] for row in rows[0]["missions"]])

    def test_anniversary_marker_cannot_be_a_mission(self):
        rows=build_adventure_campaign_rows({}, {},[{"name":"Anniversary","marker":"9040"}])
        self.assertEqual([],rows[0]["missions"])
        self.assertEqual("player_prefs_live_marker",rows[0]["source"])

    def test_real_catalog_resolves_marker_and_stages(self):
        rows=build_adventure_campaign_rows({"9040":{"name":"Anniversary"}},
             {"9101":{"mission_id":9101,"campaign_id":9040,"stage":1},"9102":{"mission_id":9102,"campaign_id":9040,"stage":2}},
             [{"name":"Anniversary","marker":"9040"}])
        self.assertEqual(1,len(rows));self.assertTrue(rows[0]["active"])
        self.assertEqual(["9101","9102"],[m["id"] for m in rows[0]["missions"]])

    def test_explicit_event_link_activates_named_campaign_without_duplicate(self):
        rows=build_adventure_campaign_rows({"90":{"name":"September Chapter"}},
             {"9001":{"id":9001,"campaign_id":90,"stage":1}},
             [{"name":"Anniversary","marker":"9040"}],
             [{"type":11,"name":"Anniversary","configs":{"campaign_id":90}}])
        self.assertEqual(1,len(rows));self.assertTrue(rows[0]["active"])

    def test_unknown_event_link_does_not_fabricate_stage(self):
        rows=build_adventure_campaign_rows({}, {},[],[{"type":11,"name":"New Event","mission_id":99999}])
        self.assertEqual([],rows[0]["missions"])

    def test_old_learned_string_shape_is_supported(self):
        rows=build_adventure_campaign_rows({}, {},[{"name":"Anniversary","marker":"9040"}],None,{"anniversary":"9101"})
        self.assertEqual("9101",rows[0]["missions"][0]["id"])

    def test_same_name_chapters_use_explicit_id(self):
        rows=[{"id":"9037","name":"Throwdown Celebration"},{"id":"9040","name":"Throwdown Celebration"}]
        self.assertEqual("9040",find_adventure_campaign(rows,"Seasonal / Event: Throwdown Celebration [Campaign 9040]")["id"])

    def test_old_unresolved_seasonal_markers_do_not_appear_active(self):
        rows=build_adventure_campaign_rows({}, {},[{"name":"New Year","marker":"12004"},{"name":"Anniversary","marker":"9040"}],
             [{"type":11,"name":"Anniversary"}])
        self.assertEqual(["Anniversary"],[r["name"] for r in rows])

if __name__=="__main__":unittest.main()
