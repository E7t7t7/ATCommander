import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests

from atcommander import public_client_version as version


BUILD = "Throwdown_WebGL_v1.182.0_r40510"
INDEX = f"https://cb-cdn.synapsegames.com/webgl/{BUILD}/index.html"
PROFILE = {"client_version": "1182000", "client_version_full": "1.182.0",
           "platform": "Web", "unity": "Unity6000_3_6"}


class Response:
    def __init__(self, body, status=200):
        self.body = body.encode() if isinstance(body, str) else body
        self.status_code = status
        self.closed = False

    def iter_content(self, chunk_size):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start:start + chunk_size]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.trust_env = True
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class PublicClientVersionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = Path(self.temporary.name) / "public_client_version.json"
        self.addCleanup(patch.stopall)
        patch.object(version.time, "time", return_value=10000).start()

    def session(self, *, embed=None, index=None, responses=None):
        if responses is None:
            responses = [Response(embed if embed is not None else f'<iframe src="{INDEX}"></iframe>'),
                         Response(index if index is not None else 'productVersion: "1.182.0",')]
        session = Session(responses)
        patch.object(version.requests, "Session", return_value=session).start()
        return session

    def resolve(self, **kwargs):
        return version.resolve_public_client_profile(cache_path=self.cache, **kwargs)

    def cached(self, *, checked_at=9999, profile=None, **extra):
        value = {"schema": 1, "build": BUILD, "checked_at": checked_at,
                 "profile": PROFILE if profile is None else profile}
        value.update(extra)
        self.cache.write_text(json.dumps(value))

    def test_exact_public_build_returns_four_verified_signer_fields(self):
        session = self.session()
        self.assertEqual(self.resolve(), PROFILE)
        self.assertEqual([call[0] for call in session.calls], [version.PUBLIC_EMBED_URL, INDEX])
        self.assertFalse(session.trust_env)
        self.assertTrue(session.closed)
        for _, options in session.calls:
            self.assertFalse(options["allow_redirects"])
            self.assertTrue(options["stream"])
            self.assertNotIn("cookies", options)
            self.assertNotIn("auth", options)
        cached = json.loads(self.cache.read_text())
        self.assertEqual(set(cached), {"schema", "build", "checked_at", "profile"})
        self.assertEqual(cached["profile"], PROFILE)

    def test_previous_verified_public_build_remains_supported(self):
        old = "Throwdown_WebGL_v1.181.0_r39893"
        self.session(embed=f'https://cb-cdn.synapsegames.com/webgl/{old}/index.html',
                     index='productVersion: "1.181.0"')
        self.assertEqual(self.resolve()["client_version"], "1181000")

    def test_valid_cache_sends_no_http_and_returns_an_independent_copy(self):
        self.cached()
        session = self.session(responses=[])
        first = self.resolve()
        first["client_version"] = "changed"
        self.assertEqual(self.resolve(), PROFILE)
        self.assertEqual(session.calls, [])

    def test_exact_one_hour_old_cache_is_refreshed(self):
        self.cached(checked_at=6400)
        session = self.session()
        self.assertEqual(self.resolve(), PROFILE)
        self.assertEqual(len(session.calls), 2)

    def test_forced_refresh_ignores_valid_cache(self):
        self.cached()
        session = self.session()
        self.assertEqual(self.resolve(force_refresh=True), PROFILE)
        self.assertEqual(len(session.calls), 2)

    def test_unknown_build_never_falls_back_to_old_valid_cache(self):
        self.cached()
        self.session(embed='https://cb-cdn.synapsegames.com/webgl/Throwdown_WebGL_v1.183.0_r41000/index.html')
        with self.assertRaises(version.PublicClientVersionError) as caught:
            self.resolve(force_refresh=True)
        self.assertEqual(caught.exception.reason, "public_build_unverified")

    def test_unknown_revision_is_not_assumed_to_share_signing_material(self):
        self.session(embed='https://cb-cdn.synapsegames.com/webgl/Throwdown_WebGL_v1.182.0_r40511/index.html')
        with self.assertRaises(version.PublicClientVersionError) as caught:
            self.resolve()
        self.assertEqual(caught.exception.reason, "public_build_unverified")

    def test_invalid_cached_fields_or_timestamp_cannot_authorize_combat(self):
        variants = [
            {"checked_at": 10001}, {"checked_at": True}, {"checked_at": float("nan")},
            {"profile": {**PROFILE, "client_version": "old"}},
            {"profile": {**PROFILE, "extra": "untrusted"}}, {"extra": "untrusted"},
        ]
        for variant in variants:
            with self.subTest(variant=list(variant)):
                self.cached(**variant)
                session = self.session()
                self.assertEqual(self.resolve(), PROFILE)
                self.assertEqual(len(session.calls), 2)

    def test_corrupted_or_oversized_cache_is_not_used(self):
        for contents in ("{", "x" * (version._MAX_CACHE_BYTES + 1)):
            with self.subTest(size=len(contents)):
                self.cache.write_text(contents)
                session = self.session()
                self.assertEqual(self.resolve(), PROFILE)
                self.assertEqual(len(session.calls), 2)

    def test_network_failure_does_not_use_expired_cache_or_echo_private_error(self):
        self.cached(checked_at=6400)
        private = "synthetic_private_proxy_password"
        self.session(responses=[requests.ConnectionError(private)])
        with self.assertRaises(version.PublicClientVersionError) as caught:
            self.resolve()
        self.assertNotIn(private, str(caught.exception))
        self.assertEqual(caught.exception.reason, "public_launcher_read_failed")

    def test_only_exact_official_index_origin_and_path_are_accepted(self):
        bad_urls = [
            INDEX.replace("https://", "http://"),
            INDEX.replace("cb-cdn.synapsegames.com", "cb-cdn.synapsegames.com.evil.invalid"),
            INDEX.replace("cb-cdn.synapsegames.com", "elsewhere.invalid"),
            INDEX.replace(".com/webgl", ".com:8443/webgl"),
            INDEX + "?token=untrusted", INDEX + "#untrusted", INDEX + ".other",
        ]
        for url in bad_urls:
            with self.subTest(url=url):
                session = self.session(embed=f'<iframe src="{url}">')
                with self.assertRaises(version.PublicClientVersionError):
                    self.resolve(force_refresh=True)
                self.assertEqual(len(session.calls), 1)

    def test_duplicate_identical_index_links_are_accepted(self):
        self.session(embed=f'"{INDEX}" "{INDEX}"')
        self.assertEqual(self.resolve(), PROFILE)

    def test_escaped_json_index_link_is_accepted(self):
        self.session(embed='"' + INDEX.replace("/", "\\/") + '"')
        self.assertEqual(self.resolve(), PROFILE)

    def test_multiple_distinct_build_links_fail_without_following_any(self):
        session = self.session(embed=f'"{INDEX}" "https://cb-cdn.synapsegames.com/webgl/Throwdown_WebGL_v1.181.0_r39893/index.html"')
        with self.assertRaises(version.PublicClientVersionError) as caught:
            self.resolve()
        self.assertEqual(caught.exception.reason, "public_build_ambiguous")
        self.assertEqual(len(session.calls), 1)

    def test_index_product_version_mismatch_or_ambiguity_is_rejected(self):
        for index in ('productVersion: "1.181.0"',
                      'productVersion: "1.182.0", productVersion: "1.181.0"', "missing"):
            with self.subTest(index=index):
                self.session(index=index)
                with self.assertRaises(version.PublicClientVersionError) as caught:
                    self.resolve(force_refresh=True)
                self.assertEqual(caught.exception.reason, "public_build_mismatch")
                self.assertFalse(self.cache.exists())

    def test_redirect_is_not_followed(self):
        response = Response("", status=302)
        session = self.session(responses=[response])
        with self.assertRaises(version.PublicClientVersionError):
            self.resolve()
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(response.closed)

    def test_oversized_embed_or_index_stops_and_closes_response(self):
        for limit, index in ((version._MAX_EMBED_BYTES, False), (version._MAX_INDEX_BYTES, True)):
            with self.subTest(index=index):
                large = Response(b"x" * (limit + 1))
                responses = [Response(f'"{INDEX}"'), large] if index else [large]
                self.session(responses=responses)
                with self.assertRaises(version.PublicClientVersionError) as caught:
                    self.resolve(force_refresh=True)
                self.assertEqual(caught.exception.reason, "public_launcher_limit")
                self.assertTrue(large.closed)
                self.assertFalse(self.cache.exists())

    def test_public_cache_never_contains_fetched_html(self):
        untrusted = "synthetic_private_page_marker"
        self.session(embed=f'"{INDEX}" {untrusted}', index=f'productVersion: "1.182.0" // {untrusted}')
        self.resolve()
        self.assertNotIn(untrusted, self.cache.read_text())

    def test_cache_write_failure_keeps_verified_online_profile(self):
        self.cache.mkdir()
        self.session()
        self.assertEqual(self.resolve(), PROFILE)

    def test_invalid_timeout_fails_before_http(self):
        session = self.session(responses=[])
        for invalid in (0, -1, True, 31, float("inf"), "bad"):
            with self.subTest(timeout=str(invalid)):
                with self.assertRaises(ValueError):
                    self.resolve(timeout=invalid)
        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
