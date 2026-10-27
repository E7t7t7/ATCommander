from __future__ import annotations

from dataclasses import dataclass, field, replace, asdict
from datetime import datetime, timezone
import copy, html, math, re, threading, time, unicodedata, json
from pathlib import Path
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse
import requests

SUBREDDIT="AnimationThrowdown"
UA="ATCommander/1.7.26 public CM review lookup"
_CACHE:dict[tuple[str,int],tuple[float,'CommunityConsensus']]={}
_LAST_GOOD:dict[tuple[str,int],tuple[float,'CommunityConsensus']]={}
_INFLIGHT:dict[tuple[str,int],threading.Event]={}
_CACHE_LOCK=threading.Lock()
_REQUEST_LOCK=threading.Lock()
_RETRY_AT=0.0
_NEXT_REQUEST=0.0
_POOL_LOCK=threading.Lock()
_RECENT_POOL:tuple[float,list[dict[str,Any]]]|None=None
_POOL_RETRY_AT=0.0
_POOL_ERROR=""
_COMMENTS_CACHE:dict[str,tuple[float,list[dict[str,Any]]]]={}
_COMMENTS_LOCK=threading.Lock()
_PUBLIC_CACHE_PATH=Path.home()/".atcommander"/"community_reviews.json"
_DISK_LOADED=False


@dataclass
class CommunityRating:
    score:float
    author:str=""
    rating_date:str=""
    title:str=""
    url:str=""
    source:str="Reddit"
    age_days:int|None=None


@dataclass
class CommunityConsensus:
    combo:str
    score:float|None=None
    ratings:list[CommunityRating]=field(default_factory=list)
    confidence:str="None"
    note:str=""
    latest_date:str=""
    error:str=""
    status:str="no_match"  # matched, no_match, unavailable, stale
    checked_posts:int=0
    fetched_at:float=0.0
    cached:bool=False
    lookup_scope:str=""
    partial:bool=False

    @property
    def count(self)->int:return len(self.ratings)


def _clean(s:Any)->str:
    return re.sub(r"\s+"," ",unicodedata.normalize("NFKC",str(s or ""))).strip()


def _author(s:Any)->str:
    value=_clean(s).replace("\\_","_")
    return re.sub(r"^/?u/","",value,flags=re.I).replace(" ","")


_DASHES="-‐‑‒–—−"


def _name_key(combo:str)->str:
    # Match only typographic aliases, never abbreviations or nearby names.
    # The same key also lets an apostrophe/dash variant reuse dated evidence.
    text=_clean(combo).replace("’","'").replace("'","")
    return re.sub(r"\s+"," ",text.translate(str.maketrans({dash:" " for dash in _DASHES}))).strip().casefold()


def _name_pattern(combo:str):
    parts=[]
    text=re.sub(r"\s*[-‐‑‒–—−]\s*","-",_clean(combo)).replace("'","").replace("’","")
    for index,char in enumerate(text):
        if char in _DASHES:parts.append(r"(?:\s*[-‐‑‒–—−]\s*|\s+)")
        elif char.isspace():parts.append(r"(?:\s+|\s*[-‐‑‒–—−]\s*)")
        else:
            parts.append(re.escape(char))
            if char.isalnum() and index+1<len(text) and text[index+1].isalnum():parts.append("['’]?")
    return re.compile(r"(?<!\w)"+"".join(parts)+r"(?!\w)",re.I)


def _search_alias(combo:str)->str:
    text=_clean(combo).replace("’","'")
    return _clean(text.translate(str.maketrans({dash:" " for dash in _DASHES})).replace("'",""))


def _date_from_mmddyyyy(s:str)->datetime|None:
    for fmt in ("%m/%d/%Y","%m/%d/%y"):
        try:return datetime.strptime(s,fmt).replace(tzinfo=timezone.utc)
        except ValueError:pass
    return None


def _date_iso(dt:datetime|None)->str:return dt.strftime("%Y-%m-%d") if dt else ""


