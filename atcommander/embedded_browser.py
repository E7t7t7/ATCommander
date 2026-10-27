"""Attach only Commander's existing dedicated Edge app to its own Tk host.

All window changes belong to the product's main GUI thread. Worker requests
cross a queue; this module never calls Tk, activates a desktop window, moves the
Windows cursor, changes a profile or exposes debugging/session identifiers.
"""
from __future__ import annotations

from dataclasses import dataclass,field
import ctypes
from ctypes import wintypes
import ntpath
import os
import queue
import sys
import threading
import time
from urllib.parse import urlparse

import requests
import websocket

from .config import APP_DIR

PORT=9223
PROFILE_DIR=APP_DIR/"learn_browser_profile"
WS_CHILD=0x40000000
WS_POPUP=0x80000000
WS_CAPTION=0x00C00000
WS_THICKFRAME=0x00040000
WS_SYSMENU=0x00080000
WS_MINIMIZEBOX=0x00020000
WS_MAXIMIZEBOX=0x00010000


class _Unavailable(Exception):
    def __init__(self,reason):self.reason=reason


def _dimensions(width,height):
    if any(isinstance(n,bool) or not isinstance(n,int) or not 100<=n<=4000 for n in (width,height)):
        raise _Unavailable("invalid_host_size")
    return width,height


def _inset(value):
    if isinstance(value,bool) or not isinstance(value,int) or not 0<=value<=128:
        raise _Unavailable("invalid_content_inset")
    return value


