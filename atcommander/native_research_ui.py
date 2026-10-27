"""Bounded exact-card selection through the game's ordinary Research UI.

The native ABIs below are supported for explicitly verified public WebGL builds.
Internal pointers never leave the page. This module neither starts research nor
invokes a purchase, playback, reward, or ad-completion callback.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping


PUBLIC_WASM_SHA256 = "f337d37ff520406d2b40257f351e9417c825fbfd66d32dd1023d990b1afbc88c"
CURRENT_PUBLIC_WASM_SHA256 = "e561a945094a514790cbf364a99dbca3d647af701fe9b51902ffe56a7bb6cc6a"
MAX_SCROLLS = 128
MAX_WAIT_SECONDS = 60.0

# Only our own fixed guard codes may cross the diagnostic boundary. A page,
# transport error or unrecognized build cannot supply arbitrary report text.
_SAFE_NATIVE_FAILURES = frozenset({
    "runtime_unavailable", "build_unavailable", "build_changed",
    "metadata_ambiguous", "metadata_unavailable", "metadata_changed",
    "class_ambiguous", "class_unavailable", "popup_ambiguous",
    "popup_unavailable", "wrong_chamber", "selected_cards_unavailable",
    "library_unavailable", "owned_cards_unavailable", "runtime_changed",
    "library_not_eligible", "scroll_unavailable", "card_ambiguous",
    "card_unavailable", "popup_changed", "destination_not_empty",
    "card_not_visible", "selector_unavailable", "selection_unknown",
    "operation_unavailable", "scroll_unknown",
    "tabs_unavailable", "library_refresh_pending",
})
_SAFE_TRANSPORT_FAILURES = frozenset({
    "native_transport_unavailable", "native_result_unavailable",
    "native_operation_failed", "native_evaluation_rejected",
    "native_transport_timeout",
})


class _NativeUIError(RuntimeError):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = (reason if isinstance(reason, str) and reason in _SAFE_TRANSPORT_FAILURES
                       else "native_transport_unavailable")


def _safe_native_failure(state, default="native_ui_unavailable"):
    reason = state.get("reason") if isinstance(state, Mapping) and state.get("ok") is False else None
    return reason if isinstance(reason, str) and reason in _SAFE_NATIVE_FAILURES else default

# Names, field counts and offsets are from copied public metadata and were
# checked against the running public build. Never infer offsets for a new build.
PUBLIC_TYPES = {
    "ResearchPopup": {"offset": 764598, "count": 42, "fields": {
        "_tabs": 112, "_library": 124, "_card1": 156, "_card2": 184,
        "<Index>k__BackingField": 252, "_isAdLocked": 258}},
    "CardView": {"offset": 775107, "count": 107, "fields": {
        "<CardID>k__BackingField": 260, "<CardData>k__BackingField": 388}},
    "CardScrollerViewNew": {"offset": 785084, "count": 60, "fields": {
        "EnableDragging": 42, "_cardViews": 84, "_cardData": 88,
        "_cardStacks": 96, "_containerWidth": 108, "_cardWidth": 120,
        "_scrollX": 128, "_maxScrollX": 132,
        "_needsReload": 185, "_needsSortFilter": 186}},
    "ServiceCardData": {"offset": 617089, "count": 17, "fields": {
        "<CardID>k__BackingField": 8, "<CardUID>k__BackingField": 12,
        "<UnitIndex>k__BackingField": 16}},
    "tk2dUIToggleButtonGroup": {"offset": 944234, "count": 6, "fields": {
        "toggleBtns": 16, "selectedIndex": 24}},
}

# Each layout and ordinary-handler slot is resolved independently from the
# matching public metadata/code-registration tables. Never infer a new build.
PUBLIC_NATIVE_BUILDS = {
    PUBLIC_WASM_SHA256: {"types": PUBLIC_TYPES, "slots": {
        "game_object": 2825, "active": 5538, "transform": 4346,
        "screen_to_local": 37702, "move_card": 33882,
        "start_coroutine": 4514, "drag_begin": 29040, "drag_move": 29041,
        "drag_end": 29042, "update": 29044, "late_update": 29045}},
    CURRENT_PUBLIC_WASM_SHA256: {"types": {
        "ResearchPopup": {"offset": 779523, "count": 42, "fields": {
            "_tabs": 112, "_library": 124, "_card1": 156, "_card2": 184,
            "<Index>k__BackingField": 252, "_isAdLocked": 258}},
        "CardView": {"offset": 790050, "count": 104, "fields": {
            "<CardID>k__BackingField": 248, "<CardData>k__BackingField": 376}},
        "CardScrollerViewNew": {"offset": 799968, "count": 61, "fields": {
            "EnableDragging": 42, "_cardViews": 84, "_cardData": 88,
            "_cardStacks": 96, "_containerWidth": 108, "_cardWidth": 120,
            "_scrollX": 128, "_maxScrollX": 132,
            "_needsReload": 185, "_needsSortFilter": 186}},
        "ServiceCardData": {"offset": 631139, "count": 17, "fields": {
            "<CardID>k__BackingField": 8, "<CardUID>k__BackingField": 12,
            "<UnitIndex>k__BackingField": 16}},
        "tk2dUIToggleButtonGroup": {"offset": 960044, "count": 6, "fields": {
            "toggleBtns": 16, "selectedIndex": 24}},
    }, "slots": {
        "game_object": 3158, "active": 6518, "transform": 5429,
        "screen_to_local": 38652, "move_card": 34812,
        "start_coroutine": 5957, "drag_begin": 29952, "drag_move": 29953,
        "drag_end": 29954, "update": 29956, "late_update": 29957}},
}


_NATIVE_JS = r'''(async()=>{
 const request=__REQUEST__, builds=__BUILDS__;
 const fail=reason=>({ok:false,reason});
 const m=(window.unityInstance||window.gameInstance)?.Module;
 if(!m?.HEAPU32||!m.HEAPU8||!m.HEAPF32||!m.wasmBinary||!globalThis.crypto?.subtle)return fail('runtime_unavailable');
 const binary=m.wasmBinary;
 if(!Number.isInteger(binary.byteLength)||binary.byteLength<8||binary.byteLength>100*1024*1024)return fail('build_unavailable');
 let digest;
 try{digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',binary))).map(n=>n.toString(16).padStart(2,'0')).join('');}
 catch{return fail('build_unavailable');}
 const layout=builds[digest];
 if(!layout)return fail('build_changed');
 const definitions=layout.types,slots=layout.slots;
 const cardIDOffset=definitions.CardView.fields['<CardID>k__BackingField'];
 const cardDataOffset=definitions.CardView.fields['<CardData>k__BackingField'];
 const h=m.HEAPU32,b=m.HEAPU8,t=m.asm?.__indirect_function_table;
 // Both public builds export the standard named allocator ABI (old functions
 // 693/694, current 695/696). The names are checked in each copied binary.
 // These names are verified in the exact hash-guarded binary, not inferred.
 const allocate=typeof m.asm?.malloc==='function'?m.asm.malloc:(typeof m._malloc==='function'?m._malloc.bind(m):null);
 const release=typeof m.asm?.free==='function'?m.asm.free:(typeof m._free==='function'?m._free.bind(m):null);
 if(h.byteLength!==b.byteLength||b.byteLength<4096||b.byteLength>768*1024*1024||!t?.get)return fail('runtime_unavailable');
 const valid=(p,size=512)=>Number.isInteger(p)&&p>=4096&&p%4===0&&p+size<=b.length;
 const matches=(p,name)=>Number.isInteger(p)&&p>=4096&&p+name.length<b.length&&b[p+name.length]===0&&Array.from(name).every((c,i)=>b[p+i]===c.charCodeAt(0));
 const read=p=>valid(p,4)?h[p>>>2]:null;
 const fn=slot=>{const value=t.get(slot);if(typeof value!=='function')throw Error('native unavailable');return value;};
 // Three finite passes at most: metadata header, required classes, active popup.
 let base=null;
 for(let i=0;i<h.length-95;i++)if(h[i]===0xFAB11BAF&&h[i+1]===39&&h[i+2]===380){
  if(base!==null)return fail('metadata_ambiguous');base=i*4;
 }
 if(base===null)return fail('metadata_unavailable');
 const addresses=new Map(),candidates={};
 for(const [name,d] of Object.entries(definitions)){
  const address=base+d.offset;
  if(!matches(address,name))return fail('metadata_changed');
  addresses.set(address,name);candidates[name]=[];
 }
 for(let i=2;i<h.length;i++){
  const value=h[i];
  if(!addresses.has(value))continue;
  const name=addresses.get(value);
  const klass=i*4-8,d=definitions[name];if(!valid(klass))continue;
  const fields=read(klass+64);if(!valid(fields,d.count*20))continue;
  const found=new Set();let rejected=false;
  for(let j=0;j<d.count;j++){
   const entry=fields+j*20,fieldName=read(entry);
   for(const [expected,offset] of Object.entries(d.fields))if(matches(fieldName,expected)){
    if(read(entry+12)!==offset||found.has(expected)){rejected=true;break;}
    found.add(expected);
   }
   if(rejected)break;
  }
  if(!rejected&&found.size===Object.keys(d.fields).length){
   candidates[name].push(klass);
   if(candidates[name].length>1)return fail('class_ambiguous');
  }
 }
 const classes={};
 for(const name of Object.keys(definitions)){
  if(candidates[name].length!==1)return fail('class_unavailable');classes[name]=candidates[name][0];
 }
 const isClass=(p,expected)=>{
  if(!valid(p))return false;
  let klass=read(p);
  for(let depth=0;depth<6&&valid(klass);depth++,klass=read(klass+44))if(klass===expected)return true;
  return false;
 };
 const card=p=>{
  if(!isClass(p,classes.CardView))return null;
  const id=read(p+cardIDOffset);return Number.isInteger(id)&&id>=0&&id<5000000?id:null;
 };
 const list=(p,stride,max,min=1)=>{
  if(!valid(p,20)||!valid(read(p)))return null;
  const array=read(p+8),count=read(p+12);
  if(!Number.isInteger(count)||count<min||count>max||!valid(array,16+stride*count)||!valid(read(array))||read(array+12)<count)return null;
  return {array,count};
 };
 const active=p=>{
  if(!valid(p)||!valid(read(p+8),16))return false;
  try{const go=fn(slots.game_object)(p,0);return !!go&&!!fn(slots.active)(go,0);}catch{return false;}
 };
 const popups=[];
 for(let i=0;i<h.length;i++)if(h[i]===classes.ResearchPopup){
  const p=i*4;
  if(!valid(p)||!valid(read(p+8),16))continue;
  const library=read(p+124);
  if(!isClass(library,classes.CardScrollerViewNew)||card(read(p+156))===null||card(read(p+184))===null)continue;
  if(read(p+252)>3||b[p+258]>1||!active(p))continue;
  popups.push({p,library});if(popups.length>1)return fail('popup_ambiguous');
 }
 if(popups.length!==1)return fail('popup_unavailable');
 const {p,library}=popups[0];
 const selected=()=>[card(read(p+156)),card(read(p+184))];
 const selectedBefore=selected();
 if(read(p+252)!==3||b[p+258]!==1)return fail('wrong_chamber');
 if(selectedBefore.some(id=>id===null))return fail('selected_cards_unavailable');
 const tabs=read(p+112);
 if(!isClass(tabs,classes.tk2dUIToggleButtonGroup))return fail('tabs_unavailable');
 const tabButtons=read(tabs+16),tabIndex=read(tabs+24),tabCount=read(tabButtons+12);
 if(!Number.isInteger(tabCount)||tabCount<1||tabCount>3||!valid(tabButtons,16+tabCount*4)
    ||!Number.isInteger(tabIndex)||tabIndex<0||tabIndex>=tabCount)return fail('tabs_unavailable');
 if(b[library+185]>1||b[library+186]>1)return fail('library_unavailable');
 const libraryReady=b[library+185]===0&&b[library+186]===0;
 const waiting={ok:true,verified_build:true,verified_public_build:true,chamber:3,ad_locked:true,
   selected:selectedBefore,selected_card_ids:selectedBefore,library_ready:false,
   native_tab_index:tabIndex,native_tab_count:tabCount,
   library_card_ids:[],library_ids_truncated:false,stack_count:0,
   source_card_ids:[],source_ids_truncated:false,source_count:0,visible_card_ids:[]};
 // OnTabChange initializes the source list; LateUpdate/Refresh rebuilds the
 // filtered stacks. A pending refresh is never evidence of a missing card.
 if(!libraryReady){
  if(m.HEAPU32!==h||m.HEAPU8!==b)return fail('runtime_changed');
  return request.operation==='inspect'||request.operation==='read'?waiting:fail('library_refresh_pending');
 }
 const views=list(read(library+84),4,64),stacks=list(read(library+96),8,50000,0);
 const sourceData=list(read(library+88),4,50000,0);
 if(!views||!stacks||!sourceData)return fail('library_unavailable');
 const wanted=request.pair?.[request.slot-1],found=[],libraryIDs=[];
 for(let i=0;i<stacks.count;i++){
  const entry=stacks.array+16+i*8,data=read(entry),count=read(entry+4);
  if(!isClass(data,classes.ServiceCardData)||!Number.isInteger(count)||count<1||count>1000000)return fail('owned_cards_unavailable');
  const id=read(data+8);
  if(!Number.isInteger(id)||id<=0||id>=5000000)return fail('owned_cards_unavailable');
  if(libraryIDs.length<2048)libraryIDs.push(id);
  if(id===wanted)found.push({index:i,data});
 }
 const sourceIDs=[],visibleIDs=[];
 for(let i=0;i<sourceData.count;i++){
  const data=read(sourceData.array+16+i*4),id=valid(data)?read(data+8):null;
  if(!isClass(data,classes.ServiceCardData)||!Number.isInteger(id)||id<=0||id>=5000000)return fail('owned_cards_unavailable');
  if(sourceIDs.length<2048)sourceIDs.push(id);
 }
 for(let i=0;i<views.count;i++){
  const view=read(views.array+16+i*4),id=card(view),data=valid(view)?read(view+cardDataOffset):null;
  if(id>0&&isClass(data,classes.ServiceCardData)&&read(data+8)===id&&active(view))visibleIDs.push(id);
 }
 const inspection={ok:true,verified_build:true,verified_public_build:true,chamber:3,ad_locked:true,
                   selected:selectedBefore,selected_card_ids:selectedBefore,
                   library_card_ids:libraryIDs,library_ids_truncated:stacks.count>2048,stack_count:stacks.count,
                   library_ready:true,native_tab_index:tabIndex,native_tab_count:tabCount,
                   source_card_ids:sourceIDs,source_ids_truncated:sourceData.count>2048,source_count:sourceData.count,
                   visible_card_ids:visibleIDs};
 if(m.HEAPU32!==h||m.HEAPU8!==b)return fail('runtime_changed');
 if(request.operation==='inspect')return inspection;
 // The chooser can filter out a card after its ordinary selection settles.
 if(request.operation==='read'&&selectedBefore[request.slot-1]===wanted)return inspection;
 if(b[library+42]!==1)return fail('library_not_eligible');
 const width=m.HEAPF32[(library+120)>>>2],container=m.HEAPF32[(library+108)>>>2];
 const scroll=m.HEAPF32[(library+128)>>>2],maximum=m.HEAPF32[(library+132)>>>2];
 if(![width,container,scroll,maximum].every(Number.isFinite)||width<=0||width>10000||container<=0||container>10000||maximum<0||maximum>10000000||scroll<0||scroll>maximum+.1)return fail('scroll_unavailable');
 const start=Math.max(0,Math.floor(scroll/width));
 const end=Math.min(stacks.count,Math.ceil((scroll+container)/width));
 if(found.length!==1)return fail(found.length?'card_ambiguous':'card_unavailable');
 const target=found[0],visible=target.index>=start&&target.index<end;
 let source=null;
 if(visible){
  const view=read(views.array+16+(target.index%views.count)*4),data=valid(view)?read(view+cardDataOffset):null;
  if(card(view)===wanted&&isClass(data,classes.ServiceCardData)&&read(data+8)===wanted&&active(view))source=view;
 }
 const state={ok:true,verified_build:true,verified_public_build:true,chamber:3,ad_locked:true,
              selected:selectedBefore,card_id:wanted,slot:request.slot,
              selected_card_ids:selectedBefore,exact_visible:!!source,ordered_index:target.index,stack_count:stacks.count,
              scroll_direction:target.index<start?-1:1,
              native_scroll_available:!!allocate&&!!release,library_ready:true,
              native_tab_index:tabIndex,native_tab_count:tabCount};
 if(m.HEAPU32!==h||m.HEAPU8!==b)return fail('runtime_changed');
 if(request.operation==='read')return state;
 const stillValid=()=>m.HEAPU32===h&&m.HEAPU8===b&&read(p)===classes.ResearchPopup&&read(p+252)===3&&b[p+258]===1&&read(p+124)===library&&read(p+112)===tabs&&read(tabs+24)===tabIndex&&b[library+185]===0&&b[library+186]===0&&active(p)&&selected().every((id,i)=>id===selectedBefore[i]);
 if(!stillValid())return fail('popup_changed');
 const expectedFirst=request.slot===2?request.pair[0]:0;
 if(selectedBefore[request.slot-1]!==0||request.slot===2&&selectedBefore[0]!==expectedFirst||request.slot===1&&selectedBefore[1]!==0)return fail('destination_not_empty');
 if(request.operation==='select'){
  if(!source)return fail('card_not_visible');
  // Only ordinary UI selection factory and MonoBehaviour scheduler are allowed.
  let iterator;
  try{iterator=fn(slots.move_card)(p,source,request.slot,0,0);if(!iterator)return fail('selector_unavailable');fn(slots.start_coroutine)(p,iterator,0);}
  catch{return fail('selection_unknown');}
  return {...state,selection_started:true};
 }
 if(request.operation!=='scroll')return fail('operation_unavailable');
 if(!allocate||!release||!m.HEAPF32)return fail('scroll_unavailable');
 const canvas=m.canvas;
 if(!canvas||!Number.isFinite(canvas.width)||!Number.isFinite(canvas.height)||canvas.width<100||canvas.height<100||canvas.width>20000||canvas.height>20000)return fail('scroll_unavailable');
 let memory=0,began=false,ended=false;
 try{
  memory=allocate(48);
  if(!valid(memory,48)||m.HEAPU32!==h||m.HEAPU8!==b)return fail('runtime_changed');
  const f=m.HEAPF32,put=(offset,x,y)=>{f[(memory+offset)>>>2]=x;f[((memory+offset)>>>2)+1]=y;};
  const x=canvas.width*.5,y=canvas.height*.2;
  put(0,x,y);put(8,x+1,y);put(16,0,0);
  const transform=fn(slots.transform)(library,0);if(!transform)return fail('scroll_unavailable');
  fn(slots.screen_to_local)(memory+24,transform,memory,0,0);
  fn(slots.screen_to_local)(memory+36,transform,memory+8,0,0);
  const scale=f[(memory+36)>>>2]-f[(memory+24)>>>2];
  const desired=Math.max(0,Math.min(maximum,target.index*width-(container-width)*.5));
  const delta=(scroll-desired)/scale;
  if(!Number.isFinite(scale)||Math.abs(scale)<.000001||!Number.isFinite(delta)||Math.abs(delta)>2000000)return fail('scroll_unavailable');
  put(8,x+delta,y);put(16,delta,0);
  if(!stillValid())return fail('popup_changed');
  fn(slots.drag_begin)(library,memory,memory+16,0);began=true;
  fn(slots.drag_move)(library,memory+8,memory+16,0);
  // One steady horizontal event ends native inertia through the same handler.
  await new Promise(resolve=>setTimeout(resolve,60));
  if(!stillValid())return fail('scroll_unknown');
  put(16,0,0);fn(slots.drag_move)(library,memory+8,memory+16,0);
  fn(slots.drag_end)(library,memory+8,0);ended=true;
  fn(slots.update)(library,0);fn(slots.late_update)(library,0);
  return {...state,scrolled:true};
 }catch{return fail('scroll_unknown');}
 finally{
  // Release only our ordinary argument buffer; never write a UI field directly.
  if(memory){
   if(began&&!ended&&stillValid()){try{fn(slots.drag_end)(library,memory+8,0);}catch{}}
   try{release(memory);}catch{}
  }
 }
})()'''


def _pair_ids(pair) -> tuple[int, int]:
    if isinstance(pair, str) and re.fullmatch(r"[0-9]{1,7}~[0-9]{1,7}", pair):
        pair = pair.split("~")
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        raise ValueError("An exact pair of card IDs is required.")
    ids = []
    for value in pair:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError("Card IDs must be positive integers.")
        if isinstance(value, str) and not re.fullmatch(r"[0-9]{1,7}", value):
            raise ValueError("Card IDs must be positive integers.")
        number = int(value)
        if not 0 < number < 5_000_000:
            raise ValueError("Card IDs are outside the supported range.")
        ids.append(number)
    if ids[0] == ids[1]:
        raise ValueError("Two different card IDs are required.")
    return tuple(ids)


def _expression(operation: str, pair: tuple[int, int] | None, slot: int) -> str:
    if operation not in {"inspect", "read", "scroll", "select"} or slot not in {1, 2}:
        raise ValueError("Unsupported Research UI operation.")
    request = {"operation": operation}
    if operation != "inspect":
        request.update(pair=list(_pair_ids(pair)), slot=slot)
    return (_NATIVE_JS.replace("__REQUEST__", json.dumps(request))
            .replace("__BUILDS__", json.dumps(PUBLIC_NATIVE_BUILDS)))


def _evaluate(prep, operation, pair, slot):
    ws = prep["target"]["webSocketDebuggerUrl"]
    try:
        result = _cdp_native(ws, _expression(operation, pair, slot))
    except _NativeUIError:
        raise
    except Exception:
        raise _NativeUIError("native_transport_unavailable",
                             "The native Research UI transport did not finish.") from None
    if not isinstance(result, dict):
        raise _NativeUIError("native_result_unavailable",
                             "The native Research UI result is unavailable.")
    if result.get("exceptionDetails"):
        raise _NativeUIError("native_operation_failed",
                             "The native Research UI operation did not finish.")
    remote = result.get("result")
    value = remote.get("value") if isinstance(remote, dict) else None
    if not isinstance(value, dict):
        raise _NativeUIError("native_result_unavailable",
                             "The native Research UI result is unavailable.")
    return value


def _cdp_native(ws_url, expression):
    """One bounded CDP evaluation; longer scans must not inherit a 2s socket."""
    import websocket
    request_id = 867531
    deadline = time.monotonic() + 20.0
    ws = websocket.create_connection(ws_url, timeout=20.0, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": request_id, "method": "Runtime.evaluate",
                            "params": {"expression": expression,
                                       "returnByValue": True, "awaitPromise": True}}))
        for _ in range(1024):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ws.settimeout(remaining)
            message = json.loads(ws.recv())
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if message.get("error") or not isinstance(message.get("result"), dict):
                raise _NativeUIError("native_evaluation_rejected",
                                     "The native Research UI evaluation was rejected.")
            return message["result"]
        raise _NativeUIError("native_transport_timeout",
                             "The native Research UI evaluation timed out.")
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _prepared(prep):
    if not isinstance(prep, Mapping) or prep.get("ok") is not True:
        return False
    target = prep.get("target")
    return (isinstance(target, Mapping)
            and isinstance(target.get("webSocketDebuggerUrl"), str)
            and bool(target["webSocketDebuggerUrl"]))


def _verified_selection(state):
    if (not isinstance(state, Mapping) or state.get("ok") is not True
            or state.get("verified_public_build") is not True
            or state.get("chamber") != 3 or state.get("ad_locked") is not True):
        return None
    ids = state.get("selected_card_ids")
    if (not isinstance(ids, list) or len(ids) != 2
            or any(isinstance(id_, bool) or not isinstance(id_, int)
                   or not 0 <= id_ < 5_000_000 for id_ in ids)):
        return None
    return ids


def read_native_research_selection(prep):
    """Return fresh safe UI identities, without starting any native operation."""
    unavailable = {"ok": False, "verified_public_build": False,
                   "reason": "native_ui_unavailable"}
    if not _prepared(prep):
        return {**unavailable, "reason": "browser_not_prepared"}
    try:
        state = _evaluate(prep, "inspect", None, 1)
    except _NativeUIError as exc:
        return {**unavailable, "reason": exc.reason}
    except Exception:
        return unavailable
    selected = _verified_selection(state)
    if selected is None:
        return {**unavailable, "reason": _safe_native_failure(state)}
    library_ids = state.get("library_card_ids")
    if (not isinstance(library_ids, list) or len(library_ids) > 2048
            or any(isinstance(id_, bool) or not isinstance(id_, int)
                   or not 0 < id_ < 5_000_000 for id_ in library_ids)):
        return unavailable
    count = state.get("stack_count")
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 50_000:
        return unavailable
    details = {}
    for name, limit in (("source_card_ids", 2048), ("visible_card_ids", 64)):
        if name in state:
            value = state[name]
            if (not isinstance(value, list) or len(value) > limit
                    or any(isinstance(id_, bool) or not isinstance(id_, int)
                           or not 0 < id_ < 5_000_000 for id_ in value)):
                return unavailable
            details[name] = list(value)
    if "source_count" in state:
        value = state["source_count"]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 50_000:
            return unavailable
        details["source_count"] = value
        details["source_ids_truncated"] = state.get("source_ids_truncated") is True
    if "library_ready" in state:
        if type(state["library_ready"]) is not bool:
            return unavailable
        details["library_ready"] = state["library_ready"]
    if "native_tab_index" in state or "native_tab_count" in state:
        index, total = state.get("native_tab_index"), state.get("native_tab_count")
        if (type(index) is not int or type(total) is not int
                or not 1 <= total <= 3 or not 0 <= index < total):
            return unavailable
        details.update(native_tab_index=index, native_tab_count=total)
    return {"ok": True, "verified_public_build": True, "chamber": 3,
            "ad_locked": True, "selected_card_ids": list(selected),
            "library_card_ids": list(library_ids), "stack_count": count,
            "library_ids_truncated": state.get("library_ids_truncated") is True,
            **details}


def select_native_research_pair(prep, pair, *, should_stop=None, card1=None,
                                card2=None, wait_seconds=30,
                                max_scrolls=MAX_SCROLLS):
    """Select two owned exact IDs in an already open native TV chamber.

    An uncertain selector or input is never repeated. Selection may be partial
    on failure; the caller must obtain fresh proof before any subsequent action.
    The result authorizes neither research start nor an ad reward.
    """
    stopped = should_stop or (lambda: False)
    changed = False

    def failure(reason, unknown=False, native_reason=None):
        result = {"ok": False, "verified": False, "reason": reason,
                "selection_only": True, "selection_changed": changed,
                "unknown_outcome": unknown, "resource_action_started": False}
        if native_reason is not None:
            result["native_reason"] = native_reason
        return result

    try:
        wanted = _pair_ids(pair)
        canonical_pair = sorted(wanted)
        if card1 is not None or card2 is not None:
            ordered = _pair_ids([card1, card2])
            if sorted(ordered) != canonical_pair:
                return failure("invalid_pair")
            wanted = ordered
        if not _prepared(prep):
            return failure("browser_not_prepared")
        if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, (int, float)) or not 1 <= wait_seconds <= MAX_WAIT_SECONDS:
            return failure("invalid_limit")
        if isinstance(max_scrolls, bool) or not isinstance(max_scrolls, int) or not 0 <= max_scrolls <= MAX_SCROLLS:
            return failure("invalid_limit")
    except (TypeError, ValueError):
        return failure("invalid_pair")

    deadline = time.monotonic() + float(wait_seconds)
    scrolls = 0
    for slot in (1, 2):
        pending = False
        while time.monotonic() < deadline:
            if stopped():
                return failure("stopped", pending)
            try:
                state = _evaluate(prep, "read", wanted, slot)
            except _NativeUIError as exc:
                return failure("ui_read_unavailable", pending, exc.reason)
            except Exception:
                return failure("ui_read_unavailable", pending)
            selected = _verified_selection(state)
            if selected is None:
                return failure(_safe_native_failure(state), pending)
            if any(id_ not in {0, wanted[i]} for i, id_ in enumerate(selected)) or slot == 2 and selected[0] != wanted[0]:
                return failure("selection_changed", pending)
            if selected[slot - 1] == wanted[slot - 1]:
                break
            if state.get("library_ready") is False:
                # Native OnTabChange and filtering settle on ordinary frames.
                # Read again within the existing deadline without any input.
                time.sleep(.25)
                continue
            if pending:
                time.sleep(.25)
                continue
            if stopped():
                return failure("stopped")
            operation = "select" if state.get("exact_visible") is True else "scroll"
            if operation == "scroll" and scrolls >= max_scrolls:
                return failure("scroll_limit")
            try:
                result = _evaluate(prep, operation, wanted, slot)
            except _NativeUIError as exc:
                changed = changed or operation == "select"
                return failure("ui_action_unknown", True, exc.reason)
            except Exception:
                changed = changed or operation == "select"
                return failure("ui_action_unknown", True)
            if result.get("ok") is not True:
                changed = changed or operation == "select"
                return failure("ui_action_unverified", True,
                               _safe_native_failure(result, "native_action_unverified"))
            if operation == "select":
                if result.get("selection_started") is not True:
                    return failure("ui_action_unverified", True)
                changed = True
                pending = True
            else:
                if result.get("scrolled") is not True:
                    return failure("ui_action_unverified", True)
                scrolls += 1
            time.sleep(.25)
        else:
            return failure("selection_timeout", pending)
    if stopped():
        return failure("stopped")
    # Verify both exact IDs together after the second coroutine has settled.
    try:
        final = _evaluate(prep, "read", wanted, 2)
    except _NativeUIError as exc:
        return failure("ui_read_unavailable", True, exc.reason)
    except Exception:
        return failure("ui_read_unavailable", True)
    if _verified_selection(final) != list(wanted):
        return failure("selection_unverified", True)
    return {"ok": True, "verified": True, "verified_public_build": True,
            "chamber": 3, "ad_locked": True, "selected_card_ids": list(wanted),
            "pair": canonical_pair, "card1": wanted[0], "card2": wanted[1], "slot_index": 3,
            "selection_only": True, "selection_changed": changed,
            "resource_action_started": False}