def _review_date(title:str,created:datetime|None)->datetime|None:
    if not re.search(r"(?i)\b(?:cm|combo mastery)\s+review",title):return created
    match=re.search(r"\b\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})\b",title)
    declared=_date_from_mmddyyyy(match.group()) if match else None
    if declared is None:
        match=re.search(r"(?i)\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}",title)
        if match:
            try:declared=datetime.strptime(match.group().replace(",",""),"%B %d %Y").replace(tzinfo=timezone.utc)
            except ValueError:pass
    # An explicit daily-review date resolves UTC/local-day differences, but
    # cannot turn a years-old post into a new review.
    return declared if declared and created and abs((created-declared).total_seconds())<=2*86400 else created


def _combo_heading(text:str,match)->bool:
    prefix=text[text.rfind("\n",0,match.start())+1:match.start()]
    stripped=prefix.strip(" \t#>*_0123456789.)")
    # Community previews use "Character - **Combo (skills)**" headings.
    return not stripped.strip("-") or bool(re.fullmatch(r"[\w'’ /.-]{1,45}\s+[-–—]\s*",stripped))


def _segment_from_match(text:str,combo:str,match)->str:
    pattern=_name_pattern(combo)
    tail=text[match.start():match.start()+6000];offset=0
    for index,line in enumerate(tail.splitlines(keepends=True)):
        plain=line.strip();lower=plain.strip("#* _").lower()
        if index and not pattern.search(line):
            markdown=bool(re.match(r"^(?:#{1,6}\s|\*\*[^*]+\*\*)",plain))
            known_detail=lower.startswith(("rating","score","historic","history","previous review"))
            priced=bool(re.match(r"^.{2,90}?\b\d{3,5}\s+(?:gems?|stones?)\b",plain,re.I))
            scored=bool(re.match(r"^[A-Z][\w'’ -]{2,70}\s+[-–—:]\s*\d(?:\.\d+)?\s*(?:/10|[-–—]|$)",plain))
            character_combo=bool(re.match(r"^[\w'’ /.-]{1,45}\s+[-–—]\s+\*{0,2}[\w'’ -]{2,70}\s*\(",plain))
            if ((markdown and not known_detail) or priced or scored or character_combo or lower.startswith(("scale:","scaling chart","submitted by"))):
                return tail[:offset].strip()
        offset+=len(line)
    # A passing reference is confined to its paragraph, rather than allowing
    # some unrelated later paragraph's rating to attach to it.
    return tail.strip() if _combo_heading(text,match) else tail.split("\n\n",1)[0].strip()


def _rating_segments(text:str,combo:str)->list[tuple[str,bool]]:
    matches=list(_name_pattern(combo).finditer(text))
    headings=[match for match in matches if _combo_heading(text,match)]
    chosen=headings or matches[:1]
    return [(_segment_from_match(text,combo,match),bool(headings)) for match in chosen]


def _rating_segment(text:str,combo:str)->str:
    """Compatibility view of the first isolated combo section."""
    sections=_rating_segments(text,combo)
    return sections[0][0] if sections else ""


