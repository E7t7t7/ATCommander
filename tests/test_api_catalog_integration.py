import unittest
from unittest.mock import Mock, patch
import requests

from atcommander.api import ATClient, ATApiError
from atcommander.credentials import Credentials


def response(payload):
    result=Mock(status_code=200,content=b"{}",text="{}")
    result.json.return_value=payload
    return result


class TransportAndCatalogTests(unittest.TestCase):
    def setUp(self):
        self.client=ATClient(Credentials("fixture-user","fixture-password"))

    def test_each_call_builds_fresh_time_and_keeps_action_fields(self):
        self.client.session.post=Mock(return_value=response({"result":True}))
        with patch("atcommander.api.time.time",side_effect=[100.9,101.1]):
            self.client.call("fixtureRead",{"mission_id":"9258"})
            self.client.call("fixtureRead",{"mission_id":"9259"})
        first,second=self.client.session.post.call_args_list
        self.assertEqual("100",first.kwargs["data"]["client_time"])
        self.assertEqual("101",second.kwargs["data"]["client_time"])
        self.assertEqual("9258",first.kwargs["data"]["mission_id"])
        self.assertEqual("9259",second.kwargs["data"]["mission_id"])
        self.assertIsNot(first.kwargs["data"],second.kwargs["data"])

    def test_explicit_client_time_is_preserved(self):
        self.client.session.post=Mock(return_value=response({"result":True}))
        self.client.call("fixtureRead",{"client_time":"102"})
        self.assertEqual("102",self.client.session.post.call_args.kwargs["data"]["client_time"])

    def test_network_exception_does_not_expose_private_url(self):
        self.client.session.post=Mock(side_effect=requests.ConnectionError("https://example.test/?user_id=fixture-user&password=fixture-password"))
        with self.assertRaises(ATApiError) as caught:self.client.call("fixtureRead")
        self.assertNotIn("fixture-user",str(caught.exception))
        self.assertNotIn("fixture-password",str(caught.exception))

    def test_rejected_init_cannot_replace_good_cached_snapshot(self):
        good={"user_data":{"energy":"10"},"mission_data":{"9258":{"id":9258,"campaign_id":9040}}}
        self.client.call=Mock(side_effect=[good,{"result":False}])
        self.client.init();self.client.init()
        self.assertIs(good,self.client._init_snapshot)

    def test_init_catalogs_do_not_trigger_cdn_or_browser_discovery(self):
        self.client._init_snapshot={"user_data":{"energy":"10"},
            "mission_data":{"9258":{"id":9258,"campaign_id":9040}},
            "campaign_data":{"9040":{"id":9040,"name":"Throwdown Celebration","missions":[9258]}}}
        self.client.get_static_files=Mock(side_effect=AssertionError("Authoritative init catalog needs no additional call"))
        self.client.session.get=Mock(side_effect=AssertionError("Authoritative init catalog needs no CDN GET"))
        self.assertEqual(["9258"],list(self.client.mission_catalog()))
        self.assertEqual(["9040"],list(self.client.campaign_catalog()))
        self.client.get_static_files.assert_not_called()
        self.client.session.get.assert_not_called()

    def test_static_candidates_reuse_existing_init_version_without_refresh(self):
        expected=[f"{host}/{prefix}_fixture-version.json"
            for host in ("https://cb-cdn.synapsegames.com/data","https://cb-cdn.synapse-games.com/data")
            for prefix in ("missions","mission_data","mission")]
        self.client.init=Mock(side_effect=AssertionError("Cached init needs no repeat API call"))
        for snapshot in ({"mission_data_version":"fixture-version"},
                         {"request":{"mission_data_version":"fixture-version"}}):
            with self.subTest(snapshot_shape=list(snapshot)):
                self.client._init_snapshot=snapshot
                self.assertEqual(expected,self.client._versioned_static_candidates("mission_data"))
        self.client.init.assert_not_called()

    def test_static_candidates_fetch_init_when_no_snapshot_exists(self):
        self.client.init=Mock(return_value={"item_data_version":"fixture-version"})
        expected=[f"{host}/{prefix}_fixture-version.json"
            for host in ("https://cb-cdn.synapsegames.com/data","https://cb-cdn.synapse-games.com/data")
            for prefix in ("items","item_data","item")]
        self.assertEqual(expected,self.client._versioned_static_candidates("item_data"))
        self.client.init.assert_called_once_with()

    def test_wrong_json_candidate_does_not_prevent_later_real_catalog(self):
        self.client._init_snapshot={"user_data":{"energy":"10"}}
        self.client.get_static_files=Mock(return_value={})
        self.client._find_static_url=Mock(return_value="https://example.test/first")
        self.client._versioned_static_candidates=Mock(return_value=["https://example.test/second"])
        self.client.session.get=Mock(side_effect=[response({"config":{"enabled":True}}),
                                                  response({"mission_data":[{"mission_id":9258,"campaign_id":9040}]})])
        with patch("atcommander.browser_bridge.discover_static_resource_urls",return_value=[]), \
             patch("atcommander.browser_bridge.fetch_json_resource_in_browser",return_value=None):
            rows=self.client.mission_catalog()
        self.assertEqual({"9258":{"mission_id":9258,"campaign_id":9040}},rows)
        self.assertEqual(2,self.client.session.get.call_count)

if __name__=="__main__":unittest.main()
