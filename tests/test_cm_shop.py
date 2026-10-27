import unittest
from atcommander.cm_reviews import CMReviewEngine, collect_live_review_data


class ShopDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.engine=CMReviewEngine({
            "25351":{"id":"25351","name":"Test Tech Combo","set":"1001"},
            "35365":{"id":"35365","name":"Second Combo","set":"1001"},
            "15002":{"id":"15002","name":"Third Combo","set":"1001"},
            "25046":{"id":"25046","name":"Fourth Combo","set":"1001"},
        })

    def test_real_native_store_shape(self):
        rows=[]
        for sid,cid,currency in (("40",25351,"gems"),("42",35365,"stones"),
                                 ("41",15002,"gems"),("43",25046,"stones")):
            row={"id":sid,"name":"Mastery Token Pack","tabs":{"mastery":"1"},
                 "item":[{"id":int(f"2{cid:06d}"),"number":"10","quantity":"10"}],
                 "items":{"item":[{"number":"10","category":"2","set":"1001,1002"}]}}
            row.update({"hc_cost":"500"} if currency=="gems" else {"cost":{"item":"200008","num":"1000"}})
            rows.append(row)
        offers=self.engine.discover_shop({"store_items":rows})
        self.assertEqual(4,len(offers))
        by_id={row["store_id"]:row for row in offers}
        self.assertEqual(("25351",500,"Gems"),(by_id["40"]["combo_id"],by_id["40"]["cost"],by_id["40"]["currency"]))
        self.assertEqual(("35365",1000,"Mastery Stones"),(by_id["42"]["combo_id"],by_id["42"]["cost"],by_id["42"]["currency"]))

    def test_inventory_and_templates_are_not_shop_offers(self):
        payload={"user_combo_mastery":[{"combo_id":"25351","amount":500}],
                 "user_items":[{"id":"25351","cost":500}],
                 "store_items":[{"id":"40","tabs":{"mastery":"1"},"hc_cost":"500",
                                  "items":{"item":[{"id":2025351}]}}]}
        self.assertEqual([],self.engine.discover_shop(payload))

    def test_token_requires_mastery_context_and_catalog_match(self):
        rows=[{"id":"40","tabs":{"mastery":"1"},"hc_cost":"500","item":[{"id":2999999}]},
              {"id":"50","tabs":{"featured":"1"},"hc_cost":"500","item":[{"id":2025351}]}]
        self.assertEqual([],self.engine.discover_shop({"store_items":rows}))

    def test_legacy_explicit_offer_remains_supported(self):
        offers=self.engine.discover_shop({"mastery_shop":[{"combo_id":"25351","cost":1000,"currency":"Mastery Stones"}]})
        self.assertEqual(1,len(offers))
        self.assertEqual("25351",offers[0]["combo_id"])

    def test_rejected_init_cannot_build_empty_review_engine(self):
        class RejectedClient:
            def init(self):return {"result":False,"result_message":["Please try that again in a moment."]}
            def card_catalog(self):raise AssertionError("A rejected account snapshot must stop before catalog loads")
        with self.assertRaisesRegex(RuntimeError,"fresh account snapshot"):
            collect_live_review_data(RejectedClient())

    def test_current_init_offers_avoid_redundant_deck_writes(self):
        class CurrentClient:
            def init(self):return {"user_combo_mastery":{},"store_items":[
                {"id":"40","tabs":{"mastery":"1"},"hc_cost":"500","item":[{"id":2025351}]}]}
            def card_catalog(self):return {"25351":{"id":"25351","name":"Test Tech Combo","set":"1001"}}
            def call(self,*args):raise AssertionError("Complete init mastery state needs no extra call")
            def get_exact_decks(self,*args):raise AssertionError("Current init offers need no no-op deck write")
        _engine,offers,_init=collect_live_review_data(CurrentClient())
        self.assertEqual(1,len(offers))


if __name__=="__main__":unittest.main()
