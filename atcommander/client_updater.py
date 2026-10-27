"""Verify compatible official client releases without game/account requests."""
from __future__ import annotations
import hashlib
import html
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import time
import zlib
import requests
from . import public_client_version as versions
from .public_native import NativeBuild,NativeCompatibilityError,unity_archive

# These relocation-independent structures match the inspected official
# 1.182 and 1.183 clients, including literal/type/method bindings.
EXPECTED={
    "hash":["e0d8d2c24f5c1234700d83103c6e538cd5ae9e6720378a69c79b8972f185ac51",128],
    "version":["4d8456e72c2eb7333f422302f0b720ec003f6a3c0a09a46d73a6a624396d2499",28],
    "unity":["bc9182ba075744a18f22bb70f2dceeb0be6b85a6c9605c846c22b53cb946a78b",71],
    "send":["0ea6a0072f7faa995286f4781b4eab34fc952e522393f4e899cb70b5d340a1ae",5876],
}
ASSET_LIMIT=160*1024*1024

def _registry_path():
    return Path.home()/".atcommander"/"verified_client_builds.json"

def _profile(build):
    match=re.fullmatch(versions._BUILD_PATTERN,build) if isinstance(build,str) else None
    if not match:raise ValueError("Invalid public build")
    parts=tuple(map(int,match[1].split(".")))
    return {"client_version":str(parts[0]*1000000+parts[1]*1000+parts[2]),
            "client_version_full":match[1],"platform":"Web","unity":"Unity6000_3_6"}

def _registry(path):
    path=Path(path)
    if not path.exists():return {"schema":1,"builds":{}}
    if path.stat().st_size>128*1024:raise ValueError("Public verification registry limit")
    value=json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value,dict) or set(value)!={"schema","builds"} or type(value["schema"]) is not int or value["schema"]!=1
            or not isinstance(value["builds"],dict) or len(value["builds"])>32):
        raise ValueError("Invalid public verification registry")
    return value

def _valid_record(build,record):
    if not isinstance(record,dict) or set(record)!={"profile","proof"} or record["profile"]!=_profile(build):
        return False
    proof=record["proof"]
    if not isinstance(proof,dict) or set(proof)!={*EXPECTED,"engine","wasm_sha256","metadata_sha256"}:return False
    if proof["engine"]!="6000.3.6f1" or any(proof[key]!=value for key,value in EXPECTED.items()):return False
    return all(isinstance(proof[key],str) and re.fullmatch("[a-f0-9]{64}",proof[key]) for key in ("wasm_sha256","metadata_sha256"))

def load_verified_profile(build,*,path=None):
    try:
        value=_registry(path or _registry_path())
        record=value["builds"].get(build)
        return dict(record["profile"]) if _valid_record(build,record) else None
    except (OSError,ValueError,TypeError,KeyError,OverflowError):return None

def _atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=path.parent,prefix=".verified-client-",suffix=".tmp",delete=False) as stream:
            temporary=stream.name;json.dump(value,stream,separators=(",",":"),allow_nan=False)
        os.replace(temporary,path);temporary=None
    finally:
        if temporary is not None:
            try:Path(temporary).unlink()
            except OSError:pass

def _cancel(stopped):
    if stopped():raise versions.PublicClientVersionError("Game client check cancelled.",reason="client_update_cancelled")

def _decode_asset(data,magic,stopped):
    if data.startswith(magic):return data
    output=bytearray()
    if data.startswith(b"\x1f\x8b"):
        decoder=zlib.decompressobj(31)
        for at in range(0,len(data),16384):
            _cancel(stopped)
            output.extend(decoder.decompress(data[at:at+16384],ASSET_LIMIT+1-len(output)))
            if len(output)>ASSET_LIMIT:raise NativeCompatibilityError("Public decompression limit")
        if not decoder.eof:raise NativeCompatibilityError("Truncated public compressed asset")
    else:
        try:import brotli
        except ImportError:raise NativeCompatibilityError("Public asset decompressor unavailable") from None
        decoder=brotli.Decompressor()
        for at in range(0,len(data),16384):
            _cancel(stopped);output.extend(decoder.process(data[at:at+16384]))
            if len(output)>ASSET_LIMIT:raise NativeCompatibilityError("Public decompression limit")
        if not decoder.is_finished():raise NativeCompatibilityError("Truncated public compressed asset")
    result=bytes(output)
    if not result.startswith(magic):raise NativeCompatibilityError("Unrecognized public asset")
    return result

def _download(session,url,label,magic,emit,stopped):
    _cancel(stopped);emit(f"Downloading {label}…")
    with session.get(url,stream=True,timeout=25,allow_redirects=False) as response:
        if response.status_code!=200:raise NativeCompatibilityError("Official client download unavailable")
        chunks=bytearray();last=0
        for block in response.iter_content(65536):
            _cancel(stopped);chunks.extend(block)
            if len(chunks)>ASSET_LIMIT:raise NativeCompatibilityError("Public asset limit exceeded")
            if len(chunks)-last>=4*1024*1024:
                emit(f"Downloading {label} · {len(chunks)//1048576} MB");last=len(chunks)
    return _decode_asset(bytes(chunks),magic,stopped)

