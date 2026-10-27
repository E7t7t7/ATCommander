import json
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from atcommander.combat_signing import (
    DEFAULT_CLIENT_VERSION,
    DEFAULT_CLIENT_VERSION_FULL,
    DEFAULT_PLATFORM,
    DEFAULT_UNITY,
    generate_signatures,
    sign_combat_fields,
)


class CombatSigningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((Path(__file__).parent / 'fixtures' / 'public_combat_signing.json').read_text(encoding='utf-8'))

    def test_public_build_synthetic_known_answer(self):
        inputs = {key: value for key, value in self.fixture.items() if key != 'expected_sha256'}
        actual = generate_signatures(**inputs)
        actual_digests = {key: hashlib.sha256(value.encode('utf-8')).hexdigest() for key, value in actual.items()}
        self.assertTrue(actual_digests == self.fixture['expected_sha256'], 'Known synthetic answer digests must match public client formulas')

    def test_message_name_changes_only_message_signature(self):
        first = generate_signatures('fixture-user', 'startChallenge', 1700000000)
        second = generate_signatures('fixture-user', 'fightGuildSiege', 1700000000)
        self.assertTrue(first['clisig'] == second['clisig'])
        self.assertTrue(first['msgsig'] != second['msgsig'])

    def test_identity_and_time_change_both_signatures(self):
        first = generate_signatures('fixture-user', 'startChallenge', 1700000000)
        for identity, timestamp in [('another-fixture', 1700000000), ('fixture-user', 1700000001)]:
            other = generate_signatures(identity, 'startChallenge', timestamp)
            self.assertTrue(all(first[key] != other[key] for key in first))

    def test_fresh_signing_overwrites_stale_fields_without_mutating_input(self):
        stale = {'client_time': '1', 'msgsig': 'stale-fixture', 'clisig': 'stale-fixture', 'client_version': 'old', 'client_version_full': 'old', 'unity': 'old', 'challenge_id': 'fixture-challenge', 'platform': 'stale-fixture'}
        original = dict(stale)
        with patch('atcommander.combat_signing.time.time', return_value=1700000000.9):
            result = sign_combat_fields(stale, 'startChallenge', user_id='fixture-user')
        self.assertEqual(stale, original)
        self.assertEqual(result['client_time'], '1700000000')
        self.assertEqual(result['client_version'], DEFAULT_CLIENT_VERSION)
        self.assertEqual(result['platform'], DEFAULT_PLATFORM)
        self.assertEqual(result['client_version_full'], DEFAULT_CLIENT_VERSION_FULL)
        self.assertEqual(result['unity'], DEFAULT_UNITY)
        self.assertEqual(result['challenge_id'], 'fixture-challenge')
        self.assertTrue(all(hashlib.sha256(result[key].encode('utf-8')).hexdigest() == self.fixture['expected_sha256'][key] for key in ('msgsig', 'clisig')))

    def test_explicit_client_override_and_arbitrary_combat_payload(self):
        result = sign_combat_fields({'slot_id': 4, 'combat_data': 'fixture-payload'}, 'fightGuildSiege', user_id='fixture-user', now=1700000000, client_version='fixture-version', platform='fixture-platform')
        expected = generate_signatures('fixture-user', 'fightGuildSiege', 1700000000, 'fixture-version', 'fixture-platform')
        self.assertTrue(all(result[key] == expected[key] for key in expected))
        self.assertEqual(result['slot_id'], 4)
        self.assertEqual(result['platform'], 'fixture-platform')
        self.assertEqual(result['combat_data'], 'fixture-payload')

    def test_utf8_inputs_and_fractional_timestamp_are_deterministic(self):
        whole = generate_signatures('synthetic-ü', 'synthetic-⚙', 1700000000)
        fraction = generate_signatures('synthetic-ü', 'synthetic-⚙', 1700000000.8)
        self.assertTrue(whole == fraction)
        self.assertTrue(all(len(value) == 32 and all(c in '0123456789abcdef' for c in value) for value in whole.values()))

    def test_invalid_inputs_fail_without_echoing_private_material(self):
        for timestamp in (None, True, -1, 0, float('nan'), float('inf'), 'private-fixture-value'):
            with self.subTest(timestamp_type=type(timestamp).__name__):
                with self.assertRaises(ValueError) as caught:
                    generate_signatures('fixture-user', 'startChallenge', timestamp)
                self.assertNotIn('private-fixture-value', str(caught.exception))
        for identity, message in [(None, 'startChallenge'), ('', 'startChallenge'), ('fixture-user', '')]:
            with self.assertRaises(ValueError):
                generate_signatures(identity, message, 1700000000)


if __name__ == '__main__':
    unittest.main()
