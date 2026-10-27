"""Recognize and operate the real game ad controls, without user calibration.

All input belongs to Commander's dedicated browser. This module neither calls
an ad-completion API nor clicks ad destinations. Credit is checked by ad_ui.
"""
from __future__ import annotations

import json
import time

from .ad_visual_templates import TEMPLATES
from .ad_browser_focus import GameInputStopped

DEFAULT_AREAS={"boost_title":[.25,.95,.08,.35],"watch_button":[.50,.92,.75,1],
               "boost_entry":[.65,.90,0,.25],"close":[.55,1,0,.32]}


# Match public control artwork after normalizing the game surface. Search only
# their expected regions; a green purchase button alone never qualifies.
VISION_JS = r"""
(async () => {
 const shot = __SHOT__, templates = __TEMPLATES__;
 const load = src => new Promise((resolve,reject) => {
   const im = new Image(); im.onload=()=>resolve(im); im.onerror=reject; im.src=src;
 });
 const im = await load('data:image/png;base64,'+shot);
 const surface=document.createElement('canvas');surface.width=557;surface.height=313;
 const ctx=surface.getContext('2d',{willReadFrequently:true});
 ctx.drawImage(im,0,0,557,313);
 const pixels=ctx.getImageData(0,0,557,313).data;
 const areas=__AREAS__;
 const matches={};
 for(const [name,t] of Object.entries(templates)) {
   const ti=await load('data:image/png;base64,'+t.image);
   const tc=document.createElement('canvas');tc.width=t.width;tc.height=t.height;
   const tx=tc.getContext('2d',{willReadFrequently:true});tx.drawImage(ti,0,0);
   const tp=tx.getImageData(0,0,t.width,t.height).data;
   const a=areas[name];let best={error:999};
   for(let y=Math.floor(a[2]*313);y<=Math.min(313-t.height,a[3]*313);y+=2) {
     for(let x=Math.floor(a[0]*557);x<=Math.min(557-t.width,a[1]*557);x+=2) {
       let sum=0,n=0;
       for(let j=2;j<t.height-2;j+=3)for(let i=2;i<t.width-2;i+=3){
         const mask=t.exclude_rectangle;
         if(mask && i>=mask[0] && i<=mask[2] && j>=mask[1] && j<=mask[3])continue;
         const p=((y+j)*557+x+i)*4,q=(j*t.width+i)*4;
         if(name==='close' && !(tp[q]>140 && (tp[q]>tp[q+1]*1.45 || Math.min(tp[q+1],tp[q+2])>180)))continue;
         sum+=Math.abs(pixels[p]-tp[q])+Math.abs(pixels[p+1]-tp[q+1])+Math.abs(pixels[p+2]-tp[q+2]);n+=3;
       }
       const error=sum/Math.max(1,n);
       if(error<best.error)best={error,x:(x+t.width/2)/557,y:(y+t.height/2)/313};
     }
   }
   // The label and title both need a tight match. Close uses artwork rather
   // than arbitrary ad creative text, and is considered only after playback.
   if(best.error<(name==='close'?37:30))matches[name]=best;
 }
 return {matches,width:im.width,height:im.height};
})()
"""


def classify_pixels(shot, evaluate, *, templates=None, areas=None):
    expression=VISION_JS.replace("__SHOT__",json.dumps(shot)).replace("__TEMPLATES__",json.dumps(TEMPLATES if templates is None else templates)).replace("__AREAS__",json.dumps(DEFAULT_AREAS if areas is None else areas))
    result=evaluate(expression)
    return result.get("matches",{}) if isinstance(result,dict) else {}


