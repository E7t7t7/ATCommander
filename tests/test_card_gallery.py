import base64
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import xml.etree.ElementTree as ET

from atcommander import card_gallery as gallery
from atcommander.card_presentation import at_level

try:
    from PIL import Image,PngImagePlugin
except ImportError:
    Image=None

URL="https://cb-cdn.synapsegames.com/public/card-art/synthetic.png"


def card():
    return {"id":"22","name":"Synthetic Rogu","rarity":4,"card_type":"character","picture_url":URL,
            "attack":10,"health":24,"skill":[{"id":"outlast","x":4}],
            "upgrade":[{"level":2,"health":25,"skill":[{"id":"outlast","x":5}]},
                       {"level":3,"attack":11,"health":27}]}


def png():
    image=Image.new("RGBA",(120,80),(36,67,90,255));out=BytesIO()
    metadata=PngImagePlugin.PngInfo();metadata.add_text("private","synthetic-private-metadata")
    image.save(out,format="PNG",pnginfo=metadata)
    return out.getvalue()


class ArtURLTests(unittest.TestCase):
    def test_only_uncredentialed_official_raster_urls_are_allowed(self):
        self.assertEqual(gallery.valid_art_url(URL),URL)
        for url in ("http://cb-cdn.synapsegames.com/card.png",URL+"?token=synthetic-private",
                    URL+"#fragment","https://cb-cdn.synapsegames.com.evil.test/card.png",
                    "https://user:synthetic-private@cb-cdn.synapsegames.com/card.png",
                    "https://cb-cdn.synapsegames.com:bad/card.png",
                    "https://[invalid/card.png","https://cb-cdn.synapsegames.com/../card.png",
                    "https://cb-cdn.synapsegames.com/card.svg","data:image/png;base64,test"):
            with self.subTest(url=url):self.assertIsNone(gallery.valid_art_url(url))

    def test_picture_basename_needs_explicit_verified_base(self):
        self.assertIsNone(gallery.official_art_url({"picture":"Public_Picture"}))
        base="https://cb-cdn.synapsegames.com/public/card-art/"
        self.assertEqual(gallery.official_art_url({"picture":"Public_Picture"},base),base+"Public_Picture.png")
        self.assertIsNone(gallery.official_art_url({"picture":"../private"},base))

    def test_art_download_uses_fresh_no_auth_session_and_rejects_redirects(self):
        response=Mock(status_code=302);response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        session=Mock();session.__enter__=Mock(return_value=session);session.__exit__=Mock(return_value=False);session.get.return_value=response
        with patch.object(gallery.requests,"Session",return_value=session):
            with self.assertRaises(ValueError):gallery._download_public(URL)
        self.assertFalse(session.trust_env)
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
        self.assertNotIn("auth",session.get.call_args.kwargs)
        self.assertNotIn("cookies",session.get.call_args.kwargs)


