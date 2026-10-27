"""Transport typing preserves privacy and never repeats an API mutation."""
import json
import traceback
import unittest
from unittest.mock import Mock, patch

import requests

from atcommander.api import ATApiError, ATClient, ATNetworkError
from atcommander.credentials import Credentials


PRIVATE="synthetic-private-query-value"
PUBLIC_URL="https://cb-cdn.synapsegames.com/data/public-catalog.json"


def response(payload=None,*,status=200,body=None):
    result=requests.Response()
    result.status_code=status
    result.url="https://example.test/?private="+PRIVATE
    result._content=json.dumps(payload).encode() if body is None else body
    return result


class ApiNetworkErrorTests(unittest.TestCase):
    def setUp(self):
        self.client=ATClient(Credentials("synthetic-user","synthetic-password"))
        self.client.session=Mock(headers={},cookies={})

    def assert_private(self,error):
        rendered="".join(traceback.format_exception(type(error),error,error.__traceback__))
        for private in (PRIVATE,"synthetic-user","synthetic-password"):
            self.assertNotIn(private,str(error))
            self.assertNotIn(private,rendered)
        self.assertTrue(error.__suppress_context__)

    def test_default_constructor_and_category_are_fixed_safe_literals(self):
        error=ATNetworkError()
        self.assertEqual(error.category,"ReadTimeout")
        self.assertTrue(error.retryable_read)
        self.assertEqual(str(error),"Network/API request failed (ReadTimeout).")
        self.assertIsInstance(error,ATApiError)
        unsafe=ATNetworkError(PRIVATE,retryable_read=False)
        self.assertEqual(unsafe.category,"RequestException")
        self.assertNotIn(PRIVATE,str(unsafe))
        self.assertFalse(unsafe.retryable_read)

    def test_transport_timeouts_on_reads_and_writes_are_sent_exactly_once(self):
        for message in ("init","getUserAccount","researchCombo","useResearchSpeedUpItem","completeResearch"):
            with self.subTest(message=message):
                self.client.session.post=Mock(side_effect=requests.ReadTimeout("private="+PRIVATE))
                with self.assertRaises(ATNetworkError) as caught:self.client.call(message)
                self.assertEqual(self.client.session.post.call_count,1)
                self.assertEqual(caught.exception.category,"ReadTimeout")
                self.assertTrue(caught.exception.retryable_read)
                self.assert_private(caught.exception)

    def test_connection_and_timeout_categories_are_retryable_only_for_caller_reads(self):
        for kind in (requests.ConnectionError,requests.Timeout,requests.ConnectTimeout,requests.exceptions.ProxyError):
            with self.subTest(kind=kind.__name__):
                self.client.session.post=Mock(side_effect=kind(PRIVATE))
                with self.assertRaises(ATNetworkError) as caught:self.client.call("researchCombo")
                self.assertEqual(caught.exception.category,kind.__name__)
                self.assertTrue(caught.exception.retryable_read)
                self.client.session.post.assert_called_once()
                self.assert_private(caught.exception)

    def test_certificate_and_invalid_configuration_errors_are_not_retryable(self):
        for name in ("SSLError","InvalidURL","InvalidProxyURL","MissingSchema","InvalidSchema",
                     "URLRequired","InvalidHeader","TooManyRedirects"):
            with self.subTest(name=name):
                self.client.session.post=Mock(side_effect=getattr(requests.exceptions,name)(PRIVATE))
                with self.assertRaises(ATNetworkError) as caught:self.client.call("init")
                self.assertEqual(caught.exception.category,name)
                self.assertFalse(caught.exception.retryable_read)
                self.assert_private(caught.exception)

    def test_http_retry_allowlist_uses_status_even_for_falsey_error_response(self):
        for status in (408,429,500,502,503,504,400,401,403,404,409,501,505):
            with self.subTest(status=status):
                reply=response({},status=status)
                self.assertFalse(bool(reply))
                self.client.session.post=Mock(return_value=reply)
                with self.assertRaises(ATNetworkError) as caught:self.client.call("init")
                self.assertEqual(caught.exception.category,"HTTPError")
                self.assertEqual(caught.exception.retryable_read,status in {408,429,500,502,503,504})
                self.client.session.post.assert_called_once()
                self.assert_private(caught.exception)

    def test_http_error_without_response_has_no_transient_assumption(self):
        self.client.session.post=Mock(side_effect=requests.HTTPError(PRIVATE))
        with self.assertRaises(ATNetworkError) as caught:self.client.call("init")
        self.assertFalse(caught.exception.retryable_read)
        self.assert_private(caught.exception)

    def test_unknown_request_exception_type_name_is_not_exposed_or_retryable(self):
        kind=type(PRIVATE,(requests.RequestException,),{})
        self.client.session.post=Mock(side_effect=kind(PRIVATE))
        with self.assertRaises(ATNetworkError) as caught:self.client.call("init")
        self.assertEqual(caught.exception.category,"RequestException")
        self.assertFalse(caught.exception.retryable_read)
        self.assert_private(caught.exception)

    def test_non_json_response_is_permanent_and_body_is_not_exposed(self):
        self.client.session.post=Mock(return_value=response(body=("<html>"+PRIVATE).encode()))
        with self.assertRaises(ATApiError) as caught:self.client.call("init")
        self.assertNotIsInstance(caught.exception,ATNetworkError)
        self.assertEqual(str(caught.exception),"AT returned a non-JSON response.")
        self.client.session.post.assert_called_once()
        self.assert_private(caught.exception)

    def test_empty_response_behavior_is_preserved_without_a_second_request(self):
        self.client.session.post=Mock(return_value=response(body=b""))
        self.assertEqual(self.client.call("completeResearch"),{})
        self.client.session.post.assert_called_once()


