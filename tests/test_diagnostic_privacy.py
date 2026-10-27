import json
import unittest
from urllib.parse import quote

from atcommander.diagnostic_privacy import private_literals, safe_text, safe_url, sanitize_report


class DiagnosticPrivacyTests(unittest.TestCase):
    def test_ad_signature_assignments_are_redacted(self):
        self.assertNotIn("synthetic-ad-signature",safe_text('adsig="synthetic-ad-signature"'))
        self.assertEqual(sanitize_report({"adsig":"synthetic-ad-signature"})["adsig"],"<redacted>")
    def test_debugger_websocket_ids_are_suppressed_in_error_text(self):
        text = 'Connection failed to ws://127.0.0.1:9222/devtools/page/fixture-session-id?token=fixture-token'
        result = safe_text(text)
        self.assertNotIn('fixture-session-id', result)
        self.assertNotIn('fixture-token', result)
        self.assertIn('<non-public source>', result)

    def test_source_urls_omit_query_fragment_and_userinfo(self):
        raw = "https://fixture-user:fixture-pass@example.test/game.js?kong_token=fixture-token#fixture-session"
        self.assertEqual(safe_url(raw), "https://example.test/game.js")
        self.assertEqual(safe_url("blob:https://example.test/fixture-session"), "<non-public source>")

    def test_nested_report_and_embedded_json_remove_private_values(self):
        raw = {
            "target": {"url": "https://example.test/game?kong_token=fixture-token"},
            "frames": [{"url": "https://example.test/game.js?session=fixture-session"}],
            "diagnostics": [
                {"payload": json.dumps({"kong_vars": {"game_auth_token": "fixture-nested-token"}})},
                "password=fixture-password&message=startChallenge",
                quote(quote("session_id=fixture-encoded-session&message=startChallenge", safe=""), safe=""),
            ],
        }
        clean = json.dumps(sanitize_report(raw))
        for secret in ("fixture-token", "fixture-session", "fixture-nested-token", "fixture-password", "fixture-encoded-session"):
            self.assertNotIn(secret, clean)
        self.assertIn("startChallenge", clean)

    def test_signature_presence_metadata_survives_without_values(self):
        clean = sanitize_report({
            "clisig": {"present": True, "length": 32},
            "msgsig": "fixture-signature",
            "user_id": 1234567,
        })
        self.assertEqual(clean["clisig"], {"present": True, "length": 32})
        self.assertEqual(clean["msgsig"], "<redacted>")
        self.assertEqual(clean["user_id"], "<redacted>")

    def test_source_excerpts_redact_assignments_and_known_runtime_literals(self):
        source = (
            'const password="fixture-password"; const msgsig="fixture-signature"; '
            'const mystery="fixture-runtime-token"; '
            'const nested="{\\"kong_token\\":\\"fixture-escaped-token\\"}"; '
            'fetchWithProgress("https://example.test/game.js?kong_token=fixture-url-token");'
        )
        clean = safe_text(source, {"fixture-runtime-token"})
        for secret in ("fixture-password", "fixture-signature", "fixture-runtime-token", "fixture-escaped-token", "fixture-url-token"):
            self.assertNotIn(secret, clean)
        self.assertIn("fetchWithProgress", clean)

    def test_encoded_private_request_literals_are_collected_locally(self):
        raw = {"kong_vars": json.dumps({"game_auth_token": "fixture-inner-token"}), "password": "fixture-password", "message": "startChallenge"}
        literals = private_literals(raw)
        self.assertIn("fixture-inner-token", literals)
        self.assertIn("fixture-password", literals)
        self.assertNotIn("startChallenge", literals)
        self.assertNotIn("fixture-inner-token", safe_text('literal="fixture-inner-token"', literals))

    def test_encoded_json_dictionary_keys_cannot_persist_private_payload(self):
        clean = sanitize_report({json.dumps({"password": "fixture-hidden-password"}): "value"})
        self.assertNotIn("fixture-hidden-password", json.dumps(clean))


if __name__ == "__main__":
    unittest.main()
