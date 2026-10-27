import copy
import gzip
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from atcommander import client_updater as updater
from atcommander import public_client_version as versions
from atcommander.public_native import NativeBuild,NativeCompatibilityError,_leb,unity_archive
from test_public_client_version import Response,Session

BUILD="Throwdown_WebGL_v1.184.0_r50000"
INDEX='var buildUrl = "Build"; dataUrl: buildUrl + "/WebGL.data.unityweb", codeUrl: buildUrl + "/WebGL.wasm.unityweb", productVersion: "1.184.0"'

class AssetResponse(Response):
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

def proof():
    return {**copy.deepcopy(updater.EXPECTED),"engine":"6000.3.6f1","wasm_sha256":"a"*64,"metadata_sha256":"b"*64}

class ClientUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.cache=Path(self.directory.name)/"profile.json";self.registry=Path(self.directory.name)/"verified.json"
        self.messages=[]
    def session(self,index=INDEX):
        session=Session([Response(f"https://cb-cdn.synapsegames.com/webgl/{BUILD}/index.html"),
                         Response(index),AssetResponse(b"\0asm-fixture"),AssetResponse(b"UnityWebData1.0\0fixture")])
        self.addCleanup(patch.stopall)
        patch.object(updater.requests,"Session",return_value=session).start()
        return session
    def update(self,**options):
        return updater.update_public_client_profile(progress=self.messages.append,cache_path=self.cache,verified_path=self.registry,**options)

    def test_unknown_compatible_build_is_verified_and_persisted_public_only(self):
        session=self.session()
        with patch.object(updater,"verify_assets",return_value=proof()) as verify:
            result=self.update()
        self.assertEqual(result["profile"]["client_version"],"1184000")
        self.assertEqual(verify.call_count,1);self.assertEqual(len(session.calls),4)
        self.assertFalse(session.trust_env)
        self.assertTrue(all(call[1]["allow_redirects"] is False for call in session.calls))
        self.assertTrue(all("cookies" not in call[1] and "auth" not in call[1] for call in session.calls))
        record=json.loads(self.registry.read_text())["builds"][BUILD]
        self.assertTrue(updater._valid_record(BUILD,record))
        self.assertEqual(updater.load_verified_profile(BUILD,path=self.registry),result["profile"])
        self.assertEqual(set(json.loads(self.cache.read_text())),{"schema","build","checked_at","profile"})
        self.assertFalse(any(word in self.registry.read_text() for word in ("password","cookie","msgsig","clisig","user_id")))

    def test_already_verified_build_checks_launcher_without_redownloading_native_assets(self):
        self.session()
        with patch.object(updater,"verify_assets",return_value=proof()):self.update()
        session=self.session()
        with patch.object(updater,"verify_assets",side_effect=AssertionError("No second download")):
            self.update()
        self.assertEqual(len(session.calls),2)

    def test_changed_signer_leaves_existing_cache_and_registry_unchanged(self):
        self.cache.write_text("previous public profile");self.session()
        with patch.object(updater,"verify_assets",side_effect=NativeCompatibilityError("Private material must not be echoed")):
            with self.assertRaises(versions.PublicClientVersionError) as caught:self.update()
        self.assertEqual(self.cache.read_text(),"previous public profile")
        self.assertFalse(self.registry.exists())
        self.assertNotIn("Private material",str(caught.exception))

    def test_version_disagreement_and_unknown_asset_paths_send_no_asset_request(self):
        for index in (INDEX.replace("1.184.0","1.185.0"),INDEX.replace('buildUrl = "Build"','buildUrl = "Other"'),
                      INDEX.replace("/WebGL.wasm.unityweb","/other.wasm")):
            with self.subTest(index=index):
                session=self.session(index)
                with self.assertRaises(versions.PublicClientVersionError):self.update()
                self.assertEqual(len(session.calls),2)
                self.assertFalse(self.cache.exists())

    def test_cancellation_before_fetch_creates_no_files(self):
        with patch.object(updater.requests,"Session",side_effect=AssertionError("No fetch")):
            with self.assertRaises(versions.PublicClientVersionError) as caught:self.update(should_stop=lambda:True)
        self.assertEqual(caught.exception.reason,"client_update_cancelled")
        self.assertFalse(self.cache.exists());self.assertFalse(self.registry.exists())

    def test_registry_cannot_assert_a_changed_profile_or_changed_proof(self):
        record={"profile":updater._profile(BUILD),"proof":proof()}
        for change in ("profile","proof"):
            value=copy.deepcopy(record)
            if change=="profile":value["profile"]["client_version"]="1"
            else:value["proof"]["send"]=["0"*64,5876]
            self.registry.write_text(json.dumps({"schema":1,"builds":{BUILD:value}}))
            self.assertIsNone(updater.load_verified_profile(BUILD,path=self.registry))

    def test_registry_cannot_redirect_downloads_or_introduce_extra_fields(self):
        record={"profile":updater._profile(BUILD),"proof":proof(),"url":"https://example.test/private"}
        self.registry.write_text(json.dumps({"schema":1,"builds":{BUILD:record}}))
        self.assertIsNone(updater.load_verified_profile(BUILD,path=self.registry))
        for build in ("../elsewhere","https://example.test","Throwdown_WebGL_v1.184.0_r1?secret=x"):
            with self.assertRaises(ValueError):updater._profile(build)

    def test_compressed_public_code_is_decoded_without_execution(self):
        content=b"\0asm"+b"public-fixture"*20
        self.assertEqual(updater._decode_asset(gzip.compress(content),b"\0asm",lambda:False),content)
        with self.assertRaises(Exception):updater._decode_asset(b"unknown",b"\0asm",lambda:False)

    def test_dynamic_verified_profile_is_accepted_by_the_normal_resolver(self):
        record={"profile":updater._profile(BUILD),"proof":proof()}
        self.registry.write_text(json.dumps({"schema":1,"builds":{BUILD:record}}))
        with patch.object(updater,"_registry_path",return_value=self.registry):
            self.assertEqual(versions._profile_for_build(BUILD),record["profile"])
            record["proof"]["hash"]=["0"*64,128]
            self.registry.write_text(json.dumps({"schema":1,"builds":{BUILD:record}}))
            self.assertIsNone(versions._profile_for_build(BUILD))

    def test_current_183_build_is_supported_independently_of_runtime_files(self):
        self.assertEqual(versions._VERIFIED_PROFILES["Throwdown_WebGL_v1.183.0_r40786"]["client_version"],"1183000")


