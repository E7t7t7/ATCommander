import unittest
from atcommander.catalog_parser import normalize_static_catalog

class StaticCatalogTests(unittest.TestCase):
    def test_mission_and_campaign_specific_ids(self):
        self.assertEqual({"123":{"mission_id":123,"campaign_id":90}},
                         normalize_static_catalog({"mission_data":[{"mission_id":123,"campaign_id":90}]},"mission_data"))
        self.assertEqual({"90":{"campaign_id":90,"name":"Event"}},
                         normalize_static_catalog([{"campaign_id":90,"name":"Event"}],"campaign_data"))

    def test_game_keyed_map_and_card_wrapper(self):
        rows={"25351":{"name":"Combo","set":"1001"}}
        self.assertEqual(rows,normalize_static_catalog({"data":{"card_data":rows}},"card_data"))

    def test_error_or_config_is_not_catalog(self):
        for payload in ({"result":False,"mission_data":[{"id":123}]},
                        {"config":{"1":{"name":"Wrong"}}},
                        {"time":1,"version":"test","data_cache":[]}):
            self.assertEqual({},normalize_static_catalog(payload,"mission_data"))

    def test_nested_recipe_tables_are_not_rows(self):
        rows={"25351":{"id":25351,"name":"Combo","recipe":{"50000":{"id":50000}}}}
        self.assertEqual(rows,normalize_static_catalog(rows,"card_data"))

    def test_wrong_catalog_specific_id_is_not_a_row(self):
        self.assertEqual({},normalize_static_catalog([{"campaign_id":90,"name":"Campaign"}],"mission_data"))

    def test_single_explicit_row(self):
        row={"mission_id":123,"campaign_id":90}
        self.assertEqual({"123":row},normalize_static_catalog({"mission_data":row},"mission_data"))

if __name__=="__main__":unittest.main()