class CatalogNetworkErrorTests(unittest.TestCase):
    def setUp(self):
        self.client=ATClient(Credentials("synthetic-user","synthetic-password"))
        self.client.session=Mock(headers={},cookies={})
        self.client._init_snapshot={"user_data":{"energy":1}}
        self.client._versioned_static_candidates=Mock(return_value=[])
        self.discovery=patch("atcommander.browser_bridge.discover_static_resource_urls",return_value=[])
        self.browser=patch("atcommander.browser_bridge.fetch_json_resource_in_browser",return_value=None)
        self.discovery.start();self.browser.start()
        self.addCleanup(self.discovery.stop);self.addCleanup(self.browser.stop)

    def test_combo_download_timeout_remains_typed_without_repeating_get(self):
        self.client.get_static_files=Mock(return_value={"static_files":{"combo_data_compressed":PUBLIC_URL}})
        self.client.session.get=Mock(side_effect=requests.ConnectionError(PRIVATE))
        with self.assertRaises(ATNetworkError) as caught:self.client.combo_catalog()
        self.assertTrue(caught.exception.retryable_read)
        self.assertEqual(caught.exception.category,"ConnectionError")
        self.assertNotIn(PRIVATE,str(caught.exception))
        self.client.get_static_files.assert_called_once()
        self.client.session.get.assert_called_once()

    def test_combo_manifest_timeout_is_not_swallowed_or_automatically_retried(self):
        self.client.get_static_files=Mock(side_effect=ATNetworkError("ReadTimeout"))
        with self.assertRaises(ATNetworkError):self.client.combo_catalog()
        self.client.get_static_files.assert_called_once()
        self.client.session.get.assert_not_called()

    def test_combo_malformed_json_or_schema_is_not_a_retryable_network_error(self):
        self.client.get_static_files=Mock(return_value={"static_files":{"combo_data_compressed":PUBLIC_URL}})
        for reply in (response(body=PRIVATE.encode()),response([])):
            with self.subTest(reply=reply.status_code):
                self.client.session.get=Mock(return_value=reply)
                with self.assertRaises(ATApiError) as caught:self.client.combo_catalog()
                self.assertNotIsInstance(caught.exception,ATNetworkError)
                self.assertNotIn(PRIVATE,str(caught.exception))
                self.client.session.get.assert_called_once()

    def test_combo_http_not_found_is_permanent(self):
        self.client.get_static_files=Mock(return_value={"static_files":{"combo_data_compressed":PUBLIC_URL}})
        self.client.session.get=Mock(return_value=response({},status=404))
        with self.assertRaises(ATNetworkError) as caught:self.client.combo_catalog()
        self.assertFalse(caught.exception.retryable_read)
        self.client.session.get.assert_called_once()

    def test_static_manifest_timeout_survives_when_no_fallback_url_exists(self):
        self.client.get_static_files=Mock(side_effect=ATNetworkError("ReadTimeout"))
        with self.assertRaises(ATNetworkError) as caught:self.client.item_catalog()
        self.assertEqual(caught.exception.category,"ReadTimeout")
        self.assertTrue(caught.exception.retryable_read)
        self.client.get_static_files.assert_called_once()
        self.client.session.get.assert_not_called()

    def test_static_valid_fallback_still_succeeds_after_manifest_timeout(self):
        self.client.get_static_files=Mock(side_effect=ATNetworkError("ReadTimeout"))
        self.client._versioned_static_candidates.return_value=[PUBLIC_URL]
        self.client.session.get=Mock(return_value=response({"item_data":{"1031":{"id":1031,"name":"Research Speedup"}}}))
        self.assertEqual(self.client.item_catalog(),{"1031":{"id":1031,"name":"Research Speedup"}})
        self.client.get_static_files.assert_called_once()
        self.client.session.get.assert_called_once()

    def test_static_download_timeout_is_not_replaced_by_browser_fallback_failure(self):
        self.client.get_static_files=Mock(return_value={"static_files":{"item_data":PUBLIC_URL}})
        self.client.session.get=Mock(side_effect=requests.ReadTimeout(PRIVATE))
        with self.assertRaises(ATNetworkError) as caught:self.client.item_catalog()
        self.assertEqual(caught.exception.category,"ReadTimeout")
        self.assertTrue(caught.exception.retryable_read)
        self.assertNotIn(PRIVATE,str(caught.exception))
        self.client.session.get.assert_called_once()

    def test_static_certificate_failure_stays_nonretryable(self):
        self.client.get_static_files=Mock(return_value={"static_files":{"item_data":PUBLIC_URL}})
        self.client.session.get=Mock(side_effect=requests.exceptions.SSLError(PRIVATE))
        with self.assertRaises(ATNetworkError) as caught:self.client.item_catalog()
        self.assertEqual(caught.exception.category,"SSLError")
        self.assertFalse(caught.exception.retryable_read)

    def test_static_json_schema_and_not_found_remain_permanent(self):
        self.client.get_static_files=Mock(return_value={"static_files":{"item_data":PUBLIC_URL}})
        for reply in (response(body=PRIVATE.encode()),response({"config":{"enabled":True}}),response({},status=404)):
            with self.subTest(reply=reply.status_code):
                self.client.session.get=Mock(return_value=reply)
                with self.assertRaises(ATApiError) as caught:self.client.item_catalog()
                self.assertFalse(isinstance(caught.exception,ATNetworkError) and caught.exception.retryable_read)
                self.assertNotIn(PRIVATE,str(caught.exception))
                self.client.session.get.assert_called_once()

    def test_other_fallback_candidate_can_succeed_without_repeating_failed_url(self):
        self.client.get_static_files=Mock(return_value={"static_files":{"item_data":PUBLIC_URL}})
        alternate="https://cb-cdn.synapsegames.com/data/alternate-public.json"
        self.client._versioned_static_candidates.return_value=[alternate]
        self.client.session.get=Mock(side_effect=[requests.ReadTimeout(PRIVATE),response({"1031":{"id":1031,"name":"Research Speedup"}})])
        self.assertEqual(list(self.client.item_catalog()),["1031"])
        self.assertEqual([call.args[0] for call in self.client.session.get.call_args_list],[PUBLIC_URL,alternate])

    def test_embedded_catalog_avoids_manifest_download_and_browser_discovery(self):
        self.client._init_snapshot["item_data"]={"1031":{"id":1031,"name":"Research Speedup"}}
        self.client.get_static_files=Mock(side_effect=AssertionError("No manifest request"))
        self.assertEqual(list(self.client.item_catalog()),["1031"])
        self.client.get_static_files.assert_not_called()
        self.client.session.get.assert_not_called()


if __name__=="__main__":unittest.main()
