from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
import websocket

from .config import APP_DIR
from .diagnostic_privacy import private_literals, safe_text, safe_url, sanitize_report
from .learn_center import PORT
from .protocol_config import load_profiles

_SECRET_KEYS = {
    'password','password_hash','login_hash','kong_token','kong_vars','cookie',
    'authorization','clisig','msgsig','token','session','secret'
}
_KEYWORDS = ('fetchWithProgress','clisig','msgsig','startChallenge')


def _targets() -> list[dict[str, Any]]:
    try:
        r=requests.get(f'http://127.0.0.1:{PORT}/json',timeout=1.0)
        d=r.json() if r.ok else []
        return d if isinstance(d,list) else []
    except Exception:
        return []


def _score(t:dict[str,Any], preferred:str='')->int:
    url=str(t.get('url') or '').lower(); typ=str(t.get('type') or '').lower(); tid=str(t.get('id') or '')
    n=10000 if preferred and tid==preferred else 0
    if 'cb-live.synapsegames.com' in url:n+=1200
    elif 'synapsegames.com' in url:n+=1000
    if 'animationthrowdown' in url:n+=850
    if 'throwdown' in url:n+=650
    if typ in {'iframe','worker','service_worker','shared_worker'}:n+=120
    if typ=='page':n+=60
    if 'kongregate.com' in url:n-=250
    return n


def _target(profile:dict[str,Any])->dict[str,Any]|None:
    pref=str(profile.get('browser_click_target_id') or profile.get('browser_target_id') or '')
    rows=[t for t in _targets() if t.get('webSocketDebuggerUrl') and t.get('type') in {'page','iframe','worker','service_worker','shared_worker'}]
    rows.sort(key=lambda t:_score(t,pref),reverse=True)
    return rows[0] if rows else None


def _send(ws,ident:int,method:str,params:dict[str,Any]|None=None):
    ws.send(json.dumps({'id':ident,'method':method,'params':params or {}}))


def _result(ws,ident:int,method:str,params:dict[str,Any]|None=None,timeout:float=3.0)->dict[str,Any]:
    _send(ws,ident,method,params)
    end=time.time()+timeout
    while time.time()<end:
        try:
            ws.settimeout(max(.1,end-time.time())); m=json.loads(ws.recv())
        except Exception:
            continue
        if m.get('id')!=ident: continue
        if m.get('error'): return {'__error':str(m.get('error'))}
        return m.get('result') or {}
    return {'__error':'timeout'}


def _message(req:dict[str,Any])->str:
    try:
        q=parse_qs(urlparse(str(req.get('url') or '')).query,keep_blank_values=True)
        if q.get('message'):return str(q['message'][0] or '')
    except Exception:pass
    raw=str(req.get('postData') or '')
    if raw:
        try:
            q=parse_qs(raw,keep_blank_values=True)
            if q.get('message'):return str(q['message'][0] or '')
        except Exception:pass
        try:
            d=json.loads(raw)
            if isinstance(d,dict):return str(d.get('message') or d.get('action') or '')
        except Exception:pass
    return ''


def _fields(req:dict[str,Any])->dict[str,Any]:
    raw=str(req.get('postData') or '')
    data={}
    if raw:
        try:
            d=json.loads(raw);data=d if isinstance(d,dict) else {}
        except Exception:pass
        if not data:
            try:data={k:(v[0] if isinstance(v,list) and v else v) for k,v in parse_qs(raw,keep_blank_values=True).items()}
            except Exception:data={}
    try:
        query={k:(v[0] if isinstance(v,list) and v else v) for k,v in parse_qs(urlparse(str(req.get('url') or '')).query,keep_blank_values=True).items()}
        data={**query,**data}
    except Exception:pass
    return data


def _safe_field_summary(data:dict[str,Any])->dict[str,Any]:
    out={}
    for k,v in data.items():
        lk=str(k).lower()
        if lk in {'clisig','msgsig'}:
            out[k]={'present':bool(v),'length':len(str(v or ''))}
            continue
        if any(s in lk for s in _SECRET_KEYS):
            out[k]='<redacted>'
            continue
        if lk in {'message','challenge_id','level','auto_battle','client_time','client_version','client_version_full','platform','resolution','unity','os_version','graphics_device_type','graphics_device_version'} or lk.endswith('_version'):
            out[k]=v
        else:
            out[k]='<present>'
    return out


