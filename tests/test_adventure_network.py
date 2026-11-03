import unittest
from unittest.mock import Mock
from atcommander.api import ATNetworkError
from atcommander.adventure_runner import AdventureRunner,AdventureReadStopped

class AdventureNetworkTests(unittest.TestCase):
 def setUp(self):
  self.events=[];self.runner=AdventureRunner(107,event=self.events.append)
  self.runner._stop=Mock();self.runner._stop.is_set.return_value=False;self.runner._stop.wait.return_value=False
 def test_read_recovers_after_transient_failure(self):
  read=Mock(side_effect=[ATNetworkError('HTTPError',http_status=503),{'ready':True}])
  self.assertEqual(self.runner._read('account refresh',read),{'ready':True})
  self.assertEqual(read.call_count,2);self.runner._stop.wait.assert_called_once_with(5)
 def test_retries_are_bounded(self):
  read=Mock(side_effect=ATNetworkError('ConnectionError'))
  with self.assertRaises(ATNetworkError):self.runner._read('account refresh',read)
  self.assertEqual(read.call_count,4)
  self.assertEqual([c.args[0]for c in self.runner._stop.wait.call_args_list],[5,15,30])
 def test_forbidden_response_is_not_retried(self):
  read=Mock(side_effect=ATNetworkError('HTTPError',http_status=403,retryable_read=False))
  with self.assertRaises(ATNetworkError):self.runner._read('account refresh',read)
  read.assert_called_once();self.runner._stop.wait.assert_not_called()
 def test_stop_interrupts_backoff(self):
  self.runner._stop.wait.return_value=True;read=Mock(side_effect=ATNetworkError())
  with self.assertRaises(AdventureReadStopped):self.runner._read('account refresh',read)
  read.assert_called_once()
 def test_stop_before_read_makes_no_request(self):
  self.runner._stop.is_set.return_value=True;read=Mock()
  with self.assertRaises(AdventureReadStopped):self.runner._read('account refresh',read)
  read.assert_not_called()
 def test_battle_start_never_retried(self):
  from atcommander.api import ATApiError
  send=Mock(side_effect=ATNetworkError('HTTPError',http_status=503))
  with self.assertRaisesRegex(ATApiError,'not repeated'):self.runner._send_once('battle start',send)
  send.assert_called_once();self.runner._stop.wait.assert_not_called()
 def test_status_is_allowlisted_and_no_private_text(self):
  self.assertIn('HTTP 503',str(ATNetworkError('HTTPError',http_status=503)))
  self.assertNotIn('private',str(ATNetworkError('HTTPError',http_status='private')))
  self.assertIsNone(ATNetworkError('HTTPError',http_status=True).http_status)

 def test_stop_still_allows_final_charged_fight_verification(self):
  self.runner._stop.is_set.return_value=True;read=Mock(return_value={'wins':1})
  self.assertEqual(self.runner._read('outcome',read,finish_current=True),{'wins':1})
  read.assert_called_once()
