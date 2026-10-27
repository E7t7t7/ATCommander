"""Official card illustrations from the current public Unity bundle manifest.

Only the public asset_bundle_url and asset_bundles_v3 fields are retained.
Downloads use fresh uncredentialed sessions. No browser, account request or
external texture/dependency path is used by this module.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import math
from pathlib import Path
import re
import struct
import time
import unicodedata
from urllib.parse import urlparse
import uuid

import requests

from .card_gallery import ArtAsset,normalize_art,_pillow,MAX_ART_BYTES

CDN_HOSTS=frozenset(("cb-cdn.synapsegames.com","cb-cdn.synapse-games.com"))
MAX_BUNDLE_BYTES=64*1024*1024
MAX_UNPACKED_BYTES=128*1024*1024
MAX_INFO_BYTES=4*1024*1024
MAX_TEXTURE_PIXELS=16_777_216
_PICTURE=re.compile(r"[^/\\:?#%\x00-\x1f\x7f]{1,160}")


def _public_base(value):
    if not isinstance(value,str):return None
    try:
        parts=urlparse(value)
        if (parts.scheme!="https" or parts.hostname not in CDN_HOSTS or parts.port not in (None,443)
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.path!="/assetbundles/v3/"):return None
    except ValueError:return None
    return value


def valid_bundle_url(value):
    if not isinstance(value,str):return None
    try:
        parts=urlparse(value)
        if (parts.scheme!="https" or parts.hostname not in CDN_HOSTS or parts.port not in (None,443)
                or parts.username or parts.password or parts.query or parts.fragment):return None
    except ValueError:return None
    if not re.fullmatch(r"/assetbundles/v3/bundle-[1-9]\d{0,3}_WebGLPlayer_[0-9a-f]{32}\.unity3d",parts.path):return None
    return value


def _id(value):
    if isinstance(value,bool) or not re.fullmatch(r"[1-9]\d{0,3}",str(value)):return None
    return int(value)


def safe_picture_name(value):
    # Public Tk2d sprite names include decimal timestamps, punctuation and
    # spaces. They are lookup keys only; every persisted filename is a digest.
    return (isinstance(value,str) and _PICTURE.fullmatch(value) is not None and ".." not in value
            and not any(unicodedata.category(char) in {"Cc","Cf","Cs","Zl","Zp"} for char in value))


def card_bundle_key(card):
    picture=card.get("picture");bundle=_id(card.get("asset_bundle"))
    return (bundle,picture) if bundle and safe_picture_name(picture) else None


@dataclass(frozen=True)
class BundleManifest:
    urls:dict
    moves:dict

    def resolve(self,bundle,picture):
        seen=set()
        for _ in range(8):
            if bundle in seen:return None
            seen.add(bundle)
            moved=self.moves.get((bundle,picture))
            if moved is None:return self.urls.get(bundle)
            bundle=moved
        return None


def public_bundle_manifest(snapshot):
    """Select public manifest fields; do not retain the supplied account data."""
    bases=[];tables=[]
    def walk(value,depth=0):
        if depth>5 or not isinstance(value,dict):return
        base=_public_base(value.get("asset_bundle_url"))
        if base:bases.append(base)
        table=value.get("asset_bundles_v3")
        if isinstance(table,list) and len(table)<=2048:tables.append(table)
        for key,item in value.items():
            if key!="asset_bundles_v3" and isinstance(item,dict):walk(item,depth+1)
    walk(snapshot)
    if len(set(bases))!=1 or len(tables)!=1:return BundleManifest({}, {})
    base=bases[0];urls={};moves={};conflicts=set();move_conflicts=set()
    for row in tables[0]:
        if not isinstance(row,dict):continue
        bid=_id(row.get("id"));bundle=row.get("bundle")
        if not bid or not isinstance(bundle,dict) or bundle.get("name")!=f"bundle-{bid}":continue
        hashes=bundle.get("hashes")
        digest=hashes.get("WebGLPlayer") if isinstance(hashes,dict) else None
        if not isinstance(digest,str) or not re.fullmatch(r"[0-9a-f]{32}",digest):continue
        url=valid_bundle_url(base+bundle["name"]+"_WebGLPlayer_"+digest+".unity3d")
        if not url:continue
        if bid in urls and urls[bid]!=url:conflicts.add(bid)
        urls[bid]=url
        changed=row.get("sprites_moved",[])
        if not isinstance(changed,list) or len(changed)>10000:continue
        for move in changed:
            if not isinstance(move,dict):continue
            name=move.get("name");old=_id(move.get("previous_asset_bundle_id"));new=_id(move.get("new_asset_bundle_id"))
            if not old or not new or not safe_picture_name(name):continue
            key=(old,name)
            if key in moves and moves[key]!=new:move_conflicts.add(old)
            moves[key]=new
    for bid in conflicts|move_conflicts:urls.pop(bid,None)
    # Conflicting movement information must never select an arbitrary source.
    for key in list(moves):
        if key[0] in move_conflicts:moves[key]=key[0]
    return BundleManifest(urls,moves)


def _download_bundle(url,stopped):
    if not valid_bundle_url(url):raise ValueError("The official bundle URL is invalid.")
    if stopped():raise ValueError("Card artwork loading stopped.")
    with requests.Session() as session:
        session.trust_env=False
        with session.get(url,headers={"User-Agent":"ATCommander/1.7.26 public card art","Accept":"application/octet-stream"},
                         timeout=(5,20),stream=True,allow_redirects=False) as response:
            if response.status_code!=200:raise ValueError("Official card artwork is unavailable.")
            length=response.headers.get("Content-Length","")
            if str(length).isdigit() and int(length)>MAX_BUNDLE_BYTES:raise ValueError("Official bundle exceeds the supported limit.")
            data=bytearray()
            for chunk in response.iter_content(65536):
                if stopped():raise ValueError("Card artwork loading stopped.")
                data.extend(chunk)
                if len(data)>MAX_BUNDLE_BYTES:raise ValueError("Official bundle exceeds the supported limit.")
            return bytes(data)


def validate_bundle(data):
    """Bound declared decompression sizes before invoking the optional reader."""
    try:
        if not isinstance(data,bytes) or not 64<=len(data)<=MAX_BUNDLE_BYTES or not data.startswith(b"UnityFS\0"):raise ValueError
        version=struct.unpack_from(">I",data,8)[0]
        if not 7<=version<=9:raise ValueError
        pos=12
        for _ in range(2):pos=data.index(b"\0",pos,min(pos+128,len(data)))+1
        size,compressed,uncompressed,flags=struct.unpack_from(">QIII",data,pos);pos+=20
        if size!=len(data) or not 20<=uncompressed<=MAX_INFO_BYTES or not 1<=compressed<=MAX_INFO_BYTES:raise ValueError
        pos=(pos+15)//16*16
        start=len(data)-compressed if flags&128 else pos
        packed=data[start:start+compressed]
        if len(packed)!=compressed:raise ValueError
        compression=flags&63
        if compression==0:info=packed
        elif compression in (2,3):
            import lz4.block
            info=lz4.block.decompress(packed,uncompressed_size=uncompressed)
        else:raise ValueError
        if len(info)!=uncompressed:raise ValueError
        count=struct.unpack_from(">I",info,16)[0]
        if not 1<=count<=4096 or 20+count*10+4>len(info):raise ValueError
        unpacked=packed_total=0
        for index in range(count):
            raw,stored,kind=struct.unpack_from(">IIH",info,20+index*10)
            if kind&63 not in (0,2,3):raise ValueError
            unpacked+=raw;packed_total+=stored
        if not 1<=unpacked<=MAX_UNPACKED_BYTES or not 1<=packed_total<=len(data):raise ValueError
        pos=20+count*10
        nodes=struct.unpack_from(">I",info,pos)[0];pos+=4
        if nodes>4096:raise ValueError
        for _ in range(nodes):
            offset,length,_flags=struct.unpack_from(">QQI",info,pos);pos+=20
            if offset+length>unpacked:raise ValueError
            pos=info.index(b"\0",pos,min(pos+1025,len(info)))+1
    except Exception:
        raise ValueError("The official card bundle has an unsupported or oversized format.") from None


def _load_bundle(data):
    validate_bundle(data)
    try:
        import UnityPy
        return UnityPy.load(data)
    except Exception:
        raise ValueError("The official card bundle could not be decoded.") from None


def quad_illustration(atlas,definition,*,premultiplied=False):
    """Follow exact Tk2d vertex UVs, including rotated/flipped atlas packing."""
    Image,_,_,_=_pillow()
    try:
        positions=definition["positions"];uvs=definition["uvs"];texel=definition["texelSize"]
        if definition.get("complexGeometry") or len(positions)!=4 or len(uvs)!=4:raise ValueError
        xsize=float(texel["x"]);ysize=float(texel["y"])
        if not all(math.isfinite(value) and value>0 for value in (xsize,ysize)):raise ValueError
        xy=[(float(p["x"]),float(p["y"])) for p in positions]
        coords=[(float(p["x"]),float(p["y"])) for p in uvs]
        if not all(math.isfinite(v) for pair in xy for v in pair) or not all(math.isfinite(v) and 0<=v<=1 for pair in coords for v in pair):raise ValueError
        bl,br,tl,tr=xy
        if any(abs(a-b)>1e-4 for a,b in ((bl[0],tl[0]),(br[0],tr[0]),(bl[1],br[1]),(tl[1],tr[1]))):raise ValueError
        width=round((br[0]-bl[0])/xsize);height=round((tl[1]-bl[1])/ysize)
        if not 1<=width<=4096 or not 1<=height<=4096 or width*height>8_000_000:raise ValueError
        if not 1<=atlas.width*atlas.height<=MAX_TEXTURE_PIXELS:raise ValueError
        # Serialized float UVs can put an intended integer texel boundary a
        # fraction below that pixel; avoid sampling a neighboring atlas texel.
        def texel_point(value):return float(round(value)) if abs(value-round(value))<0.002 else value
        points=[(texel_point(p[0]*atlas.width),texel_point((1-p[1])*atlas.height)) for p in coords]
        bl,br,tl,tr=points
        if any(abs(br[k]-(bl[k]+tr[k]-tl[k]))>0.02 for k in (0,1)):raise ValueError
        a=(tr[0]-tl[0])/width;b=(bl[0]-tl[0])/height
        d=(tr[1]-tl[1])/width;e=(bl[1]-tl[1])/height
        if abs(a*e-b*d)<0.001:raise ValueError
        source=atlas.convert("RGBA")
        # Pillow filters RGBA through premultiplied alpha internally. Mark an
        # already-premultiplied atlas first so it is not multiplied twice.
        if premultiplied:source=Image.frombytes("RGBa",source.size,source.tobytes())
        return source.transform((width,height),Image.Transform.AFFINE,(a,b,tl[0],d,e,tl[1]),Image.Resampling.BICUBIC).convert("RGBA")
    except Exception:
        raise ValueError("The official illustration geometry is unavailable.") from None


class BundleArtProvider:
    def __init__(self,directory,manifest,*,fetcher=None,loader=None,clock=None,should_stop=None):
        self.directory=Path(directory);self.manifest=manifest
        self.fetcher=fetcher;self.loader=loader or _load_bundle;self.clock=clock or time.time
        self.stopped=should_stop or (lambda:False)
        self._environments=OrderedDict();self._failed={}

    def configure(self,manifest,*,should_stop=None):
        self.manifest=manifest
        if should_stop is not None:self.stopped=should_stop

    def _write(self,target,data):
        if self.stopped():raise ValueError("Card artwork loading stopped.")
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_suffix("."+uuid.uuid4().hex[:8]+".tmp")
        temporary.write_bytes(data);temporary.replace(target)

    def _environment(self,url):
        if self.stopped():raise ValueError("Card artwork loading stopped.")
        if url in self._environments:
            self._environments.move_to_end(url);return self._environments[url]
        target=self.directory/"bundles"/(sha256(url.encode()).hexdigest()+".unity3d")
        env=None
        if target.is_file() and target.stat().st_size<=MAX_BUNDLE_BYTES:
            try:env=self.loader(target.read_bytes())
            except Exception:pass
        if env is None:
            if self.clock()-self._failed.get(url,-1e20)<300:raise ValueError("Official artwork is temporarily unavailable.")
            try:
                data=self.fetcher(url) if self.fetcher else _download_bundle(url,self.stopped)
                if not isinstance(data,bytes) or not 1<=len(data)<=MAX_BUNDLE_BYTES:raise ValueError
                if self.stopped():raise ValueError("Card artwork loading stopped.")
                env=self.loader(data)
                self._write(target,data)
            except Exception:
                self._failed[url]=self.clock();raise ValueError("Official artwork is temporarily unavailable.") from None
        objects={obj.path_id:obj for obj in env.objects}
        if len(objects)>100000:raise ValueError("Official bundle object limit exceeded.")
        entry=(env,objects);self._environments[url]=entry
        while len(self._environments)>2:self._environments.popitem(last=False)
        return entry

    def _illustration(self,entry,picture):
        env,objects=entry;matches=[]
        for obj in env.objects:
            if self.stopped():raise ValueError("Card artwork loading stopped.")
            if obj.type.name!="MonoBehaviour":continue
            try:row=obj.parse_as_dict()
            except Exception:continue
            definitions=row.get("spriteDefinitions",[])
            if not isinstance(definitions,list) or len(definitions)>10000:continue
            for definition in definitions:
                if not isinstance(definition,dict) or definition.get("name")!=picture:continue
                refs=row.get("textures",[]);index=definition.get("materialId")
                if isinstance(index,bool) or not isinstance(index,int) or not isinstance(refs,list) or not 0<=index<len(refs):continue
                ref=refs[index]
                if not isinstance(ref,dict) or ref.get("m_FileID")!=0:continue
                target=objects.get(ref.get("m_PathID"))
                if target is None or target.type.name!="Texture2D":continue
                texture=target.parse_as_object()
                dimensions=(texture.m_Width,texture.m_Height)
                if any(isinstance(value,bool) or not isinstance(value,int) or value<1 for value in dimensions):continue
                if texture.m_Width*texture.m_Height>MAX_TEXTURE_PIXELS:continue
                matches.append((texture,definition,bool(row.get("premultipliedAlpha"))))
        if len(matches)!=1:raise ValueError("The exact official card illustration was not found unambiguously.")
        texture,definition,premultiplied=matches[0]
        image=quad_illustration(texture.image,definition,premultiplied=premultiplied)
        output=BytesIO();image.save(output,format="PNG")
        return normalize_art(output.getvalue())

    def get(self,card):
        key=card_bundle_key(card)
        if key is None or self.stopped():return None
        bundle,picture=key;url=self.manifest.resolve(bundle,picture)
        if not valid_bundle_url(url):return None
        digest=sha256((url+"\0"+picture).encode()).hexdigest()
        target=self.directory/"illustrations"/(digest+".png")
        if self.clock()-self._failed.get(digest,-1e20)<300:return None
        try:
            if target.is_file() and target.stat().st_size<=MAX_ART_BYTES:
                try:return ArtAsset(normalize_art(target.read_bytes()),url)
                except Exception:pass
            data=self._illustration(self._environment(url),picture)
            self._write(target,data)
            return ArtAsset(data,url)
        except Exception:
            self._failed[digest]=self.clock();return None