def _extract_ratings(post:dict[str,Any],combo:str)->list[CommunityRating]:
    data=post.get("data") if isinstance(post.get("data"),dict) else post
    title=str(data.get("title") or "");body=str(data.get("selftext") or data.get("body") or "")
    if not _name_pattern(combo).search(title+"\n"+body):return []
    sections=_rating_segments(body,combo)
    if not sections and _name_pattern(combo).search(title):sections=[(body[:6000],True)]
    if not sections:return []
    author=_author(data.get("author"))
    permalink=str(data.get("permalink") or "")
    url=("https://www.reddit.com"+permalink) if permalink.startswith("/") else str(data.get("url") or "")
    created=None
    try:
        stamp=float(data.get("created_utc") or 0)
        if stamp>0:created=datetime.fromtimestamp(stamp,tz=timezone.utc)
    except (ValueError,TypeError,OverflowError):pass
    if not data.get("is_comment"):created=_review_date(title,created)
    rows=[]
    named_pattern=_name_pattern(combo).pattern+r"\s*[-–—:]\s*([0-9]+(?:\.[0-9]+)?)\s*(?:/\s*10)?\b"
    patterns=(
        named_pattern,
        r"\brating\s*[:=-]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:/\s*10)?\b",
        r"\bscore(?:\s+it)?(?:\s+(?:a|an))?\s*[:=-]?\s*[^0-9\n]{0,12}([0-9]+(?:\.[0-9]+)?)\b",
        r"\b(?:give|giving)\s+(?:this|it)\s+(?:a\s+)?([0-9]+(?:\.[0-9]+)?)\s*(?:/\s*10)?\b",
    )
    # Current prose and historic tables can coexist. Neither suppresses the
    # other, and historic table dates never inherit today's publication date.
    for segment,own_section in sections:
        seg=re.sub(r"\*\*|__","",segment)
        if own_section:
            for m in re.finditer(r"(?im)(\d{1,2}/\d{1,2}/(?:\d{4}|\d{2}))\s+([0-9]+(?:\.[0-9]+)?)\s*\(\s*((?:/?u/)?[^\)]+)\)",seg):
                score=float(m.group(2));dt=_date_from_mmddyyyy(m.group(1))
                if 0<=score<=10 and dt:
                    rows.append(CommunityRating(score,_author(m.group(3)),_date_iso(dt),title,url,"Reddit dated historic rating (original reviewer/date)"))
        for pat in patterns if own_section else (named_pattern,):
            match=re.search(pat,seg,re.I|re.M)
            if match and 0<=float(match.group(1))<=10:
                source="Reddit explicit comment review score" if data.get("is_comment") else "Reddit explicit review score"
                rows.append(CommunityRating(float(match.group(1)),author,_date_iso(created),title,url,source));break
        else:
            # A terminal /10 rating in an actual combo heading/paragraph is
            # explicit evidence. Passing references never use this fallback.
            match=re.search(r"(?<![\d./])([0-9]+(?:\.[0-9]+)?)\s*/\s*10\s*[.!]?\s*$",seg) if own_section else None
            if match and 0<=float(match.group(1))<=10:
                source="Reddit explicit comment combo-section /10 rating" if data.get("is_comment") else "Reddit explicit combo-section /10 rating"
                rows.append(CommunityRating(float(match.group(1)),author,_date_iso(created),title,url,source))
    return rows


def _strip_html(text:str)->str:
    raw=html.unescape(str(text or ""))
    raw=re.sub(r"(?is)<(?:strong|b)\b[^>]*>","**",raw)
    raw=re.sub(r"(?is)</(?:strong|b)\s*>","**",raw)
    raw=re.sub(r"(?is)<br\s*/?>|</li\s*>","\n",raw)
    raw=re.sub(r"(?is)</(?:p|div|h[1-6])\s*>","\n\n",raw)
    raw=re.sub(r"(?is)<[^>]+>","",raw)
    return re.sub(r"[ \t]+"," ",raw).replace(" \n","\n").strip()


class RedditUnavailable(RuntimeError):pass
class RedditRateLimited(RedditUnavailable):pass


class _PublicPosts(list):
    """Public post candidates plus truthful lookup coverage for the report."""
    def __init__(self,posts=(),*,scope="",partial=False,stale=False,error="",fetched_at=0.0):
        super().__init__(posts)
        self.scope=scope;self.partial=partial;self.stale=stale
        self.error=error;self.fetched_at=fetched_at


def _public_get(url:str,params:dict[str,str],accept:str,timeout:float):
    global _RETRY_AT, _NEXT_REQUEST
    with _REQUEST_LOCK:
        if time.time()<_RETRY_AT:raise RedditRateLimited("Reddit rate limit cooldown is active")
        delay=_NEXT_REQUEST-time.monotonic()
        if delay>0:time.sleep(delay)
        try:r=requests.get(url,params=params,headers={"User-Agent":UA,"Accept":accept},timeout=timeout)
        except requests.RequestException as exc:raise RedditUnavailable(f"Reddit request failed ({type(exc).__name__})") from None
        finally:_NEXT_REQUEST=time.monotonic()+8.0
        if r.status_code==429:
            hint=r.headers.get("Retry-After","60")
            try:delay=float(hint)
            except (TypeError,ValueError):
                try:delay=parsedate_to_datetime(hint).timestamp()-time.time()
                except (ValueError,TypeError,OverflowError):delay=60
            if not math.isfinite(delay):delay=60
            _RETRY_AT=time.time()+max(1,delay)
            raise RedditRateLimited("Reddit rate-limited the community lookup; retry after cooldown")
        if r.status_code!=200:raise RedditUnavailable(f"Reddit returned HTTP {r.status_code}")
        return r