def _view(prep, classifier=None):
    """Capture the game's surface, including a cross-origin Unity iframe."""
    from . import ad_ui as ui
    target=prep["target"];ws=target["webSocketDebuggerUrl"]
    state=ui._eval(ws,ui._CANVAS_JS) or {}
    canvas=state.get("canvas")
    if not isinstance(canvas,dict):raise RuntimeError("The game canvas is unavailable.")
    capture_target=target
    clip={k:float(canvas.get(k) or 0) for k in ("x","y","width","height")}
    if target.get("type")=="iframe":
        # Chromium screenshots use the top-level viewport. Resolve the real
        # game iframe's visible rectangle in the parent page, not screen pixels.
        resolved=False
        for parent in ui._page_targets():
            if parent.get("type")!="page" or "kongregate.com" not in str(parent.get("url") or ""):continue
            try:
                owner=ui._cdp(parent["webSocketDebuggerUrl"],"DOM.getFrameOwner",{"frameId":target["id"]})
                box=ui._cdp(parent["webSocketDebuggerUrl"],"DOM.getBoxModel",{"backendNodeId":owner["backendNodeId"]})["model"]
                quad=box["content"]
                clip["x"]+=float(quad[0]);clip["y"]+=float(quad[1])
                capture_target=parent;resolved=True
                break
            except Exception:
                pass
            frames=ui._eval(parent["webSocketDebuggerUrl"],r"""(() => [...document.querySelectorAll('iframe')].map(f=>{
             const r=f.getBoundingClientRect();return {src:f.src,x:r.x,y:r.y,width:r.width,height:r.height};
            }).filter(r=>r.width>100&&r.height>100))()""") or []
            from urllib.parse import urlparse
            host=urlparse(str(target.get("url") or "")).hostname
            matching=[f for f in frames if urlparse(str(f.get("src") or "")).hostname==host]
            if len(matching)==1:
                frame=matching[0];clip["x"]+=float(frame["x"]);clip["y"]+=float(frame["y"])
                capture_target=parent
                resolved=True
                break
        if not resolved:raise RuntimeError("The visible game frame could not be identified uniquely.")
    clip["scale"]=1
    shot=ui._cdp(capture_target["webSocketDebuggerUrl"],"Page.captureScreenshot",{
        "format":"png","captureBeyondViewport":False,"clip":clip
    }).get("data")
    if not shot:raise RuntimeError("The game surface could not be read.")
    prep["input_target"]=capture_target
    prep["input_canvas"]=clip
    matches=(classifier or classify_pixels)(shot,lambda expression:ui._eval(ws,expression))
    return matches,canvas


def _click(prep,canvas,point):
    from . import ad_ui as ui
    target=prep.get("input_target") or prep["target"]
    canvas=prep.get("input_canvas") or canvas
    # Unity updates its pointer position on MouseMove, and ignores input while
    # its game frame is unfocused. Neither operation moves the Windows cursor.
    from .ad_browser_focus import ensure_game_input_focus,check_game_input_stop
    ensure_game_input_focus(prep, ui._cdp)
    x=float(canvas["x"])+float(canvas["width"])*float(point["x"])
    y=float(canvas["y"])+float(canvas["height"])*float(point["y"])
    ui._cdp(target["webSocketDebuggerUrl"],"Input.dispatchMouseEvent",{"type":"mouseMoved","x":x,"y":y})
    check_game_input_stop(prep)
    ui._dispatch_click(target["webSocketDebuggerUrl"],
        x,y)


