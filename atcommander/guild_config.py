"""Local guild preferences and portable roster/defense reports.

These helpers make no game or cloud request. Callers supply authoritative public
roster/deck rows and apply the usual report sanitization before writing a file.
"""
from __future__ import annotations

from copy import deepcopy
import csv
from datetime import datetime,timezone
from html import escape
import json
from pathlib import Path
import uuid

COLUMNS=("name","role","last_seen","sfc_rating","rumble_score","rumble_wins","rumble_losses",
         "siege_attacks","cards","defense_island","deck","notes")
DEFAULT_COLUMNS=("name","role","last_seen","sfc_rating","cards","defense_island","deck","notes")


def _integer(value,minimum=1,maximum=99999999):
    if isinstance(value,bool):raise ValueError("A numeric guild/assignment value is invalid.")
    try:number=int(str(value))
    except (TypeError,ValueError):raise ValueError("A numeric guild/assignment value is invalid.") from None
    if not minimum<=number<=maximum:raise ValueError("A numeric guild/assignment value is out of range.")
    return number


def _text(value,maximum=5000):
    if not isinstance(value,str) or len(value)>maximum:raise ValueError("A guild preference contains invalid text.")
    return value.strip()


def _roster_names(roster):
    counts={}
    for member in roster:
        if isinstance(member,dict) and member.get("name"):
            key=str(member["name"]).strip().casefold();counts[key]=counts.get(key,0)+1
    return counts


def validate_assignments(value,roster=None):
    if isinstance(value,str):
        try:value=json.loads(value or "[]")
        except ValueError:raise ValueError("Defense assignments must be a JSON list.") from None
    if not isinstance(value,list) or len(value)>100:raise ValueError("Defense assignments must contain at most 100 rows.")
    names=_roster_names(roster or []);seen=set();out=[]
    for row in value:
        if not isinstance(row,dict) or set(row)-{"member","island","deck","notes"}:
            raise ValueError("Defense assignment fields are member, island, deck and notes.")
        member=_text(row.get("member"),100)
        if not member or member.casefold() in seen:raise ValueError("Each defense assignment needs one unique exact member name.")
        if roster is not None and names.get(member.casefold())!=1:
            raise ValueError("A defense assignment does not match one unambiguous current roster member.")
        seen.add(member.casefold())
        parsed={"member":member,"island":_integer(row.get("island"),1,100)}
        if row.get("deck") not in (None,""):parsed["deck"]=_integer(row["deck"],1,999)
        if "notes" in row:parsed["notes"]=_text(row["notes"],1000)
        out.append(parsed)
    return out


def get_guild_preferences(store,guild_id):
    key=str(_integer(guild_id));data=store.read()
    by_id=data.get("guild_preferences_by_id",{})
    if not isinstance(by_id,dict):by_id={}
    saved=by_id.get(key,{})
    if not isinstance(saved,dict):saved={}
    try:assignments=validate_assignments(saved.get("assignments",[]))
    except ValueError:assignments=[]
    columns=saved.get("columns",DEFAULT_COLUMNS)
    if not isinstance(columns,(list,tuple)) or not columns or any(not isinstance(key,str) or key not in COLUMNS for key in columns) or len(set(columns))!=len(columns):
        columns=DEFAULT_COLUMNS
    return {"guild_id":int(key),"name":saved.get("name","") if isinstance(saved.get("name",""),str) else "",
            "defense_plan":saved.get("defense_plan","") if isinstance(saved.get("defense_plan",""),str) else "",
            "report_format":saved.get("report_format") if isinstance(saved.get("report_format"),str) and saved.get("report_format") in {"html","csv","json"} else "html",
            "columns":list(columns),"assignments":assignments}


def save_guild_preferences(store,guild_id,changes,*,roster=None):
    """Preserve omitted fields and isolate preferences per actual guild ID."""
    if not isinstance(changes,dict):raise ValueError("Guild preference changes must be a mapping.")
    allowed={"name","defense_plan","report_format","columns","assignments"}
    if set(changes)-allowed:raise ValueError("An unknown guild preference was supplied.")
    current=get_guild_preferences(store,guild_id)
    supplied={key:value for key,value in changes.items() if value is not None}
    if not supplied:return current
    for key in ("name","defense_plan"):
        if key in supplied:current[key]=_text(supplied[key],100 if key=="name" else 5000)
    if "report_format" in supplied:
        if not isinstance(supplied["report_format"],str) or supplied["report_format"] not in {"html","csv","json"}:raise ValueError("Choose HTML, CSV or JSON guild report format.")
        current["report_format"]=supplied["report_format"]
    if "columns" in supplied:
        columns=supplied["columns"]
        if isinstance(columns,str):columns=[part.strip() for part in columns.split(",") if part.strip()]
        if not isinstance(columns,list) or not columns or any(not isinstance(key,str) or key not in COLUMNS for key in columns) or len(columns)!=len(set(columns)):
            raise ValueError("Choose unique known guild report columns.")
        current["columns"]=columns
    if "assignments" in supplied:current["assignments"]=validate_assignments(supplied["assignments"],roster)
    all_preferences=store.read().get("guild_preferences_by_id",{})
    if not isinstance(all_preferences,dict):all_preferences={}
    all_preferences[str(current["guild_id"])]=current
    store.update("guild_preferences_by_id",all_preferences)
    return deepcopy(current)


