from __future__ import annotations

import json
import math
import time
import sys
import ctypes
from typing import Any, Callable

import requests
import websocket

from .learn_center import LearningBrowser, PORT
from .api import ATClient
from .credentials import load_credentials
from .account_state import find_ad_state
from .settings import load_settings

# Keep the dedicated helper window small and repeatable for browser-scoped canvas playback.
# These are outer-window bounds; the captured canvas clicks remain normalized to
# the canvas itself, so minor title-bar differences do not matter.
AD_WINDOW_LEFT = 24
AD_WINDOW_TOP = 24
AD_WINDOW_WIDTH = 640
AD_WINDOW_HEIGHT = 720
MAX_CALIBRATED_AD_DELAY = 300.0
_geometry_ready = set()


def _targets() -> list[dict[str, Any]]:
    try:
        r = requests.get(f"http://127.0.0.1:{PORT}/json", timeout=1)
        data = r.json() if r.ok else []
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _cdp(ws_url: str, method: str, params: dict[str, Any] | None = None, timeout: float = 4.0):
    ws = websocket.create_connection(ws_url, timeout=2, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 101, "method": method, "params": params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            msg = json.loads(ws.recv())
            if msg.get("id") == 101:
                if msg.get("error"):
                    raise RuntimeError(str(msg.get("error")))
                return msg.get("result") or {}
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return {}


def _eval(ws_url: str, expr: str):
    result = _cdp(ws_url, "Runtime.evaluate", {
        "expression": expr,
        "returnByValue": True,
        "awaitPromise": True,
    })
    return ((result.get("result") or {}).get("value"))


def _page_targets() -> list[dict[str, Any]]:
    rows = [t for t in _targets() if t.get("type") in {"page", "iframe"} and t.get("webSocketDebuggerUrl")]
    rows.sort(key=lambda t: (
        0 if "animation-throwdown" in str(t.get("url") or "").lower() else 1,
        0 if "kongregate.com" in str(t.get("url") or "").lower() else 1,
    ))
    return rows


def _fix_window_geometry(t: dict[str, Any]) -> None:
    """Set standalone bounds once; an embedded host owns its child geometry."""
    from .embedded_browser import get_embedded_state
    if get_embedded_state().get("registered"):
        return
    ws_url = t.get("webSocketDebuggerUrl")
    tid = t.get("id")
    if not ws_url:
        return
    if ws_url in _geometry_ready:
        return
    try:
        info = _cdp(ws_url, "Browser.getWindowForTarget", {"targetId": tid} if tid else {})
        wid = info.get("windowId")
        if wid is not None:
            _cdp(ws_url, "Browser.setWindowBounds", {
                "windowId": wid,
                "bounds": {
                    "left": AD_WINDOW_LEFT,
                    "top": AD_WINDOW_TOP,
                    "width": AD_WINDOW_WIDTH,
                    "height": AD_WINDOW_HEIGHT,
                    "windowState": "normal",
                },
            })
            _geometry_ready.add(ws_url)
            if len(_geometry_ready)>32:
                _geometry_ready.intersection_update({ws_url})
    except Exception:
        pass


_LOCK_SCROLL_JS = r'''
(() => {
  try { window.scrollTo(0,0); } catch(e) {}
  try { document.documentElement.scrollTop=0; document.body.scrollTop=0; } catch(e) {}
  try { document.documentElement.style.overflow='hidden'; document.body.style.overflow='hidden'; } catch(e) {}
  if (!window.__atc_scroll_lock_installed) {
    window.__atc_scroll_lock_installed=true;
    const stop = ev => { try { ev.preventDefault(); } catch(e) {} };
    window.addEventListener('wheel', stop, {capture:true, passive:false});
    window.addEventListener('touchmove', stop, {capture:true, passive:false});
    window.addEventListener('scroll', () => { if (window.scrollX || window.scrollY) window.scrollTo(0,0); }, true);
  }
  return {x:window.scrollX,y:window.scrollY,w:window.innerWidth,h:window.innerHeight};
})()
'''


_PLAY_JS = r'''
(() => {
  const norm = s => (s || '').replace(/\s+/g,' ').trim().toLowerCase();
  const good = t => t==='play now' || t==='play game' || t==='play' || /^play now\b/.test(t) || /^play game\b/.test(t);
  const all=[];
  const walk = root => {
    let els=[]; try { els=[...root.querySelectorAll('*')]; } catch(e) {}
    for (const e of els) {
      all.push(e);
      if (e.shadowRoot) walk(e.shadowRoot);
    }
  };
  walk(document);
  const cands=[];
  for (const e of all) {
    const role=norm(e.getAttribute && e.getAttribute('role'));
    const tag=norm(e.tagName);
    if (!(tag==='button'||tag==='a'||tag==='input'||role==='button'||typeof e.onclick==='function')) continue;
    let t=norm((e.innerText||'')+' '+(e.textContent||'')+' '+(e.getAttribute&&e.getAttribute('aria-label')||'')+' '+(e.getAttribute&&e.getAttribute('title')||'')+' '+(e.value||''));
    if (!good(t)) continue;
    const r=e.getBoundingClientRect(); const s=getComputedStyle(e);
    if (e.disabled || r.width < 20 || r.height < 12 || s.display==='none' || s.visibility==='hidden' || s.pointerEvents==='none') continue;
    const x=r.x+r.width/2,y=r.y+r.height/2,hit=document.elementFromPoint(x,y);
    if(x<0||y<0||x>innerWidth||y>innerHeight||!hit||!(hit===e||e.contains(hit)))continue;
    cands.push({e,t,x,y,area:r.width*r.height});
  }
  cands.sort((a,b)=>a.area-b.area);
  if (!cands.length) return {clicked:false};
  const x=cands[0];
  return {found:true,text:x.t,x:x.x,y:x.y};
})()
'''


_CANVAS_JS = r'''
(() => {
  const rows=[...document.querySelectorAll('canvas')].map(c=>{const r=c.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,area:r.width*r.height};}).filter(r=>r.width>100&&r.height>100);
  rows.sort((a,b)=>b.area-a.area);
  const c=rows[0]||null;
  return {canvas:c,innerWidth:window.innerWidth,innerHeight:window.innerHeight,outerWidth:window.outerWidth,outerHeight:window.outerHeight,screenX:window.screenX,screenY:window.screenY,scrollX:window.scrollX,scrollY:window.scrollY,url:location.href};
})()
'''


_CAPTURE_JS = r'''
(() => {
  window.__atc_ad_clicks=[];
  try { localStorage.removeItem('__atc_ad_clicks'); } catch(e) {}
  if (window.__atc_ad_capture_handler) document.removeEventListener('pointerdown',window.__atc_ad_capture_handler,true);
  window.__atc_ad_capture_handler = ev => {
    const cs=[...document.querySelectorAll('canvas')].map(c=>{const r=c.getBoundingClientRect();return {r,area:r.width*r.height};}).filter(x=>x.r.width>100&&x.r.height>100).sort((a,b)=>b.area-a.area);
    const cr=cs.length?cs[0].r:null;
    let inside=false,cx=null,cy=null;
    if(cr && ev.clientX>=cr.x && ev.clientX<=cr.right && ev.clientY>=cr.y && ev.clientY<=cr.bottom){inside=true;cx=(ev.clientX-cr.x)/cr.width;cy=(ev.clientY-cr.y)/cr.height;}
    window.__atc_ad_clicks.push({ts:Date.now(),vx:ev.clientX/window.innerWidth,vy:ev.clientY/window.innerHeight,sx:ev.screenX,sy:ev.screenY,insideCanvas:inside,cx,cy,tag:(ev.target&&ev.target.tagName)||'',text:((ev.target&&ev.target.innerText)||'').slice(0,80)});
    try { localStorage.setItem('__atc_ad_clicks', JSON.stringify(window.__atc_ad_clicks.slice(-8))); } catch(e) {}
  };
  document.addEventListener('pointerdown',window.__atc_ad_capture_handler,true);
  return {ok:true};
})()
'''

_READ_CAPTURE_JS = r'''(() => { let a=(window.__atc_ad_clicks||[]).slice(); if(!a.length){try{a=JSON.parse(localStorage.getItem('__atc_ad_clicks')||'[]')}catch(e){}} return a; })()'''


def _launcher_target(rows):
    pages=[t for t in rows if t.get('type')=='page']
    for t in pages:
        if 'kongregate.com' in str(t.get('url') or '').lower():
            return t
    return pages[0] if pages else (rows[0] if rows else None)


def _saved_play_click():
    try:
        row=load_settings().get('ad_play_click') or {}
        # 1.7.17 retains compatibility with the old absolute/native-cursor teaching
        # format. Runtime ad automation must never move or reserve the user's
        # Windows mouse. Re-teaching Play Now once records a browser viewport
        # coordinate that is replayed only through Chromium DevTools input.
        if not isinstance(row,dict) or row.get('source')!='browser_pointer':
            return None
        if row.get('x') is not None and row.get('y') is not None:
            return {'x':float(row['x']),'y':float(row['y']),'source':'browser_pointer'}
    except Exception:
        pass
    return None


def _activate_browser_window(target: dict[str, Any] | None = None) -> bool:
    """Bring the dedicated Edge/Kongregate window to the foreground on Windows.

    CDP canvas events were observed reaching the page but not Unity reliably. A
    native click only helps if the Edge window is actually foreground, so find
    the visible Edge/Kongregate window by title and activate it first.
    """
    if sys.platform != 'win32':
        return False
    title_hint=str((target or {}).get('title') or '').strip().lower()
    user32=ctypes.windll.user32
    matches=[]
    EnumProc=ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    @EnumProc
    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n=user32.GetWindowTextLengthW(hwnd)
        if n<=0:
            return True
        buf=ctypes.create_unicode_buffer(n+1); user32.GetWindowTextW(hwnd,buf,n+1)
        title=buf.value.lower()
        score=0
        if title_hint and title_hint in title: score+=100
        if 'animation throwdown' in title: score+=60
        if 'kongregate' in title: score+=40
        if 'microsoft edge' in title: score+=10
        if score: matches.append((score,hwnd,title))
        return True
    try:
        user32.EnumWindows(cb,0)
        if not matches:return False
        hwnd=max(matches,key=lambda x:x[0])[1]
        user32.ShowWindow(hwnd,5)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        time.sleep(.18)
        return True
    except Exception:
        return False


def _native_click_absolute(x: float, y: float, target: dict[str, Any] | None = None) -> bool:
    if sys.platform != 'win32':
        return False
    try:
        _activate_browser_window(target)
        user32=ctypes.windll.user32
        class POINT(ctypes.Structure):
            _fields_=[('x',ctypes.c_long),('y',ctypes.c_long)]
        old=POINT(); user32.GetCursorPos(ctypes.byref(old))
        user32.SetCursorPos(int(round(x)),int(round(y)))
        time.sleep(.06)
        user32.mouse_event(0x0002,0,0,0,0)  # LEFTDOWN
        time.sleep(.06)
        user32.mouse_event(0x0004,0,0,0,0)  # LEFTUP
        time.sleep(.08)
        user32.SetCursorPos(old.x,old.y)
        return True
    except Exception:
        return False


def _client_to_screen(state: dict[str, Any], x: float, y: float) -> tuple[float,float]:
    """Approximate a viewport client point in screen coordinates.

    Fresh calibration stores exact screenX/screenY, so this is mainly a backwards
    compatible fallback for old saved sequences.
    """
    sx=float(state.get('screenX') or AD_WINDOW_LEFT); sy=float(state.get('screenY') or AD_WINDOW_TOP)
    iw=float(state.get('innerWidth') or 1); ih=float(state.get('innerHeight') or 1)
    ow=float(state.get('outerWidth') or iw); oh=float(state.get('outerHeight') or ih)
    border=max(0.0,(ow-iw)/2.0); top=max(0.0,oh-ih-border)
    return sx+border+x, sy+top+y


def _ax_click_play(t: dict[str, Any]) -> bool:
    """Accessibility-tree fallback for Kongregate's Play Now button."""
    ws_url = t.get("webSocketDebuggerUrl")
    if not ws_url:
        return False
    try:
        tree = _cdp(ws_url, "Accessibility.getFullAXTree", {})
        nodes = tree.get("nodes") or []
        for n in nodes:
            role = str(((n.get("role") or {}).get("value") or "")).lower()
            name = str(((n.get("name") or {}).get("value") or "")).strip().lower()
            if role not in {"button", "link"}:
                continue
            if not (name == "play now" or name == "play game" or name == "play" or name.startswith("play now ") or name.startswith("play game ")):
                continue
            backend = n.get("backendDOMNodeId")
            if backend is None:
                continue
            obj = _cdp(ws_url, "DOM.resolveNode", {"backendNodeId": backend})
            oid = ((obj.get("object") or {}).get("objectId"))
            if not oid:
                continue
            _cdp(ws_url, "Runtime.callFunctionOn", {
                "objectId": oid,
                "functionDeclaration": "function(){ try{ this.focus({preventScroll:true}); }catch(e){}; this.click(); return true; }",
                "returnByValue": True,
            })
            return True
    except Exception:
        return False
    return False


def _stabilize_targets() -> list[dict[str, Any]]:
    rows = _page_targets()
    for t in rows:
        _fix_window_geometry(t)
        try:
            _eval(t["webSocketDebuggerUrl"], _LOCK_SCROLL_JS)
        except Exception:
            pass
    return rows


def _click_play_control(target,point):
    """Deliver real browser input to the semantically observed launcher."""
    ws=target["webSocketDebuggerUrl"]
    from .ad_browser_focus import ensure_game_input_focus, release_game_input_focus
    from .embedded_browser import get_embedded_state
    prep={"target":target,"embedded":get_embedded_state().get("embedded") is True}
    try:
        ensure_game_input_focus(prep,_cdp)
        _cdp(ws,"Input.dispatchMouseEvent",{"type":"mouseMoved","x":point["x"],"y":point["y"]})
        _dispatch_click(ws,float(point["x"]),float(point["y"]))
    finally:
        release_game_input_focus(prep)


def prepare_game_ui(log: Callable[[str], None] | None = None, wait_for_canvas: int = 20, *, should_stop=None) -> dict[str, Any]:
    """Prepare the real mini game window without ever spraying coordinate clicks.

    1.7.17 deliberately stops treating Play Now as a calibrated coordinate. We
    first reuse an already-loaded canvas, then try the actual DOM/accessibility
    Play button once after it appears. If Kongregate still refuses to expose/load
    the game, fail cleanly. Recurring ad playback remains
    browser-scoped and never touches the Windows cursor.
    """
    emit = log or (lambda _m: None)
    stopped=should_stop or (lambda:False)
    if stopped():return {"ok":False,"playback_started":False,"message":"Game preparation stopped."}
    b = LearningBrowser(); ok, msg = b.ensure_open(); emit(msg)
    if not ok:
        return {"ok": False, "message": msg}
    from .embedded_browser import get_embedded_state, request_attach
    embedded=False
    if get_embedded_state().get("registered"):
        embedded=request_attach().get("embedded") is True
        if not embedded:
            return {"ok":False,"message":"The game could not attach to Commander's panel. No ad was started."}
    time.sleep(.45)

    def find_canvas():
        rows=_stabilize_targets()
        for t in rows:
            try:
                state=_eval(t["webSocketDebuggerUrl"],_CANVAS_JS)
                c=(state or {}).get("canvas") if isinstance(state,dict) else None
                if isinstance(c,dict) and float(c.get("width") or 0)>100 and float(c.get("height") or 0)>100:
                    return {"ok":True,"target":t,"canvas":c,"play_clicked":False,"embedded":embedded}
            except Exception:pass
        return None

    ready=find_canvas()
    if ready:return ready

    clicked=False;visible_since=None
    deadline=time.time()+max(4,min(90,int(wait_for_canvas)))
    while time.time()<deadline:
        if stopped():return {"ok":False,"playback_started":False,"message":"Game preparation stopped."}
        ready=find_canvas()
        if ready:
            ready["play_clicked"]=clicked
            emit("Ad UI: game canvas ready in Commander." if embedded else f"Ad UI: mini game canvas ready ({AD_WINDOW_WIDTH}x{AD_WINDOW_HEIGHT}).")
            return ready
        # The launcher is populated asynchronously after a cold browser start.
        # Poll for the semantic control, then stop looking after one click.
        # Never use saved coordinates or the Windows mouse for this entry.
        if not clicked:
            from urllib.parse import urlparse
            rows=[t for t in _stabilize_targets() if t.get("type")=="page"
                  and (urlparse(str(t.get("url")or"")).hostname or "").lower() in {"kongregate.com","www.kongregate.com"}
                  and "animation-throwdown" in urlparse(str(t.get("url")or"")).path.lower()]
            for t in rows:
                try:
                    res=_eval(t["webSocketDebuggerUrl"],_PLAY_JS)
                except Exception:pass
                else:
                    if isinstance(res,dict) and res.get("found"):
                        # Let the freshly rendered React launcher settle before
                        # one trusted pointer click; synthetic .click() can be
                        # ignored before hydration or without user activation.
                        identity=(t.get("id"),res.get("text"),res.get("x"),res.get("y"))
                        if visible_since is None or visible_since[0]!=identity:
                            visible_since=(identity,time.time());break
                        if time.time()-visible_since[1]<2:break
                        if stopped():return {"ok":False,"playback_started":False,"message":"Game preparation stopped before Play Now."}
                        try:_click_play_control(t,res)
                        except Exception:
                            return {"ok":False,"message":"Play Now input could not be confirmed. No launcher click or ad was repeated."}
                        emit("Ad UI: clicked the real Play Now control with browser input.")
                        clicked=True;break
                    visible_since=None
        time.sleep(.75)
    return {"ok":False,
            "message":"The real AT game did not finish loading within the wait. Check the game window for a login or service error, then retry. No ad was started."}


def begin_play_click_calibration(log: Callable[[str], None] | None = None, *, should_stop=None) -> dict[str, Any]:
    emit=log or (lambda _m:None)
    stopped=should_stop or (lambda:False)
    if stopped():return {'ok':False,'message':'Play Now teaching stopped.'}
    b=LearningBrowser(); ok,msg=b.ensure_open(); emit(msg)
    if not ok:return {'ok':False,'message':msg}
    if stopped():return {'ok':False,'message':'Play Now teaching stopped.'}
    time.sleep(.5)
    if stopped():return {'ok':False,'message':'Play Now teaching stopped.'}
    rows=_stabilize_targets(); installed=0
    for t in rows:
        if stopped():return {'ok':False,'message':'Play Now teaching stopped.'}
        try:
            r=_eval(t['webSocketDebuggerUrl'], _CAPTURE_JS)
            if isinstance(r,dict) and r.get('ok'):installed+=1
        except Exception:pass
    if not installed:return {'ok':False,'message':'Could not arm the Play Now click recorder.'}
    emit('Play Now teaching armed. In the fixed window click Play Now exactly once, then return to Commander.')
    return {'ok':True}


def finish_play_click_calibration() -> dict[str, Any]:
    raw=[]
    for t in _stabilize_targets():
        try:
            vals=_eval(t['webSocketDebuggerUrl'], _READ_CAPTURE_JS)
            if isinstance(vals,list):raw.extend([x for x in vals if isinstance(x,dict) and x.get('ts') is not None])
        except Exception:pass
    raw.sort(key=lambda r:int(r.get('ts') or 0))
    if not raw:return {'ok':False,'message':'No click was captured. Click Play Now once before finishing.'}
    row=raw[0]
    click={'x':float(row.get('vx') or 0),'y':float(row.get('vy') or 0),'source':'browser_pointer'}
    if row.get('sx') is not None and row.get('sy') is not None:
        click.update({'sx':float(row.get('sx')),'sy':float(row.get('sy'))})
    return {'ok':True,'click':click}


def test_calibrated_ad_ui(log: Callable[[str], None] | None = None, *, should_stop=None) -> dict[str, Any]:
    emit=log or (lambda _m:None)
    stopped=should_stop or (lambda:False)
    if stopped():return {'ok':False,'message':'Ad click testing stopped.'}
    seq=load_settings().get('ad_click_sequence') or []
    if not isinstance(seq,list) or len(seq)<2:
        return {'ok':False,'message':'No calibrated ad click sequence is saved yet.'}
    ok=_replay_sequence(seq,emit,should_stop=should_stop)
    if stopped():return {'ok':False,'message':'Ad click testing stopped. The sequence was not repeated.'}
    level=None
    try:
        level=int(find_ad_state(ATClient(load_credentials()).get_boost_level()).get('boost_level') or 0)
    except Exception:pass
    return {'ok':bool(ok),'boost_level':level,'message':None if ok else 'Could not replay the calibrated clicks.'}


def begin_ad_click_calibration(log: Callable[[str], None] | None = None, *, should_stop=None) -> dict[str, Any]:
    emit = log or (lambda _m: None)
    stopped=should_stop or (lambda:False)
    if stopped():return {"ok":False,"message":"Ad calibration stopped."}
    prep = prepare_game_ui(emit, wait_for_canvas=30, should_stop=should_stop)
    if not prep.get("ok"):
        return prep
    if stopped():return {"ok":False,"message":"Ad calibration stopped."}
    installed = 0
    for t in _stabilize_targets():
        if stopped():return {"ok":False,"message":"Ad calibration stopped."}
        try:
            r = _eval(t["webSocketDebuggerUrl"], _CAPTURE_JS)
            if isinstance(r, dict) and r.get("ok"):
                installed += 1
        except Exception:
            pass
    if not installed:
        return {"ok": False, "message": "Could not install the one-time ad click recorder in the fixed game window."}
    emit("Ad calibration armed in the fixed/no-scroll window. Perform exactly one normal ad: ad box → Watch Ad → close X, then return to Commander.")
    return {"ok": True}


def finish_ad_click_calibration() -> dict[str, Any]:
    raw: list[dict[str, Any]] = []
    for t in _stabilize_targets():
        try:
            vals = _eval(t["webSocketDebuggerUrl"], _READ_CAPTURE_JS)
            if isinstance(vals, list):
                for row in vals:
                    if isinstance(row, dict) and row.get("ts") is not None:
                        raw.append(row)
        except Exception:
            pass
    raw.sort(key=lambda r: int(r.get("ts") or 0))
    if len(raw) > 4:
        raw = raw[-4:]
    if len(raw) < 2:
        return {"ok": False, "message": f"Only {len(raw)} click(s) were captured. Perform one complete ad flow before finishing calibration."}
    seq = []
    prev = None
    for row in raw:
        ts = int(row.get("ts") or 0)
        delay = .6 if prev is None else max(.25, (ts-prev)/1000.0)
        if delay > MAX_CALIBRATED_AD_DELAY:
            return {"ok": False, "message": "The captured ad flow has a gap longer than five minutes. Calibrate one normal ad without unrelated pauses."}
        prev = ts
        step={
            "delay": round(delay, 2),
            "inside_canvas": bool(row.get("insideCanvas")),
            "x": float(row.get("cx") if row.get("insideCanvas") and row.get("cx") is not None else row.get("vx") or 0),
            "y": float(row.get("cy") if row.get("insideCanvas") and row.get("cy") is not None else row.get("vy") or 0),
        }
        if row.get('sx') is not None and row.get('sy') is not None:
            step.update({'sx':float(row.get('sx')),'sy':float(row.get('sy'))})
        seq.append(step)
    return {
        "ok": True,
        "sequence": seq,
        "clicks": len(seq),
        "window": {"width": AD_WINDOW_WIDTH, "height": AD_WINDOW_HEIGHT, "left": AD_WINDOW_LEFT, "top": AD_WINDOW_TOP},
    }


def rewarded_benefit_changed(before,after,kind):
    """Accept only server-confirmed ad benefits; never manufacture completion."""
    if not isinstance(after,dict) or after.get("result") is False:return False
    if kind=="mom":
        old=before.get("slot_machine_data") or {};new=after.get("slot_machine_data") or {}
        return isinstance(new.get("pulls"),(int,float)) and isinstance(old.get("pulls"),(int,float)) and new["pulls"]>old["pulls"]
    if kind=="research":
        old=before.get("research_ad_speed_ups");new=after.get("research_ad_speed_ups")
        if isinstance(old,(int,float)) and isinstance(new,(int,float)) and new>old:return True
        def slots(value):
            values=value.values() if isinstance(value,dict) else value if isinstance(value,list) else []
            return {str(row.get("slot_index")):row for row in values if isinstance(row,dict)}
        previous_slots=slots(old)
        for slot,row in slots(new).items():
            previous=previous_slots.get(slot)
            if previous and int(row.get("views")or 0)>int(previous.get("views")or 0):return True
        previous={str(r.get("task_index")):r for r in (before.get("user_tasks")or{}).values()} if isinstance(before.get("user_tasks"),dict) else {}
        current=(after.get("user_tasks")or{}).values() if isinstance(after.get("user_tasks"),dict) else after.get("user_tasks")or[]
        for task in current:
            old=previous.get(str(task.get("task_index"))) if isinstance(task,dict) else None
            if old and int(task.get("task_type")or 0)==1 and int(task.get("end_time")or 0)>0 and int(task["end_time"])<int(old.get("end_time")or 0):return True
    return False


def attempt_legitimate_rewarded_flow(client,kind,log=None,wait_seconds=45):
    """Replay a separately calibrated normal Mom/research ad, then verify credit.

    This invokes no reward-completion endpoint. It requires real game playback.
    Each ad kind has its own recorded sequence to avoid replaying boost clicks
    in a different screen.
    """
    if kind not in {"mom","research"}:raise ValueError("Unknown rewarded ad kind.")
    settings=load_settings();sequence=(settings.get("rewarded_ad_sequences")or{}).get(kind)
    if not isinstance(sequence,list) or len(sequence)<2:
        return {"ok":False,"message":f"Calibrate one real {kind} ad in Tools / Ads before running this command."}
    before=client.get_user_account() if kind=="research" else client.init()
    if not isinstance(before,dict) or before.get("result") is False:return {"ok":False,"message":"The pre-ad account state could not be verified."}
    if not _replay_sequence(sequence,log or (lambda _text:None)):return {"ok":False,"message":"The legitimate ad sequence could not be played."}
    until=time.monotonic()+max(5,min(60,int(wait_seconds)))
    while time.monotonic()<until:
        time.sleep(4.1 if kind=="research" else 15)
        after=client.get_user_account() if kind=="research" else client.init()
        if rewarded_benefit_changed(before,after,kind):return {"ok":True,"kind":kind}
    return {"ok":False,"message":"AT did not verify the rewarded benefit. No ad completion request was fabricated or retried."}


def _dispatch_click(ws_url: str, x: float, y: float):
    for typ in ("mousePressed", "mouseReleased"):
        _cdp(ws_url, "Input.dispatchMouseEvent", {
            "type": typ, "x": float(x), "y": float(y), "button": "left", "clickCount": 1,
        })
        time.sleep(.06)


def _replay_sequence(sequence: list[dict[str, Any]], log: Callable[[str], None], *, should_stop=None) -> bool:
    stopped=should_stop or (lambda:False)
    if stopped():return False
    delays = []
    for step in sequence:
        try:
            delay = float(step.get("delay") or .6)
        except (TypeError, ValueError, AttributeError):
            log("Ad UI: the saved click timing is invalid. Calibrate one normal ad again.")
            return False
        if not math.isfinite(delay) or delay < 0 or delay > MAX_CALIBRATED_AD_DELAY:
            log("Ad UI: the saved click timing is outside the normal ad-flow range. Calibrate one normal ad again.")
            return False
        delays.append(max(.25, delay))
    prep = prepare_game_ui(log, wait_for_canvas=25, should_stop=should_stop)
    if not prep.get("ok"):
        log(str(prep.get("message") or "Game UI unavailable.")); return False
    target = prep.get("target") or {}
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return False
    from .ad_browser_focus import ensure_game_input_focus, GameInputStopped
    if stopped():return False
    if should_stop is not None:prep["_commander_should_stop"]=should_stop
    try:ensure_game_input_focus(prep,_cdp)
    except GameInputStopped:return False
    for i, step in enumerate(sequence):
        if stopped():return False
        try:
            _eval(ws_url, _LOCK_SCROLL_JS)
        except Exception:
            pass
        # Preserve the full taught ad timer while allowing Stop to interrupt
        # long waits. The legacy callers without Stop keep their original wait.
        delay=delays[i] if i else .7
        if should_stop is None:time.sleep(delay)
        else:
            while delay>0:
                if stopped():return False
                pause=min(.25,delay);time.sleep(pause);delay-=pause
        if stopped():return False
        try:
            state = _eval(ws_url, _CANVAS_JS) or {}
            iw = float(state.get("innerWidth") or 1); ih = float(state.get("innerHeight") or 1)
            if step.get("inside_canvas") and isinstance(state.get("canvas"), dict):
                c = state["canvas"]
                x = float(c.get("x") or 0) + float(c.get("width") or 0) * float(step.get("x") or 0)
                y = float(c.get("y") or 0) + float(c.get("height") or 0) * float(step.get("y") or 0)
            else:
                x = iw * float(step.get("x") or 0); y = ih * float(step.get("y") or 0)
            # 1.7.14 never touches the Windows cursor during ad automation.
            # All playback is scoped to Commander's small dedicated game window.
            if stopped():return False
            _dispatch_click(ws_url, x, y)
        except Exception:
            log(f"Ad UI playback click {i+1} could not be confirmed. The sequence was not repeated.")
            return False
    # A bad ad creative can still try to open an external browser tab.  Keep the
    # helper self-contained by closing extra pages in Commander's dedicated Edge
    # profile after the calibrated sequence finishes. This never touches the
    # user's normal Edge/Chrome windows.
    if stopped():return False
    try:
        LearningBrowser()._close_extra_tabs()
    except Exception:
        pass
    return True


def _verified_boost_level(client):
    from .shop_rewards import api_rejected
    response=client.get_boost_level()
    if not isinstance(response,dict) or not response or api_rejected(response):
        raise RuntimeError("AT did not confirm the boost status read.")
    level=find_ad_state(response).get("boost_level")
    if level is None or not math.isfinite(float(level)) or float(level)<0 or int(level)!=float(level):
        raise RuntimeError("AT did not expose a valid boost level.")
    return int(level)


def prepare_ad_game_ui(client=None, log=None, should_stop=None, wait_for_canvas=60):
    """Prepare the browser and settle verified startup rewards before ad input."""
    stopped=should_stop or (lambda:False)
    if stopped():return {"ok":False,"playback_started":False,"message":"Ad preparation stopped."}
    prep=prepare_game_ui(log,wait_for_canvas=wait_for_canvas,should_stop=stopped)
    if not prep.get("ok"):return prep
    try:
        from .utility_commands import build_service
        from .startup_popups import settle_startup_popups
        service=build_service(client or ATClient(load_credentials()))
        result=settle_startup_popups(service,prep,should_stop=stopped,log=log)
        if not isinstance(result,dict) or result.get("ok") is not True or result.get("verified") is not True:
            raise RuntimeError("Startup settlement is unverified.")
    except Exception:
        from .ad_browser_focus import release_game_input_focus
        release_game_input_focus(prep)
        return {"ok":False,"playback_started":False,
                "message":"A startup reward display could not be verified. No ad was started."}
    if stopped():return {"ok":False,"playback_started":False,"message":"Ad preparation stopped."}
    return prep


def _automatic_boost_playback(log,client=None,should_stop=None):
    from .automatic_ads import play_boost_ad
    return play_boost_ad(log,client=client,should_stop=should_stop)


def attempt_legitimate_ad_ui(log: Callable[[str], None] | None = None, wait_seconds: int = 45, target_level: int = 3, *, client=None, should_stop=None) -> dict[str, Any]:
    """Find real ad controls automatically, play genuine ads and verify credit."""
    emit = log or (lambda _m: None)
    client = client or ATClient(load_credentials())
    if should_stop and should_stop():return {"ok":False,"cancelled":True,"attempts":0,"message":"Ad playback was stopped before another request."}
    try:
        level = _verified_boost_level(client)
    except Exception:
        return {"ok":False,"boost_level":None,"attempts":0,"message":"AT's current boost status could not be verified. No ad playback was started; retry after a quiet interval."}
    last_read=time.monotonic()
    if level >= target_level:
        emit(f"Ad UI: boost level {level} is already maxed/at target.")
        return {"ok": True, "boost_level": level, "attempts": 0}

    attempts = 0
    # The real client presents an available login reward before its home screen.
    # Claim only explicit calendar availability before opening a cold game, so
    # the ad flow never needs a user to dismiss/teach that modal.
    delay=max(0,8-(time.monotonic()-last_read))
    if delay:time.sleep(delay)
    try:
        from .login_bonus import prepare_login_for_ads
        login=prepare_login_for_ads(client,should_stop)
        if login.data.get("claimed"):emit("Ad UI: verified the available free login rewards before game startup.")
    except Exception:
        return {"ok":False,"boost_level":level,"attempts":0,"message":"Current login-reward eligibility could not be verified before game startup. No login claim or ad was replayed."}
    if should_stop and should_stop():return {"ok":False,"cancelled":True,"boost_level":level,"attempts":0,"message":"Ad playback stopped before game startup."}
    deadline = time.time() + max(360, int(wait_seconds) * 6)
    while level < target_level and attempts < 3 and time.time() < deadline:
        if should_stop and should_stop():return {"ok":False,"cancelled":True,"boost_level":level,"attempts":attempts,"message":"Ad playback stopped before another ad."}
        attempts += 1
        emit(f"Ad UI: automatically finding and playing real ad {attempts}/3…")
        playback=_automatic_boost_playback(emit,client=client,should_stop=should_stop)
        if not playback.get("ok") and not playback.get("playback_started"):
            return {"ok":False,"boost_level":level,"attempts":attempts,
                    "message":playback.get("message") or "The real ad playback could not be completed."}
        check_end = time.monotonic() + max(8, int(wait_seconds))
        new_level = level; valid_reads=0
        while time.monotonic() < check_end:
            delay=max(0,8-(time.monotonic()-last_read))
            if time.monotonic()+delay>check_end:break
            if delay:time.sleep(delay)
            last_read=time.monotonic()
            try:
                new_level = _verified_boost_level(client);valid_reads+=1
                if new_level > level:
                    break
            except Exception:
                pass
        if new_level <= level:
            confirmed_level=new_level if valid_reads else level
            message=(f"AT still reports boost level {confirmed_level}; no additional credit was confirmed within the wait. Check that a real ad was available and finished."
                     if valid_reads else f"Ad playback ran, but AT's status reads were unavailable. Last verified boost level: {level}. No extra ad sequence was replayed.")
            return {"ok":False,"boost_level":confirmed_level,"attempts":attempts,"message":message}
        level = new_level
        emit(f"Ad UI: AT verified boost level {level}.")
        if not playback.get("ok") and playback.get("closed_completed_ad"):
            if level>=target_level:return {"ok":True,"boost_level":level,"attempts":attempts}
            from .automatic_ads import reset_ad_game_view
            if reset_ad_game_view(emit):continue
        if not playback.get("ok"):
            return {"ok":level>=target_level,"boost_level":level,"attempts":attempts,
                    "message":playback.get("message") or "Credit was verified, but the browser state is uncertain. No further ad was started."}
    return {"ok":level>=target_level,"boost_level":level,"attempts":attempts,
            "message":None if level>=target_level else f"Verified boost level {level}; target level {target_level} was not reached within this run."}


def capture_play_click_from_cursor(log: Callable[[str], None] | None = None, countdown: int = 4) -> dict[str, Any]:
    """Deprecated compatibility helper. 1.7.17 does not use this path for automated ads.

    Automated ads and Play Now teaching use browser-scoped pointer capture/input
    so AT Commander never moves, reserves, or clicks the Windows cursor.
    """
    emit=log or (lambda _m:None)
    b=LearningBrowser(); ok,msg=b.ensure_open(); emit(msg)
    if not ok:return {'ok':False,'message':msg}
    rows=_stabilize_targets(); lt=_launcher_target(rows)
    if not lt:return {'ok':False,'message':'Could not find the fixed Kongregate/AT launcher window.'}
    _activate_browser_window(lt)
    for n in range(max(1,int(countdown)),0,-1):
        emit(f'PLAY NOW TEACH: hover the mouse over Play Now — capturing in {n}…')
        time.sleep(1)
    if sys.platform!='win32':return {'ok':False,'message':'Cursor-based Play Now teaching currently requires Windows.'}
    try:
        user32=ctypes.windll.user32
        class POINT(ctypes.Structure): _fields_=[('x',ctypes.c_long),('y',ctypes.c_long)]
        pt=POINT(); user32.GetCursorPos(ctypes.byref(pt))
        click={'sx':float(pt.x),'sy':float(pt.y)}
        try:
            state=_eval(lt['webSocketDebuggerUrl'],_CANVAS_JS) or {}
            iw=float(state.get('innerWidth') or 1); ih=float(state.get('innerHeight') or 1)
            # Best-effort normalized fallback. Native absolute coordinates are the
            # primary path and are enough because the window geometry is locked.
            cx=float(pt.x)-float(state.get('screenX') or AD_WINDOW_LEFT)
            cy=float(pt.y)-float(state.get('screenY') or AD_WINDOW_TOP)
            click['x']=max(0.0,min(1.0,cx/max(1.0,iw)));click['y']=max(0.0,min(1.0,cy/max(1.0,ih)))
        except Exception:
            click.update({'x':0.5,'y':0.5})
        return {'ok':True,'click':click}
    except Exception as exc:
        return {'ok':False,'message':f'Could not read the Windows cursor position: {exc}'}