def _atom_posts(text:str,combo:str,limit:int)->list[dict[str,Any]]:
    try:root=ET.fromstring(text)
    except ET.ParseError:raise RedditUnavailable("Reddit returned an invalid feed") from None
    if root.tag.rsplit("}",1)[-1] not in {"feed","rss"}:raise RedditUnavailable("Reddit returned a non-feed response")
    out=[]
    entries=[entry for entry in root.iter() if entry.tag.rsplit("}",1)[-1] in {"entry","item"}]
    for entry in entries:
        vals={};author="";url=""
        for child in entry:
            name=child.tag.rsplit("}",1)[-1]
            if name=="author":
                author=_author(next((a.text for a in child if a.tag.rsplit("}",1)[-1]=="name"),child.text))
            elif name=="link":url=child.attrib.get("href") or child.text or url
            elif name in {"title","content","summary","description","published","updated","id","pubDate"}:vals[name]=child.text or ""
        title=_strip_html(vals.get("title",""));body=_strip_html(vals.get("content") or vals.get("summary") or vals.get("description") or "")
        if combo and not _name_pattern(combo).search(title+"\n"+body):continue
        created=vals.get("published") or vals.get("updated") or vals.get("pubDate") or "";stamp=0.0
        try:stamp=datetime.fromisoformat(created.replace("Z","+00:00")).timestamp()
        except ValueError:
            try:stamp=parsedate_to_datetime(created).timestamp()
            except (ValueError,TypeError,OverflowError):pass
        if not url and str(vals.get("id","")).startswith(("http://","https://")):url=vals["id"]
        parsed=urlparse(url);permalink=parsed.path if parsed.hostname in {"reddit.com","www.reddit.com","old.reddit.com"} else ""
        is_comment=str(vals.get("id","")).startswith("t1_") or bool(re.match(r"^/r/[^/]+/comments/[^/]+/[^/]+/[a-z0-9]+/?$",permalink,re.I))
        out.append({"data":{"title":title,"selftext":body,"author":author,"url":url,"permalink":permalink,"created_utc":stamp,"is_comment":is_comment}})
        if len(out)>=limit:break
    return out


def _reddit_rss_search(combo:str,*,limit:int=30,timeout:float=5.0)->list[dict[str,Any]]:
    valid_empty=False;errors=[]
    for query in (f'"{combo}"',_search_alias(combo)):
        for host in ("www.reddit.com","old.reddit.com"):
            try:
                r=_public_get(f"https://{host}/r/{SUBREDDIT}/search.rss",
                    {"q":query,"restrict_sr":"on","sort":"new","t":"all","limit":str(limit)},"application/atom+xml",timeout)
                # Search can find a relevant image submission whose review is
                # in its comments. Retain these candidates for bounded reads.
                posts=_atom_posts(r.text,"",limit)
                if posts:return _PublicPosts(posts,scope="Reddit public RSS search (bounded candidates)",partial=True)
                valid_empty=True;break
            except RedditRateLimited:raise
            except RedditUnavailable as exc:errors.append(str(exc))
    if valid_empty:return _PublicPosts(scope="Reddit public RSS search (bounded candidates)",partial=True)
    raise RedditUnavailable("; ".join(dict.fromkeys(errors)) or "Reddit feed was unavailable")


def _reddit_json_search(combo:str,limit:int,timeout:float)->list[dict[str,Any]]:
    r=_public_get(f"https://www.reddit.com/r/{SUBREDDIT}/search.json",
        {"q":f'"{combo}"',"restrict_sr":"on","sort":"new","t":"all","limit":str(limit),"raw_json":"1"},"application/json",timeout)
    try:obj=r.json()
    except (ValueError,AttributeError):raise RedditUnavailable("Reddit returned a non-JSON search response") from None
    data=obj.get("data") if isinstance(obj,dict) else None
    if not isinstance(data,dict) or not isinstance(data.get("children"),list):raise RedditUnavailable("Reddit returned an invalid search listing")
    posts=[p for p in data["children"] if isinstance(p,dict)]
    return _PublicPosts(posts,scope="Reddit public JSON search (bounded candidates)",partial=True)