def _public_value(value):
    if value is None:return ""
    if isinstance(value,(str,int,float)) and not isinstance(value,bool):return value
    return ""


def guild_report_rows(roster,*,deck_rows=(),preferences=None):
    """Whitelist public display fields; never copy whole snapshots/decks."""
    preferences=preferences or {};deck_map={}
    for row in deck_rows:
        if not isinstance(row,dict):continue
        member=str(row.get("member") or row.get("name") or "").strip().casefold()
        if member and (row.get("active") or member not in deck_map):deck_map[member]=row
    assignments={row["member"].strip().casefold():row for row in validate_assignments(preferences.get("assignments",[]))}
    out=[]
    for member in roster:
        if not isinstance(member,dict):continue
        name=str(member.get("name")or"").strip()
        if not name:continue
        row={key:_public_value(member.get(key)) for key in COLUMNS};row["name"]=name
        if row["last_seen"]=="":row["last_seen"]=_public_value(member.get("last_online",member.get("last_active")))
        deck=deck_map.get(name.casefold())
        if deck:
            row["deck"]=_public_value(deck.get("deck"))
            row["cards"]=_public_value(deck.get("card_count"))
        assignment=assignments.get(name.casefold())
        if assignment:
            row["defense_island"]=assignment["island"]
            if "deck" in assignment:row["deck"]=assignment["deck"]
            if "notes" in assignment:row["notes"]=assignment["notes"]
        out.append(row)
    return out


def _csv_value(value):
    # Prevent spreadsheet applications from interpreting imported text as code.
    if isinstance(value,str) and (value.startswith("'") or value.lstrip().startswith(("=","+","-","@"))):return "'"+value
    return value


def parse_defense_plan(text,*,plan_format="csv",guild_id=None,roster=None):
    """Parse a bounded local plan and verify its guild and exact member names."""
    if not isinstance(text,str) or len(text.encode("utf-8"))>256*1024:
        raise ValueError("Defense plan text exceeds the supported limit.")
    expected=_integer(guild_id) if guild_id is not None else None
    if plan_format=="json":
        try:value=json.loads(text)
        except ValueError:raise ValueError("Defense plan JSON could not be parsed.") from None
        if isinstance(value,dict):
            if set(value)-{"guild_id","assignments"} or "assignments" not in value:
                raise ValueError("Defense plan JSON fields are guild_id and assignments.")
            if "guild_id" in value and expected is not None and _integer(value["guild_id"])!=expected:
                raise ValueError("The defense plan belongs to a different guild.")
            value=value["assignments"]
    elif plan_format=="csv":
        from io import StringIO
        reader=csv.DictReader(StringIO(text.lstrip("\ufeff")))
        names=reader.fieldnames or []
        if (not {"member","island"}<=set(names) or len(names)!=len(set(names))
                or set(names)-{"guild_id","member","island","deck","notes"}):
            raise ValueError("Defense CSV needs member,island and optional guild_id,deck,notes columns.")
        value=[];known=_roster_names(roster or [])
        for row in reader:
            if len(value)>=100 or None in row or any(item is None for item in row.values()):
                raise ValueError("Defense CSV has invalid or too many rows.")
            if expected is not None and row.get("guild_id") and _integer(row["guild_id"])!=expected:
                raise ValueError("The defense plan belongs to a different guild.")
            item={key:row[key] for key in ("member","island","deck","notes") if key in row}
            # Spreadsheet-safe exported names can carry an apostrophe prefix.
            # Keep a literal exact roster name; remove it only for a known match.
            member=item["member"]
            if (known.get(member.casefold())!=1 and member.startswith("'")
                    and (member.startswith("''") or member[1:].lstrip().startswith(("=","+","-","@")))
                    and known.get(member[1:].casefold())==1):item["member"]=member[1:]
            note=item.get("notes","")
            if note.startswith("''") or note.startswith("'") and note[1:].lstrip().startswith(("=","+","-","@")):item["notes"]=note[1:]
            value.append(item)
    else:raise ValueError("Choose CSV or JSON defense plan format.")
    return validate_assignments(value,roster)