def _stack_frames(init:dict[str,Any])->list[dict[str,Any]]:
    st=init.get('stack') if isinstance(init,dict) else None
    frames=[]
    depth=0
    while isinstance(st,dict) and depth<8:
        for f in st.get('callFrames') or []:
            if not isinstance(f,dict):continue
            frames.append({
                'functionName':str(f.get('functionName') or ''),
                'url':safe_url(f.get('url')),
                'scriptId':str(f.get('scriptId') or ''),
                'lineNumber':f.get('lineNumber'),'columnNumber':f.get('columnNumber')
            })
        st=st.get('parent');depth+=1
    return frames[:24]


def _excerpt(source:str,line:int|None,col:int|None,span:int=900)->str:
    if not source:return ''
    try:
        if line is not None:
            lines=source.splitlines()
            if 0<=int(line)<len(lines):
                ln=lines[int(line)]
                c=max(0,int(col or 0));return ln[max(0,c-span):min(len(ln),c+span)]
    except Exception:pass
    return source[:span*2]


def _scan_scripts(ws, script_meta:dict[str,dict[str,Any]], frame_rows:list[dict[str,Any]], log, sensitive_literals=()) -> dict[str,Any]:
    wanted_ids={str(f.get('scriptId') or '') for f in frame_rows if f.get('scriptId')}
    # Prefer same-site scripts plus exact stack scripts. Keep this bounded.
    for sid,m in script_meta.items():
        u=str(m.get('url') or '').lower()
        if sid in wanted_ids or 'synapsegames.com' in u or 'animationthrowdown' in u:
            wanted_ids.add(sid)
    out={'stack_source':[],'keyword_hits':[]}
    seq=5000
    for sid in list(wanted_ids)[:36]:
        seq+=1; r=_result(ws,seq,'Debugger.getScriptSource',{'scriptId':sid},timeout=2.5)
        src=str(r.get('scriptSource') or '')
        if not src:continue
        meta=script_meta.get(sid) or {}; url=safe_url(meta.get('url'))
        for f in frame_rows:
            if str(f.get('scriptId') or '')==sid:
                ex=safe_text(_excerpt(src,f.get('lineNumber'),f.get('columnNumber')),sensitive_literals)
                if ex:
                    out['stack_source'].append({'functionName':f.get('functionName'),'url':url,'lineNumber':f.get('lineNumber'),'columnNumber':f.get('columnNumber'),'excerpt':ex})
        low=src.lower()
        for kw in _KEYWORDS:
            start=0;hits=0
            while hits<3:
                idx=low.find(kw.lower(),start)
                if idx<0:break
                out['keyword_hits'].append({'keyword':kw,'url':url,'excerpt':safe_text(src[max(0,idx-800):min(len(src),idx+1200)],sensitive_literals)})
                start=idx+len(kw);hits+=1
        if len(out['keyword_hits'])>=18:break
    log(f'Protocol Lab: collected {len(out["stack_source"])} stack-source excerpt(s) and {len(out["keyword_hits"])} keyword hit(s).')
    return out