def _comment_url(post:dict[str,Any])->str:
    data=post.get("data") if isinstance(post.get("data"),dict) else post
    path=str(data.get("permalink") or urlparse(str(data.get("url") or "")).path)
    match=re.match(r"^/r/"+SUBREDDIT+r"/comments/([a-z0-9]+)/",path,re.I)
    return f"https://www.reddit.com/r/{SUBREDDIT}/comments/{match.group(1)}/.rss" if match else ""


def _has_dated_evidence(posts,combo,max_age_days):
    rows=[row for post in posts for row in _extract_ratings(post,combo)]
    return bool(_consensus_from_ratings(combo,rows,max_age_days,time.time()).ratings)


def _recent_review_threads(posts:list[dict[str,Any]])->list[dict[str,Any]]:
    """Retain bounded review previews whose scores may only be in comments."""
    candidates=[]
    for post in posts:
        data=post.get("data") if isinstance(post.get("data"),dict) else post
        title=_clean(data.get("title"))
        review=bool(re.search(r"\b(?:cm|combo mastery)\s+reviews?\b",title,re.I))
        preview=bool(re.search(r"\b(?:new|upcoming|updated|upgraded)\b.*\b(?:combos?|bge)\b",title,re.I))
        if (review or preview) and _comment_url(post):candidates.append(post)
        if len(candidates)>=3:break
    return candidates


def _cached_review_comments(posts:list[dict[str,Any]],combo:str)->_PublicPosts:
    out=[];stamps=[];now=time.time()
    with _COMMENTS_LOCK:
        for post in _recent_review_threads(posts):
            cached=_COMMENTS_CACHE.get(_comment_url(post))
            if not cached or not 0<=now-cached[0]<1800:continue
            stamps.append(cached[0])
            out.extend(row for row in cached[1] if _name_pattern(combo).search(str(row.get("data",{}).get("selftext",""))))
    return _PublicPosts(out,scope="shared cached public Reddit comment feeds (limited snapshot)",partial=True,
                        fetched_at=max(stamps) if stamps else 0.0)


def _comment_candidates(posts:list[dict[str,Any]],combo:str,timeout:float,max_age_days:int=730)->_PublicPosts:
    """Read at most two candidate threads; a 429 ends further requests."""
    out=[];seen=set();errors=[]
    for post in posts:
        url=_comment_url(post)
        if not url or url in seen:continue
        seen.add(url)
        if len(seen)>2:break
        with _COMMENTS_LOCK:
            cached=_COMMENTS_CACHE.get(url)
            if cached and time.time()-cached[0]<1800:comments=cached[1]
            else:
                try:
                    response=_public_get(url,{"limit":"100"},"application/atom+xml",timeout)
                    comments=_atom_posts(response.text,"",100)
                    if len(_COMMENTS_CACHE)>=100:_COMMENTS_CACHE.pop(min(_COMMENTS_CACHE,key=lambda key:_COMMENTS_CACHE[key][0]))
                    _COMMENTS_CACHE[url]=(time.time(),comments)
                except RedditUnavailable as error:
                    errors.append(str(error))
                    if isinstance(error,RedditRateLimited):break
                    continue
        out.extend(p for p in comments if _name_pattern(combo).search(str(p.get("data",{}).get("selftext",""))))
        if _has_dated_evidence(out,combo,max_age_days):break
    return _PublicPosts(out,scope="bounded public Reddit comment feeds",partial=True,error="; ".join(dict.fromkeys(errors)))


