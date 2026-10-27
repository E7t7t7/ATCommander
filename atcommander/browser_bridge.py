from __future__ import annotations

import base64
import json
import time
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

import requests
import websocket

from .learn_center import PORT
from .settings import load_settings, save_settings


def _targets() -> list[dict[str, Any]]:
    try:
        r=requests.get(f"http://127.0.0.1:{PORT}/json",timeout=1.0)
        data=r.json() if r.ok else []
        return data if isinstance(data,list) else []
    except Exception:
        return []


def _cdp(ws, ident:int, method:str, params:dict[str,Any]|None=None):
    ws.send(json.dumps({"id":ident,"method":method,"params":params or {}}))


def discover_static_resource_urls(key:str) -> list[str]:
    """Read mission/campaign/item/etc. JSON URLs already loaded by the live web client.

    The exact AT CDN filename pattern changes. The browser already knows the URL,
    so prefer its Performance resource list over filename guessing and cache the
    discovered URL for later Refresh Account calls.
    """
    key=str(key or '').lower()
    terms={
        'mission_data':('mission','missions'),
        'campaign_data':('campaign','campaigns'),
        'item_data':('item_data','items'),
        'hero_data':('hero_data','heroes','commander'),
        'card_data':('card_data','cards'),
    }.get(key,(key,))
    found=[]
    expr="""(() => { try { return performance.getEntriesByType('resource').map(e=>e.name).filter(Boolean); } catch(e) { return []; } })()"""
    for t in _targets():
        if t.get('type') not in {'page','iframe','worker','service_worker','shared_worker'} or not t.get('webSocketDebuggerUrl'):
            continue
        try:
            ws=websocket.create_connection(t['webSocketDebuggerUrl'],timeout=2,suppress_origin=True)
            try:
                _cdp(ws,1,'Runtime.evaluate',{'expression':expr,'returnByValue':True})
                end=time.time()+2.5
                while time.time()<end:
                    m=json.loads(ws.recv())
                    if m.get('id')!=1: continue
                    vals=(((m.get('result') or {}).get('result') or {}).get('value')) or []
                    if isinstance(vals,list):
                        for u in vals:
                            su=str(u or '')
                            low=su.lower()
                            if (su.startswith(('http://','https://')) and any(term in low for term in terms)
                                    and 'api.php' not in low and '?message=' not in low and '&message=' not in low):
                                # AT has used hashed/versioned static resource URLs
                                # that do not always end in .json or contain /data/.
                                # Exclude live api.php mutations, then let the caller
                                # verify that the remaining resource is really JSON.
                                found.append(su)
                    break
            finally:
                ws.close()
        except Exception:
            pass
    # Keep prior cached URL(s) too; live browser wins ordering.
    settings=load_settings(); cached=((settings.get('static_resource_urls') or {}).get(key) or [])
    if isinstance(cached,str): cached=[cached]
    out=[]
    for u in [*found,*cached]:
        if u and u not in out: out.append(u)
    if found:
        try:
            m=dict(settings.get('static_resource_urls') or {}); m[key]=out[:8]; settings['static_resource_urls']=m; save_settings(settings)
        except Exception:
            pass
    return out



def fetch_json_resource_in_browser(url: str, *, timeout: float = 20.0) -> dict[str, Any] | list[Any] | None:
    """Fetch a static AT resource from the live game browser and return JSON.

    This is a fallback for versioned event mission/campaign resources that are
    visible in Performance entries but reject or redirect a plain requests call.
    """
    target=_target_for_profile({})
    if not target or not str(url).startswith(('http://','https://')):
        return None
    js=f"""(async()=>{{
      try {{
        const r=await fetch({json.dumps(str(url))},{{credentials:'include'}});
        if (!r.ok) return {{__atc_error:'HTTP '+r.status}};
        const text=await r.text();
        try {{ return JSON.parse(text); }} catch(e) {{ return {{__atc_error:'non-json'}}; }}
      }} catch(e) {{ return {{__atc_error:String(e)}}; }}
    }})()"""
    try:
        ws=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=2,suppress_origin=True)
    except Exception:
        return None
    try:
        result=_runtime_eval(ws,650,js,timeout=timeout)
        if isinstance(result,dict) and result.get('__atc_error'):
            return None
        if isinstance(result,(dict,list)):
            return result
        return None
    finally:
        try: ws.close()
        except Exception: pass


def _target_score(t: dict[str, Any], preferred_id: str = '') -> int:
    url=str(t.get('url') or '').lower(); typ=str(t.get('type') or '').lower(); tid=str(t.get('id') or '')
    score=0
    if preferred_id and tid==preferred_id: score+=10000
    if 'cb-live.synapsegames.com' in url: score+=1200
    elif 'synapsegames.com' in url: score+=1000
    if 'animationthrowdown' in url: score+=850
    if 'animation-throwdown' in url or 'throwdown' in url: score+=650
    if typ in {'iframe','worker','service_worker','shared_worker'}: score+=120
    if typ=='page': score+=60
    if 'kongregate.com' in url: score-=250
    return score


