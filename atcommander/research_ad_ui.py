"""Automatic genuine research speedup ads in Commander's dedicated browser."""
from __future__ import annotations

import time

from .ad_visual_templates import TEMPLATES as AD_TEMPLATES
from .research_ad_templates import TEMPLATES as RESEARCH_TEMPLATES
from .research_ad_targets import select_research_ad_target, verify_research_ad_target
from .local_commands import CommandUnavailable
from .ad_browser_focus import GameInputStopped

AREAS={"research_time_title":[.25,.75,.02,.20], "research_ad_button":[.20,.60,.75,1],
       "research_boost_title":[.25,.75,.01,.25], "research_boost_description":[.20,.80,.30,.70],
       "research_watch_button":[.30,.70,.65,1],
       "research_hub_marker":[.50,.80,.78,.94], "research_ad_chamber":[.05,.38,0,.40],
       "research_picker_marker":[.15,.55,.40,.70],
       "research_unlock_watch_button":[.33,.58,.04,.32],
       "research_entry":[.78,1,.10,.45], "close":[.55,1,0,.32]}


def _view(prep):
    from .automatic_ads import _view as surface, classify_pixels
    templates={**RESEARCH_TEMPLATES,"close":AD_TEMPLATES['close']}
    return surface(prep,lambda shot,evaluate:classify_pixels(shot,evaluate,templates=templates,areas=AREAS))


def _click(prep,canvas,point):
    """Allow Unity to process pointer state before releasing one real click."""
    from . import ad_ui as ui
    target=prep.get('input_target')or prep['target'];canvas=prep.get('input_canvas')or canvas
    ws=target['webSocketDebuggerUrl']
    from .ad_browser_focus import ensure_game_input_focus,check_game_input_stop
    ensure_game_input_focus(prep, ui._cdp)
    point={"x":float(canvas['x'])+float(canvas['width'])*float(point['x']),
           "y":float(canvas['y'])+float(canvas['height'])*float(point['y'])}
    ui._cdp(ws,'Input.dispatchMouseEvent',{'type':'mouseMoved',**point})
    time.sleep(.25)
    check_game_input_stop(prep)
    ui._cdp(ws,'Input.dispatchMouseEvent',{'type':'mousePressed',**point,'button':'left','buttons':1,'clickCount':1})
    # Finish this press/release pair even if Stop arrives while held. Leaving
    # Unity's pointer down could turn the next interaction into a drag.
    time.sleep(.35)
    ui._cdp(ws,'Input.dispatchMouseEvent',{'type':'mouseMoved',**point,'button':'left','buttons':1})
    time.sleep(.25)
    ui._cdp(ws,'Input.dispatchMouseEvent',{'type':'mouseReleased',**point,'button':'left','buttons':0,'clickCount':1})