class GalleryStatTests(unittest.TestCase):
    def test_svg_uses_exact_selected_level_stats_without_mutating_source(self):
        selected=at_level(card(),3);before=deepcopy(selected)
        source=gallery.gallery_svg([selected])
        self.assertIn("ATK 11   HP 27",source)
        self.assertIn("Recover 5",source)
        self.assertIn("Level 3",source)
        self.assertEqual(selected,before)

    def test_stat_map_omits_private_instance_auth_and_account_fields(self):
        selected={**at_level(card(),3),"unit_index":"synthetic-private-instance","password":"synthetic-private-password","user_id":"synthetic-private-account"}
        source=gallery.gallery_svg([selected])
        for value in ("synthetic-private-instance","synthetic-private-password","synthetic-private-account"):
            self.assertNotIn(value,source)
        self.assertNotIn(URL,source)

    def test_combo_map_does_not_invent_input_dependent_attack_or_health(self):
        combo={"id":25001,"name":"Synthetic combo","card_type":"combo","level":1,"attack_multiplier":"1.5","health_multiplier":"2.0"}
        source=gallery.gallery_svg([combo])
        self.assertIn("ATK —   HP —",source)
        self.assertIn("Combo scaling: ATK 1.5 / HP 2.0",source)
        self.assertIn("Combo values depend on the selected input cards.",source)

    def test_svg_name_escaping_and_portable_html_have_no_remote_requests(self):
        selected=at_level(card(),3);selected["name"]='<script>unsafe()</script> & text'
        with tempfile.TemporaryDirectory() as folder:
            artifact=gallery.export_card_map([selected],folder,image_type="html")
            source=Path(artifact["path"]).read_text(encoding="utf-8")
        self.assertIn("&lt;script&gt;unsafe()&lt;/script&gt;",source)
        self.assertNotIn("<script>unsafe()",source)
        self.assertNotIn("https://",source)
        self.assertIn("Content-Security-Policy",source)
        self.assertEqual(artifact["missing_artwork"],1)

    def test_grid_compact_and_list_layouts_are_bounded_valid_svg(self):
        for layout in gallery.LAYOUTS:
            source=gallery.gallery_svg([at_level(card(),3)]*4,layout=layout,columns=2)
            root=ET.fromstring(source)
            self.assertGreater(int(root.attrib["width"]),0)
            self.assertEqual(len(root.findall("{http://www.w3.org/2000/svg}g")),4)
        for changes in ({"columns":True},{"columns":9},{"layout":"unverified-bender-format"}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):gallery.gallery_svg([card()],**changes)
        with self.assertRaises(ValueError):gallery.gallery_svg([card()]*101)

    def test_stop_prevents_assets_and_output(self):
        cache=Mock()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,"stopped"):
                gallery.export_card_map([card()],folder,art_cache=cache,should_stop=lambda:True)
            self.assertEqual(list(Path(folder).iterdir()),[])
        cache.get.assert_not_called()

    def test_optional_decoder_missing_preserves_svg_stat_fallback(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(gallery,"_pillow",side_effect=ValueError("optional decoder unavailable")):
            cache=gallery.OfficialArtCache(Path(folder)/"art",fetcher=Mock(return_value=b"synthetic"))
            artifact=gallery.export_card_map([at_level(card(),3)],folder,art_cache=cache)
            self.assertEqual(artifact["illustrated"],0)
            self.assertIn("ATK 11   HP 27",Path(artifact["path"]).read_text(encoding="utf-8"))
            with self.assertRaises(ValueError):gallery.export_card_map([card()],folder,image_type="png")


@unittest.skipIf(Image is None,"Optional Pillow is unavailable")
class GalleryArtworkTests(unittest.TestCase):
    def test_decode_strips_metadata_and_disallows_non_raster_content(self):
        normalized=gallery.normalize_art(png())
        with Image.open(BytesIO(normalized)) as decoded:
            self.assertNotIn("private",decoded.info)
            self.assertEqual(decoded.format,"PNG")
        self.assertNotIn(b"synthetic-private-metadata",normalized)
        with self.assertRaises(ValueError):gallery.normalize_art(b'<svg><script>bad()</script></svg>')

    def test_cache_reuses_art_across_cards_and_restarts_without_network(self):
        fetch=Mock(return_value=png())
        with tempfile.TemporaryDirectory() as folder:
            cache=gallery.OfficialArtCache(folder,fetcher=fetch)
            first=cache.get(card());self.assertIsNotNone(first)
            self.assertEqual(cache.get({**card(),"id":23}),first)
            restarted=gallery.OfficialArtCache(folder,fetcher=Mock(side_effect=AssertionError("network should not run")))
            self.assertIsNotNone(restarted.get(card()))
        fetch.assert_called_once_with(URL)

    def test_corrupt_cached_image_is_recovered_with_one_public_read(self):
        fetch=Mock(return_value=png())
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/(sha256(URL.encode()).hexdigest()+".png");target.write_bytes(b"corrupt-cache")
            cache=gallery.OfficialArtCache(folder,fetcher=fetch)
            self.assertIsNotNone(cache.get(card()))
            with Image.open(target) as decoded:self.assertEqual(decoded.format,"PNG")
        fetch.assert_called_once()

    def test_unavailable_art_is_not_hammered_and_error_text_is_not_exposed(self):
        now=[1000];fetch=Mock(side_effect=RuntimeError("synthetic-private-fetch-detail"))
        with tempfile.TemporaryDirectory() as folder:
            cache=gallery.OfficialArtCache(folder,fetcher=fetch,clock=lambda:now[0])
            self.assertIsNone(cache.get(card()));self.assertIsNone(cache.get(card()))
            fetch.assert_called_once()
            now[0]+=301;self.assertIsNone(cache.get(card()));self.assertEqual(fetch.call_count,2)
            artifact=gallery.export_card_map([card()],folder,art_cache=cache)
            self.assertNotIn("synthetic-private-fetch-detail",Path(artifact["path"]).read_text(encoding="utf-8"))

    def test_svg_embeds_only_decoded_raster_art_with_exact_card_count(self):
        with tempfile.TemporaryDirectory() as folder:
            cache=gallery.OfficialArtCache(Path(folder)/"art",fetcher=Mock(return_value=png()))
            artifact=gallery.export_card_map([at_level(card(),3)],folder,art_cache=cache)
            root=ET.fromstring(Path(artifact["path"]).read_text(encoding="utf-8"))
        images=root.findall(".//{http://www.w3.org/2000/svg}image")
        self.assertEqual(artifact["illustrated"],1)
        self.assertEqual(len(images),1)
        self.assertTrue(images[0].attrib["href"].startswith("data:image/png;base64,"))
        decoded=base64.b64decode(images[0].attrib["href"].split(",",1)[1])
        with Image.open(BytesIO(decoded)) as image:self.assertEqual(image.size,(120,80))

    def test_png_and_jpeg_outputs_are_decodable_with_expected_map_dimensions(self):
        with tempfile.TemporaryDirectory() as folder:
            cache=gallery.OfficialArtCache(Path(folder)/"art",fetcher=Mock(return_value=png()))
            for image_type in ("png","jpeg","jpg"):
                artifact=gallery.export_card_map([at_level(card(),3)],folder,image_type=image_type,art_cache=cache)
                with Image.open(artifact["path"]) as rendered:
                    self.assertEqual(rendered.size,(364,576))
                    self.assertIn(rendered.format,{"PNG","JPEG"})
                    pixel=rendered.getpixel((164,150))
                    for actual,expected in zip(pixel,(36,67,90)):
                        self.assertLessEqual(abs(actual-expected),3)
                self.assertEqual(artifact["illustrated"],1)


if __name__=="__main__":unittest.main()