def _candidate_runtime_targets(profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    profile=profile or {}; preferred=str(profile.get('browser_target_id') or '')
    rows=[t for t in _targets() if t.get('webSocketDebuggerUrl') and t.get('type') in {'page','iframe','worker','service_worker','shared_worker'}]
    rows.sort(key=lambda t:_target_score(t,preferred), reverse=True)
    return rows


def _target_for_profile(profile:dict[str,Any]) -> dict[str,Any]|None:
    rows=_candidate_runtime_targets(profile)
    return rows[0] if rows else None


def _cdp_result(ws, ident:int, method:str, params:dict[str,Any]|None=None, timeout:float=4.0) -> dict[str,Any]:
    _cdp(ws,ident,method,params)
    end=time.time()+timeout
    while time.time()<end:
        try:
            ws.settimeout(max(.1,end-time.time())); msg=json.loads(ws.recv())
        except Exception:
            continue
        if msg.get('id')!=ident: continue
        if msg.get('error'): return {'__error':str(msg.get('error'))}
        return msg.get('result') or {}
    return {'__error':'timeout'}


def _frame_rows(tree: dict[str,Any]) -> list[dict[str,Any]]:
    out=[]
    def walk(node):
        if not isinstance(node,dict): return
        f=node.get('frame') or {}
        if isinstance(f,dict): out.append(f)
        for c in node.get('childFrames') or []: walk(c)
    walk(tree.get('frameTree') or tree)
    return out


def _frame_score(frame:dict[str,Any]) -> int:
    url=str(frame.get('url') or '').lower(); score=0
    if 'cb-live.synapsegames.com' in url: score+=1200
    elif 'synapsegames.com' in url: score+=1000
    if 'animationthrowdown' in url: score+=850
    if 'animation-throwdown' in url or 'throwdown' in url: score+=650
    if 'kongregate.com' in url: score-=250
    return score

def _replace_slot_in_form(raw:str, slot_id:str) -> str:
    try:
        pairs=parse_qs(raw,keep_blank_values=True)
        changed=False
        for k in list(pairs):
            lk=str(k).lower()
            if lk in {'slot_id','island_id','target_slot_id'} or ('slot' in lk and 'id' in lk):
                pairs[k]=[str(slot_id)];changed=True
        if not changed:
            pairs['slot_id']=[str(slot_id)]
        return urlencode([(k,v) for k,vals in pairs.items() for v in vals])
    except Exception:
        return raw




def _format_profile_value(value: Any, values: dict[str, Any]) -> Any:
    if isinstance(value, str):
        try:
            return value.format(**values)
        except Exception:
            return value
    if isinstance(value, dict):
        return {str(k): _format_profile_value(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [_format_profile_value(v, values) for v in value]
    if isinstance(value, tuple):
        return [_format_profile_value(v, values) for v in value]
    return value


def _runtime_eval(ws, ident: int, expression: str, timeout: float = 12.0, context_id: int | None = None) -> dict[str, Any]:
    params={
        'expression': expression,
        'awaitPromise': True,
        'returnByValue': True,
    }
    if context_id is not None: params['contextId']=int(context_id)
    _cdp(ws, ident, 'Runtime.evaluate', params)
    end=time.time()+max(2.0,float(timeout))
    while time.time()<end:
        try:
            ws.settimeout(max(.1,end-time.time()))
            msg=json.loads(ws.recv())
        except Exception:
            continue
        if msg.get('id')!=ident:
            continue
        if msg.get('error'):
            return {'ok':False,'message':str(msg.get('error'))}
        result=((msg.get('result') or {}).get('result') or {})
        value=result.get('value')
        return value if isinstance(value,dict) else {'ok':True,'value':value}
    return {'ok':False,'message':'Browser-context request timed out.'}


def browser_call_profile(profile: dict[str, Any], values: dict[str, Any], credentials, *, timeout: float = 12.0) -> dict[str, Any]:
    """Execute one learned AT request inside the live game browser.

    Unlike Network.replayXHR this does not depend on a transient Chromium request
    id. Learned action fields are rebuilt with current live values, while current
    credentials are injected only in memory and never persisted in the profile.
    """
    if not isinstance(profile,dict) or not profile.get('message'):
        return {'ok':False,'message':'The learned browser request has no message name.'}
    target=_target_for_profile(profile)
    if not target:
        return {'ok':False,'message':'The fixed Learning Browser/game window is not open.'}
    payload={str(k):_format_profile_value(v,values or {}) for k,v in (profile.get('data') or {}).items()}
    payload['password']=str(getattr(credentials,'password_hash','') or '')
    message=str(profile.get('message') or '')
    user_id=str(getattr(credentials,'user_id','') or '')
    if not user_id or not payload['password']:
        return {'ok':False,'message':'Animation Throwdown credentials are not available for browser-context replay.'}
    js=f"""(async()=>{{
      const payload={json.dumps(payload,separators=(',',':'))};
      const body=new URLSearchParams();
      for (const [k,v] of Object.entries(payload)) {{
        if (v===null || v===undefined) continue;
        body.append(k, (typeof v==='object') ? JSON.stringify(v) : String(v));
      }}
      const url={json.dumps('https://cb-live.synapsegames.com/api.php')}+'?message='+encodeURIComponent({json.dumps(message)})+'&user_id='+encodeURIComponent({json.dumps(user_id)});
      try {{
        const r=await fetch(url,{{method:'POST',credentials:'include',headers:{{'Accept':'application/json,text/plain,*/*','Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'}},body:body.toString()}});
        const text=await r.text();
        let data={{}};
        try {{ data=text ? JSON.parse(text) : {{}}; }} catch(e) {{ data={{_raw:text.slice(0,2000)}}; }}
        return {{ok:r.ok,status:r.status,response:data}};
      }} catch(e) {{ return {{ok:false,message:String(e)}}; }}
    }})()"""
    last={'ok':False,'message':'No usable live game execution context was found.'}
    for target in _candidate_runtime_targets(profile):
        try:
            ws=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=2,suppress_origin=True)
        except Exception as exc:
            last={'ok':False,'message':f'Could not attach to one live game target: {exc}'}; continue
        try:
            # First try the target's own origin.  1.7.13 accidentally preferred
            # Kongregate's outer page, where cb-live fetch is blocked by CORS.
            result=_runtime_eval(ws,700,js,timeout=timeout)
            if result.get('ok'):
                response=result.get('response')
                return {'ok':True,'status':result.get('status'),'response':response if isinstance(response,dict) else {'result':response},'target_url':target.get('url')}
            last=result
            # For a page target, execute from the actual game iframe's isolated
            # world.  This preserves the logged-in browser/game origin without
            # requiring a global mouse click or a cross-origin fetch from Kongregate.
            if str(target.get('type') or '')=='page':
                tree=_cdp_result(ws,701,'Page.getFrameTree',{},timeout=3.0)
                frames=sorted(_frame_rows(tree),key=_frame_score,reverse=True)
                for j,frame in enumerate(frames[:10]):
                    if _frame_score(frame)<=0: continue
                    fid=str(frame.get('id') or '')
                    if not fid: continue
                    world=_cdp_result(ws,710+j,'Page.createIsolatedWorld',{'frameId':fid,'worldName':'ATCommanderRuntime','grantUniveralAccess':False},timeout=3.0)
                    cid=world.get('executionContextId')
                    if cid is None: continue
                    result=_runtime_eval(ws,730+j,js,timeout=timeout,context_id=int(cid))
                    if result.get('ok'):
                        response=result.get('response')
                        return {'ok':True,'status':result.get('status'),'response':response if isinstance(response,dict) else {'result':response},'frame_url':frame.get('url')}
                    last=result
        finally:
            try: ws.close()
            except Exception: pass
    return last


def replay_profile_flow_in_browser(profile: dict[str, Any], values: dict[str, Any], credentials, *, timeout: float = 12.0) -> dict[str, Any]:
    """Replay a learned short request flow in-browser without stale request ids."""
    flow=(profile or {}).get('flow') or []
    steps=[x for x in flow if isinstance(x,dict) and x.get('message')] if isinstance(flow,list) else []
    if not steps:
        steps=[profile]
    responses=[]
    for i,step in enumerate(steps):
        result=browser_call_profile(step,values,credentials,timeout=timeout)
        responses.append({'message':str(step.get('message') or ''),'result':result})
        if not result.get('ok'):
            return {'ok':False,'message':result.get('message') or f"Browser call {step.get('message')} failed.",'steps':responses}
        response=result.get('response') or {}
        if isinstance(response,dict) and response.get('result') is False:
            return {'ok':True,'response':response,'steps':responses}
        if i+1<len(steps):
            time.sleep(.08)
    return {'ok':True,'response':(responses[-1]['result'].get('response') if responses else {}),'steps':responses}

def replay_captured_xhr(profile:dict[str,Any], *, target_slot_id:str, timeout:float=10.0) -> dict[str,Any]:
    """Replay the exact browser XHR captured from a successful Siege start.

    This keeps the browser's own request envelope/signatures instead of rebuilding
    fightGuildSiege with requests.Session (which AT rejects as Invalid combat data).
    For A→B→C, Fetch interception swaps only slot_id before the replay is released.
    """
    reqid=str(profile.get('browser_request_id') or '')
    if not reqid:
        return {'ok':False,'message':'The learned Siege profile has no browser request id. Re-learn one successful Siege attack in this build.'}
    target=_target_for_profile(profile)
    if not target:
        return {'ok':False,'message':'The Learning Browser is not open, so the captured Siege XHR cannot be replayed.'}
    ws_url=target.get('webSocketDebuggerUrl')
    try:
        ws=websocket.create_connection(ws_url,timeout=2,suppress_origin=True)
    except Exception as exc:
        return {'ok':False,'message':f'Could not attach to the live game browser: {exc}'}
    try:
        _cdp(ws,1,'Network.enable',{})
        _cdp(ws,2,'Fetch.enable',{'patterns':[{'urlPattern':'*api.php*','requestStage':'Request'}]})
        # Drain command acknowledgements briefly.
        deadline=time.time()+1.0
        while time.time()<deadline:
            try:
                ws.settimeout(max(.05,deadline-time.time())); m=json.loads(ws.recv())
                if m.get('id') in {1,2}: continue
            except Exception: break
        ws.settimeout(1.0)
        _cdp(ws,3,'Network.replayXHR',{'requestId':reqid})
        network_id=None; response_status=None; get_body_id=100
        body_result=None; replay_seen=False
        end=time.time()+max(4.0,float(timeout))
        while time.time()<end:
            try:m=json.loads(ws.recv())
            except Exception:continue
            if m.get('id')==3 and m.get('error'):
                return {'ok':False,'message':f"Browser replayXHR rejected the captured request id: {m.get('error')}"}
            method=m.get('method'); p=m.get('params') or {}
            if method=='Fetch.requestPaused':
                rid=p.get('requestId'); req=p.get('request') or {}; url=str(req.get('url') or '')
                post=str(req.get('postData') or '')
                parsed=urlparse(url); q=parse_qs(parsed.query)
                msg=(q.get('message') or [''])[0]
                if str(msg).lower()=='fightguildsiege' or 'fightGuildSiege' in post or 'fightguildsiege' in post.lower():
                    replay_seen=True; network_id=str(p.get('networkId') or '') or network_id
                    newpost=_replace_slot_in_form(post,str(target_slot_id)) if post else post
                    args={'requestId':rid}
                    if newpost!=post: args['postData']=base64.b64encode(newpost.encode('utf-8')).decode('ascii')
                    _cdp(ws,10,'Fetch.continueRequest',args)
                else:
                    _cdp(ws,11,'Fetch.continueRequest',{'requestId':rid})
            elif method=='Network.responseReceived':
                rid=str(p.get('requestId') or '')
                resp=p.get('response') or {}; url=str(resp.get('url') or '')
                q=parse_qs(urlparse(url).query); msg=(q.get('message') or [''])[0]
                if (network_id and rid==network_id) or str(msg).lower()=='fightguildsiege':
                    network_id=rid; response_status=resp.get('status'); get_body_id+=1; _cdp(ws,get_body_id,'Network.getResponseBody',{'requestId':rid})
            elif m.get('id')==get_body_id:
                raw=str((m.get('result') or {}).get('body') or '')
                try: body_result=json.loads(raw) if raw else {}
                except Exception: body_result={'_raw':raw[:2000]}
                return {'ok':True,'response':body_result if isinstance(body_result,dict) else {'result':body_result},'status':response_status,'replay_seen':replay_seen}
        return {'ok':False,'message':'The captured Siege XHR was not replayable in the current browser session. Keep the browser open after learning and try once more.','replay_seen':replay_seen}
    finally:
        try:
            _cdp(ws,99,'Fetch.disable',{})
        except Exception: pass
        try:ws.close()
        except Exception:pass



def _override_replayed_form(raw: str, overrides: dict[str, Any]) -> str:
    """Replace only known dynamic action fields in an already-valid browser XHR."""
    if not raw:
        return raw
    pairs=parse_qsl(raw,keep_blank_values=True)
    out=[]
    for k,v in pairs:
        lk=str(k).lower()
        nv=v
        if lk in overrides and overrides.get(lk) not in (None,''):
            nv=str(overrides[lk])
        elif lk in {'challenge','challengeid'} and overrides.get('challenge_id') not in (None,''):
            nv=str(overrides['challenge_id'])
        elif lk in {'event','eventid'} and overrides.get('event_id') not in (None,''):
            nv=str(overrides['event_id'])
        elif lk in {'mission','missionid'} and overrides.get('mission_id') not in (None,''):
            nv=str(overrides['mission_id'])
        elif lk in {'item','itemid','store_item_id','product_id','purchase_id'} and overrides.get('item_id') not in (None,''):
            nv=str(overrides['item_id'])
        out.append((k,nv))
    return urlencode(out)


def replay_captured_api_xhr(profile:dict[str,Any], *, expected_message:str, overrides:dict[str,Any]|None=None, timeout:float=10.0) -> dict[str,Any]:
    """Replay the exact successful browser XHR while changing only live fields.

    This path exists for AT mutations that reject reconstructed HTTP requests as
    ``Invalid combat data``. Chromium replays the original request envelope; Fetch
    interception edits only fields such as challenge_id/level/mission_id. No
    password, cookie, token, or browser signature is copied into Commander files.
    """
    reqid=str((profile or {}).get('browser_request_id') or '')
    if not reqid:
        return {'ok':False,'needs_capture':True,'message':f'No native {expected_message} browser request is saved. Capture one successful manual start in Diagnostics / Learn.'}
    target=_target_for_profile(profile or {})
    if not target:
        return {'ok':False,'message':'The dedicated Learning Browser/game window is not open.'}
    try:
        ws=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=2,suppress_origin=True)
    except Exception as exc:
        return {'ok':False,'message':f'Could not attach to the dedicated game browser: {exc}'}
    overrides={str(k).lower():v for k,v in (overrides or {}).items()}
    wanted=str(expected_message or '').lower()
    try:
        _cdp(ws,801,'Network.enable',{})
        _cdp(ws,802,'Fetch.enable',{'patterns':[{'urlPattern':'*api.php*','requestStage':'Request'}]})
        end_ack=time.time()+0.5
        while time.time()<end_ack:
            try:
                ws.settimeout(max(.05,end_ack-time.time())); m=json.loads(ws.recv())
                if m.get('id') in {801,802}: continue
            except Exception: break
        ws.settimeout(1.0)
        _cdp(ws,803,'Network.replayXHR',{'requestId':reqid})
        network_id=None; response_status=None; body_cmd=None; replay_seen=False
        end=time.time()+max(4.0,float(timeout))
        while time.time()<end:
            try:m=json.loads(ws.recv())
            except Exception:continue
            if m.get('id')==803 and m.get('error'):
                return {'ok':False,'stale':True,'needs_capture':True,'message':f"Saved native request is stale in this browser session: {m.get('error')}"}
            method=m.get('method'); p=m.get('params') or {}
            if method=='Fetch.requestPaused':
                frid=p.get('requestId'); req=p.get('request') or {}; url=str(req.get('url') or ''); post=str(req.get('postData') or '')
                q=parse_qs(urlparse(url).query); msg=str((q.get('message') or [''])[0] or '')
                is_match=(msg.lower()==wanted) or (wanted and wanted in post.lower())
                args={'requestId':frid}
                if is_match:
                    replay_seen=True; network_id=str(p.get('networkId') or '') or network_id
                    newpost=_override_replayed_form(post,overrides)
                    if newpost!=post: args['postData']=base64.b64encode(newpost.encode('utf-8')).decode('ascii')
                    # Rare clients place action fields in the query string.
                    parsed=urlparse(url); qp=parse_qsl(parsed.query,keep_blank_values=True); changed=False; out=[]
                    for k,v in qp:
                        lk=str(k).lower(); nv=v
                        if lk in overrides and overrides.get(lk) not in (None,''):
                            nv=str(overrides[lk]); changed=True
                        out.append((k,nv))
                    if changed: args['url']=urlunparse(parsed._replace(query=urlencode(out)))
                _cdp(ws,804 if is_match else 805,'Fetch.continueRequest',args)
            elif method=='Network.responseReceived':
                rid=str(p.get('requestId') or ''); resp=p.get('response') or {}; url=str(resp.get('url') or '')
                msg=str((parse_qs(urlparse(url).query).get('message') or [''])[0] or '')
                if (network_id and rid==network_id) or msg.lower()==wanted:
                    network_id=rid; response_status=resp.get('status'); body_cmd=880; _cdp(ws,body_cmd,'Network.getResponseBody',{'requestId':rid})
            elif body_cmd is not None and m.get('id')==body_cmd:
                raw=str((m.get('result') or {}).get('body') or '')
                try: body=json.loads(raw) if raw else {}
                except Exception: body={'_raw':raw[:2000]}
                return {'ok':True,'response':body if isinstance(body,dict) else {'result':body},'status':response_status,'replay_seen':replay_seen,'target_url':target.get('url')}
        return {'ok':False,'message':f'The saved native {expected_message} request could not be observed during replay. Re-capture one successful manual start in the same dedicated browser session.','replay_seen':replay_seen,'needs_capture':True}
    finally:
        try:_cdp(ws,899,'Fetch.disable',{})
        except Exception:pass
        try:ws.close()
        except Exception:pass


def capture_next_successful_api_request(expected_message: str, *, timeout: float = 60.0, should_stop=None) -> dict[str, Any]:
    """Capture the next *live* successful AT API request from the open mini browser.

    Unlike saved ``Network.replayXHR`` ids, this listener is armed before the user
    performs the one manual action. It records the request id/target id immediately
    in the same Chromium session and waits for the response so a rejected request
    is never installed as the new native template. Only field *names* and safe
    action fields are returned for diagnostics; credentials/signature values are
    never persisted here.
    """
    import queue as _queue
    import threading as _threading

    cancelled=should_stop or (lambda:False)
    stopped_result={'ok':False,'stopped':True,'message':'Live request capture stopped. No capture was installed.'}
    if cancelled():return stopped_result

    wanted=str(expected_message or '').strip().lower()
    if not wanted:
        return {'ok':False,'message':'No API message was specified for live capture.'}
    targets=_candidate_runtime_targets({})
    if not targets:
        return {'ok':False,'message':'The mini AT browser is not open or has no attachable game target.'}

    # Arm pointer probes on all visible document targets up front. The network
    # request may originate from a worker while the user's trusted click lands on
    # the game iframe/page, so tying click capture only to the request target can
    # lose the exact action we need to replay.
    probe_expr="""(() => {
      try {
        if (!window.__atcPointerProbeInstalled) {
          window.__atcPointerProbeInstalled = true; window.__atcLastPointer = null;
          document.addEventListener('pointerdown', e => { window.__atcLastPointer = {
            x:Number(e.clientX||0), y:Number(e.clientY||0), button:Number(e.button||0),
            ts:Date.now(), tag:(e.target&&e.target.tagName)||'', id:(e.target&&e.target.id)||''
          }; }, true);
        }
        return true;
      } catch(e) { return false; }
    })()"""
    for _t in targets:
        if cancelled():return stopped_result
        if str(_t.get('type') or '') not in {'page','iframe'}:continue
        _ws=None
        try:
            _ws=websocket.create_connection(_t['webSocketDebuggerUrl'],timeout=1.5,suppress_origin=True)
            _cdp_result(_ws,890,'Runtime.evaluate',{'expression':probe_expr,'returnByValue':True},timeout=1.0)
        except Exception:pass
        finally:
            if _ws is not None:
                try:_ws.close()
                except Exception:pass

    def latest_pointer_anywhere():
        best=None
        for _t in targets:
            if cancelled():return None
            if str(_t.get('type') or '') not in {'page','iframe'}:continue
            _ws=None
            try:
                _ws=websocket.create_connection(_t['webSocketDebuggerUrl'],timeout=1.5,suppress_origin=True)
                cr=_cdp_result(_ws,891,'Runtime.evaluate',{'expression':'(() => window.__atcLastPointer || null)()','returnByValue':True},timeout=1.0)
                click=(((cr.get('result') or {}).get('value')) if isinstance(cr,dict) else None)
                if not isinstance(click,dict):continue
                ts=float(click.get('ts') or 0)
                if (time.time()*1000-ts)>15000:continue
                row={'x':float(click.get('x') or 0),'y':float(click.get('y') or 0),'button':int(click.get('button') or 0),
                     'target_id':str(_t.get('id') or ''),'target_url':str(_t.get('url') or ''),
                     'tag':str(click.get('tag') or ''),'element_id':str(click.get('id') or ''),'ts':ts}
                if best is None or ts>best.get('ts',0):best=row
            except Exception:pass
            finally:
                if _ws is not None:
                    try:_ws.close()
                    except Exception:pass
        if isinstance(best,dict):best.pop('ts',None)
        return best

    outq:_queue.Queue=_queue.Queue()
    stop=_threading.Event()

    sensitive_parts=('password','cookie','authorization','token','session','secret')
    sensitive_exact={'password','password_hash','pass','auth','user_id','kong_token','kong_vars'}

    def safe_action_fields(data:dict[str,Any]) -> dict[str,Any]:
        out={}
        for k,v in (data or {}).items():
            lk=str(k).lower()
            if lk in sensitive_exact or any(x in lk for x in sensitive_parts) or lk.endswith('sig'):
                continue
            if lk in {'message'}:
                continue
            out[str(k)]=v
        return out

    def parse_form(raw:str) -> dict[str,Any]:
        if not raw:return {}
        try:
            d=parse_qs(raw,keep_blank_values=True)
            if d:
                return {str(k):(vals[0] if len(vals)==1 else vals) for k,vals in d.items()}
        except Exception:pass
        try:
            d=json.loads(raw)
            return d if isinstance(d,dict) else {}
        except Exception:return {}

    def initiator_hint(ini:dict[str,Any]) -> dict[str,Any]:
        if not isinstance(ini,dict):return {}
        row={'type':str(ini.get('type') or '')}
        stack=ini.get('stack') or {}
        frames=stack.get('callFrames') if isinstance(stack,dict) else None
        if isinstance(frames,list) and frames:
            f=frames[0] if isinstance(frames[0],dict) else {}
            row.update({
                'function':str(f.get('functionName') or ''),
                'url':str(f.get('url') or '')[:500],
                'line':f.get('lineNumber'),
                'column':f.get('columnNumber'),
            })
        return row

    def worker(t:dict[str,Any]):
        ws=None
        try:
            if cancelled() or stop.is_set():return
            ws=websocket.create_connection(t['webSocketDebuggerUrl'],timeout=2,suppress_origin=True)
            # Record the most recent trusted pointer-down in this game target.
            # startChallenge is a Fetch request, not XHR, so the durable replay
            # path is to let the real game UI generate a fresh signed request.
            try:
                _cdp_result(ws,900,'Runtime.evaluate',{
                    'expression':"""(() => {
                      try {
                        if (!window.__atcPointerProbeInstalled) {
                          window.__atcPointerProbeInstalled = true;
                          window.__atcLastPointer = null;
                          document.addEventListener('pointerdown', e => {
                            window.__atcLastPointer = {
                              x:Number(e.clientX||0), y:Number(e.clientY||0),
                              button:Number(e.button||0), ts:Date.now(),
                              tag:(e.target && e.target.tagName)||'',
                              id:(e.target && e.target.id)||''
                            };
                          }, true);
                        }
                        return true;
                      } catch(e) { return false; }
                    })()""",
                    'returnByValue':True
                },timeout=1.5)
            except Exception:
                pass
            _cdp(ws,901,'Network.enable',{})
            ws.settimeout(.5)
            candidate=None; body_cmd=930
            deadline=time.time()+max(5.0,float(timeout))
            while not stop.is_set() and not cancelled() and time.time()<deadline:
                try:m=json.loads(ws.recv())
                except Exception:continue
                method=m.get('method'); p=m.get('params') or {}
                if method=='Network.requestWillBeSent':
                    req=p.get('request') or {}; url=str(req.get('url') or ''); raw=str(req.get('postData') or '')
                    q=parse_qs(urlparse(url).query,keep_blank_values=True)
                    data=parse_form(raw)
                    msg=str((q.get('message') or [''])[0] or data.get('message') or data.get('action') or '')
                    if msg.lower()!=wanted:
                        continue
                    rid=str(p.get('requestId') or '')
                    if not raw and rid:
                        try:
                            r=_cdp_result(ws,902,'Network.getRequestPostData',{'requestId':rid},timeout=1.5)
                            raw=str(r.get('postData') or '')
                            data=parse_form(raw)
                        except Exception:pass
                    all_names=sorted(set([*(str(k) for k in q.keys()),*(str(k) for k in data.keys())]))
                    candidate={
                        'request_id':rid,'target_id':str(t.get('id') or ''),'target_url':str(t.get('url') or ''),
                        'resource_type':str(p.get('type') or ''),'method':str(req.get('method') or ''),
                        'action_fields':safe_action_fields({**{k:(v[0] if isinstance(v,list) and len(v)==1 else v) for k,v in q.items()},**data}),
                        'field_names':all_names,'initiator':initiator_hint(p.get('initiator') or {}),
                    }
                elif candidate and method=='Network.responseReceived' and str(p.get('requestId') or '')==candidate.get('request_id'):
                    candidate['status']=(p.get('response') or {}).get('status')
                    body_cmd+=1; cmd=body_cmd
                    _cdp(ws,cmd,'Network.getResponseBody',{'requestId':candidate['request_id']})
                    end2=time.time()+3
                    while time.time()<end2 and not stop.is_set() and not cancelled():
                        try:rmsg=json.loads(ws.recv())
                        except Exception:continue
                        if rmsg.get('id')!=cmd:continue
                        rawbody=str((rmsg.get('result') or {}).get('body') or '')
                        try:body=json.loads(rawbody) if rawbody else {}
                        except Exception:body={'_raw':rawbody[:1200]}
                        candidate['response']=body if isinstance(body,dict) else {'result':body}
                        # Capture the click that immediately preceded the successful
                        # request. Coordinates are harmless and can be persisted;
                        # secret request/signature values are still never written.
                        try:
                            cr=_cdp_result(ws,cmd+1000,'Runtime.evaluate',{
                                'expression':'(() => window.__atcLastPointer || null)()',
                                'returnByValue':True
                            },timeout=1.0)
                            click=(((cr.get('result') or {}).get('value')) if isinstance(cr,dict) else None)
                            if isinstance(click,dict) and (time.time()*1000-float(click.get('ts') or 0)) < 15000:
                                candidate['click']={
                                    'x':float(click.get('x') or 0),
                                    'y':float(click.get('y') or 0),
                                    'button':int(click.get('button') or 0),
                                    'target_id':str(t.get('id') or ''),
                                    'target_url':str(t.get('url') or ''),
                                    'tag':str(click.get('tag') or ''),
                                    'element_id':str(click.get('id') or ''),
                                }
                        except Exception:
                            pass
                        # Accept only a response that looks like a real successful battle start.
                        result=(candidate['response'] or {}).get('result') if isinstance(candidate['response'],dict) else None
                        battle=(candidate['response'] or {}).get('battle_data') if isinstance(candidate['response'],dict) else None
                        candidate['ok']=bool(isinstance(battle,dict) or result is not False)
                        try:outq.put_nowait(candidate)
                        except Exception:pass
                        stop.set();return
            return
        except Exception:
            return
        finally:
            if ws is not None:
                try:ws.close()
                except Exception:pass

    threads=[]
    def finished(answer):
        stop.set()
        # Own all observer threads until their bounded reads finish. They never
        # press controls or replay requests, and cancelled results are discarded.
        end=time.monotonic()+4
        for thread in threads:thread.join(max(0,end-time.monotonic()))
        return stopped_result if cancelled() else answer
    # Highest-scoring targets first, but listen to several because the request can
    # originate from the iframe while the visible app target is a page.
    for t in targets[:8]:
        if cancelled():return finished(stopped_result)
        th=_threading.Thread(target=worker,args=(t,),daemon=True);th.start();threads.append(th)
    deadline=time.time()+max(5.0,float(timeout))
    while time.time()<deadline and not cancelled():
        try:
            row=outq.get(timeout=.25)
            stop.set()
            if row.get('ok'):
                if not row.get('click'):
                    click=latest_pointer_anywhere()
                    if click:row['click']=click
                return finished(row)
            return finished({'ok':False,'message':f"Captured {expected_message}, but AT did not return a successful battle start.",'capture':row})
        except _queue.Empty:
            continue
    return finished({'ok':False,'timeout':True,'message':f'No live {expected_message} request was seen before the {int(timeout)}s capture window expired.'})


def _target_by_id(target_id: str) -> dict[str, Any] | None:
    tid=str(target_id or '')
    if not tid:
        return None
    return next((t for t in _targets() if str(t.get('id') or '')==tid and t.get('webSocketDebuggerUrl')),None)


def dispatch_captured_game_click(profile: dict[str, Any]) -> dict[str, Any]:
    """Replay the learned in-game fight-button click with trusted CDP input.

    This never moves the Windows cursor. Focus emulation keeps the off-screen
    helper renderer active so Unity accepts the click without bringing Edge onto
    the user's desktop.
    """
    x=profile.get('browser_click_x'); y=profile.get('browser_click_y')
    if x is None or y is None:
        return {'ok':False,'needs_capture':True,'message':'No captured in-game fight click is saved. ARM LIVE FIGHT CAPTURE is only needed to repair this once.'}
    target=_target_by_id(str(profile.get('browser_click_target_id') or '')) or _target_for_profile(profile or {})
    if not target:
        return {'ok':False,'message':'The mini AT signing helper is not open. Open/reuse the mini game, log in if needed, navigate to the event pre-fight screen, then background it. No manual fight is required.'}
    try:
        ws=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=2,suppress_origin=True)
    except Exception as exc:
        return {'ok':False,'message':f'Could not attach to the mini game target: {exc}'}
    try:
        fx=float(x); fy=float(y)
        # Chromium can ignore synthetic input when a game renderer is far
        # off-screen/occluded. Focus emulation marks the target active without
        # moving the helper window or the user's Windows cursor.
        try:_cdp_result(ws,1197,'Emulation.setFocusEmulationEnabled',{'enabled':True},timeout=1.0)
        except Exception:pass
        try:_cdp_result(ws,1198,'Page.setWebLifecycleState',{'state':'active'},timeout=1.0)
        except Exception:pass
        try:_cdp_result(ws,1199,'Runtime.evaluate',{'expression':'(()=>{try{window.focus();return true}catch(e){return false}})()','returnByValue':True},timeout=1.0)
        except Exception:pass
        _cdp_result(ws,1201,'Input.dispatchMouseEvent',{'type':'mouseMoved','x':fx,'y':fy,'button':'none'},timeout=1.0)
        _cdp_result(ws,1202,'Input.dispatchMouseEvent',{'type':'mousePressed','x':fx,'y':fy,'button':'left','clickCount':1},timeout=1.0)
        _cdp_result(ws,1203,'Input.dispatchMouseEvent',{'type':'mouseReleased','x':fx,'y':fy,'button':'left','clickCount':1},timeout=1.0)
        return {'ok':True,'target_id':str(target.get('id') or ''),'target_url':str(target.get('url') or ''),'x':fx,'y':fy}
    except Exception as exc:
        return {'ok':False,'message':f'Could not replay the captured game click: {exc}'}
    finally:
        try: ws.close()
        except Exception: pass

