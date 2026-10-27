"""Portable illustrated card maps using exact supplied stats and public game art.

No account request, cookie, API credential or browser is used for artwork reads.
Pillow is optional: SVG/HTML retain readable stat panels when art cannot decode.
PNG/JPEG exports need the optional installed Pillow runtime.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from hashlib import sha256
from html import escape
from io import BytesIO
from pathlib import Path
import re
import time
from urllib.parse import urlparse
import uuid

import requests

from .card_presentation import summary

OFFICIAL_HOSTS=frozenset(("cb-cdn.synapsegames.com","cb-cdn.synapse-games.com",
                          "cb-live.synapsegames.com","cb-live.synapse-games.com"))
MAX_ART_BYTES=6*1024*1024
FORMATS=("svg","html","png","jpeg")
LAYOUTS=("grid","compact","list")
COLORS={"Common":"#81929d","Rare":"#52b561","Epic":"#509de7",
        "Legendary":"#aa6ce4","Mythic":"#eab947","Unknown":"#5d6e88"}


def valid_art_url(value):
    if not isinstance(value,str) or len(value)>1000:return None
    try:
        parsed=urlparse(value)
        if (parsed.scheme!="https" or parsed.hostname not in OFFICIAL_HOSTS or parsed.username
                or parsed.password or parsed.port not in (None,443) or parsed.query or parsed.fragment):return None
    except ValueError:return None
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+\.(?:png|jpe?g|webp)",parsed.path,re.I):return None
    if ".." in parsed.path.split("/"):return None
    return parsed.geturl()


def official_art_url(card,base_url=None):
    """Use only explicit official URLs or a caller-verified picture base path."""
    for key in ("picture_url","art_url","image_url","picture","image"):
        value=card.get(key)
        found=valid_art_url(value)
        if found:return found
    picture=card.get("picture")
    if (base_url and isinstance(picture,str) and re.fullmatch(r"[A-Za-z0-9_-]{1,160}",picture)):
        return valid_art_url(str(base_url).rstrip("/")+"/"+picture+".png")
    return None


def _pillow():
    try:
        from PIL import Image,ImageDraw,ImageFont,ImageOps
    except ImportError:
        raise ValueError("PNG/JPEG and decoded artwork require optional Pillow. Choose SVG or HTML for a stat map.") from None
    return Image,ImageDraw,ImageFont,ImageOps


def normalize_art(data):
    if not isinstance(data,bytes) or not 1<=len(data)<=MAX_ART_BYTES:raise ValueError("Invalid artwork data.")
    Image,_,_,ImageOps=_pillow()
    try:
        with Image.open(BytesIO(data)) as original:
            if original.format not in {"PNG","JPEG","WEBP"} or not 1<=original.width*original.height<=8_000_000:
                raise ValueError("Invalid artwork data.")
            original.load()
            image=ImageOps.exif_transpose(original).convert("RGBA")
            image.info.clear()
            image.thumbnail((1200,1200),Image.Resampling.LANCZOS)
            output=BytesIO();image.save(output,format="PNG")
            result=output.getvalue()
            if len(result)>MAX_ART_BYTES:raise ValueError("Invalid artwork data.")
            return result
    except Exception:
        raise ValueError("The official artwork could not be decoded safely.") from None


def _download_public(url):
    # A fresh uncredentialed session avoids browser cookies, netrc credentials,
    # application authentication and environment proxy/header inheritance.
    with requests.Session() as session:
        session.trust_env=False
        with session.get(url,headers={"User-Agent":"ATCommander/1.7.26 public card art",
                                      "Accept":"image/png,image/jpeg,image/webp"},
                         stream=True,timeout=(5,12),allow_redirects=False) as response:
            if response.status_code!=200:raise ValueError("Official artwork is unavailable.")
            content=bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content)>MAX_ART_BYTES:raise ValueError("Official artwork exceeds the image limit.")
            return bytes(content)


@dataclass(frozen=True)
class ArtAsset:
    png:bytes
    source:str


class OfficialArtCache:
    def __init__(self,directory,*,base_url=None,fetcher=None,clock=None,bundle_provider=None):
        self.directory=Path(directory);self.base_url=base_url
        self.bundle_provider=bundle_provider
        self.fetcher=fetcher or _download_public;self.clock=clock or time.time
        self._failed={};self._memory={}

    def get(self,card):
        url=official_art_url(card,self.base_url)
        if not url:
            if self.bundle_provider is None:return None
            try:return self.bundle_provider.get(card)
            except Exception:return None
        if url in self._memory:return self._memory[url]
        if self.clock()-self._failed.get(url,-1e20)<300:return None
        target=self.directory/(sha256(url.encode("utf-8")).hexdigest()+".png")
        try:
            data=None
            if target.is_file() and target.stat().st_size<=MAX_ART_BYTES:
                try:data=normalize_art(target.read_bytes())
                except (OSError,ValueError):pass
            if data is None:
                data=normalize_art(self.fetcher(url))
                self.directory.mkdir(parents=True,exist_ok=True)
                temporary=target.with_suffix("."+uuid.uuid4().hex[:8]+".tmp")
                temporary.write_bytes(data);temporary.replace(target)
            asset=ArtAsset(data,url);self._memory[url]=asset
            return asset
        except Exception:
            self._failed[url]=self.clock()
            return None


def public_render_row(card):
    raw=summary(card)
    # Accept already-public summaries as well as exact raw level definitions.
    if card.get("rarity") in COLORS:raw["rarity"]=card["rarity"]
    if card.get("fusion") in {"Single","Dual","Quad","Unknown"}:raw["fusion"]=card["fusion"]
    if isinstance(card.get("skills"),list) and all(isinstance(value,str) for value in card["skills"]):raw["skills"]=list(card["skills"])
    return {key:raw[key] for key in ("name","card_id","rarity","fusion","level","max_level","attack","health","power",
                                    "skills","traits","attack_multiplier","health_multiplier","stats_status") if key in raw}


def _lines(card):
    row=public_render_row(card)
    lines=[str(row.get("name")or"Unknown"),f'Card {row.get("card_id","")} · {row.get("rarity","Unknown")}',
           f'Level {row.get("level","—")} · {row.get("fusion","Unknown")}',
           f'ATK {row.get("attack","—")}   HP {row.get("health","—")}   Power {row.get("power","—")}']
    if row.get("traits"):lines.append(" / ".join(map(str,row["traits"])))
    lines.extend(map(str,row.get("skills",[])[:5]))
    if "attack_multiplier" in row or "health_multiplier" in row:
        lines.append(f'Combo scaling: ATK {row.get("attack_multiplier","—")} / HP {row.get("health_multiplier","—")}')
    if row.get("stats_status"):lines.append(str(row["stats_status"]))
    elif "attack" not in row and "attack_multiplier" in row:lines.append("Combo values depend on the selected input cards.")
    return lines


def _geometry(count,layout,columns):
    if not 1<=count<=100:raise ValueError("Card maps support 1–100 cards. Narrow the filters first.")
    if layout not in LAYOUTS:raise ValueError("Choose grid, compact or list card layout.")
    if isinstance(columns,bool) or not isinstance(columns,int) or not 1<=columns<=8:raise ValueError("Columns must be between 1 and 8.")
    if layout=="list":width,height,columns=1000,185,1
    elif layout=="compact":width,height=280,410
    else:width,height=340,500
    columns=min(columns,count);total_width=columns*width+24
    total_height=((count+columns-1)//columns)*height+76
    placements=[]
    for index in range(count):
        x,y=12+index%columns*width,54+index//columns*height
        if layout=="list":art=(x+12,y+12,150,154);text=(x+180,y+28);line_height=17;size=13
        elif layout=="compact":art=(x+12,y+12,width-36,144);text=(x+14,y+185);line_height=19;size=12
        else:art=(x+12,y+12,width-36,200);text=(x+16,y+244);line_height=21;size=13
        placements.append({"rect":(x,y,width-12,height-12),"art":art,"text":text,
                           "line_height":line_height,"font_size":size})
    return total_width,total_height,placements


def _assets(cards,cache,stopped):
    out=[]
    for card in cards:
        if stopped():raise ValueError("Card map export stopped before completion.")
        out.append(cache.get(card) if cache else None)
    return out


def gallery_svg(cards,*,assets=None,layout="grid",columns=3,title="Card map"):
    width,height,places=_geometry(len(cards),layout,columns)
    art=list(assets or [None]*len(cards))
    if len(art)!=len(cards):raise ValueError("Artwork must correspond exactly to the selected cards.")
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
         '<rect width="100%" height="100%" fill="#0b1020"/>',
         f'<text x="18" y="31" font-family="Segoe UI,Arial,sans-serif" font-size="20" fill="#edf2ff">{escape(str(title))}</text>']
    for index,(card,place,asset) in enumerate(zip(cards,places,art)):
        row=public_render_row(card);lines=_lines(card);x,y,w,h=place["rect"]
        color=COLORS.get(row.get("rarity"),COLORS["Unknown"])
        out.append(f'<g><title>{escape(" | ".join(lines))}</title><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="13" fill="#182238" stroke="{color}" stroke-width="3"/>')
        ax,ay,aw,ah=place["art"]
        out.append(f'<rect x="{ax}" y="{ay}" width="{aw}" height="{ah}" rx="7" fill="#101a2b"/>')
        if asset:
            data=base64.b64encode(asset.png).decode("ascii")
            out.append(f'<image x="{ax}" y="{ay}" width="{aw}" height="{ah}" preserveAspectRatio="xMidYMid meet" href="data:image/png;base64,{data}"/>')
        else:
            out.append(f'<text x="{ax+8}" y="{ay+ah/2}" font-family="Segoe UI,Arial,sans-serif" font-size="11" fill="#95a7c0">Official artwork unavailable</text>')
        tx,ty=place["text"]
        for line_index,line in enumerate(lines[:11]):
            if layout=="list":lx,ly=tx+(line_index//7)*395,ty+(line_index%7)*place["line_height"]
            else:lx,ly=tx,ty+line_index*place["line_height"]
            size=place["font_size"]+2 if line_index==0 else place["font_size"]
            limit=48 if layout=="grid" else 38 if layout=="compact" else 58
            shown=line if len(line)<=limit else line[:limit-1]+"…"
            out.append(f'<text x="{lx}" y="{ly}" font-family="Segoe UI,Arial,sans-serif" font-size="{size}" fill="#edf2ff">{escape(shown)}</text>')
        out.append('</g>')
    out.append('</svg>')
    return "\n".join(out)


def _font(ImageFont,size):
    for path in (Path("C:/Windows/Fonts/segoeui.ttf"),Path("C:/Windows/Fonts/arial.ttf")):
        if path.is_file():
            try:return ImageFont.truetype(str(path),size)
            except OSError:pass
    return ImageFont.load_default(size=size)


def _raster(cards,assets,layout,columns,title):
    Image,ImageDraw,ImageFont,ImageOps=_pillow()
    width,height,places=_geometry(len(cards),layout,columns)
    if width*height>40_000_000:raise ValueError("Raster map is too large. Use SVG/HTML, fewer cards or more columns.")
    image=Image.new("RGB",(width,height),"#0b1020");draw=ImageDraw.Draw(image)
    draw.text((18,12),str(title),font=_font(ImageFont,20),fill="#edf2ff")
    for card,place,asset in zip(cards,places,assets):
        row=public_render_row(card);lines=_lines(card);x,y,w,h=place["rect"]
        draw.rounded_rectangle((x,y,x+w,y+h),13,fill="#182238",outline=COLORS.get(row.get("rarity"),COLORS["Unknown"]),width=3)
        ax,ay,aw,ah=place["art"]
        draw.rounded_rectangle((ax,ay,ax+aw,ay+ah),7,fill="#101a2b")
        if asset:
            with Image.open(BytesIO(asset.png)) as original:
                thumb=ImageOps.contain(original.convert("RGBA"),(aw,ah),Image.Resampling.LANCZOS)
                image.paste(thumb,(ax+(aw-thumb.width)//2,ay+(ah-thumb.height)//2),thumb)
        else:draw.text((ax+8,ay+ah//2),"Official artwork unavailable",font=_font(ImageFont,11),fill="#95a7c0")
        tx,ty=place["text"]
        for index,line in enumerate(lines[:11]):
            if layout=="list":lx,ly=tx+(index//7)*395,ty+(index%7)*place["line_height"]
            else:lx,ly=tx,ty+index*place["line_height"]
            limit=48 if layout=="grid" else 38 if layout=="compact" else 58
            shown=line if len(line)<=limit else line[:limit-1]+"…"
            draw.text((lx,ly-place["font_size"]),shown,font=_font(ImageFont,place["font_size"]+2 if index==0 else place["font_size"]),fill="#edf2ff")
    return image


def export_card_map(cards,directory,*,image_type="svg",layout="grid",columns=3,title="Card map",art_cache=None,should_stop=None):
    """Export self-contained art/stats, without modifying supplied card levels."""
    if image_type=="jpg":image_type="jpeg"
    if image_type not in FORMATS:raise ValueError("Choose SVG, HTML, PNG or JPEG card export.")
    _geometry(len(cards),layout,columns)
    stopped=should_stop or (lambda:False)
    assets=_assets(cards,art_cache,stopped)
    target_dir=Path(directory);target_dir.mkdir(parents=True,exist_ok=True)
    target=target_dir/("card-map-"+uuid.uuid4().hex[:12]+"."+image_type)
    if stopped():raise ValueError("Card map export stopped before completion.")
    if image_type in {"svg","html"}:
        source=gallery_svg(cards,assets=assets,layout=layout,columns=columns,title=title)
        if image_type=="html":
            source='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'"><title>'+escape(str(title))+'</title><style>body{margin:0;background:#0b1020;color:#edf2ff}svg{display:block;max-width:100%;height:auto}</style></head><body>'+source+'</body></html>'
        target.write_text(source,encoding="utf-8")
    else:
        image=_raster(cards,assets,layout,columns,title)
        image.save(target,format="PNG" if image_type=="png" else "JPEG",**({"quality":92} if image_type=="jpeg" else {}))
    return {"path":str(target.resolve()),"type":{"svg":"image/svg+xml","html":"text/html","png":"image/png","jpeg":"image/jpeg"}[image_type],
            "label":"Illustrated card map","cards":len(cards),"illustrated":sum(asset is not None for asset in assets),
            "missing_artwork":sum(asset is None for asset in assets)}