def play_research_ad(client, context=None, log=None, should_stop=None, wait_seconds=100):
    """Play once, then require credit and a benefit for the frozen exact task.

    Unlocking a new pair is deliberately unavailable until native card identity
    selection is verified. This function never invokes an ad-completion API.
    """
    from . import ad_ui as ui
    emit=log or (lambda _message:None);stopped=should_stop or (lambda:False)
    def failure(message,started=False):
        return {"ok":False,"playback_started":started,"message":message}
    if stopped():return failure('Research ad stopped before playback.')
    try:prep=ui.prepare_ad_game_ui(client=client,log=emit,should_stop=stopped,wait_for_canvas=60)
    except Exception:return failure('The game browser could not be prepared. No ad was started.')
    if not prep.get('ok'):return failure(prep.get('message')or'The real game did not load.')
    if stopped():return failure('Research ad stopped before playback.')
    prep['_commander_should_stop']=stopped
    try:target=select_research_ad_target(client.init(),context)
    except Exception:return failure('No unambiguous eligible running research-ad task is available. No ad was started.')
    if target['slot_index']!=3:
        return failure('Automatic navigation is verified for the native TV chamber only. No ad was started.')
    deadline=time.monotonic()+max(20,min(150,float(wait_seconds)))
    started=None;closed=False;closed_at=None;last_credit_read=None;route=set();dismissals=0;previous=None;ad_frames=0
    while time.monotonic()<deadline:
        if stopped():
            return failure('Research ad stopped; any pending benefit must be checked before another playback.',started is not None)
        try:matches,canvas=_view(prep)
        except Exception:return failure('The research ad controls could not be read. Playback was not repeated.',started is not None)
        if stopped():return failure('Research ad stopped before another browser input.',started is not None)
        popup='research_time_title' in matches and 'research_ad_button' in matches
        prompt='research_boost_description' in matches and 'research_watch_button' in matches
        native_panel=popup or any(key in matches for key in ('research_time_title','research_boost_title','research_boost_description'))
        if started is None:
            action=None
            if prompt:action=('watch',matches['research_watch_button'])
            elif popup and 'prompt' not in route:action=('prompt',matches['research_ad_button'])
            elif {'research_hub_marker','research_ad_chamber'}<=matches.keys() and 'chamber' not in route:
                action=('chamber',matches['research_ad_chamber'])
            elif 'research_entry' in matches and 'research' not in route:
                action=('research',matches['research_entry'])
            elif 'close' in matches and dismissals<3 and not native_panel:
                action=('dismiss',matches['close'])
            if action is None:previous=None
            else:
                name,point=action
                identity=(name,round(point['x'],2),round(point['y'],2))
                # Native panels animate after a click. Require the same control
                # in two separate frames, and stop after one input per stage.
                if previous!=identity:previous=identity
                else:
                    if name=='watch':
                        try:select_research_ad_target(client.init(),target)
                        except Exception:return failure('The selected research task changed before playback. No ad was started.')
                        if stopped():return failure('Research ad stopped before playback.')
                        started=time.monotonic()
                    elif name=='dismiss':dismissals+=1
                    else:route.add(name)
                    previous=None
                    if stopped():return failure('Research ad stopped before another browser input.',started is not None)
                    try:_click(prep,canvas,point)
                    except GameInputStopped:
                        if name=='watch':started=None
                        return failure('Research ad stopped before another browser input.',started is not None)
                    except Exception:return failure('Research browser input has an unknown outcome and was not repeated.',started is not None)
                    emit('Research ad: selected the real Watch control.' if name=='watch' else 'Research ad: navigating the detected native controls.')
        elif not closed:
            elapsed=time.monotonic()-started
            native_screen=native_panel or 'research_entry' in matches or 'research_hub_marker' in matches
            ad_frames=0 if native_screen else ad_frames+1
            if native_panel and elapsed>=5:
                return failure('The game returned without completed research ad playback. No ad was repeated.',True)
            if not native_screen and ad_frames>=3 and elapsed>=12 and 'close' in matches:
                if stopped():return failure('Research ad stopped before another browser input.',True)
                closed=True;closed_at=time.monotonic()
                try:_click(prep,canvas,matches['close'])
                except Exception:
                    # Only a fresh state read can resolve an uncertain Close.
                    pass
                emit('Research ad: closed the completed ad using its own Close button.')
        elif time.monotonic()-closed_at>=3 and (last_credit_read is None or time.monotonic()-last_credit_read>=8):
            last_credit_read=time.monotonic()
            try:after=client.init()
            except Exception:after=None
            if after is not None and verify_research_ad_target(target,after):
                return {'ok':True,'verified':True,'phase':'speedup','slot_index':target['slot_index'],
                        'pair':target['pair'],'playback_started':True,
                        'message':'The selected research task received a verified real ad speedup.'}
            if time.monotonic()-closed_at>=24:
                return failure('The exact research task benefit was not confirmed. No ad was repeated.',True)
        time.sleep(1)
    return failure('The game did not expose a verified completed research ad within the wait.',started is not None)
