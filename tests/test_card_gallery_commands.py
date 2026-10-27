from copy import deepcopy
from io import BytesIO
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock,patch

from atcommander import utility_commands
from atcommander.card_gallery import ArtAsset
from atcommander.local_commands import CommandInputError,CommandUnavailable
from tests import test_card_presentation as fixture
from tests.test_card_bundle_art import URL,bundle_row,snapshot,atlas


class GalleryCommandTests(unittest.TestCase):
    setUp=fixture.CardCommandTests.setUp

    def test_shared_options_are_registered_and_invalid_choices_never_read_account(self):
        for name in ("card-info","card-combo","user-inventory","deck-map"):
            spec=self.service.spec(name);arguments={arg.name:arg for arg in spec.args}
            self.assertEqual(arguments["image_type"].choices,("none","svg","html","png","jpeg"))
            self.assertEqual(arguments["layout"].choices,("grid","compact","list"))
            self.assertEqual((arguments["columns"].minimum,arguments["columns"].maximum),(1,8))
            for values in ({"image_type":"unverified"},{"columns":0},{"columns":9},{"columns":True},{"layout":"paid"}):
                if name=="deck-map":values={"deck":1,**values}
                with self.subTest(name=name,values=values),self.assertRaises(CommandInputError):
                    self.service.execute(name,values)
        self.assertEqual(self.client.calls,[])

    def test_none_preserves_raw_catalog_and_never_creates_art_cache(self):
        answer=self.service.execute("card-info",{"query":"Rogu"})
        self.assertEqual(answer.rows[0]["upgrade"],fixture.card()["upgrade"])
        self.assertEqual(answer.data,{})
        self.assertFalse(hasattr(self.service,"card_art_cache"))
        self.assertEqual(self.client.calls,[])

    def test_all_export_formats_receive_exact_selected_level_and_layout(self):
        source=fixture.card();before=deepcopy(source)
        self.client.card_catalog=lambda:{"22":source}
        with patch.object(utility_commands,"APP_DIR",Path(self.temp.name)), \
             patch.object(utility_commands,"export_card_map",wraps=utility_commands.export_card_map) as render:
            for kind in ("svg","html","png","jpeg"):
                answer=self.service.execute("card-info",{"query":"Rogu","level":3,"image_type":kind,"layout":"compact","columns":2})
                artifact=answer.data["artifacts"][0]
                self.assertTrue(Path(artifact["path"]).is_file())
                self.assertEqual(artifact["type"],{"svg":"image/svg+xml","html":"text/html","png":"image/png","jpeg":"image/jpeg"}[kind])
                args,kwargs=render.call_args
                self.assertEqual((args[0][0]["level"],args[0][0]["attack"],args[0][0]["health"]),(3,"11","27"))
                self.assertEqual((kwargs["layout"],kwargs["columns"]),("compact",2))
                self.assertEqual(artifact["missing_artwork"],1)
        self.assertEqual(source,before)
        self.assertEqual(self.client.calls,[])

    def test_owned_inventory_export_uses_live_level_fusion_and_sanitized_names(self):
        self.client.card_catalog=lambda:{"22":{**fixture.card(),"name":"Rogu test-password-private"}}
        with patch.object(utility_commands,"APP_DIR",Path(self.temp.name)):
            answer=self.service.execute("user-inventory",{"image_type":"html","display":"summary","fuse_level":1,"layout":"list"})
        self.assertEqual((answer.rows[0]["attack"],answer.rows[0]["health"]),("11","27"))
        self.assertEqual(answer.rows[0]["fusion"],"Dual")
        source=Path(answer.data["artifacts"][0]["path"]).read_text(encoding="utf-8")
        self.assertIn("ATK 11   HP 27",source)
        self.assertNotIn("test-password-private",source)
        self.assertNotIn("unit_index",source)
        self.assertEqual([name for name,_ in self.client.calls],["init"])

    def test_deck_summary_and_map_use_owned_level_without_changing_default_rows(self):
        initial=self.service.execute("deck-map",{"deck":1})
        self.assertEqual(initial.rows[0]["unit_index"],3)
        self.assertNotIn("attack",initial.rows[0])
        self.assertIn('"units"',initial.details)
        with patch.object(utility_commands,"APP_DIR",Path(self.temp.name)):
            answer=self.service.execute("deck-map",{"deck":1,"display":"summary","image_type":"svg","layout":"grid","columns":1})
        self.assertEqual((answer.rows[0]["attack"],answer.rows[0]["health"]),("11","27"))
        self.assertEqual(answer.rows[0]["fusion"],"Dual")
        self.assertNotIn("unit_index",str(answer.rows))
        self.assertNotIn("unit_index",answer.details)
        source=Path(answer.data["artifacts"][0]["path"]).read_text(encoding="utf-8")
        self.assertIn("ATK 11   HP 27",source)
        self.assertEqual([name for name,_ in self.client.calls],["init"])

    def test_deck_unknown_level_does_not_show_base_stats_as_current(self):
        self.client.data["user_units"]["3"]["level"]=18
        answer=self.service.execute("deck-map",{"deck":1,"display":"summary"})
        self.assertNotIn("attack",answer.rows[0])
        self.assertNotIn("health",answer.rows[0])
        self.assertEqual(answer.rows[0]["stats_status"],"Current level stats unavailable")

    def test_combo_exports_real_definitions_preserving_recipe_rows(self):
        self.client.card_catalog=lambda:{"22":fixture.card(),"23":{"name":"Object","rarity":4,"card_type":"item"},
            "25001":{"name":"Real Combo","card_type":"combo","attack_multiplier":"1.5","health_multiplier":"2"}}
        self.client.combo_catalog=lambda:{"22~23":"25001|1"}
        with patch.object(utility_commands,"APP_DIR",Path(self.temp.name)):
            answer=self.service.execute("card-combo",{"query":"Real Combo","display":"summary","image_type":"html"})
        self.assertEqual(answer.rows[0]["combo_id"],"25001")
        self.assertEqual(answer.rows[0]["card1_name"],"Rogu")
        self.assertEqual(answer.data["combo_definitions"][0]["attack_multiplier"],"1.5")
        source=Path(answer.data["artifacts"][0]["path"]).read_text(encoding="utf-8")
        self.assertIn("ATK —   HP —",source)
        self.assertIn("Combo scaling: ATK 1.5 / HP 2",source)
        self.assertEqual(self.client.calls,[])

    def test_unknown_public_art_base_uses_stats_and_no_guessed_asset_requests(self):
        self.client.card_catalog=lambda:{"22":{**fixture.card(),"picture":"Unverified_Picture_Basename"}}
        with patch.object(utility_commands,"APP_DIR",Path(self.temp.name)), \
             patch("atcommander.card_gallery._download_public",side_effect=AssertionError("No guessed URL")) as fetch:
            first=self.service.execute("card-info",{"query":"Rogu","image_type":"svg"})
            cache=self.service.card_art_cache
            second=self.service.execute("card-info",{"query":"Rogu","image_type":"html"})
        self.assertIs(self.service.card_art_cache,cache)
        self.assertEqual(first.data["artifacts"][0]["illustrated"],0)
        self.assertIn("Artwork unavailable",second.message)
        fetch.assert_not_called()

    def test_export_passes_stop_and_creates_no_file_when_stopped(self):
        self.service.stop=threading.Event();self.service.stop.set()
        cache=Mock();self.service.card_art_cache=cache
        with patch.object(utility_commands,"APP_DIR",Path(self.temp.name)),self.assertRaisesRegex(CommandInputError,"stopped"):
            self.service.execute("card-info",{"query":"Rogu","image_type":"svg"})
        self.assertFalse((Path(self.temp.name)/"card_exports").exists())
        cache.get.assert_not_called()

    def test_filesystem_errors_return_generic_message_without_internal_details(self):
        with patch.object(utility_commands,"export_card_map",side_effect=OSError("synthetic-private-file-detail")):
            with self.assertRaisesRegex(CommandUnavailable,"local card map could not be saved") as error:
                self.service.execute("card-info",{"query":"Rogu","image_type":"svg"})
        self.assertNotIn("synthetic-private-file-detail",str(error.exception))

    def test_export_limit_requests_narrowing_before_asset_reads(self):
        self.client.card_catalog=lambda:{str(cid):{**fixture.card(),"id":str(cid)} for cid in range(101)}
        cache=Mock();self.service.card_art_cache=cache
        with patch.object(utility_commands,"APP_DIR",Path(self.temp.name)),self.assertRaisesRegex(CommandInputError,"1–100"):
            self.service.execute("card-info",{"image_type":"svg"})
        cache.get.assert_not_called()
        self.assertFalse((Path(self.temp.name)/"card_exports").exists())

    def public_bundle_cards(self,count=1):
        cards={str(22+offset):{**fixture.card(),"id":str(22+offset),"name":f"Rogu {offset}",
                             "asset_bundle":116,"picture":f"Official_Rogu_{offset}"} for offset in range(count)}
        self.client.card_catalog=lambda:cards
        self.client.data.update(snapshot())
        image=BytesIO();atlas().save(image,format="PNG")
        provider=Mock();provider.get.return_value=ArtAsset(image.getvalue(),URL)
        return provider

    def test_public_bundle_map_reads_one_snapshot_for_all_cards_then_reuses_it(self):
        provider=self.public_bundle_cards(2)
        with patch.object(utility_commands,"APP_DIR",Path(self.temp.name)), \
             patch("atcommander.card_bundle_art.BundleArtProvider",return_value=provider) as constructor:
            first=self.service.execute("card-info",{"image_type":"svg"})
            second=self.service.execute("card-info",{"image_type":"html"})
        self.assertEqual([name for name,_ in self.client.calls],["init"])
        constructor.assert_called_once();self.assertEqual(provider.get.call_count,4)
        self.assertEqual(first.data["artifacts"][0]["illustrated"],2)
        self.assertEqual(second.data["artifacts"][0]["missing_artwork"],0)
        manifest=constructor.call_args.args[1]
        self.assertEqual(manifest.urls,{116:URL})
        self.assertNotIn("synthetic-private-detail",str(manifest))
        provider.configure.assert_called_once()
        source=Path(first.data["artifacts"][0]["path"]).read_text(encoding="utf-8")
        self.assertIn("data:image/png;base64,",source)
        self.assertIn("ATK 10   HP 24",source)
        self.assertNotIn(URL,source)

    def test_bundle_metadata_without_image_request_keeps_no_account_read(self):
        self.public_bundle_cards()
        with patch("atcommander.card_bundle_art.BundleArtProvider") as constructor:
            answer=self.service.execute("card-info",{"display":"summary"})
        self.assertEqual(answer.rows[0]["attack"],"10")
        self.assertEqual(self.client.calls,[]);constructor.assert_not_called()

    def test_explicit_raster_url_bypasses_public_manifest_read(self):
        self.public_bundle_cards()
        self.client.card_catalog=lambda:{"22":{**fixture.card(),"asset_bundle":116,"picture":"Official_Rogu",
            "art_url":"https://cb-cdn.synapsegames.com/cards/verified.png"}}
        cache=utility_commands.OfficialArtCache(Path(self.temp.name),fetcher=lambda _:self.public_png())
        self.service.card_art_cache=cache
        with patch.object(utility_commands,"APP_DIR",Path(self.temp.name)), \
             patch("atcommander.card_bundle_art.BundleArtProvider") as constructor:
            answer=self.service.execute("card-info",{"image_type":"svg"})
        self.assertEqual(answer.data["artifacts"][0]["illustrated"],1)
        self.assertEqual(self.client.calls,[]);constructor.assert_not_called()

    def public_png(self):
        image=BytesIO();atlas().save(image,format="PNG");return image.getvalue()

    def test_missing_or_failed_public_manifest_keeps_stats_and_generic_fallback(self):
        self.public_bundle_cards();del self.client.data["asset_bundles_v3"]
        with patch.object(utility_commands,"APP_DIR",Path(self.temp.name)), \
             patch("atcommander.card_bundle_art.BundleArtProvider") as constructor:
            first=self.service.execute("card-info",{"image_type":"svg"})
            with patch.object(self.service,"snapshot",side_effect=RuntimeError("synthetic-private-detail")):
                second=self.service.execute("card-info",{"image_type":"html"})
        constructor.assert_not_called()
        self.assertEqual(first.data["artifacts"][0]["illustrated"],0)
        source=Path(second.data["artifacts"][0]["path"]).read_text(encoding="utf-8")
        self.assertIn("ATK 10   HP 24",source)
        self.assertNotIn("synthetic-private-detail",source)
        self.assertIn("Artwork unavailable",second.message)

    def test_manifest_refresh_updates_provider_and_absence_clears_stale_source(self):
        provider=self.public_bundle_cards()
        with patch.object(utility_commands,"APP_DIR",Path(self.temp.name)), \
             patch("atcommander.card_bundle_art.BundleArtProvider",return_value=provider) as constructor:
            self.service.execute("card-info",{"image_type":"svg"})
            self.client.data["asset_bundles_v3"]=[bundle_row(digest="0"*32)]
            self.service.execute("card-info",{"image_type":"svg"})
            self.assertEqual(provider.configure.call_args.args[0].urls[116],URL.replace("93abfdb440f31fc60bc80619b111133e","0"*32))
            del self.client.data["asset_bundles_v3"]
            answer=self.service.execute("card-info",{"image_type":"svg"})
        constructor.assert_called_once();self.assertIsNone(self.service.card_art_cache.bundle_provider)
        self.assertEqual(answer.data["artifacts"][0]["illustrated"],0)
        self.assertEqual([name for name,_ in self.client.calls],["init"])

    def test_stop_and_size_limit_precede_any_public_manifest_read(self):
        self.public_bundle_cards(101)
        with patch.object(utility_commands,"APP_DIR",Path(self.temp.name)), \
             patch.object(self.service,"snapshot") as snapshot_read:
            with self.assertRaisesRegex(CommandInputError,"1–100"):
                self.service.execute("card-info",{"image_type":"svg"})
            self.service.stop=threading.Event();self.service.stop.set()
            with self.assertRaisesRegex(CommandInputError,"stopped"):
                self.service.execute("card-info",{"image_type":"svg"})
        snapshot_read.assert_not_called();self.assertEqual(self.client.calls,[])

    def test_manifest_read_updates_private_redaction_before_export(self):
        provider=self.public_bundle_cards()
        self.client.data["session_id"]="fresh-private-snapshot-detail"
        self.client.card_catalog=lambda:{"22":{**fixture.card(),"name":"Rogu fresh-private-snapshot-detail",
                                               "asset_bundle":116,"picture":"Official_Rogu"}}
        with patch.object(utility_commands,"APP_DIR",Path(self.temp.name)), \
             patch("atcommander.card_bundle_art.BundleArtProvider",return_value=provider):
            answer=self.service.execute("card-info",{"image_type":"svg"})
        source=Path(answer.data["artifacts"][0]["path"]).read_text(encoding="utf-8")
        self.assertNotIn("fresh-private-snapshot-detail",source)
        self.assertEqual([name for name,_ in self.client.calls],["init"])


if __name__=="__main__":unittest.main()