class NativeReaderTests(unittest.TestCase):
    def test_unity_archive_reads_only_bounded_named_slices(self):
        name=b"Il2CppData/Metadata/global-metadata.dat";payload=b"public metadata"
        end=20+12+len(name)
        data=b"UnityWebData1.0\0"+struct.pack("<I",end)+struct.pack("<III",end,len(payload),len(name))+name+payload
        self.assertEqual(bytes(unity_archive(data)[name.decode()]),payload)
        for changed in (data[:-1],data[:16]+struct.pack("<I",0)+data[20:],b"not an archive"):
            with self.assertRaises((NativeCompatibilityError,ValueError)):unity_archive(changed)

    def test_native_reader_rejects_unknown_headers_and_truncated_integers(self):
        with self.assertRaises(NativeCompatibilityError):NativeBuild(b"\0asm",b"x"*380)
        with self.assertRaises(NativeCompatibilityError):_leb(b"\x80"*11,0)
        with self.assertRaises(NativeCompatibilityError):_leb(b"\x80",0)

    def test_relocated_addresses_match_but_literal_and_call_binding_changes_do_not(self):
        native=NativeBuild.__new__(NativeBuild);native.hash_function=9;native.table={}
        rows=[(65,[70000]),(65,[71000]),(16,[1,"GenerateHash"])]
        native.instructions=lambda function:rows
        before=native.fingerprint(1,{71000:("literal","field-hash")})
        rows[:]=[(65,[80000]),(65,[81000]),(16,[2,"GenerateHash"])]
        self.assertEqual(native.fingerprint(1,{81000:("literal","field-hash")}),before)
        self.assertNotEqual(native.fingerprint(1,{81000:("literal","changed-field-hash")}),before)
        rows[-1]=(16,[2,"DifferentHash"])
        self.assertNotEqual(native.fingerprint(1,{81000:("literal","field-hash")}),before)

if __name__=="__main__":unittest.main()