def start_api_via_captured_game_click(profile: dict[str, Any], *, expected_message: str, timeout: float = 12.0) -> dict[str, Any]:
    """Use the live game as a background signing helper without entering battle UI.

    The game client must generate client_time/clisig/msgsig; rebuilding those
    fields outside the client is what produced warning_157. 1.7.22 therefore:

      1. arms CDP Fetch interception on the real AT API response,
      2. injects the already-learned Fight click into the off-screen Unity page,
      3. lets the signed request reach AT normally,
      4. reads the real server response for Commander,
      5. substitutes a harmless tiny response back to the hidden UI.

    The server-side battle is real, but the helper UI stays parked on the same
    pre-fight screen. That makes the helper reusable for the next fight and
    removes the requirement to manually start/finish a teaching battle.
    """
    x=profile.get('browser_click_x'); y=profile.get('browser_click_y')
    if x is None or y is None:
        return {'ok':False,'needs_capture':True,'message':'No learned Fight-button position is available. ARM LIVE FIGHT CAPTURE is a recovery tool only; capture one fight once if the game UI changed.'}
    target=_target_by_id(str(profile.get('browser_click_target_id') or '')) or _target_for_profile(profile or {})
    if not target:
        return {'ok':False,'message':'The mini AT signing helper is not open. Open/reuse it, navigate to the pre-fight screen, then use BACKGROUND GAME. No fight needs to be started manually.'}
    try:
        ws=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=2,suppress_origin=True)
    except Exception as exc:
        return {'ok':False,'message':f'Could not attach to the mini game signing helper: {exc}'}

    def _msg_from_req(req:dict[str,Any])->str:
        try:
            u=urlparse(str(req.get('url') or '')); q=parse_qs(u.query,keep_blank_values=True)
            if q.get('message'): return str(q.get('message')[0] or '')
        except Exception: pass
        raw=str(req.get('postData') or '')
        if raw:
            try:
                q=parse_qs(raw,keep_blank_values=True)
                if q.get('message'): return str(q.get('message')[0] or '')
            except Exception: pass
            try:
                d=json.loads(raw)
                if isinstance(d,dict): return str(d.get('message') or d.get('action') or '')
            except Exception: pass
        return ''

    try:
        # Only pause the battle-start API response. This avoids freezing unrelated
        # game/CDN traffic while the helper sits in the background.
        patt='*cb-live.synapsegames.com/api.php*'
        fr=_cdp_result(ws,1300,'Fetch.enable',{'patterns':[{'urlPattern':patt,'requestStage':'Response'}]},timeout=2.0)
        if fr.get('__error'):
            return {'ok':False,'message':f'Could not arm the background signing helper: {fr.get("__error")}' }
        try:_cdp_result(ws,1301,'Emulation.setFocusEmulationEnabled',{'enabled':True},timeout=1.0)
        except Exception:pass
        try:_cdp_result(ws,1302,'Page.setWebLifecycleState',{'state':'active'},timeout=1.0)
        except Exception:pass
        try:_cdp_result(ws,1303,'Runtime.evaluate',{'expression':'(()=>{try{window.focus();return true}catch(e){return false}})()','returnByValue':True},timeout=1.0)
        except Exception:pass

        fx=float(x); fy=float(y)
        # Use a second CDP session for input. If the signed Fetch returns very
        # quickly, an Input command waiter on the interception socket could
        # otherwise consume and discard Fetch.requestPaused before our loop sees
        # it. Keeping the sockets separate makes fast starts deterministic.
        iws=None
        try:
            iws=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=2,suppress_origin=True)
            try:_cdp_result(iws,1310,'Emulation.setFocusEmulationEnabled',{'enabled':True},timeout=1.0)
            except Exception:pass
            try:_cdp_result(iws,1311,'Page.setWebLifecycleState',{'state':'active'},timeout=1.0)
            except Exception:pass
            _cdp_result(iws,1312,'Input.dispatchMouseEvent',{'type':'mouseMoved','x':fx,'y':fy,'button':'none'},timeout=1.0)
            _cdp_result(iws,1313,'Input.dispatchMouseEvent',{'type':'mousePressed','x':fx,'y':fy,'button':'left','clickCount':1},timeout=1.0)
            _cdp_result(iws,1314,'Input.dispatchMouseEvent',{'type':'mouseReleased','x':fx,'y':fy,'button':'left','clickCount':1},timeout=1.0)
        finally:
            if iws is not None:
                try:iws.close()
                except Exception:pass

        deadline=time.time()+max(5.0,float(timeout)); seq=1400
        while time.time()<deadline:
            try:
                ws.settimeout(max(.1,deadline-time.time())); m=json.loads(ws.recv())
            except Exception:
                continue
            if m.get('method')!='Fetch.requestPaused':
                continue
            p=m.get('params') or {}; req=p.get('request') or {}; fid=str(p.get('requestId') or '')
            if _msg_from_req(req).lower()!=str(expected_message).lower():
                # Background status/asset API calls can share api.php. Continue
                # them immediately. Do not wait for the command response here; a
                # waiter could swallow the startChallenge event if both arrive
                # close together. Edge 153 supports continueResponse at response
                # stage; continueRequest remains a harmless compatibility fallback
                # on older Chromium if needed on the next loop.
                seq+=1; _cdp(ws,seq,'Fetch.continueResponse',{'requestId':fid})
                continue

            seq+=1; br=_cdp_result(ws,seq,'Fetch.getResponseBody',{'requestId':fid},timeout=3.0)
            raw=str(br.get('body') or '')
            if br.get('base64Encoded') and raw:
                try: raw=base64.b64decode(raw).decode('utf-8','replace')
                except Exception: pass
            try: response=json.loads(raw) if raw else {}
            except Exception: response={'_raw':raw[:1200]}
            if not isinstance(response,dict): response={'result':response}

            # The request already reached AT and created (or rejected) the real
            # battle. Feed the hidden game a benign success without battle_data so
            # it does not transition into battle UI. Commander consumes the real
            # captured response and drives the battle separately through the API.
            fake=base64.b64encode(b'{"result":true}').decode('ascii')
            seq+=1
            fulfilled=_cdp_result(ws,seq,'Fetch.fulfillRequest',{
                'requestId':fid,'responseCode':200,
                'responseHeaders':[{'name':'Content-Type','value':'application/json; charset=utf-8'},
                                   {'name':'Cache-Control','value':'no-store'}],
                'body':fake,
            },timeout=2.0)
            helper_preserved=not bool(fulfilled.get('__error'))
            if not helper_preserved:
                # Last-resort: don't wedge the browser if Edge rejects response
                # substitution. The fight still started; let the real response
                # through and tell the caller that the helper screen may need reset.
                seq+=1
                rr=_cdp_result(ws,seq,'Fetch.continueResponse',{'requestId':fid},timeout=1.0)
                if rr.get('__error'):
                    seq+=1;_cdp_result(ws,seq,'Fetch.continueRequest',{'requestId':fid},timeout=1.0)
            try:
                seq+=1;_cdp_result(ws,seq,'Fetch.disable',{},timeout=1.0)
            except Exception:pass
            return {
                'ok':True,
                'response':response,
                'resource_type':'Fetch',
                'target_id':str(target.get('id') or ''),
                'target_url':str(target.get('url') or ''),
                'field_names':list(profile.get('native_field_names') or []),
                'initiator':dict(profile.get('native_initiator') or {}),
                'click':{'x':fx,'y':fy},
                'helper_preserved':helper_preserved,
            }
        return {'ok':False,'timeout':True,'message':f'No live {expected_message} response was seen before the {int(timeout)}s signing window expired. Open the mini game and navigate to the correct pre-fight screen, then BACKGROUND GAME. Do not start a fight manually.'}
    except Exception as exc:
        return {'ok':False,'message':f'Background signing helper failed: {exc}'}
    finally:
        try:
            _cdp_result(ws,1998,'Fetch.disable',{},timeout=.5)
        except Exception:pass
        try:ws.close()
        except Exception:pass
