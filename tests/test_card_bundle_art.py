"""Offline contracts for current public Unity/Tk2d card illustrations."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from PIL import Image

from atcommander import card_bundle_art as art
from atcommander.card_gallery import OfficialArtCache


BASE="https://cb-cdn.synapsegames.com/assetbundles/v3/"
HASH="93abfdb440f31fc60bc80619b111133e"
URL=BASE+"bundle-116_WebGLPlayer_"+HASH+".unity3d"


def bundle_row(bid=116,digest=HASH,moves=()):
    return {"id":bid,"bundle":{"name":f"bundle-{bid}","hashes":{"WebGLPlayer":digest,"Android":"irrelevant"}},
            "sprites_moved":list(moves),"download_size":{"WebGLPlayer":1497114}}


def snapshot(*rows):
    return {"config":{"asset_bundle_url":BASE},"asset_bundles_v3":list(rows or (bundle_row(),)),
            "private_account_data":{"password":"synthetic-private-detail","user_id":987654}}


def geometry(name="Official_Bobby",width=4,height=4,uvs=None):
    return {"name":name,"materialId":0,"positions":[{"x":0,"y":0},{"x":width,"y":0},
            {"x":0,"y":height},{"x":width,"y":height}],"texelSize":{"x":1,"y":1},
            "uvs":[{"x":x,"y":y} for x,y in (uvs or [(0,0),(1,0),(0,1),(1,1)])]}


def atlas():
    image=Image.new("RGBA",(4,4))
    for y in range(4):
        for x in range(4):image.putpixel((x,y),((255,0,0,255),(0,255,0,255),(0,0,255,255),(255,255,0,255))[(y//2)*2+x//2])
    return image


def fake_env(*names,ref_file=0,definitions=None,size=(4,4)):
    texture=SimpleNamespace(m_Width=size[0],m_Height=size[1],image=atlas())
    texture_reader=SimpleNamespace(path_id=8,type=SimpleNamespace(name="Texture2D"),parse_as_object=Mock(return_value=texture))
    row={"spriteDefinitions":definitions if definitions is not None else [geometry(name) for name in (names or ("Official_Bobby",))],
         "textures":[{"m_FileID":ref_file,"m_PathID":8}],"premultipliedAlpha":False}
    collection=SimpleNamespace(path_id=4,type=SimpleNamespace(name="MonoBehaviour"),parse_as_dict=Mock(return_value=row))
    return SimpleNamespace(objects=[collection,texture_reader])


def minimal_bundle(*,raw=64,stored=64,kind=0,version=7,flags=0,uncompressed=None):
    info=b"\0"*16+struct.pack(">I",1)+struct.pack(">IIH",raw,stored,kind)+struct.pack(">I",0)
    header=b"UnityFS\0"+struct.pack(">I",version)+b"2022.3\0"+b"2022.3.0f1\0"
    length=len(header)+20;aligned=(length+15)//16*16
    full_length=aligned+len(info)+stored
    header+=struct.pack(">QIII",full_length,len(info),len(info) if uncompressed is None else uncompressed,flags)
    header+=b"\0"*(aligned-len(header))
    return header+((b"\0"*stored+info) if flags&128 else (info+b"\0"*stored))


class ManifestTests(unittest.TestCase):
    def test_exact_public_manifest_only_and_no_snapshot_retained(self):
        data=snapshot();before=deepcopy(data);manifest=art.public_bundle_manifest(data)
        self.assertEqual(manifest.urls,{116:URL});self.assertEqual(manifest.moves,{})
        self.assertEqual(data,before)
        self.assertNotIn("synthetic-private-detail",str(manifest))
        self.assertEqual(art.card_bundle_key({"asset_bundle":"116","picture":"Official_Bobby"}),(116,"Official_Bobby"))

    def test_missing_or_ambiguous_sources_never_guess(self):
        values=[{}, {"config":{"asset_bundle_url":BASE}}, {"asset_bundles_v3":[bundle_row()]},
                {"asset_bundle_url":BASE,"asset_bundles":[bundle_row()]},
                {"asset_bundle_url":BASE,"asset_bundles_v3":[bundle_row()],"nested":{"asset_bundles_v3":[bundle_row()]}},
                {**snapshot(),"nested":{"asset_bundle_url":BASE.replace("synapsegames","synapse-games")}},
                {"asset_bundle_url":BASE,"asset_bundles_v3":[bundle_row()]*2049}]
        for value in values:
            with self.subTest(value=list(value)):self.assertEqual(art.public_bundle_manifest(value).urls,{})

    def test_invalid_bundle_rows_are_not_used(self):
        bad=[]
        for value in (True,0,-1,"0116",10000,"116/.."):
            row=bundle_row();row["id"]=value;bad.append(row)
        for name in ("bundle-115","../bundle-116","Bundle-116"):
            row=bundle_row();row["bundle"]["name"]=name;bad.append(row)
        for digest in (HASH.upper(),"not-hash",HASH+"?private",None):bad.append(bundle_row(digest=digest))
        for row in bad:
            with self.subTest(row=row):self.assertEqual(art.public_bundle_manifest(snapshot(row)).urls,{})

    def test_conflicting_hashes_reject_instead_of_selecting_one(self):
        manifest=art.public_bundle_manifest(snapshot(bundle_row(),bundle_row(digest="0"*32)))
        self.assertIsNone(manifest.resolve(116,"Official_Bobby"))

    def test_exact_movement_only_and_cycles_or_conflicts_rejected(self):
        moved={"name":"Official_Bobby","previous_asset_bundle_id":116,"new_asset_bundle_id":117}
        manifest=art.public_bundle_manifest(snapshot(bundle_row(),bundle_row(117,moves=[moved])))
        self.assertEqual(manifest.resolve(116,"Official_Bobby"),manifest.urls[117])
        self.assertEqual(manifest.resolve(116,"Other_Bobby"),URL)
        back={**moved,"previous_asset_bundle_id":117,"new_asset_bundle_id":116}
        self.assertIsNone(art.public_bundle_manifest(snapshot(bundle_row(moves=[back]),bundle_row(117,moves=[moved]))).resolve(116,"Official_Bobby"))
        conflict={**moved,"new_asset_bundle_id":118}
        self.assertIsNone(art.public_bundle_manifest(snapshot(bundle_row(moves=[moved,conflict]),bundle_row(117),bundle_row(118))).resolve(116,"Official_Bobby"))

    def test_public_url_validation_rejects_credentials_and_paths(self):
        self.assertEqual(art.valid_bundle_url(URL),URL)
        for value in (URL.replace("https:","http:"),URL+"?token=synthetic",URL+"#fragment",
                      URL.replace("cb-cdn.","user:pass@cb-cdn."),URL.replace(".com/",".com:444/"),
                      URL.replace(".com/",".com.evil.test/"),URL.replace("/v3/","/v3/../v3/"),
                      URL.replace(HASH,HASH.upper()),URL.replace("116_WebGLPlayer","0_WebGLPlayer")):
            with self.subTest(value=value):self.assertIsNone(art.valid_bundle_url(value))
        for value in ("../Official_Bobby","Official/Bobby","Bobby?x","Bobby\\sprite","Bobby\n",""):
            self.assertIsNone(art.card_bundle_key({"asset_bundle":116,"picture":value}))
        self.assertIsNone(art.card_bundle_key({"asset_bundle":True,"picture":"Bobby"}))

    def test_real_public_names_support_spaces_decimal_timestamps_and_punctuation(self):
        for value in ("AD_S16E05_Buckarito_02.44","AD_S13E16_Rogu_15.00","Bender's $100 Combo!","Bonus\u202fArt"):
            with self.subTest(name=value):
                self.assertEqual(art.card_bundle_key({"asset_bundle":116,"picture":value}),(116,value))
        moved={"name":"AD_S13E16_Rogu_15.00","previous_asset_bundle_id":116,"new_asset_bundle_id":117}
        manifest=art.public_bundle_manifest(snapshot(bundle_row(),bundle_row(117,moves=[moved])))
        self.assertEqual(manifest.resolve(116,moved["name"]),manifest.urls[117])


class PublicDownloadTests(unittest.TestCase):
    def response(self,*,status=200,headers=None,chunks=(b"public",)):
        response=Mock(status_code=status,headers=headers or {})
        response.iter_content.return_value=iter(chunks);response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        return response

    def download(self,response,stopped=lambda:False):
        session=Mock();session.__enter__=Mock(return_value=session);session.__exit__=Mock(return_value=False)
        session.get.return_value=response
        with patch.object(art.requests,"Session",return_value=session):result=art._download_bundle(URL,stopped)
        return result,session

    def test_fresh_uncredentialed_non_redirecting_session(self):
        result,session=self.download(self.response(chunks=(b"one",b"two")))
        self.assertEqual(result,b"onetwo");self.assertFalse(session.trust_env)
        args,kwargs=session.get.call_args
        self.assertEqual(args,(URL,));self.assertFalse(kwargs["allow_redirects"]);self.assertEqual(kwargs["timeout"],(5,20))
        self.assertNotIn("auth",kwargs);self.assertNotIn("cookies",kwargs)
        self.assertNotIn("Authorization",kwargs["headers"])

    def test_stopped_or_invalid_url_never_opens_session(self):
        with patch.object(art.requests,"Session") as session:
            with self.assertRaises(ValueError):art._download_bundle(URL,lambda:True)
            with self.assertRaises(ValueError):art._download_bundle(URL+"?private",lambda:False)
        session.assert_not_called()

    def test_status_redirect_declared_streamed_limits_and_stop(self):
        values=[(self.response(status=302),lambda:False),(self.response(status=403),lambda:False),
                (self.response(headers={"Content-Length":"17"}),lambda:False),
                (self.response(chunks=(b"123456789",b"123456789")),lambda:False),
                (self.response(),lambda:True)]
        with patch.object(art,"MAX_BUNDLE_BYTES",16):
            for response,stop in values:
                with self.subTest(response=response),self.assertRaises(ValueError):self.download(response,stop)
        with self.assertRaisesRegex(ValueError,"stopped"):
            self.download(self.response(chunks=(b"one",b"two")),Mock(side_effect=[False,False,True]))


class BundleReaderBoundsTests(unittest.TestCase):
    def test_supported_uncompressed_metadata_at_front_or_end(self):
        self.assertIsNone(art.validate_bundle(minimal_bundle()))
        self.assertIsNone(art.validate_bundle(minimal_bundle(flags=128)))

    def test_invalid_headers_and_oversized_or_unsupported_decompression(self):
        values=[b"not Unity"*12,minimal_bundle(version=6),minimal_bundle(flags=1),minimal_bundle(kind=1),
                minimal_bundle(raw=art.MAX_UNPACKED_BYTES+1),minimal_bundle(uncompressed=art.MAX_INFO_BYTES+1),
                minimal_bundle(raw=0),minimal_bundle(stored=0),minimal_bundle()[:-1]]
        for value in values:
            with self.subTest(size=len(value)),self.assertRaisesRegex(ValueError,"unsupported or oversized"):
                art.validate_bundle(value)

    def test_unbounded_or_truncated_file_nodes_are_rejected(self):
        good=bytearray(minimal_bundle());header_size=struct.unpack_from(">I",good,8)[0]
        self.assertEqual(header_size,7)
        # The synthetic v7 header is padded to 64; metadata has one block.
        info_start=64;node_count=info_start+30
        oversized=bytearray(good);struct.pack_into(">I",oversized,node_count,4097)
        truncated=bytearray(good);struct.pack_into(">I",truncated,node_count,1)
        for value in (bytes(oversized),bytes(truncated)):
            with self.assertRaisesRegex(ValueError,"unsupported or oversized"):art.validate_bundle(value)

    def test_loader_validation_precedes_optional_reader_and_errors_are_generic(self):
        reader=Mock();reader.load.side_effect=RuntimeError("synthetic-private-detail")
        with patch.dict("sys.modules",{"UnityPy":reader}):
            with self.assertRaises(ValueError):art._load_bundle(b"bad")
            reader.load.assert_not_called()
            with self.assertRaisesRegex(ValueError,"could not be decoded") as error:art._load_bundle(minimal_bundle())
            self.assertNotIn("synthetic-private-detail",str(error.exception))


class AtlasTests(unittest.TestCase):
    def assert_image(self,actual,expected):
        self.assertEqual(actual.size,expected.size);self.assertEqual(actual.tobytes(),expected.tobytes())

    def test_normal_rotated_and_reflected_packing(self):
        original=atlas()
        self.assert_image(art.quad_illustration(original,geometry()),original)
        reflected=geometry(uvs=[(1,0),(0,0),(1,1),(0,1)])
        self.assert_image(art.quad_illustration(original,reflected),original.transpose(Image.Transpose.FLIP_LEFT_RIGHT))
        rotated=geometry(uvs=[(0,1),(0,0),(1,1),(1,0)])
        self.assert_image(art.quad_illustration(original,rotated),original.transpose(Image.Transpose.ROTATE_90))

    def test_crop_is_exact_without_neighboring_sprites(self):
        original=Image.new("RGBA",(6,6))
        for y in range(6):
            for x in range(6):original.putpixel((x,y),(x*40,y*40,20,255))
        definition=geometry(width=2,height=2,uvs=[(2/6,3/6),(4/6,3/6),(2/6,5/6),(4/6,5/6)])
        self.assert_image(art.quad_illustration(original,definition),original.crop((2,1,4,3)))

    def test_premultiplied_alpha_is_restored(self):
        original=Image.new("RGBA",(4,4),(100,50,25,128))
        pixel=art.quad_illustration(original,geometry(),premultiplied=True).getpixel((1,1))
        self.assertEqual(pixel,(199,99,49,128))

    def test_unsupported_or_unbounded_geometry_returns_generic_error(self):
        bad=[]
        for field,value in (("complexGeometry",True),("texelSize",{"x":0,"y":1}),("positions",[])):
            row=geometry();row[field]=value;bad.append(row)
        for value in (-0.1,1.1,float("nan")):
            row=geometry();row["uvs"][0]["x"]=value;bad.append(row)
        row=geometry();row["uvs"][3]["x"]=0.8;bad.append(row)
        row=geometry();row["uvs"]=[{"x":0,"y":0}]*4;bad.append(row)
        row=geometry(width=4097);bad.append(row)
        row=geometry();row["positions"][3]["x"]=3;bad.append(row)
        for definition in bad:
            with self.subTest(definition=definition),self.assertRaisesRegex(ValueError,"geometry is unavailable"):
                art.quad_illustration(atlas(),definition)
        with patch.object(art,"MAX_TEXTURE_PIXELS",15),self.assertRaises(ValueError):art.quad_illustration(atlas(),geometry())


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name);self.now=[10.0]
        self.fetch=Mock(return_value=b"public fixture bundle");self.loader=Mock(return_value=fake_env("Official_Bobby","Other_Card"))
        self.manifest=art.public_bundle_manifest(snapshot())
        self.provider=self.new_provider()

    def new_provider(self,**kwargs):
        return art.BundleArtProvider(self.directory,self.manifest,fetcher=self.fetch,loader=self.loader,clock=lambda:self.now[0],**kwargs)

    def card(self,name="Official_Bobby",bundle=116):return {"picture":name,"asset_bundle":bundle}

    def test_exact_art_and_one_download_per_bundle(self):
        first=self.provider.get(self.card());second=self.provider.get(self.card("Other_Card"))
        self.assertEqual(first.source,URL);self.assertIsNotNone(second)
        with Image.open(BytesIO(first.png)) as image:self.assertEqual(image.convert("RGBA").tobytes(),atlas().tobytes())
        self.fetch.assert_called_once_with(URL);self.loader.assert_called_once()
        paths=list(self.directory.rglob("*"))
        for path in paths:
            if path.is_file():self.assertRegex(path.stem,r"^[0-9a-f]{64}$")

    def test_restart_uses_normalized_image_then_raw_bundle_without_download(self):
        self.assertIsNotNone(self.provider.get(self.card()))
        self.fetch.reset_mock();self.loader.reset_mock()
        restarted=self.new_provider()
        self.assertIsNotNone(restarted.get(self.card()));self.fetch.assert_not_called();self.loader.assert_not_called()
        self.assertIsNotNone(restarted.get(self.card("Other_Card")));self.fetch.assert_not_called();self.loader.assert_called_once()

    def test_corrupt_png_is_recovered_from_valid_cached_bundle(self):
        self.provider.get(self.card())
        next((self.directory/"illustrations").glob("*.png")).write_bytes(b"broken")
        self.fetch.reset_mock();self.loader.reset_mock()
        self.assertIsNotNone(self.new_provider().get(self.card()))
        self.fetch.assert_not_called();self.loader.assert_called_once()

    def test_corrupt_raw_bundle_is_recovered_by_one_fresh_fetch(self):
        digest=sha256(URL.encode()).hexdigest();folder=self.directory/"bundles";folder.mkdir()
        (folder/(digest+".unity3d")).write_bytes(b"broken")
        self.loader.side_effect=[ValueError("bad cached fixture"),fake_env()]
        self.assertIsNotNone(self.provider.get(self.card()));self.fetch.assert_called_once_with(URL)

    def test_failed_download_cooldown_applies_across_cards_and_recovers(self):
        self.fetch.side_effect=RuntimeError("synthetic-private-detail")
        self.assertIsNone(self.provider.get(self.card()));self.assertIsNone(self.provider.get(self.card("Other_Card")))
        self.fetch.assert_called_once();self.assertFalse(self.directory.exists() and list(self.directory.iterdir()))
        self.now[0]+=301;self.fetch.side_effect=None
        self.assertIsNotNone(self.provider.get(self.card()));self.assertEqual(self.fetch.call_count,2)

    def test_exact_sprite_only_no_suffix_or_foreign_texture_fallback(self):
        fixtures=[fake_env("Official_Bobby_full"),fake_env("Official_Bobby","Official_Bobby"),fake_env(ref_file=1),
                  fake_env(size=(art.MAX_TEXTURE_PIXELS+1,1)),fake_env(size=(-4,-4)),fake_env(size=(True,4))]
        for number,env in enumerate(fixtures):
            with self.subTest(number=number):
                provider=art.BundleArtProvider(self.directory/str(number),self.manifest,fetcher=self.fetch,loader=lambda _:env)
                self.assertIsNone(provider.get(self.card()))
                if number>=3:env.objects[1].parse_as_object.assert_called_once()

    def test_stop_before_loading_and_after_download_saves_nothing(self):
        stopped=Mock(return_value=True);provider=self.new_provider(should_stop=stopped)
        self.assertIsNone(provider.get(self.card()));self.fetch.assert_not_called()
        state=[False]
        def fetch(_):state[0]=True;return b"public fixture bundle"
        self.loader.reset_mock()
        provider=art.BundleArtProvider(self.directory,self.manifest,fetcher=fetch,loader=self.loader,should_stop=lambda:state[0])
        self.assertIsNone(provider.get(self.card()));self.assertEqual(list(self.directory.iterdir()),[])
        self.loader.assert_not_called()

    def test_invalid_or_unknown_manifest_never_downloads(self):
        provider=art.BundleArtProvider(self.directory,art.BundleManifest({116:URL+"?private"},{}),fetcher=self.fetch,loader=self.loader)
        self.assertIsNone(provider.get(self.card()));self.assertIsNone(self.provider.get(self.card(bundle=117)))
        self.assertIsNone(self.provider.get(self.card("../Bobby")));self.fetch.assert_not_called()

    def test_public_hash_update_selects_new_cache_instead_of_stale_art(self):
        self.assertIsNotNone(self.provider.get(self.card()))
        replacement=art.public_bundle_manifest(snapshot(bundle_row(digest="0"*32)))
        self.provider.configure(replacement)
        self.assertIsNotNone(self.provider.get(self.card()));self.assertEqual(self.fetch.call_count,2)
        self.assertEqual(self.fetch.call_args.args,(replacement.urls[116],))
        self.assertEqual(len(list((self.directory/"illustrations").glob("*.png"))),2)

    def test_only_two_unpacked_environments_remain_in_memory(self):
        self.manifest=art.public_bundle_manifest(snapshot(bundle_row(116),bundle_row(117),bundle_row(118)))
        provider=self.new_provider()
        for bundle in (116,117,118):self.assertIsNotNone(provider.get(self.card(bundle=bundle)))
        self.assertEqual(len(provider._environments),2);self.assertNotIn(URL,provider._environments)

    def test_optional_reader_failure_preserves_stat_fallback(self):
        self.loader.side_effect=ImportError("optional reader not installed")
        cache=OfficialArtCache(self.directory,bundle_provider=self.provider)
        self.assertIsNone(cache.get(self.card()));self.assertIsNone(cache.get(self.card()))
        self.fetch.assert_called_once()


if __name__=="__main__":unittest.main()