def _arguments(command):
    """Parse Windows double-quote/backslash argument rules, without a shell."""
    if not isinstance(command,str) or not command or len(command)>32768:
        raise _Unavailable("browser_identity_unavailable")
    result=[];pos=0
    while pos<len(command):
        while pos<len(command) and command[pos] in " \t":pos+=1
        if pos==len(command):break
        value=[];quoted=False
        while pos<len(command) and (quoted or command[pos] not in " \t"):
            if command[pos]=="\\":
                start=pos
                while pos<len(command) and command[pos]=="\\":pos+=1
                count=pos-start
                if pos<len(command) and command[pos]=='"':
                    value.append("\\"*(count//2))
                    if count%2:value.append('"')
                    else:quoted=not quoted
                    pos+=1
                else:value.append("\\"*count)
            elif command[pos]=='"':quoted=not quoted;pos+=1
            else:value.append(command[pos]);pos+=1
        if quoted:raise _Unavailable("browser_identity_unavailable")
        result.append("".join(value))
    return result


def _profile_matches(command,profile_dir):
    arguments=_arguments(command);profiles=[];ports=[]
    for index,arg in enumerate(arguments):
        if arg=="--user-data-dir":
            if index+1>=len(arguments):return False
            profiles.append(arguments[index+1])
        elif arg.startswith("--user-data-dir="):profiles.append(arg.split("=",1)[1])
        if arg=="--remote-debugging-port":
            if index+1>=len(arguments):return False
            ports.append(arguments[index+1])
        elif arg.startswith("--remote-debugging-port="):ports.append(arg.split("=",1)[1])
    expected=ntpath.normcase(ntpath.normpath(str(profile_dir)))
    return (len(profiles)==1 and ntpath.isabs(profiles[0])
            and ntpath.normcase(ntpath.normpath(profiles[0]))==expected and ports==[str(PORT)])


def _browser_socket():
    with requests.Session() as session:
        session.trust_env=False
        with session.get(f"http://127.0.0.1:{PORT}/json/version",timeout=(1,2),allow_redirects=False) as response:
            if response.status_code!=200:raise _Unavailable("dedicated_browser_unavailable")
            value=response.json()
    ws=value.get("webSocketDebuggerUrl") if isinstance(value,dict) else None
    if not isinstance(ws,str):raise _Unavailable("dedicated_browser_unavailable")
    try:
        parts=urlparse(ws)
        if (parts.scheme!="ws" or parts.hostname not in {"127.0.0.1","localhost"} or parts.port!=PORT
                or parts.username or parts.password or parts.query or parts.fragment
                or not parts.path.startswith("/devtools/browser/")):
            raise _Unavailable("browser_identity_unavailable")
    except ValueError:raise _Unavailable("browser_identity_unavailable") from None
    return ws


def _cdp(ws,method):
    connection=websocket.create_connection(ws,timeout=2,suppress_origin=True)
    try:
        import json
        connection.send(json.dumps({"id":1,"method":method,"params":{}}))
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            result=json.loads(connection.recv())
            if result.get("id")!=1:continue
            if result.get("error"):raise _Unavailable("browser_identity_unavailable")
            return result.get("result")or{}
        raise _Unavailable("dedicated_browser_unavailable")
    finally:connection.close()


def _owned_browser(*,socket=None,cdp=None,profile_dir=None):
    ws=(socket or _browser_socket)();invoke=cdp or _cdp
    processes=invoke(ws,"SystemInfo.getProcessInfo").get("processInfo")
    if not isinstance(processes,list):raise _Unavailable("browser_identity_unavailable")
    candidates=[row.get("id") for row in processes if isinstance(row,dict) and row.get("type")=="browser"]
    if (len(candidates)!=1 or isinstance(candidates[0],bool)
            or not isinstance(candidates[0],int) or not 1<=candidates[0]<2**32):
        raise _Unavailable("browser_identity_unavailable")
    # Older CDP builds may omit commandLine. If it is available, a mismatched or
    # ambiguous profile/port is a hard failure, never a title-based fallback.
    try:info=invoke(ws,"SystemInfo.getInfo")
    except Exception:info={}
    if isinstance(info,dict) and info.get("commandLine"):
        if not _profile_matches(info["commandLine"],profile_dir or PROFILE_DIR):
            raise _Unavailable("dedicated_profile_mismatch")
    return candidates[0],ws


class _Windows:
    """Pointer-sized Win32 adapter; constructed only by product GUI actions."""
    def __init__(self):
        if sys.platform!="win32":raise _Unavailable("windows_host_unavailable")
        self.u=ctypes.WinDLL("user32",use_last_error=True)
        self.callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
        specs={
            "IsWindow":([wintypes.HWND],wintypes.BOOL),
            "EnumWindows":([self.callback,wintypes.LPARAM],wintypes.BOOL),
            "GetWindowThreadProcessId":([wintypes.HWND,ctypes.POINTER(wintypes.DWORD)],wintypes.DWORD),
            "GetClassNameW":([wintypes.HWND,wintypes.LPWSTR,ctypes.c_int],ctypes.c_int),
            "GetParent":([wintypes.HWND],wintypes.HWND),
            "SetParent":([wintypes.HWND,wintypes.HWND],wintypes.HWND),
            "GetWindowRect":([wintypes.HWND,ctypes.POINTER(wintypes.RECT)],wintypes.BOOL),
            "SetWindowPos":([wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT],wintypes.BOOL),
        }
        suffix="PtrW" if ctypes.sizeof(ctypes.c_void_p)==8 else "W"
        for name,(args,result) in specs.items():
            function=getattr(self.u,name);function.argtypes=args;function.restype=result
        self.get_long=getattr(self.u,"GetWindowLong"+suffix)
        self.set_long=getattr(self.u,"SetWindowLong"+suffix)
        self.get_long.argtypes=[wintypes.HWND,ctypes.c_int];self.get_long.restype=ctypes.c_ssize_t
        self.set_long.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_ssize_t];self.set_long.restype=ctypes.c_ssize_t

    def valid(self,hwnd):return bool(self.u.IsWindow(hwnd))
    def pid(self,hwnd):
        result=wintypes.DWORD()
        if not self.u.GetWindowThreadProcessId(hwnd,ctypes.byref(result)):raise _Unavailable("window_identity_unavailable")
        return result.value
    def parent(self,hwnd):return int(self.u.GetParent(hwnd) or 0)
    def class_name(self,hwnd):
        result=ctypes.create_unicode_buffer(256)
        return result.value if self.u.GetClassNameW(hwnd,result,256) else ""
    def candidates(self,pid):
        found=[]
        @self.callback
        def visit(hwnd,_):
            try:
                if self.pid(hwnd)==pid and self.class_name(hwnd)=="Chrome_WidgetWin_1" and self.parent(hwnd)==0:
                    found.append(int(hwnd))
            except Exception:pass
            return True
        if not self.u.EnumWindows(visit,0):raise _Unavailable("window_identity_unavailable")
        return found
    def snapshot(self,hwnd):
        rect=wintypes.RECT()
        if not self.u.GetWindowRect(hwnd,ctypes.byref(rect)):raise _Unavailable("window_identity_unavailable")
        return {"parent":self.parent(hwnd),"style":self.get_long(hwnd,-16),"exstyle":self.get_long(hwnd,-20),
                "bounds":(rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top)}
    def styles(self,hwnd,style,exstyle):
        for index,value in ((-16,style),(-20,exstyle)):
            ctypes.set_last_error(0);old=self.set_long(hwnd,index,value)
            if not old and ctypes.get_last_error():raise _Unavailable("window_style_failed")
    def set_parent(self,hwnd,parent):
        ctypes.set_last_error(0);previous=self.u.SetParent(hwnd,parent or None)
        if not previous and ctypes.get_last_error():raise _Unavailable("window_parent_failed")
    def place(self,hwnd,x,y,width,height):
        # NOZORDER | NOACTIVATE | FRAMECHANGED: no foreground/window activation.
        if not self.u.SetWindowPos(hwnd,None,x,y,width,height,0x0004|0x0010|0x0020):
            raise _Unavailable("window_resize_failed")


class BrowserHostBackend:
    def __init__(self,*,windows=None,identity=None,cdp=None,owner_pid=None):
        self.windows=windows;self.identity=identity or _owned_browser;self.cdp=cdp or _cdp
        self.owner_pid=owner_pid or os.getpid();self.record=None
    def _api(self):
        if self.windows is None:self.windows=_Windows()
        return self.windows
    def _live(self):
        if self.record is None:return False
        w=self._api();r=self.record
        return w.valid(r["hwnd"]) and w.pid(r["hwnd"])==r["pid"] and w.class_name(r["hwnd"])=="Chrome_WidgetWin_1"
    def state(self):
        embedded=False
        if self.record is not None:
            try:
                if not self._live():self.record=None
                else:embedded=self._api().parent(self.record["hwnd"])==self.record["host"]
            except Exception:
                # A stale/destroyed handle must not suppress safe standalone
                # behavior. Reads here never perform a window mutation.
                self.record=None
        return {"embedded":embedded,"width":self.record["width"] if embedded else 0,
                "height":self.record["height"] if embedded else 0}
    def _restore(self,hwnd,saved,expected_pid):
        errors=[]
        for operation in (lambda:self._api().set_parent(hwnd,saved["parent"]),
                          lambda:self._api().styles(hwnd,saved["style"],saved["exstyle"]),
                          lambda:self._api().place(hwnd,*saved["bounds"])):
            try:
                # A destroyed HWND can be reused; never roll a foreign window
                # back merely because its numeric handle matches old state.
                w=self._api()
                if not w.valid(hwnd) or w.pid(hwnd)!=expected_pid or w.class_name(hwnd)!="Chrome_WidgetWin_1":
                    raise _Unavailable("window_identity_unavailable")
                operation()
            except Exception:errors.append(True)
        if errors:raise _Unavailable("window_rollback_failed")
    def attach(self,parent_hwnd,width,height,*,top_inset=0):
        _dimensions(width,height);_inset(top_inset)
        if isinstance(parent_hwnd,bool) or not isinstance(parent_hwnd,int) or parent_hwnd<=0:
            raise _Unavailable("host_unavailable")
        w=self._api()
        if not w.valid(parent_hwnd) or w.pid(parent_hwnd)!=self.owner_pid:raise _Unavailable("host_not_owned")
        pid,ws=self.identity()
        if pid==self.owner_pid:raise _Unavailable("browser_identity_unavailable")
        if self.record and not self._live():self.record=None
        if self.record:
            if self.record["pid"]!=pid:raise _Unavailable("dedicated_browser_changed")
            if self.record["host"]!=parent_hwnd or w.parent(self.record["hwnd"])!=parent_hwnd:
                raise _Unavailable("host_changed")
            self.resize(width,height,top_inset=top_inset)
            return self.state()
        candidates=w.candidates(pid)
        if len(candidates)!=1:raise _Unavailable("browser_window_ambiguous" if candidates else "browser_window_unavailable")
        hwnd=candidates[0]
        if not w.valid(hwnd) or w.pid(hwnd)!=pid or w.class_name(hwnd)!="Chrome_WidgetWin_1":
            raise _Unavailable("window_identity_unavailable")
        saved=w.snapshot(hwnd)
        if saved["parent"]!=0:raise _Unavailable("browser_window_ambiguous")
        try:
            style=(saved["style"]&~(WS_POPUP|WS_CAPTION|WS_THICKFRAME|WS_SYSMENU|WS_MINIMIZEBOX|WS_MAXIMIZEBOX))|WS_CHILD
            w.styles(hwnd,style,saved["exstyle"])
            # Chromium's app titlebar can be client-drawn even without
            # WS_CAPTION. The parent clips that measured strip. Page/CDP input
            # remains relative to the unchanged web-content viewport.
            w.set_parent(hwnd,parent_hwnd);w.place(hwnd,0,-top_inset,width,height+top_inset)
            if not w.valid(hwnd) or w.pid(hwnd)!=pid or w.parent(hwnd)!=parent_hwnd:
                raise _Unavailable("window_parent_failed")
        except Exception:
            self._restore(hwnd,saved,pid)
            raise _Unavailable("browser_attach_failed") from None
        self.record={"hwnd":hwnd,"pid":pid,"host":parent_hwnd,"original":saved,"width":width,"height":height,
                     "top_inset":top_inset,"ws":ws}
        return self.state()
    def resize(self,width,height,*,top_inset=None):
        _dimensions(width,height)
        if not self._live():self.record=None;raise _Unavailable("dedicated_browser_unavailable")
        if self._api().parent(self.record["hwnd"])!=self.record["host"]:raise _Unavailable("host_changed")
        inset=self.record.get("top_inset",0) if top_inset is None else _inset(top_inset)
        if (width,height,inset)==(self.record["width"],self.record["height"],self.record.get("top_inset",0)):
            return self.state()
        self._api().place(self.record["hwnd"],0,-inset,width,height+inset)
        self.record.update(width=width,height=height,top_inset=inset)
        return self.state()
    def detach(self):
        if not self._live():self.record=None;return self.state()
        self._restore(self.record["hwnd"],self.record["original"],self.record["pid"])
        self.record=None
        return self.state()
    def close(self,timeout=2):
        if not self._live():self.record=None
        # A cold launch may still be owned even when attachment never succeeded.
        # Closing the app should not leave that dedicated off-screen app alive.
        if self.record is None:
            try:pid,ws=self.identity()
            except _Unavailable as exc:
                if exc.reason=="dedicated_browser_unavailable":return self.state()
                raise
            except requests.ConnectionError:
                # No dedicated debugging endpoint remains. This is also the
                # normal "never opened / already closed" cold-host state.
                return self.state()
            try:self.cdp(ws,"Browser.close")
            except Exception:
                # Browser.close often destroys its own transport before the
                # reply. Verify a fresh loopback identity read instead of
                # replaying that uncertain command or exposing its error.
                deadline=time.monotonic()+max(0,min(3,timeout))
                while True:
                    try:current_pid,_ws=self.identity()
                    except _Unavailable as exc:
                        if exc.reason=="dedicated_browser_unavailable":return self.state()
                    except requests.ConnectionError:
                        return self.state()
                    except Exception:pass
                    else:
                        if current_pid!=pid:raise _Unavailable("dedicated_browser_changed")
                    if time.monotonic()>=deadline:break
                    time.sleep(.025)
                raise _Unavailable("browser_close_unconfirmed") from None
            return self.state()
        pid,ws=self.identity()
        if pid!=self.record["pid"]:raise _Unavailable("dedicated_browser_changed")
        try:self.cdp(ws,"Browser.close")
        except Exception:pass  # A closing browser may drop CDP before replying.
        deadline=time.monotonic()+max(0,min(3,timeout))
        while self._live() and time.monotonic()<deadline:time.sleep(.025)
        if self._live():raise _Unavailable("browser_close_unconfirmed")
        self.record=None
        return self.state()


@dataclass
class _Request:
    generation:int
    expires_at:float
    event:threading.Event=field(default_factory=threading.Event)
    result:dict|None=None
    cancelled:bool=False


class _Bridge:
    def __init__(self):
        self.lock=threading.RLock();self.requests=queue.Queue()
        self.owner=None;self.parent=None;self.width=480;self.height=370;self.top_inset=0
        self.backend=None;self.generation=0;self.reason="host_not_registered"
    def state(self):
        with self.lock:
            state=self.backend.state() if self.backend else {"embedded":False,"width":0,"height":0}
            if not state["embedded"] and self.reason=="embedded":self.reason="dedicated_browser_unavailable"
            return {**state,"registered":self.parent is not None,"configured":self.parent is not None,"reason":self.reason}
    def main(self):
        if self.owner is None or self.owner!=threading.get_ident():raise _Unavailable("gui_thread_required")


_bridge=_Bridge()


def configure(parent_hwnd=None,width=480,height=370,*,backend=None,top_inset=0):
    """Register on Tk's GUI thread. top_inset uses parent-client pixel units.

    The caller measures custom browser chrome at the active DPI; this module
    does not change DPI awareness or assume a fixed titlebar size.
    """
    with _bridge.lock:
        if _bridge.owner is not None and _bridge.owner!=threading.get_ident():
            return {**_bridge.state(),"reason":"gui_thread_required"}
        if parent_hwnd is not None:
            try:
                _dimensions(width,height);_inset(top_inset)
                if isinstance(parent_hwnd,bool) or not isinstance(parent_hwnd,int) or parent_hwnd<=0:raise _Unavailable("host_unavailable")
            except _Unavailable as exc:return {**_bridge.state(),"reason":exc.reason}
        _bridge.owner=threading.get_ident()
        if _bridge.parent!=parent_hwnd or backend is not None and backend is not _bridge.backend:_bridge.generation+=1
        _bridge.parent=parent_hwnd;_bridge.width=width;_bridge.height=height;_bridge.top_inset=top_inset
        if backend is not None:_bridge.backend=backend
        elif _bridge.backend is None:_bridge.backend=BrowserHostBackend()
        _bridge.reason="host_registered" if parent_hwnd is not None else "host_not_registered"
        return _bridge.state()


def get_embedded_state():return _bridge.state()


def _main_action(action,*args,**kwargs):
    with _bridge.lock:
        try:
            _bridge.main()
            result=getattr(_bridge.backend,action)(*args,**kwargs)
            _bridge.reason={"attach":"embedded","resize":"embedded","detach":"detached","close":"browser_closed"}[action]
        except _Unavailable as exc:_bridge.reason=exc.reason
        except Exception:_bridge.reason="browser_host_unavailable"
        return _bridge.state()


def attach_browser(parent_hwnd,width=480,height=370,*,top_inset=None):
    """GUI-thread action; omitted inset uses the registered host's value."""
    inset=_bridge.top_inset if top_inset is None else top_inset
    return _main_action("attach",parent_hwnd,width,height,top_inset=inset)


def resize_browser(width,height,*,top_inset=None):return _main_action("resize",width,height,top_inset=top_inset)
def detach_browser():return _main_action("detach")
def close_owned_browser(timeout=2):return _main_action("close",timeout)


def request_attach(timeout=12):
    """Queue a worker request; Tk polls process_pending on its own thread."""
    try:
        duration=float(timeout)
        if not 0<=duration<=30:raise ValueError
    except (TypeError,ValueError,OverflowError):
        return {**_bridge.state(),"reason":"invalid_host_wait"}
    with _bridge.lock:
        if _bridge.parent is None:return _bridge.state()
        if threading.get_ident()==_bridge.owner:return attach_browser(_bridge.parent,_bridge.width,_bridge.height)
        request=_Request(_bridge.generation,time.monotonic()+duration);_bridge.requests.put(request)
    if request.event.wait(duration):
        return request.result
    with _bridge.lock:request.cancelled=True
    return {**_bridge.state(),"reason":"host_attach_timeout"}


def process_pending(max_requests=4):
    """Drain the bridge from root.after, without any worker Tk operations."""
    try:_bridge.main()
    except _Unavailable:return {**_bridge.state(),"reason":"gui_thread_required"}
    for _ in range(max(1,min(16,int(max_requests)))):
        try:request=_bridge.requests.get_nowait()
        except queue.Empty:break
        with _bridge.lock:
            if request.cancelled or time.monotonic()>=request.expires_at or request.generation!=_bridge.generation or _bridge.parent is None:
                request.result={**_bridge.state(),"reason":"host_request_cancelled"}
            else:request.result=attach_browser(_bridge.parent,_bridge.width,_bridge.height)
            request.event.set()
    return _bridge.state()
