import json
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from atcommander.mom_free_protocol import (
    FIRST_FREE_MOM_MESSAGE,
    first_free_mom_available,
    first_free_mom_fields,
)


class FirstFreeMomProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((Path(__file__).parent / 'fixtures' / 'public_first_free_mom.json').read_text(encoding='utf-8'))

    def matches_fixture(self, actual):
        public = {key: value for key, value in actual.items() if key not in {'adsig', 'msgsig', 'clisig'}}
        digests = {key: hashlib.sha256(actual[key].encode('utf-8')).hexdigest() for key in ('adsig', 'msgsig', 'clisig')}
        return public == self.fixture['expected_public_fields'] and digests == self.fixture['expected_sha256']

    def test_native_public_known_answer(self):
        actual = first_free_mom_fields(self.fixture['identity'], now=self.fixture['timestamp'])
        self.assertTrue(self.matches_fixture(actual), 'Native synthetic request must match the public known-answer digests')
        self.assertEqual(FIRST_FREE_MOM_MESSAGE, 'triggerMysteryBoxAd')

    def test_fresh_clock_and_same_timestamp_for_all_signatures(self):
        with patch('atcommander.mom_free_protocol.time.time', return_value=self.fixture['timestamp'] + 0.9):
            actual = first_free_mom_fields(self.fixture['identity'])
        self.assertTrue(self.matches_fixture(actual))
        self.assertEqual(actual['client_time'], str(self.fixture['timestamp']))

    def test_identity_or_time_changes_all_signatures(self):
        baseline = first_free_mom_fields('synthetic-fixture', now=1700000000)
        for identity, timestamp in (('other-synthetic', 1700000000), ('synthetic-fixture', 1700000001)):
            actual = first_free_mom_fields(identity, now=timestamp)
            self.assertTrue(all(actual[key] != baseline[key] for key in ('adsig', 'msgsig', 'clisig')))

    def test_helper_contains_no_ad_completion_or_purchase_fields(self):
        fields = first_free_mom_fields('synthetic-fixture', now=1700000000)
        self.assertEqual(set(fields), {'adsig', 'msgsig', 'clisig', 'client_time', 'client_version', 'platform', 'client_version_full', 'unity'})
        self.assertTrue(all(len(fields[key]) == 32 and all(c in '0123456789abcdef' for c in fields[key]) for key in ('adsig', 'msgsig', 'clisig')))

    def test_eligibility_requires_explicit_unconsumed_zero_cost(self):
        for state in ({'cost': 0, 'pulls': 0}, {'cost': '0', 'pulls': '0'}):
            self.assertTrue(first_free_mom_available(state))
        for state in (None, {}, {'cost': 0}, {'pulls': 0}, {'cost': 0, 'pulls': 1},
                      {'cost': 25, 'pulls': 0}, {'cost': False, 'pulls': 0},
                      {'cost': 0, 'pulls': True}, {'cost': 0.5, 'pulls': 0},
                      {'cost': 0, 'pulls': 'unknown'}, {'cost': 0, 'pulls': -1}):
            self.assertFalse(first_free_mom_available(state))

    def test_invalid_inputs_do_not_echo_private_material(self):
        for identity in (None, False, ''):
            with self.assertRaises(ValueError):
                first_free_mom_fields(identity, now=1700000000)
        for timestamp in (True, 0, -1, float('nan'), float('inf'), 'private-fixture-value'):
            with self.assertRaises(ValueError) as caught:
                first_free_mom_fields('synthetic-fixture', now=timestamp)
            self.assertNotIn('private-fixture-value', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
