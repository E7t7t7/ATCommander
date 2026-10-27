"""Cancellation contracts for public diagnostic transports; no live targets."""
import json
import threading
import unittest
from unittest.mock import Mock,patch

from atcommander import browser_bridge,protocol_lab


class LegacyCaptureStopTests(unittest.TestCase):
    def test_stopped_observer_never_discovers_or_attaches_targets(self):
        with patch.object(browser_bridge,"_candidate_runtime_targets") as targets, \
             patch.object(browser_bridge.websocket,"create_connection") as connect:
            answer=browser_bridge.capture_next_successful_api_request("startChallenge",should_stop=lambda:True)
        self.assertFalse(answer["ok"]);self.assertTrue(answer["stopped"])
        targets.assert_not_called();connect.assert_not_called()

    def test_observer_stop_after_target_read_never_installs_pointer_probe(self):
        stop=threading.Event()
        def targets(_profile):
            stop.set();return [{"type":"page","webSocketDebuggerUrl":"fixture"}]
        with patch.object(browser_bridge,"_candidate_runtime_targets",side_effect=targets), \
             patch.object(browser_bridge.websocket,"create_connection") as connect:
            answer=browser_bridge.capture_next_successful_api_request("startChallenge",should_stop=stop.is_set)
        self.assertTrue(answer["stopped"]);connect.assert_not_called()

    def test_observer_stop_closes_its_owned_transport_before_return(self):
        stop=threading.Event();socket=Mock()
        def receive():stop.set();raise TimeoutError("Fixture receive timeout")
        socket.recv.side_effect=receive
        with patch.object(browser_bridge,"_candidate_runtime_targets",return_value=[{"type":"worker","webSocketDebuggerUrl":"fixture"}]), \
             patch.object(browser_bridge.websocket,"create_connection",return_value=socket), \
             patch.object(browser_bridge,"_cdp_result",return_value={}),patch.object(browser_bridge,"_cdp"):
            answer=browser_bridge.capture_next_successful_api_request("startChallenge",timeout=5,should_stop=stop.is_set)
        self.assertTrue(answer["stopped"]);socket.close.assert_called_once()
        self.assertNotIn("capture",answer)

    def test_stopped_lab_never_loads_profile_or_connects(self):
        with patch.object(protocol_lab,"load_profiles") as profile,patch.object(protocol_lab.websocket,"create_connection") as connect:
            with self.assertRaisesRegex(RuntimeError,"stopped"):
                protocol_lab.run_swole_signature_lab(should_stop=lambda:True)
        profile.assert_not_called();connect.assert_not_called()

    def lab_fixture(self,phase):
        stop=threading.Event();clock=[0];pressed=[False];ws=Mock();pointer=Mock();methods=[]
        def now():clock[0]+=.25;return clock[0]
        def receive():
            if phase=="preload":stop.set()
            if pressed[0]:
                return json.dumps({"method":"Fetch.requestPaused","params":{"requestId":"public-fixture-request","request":{"url":"https://cb-live.synapsegames.com/api.php?message=startChallenge"}}})
            return json.dumps({"method":"Debugger.scriptParsed","params":{"scriptId":"fixture","url":"https://example.invalid/public.js"}})
        ws.recv.side_effect=receive
        def result(_ws,_ident,method,params,**_kwargs):
            methods.append((method,params))
            if method=="Input.dispatchMouseEvent":
                if params["type"]=="mouseMoved" and phase=="moved":stop.set()
                if params["type"]=="mousePressed":
                    pressed[0]=True
                    if phase=="pressed":stop.set()
            return {}
        return stop,ws,pointer,now,result,methods

    def test_lab_stop_during_preload_or_before_press_never_presses_fight(self):
        for phase in ("preload","moved"):
            stop,ws,pointer,now,result,methods=self.lab_fixture(phase)
            with self.subTest(phase=phase), \
                 patch.object(protocol_lab,"load_profiles",return_value={"swole_battle":{"browser_click_x":10,"browser_click_y":20}}), \
                 patch.object(protocol_lab,"_target",return_value={"type":"iframe","webSocketDebuggerUrl":"fixture"}), \
                 patch.object(protocol_lab.websocket,"create_connection",side_effect=[ws,pointer]), \
                 patch.object(protocol_lab,"_send"),patch.object(protocol_lab,"_result",side_effect=result), \
                 patch.object(protocol_lab.time,"time",side_effect=now):
                with self.assertRaisesRegex(RuntimeError,"stopped"):
                    protocol_lab.run_swole_signature_lab(should_stop=stop.is_set)
            self.assertFalse(any(method=="Input.dispatchMouseEvent" and params["type"]=="mousePressed" for method,params in methods))
            ws.close.assert_called_once()

    def test_lab_stop_after_press_releases_then_blocks_pending_request(self):
        stop,ws,pointer,now,result,methods=self.lab_fixture("pressed");sends=[]
        def send(_ws,_ident,method,params):sends.append(method)
        with patch.object(protocol_lab,"load_profiles",return_value={"swole_battle":{"browser_click_x":10,"browser_click_y":20}}), \
             patch.object(protocol_lab,"_target",return_value={"type":"iframe","webSocketDebuggerUrl":"fixture"}), \
             patch.object(protocol_lab.websocket,"create_connection",side_effect=[ws,pointer]), \
             patch.object(protocol_lab,"_send",side_effect=send),patch.object(protocol_lab,"_result",side_effect=result), \
             patch.object(protocol_lab.time,"time",side_effect=now),patch.object(protocol_lab,"_scan_scripts") as scan:
            with self.assertRaisesRegex(RuntimeError,"stopped"):
                protocol_lab.run_swole_signature_lab(should_stop=stop.is_set)
        self.assertEqual([params["type"] for method,params in methods if method=="Input.dispatchMouseEvent"],
                         ["mouseMoved","mousePressed","mouseReleased"])
        self.assertEqual(sends[-1],"Fetch.failRequest");scan.assert_not_called()
        ws.close.assert_called_once();pointer.close.assert_called_once()


if __name__=="__main__":unittest.main()