def _reddit_search(combo:str,*,limit:int=30,timeout:float=5.0,max_age_days:int=730)->list[dict[str,Any]]:
    # A shared recent feed avoids one anonymous search request per shop combo.
    # It is public community data only, cached briefly across lookup threads.
    global _RECENT_POOL, _POOL_RETRY_AT, _POOL_ERROR
    pool_error=""
    with _POOL_LOCK:
        if (_RECENT_POOL is None or time.time()-_RECENT_POOL[0]>1800) and time.time()>=_POOL_RETRY_AT:
            try:
                feed=_public_get(f"https://www.reddit.com/r/{SUBREDDIT}/new.rss",{"limit":"100"},"application/atom+xml",timeout)
                _RECENT_POOL=(time.time(),_atom_posts(feed.text,"",100))
                _POOL_ERROR="";_POOL_RETRY_AT=0.0
            except RedditUnavailable as error:
                _POOL_ERROR=str(error);_POOL_RETRY_AT=max(time.time()+60,_RETRY_AT)
        pool_error=_POOL_ERROR
        pooled=_RECENT_POOL[1] if _RECENT_POOL else []
        pool_stamp=_RECENT_POOL[0] if _RECENT_POOL else 0.0
    matched=[post for post in pooled if _name_pattern(combo).search(str(post.get("data",{}).get("title",""))+"\n"+str(post.get("data",{}).get("selftext","")))]
    if matched and _has_dated_evidence(matched,combo,max_age_days):
        return _PublicPosts(matched[:limit],scope="shared recent Reddit RSS feed (limited snapshot)",partial=True,
            stale=time.time()-pool_stamp>1800,error=pool_error,fetched_at=pool_stamp)
    # Reuse fresh comment bodies across shop combos before making another
    # search request. A provider cooldown must not hide already-read evidence.
    cached_comments=_cached_review_comments(pooled,combo)
    if _has_dated_evidence(cached_comments,combo,max_age_days):return cached_comments
    # Public RSS works in environments where anonymous JSON returns 403.
    # A valid empty feed is a real no-match result, not a provider failure.
    search_error="";posts=[];scope="Reddit public RSS search";partial=False
    try:
        found=_reddit_rss_search(combo,limit=limit,timeout=timeout)
        posts=list(found);scope=getattr(found,"scope",scope);partial=getattr(found,"partial",False)
    except RedditRateLimited:raise
    except RedditUnavailable as rss_error:search_error=str(rss_error)
    if search_error or (posts and not _has_dated_evidence(posts,combo,max_age_days)):
        try:
            found=_reddit_json_search(combo,limit,timeout)
            posts.extend(found);scope+=" + JSON search";partial=partial or found.partial
        except RedditUnavailable as json_error:
            if search_error:raise RedditUnavailable(f"{search_error}; {json_error}") from None
            search_error=str(json_error);partial=True
    combined=matched+posts
    if not _has_dated_evidence(combined,combo,max_age_days):
        # Image/BGE previews often name the combo only in their comments.
        # Dropping every unnamed shared-feed post made those ratings invisible.
        # Keep the existing total of at most two public comment-thread reads.
        candidates=combined+_recent_review_threads(pooled)
        comments=_comment_candidates(candidates,combo,timeout,max_age_days) if candidates else _PublicPosts()
        combined.extend(comments)
        if comments.scope:scope+=" + "+comments.scope;partial=True
        if comments.error:search_error="; ".join(filter(None,(search_error,comments.error)))
    # Post identity deduplication precedes reviewer/date deduplication.
    unique={}
    for post in combined:
        data=post.get("data") if isinstance(post.get("data"),dict) else post
        identity=(data.get("permalink") or data.get("url"),data.get("author"),data.get("selftext") or data.get("body"))
        unique.setdefault(identity,post)
    if search_error and not _has_dated_evidence(unique.values(),combo,max_age_days):
        raise RedditUnavailable("Public search/comment coverage was incomplete: "+search_error)
    return _PublicPosts(unique.values(),scope=scope,partial=partial,error=search_error)