def import_defense_plan(store,guild_id,path,*,roster,apply=False,plan_format=None):
    target=Path(path);plan_format=plan_format or target.suffix.lstrip(".").casefold()
    if plan_format not in {"csv","json"}:raise ValueError("Choose a local CSV or JSON defense plan.")
    try:
        if target.stat().st_size>256*1024:raise ValueError("Defense plan file exceeds the supported limit.")
        text=target.read_text(encoding="utf-8-sig")
    except (OSError,UnicodeError):raise ValueError("The local defense plan could not be read.") from None
    assignments=parse_defense_plan(text,plan_format=plan_format,guild_id=guild_id,roster=roster)
    if apply:save_guild_preferences(store,guild_id,{"assignments":assignments},roster=roster)
    return {"guild_id":_integer(guild_id),"assignments":assignments,"applied":bool(apply),
            "message":"Local defense plan saved." if apply else "Local defense plan preview; preferences are unchanged."}


def export_defense_plan(preferences,directory,*,plan_format="csv"):
    guild_id=_integer(preferences.get("guild_id"));assignments=validate_assignments(preferences.get("assignments",[]))
    if plan_format not in {"csv","json"}:raise ValueError("Choose CSV or JSON defense plan format.")
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    target=directory/("guild-defense-"+uuid.uuid4().hex[:12]+"."+plan_format)
    if plan_format=="json":
        target.write_text(json.dumps({"guild_id":guild_id,"assignments":assignments},ensure_ascii=False,indent=2),encoding="utf-8")
    else:
        with target.open("w",encoding="utf-8-sig",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=["guild_id","member","island","deck","notes"]);writer.writeheader()
            writer.writerows({"guild_id":guild_id,**{key:_csv_value(row.get(key,"")) for key in ("member","island","deck","notes")}} for row in assignments)
    return {"path":str(target.resolve()),"type":"text/csv" if plan_format=="csv" else "application/json",
            "label":"Local guild defense plan","assignments":len(assignments)}


def export_guild_report(roster,directory,*,deck_rows=(),preferences=None,report_format=None,title=None):
    preferences=preferences or {};report_format=report_format or preferences.get("report_format","html")
    if report_format not in {"html","csv","json"}:raise ValueError("Choose HTML, CSV or JSON guild report format.")
    columns=preferences.get("columns",DEFAULT_COLUMNS)
    if not isinstance(columns,(list,tuple)) or not columns or any(not isinstance(key,str) or key not in COLUMNS for key in columns) or len(set(columns))!=len(columns):
        raise ValueError("Guild report columns are invalid.")
    rows=guild_report_rows(roster,deck_rows=deck_rows,preferences=preferences)
    if not rows:raise ValueError("No named current roster members are available for the report.")
    title=str(title or preferences.get("name") or "Guild report")
    generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    target=directory/("guild-report-"+uuid.uuid4().hex[:12]+"."+report_format)
    if report_format=="csv":
        with target.open("w",encoding="utf-8-sig",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=list(columns));writer.writeheader()
            writer.writerows({key:_csv_value(row[key]) for key in columns} for row in rows)
    elif report_format=="json":
        target.write_text(json.dumps({"title":title,"generated":generated,"columns":list(columns),
                                      "members":[{key:row[key] for key in columns} for row in rows]},ensure_ascii=False,indent=2),encoding="utf-8")
    else:
        heading="".join('<th scope="col">'+escape(key.replace("_"," ").title())+'</th>' for key in columns)
        body="".join('<tr>'+"".join('<td>'+escape(str(row[key]))+'</td>' for key in columns)+'</tr>' for row in rows)
        notes=escape(str(preferences.get("defense_plan")or""))
        html='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'"><title>'+escape(title)+'</title><style>body{font:15px Segoe UI,Arial,sans-serif;background:#0b1020;color:#edf2ff;margin:24px}h1{font-size:25px}p{color:#a9b8d0}input{background:#182238;color:#edf2ff;border:1px solid #435473;padding:10px;max-width:95%;width:360px;margin:8px 0 18px}table{border-collapse:collapse;width:100%;background:#182238}td,th{padding:10px 12px;text-align:left;border-bottom:1px solid #344158}th{position:sticky;top:0;background:#23314b}tr:hover{background:#22324b}pre{white-space:pre-wrap;color:#c0cde0}section{overflow:auto}</style></head><body><h1>'+escape(title)+'</h1><p>'+escape(generated)+'. Local snapshot; run Guild Report again to refresh.</p><label for="filter">Filter members</label><br><input id="filter" type="search" placeholder="Name, island, notes…" autocomplete="off"><section><table><thead><tr>'+heading+'</tr></thead><tbody>'+body+'</tbody></table></section><pre>'+notes+'</pre><script>document.getElementById("filter").addEventListener("input",function(){const q=this.value.toLocaleLowerCase();for(const row of document.querySelectorAll("tbody tr"))row.hidden=!row.textContent.toLocaleLowerCase().includes(q)});</script></body></html>'
        target.write_text(html,encoding="utf-8")
    return {"path":str(target.resolve()),"type":{"html":"text/html","csv":"text/csv","json":"application/json"}[report_format],
            "label":"Local guild report","members":len(rows)}
