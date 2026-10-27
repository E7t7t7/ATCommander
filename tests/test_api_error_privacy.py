import traceback
import unittest
from unittest.mock import Mock, patch

import requests

from atcommander.api import ATClient, ATApiError
from atcommander.credentials import Credentials


class ApiErrorPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.client = ATClient(Credentials('fixture-user', 'fixture-password'))
        self.client.session = Mock()
        self.client.session.headers = {'Authorization': 'fixture-auth', 'X-Api-Key': 'fixture-api-key'}
        self.client.session.cookies = {'session': 'fixture-cookie'}

    def assert_private_absent(self, error, values):
        rendered = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
        for value in values:
            self.assertNotIn(value, str(error))
            self.assertNotIn(value, rendered)
        self.assertTrue(error.__suppress_context__)

    def test_network_exception_url_and_cause_are_not_exposed(self):
        private = ['fixture-user', 'fixture-password', 'fixture-signature', 'fixture-auth']
        self.client.session.post.side_effect = requests.HTTPError(
            'https://example.test/api?user_id=fixture-user&password=fixture-password&clisig=fixture-signature Authorization=fixture-auth')
        with self.assertRaises(ATApiError) as caught:
            self.client.call('startBattle')
        self.assert_private_absent(caught.exception, private)
        self.assertIn('HTTPError', str(caught.exception))

    def test_json_exception_body_and_cause_are_not_exposed(self):
        response = Mock(content=b'private-body', text='private-body')
        response.json.side_effect = ValueError('fixture-password fixture-cookie')
        self.client.session.post.return_value = response
        with self.assertRaises(ATApiError) as caught:
            self.client.call('startBattle')
        self.assert_private_absent(caught.exception, ['fixture-password', 'fixture-cookie'])
        self.assertEqual(str(caught.exception), 'AT returned a non-JSON response.')

    def test_server_error_scrubs_opaque_known_payload_headers_and_cookies(self):
        payload = {'clisig': 'fixture-client-signature', 'msgsig': 'fixture-message-signature'}
        private = ['fixture-user', 'fixture-password', *payload.values(), 'fixture-auth', 'fixture-api-key', 'fixture-cookie']
        response = Mock(content=b'{}', text='{}')
        response.json.return_value = {'error': {'message': 'warning_157 Invalid combat data: ' + ' / '.join(private)}}
        self.client.session.post.return_value = response
        with self.assertRaises(ATApiError) as caught:
            self.client.call('startBattle', payload)
        self.assert_private_absent(caught.exception, private)
        self.assertIn('warning_157', str(caught.exception))
        self.assertIn('Invalid combat data', str(caught.exception))

    def test_unknown_server_error_fields_and_urls_are_redacted(self):
        response = Mock(content=b'{}', text='{}')
        response.json.return_value = {'error': {'details': {'session_token': 'fixture-unseen-token'}, 'source': 'https://example.test/error?private=fixture-unseen-query'}}
        self.client.session.post.return_value = response
        with self.assertRaises(ATApiError) as caught:
            self.client.call('startBattle')
        self.assert_private_absent(caught.exception, ['fixture-unseen-token', 'fixture-unseen-query'])


if __name__ == '__main__':
    unittest.main()
