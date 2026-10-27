from copy import deepcopy
import subprocess
import threading
import time
import unittest
from unittest.mock import Mock,patch

from atcommander import embedded_browser as host


PROFILE=r"C:\Users\Example\App Data\ATCommander\learn_browser_profile"
COMMAND=subprocess.list2cmdline([r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                                "--remote-debugging-port=9223","--user-data-dir="+PROFILE])
SOCKET="ws://127.0.0.1:9223/devtools/browser/synthetic"


class FakeWindows:
    def __init__(self):
        self.ids={100:10,200:20};self.parents={100:0,200:0};self.classes={100:"TkChild",200:"Chrome_WidgetWin_1"}
        self.original={"parent":0,"style":host.WS_POPUP|host.WS_CAPTION|host.WS_THICKFRAME|0x10000000,
                       "exstyle":123,"bounds":(-10000,24,640,720)}
        self.values={200:deepcopy(self.original)};self.calls=[];self.candidate_ids=[200];self.failure=None
    def valid(self,hwnd):return hwnd in self.ids
    def pid(self,hwnd):return self.ids[hwnd]
    def class_name(self,hwnd):return self.classes[hwnd]
    def parent(self,hwnd):return self.parents[hwnd]
    def candidates(self,pid):
        self.calls.append(("candidates",pid));return list(self.candidate_ids)
    def snapshot(self,hwnd):return deepcopy(self.values[hwnd])
    def styles(self,hwnd,style,exstyle):
        self.calls.append(("styles",hwnd,style,exstyle));self.values[hwnd].update(style=style,exstyle=exstyle)
        self.fail("styles")
    def set_parent(self,hwnd,parent):
        self.calls.append(("parent",hwnd,parent));self.parents[hwnd]=parent;self.values[hwnd]["parent"]=parent
        self.fail("parent")
    def place(self,hwnd,*bounds):
        self.calls.append(("place",hwnd,*bounds));self.values[hwnd]["bounds"]=bounds;self.fail("place")
    def fail(self,operation):
        if self.failure==operation:
            self.failure=None
            raise RuntimeError("synthetic-private-detail")


