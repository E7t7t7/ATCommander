from __future__ import annotations
import json
from typing import Any
from .config import APP_DIR
FILE=APP_DIR/"protocol_profiles.json"
KEYS=[
 "deck_switch","hero_switch","ad_flow","rumble","rumble_defense","siege","siege_defense","siege_island",
 "swole_status","swole_training","swole_battle","mail_claim_all","mail_delete","item_spend","nixon_spend","recycle_cards","daily_upgrade",
 "adventure_progress","adventure_start"
]
def load_profiles()->dict[str,Any]:
    out={k:None for k in KEYS}
    try:
        raw=json.loads(FILE.read_text(encoding="utf-8"))
        if isinstance(raw,dict):out.update(raw)
    except Exception:pass
    return out
def save_profiles(data:dict[str,Any])->None:
    APP_DIR.mkdir(parents=True,exist_ok=True); FILE.write_text(json.dumps(data,indent=2),encoding="utf-8")