def _consensus_from_ratings(name:str,candidates:list[CommunityRating],max_age_days:int,now:float)->CommunityConsensus:
    dedup={};today=datetime.fromtimestamp(now,tz=timezone.utc).date()
    def reviewer(row):
        author=row.author.casefold()
        return author if author and author not in {"[deleted]","[removed]"} else row.url
    for candidate in candidates:
        row=copy.deepcopy(candidate)
        try:date=datetime.strptime(row.rating_date,"%Y-%m-%d").date()
        except (ValueError,TypeError):continue
        age=(today-date).days
        if age<0 or age>max_age_days or not math.isfinite(row.score) or not 0<=row.score<=10:continue
        row.age_days=age;row.author=_author(row.author)
        key=(reviewer(row),row.rating_date,round(row.score,2))
        previous=dedup.get(key)
        if previous is None or ("historic" in previous.source and "historic" not in row.source):dedup[key]=row
    # One reviewer's changing historical scores are context, not independent
    # votes. Their latest dated rating represents them in the consensus.
    latest_by_author={}
    for row in sorted(dedup.values(),key=lambda r:(r.age_days,"historic" in r.source)):
        latest_by_author.setdefault(reviewer(row),row)
    ratings=list(latest_by_author.values())
    if not ratings:
        return CommunityConsensus(name,note="No explicit dated 0–10 rating within the selected age window matched this combo in the checked public candidates. Current AT-data scoring is unchanged.",status="no_match",fetched_at=now)
    weighted=[(r.score,2**(-float(r.age_days)/180.0)) for r in ratings]
    score=sum(s*w for s,w in weighted)/sum(w for _,w in weighted)
    latest=ratings[0];authors={r.author.casefold() for r in ratings if r.author and r.author not in {"[deleted]","[removed]"}}
    confidence="High" if len(authors)>=3 and latest.age_days<=120 else "Medium" if len(authors)>=2 and latest.age_days<=365 else "Low"
    return CommunityConsensus(name,round(score*2)/2,ratings[:12],confidence,
        f"Dated public Reddit evidence from {len(ratings)} distinct reviewer(s); latest rating per reviewer with recency weighting. Historic reposts are deduplicated; qualitative comments are not converted into numeric scores.",
        latest.rating_date,status="matched",fetched_at=now)


def _aggregate(name:str,posts:list[dict[str,Any]],max_age_days:int,now:float)->CommunityConsensus:
    ratings=[row for post in posts for row in _extract_ratings(post,name)]
    out=_consensus_from_ratings(name,ratings,max_age_days,now)
    out.checked_posts=len(posts);out.lookup_scope=getattr(posts,"scope","")
    out.partial=bool(getattr(posts,"partial",False));out.error=getattr(posts,"error","")
    if out.lookup_scope:out.note+=" Lookup: "+out.lookup_scope+"."
    if out.partial:out.note+=" Coverage is limited; this is not an exhaustive community survey."
    if getattr(posts,"stale",False):
        out.cached=True;out.fetched_at=getattr(posts,"fetched_at",now)
        if out.ratings:
            out.status="stale"
            out.note+=" Cached public-feed evidence is shown because refresh failed; it does not adjust the current AT-data score."
    return out


def _recheck_evidence(out:CommunityConsensus,max_age_days:int,now:float)->CommunityConsensus:
    if not out.ratings:return copy.deepcopy(out)
    checked=_consensus_from_ratings(out.combo,out.ratings,max_age_days,now)
    if not checked.ratings:
        return CommunityConsensus(out.combo,status="unavailable",cached=True,error="Cached dated evidence has expired",
            note="Cached reviews fall outside the selected age window. Current AT-data scoring is unchanged.",fetched_at=out.fetched_at)
    return replace(copy.deepcopy(out),score=checked.score,ratings=checked.ratings,
        confidence=checked.confidence,latest_date=checked.latest_date)


def _read_evidence_cache(path, now):
    """Read dated public evidence only, without trusting arbitrary JSON fields."""
    found={}
    try:records=json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError,ValueError):return found
    if not isinstance(records,list):return found
    for record in records[:500]:
        try:
            stamp=float(record["saved_at"])
            if not 0<=now-stamp<=7*86400:continue
            raw=record["consensus"];name=_clean(raw["combo"])
            max_age=max(0,int(record["max_age_days"]))
            if not name:continue
            today=datetime.fromtimestamp(now,tz=timezone.utc).date()
            ratings=[]
            for row in raw["ratings"][:12]:
                value=float(row["score"]);day=datetime.strptime(row["rating_date"],"%Y-%m-%d")
                url=urlparse(row.get("url",""))
                age=(today-day.date()).days
                if not math.isfinite(value) or not 0<=value<=10 or not 0<=age<=max_age:continue
                if url.scheme!="https" or url.hostname not in {"reddit.com","www.reddit.com","old.reddit.com"}:continue
                ratings.append(CommunityRating(value,_author(row.get("author")),row["rating_date"],
                    _clean(row.get("title")),url.geturl(),_clean(row.get("source","Reddit")),age))
            if not ratings:continue
            # Recompute scores, confidence and ages, rather than trusting saved
            # aggregate values or stale author counts.
            result=_consensus_from_ratings(name,ratings,max_age,now)
            if not result.ratings:continue
            result.cached=True;result.fetched_at=stamp
            result.lookup_scope=_clean(raw.get("lookup_scope"))
            result.partial=bool(raw.get("partial",False))
            if result.lookup_scope:result.note+=" Lookup: "+result.lookup_scope+"."
            if result.partial:result.note+=" Coverage is limited; this is not an exhaustive community survey."
            found[(_name_key(name),max_age)]=(stamp,result)
        except (ValueError,TypeError,KeyError,AttributeError):continue
    return found