class IdentityTests(unittest.TestCase):
    def identity(self,*,processes=None,command=COMMAND,info_error=False):
        def cdp(ws,method):
            self.assertEqual(ws,SOCKET)
            if method=="SystemInfo.getProcessInfo":
                return {"processInfo":processes if processes is not None else [{"type":"browser","id":20},{"type":"renderer","id":30}]}
            if info_error:raise RuntimeError("unsupported")
            return {} if command is None else {"commandLine":command}
        return host._owned_browser(socket=lambda:SOCKET,cdp=cdp,profile_dir=PROFILE)
    def test_exact_browser_pid_and_windows_quoted_saved_profile(self):
        self.assertEqual(self.identity(),(20,SOCKET))
        self.assertTrue(host._profile_matches(COMMAND,PROFILE.lower().replace("\\","/")))
        self.assertTrue(host._profile_matches(subprocess.list2cmdline(["msedge","--user-data-dir",PROFILE,
            "--remote-debugging-port","9223"]),PROFILE))
    def test_missing_command_line_is_supported_but_known_mismatch_is_not(self):
        self.assertEqual(self.identity(command=None),(20,SOCKET))
        self.assertEqual(self.identity(info_error=True),(20,SOCKET))
        for command in (COMMAND.replace("learn_browser_profile","Other"),COMMAND+" --user-data-dir="+PROFILE,
                        COMMAND.replace("9223","9222"),"msedge --remote-debugging-port=9223",
                        "msedge --user-data-dir=relative --remote-debugging-port=9223"):
            with self.subTest(command=command),self.assertRaises(host._Unavailable):self.identity(command=command)
    def test_ambiguous_or_invalid_browser_process_never_selects_renderer(self):
        for rows in ([],[{"type":"renderer","id":20}],[{"type":"browser","id":True}],
                     [{"type":"browser","id":"20"}],[{"type":"browser","id":20},{"type":"browser","id":21}]):
            with self.subTest(rows=rows),self.assertRaises(host._Unavailable):self.identity(processes=rows)
    def test_parser_handles_backslash_quotes_and_refuses_unterminated_values(self):
        args=["edge",r"--user-data-dir=C:\space name\profile",r'--example=quoted "value"',r"C:\end\\"]
        self.assertEqual(host._arguments(subprocess.list2cmdline(args)),args)
        for command in ('"unterminated',None,""):
            with self.assertRaises(host._Unavailable):host._arguments(command)
    def test_socket_discovery_is_loopback_browser_only_and_uncredentialed(self):
        response=Mock(status_code=200);response.json.return_value={"webSocketDebuggerUrl":SOCKET}
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        session=Mock();session.__enter__=Mock(return_value=session);session.__exit__=Mock(return_value=False)
        session.get.return_value=response
        with patch.object(host.requests,"Session",return_value=session):
            self.assertEqual(host._browser_socket(),SOCKET)
            for value in (SOCKET.replace("127.0.0.1","remote.example"),SOCKET.replace("9223","9222"),
                          SOCKET.replace("/browser/","/page/"),SOCKET+"?private=synthetic"):
                response.json.return_value={"webSocketDebuggerUrl":value}
                with self.assertRaises(host._Unavailable):host._browser_socket()
        self.assertFalse(session.trust_env)
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
        self.assertNotIn("auth",session.get.call_args.kwargs)


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.windows=FakeWindows();self.identity=Mock(return_value=(20,SOCKET));self.cdp=Mock()
        self.backend=host.BrowserHostBackend(windows=self.windows,identity=self.identity,cdp=self.cdp,owner_pid=10)
    def attach(self):return self.backend.attach(100,480,370)
    def test_only_exact_owned_pid_child_is_attached_without_activation(self):
        state=self.attach();self.assertTrue(state["embedded"])
        self.assertEqual(self.windows.parents[200],100)
        style=self.windows.values[200]["style"]
        self.assertTrue(style&host.WS_CHILD);self.assertFalse(style&host.WS_POPUP);self.assertFalse(style&host.WS_CAPTION)
        self.assertEqual(self.windows.values[200]["bounds"],(0,0,480,370))
        self.assertEqual(self.windows.calls[0],("candidates",20))
        self.assertFalse(any("foreground" in call[0] or "cursor" in call[0] for call in self.windows.calls))
        self.assertNotIn(SOCKET,str(state));self.assertNotIn("hwnd",state)
    def test_ambiguous_browser_and_foreign_host_are_not_modified(self):
        for candidates in ([],[200,201]):
            self.windows.candidate_ids=candidates
            with self.assertRaises(host._Unavailable):self.attach()
            self.assertFalse(any(call[0] in {"styles","parent","place"} for call in self.windows.calls))
        self.windows.ids[100]=999
        reads=self.identity.call_count
        with self.assertRaises(host._Unavailable):self.attach()
        self.assertEqual(self.identity.call_count,reads)
    def test_wrong_window_class_or_reparented_window_is_rejected(self):
        self.windows.classes[200]="Other"
        with self.assertRaises(host._Unavailable):self.attach()
        self.windows.classes[200]="Chrome_WidgetWin_1";self.windows.values[200]["parent"]=999
        with self.assertRaises(host._Unavailable):self.attach()
        self.assertFalse(any(call[0] in {"styles","parent","place"} for call in self.windows.calls))
    def test_each_attach_failure_restores_parent_styles_and_bounds(self):
        for operation in ("styles","parent","place"):
            with self.subTest(operation=operation):
                self.setUp();self.windows.failure=operation
                with self.assertRaises(host._Unavailable):self.attach()
                self.assertEqual(self.windows.values[200],self.windows.original)
                self.assertEqual(self.windows.parents[200],0);self.assertIsNone(self.backend.record)
    def test_reused_foreign_handle_is_never_modified_during_rollback(self):
        original_place=self.windows.place
        def changed(hwnd,*bounds):
            original_place(hwnd,*bounds);self.windows.ids[200]=999
            raise RuntimeError("synthetic-private-detail")
        self.windows.place=changed
        with self.assertRaisesRegex(host._Unavailable,"window_rollback_failed"):self.attach()
        self.assertEqual([c[0] for c in self.windows.calls],["candidates","styles","parent","place"])
    def test_repeated_attach_only_resizes_preserving_original_rollback_record(self):
        self.attach();original=deepcopy(self.backend.record["original"]);self.windows.calls.clear()
        self.backend.attach(100,520,400)
        self.assertEqual(self.windows.calls,[("place",200,0,0,520,400)])
        self.assertEqual(self.backend.record["original"],original)
        self.backend.detach();self.assertEqual(self.windows.values[200],self.windows.original)
    def test_same_parent_and_dimensions_never_repeat_win32_placement(self):
        self.attach();self.windows.calls.clear()
        self.backend.attach(100,480,370);self.backend.resize(480,370)
        self.assertEqual(self.windows.calls,[])
    def test_client_titlebar_inset_clips_only_chrome_and_restores_original(self):
        answer=self.backend.attach(100,480,370,top_inset=30)
        self.assertEqual((answer["width"],answer["height"]),(480,370))
        self.assertEqual(self.windows.values[200]["bounds"],(0,-30,480,400))
        saved=deepcopy(self.backend.record["original"]);self.windows.calls.clear()
        self.backend.attach(100,480,370,top_inset=30)
        self.backend.resize(480,370)
        self.assertEqual(self.windows.calls,[])
        self.backend.resize(520,400)
        self.assertEqual(self.windows.calls,[("place",200,0,-30,520,430)])
        self.backend.resize(520,400,top_inset=0)
        self.assertEqual(self.windows.calls[-1],("place",200,0,0,520,400))
        self.assertEqual(self.backend.record["original"],saved)
        self.backend.detach();self.assertEqual(self.windows.values[200],self.windows.original)
    def test_invalid_content_inset_never_reads_identity_or_moves_windows(self):
        for value in (-1,129,True,None,30.5,"30"):
            with self.subTest(value=value),self.assertRaises(host._Unavailable):
                self.backend.attach(100,480,370,top_inset=value)
        self.identity.assert_not_called();self.assertEqual(self.windows.calls,[])
    def test_state_checks_destroyed_reused_and_reparented_handles(self):
        for changed in ("destroyed","reused","reparented"):
            with self.subTest(changed=changed):
                self.setUp();self.attach()
                if changed=="destroyed":del self.windows.ids[200]
                elif changed=="reused":self.windows.ids[200]=999
                else:self.windows.parents[200]=999
                self.windows.calls.clear()
                self.assertFalse(self.backend.state()["embedded"])
                self.assertEqual(self.windows.calls,[])
    def test_browser_or_host_change_fails_without_touching_other_windows(self):
        self.attach();self.windows.calls.clear();self.identity.return_value=(21,SOCKET)
        with self.assertRaises(host._Unavailable):self.attach()
        self.assertEqual(self.windows.calls,[])
        self.identity.return_value=(20,SOCKET);self.windows.parents[200]=999
        with self.assertRaises(host._Unavailable):self.attach()
        self.assertEqual(self.windows.calls,[])
    def test_resize_refuses_lost_window_or_invalid_limits(self):
        self.attach();self.windows.calls.clear()
        for values in ((True,370),(99,370),(480,4001)):
            with self.assertRaises(host._Unavailable):self.backend.resize(*values)
        self.assertEqual(self.windows.calls,[])
        del self.windows.ids[200]
        with self.assertRaises(host._Unavailable):self.backend.resize(480,370)
        self.assertIsNone(self.backend.record)
    def test_close_uses_verified_browser_close_once_before_clearing_host(self):
        self.attach();self.windows.calls.clear()
        def closed(ws,method):
            self.assertEqual((ws,method),(SOCKET,"Browser.close"));del self.windows.ids[200]
        self.cdp.side_effect=closed
        self.assertFalse(self.backend.close()["embedded"]);self.cdp.assert_called_once()
        self.assertEqual(self.windows.calls,[])
    def test_close_does_not_replay_unknown_close_or_close_different_browser(self):
        self.attach();self.identity.return_value=(99,SOCKET)
        with self.assertRaises(host._Unavailable):self.backend.close(timeout=0)
        self.cdp.assert_not_called()
        self.identity.return_value=(20,SOCKET);self.cdp.side_effect=RuntimeError("synthetic-private-detail")
        with self.assertRaises(host._Unavailable):self.backend.close(timeout=0)
        self.cdp.assert_called_once();self.assertIsNotNone(self.backend.record)
    def test_close_before_successful_attach_still_closes_verified_owned_browser(self):
        self.assertFalse(self.backend.close()["embedded"])
        self.cdp.assert_called_once_with(SOCKET,"Browser.close")
        self.assertEqual(self.windows.calls,[])
    def test_unattached_close_disconnect_is_confirmed_by_fresh_identity_without_replay(self):
        for unavailable in (host._Unavailable("dedicated_browser_unavailable"),
                            host.requests.ConnectionError("synthetic-private-detail")):
            with self.subTest(kind=type(unavailable).__name__):
                self.setUp()
                self.identity.side_effect=[(20,SOCKET),unavailable]
                self.cdp.side_effect=RuntimeError("synthetic-disconnected-before-reply")
                self.assertFalse(self.backend.close(timeout=0)["embedded"])
                self.assertEqual(self.identity.call_count,2)
                self.cdp.assert_called_once_with(SOCKET,"Browser.close")
                self.assertEqual(self.windows.calls,[])
    def test_unattached_close_does_not_treat_identity_timeout_or_new_browser_as_closed(self):
        for next_identity in (host.requests.Timeout("synthetic-private-detail"),(99,SOCKET)):
            with self.subTest(kind=type(next_identity).__name__):
                self.setUp()
                self.identity.side_effect=[(20,SOCKET),next_identity]
                self.cdp.side_effect=RuntimeError("synthetic-disconnected-before-reply")
                with self.assertRaises(host._Unavailable):self.backend.close(timeout=0)
                self.cdp.assert_called_once_with(SOCKET,"Browser.close")
                self.assertEqual(self.windows.calls,[])
    def test_close_when_not_running_is_harmless_and_profile_mismatch_not_closed(self):
        self.identity.side_effect=host._Unavailable("dedicated_browser_unavailable")
        self.assertFalse(self.backend.close()["embedded"]);self.cdp.assert_not_called()
        self.identity.side_effect=host._Unavailable("dedicated_profile_mismatch")
        with self.assertRaises(host._Unavailable):self.backend.close()
        self.cdp.assert_not_called()
    def test_cold_close_connection_refused_is_already_closed_but_timeout_is_uncertain(self):
        self.identity.side_effect=host.requests.ConnectionError("synthetic-private-detail")
        self.assertFalse(self.backend.close()["embedded"]);self.cdp.assert_not_called()
        self.identity.side_effect=host.requests.Timeout("synthetic-private-detail")
        with self.assertRaises(host.requests.Timeout):self.backend.close()
        self.cdp.assert_not_called()


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.patch=patch.object(host,"_bridge",host._Bridge());self.patch.start();self.addCleanup(self.patch.stop)
        self.windows=FakeWindows();self.backend=host.BrowserHostBackend(windows=self.windows,
            identity=lambda:(20,SOCKET),owner_pid=10,cdp=Mock())
        host.configure(100,backend=self.backend)
    def worker(self,call):
        answer=[];thread=threading.Thread(target=lambda:answer.append(call()));thread.start()
        return thread,answer
    def queued(self):
        deadline=time.monotonic()+1
        while host._bridge.requests.empty() and time.monotonic()<deadline:time.sleep(.001)
        self.assertFalse(host._bridge.requests.empty())
    def test_worker_queues_and_only_main_thread_performs_attach(self):
        thread,answer=self.worker(lambda:host.request_attach(timeout=2));self.queued()
        self.assertEqual(self.windows.calls,[])
        self.assertTrue(host.process_pending()["embedded"]);thread.join(2)
        self.assertFalse(thread.is_alive());self.assertTrue(answer[0]["embedded"])
        self.assertNotIn(SOCKET,str(answer))
    def test_worker_cannot_directly_mutate_or_poll_windows(self):
        thread,answer=self.worker(lambda:host.attach_browser(100));thread.join(2)
        self.assertEqual(answer[0]["reason"],"gui_thread_required");self.assertEqual(self.windows.calls,[])
        thread,answer=self.worker(host.process_pending);thread.join(2)
        self.assertEqual(answer[0]["reason"],"gui_thread_required");self.assertEqual(self.windows.calls,[])
        thread,answer=self.worker(lambda:host.configure(999));thread.join(2)
        self.assertEqual(answer[0]["reason"],"gui_thread_required")
    def test_expired_worker_request_is_not_attached_later(self):
        thread,answer=self.worker(lambda:host.request_attach(timeout=.01));thread.join(1)
        self.assertEqual(answer[0]["reason"],"host_attach_timeout")
        host.process_pending();self.assertEqual(self.windows.calls,[])
    def test_deadline_is_checked_before_gui_action_even_without_worker_cancellation(self):
        request=host._Request(host._bridge.generation,time.monotonic()-1)
        host._bridge.requests.put(request);host.process_pending()
        self.assertTrue(request.event.is_set())
        self.assertEqual(request.result["reason"],"host_request_cancelled")
        self.assertEqual(self.windows.calls,[])
    def test_reconfigured_or_unregistered_host_cancels_old_request(self):
        thread,answer=self.worker(lambda:host.request_attach(timeout=2));self.queued()
        host.configure(None);host.process_pending();thread.join(2)
        self.assertEqual(answer[0]["reason"],"host_request_cancelled");self.assertEqual(self.windows.calls,[])
        self.assertFalse(host.request_attach()["registered"])
    def test_main_attach_resize_and_explicit_detach(self):
        self.assertTrue(host.request_attach()["embedded"])
        self.assertEqual(host.resize_browser(640,420)["width"],640)
        self.assertFalse(host.detach_browser()["embedded"])
        self.assertEqual(self.windows.values[200],self.windows.original)
    def test_registered_inset_applies_to_worker_attach_and_resizes(self):
        host.configure(100,top_inset=30)
        thread,answer=self.worker(lambda:host.request_attach(timeout=2));self.queued()
        host.process_pending();thread.join(2)
        self.assertTrue(answer[0]["embedded"])
        self.assertEqual((answer[0]["width"],answer[0]["height"]),(480,370))
        self.assertEqual(self.windows.values[200]["bounds"],(0,-30,480,400))
        host.resize_browser(640,420)
        self.assertEqual(self.windows.values[200]["bounds"],(0,-30,640,450))
    def test_invalid_inset_does_not_replace_registration_or_queue(self):
        self.assertEqual(host.configure(100,top_inset=129)["reason"],"invalid_content_inset")
        self.assertEqual(host._bridge.top_inset,0)
        self.assertTrue(host.request_attach()["embedded"])
        self.assertEqual(self.windows.values[200]["bounds"],(0,0,480,370))
    def test_errors_are_safe_and_never_include_internal_exception_values(self):
        self.backend.identity=Mock(side_effect=RuntimeError("synthetic-private-detail"))
        answer=host.request_attach();self.assertFalse(answer["embedded"])
        self.assertEqual(answer["reason"],"browser_host_unavailable")
        self.assertNotIn("synthetic-private-detail",str(answer))
    def test_unregister_does_not_implicitly_detach_the_owned_browser(self):
        host.request_attach();self.windows.calls.clear()
        answer=host.configure(None)
        self.assertTrue(answer["embedded"]);self.assertFalse(answer["registered"])
        self.assertEqual(self.windows.calls,[])
    def test_invalid_waits_never_queue_or_modify_the_browser(self):
        for value in (None,"bad",-1,31,float("nan")):
            with self.subTest(value=value):
                self.assertEqual(host.request_attach(value)["reason"],"invalid_host_wait")
        self.assertTrue(host._bridge.requests.empty());self.assertEqual(self.windows.calls,[])
    def test_public_state_never_reports_embedding_after_owned_window_is_destroyed(self):
        self.assertTrue(host.request_attach()["embedded"]);del self.windows.ids[200]
        answer=host.get_embedded_state()
        self.assertFalse(answer["embedded"]);self.assertEqual(answer["width"],0)
        self.assertEqual(answer["reason"],"dedicated_browser_unavailable")


if __name__=="__main__":unittest.main()
