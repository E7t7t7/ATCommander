from __future__ import annotations
import json
from .config import APP_DIR
FILE=APP_DIR/"settings.json"
DEFAULTS={
 "move_delay":0.10,"battle_delay":1.5,"arena_limit":1,"arena_deck_id":"","arena_hero_id":"",
 "arena_reserve_energy":0,"arena_continuous":False,"arena_poll_seconds":60,
 "rumble_defense_deck_id":"","siege_defense_deck_id":"","siege_island_id":"","rumble_logic":"Item → Combo (default)","siege_logic":"Item → Combo (default)",
 "siege_target_a_id":"","siege_target_b_id":"","siege_target_c_id":"","siege_fallback_any":True,"siege_poll_seconds":60,
 "melee_character_first":False,
 "adventure_complete_islands":False,"adventure_completion_target":"mastery",
 "adventure_farm_kind":"Main Adventure","adventure_island":3,"adventure_stage":1,
 "swole_poll_seconds":30,"adventure_reserve_energy":0,"adventure_continuous":False,"adventure_mission_id":"","ad_click_sequence":[],
 "ad_play_click":{},"static_resource_urls":{}
}
def load_settings():
    out=dict(DEFAULTS)
    try:
        raw=json.loads(FILE.read_text(encoding="utf-8"));
        if isinstance(raw,dict):out.update(raw)
    except Exception:pass
    return out
def save_settings(data):
    APP_DIR.mkdir(parents=True,exist_ok=True); out=dict(DEFAULTS); out.update(data or {}); FILE.write_text(json.dumps(out,indent=2),encoding="utf-8")