def _write_evidence_cache(path, evidence, now):
    records=[{"saved_at":stamp,"max_age_days":key[1],"consensus":asdict(result)}
             for key,(stamp,result) in evidence.items() if result.status=="matched" and 0<=now-stamp<=7*86400]
    target=Path(path);temporary=target.with_suffix(".tmp")
    try:
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary.write_text(json.dumps(records[-500:],ensure_ascii=False),encoding="utf-8")
        temporary.replace(target)
    except OSError:pass


def fetch_reddit_consensus(combo:str,*,max_age_days:int=730,cache_seconds:int=1800)->CommunityConsensus:
    global _DISK_LOADED
    name=_clean(combo);key=(_name_key(name),max(0,int(max_age_days)))
    if not name:return CommunityConsensus(name,error="No combo name supplied",status="unavailable")
    with _CACHE_LOCK:
        if not _DISK_LOADED:
            _LAST_GOOD.update(_read_evidence_cache(_PUBLIC_CACHE_PATH,time.time()))
            # A fresh, dated lookup remains fresh across a restart. Older
            # evidence still requires live refresh or an explicit stale label.
            _CACHE.update(_LAST_GOOD)
            _DISK_LOADED=True
    while True:
        with _CACHE_LOCK:
            cached=_CACHE.get(key);now=time.time()
            if cached:
                ttl=min(cache_seconds,45) if cached[1].status in {"unavailable","stale"} else min(cache_seconds,300) if cached[1].status=="no_match" else cache_seconds
                if now-cached[0]<ttl:
                    answer=replace(_recheck_evidence(cached[1],key[1],now),cached=True)
                    if answer.fetched_at and "Cached public lookup checked" not in answer.note:
                        date=datetime.fromtimestamp(answer.fetched_at,tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                        answer.note+=" Cached public lookup checked "+date+"; no new provider request was made."
                    return answer
            waiting=_INFLIGHT.get(key)
            if waiting is None:
                _INFLIGHT[key]=threading.Event();break
        if not waiting.wait(35):return CommunityConsensus(name,error="Reddit lookup is still running",status="unavailable",note="Current AT-data scoring is unchanged while the community lookup finishes.")
    try:
        try:out=_aggregate(name,_reddit_search(name,max_age_days=key[1]),key[1],time.time())
        except Exception as error:
            message=str(error) if isinstance(error,RedditUnavailable) else f"Reddit lookup failed ({type(error).__name__})"
            with _CACHE_LOCK:good=_LAST_GOOD.get(key)
            if good and time.time()-good[0]<=7*86400:
                checked=_recheck_evidence(good[1],key[1],time.time())
                out=replace(checked,status="stale" if checked.ratings else "unavailable",cached=True,error=message,
                    note=("Cached dated Reddit evidence is shown because the live lookup is unavailable. It does not adjust the current AT-data score."
                          if checked.ratings else checked.note))
            else:out=CommunityConsensus(name,error=message,status="unavailable",note="Reddit could not be reached. This is different from finding no matching ratings; current AT-data scoring is unchanged.",fetched_at=time.time())
        with _CACHE_LOCK:
            stamp=time.time();_CACHE[key]=(stamp,out)
            if out.status=="matched":
                _LAST_GOOD[key]=(stamp,copy.deepcopy(out))
                _write_evidence_cache(_PUBLIC_CACHE_PATH,_LAST_GOOD,stamp)
        return copy.deepcopy(out)
    finally:
        with _CACHE_LOCK:
            signal=_INFLIGHT.pop(key,None)
            if signal:signal.set()