def run_swole_signature_lab(log=lambda m:None, timeout:float=20.0, *, should_stop=None) -> Path:
    """Capture a real signed startChallenge request without allowing it to reach AT.

    This is intentionally diagnostic rather than a signature-bypass tool. It uses
    the user's already-authenticated mini browser, records safe call-stack/source
    context, then aborts the request at Chromium's request stage. No passwords,
    Kong tokens, cookies, clisig/msgsig values, or login hashes are exported.
    """
    stopped=should_stop or (lambda:False)
    def check_stop():
        if stopped():raise RuntimeError('Protocol Lab stopped. No diagnostic click was repeated.')
    check_stop()
    prof=(load_profiles().get('swole_battle') or {})
    x=prof.get('browser_click_x');y=prof.get('browser_click_y')
    if x is None or y is None:raise RuntimeError('No learned Swole Fight-button position is saved. Use the existing repair capture once only if the UI moved.')
    t=_target(prof)
    if not t:raise RuntimeError('Mini AT browser is not running. Open/reuse it and leave it on the Swole pre-fight screen. Do not start a fight.')
    ws=websocket.create_connection(t['webSocketDebuggerUrl'],timeout=2,suppress_origin=True)
    iws=None
    report={'created_at':time.time(),'mode':'swole_startChallenge_safe_probe','target':{'type':t.get('type'),'url':safe_url(t.get('url'))},'note':'startChallenge was intercepted at request stage and blocked before transmission; sensitive values are redacted.'}
    try:
        check_stop()
        _send(ws,1,'Debugger.enable',{})
        _send(ws,2,'Network.enable',{})
        _send(ws,3,'Fetch.enable',{'patterns':[{'urlPattern':'*cb-live.synapsegames.com/api.php*','requestStage':'Request'}]})
        script_meta={}; network_rows=[]
        # Drain initial scriptParsed events so we can inspect the real caller source later.
        preload_end=time.time()+1.5
        while time.time()<preload_end:
            check_stop()
            try:
                ws.settimeout(.15);m=json.loads(ws.recv())
            except Exception:continue
            if m.get('method')=='Debugger.scriptParsed':
                p=m.get('params') or {};sid=str(p.get('scriptId') or '')
                if sid:script_meta[sid]={'url':str(p.get('url') or '')}
        log('Protocol Lab armed. Commander is clicking the saved Swole Fight control, but the request will be blocked before AT receives it.')
        try:_result(ws,20,'Emulation.setFocusEmulationEnabled',{'enabled':True},timeout=1.0)
        except Exception:pass
        try:_result(ws,21,'Page.setWebLifecycleState',{'state':'active'},timeout=1.0)
        except Exception:pass
        iws=websocket.create_connection(t['webSocketDebuggerUrl'],timeout=2,suppress_origin=True)
        fx=float(x);fy=float(y)
        check_stop()
        _result(iws,30,'Input.dispatchMouseEvent',{'type':'mouseMoved','x':fx,'y':fy,'button':'none'},timeout=1.0)
        check_stop()
        _result(iws,31,'Input.dispatchMouseEvent',{'type':'mousePressed','x':fx,'y':fy,'button':'left','clickCount':1},timeout=1.0)
        # A pressed pointer must be released even when Stop arrives mid-click.
        # Afterward retain interception until the generated request is blocked;
        # disabling Fetch early could resume an already pending request.
        _result(iws,32,'Input.dispatchMouseEvent',{'type':'mouseReleased','x':fx,'y':fy,'button':'left','clickCount':1},timeout=1.0)
        deadline=time.time()+max(8,float(timeout));paused=None;initiator={}
        while time.time()<deadline:
            try:
                ws.settimeout(min(.5,max(.1,deadline-time.time())));m=json.loads(ws.recv())
            except Exception:continue
            meth=m.get('method');p=m.get('params') or {}
            if meth=='Debugger.scriptParsed':
                sid=str(p.get('scriptId') or '')
                if sid:script_meta[sid]={'url':str(p.get('url') or '')}
            elif meth=='Network.requestWillBeSent':
                req=p.get('request') or {}
                if _message(req).lower()=='startchallenge':
                    initiator=p.get('initiator') or {};network_rows.append({'requestId':p.get('requestId'),'initiator':initiator})
            elif meth=='Fetch.requestPaused':
                req=p.get('request') or {}
                if _message(req).lower()!='startchallenge':
                    _send(ws,90,'Fetch.continueRequest',{'requestId':p.get('requestId')});continue
                paused=p;break
        if not paused:raise RuntimeError('No startChallenge request was generated. Leave the mini game on the Swole pre-fight screen and run the lab again.')
        req=paused.get('request') or {};data=_fields(req)
        sensitive_literals=private_literals(data)
        # Abort before transmission. This should preserve Swole energy.
        _send(ws,91,'Fetch.failRequest',{'requestId':paused.get('requestId'),'errorReason':'BlockedByClient'})
        check_stop()
        frames=_stack_frames(initiator)
        report['request']={'method':req.get('method'),'url_host':urlparse(str(req.get('url') or '')).hostname,'field_names':sorted(str(k) for k in data.keys()),'safe_fields':_safe_field_summary(data)}
        report['initiator']={'type':initiator.get('type') if isinstance(initiator,dict) else None,'frames':frames}
        report['source_analysis']=_scan_scripts(ws,script_meta,frames,log,sensitive_literals)
        out=APP_DIR/'protocol_lab_swole_sanitized.json'
        out.write_text(json.dumps(sanitize_report(report,sensitive_literals),indent=2,ensure_ascii=False),encoding='utf-8')
        log(f'Protocol Lab complete: {out}. The start request was blocked before transmission; no Swole energy should have been spent.')
        return out
    finally:
        try:_result(ws,8000,'Fetch.disable',{},timeout=.5)
        except Exception:pass
        try:ws.close()
        except Exception:pass
        if iws is not None:
            try:iws.close()
            except Exception:pass