def play_boost_ad(log=None,timeout=100,*,client=None,should_stop=None):
    """One real playback, with explicit state checks and no blind replay."""
    from . import ad_ui as ui
    emit=log or (lambda _message:None)
    stopped=should_stop or (lambda:False)
    started=None
    def stopped_result():
        return {"ok":False,"cancelled":True,"playback_started":started is not None,
                "message":"Ad playback stopped; pending credit must be checked before another ad."}
    if stopped():return stopped_result()
    try:prep=ui.prepare_ad_game_ui(client=client,log=emit,should_stop=stopped,wait_for_canvas=60)
    except Exception:return {"ok":False,"playback_started":False,"message":"The game browser could not be prepared. No ad was started."}
    if not prep.get("ok"):return {"ok":False,"message":prep.get("message")}
    if stopped():return stopped_result()
    prep["_commander_should_stop"]=stopped
    deadline=time.monotonic()+max(20,min(150,float(timeout)))
    phase="open";started=None;closed=False;closed_at=None;entry_clicked=False;dismissals=0;input_cancelled=False
    def deliver(canvas,point):
        nonlocal input_cancelled
        if stopped():
            input_cancelled=True
            return False
        try:
            _click(prep,canvas,point)
            return True
        except GameInputStopped:
            input_cancelled=True
            return False
        except Exception:
            emit("Ad UI: browser input could not be confirmed; stopping without repeating it.")
            return False
    while time.monotonic()<deadline:
        if stopped():return stopped_result()
        try:matches,canvas=_view(prep)
        except Exception as exc:
            from .diagnostic_privacy import safe_text
            emit("Ad UI: control detection failed ("+type(exc).__name__+"): "+safe_text(str(exc)))
            return {"ok":False,"playback_started":started is not None,"message":"The game ad controls could not be read. No completion or extra ad was sent."}
        if stopped():return stopped_result()
        panel="boost_title" in matches
        watch="watch_button" in matches
        if phase=="open":
            if panel and watch:
                started=time.monotonic();phase="playing"
                if not deliver(canvas,matches["watch_button"]):
                    if input_cancelled:
                        started=None
                        return stopped_result()
                    return {"ok":False,"playback_started":True,"message":"Watch input has an uncertain outcome. Checking credit without starting another ad."}
                emit("Ad UI: selected the real Watch button; waiting for the ad's own close control.")
            elif not entry_clicked and "boost_entry" in matches:
                entry_clicked=True
                if not deliver(canvas,matches["boost_entry"]):
                    if input_cancelled:return stopped_result()
                    return {"ok":False,"playback_started":False,"message":"The boost prompt input could not be confirmed."}
                emit("Ad UI: selected the detected boost control.")
            elif not panel and not entry_clicked and "close" in matches and dismissals<3:
                dismissals+=1
                if not deliver(canvas,matches["close"]):
                    if input_cancelled:return stopped_result()
                    return {"ok":False,"playback_started":False,"message":"The game popup could not be dismissed."}
                emit("Ad UI: dismissed a game popup to reach the boost control.")
        elif phase=="playing":
            elapsed=time.monotonic()-started
            if closed and not panel and time.monotonic()-closed_at>=3:
                # A credited ad can open the native Quest Complete display.
                # Let the caller verify credit before refreshing that UI.
                return {"ok":False,"playback_started":True,"closed_completed_ad":True,
                        "message":"The completed ad closed into a game reward display; server credit must be verified before refreshing."}
            if panel:
                if elapsed<2:
                    # Unity processes mouse input on its next frame.
                    pass
                elif closed:
                    emit("Ad UI: completed playback and returned to the boost panel.")
                    return {"ok":True,"playback_started":True}
                elif elapsed>5:
                    return {"ok":False,"playback_started":True,"message":"The game returned to its prompt without a completed ad. A real ad may be unavailable."}
            elif not closed and elapsed>=12 and "close" in matches:
                closed=True;closed_at=time.monotonic()
                if not deliver(canvas,matches["close"]):
                    if input_cancelled:return stopped_result()
                    return {"ok":False,"playback_started":True,"message":"The completed ad Close input could not be confirmed. Checking credit without starting another ad."}
                emit("Ad UI: the ad revealed its Close button; closing the finished playback.")
        time.sleep(1)
    return {"ok":False,"playback_started":started is not None,
            "message":"The game did not expose a completed ad within the wait. No paid skip or ad destination was clicked."}


def reset_ad_game_view(log=None):
    """Reload only the dedicated AT page after verified finished-ad credit."""
    from urllib.parse import urlparse
    from . import ad_ui as ui
    targets=[row for row in ui._page_targets() if row.get("type")=="page"
        and (urlparse(str(row.get("url")or"")).hostname or "").lower() in {"kongregate.com","www.kongregate.com"}
        and "animation-throwdown" in urlparse(str(row.get("url")or"")).path.lower()]
    if len(targets)!=1:return False
    try:ui._cdp(targets[0]["webSocketDebuggerUrl"],"Page.reload",{"ignoreCache":False})
    except Exception:return False
    (log or (lambda _message:None))("Ad UI: refreshing the credited game's reward display before another ad.")
    return True