def verify_assets(wasm,data,*,should_stop=lambda:False):
    from .combat_signing import _MESSAGE_FORMAT,_CLIENT_FORMAT
    files=unity_archive(data)
    metadata=bytes(files["Il2CppData/Metadata/global-metadata.dat"])
    bundle=files["data.unity3d"]
    if bytes(bundle[:8])!=b"UnityFS\0":raise NativeCompatibilityError("Unknown public Unity bundle")
    at=12
    first=bytes(bundle[at:at+256]).find(b"\0")
    if first<0:raise NativeCompatibilityError("Invalid public Unity version")
    at+=first+1
    second=bytes(bundle[at:at+128]).find(b"\0")
    engine=bytes(bundle[at:at+second]).decode("ascii") if second>=0 else ""
    if engine!="6000.3.6f1":raise NativeCompatibilityError("The public Unity engine changed")
    native=NativeBuild(wasm,metadata,should_stop)
    actual=native.signer_fingerprints(_MESSAGE_FORMAT.encode(),_CLIENT_FORMAT.encode())
    for key in ("hash","version","unity"):
        if list(actual[key])!=EXPECTED[key]:raise NativeCompatibilityError("The public signing helpers changed")
    if sum(list(value)==EXPECTED["send"] for value in actual["send"])!=1:
        raise NativeCompatibilityError("The public request signer changed")
    return {**EXPECTED,"engine":engine,"wasm_sha256":hashlib.sha256(wasm).hexdigest(),
            "metadata_sha256":hashlib.sha256(metadata).hexdigest()}

def update_public_client_profile(*,progress=None,should_stop=None,cache_path=None,verified_path=None):
    emit=progress or (lambda message:None);stopped=should_stop or (lambda:False)
    try:
        _cancel(stopped);emit("Checking the official game client…")
        with requests.Session() as session:
            session.trust_env=False
            embed=html.unescape(versions._read_public_text(session,versions.PUBLIC_EMBED_URL,versions._MAX_EMBED_BYTES,20)).replace("\\/","/")
            matches={(match[1],match[2]) for match in versions._INDEX_RE.finditer(embed)}
            if len(matches)!=1:raise NativeCompatibilityError("The current public build is ambiguous")
            build,version=matches.pop();profile=_profile(build)
            base=f"https://cb-cdn.synapsegames.com/webgl/{build}/"
            index=versions._read_public_text(session,base+"index.html",versions._MAX_INDEX_BYTES,20)
            if set(versions._PRODUCT_RE.findall(index))!={version}:raise NativeCompatibilityError("Public product versions disagree")
            _cancel(stopped)
            known=versions._VERIFIED_PROFILES.get(build) or load_verified_profile(build,path=verified_path)
            if known!=profile:
                # Only the official same-build asset paths declared by the
                # launcher are used; no remote script is executed.
                if (not re.search(r"\bbuildUrl\s*=\s*[\"']Build[\"']",index) or not all(re.search(r"\b"+key+r"\s*:\s*buildUrl\s*\+\s*[\"']/"+filename+r"[\"']",index)
                           for key,filename in (("codeUrl",r"WebGL\.wasm\.unityweb"),("dataUrl",r"WebGL\.data\.unityweb")))):
                    raise NativeCompatibilityError("The public asset layout changed")
                wasm=_download(session,base+"Build/WebGL.wasm.unityweb","game code",b"\0asm",emit,stopped)
                data=_download(session,base+"Build/WebGL.data.unityweb","game metadata",b"UnityWebData1.0\0",emit,stopped)
                emit("Verifying request signing and client compatibility…")
                proof=verify_assets(wasm,data,should_stop=stopped);_cancel(stopped)
                path=Path(verified_path or _registry_path())
                with versions._LOCK:
                    registry=_registry(path);registry["builds"][build]={"profile":profile,"proof":proof}
                    while len(registry["builds"])>32:registry["builds"].pop(next(iter(registry["builds"])))
                    _cancel(stopped);_atomic_json(path,registry)
            _cancel(stopped)
            # A custom test registry is checked directly; the normal registry
            # is also checked by every subsequent combat profile resolution.
            value={"schema":1,"build":build,"checked_at":time.time(),"profile":profile}
            with versions._LOCK:_atomic_json(cache_path or versions._default_cache_path(),value)
        emit(f"Game client {version} is ready.")
        return {"build":build,"profile":profile,"message":f"Game client {version} checked and ready. You can resume combat."}
    except versions.PublicClientVersionError:raise
    except Exception:
        if stopped():raise versions.PublicClientVersionError("Game client check cancelled.",reason="client_update_cancelled") from None
        raise versions.PublicClientVersionError(
            "This client could not be verified automatically. The existing profile was preserved; combat remains paused for an incompatible build.",
            reason="client_update_verification_failed") from None
