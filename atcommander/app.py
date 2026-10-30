from __future__ import annotations

from datetime import datetime
import json, os, queue, re, subprocess, threading, time, tkinter as tk, webbrowser
from pathlib import Path
from tkinter import ttk, messagebox, simpledialog

from .arena_runner import ArenaRunner
from .event_runner import EventBattleRunner, ChallengeBattleRunner
from .swole_runner import SwoleTrainingRunner
from .adventure_runner import AdventureRunner
from .ad_ui import (attempt_legitimate_ad_ui, begin_ad_click_calibration, finish_ad_click_calibration,
                    begin_play_click_calibration, finish_play_click_calibration,
                    test_calibrated_ad_ui, prepare_game_ui)
from .credentials import load_credentials, save_credentials
from .api import ATClient
from .session_stats import diff_snapshots, build_inventory_snapshot
from .persistent_stats import LifetimeTracker
from .account_state import (
    exact_decks, unlocked_heroes, discover_siege_islands, scan_progress_metrics,
    choose_trophy_metric, find_hero_medals, find_ad_state, find_arena_energy,
    find_adventure_energy, discover_challenge_events, discover_campaign_markers, parse_siege_status,
)
from .protocol_config import load_profiles, save_profiles
from .settings import load_settings, save_settings
from .learn_center import LearningBrowser, LearnSession, install_profile, install_siege_flow_profile, profile_candidates_from_trace, install_candidate_profile, migrate_last_siege_browser_replay_metadata, migrate_last_native_replay_metadata
from .config import APP_DIR
from .browser_bridge import capture_next_successful_api_request
from .cm_reviews import collect_live_review_data
from .community_consensus import fetch_reddit_consensus
from .protocol_lab import run_swole_signature_lab
from .command_center import CommandCenterMixin
from .research_center import ResearchCenterMixin
from .client_update_ui import ClientUpdateMixin
from .app_update_ui import AppUpdateMixin
from .deck_builder_ui import DeckBuilderMixin
from .reward_cleanup_ui import RewardCleanupMixin
from .deck_selection import DECK_BINDINGS,label_parts,resolve_deck,saved_name_key
from .log_view import logbox, split_controls_log
from .adventure_catalog import build_adventure_campaign_rows, find_adventure_campaign
from .shop_rewards import (api_rejected, extract_item_choices, item_choice_priority,
                           choose_item_reward, item_choice_confirmed, daily_upgrade_progress,
                           safe_upgrade_candidates, upgrade_confirmed, run_single_purchases,
                           PurchaseInterrupted)

GAME_URL="https://www.kongregate.com/en/games/throwdown/animation-throwdown"
VERSION="1.7.28"
LOGIC_VALUES=["Item → Combo (default)","Item → Combo (aggressive tiebreak)","Item → Combo (durable tiebreak)"]
ADVENTURE_MODES=["Farm selected stage","Complete islands, then farm"]
ADVENTURE_GOALS={"Stage completion":"progress","100% mastery":"mastery"}
LEARN_ACTIONS=[
    ("deck_switch","Deck switch","In the opened game, switch to a different saved deck once."),
    ("hero_switch","Hero switch","In the opened game, switch your active hero/commander once."),
    ("rumble","Rumble battle","During an active Rumble, manually start one Rumble fight."),
    ("rumble_defense","Rumble defense deck","Change the Rumble defense deck once."),
    ("siege","Siege battle","Built in: fresh signed API start uses a living target slot during the Battle phase."),
    ("siege_defense","Siege defense deck","Change the Siege defense deck once."),
    ("siege_island","Siege island","Move yourself to a different Siege island once."),
    ("challenge_battle","Challenge / Clash / Melee battle","Built in: fresh signed API start uses the active event's challenge ID and level."),
    ("swole_status","Swole status","Open Swole so the game refreshes its current training status."),
    ("swole_training","Start Swole training","Start one Swole hero training manually."),
    ("swole_battle","Swole battle","Built in: fresh signed API start uses current Swole challenge data."),
    ("mail_claim_all","Claim mail","Claim mail once (prefer Claim All if available)."),
    ("mail_delete","Delete mail","Delete one mail message manually."),
    ("item_spend","Spend an item","Spend one non-Nixon consumable/item manually."),
    ("item_choice","Choose item reward","Built in: selects a current offered reward with Rogu → Characters → Items / Power Cards priority."),
    ("recycle_cards","Recycle cards","Recycle at least one card once."),
    ("daily_upgrade","Upgrade card","Built in: verifies Daily quest progress and upgrades only safe loose cards still needed."),
    ("adventure_progress","Adventure status","Built in: Refresh Status reads energy, campaign/island metadata and the selected target."),
    ("adventure_start","Adventure battle","Start one Adventure battle manually."),
    ("ad_flow","Legitimate ad flow","Use AT's normal ad-bonus flow once. This capture is diagnostic only; AT Commander does not fake an ad view."),

]

# These actions have confirmed direct calls in the 1.7.x evidence and do not
# require the Learning Browser. Diagnostics shows that clearly instead of asking
# the user to teach the same call again.
BUILTIN_ACTIONS={
    "deck_switch","hero_switch","swole_status","swole_training","swole_battle","challenge_battle","siege",
    "mail_claim_all","item_spend","item_choice","daily_upgrade","recycle_cards","adventure_progress","ad_flow","siege_defense","siege_island",
}

# For these resources, AT's quantity field is a number of pulls/uses. One pull
# consumes 1,000 units of the resource; asking for '1000' therefore means 1000
# pulls, not spending 1000 stones once.
ITEM_USE_COSTS={
    "200002":1000,"200003":1000,"200004":1000,
    "200009":1000,"200010":1000,"200011":1000,"200012":1000,
    "200013":1000,"200014":1000,"200015":1000,"200016":1000,
}
# Choice-style rewards are deliberately opened one pull at a time until the
# follow-up selection protocol is captured and proven. Include Legendary Power
# Stone conservatively because the user's live account specifically reported a
# selection flow for it.
CHOICE_ITEM_IDS={"200010","200011","200014","200015","200016"}

# AT's shop endpoint enforces a server-side cooldown on repeated successful
# purchases. Older public AT automation scripts deliberately sleep four seconds
# between single-pack buys. 1.7.17 therefore tries the game's native quantity
# purchase first (one request for the requested stack), and only falls back to
# smaller paced chunks when the server explicitly rejects that quantity.
SHOP_COOLDOWN_SECONDS=4.05

def _shop_result_message(response):
    if not isinstance(response,dict): return str(response)
    msg=response.get("result_message")
    if isinstance(msg,list): return " ".join(str(x) for x in msg if x is not None)
    return str(msg or "")

def _shop_failed(response):
    return isinstance(response,dict) and response.get("result") is False

def _shop_warning16(response):
    low=_shop_result_message(response).lower()
    return ("warning_16" in low or "try that again in a moment" in low or
            "please try that again" in low or "too fast" in low or "rate" in low)

class ATCommanderApp(RewardCleanupMixin,DeckBuilderMixin,AppUpdateMixin,ClientUpdateMixin,CommandCenterMixin,ResearchCenterMixin):
    def __init__(self,root:tk.Tk):
        self.root=root; root.title(f"AT Commander {VERSION}"); root.geometry("1660x940"); root.minsize(1340,760); root.configure(bg="#0b1220")
        self._closing=False
        self.events=queue.Queue(); self.arena_runner=None; self.event_runner=None; self.adventure_runner=None; self.swole_runner=None; self.learn_session=None; self.learn_key=None; self.learning_browser=LearningBrowser(); self.last_capture_path=None; self.last_capture_key=None
        self.session_data=None; self.session_started_mono=None; self.paused=False; self.lifetime=LifetimeTracker(); self.lifetime_data=self.lifetime.to_dict()
        self.settings=load_settings();
        try:
            if migrate_last_siege_browser_replay_metadata():
                self.events.put({"kind":"log","message":"Migrated the last successful Siege learn trace to browser-signed replay metadata; no extra manual Siege attack should be needed just for migration."})
            migrated=migrate_last_native_replay_metadata()
            if migrated:
                self.events.put({"kind":"log","message":"Found legacy native browser request IDs in local learn traces: "+", ".join(migrated)+". These IDs are Chromium-session scoped; 1.7.17 will ask for one fresh capture if an ID is stale instead of retrying known-bad reconstructed combat calls."})
        except Exception: pass
        self.deck_rows=[]; self.hero_rows=[]; self.siege_island_rows=[]; self.siege_target_rows=[]; self.progress_rows=[]; self.hero_medal_rows=[]; self.current_snapshot={}; self.swole_plan=[]; self.nixon_pack_rows=[]; self.challenge_rows=[]; self.adventure_campaign_rows=[]; self.adventure_mission_label_map={}; self.pending_item_choices=[]
        self._style(); self._build(); self._refresh_account_badge(); self._refresh_account_metadata()
        root.after(100,self._drain_events); root.after(1000,self._tick_clock)

    def _style(self):
        from .appearance import apply_style
        apply_style(self.root)

    def _build(self):
        top=ttk.Frame(self.root); top.pack(fill="x",padx=14,pady=(10,5))
        ttk.Label(top,text="AT Commander",style="Header.TLabel").pack(side="left"); ttk.Label(top,text="Decks, events & daily routines",foreground="#9eacbc").pack(side="left",padx=(12,0),pady=(7,0))
        self.account_var=tk.StringVar(value="Account: checking…"); ttk.Label(top,textvariable=self.account_var).pack(side="right",padx=(8,0)); ttk.Button(top,text="Stop all",style="Danger.TButton",command=self.stop_all).pack(side="right",padx=6); ttk.Button(top,text="Game / Ads",command=self._open_game_panel).pack(side="right"); ttk.Button(top,text="Refresh account",command=self._refresh_account_metadata).pack(side="right",padx=(0,6))
        connection=ttk.Frame(self.root);connection.pack(fill="x",padx=14)
        self._build_app_update(connection)
        self._build_client_update(connection)
        workspace=ttk.Frame(self.root); workspace.pack(fill="both",expand=True,padx=14,pady=(3,14))
        nav=ttk.Frame(workspace,style="Nav.TFrame",padding=(8,10)); nav.pack(side="left",fill="y",padx=(0,12))
        self.nb=ttk.Notebook(workspace,style="Nav.TNotebook"); self.nb.pack(side="left",fill="both",expand=True)
        self.arena_tab=ttk.Frame(self.nb); self.events_tab=ttk.Frame(self.nb); self.swole_tab=ttk.Frame(self.nb); self.adventure_tab=ttk.Frame(self.nb); self.cm_tab=ttk.Frame(self.nb); self.inventory_tab=ttk.Frame(self.nb); self.stats_tab=ttk.Frame(self.nb); self.diag_tab=ttk.Frame(self.nb)
        for tab,name in [(self.arena_tab,"Arena"),(self.events_tab,"Events"),(self.swole_tab,"Swole"),(self.adventure_tab,"Adventure"),(self.cm_tab,"CM Reviews"),(self.inventory_tab,"Inventory / Mail"),(self.stats_tab,"Stats"),(self.diag_tab,"Diagnostics / Learn")]: self.nb.add(tab,text=name)
        self.deck_builder_tab=ttk.Frame(self.nb);self.nb.add(self.deck_builder_tab,text="Deck builder")
        self.commands_tab=ttk.Frame(self.nb); self.routines_tab=ttk.Frame(self.nb)
        self.research_tab=ttk.Frame(self.nb);self.nb.add(self.research_tab,text="Research")
        self.nb.add(self.commands_tab,text="Command Center"); self.nb.add(self.routines_tab,text="Automation")
        self.nav_buttons=[]
        groups=[("Daily play",[("Automation",self.routines_tab),("Research",self.research_tab)]),
                ("Battles",[("Arena",self.arena_tab),("Adventure",self.adventure_tab),("Events",self.events_tab),("Swole",self.swole_tab)]),
                ("Collection",[("Deck builder",self.deck_builder_tab),("CM Reviews",self.cm_tab),("Inventory / Mail",self.inventory_tab)]),
                ("More",[("Stats",self.stats_tab),("Command Center",self.commands_tab),("Tools / Ads",self.diag_tab)])]
        for section,items in groups:
            ttk.Label(nav,text=section,style="Nav.TLabel").pack(anchor="w",padx=12,pady=(8,4))
            for name,tab in items:
                button=ttk.Button(nav,text=name,style="Nav.TButton",width=19,command=lambda t=tab:self.nb.select(t))
                button.pack(fill="x",pady=1);self.nav_buttons.append((tab,button))
        for label,group in [("Cards / Decks","Deck"),("Guild / Chat","Guild")]:
            ttk.Button(nav,text=label,style="Nav.TButton",width=19,command=lambda g=group:self._open_command_group(g)).pack(fill="x",pady=1)
        self.nb.bind("<<NotebookTabChanged>>",self._sync_navigation,add="+")
        self._build_arena(); self._build_events(); self._build_swole(); self._build_adventure(); self._build_cm_reviews(); self._build_inventory(); self._build_stats(); self._build_diag()
        self._build_command_center()
        self._build_deck_builder()
        self._build_research_center()
        self._build_game_panel()
        from .appearance import soften_labels
        soften_labels(self.root);self._sync_navigation()

    def _sync_navigation(self,event=None):
        selected=self.nb.select()
        for tab,button in self.nav_buttons:
            button.configure(style="Nav.Active.TButton" if str(tab)==selected else "Nav.TButton")
        if hasattr(self,"deck_builder_tab") and str(self.deck_builder_tab)==selected and hasattr(self,"deck_builder_status"):
            self._builder_opened()

    def _build_game_panel(self):
        from .embedded_browser import configure
        self.game_panel_stop=threading.Event();self.game_panel_thread=None
        self.ad_stop=threading.Event();self.ad_thread=None
        self.game_panel_visible=False;self.game_panel_attached=False
        self.game_panel=ttk.Frame(self.root,style="Card.TFrame",padding=3)
        bar=ttk.Frame(self.game_panel,style="Card.TFrame");bar.pack(fill="x")
        ttk.Label(bar,text="Game / Ads",style="Card.TLabel").pack(side="left",padx=6)
        ttk.Button(bar,text="Larger",command=self._resize_game_panel).pack(side="right")
        ttk.Button(bar,text="Hide",command=lambda:self._show_game_panel(False)).pack(side="right")
        self.game_host=tk.Frame(self.game_panel,width=480,height=370,bg="#0b1220")
        self.game_host.pack();self.game_host.pack_propagate(False)
        self.game_panel_status=tk.StringVar(value="Open Game / Ads to load the saved game session.")
        ttk.Label(self.game_panel,textvariable=self.game_panel_status,style="Muted.Card.TLabel",wraplength=470).pack(fill="x",padx=6,pady=3)
        self.root.update_idletasks()
        configure(self.game_host.winfo_id(),480,370,top_inset=30)
        self.root.after(100,self._poll_game_panel)

    def _show_game_panel(self,visible=True):
        self.game_panel_visible=bool(visible)
        if visible:
            self.game_panel.place(relx=1,rely=1,anchor="se",x=-22,y=-22)
            self.game_panel.lift()
        else:self.game_panel.place_forget()

    def _resize_game_panel(self):
        if CommandCenterMixin._browser_action_busy(self):
            if not getattr(self,"_closing",False):messagebox.showinfo("Game / Ads","Wait for active work to finish before resizing the game panel.")
            return
        from .embedded_browser import configure,resize_browser
        width,height=(640,520) if self.game_host.winfo_reqwidth()==480 else (480,370)
        self.game_host.configure(width=width,height=height)
        configure(self.game_host.winfo_id(),width,height,top_inset=30)
        resize_browser(width,height,top_inset=30)

    def _poll_game_panel(self):
        from .embedded_browser import process_pending
        state=process_pending()
        attached=state.get("embedded") is True
        if attached and not self.game_panel_attached:
            self._show_game_panel()
            self.game_panel_status.set("Saved game session inside Commander. Hide keeps it available.")
        self.game_panel_attached=attached
        if not getattr(self,"_game_panel_closed",False):self.root.after(100,self._poll_game_panel)

    def _open_game_panel(self):
        if getattr(self,"_closing",False):return
        self._show_game_panel()
        # Showing the owned panel never restarts startup handling over an ad or
        # another worker's native game controls.
        if getattr(self,"game_panel_attached",False):return
        if CommandCenterMixin._browser_action_busy(self):
            browser_work=any(getattr(self,name,None) is not None and getattr(self,name).is_alive()
                for name in ("game_panel_thread","ad_thread"))
            if not browser_work:
                self.game_panel_status.set("Wait for active automation to finish before loading the game. An already open panel remains available.")
            return
        self.game_panel_stop.clear()
        self.game_panel_status.set("Loading the saved game session…")
        def work():
            emit=lambda m:self.events.put({"kind":"log","message":m})
            try:result=prepare_game_ui(emit,wait_for_canvas=60,should_stop=self.game_panel_stop.is_set)
            except Exception:
                emit("Game / Ads: preparation could not be verified; no ad was started.");return
            if result.get("ok") and not self.game_panel_stop.is_set():
                try:
                    from .utility_commands import build_service
                    from .startup_popups import settle_startup_popups
                    service=build_service(ATClient(load_credentials()))
                    service.stop=self.game_panel_stop
                    settle_startup_popups(service,result,should_stop=self.game_panel_stop.is_set,log=emit)
                except Exception:
                    emit("Game / Ads: a startup display needs review; no ad was started.")
            elif not result.get("ok"):emit(str(result.get("message") or "The game panel did not load."))
        self.game_panel_thread=threading.Thread(target=work,daemon=True)
        self.game_panel_thread.start()

    def _open_command_group(self,group):
        self.command_group.set(group); self.command_search.set(""); self.nb.select(self.commands_tab)

    def _card(self,p,title,var):
        f=ttk.Frame(p,style="Card.TFrame",padding=9); ttk.Label(f,text=title,style="Muted.Card.TLabel").pack(anchor="w"); ttk.Label(f,textvariable=var,style="Big.Card.TLabel").pack(anchor="w",pady=(2,0)); return f

    def _build_arena(self):
        cards=ttk.Frame(self.arena_tab); cards.pack(fill="x",pady=(10,8)); self.status_var=tk.StringVar(value="Idle"); self.battles_var=tk.StringVar(value="0"); self.record_var=tk.StringVar(value="0W / 0L"); self.rate_var=tk.StringVar(value="—"); self.trophy_var=tk.StringVar(value="—"); self.ad_var=tk.StringVar(value="Unknown"); self.energy_var=tk.StringVar(value="—")
        for i,(t,v) in enumerate([("Status",self.status_var),("Battles",self.battles_var),("Record",self.record_var),("Win rate",self.rate_var),("Trophies",self.trophy_var),("Arena energy",self.energy_var),("Ad bonus",self.ad_var)]):
            cards.columnconfigure(i,weight=1); self._card(cards,t,v).grid(row=0,column=i,sticky="nsew",padx=3)
        c=ttk.Frame(self.arena_tab,style="Card.TFrame",padding=10); c.pack(fill="x",pady=(0,8));
        self.limit_var=tk.StringVar(value=str(self.settings.get("arena_limit",1))); self.delay_var=tk.StringVar(value=str(self.settings.get("move_delay",.10))); self.battle_delay_var=tk.StringVar(value=str(self.settings.get("battle_delay",1.5))); self.reserve_var=tk.StringVar(value=str(self.settings.get("arena_reserve_energy",0))); self.continuous_var=tk.BooleanVar(value=bool(self.settings.get("arena_continuous",False))); self.deck_var=tk.StringVar(value="Current / unchanged"); self.hero_var=tk.StringVar(value="Current / unchanged")
        for col,(label,var,width) in enumerate([("Battle limit",self.limit_var,7),("Move delay",self.delay_var,7),("Battle delay",self.battle_delay_var,7),("Keep energy",self.reserve_var,7)]):
            ttk.Label(c,text=label,style="Card.TLabel").grid(row=0,column=col,sticky="w",padx=(0,8)); ttk.Entry(c,textvariable=var,width=width).grid(row=1,column=col,sticky="ew",padx=(0,8))
        ttk.Label(c,text="Deck on start",style="Card.TLabel").grid(row=0,column=4,sticky="w"); self.deck_combo=ttk.Combobox(c,textvariable=self.deck_var,state="readonly",width=25,values=["Current / unchanged"]); self.deck_combo.grid(row=1,column=4,sticky="ew",padx=(0,8))
        ttk.Label(c,text="Hero on start",style="Card.TLabel").grid(row=0,column=5,sticky="w"); self.hero_combo=ttk.Combobox(c,textvariable=self.hero_var,state="readonly",width=25,values=["Current / unchanged"]); self.hero_combo.grid(row=1,column=5,sticky="ew",padx=(0,8))
        ttk.Checkbutton(c,text="Continuous monitor (spend back down to reserve)",variable=self.continuous_var).grid(row=2,column=0,columnspan=2,sticky="w",pady=(7,0))
        self.auto_ads_var=tk.BooleanVar(value=bool(self.settings.get("auto_watch_ads",False))); ttk.Checkbutton(c,text="Auto-watch ads before any automation if boost is inactive",variable=self.auto_ads_var).grid(row=2,column=2,columnspan=2,sticky="w",pady=(7,0))
        self.arena_bypass_turn_limit_var=tk.BooleanVar(value=bool(self.settings.get("arena_bypass_turn_limit",False)))
        ttk.Checkbutton(c,text="Bypass 30-turn cutoff (Arena combo events; finish at win/loss)",variable=self.arena_bypass_turn_limit_var).grid(row=3,column=0,columnspan=6,sticky="w",pady=(7,0))
        btn=ttk.Frame(c,style="Card.TFrame"); btn.grid(row=2,column=4,columnspan=2,sticky="e",pady=(6,0)); self.start_btn=ttk.Button(btn,text="START",style="Accent.TButton",command=self.start_run); self.pause_btn=ttk.Button(btn,text="PAUSE",command=self.toggle_pause,state="disabled"); self.stop_btn=ttk.Button(btn,text="STOP",command=self.stop_run,state="disabled"); self.start_btn.pack(side="left",padx=3); self.pause_btn.pack(side="left",padx=3); self.stop_btn.pack(side="left",padx=3)
        for i in range(6):c.columnconfigure(i,weight=1 if i>=4 else 0)
        live=ttk.Frame(self.arena_tab,style="Card.TFrame",padding=9); live.pack(fill="x",pady=(0,8)); self.opponent_var=tk.StringVar(value="Opponent: —"); self.action_var=tk.StringVar(value="Waiting"); ttk.Label(live,textvariable=self.opponent_var,style="Card.TLabel",font=("Segoe UI Semibold",10)).pack(anchor="w"); ttk.Label(live,textvariable=self.action_var,style="Muted.Card.TLabel").pack(anchor="w")
        self.log=self._logbox(self.arena_tab); self._log("Ready. API-only mode; no game rendering or animations.")

    def _build_events(self):
        self.event_panes,event_controls,event_log_host=split_controls_log(self.events_tab,controls_height=580)
        ttk.Label(event_controls,text="Choose your event below. Drag the divider above Activity to resize the log.").pack(anchor="w",pady=(4,8))
        wrap=ttk.Frame(event_controls); wrap.pack(fill="x"); wrap.columnconfigure(0,weight=1); wrap.columnconfigure(1,weight=1)
        self.rumble_status=tk.StringVar(value="Checking…"); self.rumble_deck=tk.StringVar(value="Current / unchanged"); self.rumble_offense_deck=tk.StringVar(value="Current / unchanged"); self.rumble_offense_hero=tk.StringVar(value="Current / unchanged"); self.rumble_logic=tk.StringVar(value=str(self.settings.get("rumble_logic",LOGIC_VALUES[0])))
        r=ttk.Frame(wrap,style="Card.TFrame",padding=12); r.grid(row=0,column=0,sticky="nsew",padx=(0,5)); ttk.Label(r,text="RUMBLE",style="Big.Card.TLabel").grid(row=0,column=0,columnspan=2,sticky="w"); ttk.Label(r,textvariable=self.rumble_status,style="Muted.Card.TLabel").grid(row=1,column=0,columnspan=2,sticky="w",pady=(0,8)); ttk.Label(r,text="Battle logic",style="Card.TLabel").grid(row=2,column=0,sticky="w"); ttk.Combobox(r,textvariable=self.rumble_logic,state="readonly",values=LOGIC_VALUES,width=34).grid(row=2,column=1,sticky="ew",padx=(8,0)); ttk.Label(r,text="Offense deck",style="Card.TLabel").grid(row=3,column=0,sticky="w",pady=(7,0)); self.rumble_offense_deck_combo=ttk.Combobox(r,textvariable=self.rumble_offense_deck,state="readonly",values=["Current / unchanged"],width=34); self.rumble_offense_deck_combo.grid(row=3,column=1,sticky="ew",padx=(8,0),pady=(7,0)); ttk.Label(r,text="Offense hero",style="Card.TLabel").grid(row=4,column=0,sticky="w",pady=(7,0)); self.rumble_offense_hero_combo=ttk.Combobox(r,textvariable=self.rumble_offense_hero,state="readonly",values=["Current / unchanged"],width=34); self.rumble_offense_hero_combo.grid(row=4,column=1,sticky="ew",padx=(8,0),pady=(7,0)); ttk.Label(r,text="Defense deck",style="Card.TLabel").grid(row=5,column=0,sticky="w",pady=(7,0)); self.rumble_deck_combo=ttk.Combobox(r,textvariable=self.rumble_deck,state="readonly",values=["Current / unchanged"],width=34); self.rumble_deck_combo.grid(row=5,column=1,sticky="ew",padx=(8,0),pady=(7,0)); ttk.Button(r,text="AUTO-COMPLETE RUMBLE",command=lambda:self.start_event("Rumble"),style="Accent.TButton").grid(row=6,column=0,columnspan=2,sticky="w",pady=(10,0)); r.columnconfigure(1,weight=1)
        self.siege_status=tk.StringVar(value="Checking…"); self.siege_deck=tk.StringVar(value="Current / unchanged"); self.siege_offense_deck=tk.StringVar(value="Current / unchanged"); self.siege_offense_hero=tk.StringVar(value="Current / unchanged"); self.siege_logic=tk.StringVar(value=str(self.settings.get("siege_logic",LOGIC_VALUES[0]))); self.siege_island=tk.StringVar(value="Current / unchanged")
        self.siege_target_a=tk.StringVar(value="Any / none"); self.siege_target_b=tk.StringVar(value="Any / none"); self.siege_target_c=tk.StringVar(value="Any / none"); self.siege_fallback_any=tk.BooleanVar(value=bool(self.settings.get("siege_fallback_any",True))); self.siege_poll=tk.StringVar(value=str(self.settings.get("siege_poll_seconds",60)))
        s=ttk.Frame(wrap,style="Card.TFrame",padding=12); s.grid(row=0,column=1,sticky="nsew",padx=(5,0)); ttk.Label(s,text="SIEGE",style="Big.Card.TLabel").grid(row=0,column=0,columnspan=2,sticky="w"); ttk.Label(s,textvariable=self.siege_status,style="Muted.Card.TLabel").grid(row=1,column=0,columnspan=2,sticky="w",pady=(0,8)); ttk.Label(s,text="Battle logic",style="Card.TLabel").grid(row=2,column=0,sticky="w"); ttk.Combobox(s,textvariable=self.siege_logic,state="readonly",values=LOGIC_VALUES,width=34).grid(row=2,column=1,sticky="ew",padx=(8,0)); ttk.Label(s,text="Offense deck",style="Card.TLabel").grid(row=3,column=0,sticky="w",pady=(7,0)); self.siege_offense_deck_combo=ttk.Combobox(s,textvariable=self.siege_offense_deck,state="readonly",values=["Current / unchanged"],width=34); self.siege_offense_deck_combo.grid(row=3,column=1,sticky="ew",padx=(8,0),pady=(7,0)); ttk.Label(s,text="Offense hero",style="Card.TLabel").grid(row=4,column=0,sticky="w",pady=(7,0)); self.siege_offense_hero_combo=ttk.Combobox(s,textvariable=self.siege_offense_hero,state="readonly",values=["Current / unchanged"],width=34); self.siege_offense_hero_combo.grid(row=4,column=1,sticky="ew",padx=(8,0),pady=(7,0)); ttk.Label(s,text="Defense deck",style="Card.TLabel").grid(row=5,column=0,sticky="w",pady=(7,0)); self.siege_deck_combo=ttk.Combobox(s,textvariable=self.siege_deck,state="readonly",values=["Current / unchanged"],width=34); self.siege_deck_combo.grid(row=5,column=1,sticky="ew",padx=(8,0),pady=(7,0)); ttk.Label(s,text="Defense island",style="Card.TLabel").grid(row=6,column=0,sticky="w",pady=(7,0)); self.siege_island_combo=ttk.Combobox(s,textvariable=self.siege_island,state="readonly",values=["Current / unchanged","Island 1","Island 2","Island 3","Island 4","Island 5"],width=34); self.siege_island_combo.grid(row=6,column=1,sticky="ew",padx=(8,0),pady=(7,0))
        ttk.Label(s,text="Attack target A",style="Card.TLabel").grid(row=7,column=0,sticky="w",pady=(7,0)); self.siege_target_a_combo=ttk.Combobox(s,textvariable=self.siege_target_a,state="readonly",values=["Any / none"],width=34); self.siege_target_a_combo.grid(row=7,column=1,sticky="ew",padx=(8,0),pady=(7,0))
        ttk.Label(s,text="Attack target B",style="Card.TLabel").grid(row=8,column=0,sticky="w",pady=(7,0)); self.siege_target_b_combo=ttk.Combobox(s,textvariable=self.siege_target_b,state="readonly",values=["Any / none"],width=34); self.siege_target_b_combo.grid(row=8,column=1,sticky="ew",padx=(8,0),pady=(7,0))
        ttk.Label(s,text="Attack target C",style="Card.TLabel").grid(row=9,column=0,sticky="w",pady=(7,0)); self.siege_target_c_combo=ttk.Combobox(s,textvariable=self.siege_target_c,state="readonly",values=["Any / none"],width=34); self.siege_target_c_combo.grid(row=9,column=1,sticky="ew",padx=(8,0),pady=(7,0))
        opts=ttk.Frame(s,style="Card.TFrame"); opts.grid(row=10,column=0,columnspan=2,sticky="w",pady=(7,0)); ttk.Checkbutton(opts,text="If A/B/C are down, hit any living island",variable=self.siege_fallback_any).pack(side="left"); ttk.Label(opts,text="Poll s",style="Card.TLabel").pack(side="left",padx=(12,4)); ttk.Entry(opts,textvariable=self.siege_poll,width=5).pack(side="left")
        bb=ttk.Frame(s,style="Card.TFrame"); bb.grid(row=11,column=0,columnspan=2,sticky="w",pady=(10,0)); ttk.Button(bb,text="SET DEFENSE ISLAND",command=self.apply_siege_island).pack(side="left",padx=(0,6)); ttk.Button(bb,text="ARM / AUTO-COMPLETE SIEGE",command=lambda:self.start_event("Siege"),style="Accent.TButton").pack(side="left"); ttk.Label(s,text="Commander applies defense, waits for the Battle phase, then uses direct signed API starts against A → B → C and any living fallback island if enabled.",style="Muted.Card.TLabel",wraplength=520).grid(row=12,column=0,columnspan=2,sticky="w",pady=(6,0)); s.columnconfigure(1,weight=1)
        ttk.Separator(r,orient="horizontal").grid(row=7,column=0,columnspan=2,sticky="ew",pady=(8,4))
        ch=ttk.Frame(r,style="Card.TFrame"); ch.grid(row=8,column=0,columnspan=2,sticky="nsew")
        self.challenge_card=ch
        self.challenge_status=tk.StringVar(value="Refresh Account to detect live Challenge / Clash / Melee events.")
        self.challenge_var=tk.StringVar(value="No detected challenge")
        self.challenge_logic=tk.StringVar(value=str(self.settings.get("challenge_logic",LOGIC_VALUES[0]))); self.challenge_offense_deck=tk.StringVar(value="Current / unchanged"); self.challenge_offense_hero=tk.StringVar(value="Current / unchanged")
        self.melee_character_first_var=tk.BooleanVar(value=self.settings.get("melee_character_first") is True)
        ttk.Label(ch,text="Challenges, Clash & Melee",style="Big.Card.TLabel").grid(row=0,column=0,columnspan=2,sticky="w")
        ttk.Label(ch,textvariable=self.challenge_status,style="Muted.Card.TLabel",wraplength=450).grid(row=1,column=0,columnspan=2,sticky="w",pady=(0,6))
        ttk.Label(ch,text="Event",style="Card.TLabel").grid(row=2,column=0,sticky="w")
        self.challenge_combo=ttk.Combobox(ch,textvariable=self.challenge_var,state="readonly",values=["No detected challenge"],width=28)
        self.challenge_combo.grid(row=2,column=1,sticky="ew",padx=(8,0))
        ttk.Label(ch,text="Offense deck",style="Card.TLabel").grid(row=3,column=0,sticky="w",pady=(3,0))
        self.challenge_offense_deck_combo=ttk.Combobox(ch,textvariable=self.challenge_offense_deck,state="readonly",values=["Current / unchanged"],width=28)
        self.challenge_offense_deck_combo.grid(row=3,column=1,sticky="ew",padx=(8,0),pady=(3,0))
        ttk.Label(ch,text="Offense hero",style="Card.TLabel").grid(row=4,column=0,sticky="w",pady=(3,0))
        self.challenge_offense_hero_combo=ttk.Combobox(ch,textvariable=self.challenge_offense_hero,state="readonly",values=["Current / unchanged"],width=28)
        self.challenge_offense_hero_combo.grid(row=4,column=1,sticky="ew",padx=(8,0),pady=(3,0))
        ttk.Label(ch,text="Battle logic",style="Card.TLabel").grid(row=5,column=0,sticky="w",pady=(3,0))
        ttk.Combobox(ch,textvariable=self.challenge_logic,state="readonly",values=LOGIC_VALUES,width=28).grid(row=5,column=1,sticky="ew",padx=(8,0),pady=(3,0))
        ttk.Checkbutton(ch,text="Character first (Melee only)",variable=self.melee_character_first_var,command=self._save_melee_character_first).grid(row=6,column=0,columnspan=2,sticky="w",pady=(3,0))
        buttons=ttk.Frame(ch,style="Card.TFrame");buttons.grid(row=7,column=0,columnspan=2,sticky="w",pady=(4,0))
        ttk.Button(buttons,text="AUTO-COMPLETE SELECTED",style="Accent.TButton",command=self.start_challenge).pack(side="left",padx=(0,8))
        ttk.Button(buttons,text="STOP EVENT",command=self.stop_event).pack(side="left")
        ch.columnconfigure(1,weight=1)
        self.event_log=self._logbox(event_log_host,height=7)

    def _build_swole(self):
        info=ttk.Frame(self.swole_tab,style="Card.TFrame",padding=10); info.pack(fill="x",pady=(10,8)); self.swole_status=tk.StringVar(value="Checking…"); self.swole_tickets=tk.StringVar(value="Swole tickets: — / —")
        ih=ttk.Frame(info,style="Card.TFrame"); ih.pack(fill="x"); ttk.Label(ih,text="SWOLE TRAINING + FIGHTS",style="Big.Card.TLabel").pack(side="left"); ttk.Button(ih,text="REFRESH STATUS",command=self.refresh_swole_status).pack(side="right"); ttk.Label(ih,textvariable=self.swole_tickets,style="Big.Card.TLabel").pack(side="right",padx=(0,14))
        ttk.Label(info,textvariable=self.swole_status,style="Muted.Card.TLabel").pack(anchor="w")
        ttk.Label(info,text="Build a real ordered schedule: training milestones, attack blocks, day labels, and waits. One-step training can be followed immediately by FIGHT blocks while training continues. A later training step automatically waits for the active timer. Paste Schedule supports named heroes or HERO # aliases.",style="Card.TLabel",wraplength=1100).pack(anchor="w",pady=(5,0))
        body=ttk.Frame(self.swole_tab); body.pack(fill="both",expand=True); left=ttk.Frame(body,style="Card.TFrame",padding=9); left.pack(side="left",fill="both",expand=True,padx=(0,5)); right=ttk.Frame(body,style="Card.TFrame",padding=8); right.pack(side="left",fill="both",expand=True,padx=(5,0))
        choose=ttk.Frame(left,style="Card.TFrame"); choose.pack(fill="x"); self.swole_target=tk.StringVar(value="Select hero…"); self.swole_target_combo=ttk.Combobox(choose,textvariable=self.swole_target,state="readonly",values=["Select hero…"],width=28); self.swole_target_combo.pack(side="left",fill="x",expand=True); ttk.Label(choose,text="Target level",style="Card.TLabel").pack(side="left",padx=(8,4)); self.swole_level=tk.IntVar(value=1); ttk.Spinbox(choose,from_=1,to=20,textvariable=self.swole_level,width=5).pack(side="left"); ttk.Button(choose,text="ADD MILESTONE",command=self._swole_add).pack(side="left",padx=(8,0))
        self.swole_queue_list=tk.Listbox(left,bg="#08111e",fg="#dce7f4",selectbackground="#243755",height=16,font=("Segoe UI",10),relief="flat"); self.swole_queue_list.pack(fill="both",expand=True,pady=(8,0)); q=ttk.Frame(left,style="Card.TFrame"); q.pack(fill="x",pady=(7,0)); ttk.Button(q,text="Remove",command=self._swole_remove).pack(side="left"); ttk.Button(q,text="↑",width=3,command=lambda:self._swole_move(-1)).pack(side="left",padx=4); ttk.Button(q,text="↓",width=3,command=lambda:self._swole_move(1)).pack(side="left"); ttk.Button(q,text="Clear",command=self._swole_clear).pack(side="left",padx=4); ttk.Button(q,text="+ FIGHTS",command=self._swole_add_fights).pack(side="left",padx=4); ttk.Button(q,text="PASTE SCHEDULE",command=self._swole_import_schedule).pack(side="left",padx=4); ttk.Label(q,text="Poll s",style="Card.TLabel").pack(side="left",padx=(14,4)); self.swole_poll=tk.StringVar(value=str(self.settings.get("swole_poll_seconds",30))); ttk.Entry(q,textvariable=self.swole_poll,width=6).pack(side="left"); ttk.Button(q,text="START",style="Accent.TButton",command=self.start_swole).pack(side="right"); ttk.Button(q,text="STOP",command=self.stop_swole).pack(side="right",padx=5)
        self.swole_log=self._logbox(right)

    def _build_adventure(self):
        top=ttk.Frame(self.adventure_tab,style="Card.TFrame",padding=12); top.pack(fill="x",pady=(10,8))
        ttk.Label(top,text="ADVENTURE ISLAND / STAGE FARM",style="Big.Card.TLabel").grid(row=0,column=0,columnspan=6,sticky="w")
        self.adventure_energy_var=tk.StringVar(value="Adventure energy: — / —"); ttk.Label(top,textvariable=self.adventure_energy_var,style="Big.Card.TLabel").grid(row=0,column=6,columnspan=2,sticky="e")
        ttk.Label(top,text="Choose Main Adventure by Island → Stage, or any campaign/island AT exposes in its static mission data. Refresh Status updates energy, campaign/island choices and the selected target. Arena, Adventure and Swole use independent runners and can run concurrently.",style="Muted.Card.TLabel",wraplength=1080).grid(row=1,column=0,columnspan=8,sticky="w",pady=(3,8))
        ttk.Label(top,text="Adventure island / campaign",style="Card.TLabel").grid(row=2,column=0,columnspan=2,sticky="w")
        self.adventure_kind=tk.StringVar(value=str(self.settings.get("adventure_farm_kind") or "Main Adventure")); self.adventure_kind_combo=ttk.Combobox(top,textvariable=self.adventure_kind,state="readonly",values=["Main Adventure","Custom / Event mission ID"],width=38); self.adventure_kind_combo.grid(row=3,column=0,columnspan=2,sticky="ew",padx=(0,8)); self.adventure_kind_combo.bind("<<ComboboxSelected>>",lambda _e:self._adventure_campaign_changed())
        def saved_int(key,default,low,high):
            try:value=int(self.settings.get(key,default))
            except (TypeError,ValueError):return default
            return value if low<=value<=high else default
        ttk.Label(top,text="Main island",style="Card.TLabel").grid(row=2,column=2,sticky="w"); self.adventure_island_num=tk.IntVar(value=saved_int("adventure_island",3,1,99)); ttk.Spinbox(top,from_=1,to=99,textvariable=self.adventure_island_num,width=6).grid(row=3,column=2,sticky="w")
        ttk.Label(top,text="Stage",style="Card.TLabel").grid(row=2,column=3,sticky="w"); self.adventure_stage_num=tk.IntVar(value=saved_int("adventure_stage",1,1,3)); ttk.Spinbox(top,from_=1,to=3,textvariable=self.adventure_stage_num,width=6).grid(row=3,column=3,sticky="w")
        ttk.Button(top,text="USE MAIN ISLAND-STAGE",command=self._adventure_fill_id).grid(row=3,column=4,sticky="w",padx=6)
        ttk.Label(top,text="Default farm mission ID",style="Card.TLabel").grid(row=2,column=5,sticky="w"); self.adventure_mission_id=tk.StringVar(value=str(self.settings.get("adventure_mission_id") or "107")); ttk.Entry(top,textvariable=self.adventure_mission_id,width=10).grid(row=3,column=5,sticky="w")
        ttk.Label(top,text="Detected campaign mission",style="Card.TLabel").grid(row=2,column=6,columnspan=2,sticky="w"); self.adventure_mission_choice=tk.StringVar(value="Use Mission ID field"); self.adventure_mission_combo=ttk.Combobox(top,textvariable=self.adventure_mission_choice,state="readonly",values=["Use Mission ID field"],width=34); self.adventure_mission_combo.grid(row=3,column=6,columnspan=2,sticky="ew"); self.adventure_mission_combo.bind("<<ComboboxSelected>>",lambda _e:self._adventure_mission_changed())
        self.adventure_mode=tk.StringVar(value=ADVENTURE_MODES[1] if self.settings.get("adventure_complete_islands") is True else ADVENTURE_MODES[0]); ttk.Label(top,text="Mode",style="Card.TLabel").grid(row=4,column=0,sticky="w",pady=(8,0)); self.adventure_mode_combo=ttk.Combobox(top,textvariable=self.adventure_mode,state="readonly",values=ADVENTURE_MODES,width=30); self.adventure_mode_combo.grid(row=5,column=0,columnspan=2,sticky="w");self.adventure_mode_combo.bind("<<ComboboxSelected>>",lambda _e:self._save_adventure_completion_options())
        ttk.Label(top,text="Battle limit",style="Card.TLabel").grid(row=4,column=2,sticky="w",pady=(8,0)); self.adventure_limit=tk.StringVar(value=str(self.settings.get("adventure_limit",1))); ttk.Entry(top,textvariable=self.adventure_limit,width=7).grid(row=5,column=2,sticky="w")
        ttk.Label(top,text="Keep energy",style="Card.TLabel").grid(row=4,column=3,sticky="w",pady=(8,0)); self.adventure_reserve=tk.StringVar(value=str(self.settings.get("adventure_reserve_energy",0))); ttk.Entry(top,textvariable=self.adventure_reserve,width=7).grid(row=5,column=3,sticky="w")
        self.adventure_continuous=tk.BooleanVar(value=bool(self.settings.get("adventure_continuous",False))); ttk.Checkbutton(top,text="Continuous monitor",variable=self.adventure_continuous).grid(row=5,column=4,sticky="w")
        goal=next((label for label,value in ADVENTURE_GOALS.items() if value==self.settings.get("adventure_completion_target")),"100% mastery")
        self.adventure_completion_goal=tk.StringVar(value=goal);ttk.Label(top,text="Completion goal",style="Card.TLabel").grid(row=4,column=5,columnspan=2,sticky="w",pady=(8,0));self.adventure_goal_combo=ttk.Combobox(top,textvariable=self.adventure_completion_goal,state="readonly",values=list(ADVENTURE_GOALS),width=18);self.adventure_goal_combo.grid(row=5,column=5,columnspan=2,sticky="w");self.adventure_goal_combo.bind("<<ComboboxSelected>>",lambda _e:self._save_adventure_completion_options())
        self.adventure_completion=tk.StringVar(value="Status: not refreshed yet"); ttk.Label(top,textvariable=self.adventure_completion,style="Muted.Card.TLabel").grid(row=6,column=0,columnspan=5,sticky="w",pady=(8,0))
        ttk.Button(top,text="REFRESH STATUS",command=self.refresh_adventure).grid(row=6,column=5,sticky="e",pady=(8,0)); ttk.Button(top,text="START ADVENTURE",style="Accent.TButton",command=self.start_adventure).grid(row=6,column=6,sticky="e",pady=(8,0)); ttk.Button(top,text="STOP",command=self.stop_adventure).grid(row=6,column=7,sticky="e",pady=(8,0))
        self.adventure_scan_stop=threading.Event();self.adventure_scan_thread=None
        self.adventure_scan_status=tk.StringVar(value="Scan islands to preview server-confirmed stage progress. The selected mission stays your default farm.")
        ttk.Label(top,textvariable=self.adventure_scan_status,style="Muted.Card.TLabel",wraplength=850).grid(row=7,column=0,columnspan=7,sticky="w",pady=(8,0));ttk.Button(top,text="SCAN ISLANDS",command=self.scan_adventure_islands).grid(row=7,column=7,sticky="e",pady=(8,0))
        for i in (0,1,6,7): top.columnconfigure(i,weight=1)
        scan=ttk.Frame(self.adventure_tab,style="Card.TFrame",padding=8);scan.pack(fill="x",pady=(0,7))
        self.adventure_island_tree=ttk.Treeview(scan,columns=("island","progress","next"),show="headings",height=5)
        self.adventure_island_tree.tag_configure("pending",foreground="#ffbd69");self.adventure_island_tree.tag_configure("complete",foreground="#8ea4c1")
        for key,title,width in (("island","Available island",450),("progress","Verified progress",210),("next","Next stage",240)):
            self.adventure_island_tree.heading(key,text=title);self.adventure_island_tree.column(key,width=width,anchor="w")
        scroll=ttk.Scrollbar(scan,orient="vertical",command=self.adventure_island_tree.yview);self.adventure_island_tree.configure(yscrollcommand=scroll.set);scroll.pack(side="right",fill="y");self.adventure_island_tree.pack(fill="x",expand=True)
        self.adventure_log=self._logbox(self.adventure_tab,height=12,pady=(0,0)); self._append(self.adventure_log,"Adventure ready. Completion mode preserves the selected mission as the default farm target.")

    def _build_cm_reviews(self):
        self.cm_engine=None; self.cm_offers=[]; self.cm_review_rows=[]
        head=ttk.Frame(self.cm_tab,style="Card.TFrame",padding=12); head.pack(fill="x",pady=(10,8))
        ttk.Label(head,text="CM REVIEWS",style="Big.Card.TLabel").grid(row=0,column=0,columnspan=8,sticky="w")
        ttk.Label(head,text="A current-data review in the long-running 0–10 CM-review tradition: present skill magnitudes/power first, then a bounded recent-Reddit consensus check, plus PC relevance, your account context and stone-vs-gem value. Old community scores never override obvious current power creep.",style="Muted.Card.TLabel",wraplength=1080).grid(row=1,column=0,columnspan=8,sticky="w",pady=(2,9))
        self.cm_status=tk.StringVar(value="Load live data to begin.")
        ttk.Label(head,textvariable=self.cm_status,style="Card.TLabel").grid(row=2,column=0,columnspan=8,sticky="w",pady=(0,8))
        ttk.Label(head,text="Combo",style="Card.TLabel").grid(row=3,column=0,sticky="w")
        self.cm_query=tk.StringVar(value="")
        ent=ttk.Entry(head,textvariable=self.cm_query,width=42); ent.grid(row=4,column=0,columnspan=3,sticky="ew",padx=(0,6)); ent.bind("<Return>",lambda _e:self._cm_review_one())
        ttk.Button(head,text="REVIEW COMBO",command=self._cm_review_one,style="Accent.TButton").grid(row=4,column=3,padx=4)
        ttk.Button(head,text="REFRESH LIVE CM DATA",command=self._cm_refresh).grid(row=4,column=4,padx=4)
        ttk.Button(head,text="REVIEW LIVE SHOP",command=self._cm_review_shop).grid(row=4,column=5,padx=4)
        ttk.Button(head,text="START DISCORD BOT",command=self._cm_start_discord).grid(row=4,column=6,padx=4)
        ttk.Button(head,text="BOT SETUP",command=self._cm_bot_help).grid(row=4,column=7,padx=(4,0))
        for i in range(8):head.columnconfigure(i,weight=1 if i in (0,1,2) else 0)

        body=ttk.Frame(self.cm_tab); body.pack(fill="both",expand=True)
        left=ttk.Frame(body,style="Card.TFrame",padding=8); left.pack(side="left",fill="both",expand=True,padx=(0,5))
        right=ttk.Frame(body,style="Card.TFrame",padding=8); right.pack(side="left",fill="both",expand=True,padx=(5,0))
        ttk.Label(left,text="LIVE / REVIEWED COMBOS",style="Card.TLabel").pack(anchor="w",pady=(0,5))
        cols=("combo","trait","score","buy","cm","offer")
        self.cm_tree=ttk.Treeview(left,columns=cols,show="headings",height=16)
        heads=("Combo","Trait","Score","Recommendation","CM","Offer")
        widths=(205,95,55,210,55,110)
        for c,h,w in zip(cols,heads,widths):self.cm_tree.heading(c,text=h);self.cm_tree.column(c,width=w,anchor="w")
        self.cm_tree.pack(fill="both",expand=True); self.cm_tree.bind("<<TreeviewSelect>>",lambda _e:self._cm_show_selected())
        ttk.Label(right,text="REVIEW",style="Card.TLabel").pack(anchor="w",pady=(0,5))
        self.cm_detail=tk.Text(right,height=20,bg="#08111e",fg="#dce7f4",insertbackground="white",relief="flat",wrap="word",font=("Segoe UI",10));self.cm_detail.pack(fill="both",expand=True)
        self.cm_detail.insert("1.0","Load live data, then search a combo by name or review the live CM shop.\n\nDiscord: run the bot from this tab or Start_CM_Discord_Bot.bat. Commands are !cm <combo>, !cmshop, /cm and /cmshop.");self.cm_detail.configure(state="disabled")

    def _cm_set_detail(self,text):
        if not hasattr(self,"cm_detail"):return
        self.cm_detail.configure(state="normal");self.cm_detail.delete("1.0","end");self.cm_detail.insert("1.0",text);self.cm_detail.configure(state="disabled")

    def _cm_refresh(self,auto_shop=False):
        refresh_token=object();self._cm_refresh_token=refresh_token
        self.cm_status.set("Loading current AT card/mastery/shop data…")
        def work():
            try:
                eng,offers,_=collect_live_review_data(ATClient(load_credentials(),timeout=35))
                def done():
                    if self._cm_refresh_token is not refresh_token:return
                    self.cm_engine=eng;self.cm_offers=offers
                    self.cm_status.set(f"Loaded {len(eng.combos):,} current combo rows • {len(offers)} live CM offer(s) detected.")
                    if auto_shop:self._cm_review_shop(use_loaded=True)
                self.root.after(0,done)
            except Exception as exc:
                msg=str(exc)
                def failed():
                    if self._cm_refresh_token is not refresh_token:return
                    self.cm_engine=None;self.cm_offers=[]
                    self._cm_render_reviews([])
                    self._cm_set_detail("Live CM refresh failed. Previously loaded offers are stale; refresh again to get the current shop.")
                    self.cm_status.set(f"CM data refresh failed: {msg}")
                self.root.after(0,failed)
        threading.Thread(target=work,daemon=True).start()

    def _cm_render_reviews(self,rows):
        self.cm_review_rows=list(rows or [])
        for x in self.cm_tree.get_children():self.cm_tree.delete(x)
        for i,r in enumerate(self.cm_review_rows):
            offer=(f"{r.cost:,} {r.currency}" if r.cost is not None else (r.currency or "—"))
            self.cm_tree.insert("","end",iid=str(i),values=(r.name,r.trait or "—",f"{r.score:.1f}",r.recommendation,f"CM{r.current_cm}" if r.current_cm is not None else "—",offer))
        if self.cm_review_rows:
            self.cm_tree.selection_set("0");self._cm_show_selected()

    def _cm_show_selected(self):
        sel=self.cm_tree.selection() if hasattr(self,"cm_tree") else ()
        if not sel:return
        try:r=self.cm_review_rows[int(sel[0])]
        except Exception:return
        evidence="Review basis: all available AT Commander evidence — current AT/card data, current same-skill power benchmarks, account/mastery context when exposed, and recent Reddit/community ratings."
        lines=[evidence,"",r.compact(),"",f"Confidence: {r.confidence}"]
        if r.mastery_tokens is not None:lines.append(f"Owned mastery tokens: {r.mastery_tokens}")
        if r.community_score is not None:
            lines += ["",f"Recent Reddit consensus: {r.community_score:.1f}/10 from {r.community_count} explicit rating{'s' if r.community_count!=1 else ''} • {r.community_confidence or 'Low'} confidence"]
            if r.data_score is not None:lines.append(f"Current AT-data model before consensus: {r.data_score:.1f}/10")
            if r.community_note:lines.append(r.community_note)
            if r.community_sources:
                lines += ["Community evidence:"]+[f"• {x}" for x in r.community_sources[:5]]
        elif r.community_note:
            lines += ["","Community check:",r.community_note]
        if r.reasons:
            lines += ["","Why it scored there:"]+[f"• {x}" for x in r.reasons]
        if r.cautions:
            lines += ["","Cautions:"]+[f"• {x}" for x in r.cautions]
        lines += ["","AT Commander review engine: current card values first, recent community evidence second; old scores are context, not gospel."]
        self._cm_set_detail("\n".join(lines))

    def _cm_review_one(self):
        q=self.cm_query.get().strip()
        if not q:return messagebox.showinfo("CM Reviews","Type a combo name first.")
        if self.cm_engine is None:
            self.cm_status.set("Loading live data before reviewing…")
            def later():
                if self.cm_engine is not None:self._cm_review_one()
                elif "failed" not in self.cm_status.get().lower():self.root.after(300,later)
            self._cm_refresh();self.root.after(300,later);return
        try:r=self.cm_engine.review_name(q)
        except Exception as exc:
            matches=self.cm_engine.find(q)
            return messagebox.showinfo("CM Reviews",str(exc)+("" if not matches else "\n\nMatches: "+", ".join(str(x[1].get("name")) for x in matches[:10])))
        self._cm_render_reviews([r]);self.cm_status.set(f"Reviewed {r.name} from current AT data • checking recent Reddit consensus…")
        review_name=r.name;review_engine=self.cm_engine;review_token=getattr(self,"_cm_refresh_token",None)
        def community_work():
            consensus=fetch_reddit_consensus(review_name)
            def done():
                # Do not overwrite a newer review the user selected while the
                # network check was running.
                if (getattr(self,"_cm_refresh_token",None) is not review_token
                        or self.cm_engine is not review_engine or len(self.cm_review_rows)!=1
                        or self.cm_review_rows[0] is not r):return
                rr=review_engine.apply_community_consensus(r,consensus)
                self._cm_render_reviews([rr])
                if getattr(consensus,'status','')=="stale":
                    self.cm_status.set(f"Reviewed {rr.name} from current AT data • cached Reddit evidence shown; live lookup unavailable.")
                elif getattr(consensus,'score',None) is not None:
                    self.cm_status.set(f"Reviewed {rr.name}: current data + {getattr(consensus,'count',0)} recent/dated Reddit rating(s).")
                elif getattr(consensus,'error',''):
                    self.cm_status.set(f"Reviewed {rr.name} from current AT data • Reddit check unavailable ({getattr(consensus,'error','')}).")
                else:
                    self.cm_status.set(f"Reviewed {rr.name} from current AT data • no explicit recent Reddit rating found.")
            self.root.after(0,done)
        threading.Thread(target=community_work,daemon=True).start()

    def _cm_review_shop(self,use_loaded=False):
        if not use_loaded or self.cm_engine is None:
            return self._cm_refresh(auto_shop=True)
        rows=self.cm_engine.review_shop(self.cm_offers)
        if not rows:
            self.cm_status.set("Current AT payload did not expose recognizable priced CM shop rows. Individual combo review still works.")
            self._cm_set_detail("AT Commander could not safely map the live CM shop today. I am not guessing offer IDs or prices.\n\nUse the Combo box above for current-data individual reviews. If the shop payload changes, Refresh Live CM Data will pick it up automatically once the offer rows are exposed.")
            return
        self._cm_render_reviews(rows);self.cm_status.set(f"Reviewed {len(rows)} live CM shop offer(s) • checking recent Reddit consensus…")
        review_engine=self.cm_engine;expected=tuple(rows);review_token=getattr(self,"_cm_refresh_token",None)
        def community_work():
            from concurrent.futures import ThreadPoolExecutor
            names=list(dict.fromkeys(row.name for row in expected))
            def fetch(name):
                try:return name,fetch_reddit_consensus(name)
                except Exception:return name,None
            with ThreadPoolExecutor(max_workers=min(4,len(names))) as pool:
                consensus=dict(pool.map(fetch,names))
            def done():
                if (getattr(self,"_cm_refresh_token",None) is not review_token
                        or self.cm_engine is not review_engine or len(self.cm_review_rows)!=len(expected)
                        or any(current is not original for current,original in zip(self.cm_review_rows,expected))):return
                selected=None
                if hasattr(self,"cm_tree"):
                    selection=self.cm_tree.selection()
                    try:
                        row=self.cm_review_rows[int(selection[0])]
                        selected=(row.combo_id,row.currency,row.cost)
                    except (IndexError,ValueError):pass
                checked=[review_engine.apply_community_consensus(row,consensus.get(row.name)) for row in expected]
                checked.sort(key=lambda row:(-row.score,row.name.lower()))
                self._cm_render_reviews(checked)
                if selected is not None:
                    index=next((i for i,row in enumerate(checked) if (row.combo_id,row.currency,row.cost)==selected),None)
                    if index is not None:self.cm_tree.selection_set(str(index));self._cm_show_selected()
                evidence=sum(1 for row in checked if row.community_score is not None)
                stale=sum(1 for value in consensus.values() if getattr(value,"status","")=="stale")
                unavailable=sum(1 for value in consensus.values() if value is None or getattr(value,"status","")=="unavailable")
                limits=(f" • {stale} use cached evidence" if stale else "")+(f" • {unavailable} Reddit lookup(s) unavailable" if unavailable else "")
                self.cm_status.set(f"Reviewed {len(checked)} live CM shop offer(s) • community ratings found for {evidence} combo(s)"+limits+".")
            self.root.after(0,done)
        threading.Thread(target=community_work,daemon=True).start()

    def _cm_start_discord(self):
        rootdir=Path(__file__).resolve().parents[1];bat=rootdir/"Start_CM_Discord_Bot.bat"
        if not bat.exists():return messagebox.showerror("CM Discord bot","Start_CM_Discord_Bot.bat is missing from this build.")
        try:
            os.startfile(str(bat));self.cm_status.set("Discord CM bot launcher opened in a separate console.")
        except Exception as exc:messagebox.showerror("CM Discord bot",f"Could not launch bot:\n{exc}")

    def _cm_bot_help(self):
        messagebox.showinfo("CM Discord bot setup","1. Create a bot application in Discord's Developer Portal and invite it to your server with bot + application.commands scopes.\n\n2. Enable Message Content Intent if you want !cm prefix commands. Slash /cm works without that prefix intent after command sync.\n\n3. Click START DISCORD BOT. On the first run, paste the bot token into the console when asked. AT Commander saves it in Windows Credential Manager via keyring; it is not written into the ZIP/source.\n\n4. Commands: !cm <combo name>, !cmshop, /cm, /cmshop. You can also @mention the bot followed by a combo name (for example: @AT Commander Wingnut Amy).\n\nThe bot must be running on this PC (or another machine with AT Commander credentials) to answer Discord requests.")

    def _build_inventory(self):
        rewards=ttk.Frame(self.inventory_tab,padding=(8,5));rewards.pack(fill="x")
        ttk.Label(rewards,text="Earned boxes & milestones").pack(side="left")
        self._build_reward_controls(rewards)
        bar=ttk.Frame(self.inventory_tab); bar.pack(fill="x",pady=(10,7)); ttk.Label(bar,text="Current account inventory and resources",style="Header.TLabel").pack(side="left"); ttk.Button(bar,text="Refresh",command=self.refresh_inventory).pack(side="right")
        self.show_unknown_items=tk.BooleanVar(value=False); ttk.Checkbutton(bar,text="Show unresolved IDs",variable=self.show_unknown_items,command=self._render_inventory).pack(side="right",padx=(0,10))
        f=ttk.Frame(self.inventory_tab,style="Card.TFrame",padding=7); f.pack(fill="both",expand=True); cols=("type","name","count","id"); self.inventory_tree=ttk.Treeview(f,columns=cols,displaycolumns=("type","name","count"),show="headings",selectmode="browse")
        for c,t,w,a in [("type","Type",145,"w"),("name","Item / Resource",500,"w"),("count","Current",130,"e"),("id","Internal ID",1,"w")]: self.inventory_tree.heading(c,text=t); self.inventory_tree.column(c,width=w,anchor=a)
        vs=ttk.Scrollbar(f,orient="vertical",command=self.inventory_tree.yview); self.inventory_tree.configure(yscrollcommand=vs.set); self.inventory_tree.pack(side="left",fill="both",expand=True); vs.pack(side="right",fill="y")

        pack=ttk.Frame(self.inventory_tab,style="Card.TFrame",padding=9); pack.pack(fill="x",pady=(8,0)); ttk.Label(pack,text="Spend Nixons:",style="Card.TLabel").pack(side="left"); self.nixon_pack_var=tk.StringVar(value="Basic Pack — 1,000 Nixons [ID 1]"); self.nixon_pack_combo=ttk.Combobox(pack,textvariable=self.nixon_pack_var,state="readonly",width=37,values=["Basic Pack — 1,000 Nixons [ID 1]","Giggitywatts — 3,000 Nixons [ID 83]","Rare Basic Pack — 5,000 Nixons [ID 65]","Epic Basic Pack — 50,000 Nixons [ID 66]"]); self.nixon_pack_combo.pack(side="left",padx=(6,8)); ttk.Label(pack,text="Qty",style="Card.TLabel").pack(side="left"); self.nixon_qty=tk.StringVar(value="1"); ttk.Entry(pack,textvariable=self.nixon_qty,width=6).pack(side="left",padx=(4,8)); self.nixon_buy_max=tk.BooleanVar(value=False); ttk.Checkbutton(pack,text="Max affordable",variable=self.nixon_buy_max).pack(side="left",padx=(0,7)); ttk.Label(pack,text="Parallel",style="Card.TLabel").pack(side="left",padx=(2,3)); self.nixon_workers=tk.IntVar(value=int(self.settings.get("nixon_workers",6) or 6)); ttk.Spinbox(pack,from_=1,to=12,textvariable=self.nixon_workers,width=3).pack(side="left",padx=(0,7)); self.nixon_recycle_after=tk.BooleanVar(value=False); ttk.Checkbutton(pack,text="Recycle trash after",variable=self.nixon_recycle_after).pack(side="left"); ttk.Button(pack,text="BUY",command=self._spend_nixons).pack(side="right")

        recycle=ttk.Frame(self.inventory_tab,style="Card.TFrame",padding=9); recycle.pack(fill="x",pady=(6,0)); ttk.Label(recycle,text="Recycle loose level-1 cards:",style="Card.TLabel").pack(side="left"); self.recycle_common=tk.BooleanVar(value=True); self.recycle_rare=tk.BooleanVar(value=True); self.recycle_epic=tk.BooleanVar(value=True); ttk.Checkbutton(recycle,text="Common",variable=self.recycle_common).pack(side="left",padx=(8,2)); ttk.Checkbutton(recycle,text="Rare",variable=self.recycle_rare).pack(side="left",padx=2); ttk.Checkbutton(recycle,text="Epic",variable=self.recycle_epic).pack(side="left",padx=2); ttk.Label(recycle,text="Saved-deck cards + foils are always protected.",style="Muted.Card.TLabel").pack(side="left",padx=(10,0)); ttk.Button(recycle,text="RECYCLE TRASH",command=self._recycle_trash).pack(side="right")

        actions=ttk.Frame(self.inventory_tab,style="Card.TFrame",padding=9); actions.pack(fill="x",pady=(6,0)); ttk.Button(actions,text="CLAIM ALL MAIL",command=self._claim_all_mail).pack(side="left",padx=(0,5)); ttk.Button(actions,text="DELETE SAFE MAIL",command=self._delete_safe_mail).pack(side="left",padx=5); ttk.Button(actions,text="SPEND SELECTED ITEM",command=self._spend_selected).pack(side="left",padx=5); self.resolve_choice_btn=ttk.Button(actions,text="RESOLVE ITEM CHOICE",command=self._resolve_item_choice,state="disabled"); self.resolve_choice_btn.pack(side="left",padx=5); ttk.Button(actions,text="DO 3 DAILY UPGRADES",command=self._do_three_upgrades).pack(side="left",padx=5)
        self.auto_item_choice=tk.BooleanVar(value=bool(self.settings.get("auto_item_choice",True)))
        ttk.Checkbutton(self.inventory_tab,text="Automatically select choice rewards: Rogu → Characters → Items / Power Cards",variable=self.auto_item_choice,command=self._save_settings).pack(anchor="w",pady=(5,0))
        self.item_action_var=tk.StringVar(value="Readable resources are shown by name; unresolved raw IDs stay hidden unless you ask to see them."); ttk.Label(self.inventory_tab,textvariable=self.item_action_var).pack(anchor="w",pady=(6,0)); self.inventory_progress_var=tk.DoubleVar(value=0.0); self.inventory_progress_text=tk.StringVar(value=""); pf=ttk.Frame(self.inventory_tab); pf.pack(fill="x",pady=(3,0)); self.inventory_progress=ttk.Progressbar(pf,variable=self.inventory_progress_var,maximum=100); self.inventory_progress.pack(side="left",fill="x",expand=True); ttk.Label(pf,textvariable=self.inventory_progress_text,width=28).pack(side="left",padx=(8,0))

    def _build_stats(self):
        nb=ttk.Notebook(self.stats_tab); nb.pack(fill="both",expand=True,pady=(10,0)); self.live_stats=ttk.Frame(nb); self.section_stats=ttk.Frame(nb); self.total_stats=ttk.Frame(nb); self.progress_stats=ttk.Frame(nb); nb.add(self.live_stats,text="Account Snapshot"); nb.add(self.section_stats,text="Current Section"); nb.add(self.total_stats,text="Total Gains"); nb.add(self.progress_stats,text="Ladder / Hero Tokens")
        self.live_tree=self._make_simple_inventory(self.live_stats)
        self.stats_summary_var=tk.StringVar(value="No active section yet."); self.lifetime_summary_var=tk.StringVar(value="Persistent totals load from previous runs."); self.section_tree,self.changed_only_var,self.search_var=self._make_stats_view(self.section_stats,self.stats_summary_var,True); self.lifetime_tree,self.life_changed_only_var,self.life_search_var=self._make_stats_view(self.total_stats,self.lifetime_summary_var,True)
        top=ttk.Frame(self.progress_stats,style="Card.TFrame",padding=10); top.pack(fill="x",pady=(7,7)); self.trophy_detail_var=tk.StringVar(value="Arena ladder: locating correct AT field…"); ttk.Label(top,textvariable=self.trophy_detail_var,style="Big.Card.TLabel").pack(anchor="w"); ttk.Label(top,text="Hero medals/tokens are shown by hero where the API exposes a structured counter. Ambiguous raw fields are kept in Diagnostics instead of cluttering this page.",style="Muted.Card.TLabel").pack(anchor="w",pady=(3,0))
        pf=ttk.Frame(self.progress_stats,style="Card.TFrame",padding=7); pf.pack(fill="both",expand=True); self.medal_tree=ttk.Treeview(pf,columns=("hero","count","field"),show="headings"); self.medal_tree.heading("hero",text="Hero"); self.medal_tree.heading("count",text="Medals / Tokens"); self.medal_tree.heading("field",text="Source"); self.medal_tree.column("hero",width=260); self.medal_tree.column("count",width=140,anchor="e"); self.medal_tree.column("field",width=600); self.medal_tree.pack(fill="both",expand=True)

    def _build_diag(self):
        ads=ttk.Frame(self.diag_tab,style="Card.TFrame",padding=10); ads.pack(fill="x",pady=(10,0))
        ttk.Label(ads,text="LEGITIMATE REWARDED ADS",style="Big.Card.TLabel").pack(side="left",padx=(0,20))
        for text,command in [("OPEN GAME",self._open_learning_browser),("WATCH BOOST",self.watch_ads),("WATCH RESEARCH AD",self._watch_research_ad),("LEGACY MOM SETUP",lambda:self.calibrate_ads("mom"))]:ttk.Button(ads,text=text,command=command).pack(side="left",padx=4)
        account=ttk.Frame(self.diag_tab,style="Card.TFrame",padding=10); account.pack(fill="x",pady=(10,7)); creds=load_credentials(); self.diag_uid=tk.StringVar(value=creds.user_id or "Not saved"); self.diag_hash=tk.StringVar(value="Saved ••••••••" if creds.password_hash else "Not saved"); ttk.Label(account,text="ACCOUNT",style="Big.Card.TLabel").grid(row=0,column=0,columnspan=4,sticky="w"); ttk.Label(account,text="User ID",style="Card.TLabel").grid(row=1,column=0,sticky="w"); ttk.Label(account,textvariable=self.diag_uid,style="Card.TLabel").grid(row=1,column=1,sticky="w",padx=(8,18)); ttk.Label(account,text="Login hash",style="Card.TLabel").grid(row=1,column=2,sticky="w"); ttk.Label(account,textvariable=self.diag_hash,style="Card.TLabel").grid(row=1,column=3,sticky="w",padx=(8,18)); ttk.Button(account,text="Save / Replace Credentials",command=self._save_credentials_gui).grid(row=1,column=4,padx=4); ttk.Button(account,text="Refresh Account Data",command=self._refresh_account_metadata).grid(row=1,column=5,padx=4); ttk.Button(account,text="Export selector metadata",command=self.export_selector_metadata).grid(row=1,column=6,padx=4); ttk.Button(account,text="RUN SAFE SWOLE PROTOCOL LAB",command=self._run_swole_protocol_lab).grid(row=1,column=7,padx=4)

        browser=ttk.Frame(self.diag_tab,style="Card.TFrame",padding=10); browser.pack(fill="x",pady=(0,7)); self.learn_browser_status=tk.StringVar(value="Learning Browser: closed — open it once, log in once, then reuse it for every action."); ttk.Label(browser,text="LEARNING BROWSER",style="Big.Card.TLabel").grid(row=0,column=0,columnspan=3,sticky="w"); ttk.Label(browser,textvariable=self.learn_browser_status,style="Muted.Card.TLabel",wraplength=800).grid(row=1,column=0,sticky="w",padx=(0,10)); ttk.Button(browser,text="OPEN / REUSE BROWSER",command=self._open_learning_browser,style="Accent.TButton").grid(row=1,column=1,padx=4); ttk.Button(browser,text="BACKGROUND GAME",command=self._background_learning_browser).grid(row=1,column=2,padx=4); ttk.Button(browser,text="CLOSE BROWSER",command=self._close_learning_browser).grid(row=1,column=3,padx=4); browser.columnconfigure(0,weight=1)

        learn=ttk.Frame(self.diag_tab,style="Card.TFrame",padding=8); learn.pack(fill="both",expand=True); self.learn_tree=ttk.Treeview(learn,columns=("action","status","what"),show="headings",height=13); self.learn_tree.heading("action",text="Action"); self.learn_tree.heading("status",text="Status"); self.learn_tree.heading("what",text="What you do once"); self.learn_tree.column("action",width=190); self.learn_tree.column("status",width=150); self.learn_tree.column("what",width=700); self.learn_tree.pack(fill="both",expand=True); lb=ttk.Frame(self.diag_tab); lb.pack(fill="x",pady=(7,0)); ttk.Button(lb,text="START CAPTURE",command=self.start_learning,style="Accent.TButton").pack(side="left"); ttk.Button(lb,text="FINISH CAPTURE",command=self.finish_learning).pack(side="left",padx=6); ttk.Button(lb,text="REVIEW LAST CAPTURE",command=self.review_last_capture).pack(side="left",padx=6); ttk.Button(lb,text="Open learned-data folder",command=self._open_app_dir).pack(side="left"); self.learn_status=tk.StringVar(value="1) Open Learning Browser once. 2) Log in once if needed. 3) Select an action and Start Capture. 4) Perform only that action in the SAME browser. 5) Finish Capture."); ttk.Label(lb,textvariable=self.learn_status).pack(side="left",padx=12)
        self.learn_tree.heading("what",text="Setup / current behavior")
        self.learn_status.set("Boost and running TV-chamber research ads run automatically; no calibration. Only actions marked NEEDS LEARNING need a capture.")
        self.diag_log=self._logbox(self.diag_tab,height=8,pady=(7,0)); self._refresh_protocol_status()

    def _watch_research_ad(self):
        if self.command_busy or any(getattr(runner,'running',False) for runner in (self.arena_runner,self.event_runner,self.adventure_runner,self.swole_runner)):
            return messagebox.showinfo('Research ad','Stop the active runner before starting a research ad.')
        self.nb.select(self.commands_tab)
        self._command_launch([('research-ad',{})],False)

    def _logbox(self,parent,height=14,pady=(0,0)):
        return logbox(parent,height,pady)

    def _make_simple_inventory(self,parent):
        bar=ttk.Frame(parent); bar.pack(fill="x",pady=(7,6)); ttk.Label(bar,text="Live counts from the latest refresh").pack(side="left"); ttk.Button(bar,text="Refresh Now",command=self.refresh_inventory).pack(side="right"); f=ttk.Frame(parent,style="Card.TFrame",padding=7); f.pack(fill="both",expand=True); tree=ttk.Treeview(f,columns=("type","name","count"),show="headings"); tree.heading("type",text="Type"); tree.heading("name",text="Item / Resource / Card"); tree.heading("count",text="Current"); tree.column("type",width=100); tree.column("name",width=600); tree.column("count",width=130,anchor="e"); tree.pack(fill="both",expand=True); return tree

    def _make_stats_view(self,parent,summary_var,changed_default):
        bar=ttk.Frame(parent); bar.pack(fill="x",pady=(7,6)); ttk.Label(bar,textvariable=summary_var).pack(side="left"); ttk.Button(bar,text="Refresh Now",command=self.refresh_inventory).pack(side="right"); filters=ttk.Frame(parent); filters.pack(fill="x",pady=(0,6)); changed=tk.BooleanVar(value=changed_default); search=tk.StringVar(); ttk.Checkbutton(filters,text="Changed only",variable=changed,command=self._render_stats).pack(side="left"); ttk.Label(filters,text="Search:").pack(side="left",padx=(16,5)); search.trace_add("write",lambda *_:self._render_stats()); ttk.Entry(filters,textvariable=search,width=28).pack(side="left"); f=ttk.Frame(parent,style="Card.TFrame",padding=7); f.pack(fill="both",expand=True); cols=("type","name","start","current","change"); tree=ttk.Treeview(f,columns=cols,show="headings");
        for c,t,w,a in [("type","Type",95,"w"),("name","Item / Resource / Card",460,"w"),("start","Start",105,"e"),("current","Current",105,"e"),("change","Change",105,"e")]:tree.heading(c,text=t);tree.column(c,width=w,anchor=a)
        tree.pack(fill="both",expand=True); tree.tag_configure("gain",foreground="#7ee787"); tree.tag_configure("loss",foreground="#ff8a8a"); return tree,changed,search

    def _swole_add(self):
        hid=self._selected_id(self.swole_target.get(),self.hero_rows)
        if not hid: messagebox.showinfo("Swole","Choose a named hero. If names are missing, use Diagnostics / Learn → Export selector metadata."); return
        row=next((r for r in self.hero_rows if str(r.get("id"))==str(hid)),{}); level=max(1,int(self.swole_level.get() or 1)); entry={"id":str(hid),"name":row.get("name") or f"Hero {hid}","target_level":level}; self.swole_plan.append(entry); self.swole_queue_list.insert("end",f"{entry['name']}  →  level {level}")
    def _swole_remove(self):
        x=self.swole_queue_list.curselection();
        if x:i=x[0];self.swole_queue_list.delete(i);self.swole_plan.pop(i)
    def _swole_move(self,d):
        x=self.swole_queue_list.curselection();
        if not x:return
        i=x[0];j=i+d
        if j<0 or j>=len(self.swole_plan):return
        self.swole_plan[i],self.swole_plan[j]=self.swole_plan[j],self.swole_plan[i]; self._render_swole_queue(); self.swole_queue_list.selection_set(j)
    def _swole_clear(self): self.swole_plan=[]; self._render_swole_queue()
    def _swole_add_fights(self):
        n=simpledialog.askinteger("Swole fights","How many Swole attacks at this point in the schedule?",minvalue=1,maxvalue=100)
        if n:self.swole_plan.append({"type":"fight","count":int(n)});self._render_swole_queue()
    def _swole_import_schedule(self):
        win=tk.Toplevel(self.root);win.title("Paste Swole schedule");win.geometry("760x620")
        ttk.Label(win,text="Accepted examples: HERO #1 = Peter  |  DAY 1  |  TRAIN HERO #1 0->1  |  TRAIN Stewie -> 3  |  ATK 4 TIMES  |  WAIT TRAINING  |  WAIT 13h. This is designed for schedules like your day-by-day spreadsheet.",wraplength=710).pack(anchor="w",padx=12,pady=(12,6))
        box=tk.Text(win,height=24);box.pack(fill="both",expand=True,padx=12,pady=6)
        def parse():
            import re
            name_map={str(r.get("name","")).strip().lower():r for r in self.hero_rows}
            aliases={}; added=[]; errors=[]
            lines=[x.strip() for x in box.get("1.0","end").splitlines()]
            # First pass: optional HERO #N = Actual Name aliases.
            for line in lines:
                m=re.match(r"(?i)^hero\s*#?\s*(\d+)\s*=\s*(.+?)\s*$",line)
                if not m: continue
                row=name_map.get(m.group(2).strip().lower())
                if row: aliases[m.group(1)]=row
                else: errors.append(line)
            for line in lines:
                if not line or line.startswith("#"):continue
                if re.match(r"(?i)^hero\s*#?\s*\d+\s*=",line):continue
                m=re.match(r"(?i)^day\s+(.+)$",line)
                if m: added.append({"type":"label","text":f"DAY {m.group(1).strip()}"});continue
                m=re.match(r"(?i)^(?:fight|atk|attack)\s*[x:]?\s*(\d+)(?:\s+times?)?\s*$",line)
                if m: added.append({"type":"fight","count":int(m.group(1))});continue
                if re.match(r"(?i)^wait\s+(?:training|train|until\s+training(?:\s+finishes?)?)\s*$",line):
                    added.append({"type":"wait_training"});continue
                m=re.match(r"(?i)^wait\s+(\d+(?:\.\d+)?)\s*(s|sec|secs|m|min|mins|h|hr|hrs|hour|hours)$",line)
                if m:
                    val=float(m.group(1)); unit=m.group(2).lower(); mult=1 if unit.startswith("s") else 60 if unit.startswith("m") else 3600
                    added.append({"type":"wait","seconds":max(1,int(val*mult)),"label":line[5:].strip()});continue

                # TRAIN hero#1 0->1 / TRAIN Peter -> 3 / Peter 5
                m=re.match(r"(?i)^train\s+hero\s*#?\s*(\d+)(?:\s+\d+)?\s*(?:[-=]?>)\s*(\d+)\s*$",line)
                if m:
                    row=aliases.get(m.group(1)); target=int(m.group(2))
                    if row: added.append({"type":"train","id":str(row.get("id")),"name":row.get("name"),"target_level":target})
                    else: errors.append(line)
                    continue
                m=re.match(r"(?i)^train\s+(.+?)\s*(?:[-=]?>\s*|\s+)(\d+)\s*$",line)
                if not m: m=re.match(r"^(.+?)(?:\s*[-=]?>\s*|\s+)(\d+)\s*$",line)
                if not m: errors.append(line);continue
                nm=m.group(1).strip();target=int(m.group(2)); row=name_map.get(nm.lower())
                if not row: errors.append(line);continue
                added.append({"type":"train","id":str(row.get("id")),"name":row.get("name"),"target_level":target})
            if added:self.swole_plan.extend(added);self._render_swole_queue()
            win.destroy()
            if errors:messagebox.showinfo("Swole schedule",f"Added {len(added)} steps. Could not parse: "+"; ".join(errors[:8]))
        ttk.Button(win,text="ADD TO SCHEDULE",command=parse).pack(pady=(0,12))

    def _render_swole_queue(self):
        self.swole_queue_list.delete(0,"end")
        for e in self.swole_plan:
            typ=e.get("type","train")
            if typ=="fight": self.swole_queue_list.insert("end",f"FIGHT × {int(e.get('count') or 1)}")
            elif typ=="label": self.swole_queue_list.insert("end",f"──── {e.get('text','DAY')} ────")
            elif typ=="wait": self.swole_queue_list.insert("end",f"WAIT {e.get('label') or str(e.get('seconds'))+'s'}")
            elif typ=="wait_training": self.swole_queue_list.insert("end","WAIT UNTIL ACTIVE TRAINING FINISHES")
            else:self.swole_queue_list.insert("end",f"{e.get('name',e.get('id'))}  →  level {e.get('target_level',1)}")

    def _refresh_account_badge(self):
        c=load_credentials(); self.account_var.set("Account: connected" if c.ready else "Account: credentials missing")
        if hasattr(self,"diag_uid"): self.diag_uid.set(c.user_id or "Not saved"); self.diag_hash.set("Saved ••••••••" if c.password_hash else "Not saved")

    def _refresh_account_metadata(self, force_adventure_static: bool=False):
        if getattr(self,"_closing",False):return
        # Ordinary metadata refresh remains available during automation. Only
        # the explicit Adventure refresh can need browser input; reserve the
        # existing startup worker before any background catalog reads begin.
        browser_reserved=bool(force_adventure_static) and not CommandCenterMixin._browser_action_busy(self)
        if force_adventure_static and not browser_reserved:
            self.events.put({"kind":"log","message":"Adventure Refresh: account metadata will refresh; browser map discovery is deferred until active work finishes."})
        if browser_reserved:
            if not hasattr(self,"game_panel_stop"):self.game_panel_stop=threading.Event()
            self.game_panel_stop.clear()
        def work():
            try:
                c=ATClient(load_credentials())
                init=c.init()
                try: account=c.get_user_account()
                except Exception: account={}

                # Merge compact account/status data into the larger init snapshot.
                # getUserAccount is the confirmed source for user_tasks/common_fields,
                # while init is still the best source for inventory containers.
                merged=dict(init or {})
                for key in ("user_data","common_fields"):
                    a=(account.get(key) or {}) if isinstance(account,dict) else {}
                    b=(merged.get(key) or {}) if isinstance(merged.get(key),dict) else {}
                    merged[key]={**b,**a}
                if isinstance(account,dict) and account.get("user_tasks"):
                    merged["user_tasks"]=account.get("user_tasks")
                elif init.get("user_tasks") is not None:
                    merged["user_tasks"]=init.get("user_tasks") or {}
                if isinstance(account,dict) and account.get("time"):
                    merged["time"]=account.get("time")

                cardcat={}; itemcat={}; herocat={}; campaigncat={}; missioncat={}
                deck_data=c.get_exact_decks(init)
                hero_data=c.get_hero_squad()
                # Saved decks and unlocked heroes are already authoritative.
                # Publish them before optional catalog downloads delay the full
                # account/inventory refresh; later metadata enriches names by ID.
                selectors={"kind":"selectors"}
                if isinstance(deck_data,dict) and deck_data.get("result") is not False:
                    selectors["decks"]=exact_decks(deck_data)
                if isinstance(hero_data,dict) and hero_data.get("result") is not False:
                    selectors["heroes"]=unlocked_heroes(hero_data)
                if len(selectors)>1:self.events.put(selectors)
                try: boost_data=c.get_boost_level()
                except Exception: boost_data=merged
                try: cardcat=c.card_catalog()
                except Exception: pass
                try: itemcat=c.item_catalog()
                except Exception: pass
                try: herocat=c.hero_catalog()
                except Exception: pass
                try: campaigncat=c.campaign_catalog()
                except Exception: pass
                try: missioncat=c.mission_catalog()
                except Exception: pass

                # Store metadata is exposed by a confirmed no-op setDeckCommander.
                # This is what gives the Nixon dropdown real live pack names/costs.
                try: store_data=c.get_store_snapshot(deck_data)
                except Exception: store_data={}

                def harvest_item_names(o,depth=0):
                    if depth>8:return
                    if isinstance(o,dict):
                        iid=o.get("item_id"); name=o.get("name")
                        if iid is not None and name:itemcat.setdefault(str(iid),{"id":iid,"name":name})
                        for v in o.values():harvest_item_names(v,depth+1)
                    elif isinstance(o,list):
                        for v in o:harvest_item_names(v,depth+1)
                harvest_item_names(store_data)
                harvest_item_names(deck_data)

                decks=exact_decks(deck_data)
                heroes=unlocked_heroes(hero_data,herocat)
                try: siege_raw=c.get_guild_siege_status()
                except Exception: siege_raw={}
                siege_info=parse_siege_status(siege_raw)
                islands=siege_info.get("friendly") or discover_siege_islands(merged)
                siege_targets=siege_info.get("enemy") or []
                progress=scan_progress_metrics(account or merged) or scan_progress_metrics(deck_data)
                medals=find_hero_medals(hero_data,heroes)
                ad=find_ad_state(boost_data)
                energy,energy_path=find_arena_energy(account or merged)
                if energy is None: energy,energy_path=find_arena_energy(merged)
                snap=build_inventory_snapshot(merged,cardcat,itemcat)
                choices=extract_item_choices(merged,cardcat)
                self.events.put({"kind":"item_choices","options":choices,"message":f"{len(choices)} pending reward option(s)." if choices else "No pending reward choices."})

                swole_raw=dict(hero_data or {})
                swole_raw["user_tasks"]=(account.get("user_tasks") or init.get("user_tasks") or merged.get("user_tasks") or {}) if isinstance(account,dict) else (init.get("user_tasks") or merged.get("user_tasks") or {})
                swole_raw["time"]=(account.get("time") if isinstance(account,dict) else None) or init.get("time") or merged.get("time") or time.time()

                adv_current,adv_max,adv_recharge=find_adventure_energy(merged)
                challenge_rows=discover_challenge_events(merged)
                campaign_markers=discover_campaign_markers(merged)
                # Adventure Refresh may explicitly prime the real web client so
                # we can read the exact mission/campaign JSON URLs from its
                # Performance resource list. This avoids guessing CDN filenames.
                if browser_reserved and campaign_markers and not missioncat:
                    try:
                        if self.game_panel_stop.is_set() or CommandCenterMixin._browser_action_busy(self,ignore_thread=worker):
                            self.events.put({"kind":"log","message":"Adventure Refresh: browser map discovery deferred; active work or Stop takes precedence."})
                            prep={"ok":False}
                        else:
                            self.events.put({"kind":"log","message":"Adventure Refresh: mission map is not cached; opening/reusing the fixed game window once so Commander can discover AT's real mission/campaign resource URLs…"})
                            prep=prepare_game_ui(lambda m:self.events.put({"kind":"log","message":m}),wait_for_canvas=25,should_stop=self.game_panel_stop.is_set)
                        if prep.get("ok"):
                            try: missioncat=c.mission_catalog()
                            except Exception: pass
                            try: campaigncat=c.campaign_catalog()
                            except Exception: pass
                            self.events.put({"kind":"log","message":f"Adventure static-map discovery: missions={len(missioncat) if isinstance(missioncat,dict) else 0}, campaigns={len(campaigncat) if isinstance(campaigncat,dict) else 0}."})
                    except Exception:
                        self.events.put({"kind":"log","message":"Adventure static-map browser discovery could not be confirmed. Browser input was not repeated."})
                campaign_rows=build_adventure_campaign_rows(campaigncat,missioncat,campaign_markers,merged.get('active_events'),self.settings.get('adventure_event_missions'))

                packs=[]
                raw_store=(store_data.get("store_items") if isinstance(store_data,dict) else None) or (deck_data.get("store_items") if isinstance(deck_data,dict) else None) or merged.get("store_items") or []
                rows=raw_store.values() if isinstance(raw_store,dict) else raw_store if isinstance(raw_store,list) else []
                for row in rows:
                    if not isinstance(row,dict): continue
                    try: cost=int(row.get("sc_cost") or 0)
                    except Exception: cost=0
                    if cost>0 and row.get("id") is not None:
                        packs.append({"id":str(row.get("id")),"name":str(row.get("name") or f"Store item {row.get('id')}"),"cost":cost})
                packs.sort(key=lambda x:(x["cost"],x["name"]))
                self.events.put({"kind":"metadata","swole_raw":swole_raw,"decks":decks,"heroes":heroes,"islands":islands,"siege_targets":siege_targets,"siege_phase":siege_info.get("phase"),"siege_attacks":siege_info.get("num_attacks"),"siege_max_attacks":siege_info.get("max_attacks"),"progress":progress,"medals":medals,"ad":ad,"arena_energy":energy,"arena_energy_path":energy_path,"adventure_energy":adv_current,"adventure_max_energy":adv_max,"adventure_recharge":adv_recharge,"snapshot":snap,"nixon_packs":packs,"challenges":challenge_rows,"campaigns":campaign_rows})
            except Exception as e:self.events.put({"kind":"log","message":f"Account metadata refresh skipped: {e}"})
        worker=threading.Thread(target=work,daemon=True)
        if browser_reserved:self.game_panel_thread=worker
        worker.start()

    @staticmethod
    def _adventure_label_after_refresh(previous,labels,previous_row=None,rows=None):
        if previous in labels:return previous
        identity=re.search(r"\[(Campaign|Key)\s+([^\]]+)\]",previous)
        if identity:
            for label in labels:
                candidate=re.search(r"\[(Campaign|Key)\s+([^\]]+)\]",label)
                if candidate and candidate.groups()==identity.groups():return label
        if isinstance(previous_row,dict):
            marker=str(previous_row.get("marker") or "")
            matches=[row for row in (rows or []) if marker and str(row.get("id"))==marker
                     and row.get("source") in {"static","mission_data_dynamic"}]
            if len(matches)==1:
                mapped=[label for label in labels if f"[Campaign {marker}]" in label]
                if len(mapped)==1:return mapped[0]
        if previous not in {"Main Adventure","Custom / Event mission ID"}:
            old_name=previous.split(":",1)[-1].split(" •",1)[0].strip()
            old_name=re.sub(r"\s*\[(?:Campaign|Key)\s+[^\]]+\]$","",old_name).strip()
            matches=[label for label in labels if old_name and old_name in label]
            if len(matches)==1:return matches[0]
        return "Main Adventure"

    def _display_options(self,rows):
        return ["Current / unchanged"]+[f"{r['name']}  [ID {r['id']}]" for r in rows if str(r.get("resolved","1"))=="1" and not str(r.get("name","")).startswith("Unresolved ID")]

    def _apply_account_selectors(self,decks=None,heroes=None):
        """Populate only account selectors, retaining the selected stable ID."""
        def apply(rows,bindings,default="Current / unchanged"):
            labels=self._display_options(rows)
            if default!="Current / unchanged":labels=[default]+labels[1:]
            for widget_name,variable_name in bindings:
                widget=getattr(self,widget_name,None);variable=getattr(self,variable_name,None)
                if widget is None or variable is None:continue
                previous=variable.get();identity=re.search(r"\[ID\s+([^\]]+)\]",previous)
                deck_binding=dict(DECK_BINDINGS).get(variable_name)
                if deck_binding:
                    prior_id,prior_name=label_parts(previous)
                    chosen=resolve_deck(prior_id or self.settings.get(deck_binding),prior_name or self.settings.get(saved_name_key(deck_binding)),rows)
                    identity=re.search(r"\[ID\s+([^\]]+)\]",f"[ID {chosen}]") if chosen else None
                widget["values"]=labels
                matching=next((label for label in labels if identity and f"[ID {identity.group(1)}]" in label),None)
                variable.set(matching or (previous if previous in labels else default))
        if decks is not None:
            self.deck_rows=sorted(decks,key=lambda row:(str(row.get("name","")).casefold(),str(row.get("id",""))))
            apply(self.deck_rows,(("deck_combo","deck_var"),("rumble_deck_combo","rumble_deck"),
                ("siege_deck_combo","siege_deck"),("rumble_offense_deck_combo","rumble_offense_deck"),
                ("siege_offense_deck_combo","siege_offense_deck"),("challenge_offense_deck_combo","challenge_offense_deck"),("builder_slot_combo","builder_slot")))
        if heroes is not None:
            self.hero_rows=list(heroes)
            apply(self.hero_rows,(("hero_combo","hero_var"),("rumble_offense_hero_combo","rumble_offense_hero"),
                ("siege_offense_hero_combo","siege_offense_hero"),("challenge_offense_hero_combo","challenge_offense_hero")))
            apply(self.hero_rows,(("swole_target_combo","swole_target"),),"Select hero…")

    def _selected_id(self,label,rows):
        if not label or label.startswith("Current / unchanged") or label.startswith("Select hero"):return None
        if rows is getattr(self,"deck_rows",None):
            identity,name=label_parts(label)
            return resolve_deck(identity,name,rows)
        for r in rows:
            if f"[ID {r['id']}]" in label:return str(r['id'])
        return None
    def _selected_island_id(self,label):
        if not label or label.startswith("Current / unchanged"):return None
        for r in self.siege_island_rows:
            if f"[ID {r['id']}]" in label:return str(r['id'])
        import re; m=re.search(r"Island\s+(\d+)",label); return m.group(1) if m else None
    def _selected_siege_target_id(self,label):
        if not label or label.startswith("Any / none"):return None
        for r in self.siege_target_rows:
            if f"[ID {r.get('id')}]" in label:return str(r.get('id'))
        import re; m=re.search(r"\[ID\s+([^\]]+)\]",label); return m.group(1) if m else None

    def teach_play_now(self):
        if CommandCenterMixin._browser_action_busy(self):
            if not getattr(self,"_closing",False):messagebox.showinfo("Teach Play Now","Wait for Game / Ads or stop active automation before starting click capture.")
            return
        self.ad_stop.clear();done=threading.Event();self.ad_calibration_done=done
        def prepare():
            try:
                self.events.put({"kind":"log","message":"PLAY NOW TEACH: arming mouse-free click capture inside the dedicated mini AT browser…"})
                r=begin_play_click_calibration(lambda m:self.events.put({"kind":"log","message":m}),should_stop=self.ad_stop.is_set)
                self.events.put({"kind":"play_click_ready","result":r,"completion":done})
                if r.get("ok"):
                    while not done.wait(.2):
                        if self.ad_stop.is_set():break
            except Exception:self.events.put({"kind":"play_click_ready","result":{"ok":False,"message":"Play Now click capture could not be armed."},"completion":done})
            finally:done.set()
        self.ad_thread=threading.Thread(target=prepare,daemon=True);self.ad_thread.start()

    def test_ad_clicks(self):
        if CommandCenterMixin._browser_action_busy(self):
            if not getattr(self,"_closing",False):messagebox.showinfo("Test ad clicks","Wait for Game / Ads or stop active automation before testing ad playback.")
            return
        self.ad_stop.clear()
        def work():
            try:
                self.events.put({"kind":"log","message":"TEST AD CLICKS: bypassing the 3/3 boost short-circuit and replaying one saved browser click cycle…"})
                r=test_calibrated_ad_ui(lambda m:self.events.put({"kind":"log","message":m}),should_stop=self.ad_stop.is_set)
                self.events.put({"kind":"ad_test_result","result":r})
            except Exception:self.events.put({"kind":"ad_test_result","result":{"ok":False,"message":"Ad click testing could not be confirmed. The sequence was not repeated."}})
        self.ad_thread=threading.Thread(target=work,daemon=True);self.ad_thread.start()

    def calibrate_ads(self,kind="boost"):
        if CommandCenterMixin._browser_action_busy(self):
            if not getattr(self,"_closing",False):messagebox.showinfo("Calibrate ads","Wait for Game / Ads or stop active automation before starting calibration.")
            return
        self.ad_stop.clear()
        done=threading.Event();self.ad_calibration_done=done
        def prepare():
            try:
                self.events.put({"kind":"log","message":"AD CALIBRATION: opening the small dedicated AT browser window and arming browser-scoped click capture…"})
                r=begin_ad_click_calibration(lambda m:self.events.put({"kind":"log","message":m}),should_stop=self.ad_stop.is_set)
                self.events.put({"kind":"ad_calibration_ready","result":r,"ad_kind":kind,"completion":done})
                # The dialog uses Tk's nested event loop. Keep the same tracked
                # worker reserved until the capture is accepted or Stop arrives.
                if r.get("ok"):
                    while not done.wait(.2):
                        if self.ad_stop.is_set():break
            except Exception:self.events.put({"kind":"ad_calibration_ready","result":{"ok":False,"message":"Ad calibration could not be armed."},"completion":done})
            finally:done.set()
        self.ad_thread=threading.Thread(target=prepare,daemon=True);self.ad_thread.start()

    def watch_ads(self):
        if getattr(self,"_closing",False):return
        active_ad=getattr(self,"ad_thread",None)
        if active_ad is not None and active_ad.is_alive():return
        if CommandCenterMixin._browser_action_busy(self):
            messagebox.showinfo("Watch ads","Wait for Game / Ads or stop active automation before starting separate ad playback.");return
        self.ad_stop.clear()
        def work():
            try:
                self.events.put({"kind":"log","message":"WATCH ADS: automatically finding real game ad controls, waiting for completion and verifying AT credit. No calibration; Windows mouse stays untouched…"})
                r=attempt_legitimate_ad_ui(lambda m:self.events.put({"kind":"log","message":m}),wait_seconds=45,target_level=3,should_stop=self.ad_stop.is_set)
                if r.get("ok"):
                    self.events.put({"kind":"ad_watch_result","ok":True,"boost_level":r.get("boost_level")})
                else:
                    self.events.put({"kind":"ad_watch_result","ok":False,"boost_level":r.get("boost_level"),"message":r.get("message") or "No active boost verified."})
            except Exception:self.events.put({"kind":"ad_watch_result","ok":False,"message":"Ad playback could not be verified. No additional ad was started."})
        self.ad_thread=threading.Thread(target=work,daemon=True);self.ad_thread.start()

    def start_run(self):
        if self._command_blocks_runner():return
        if self.arena_runner and getattr(self.arena_runner,"running",False):return
        try:limit=int(self.limit_var.get()); delay=float(self.delay_var.get()); bdelay=float(self.battle_delay_var.get()); reserve=int(self.reserve_var.get())
        except Exception:messagebox.showerror("AT Commander","Check the Arena number fields.");return
        if limit<0 or delay<.05 or bdelay<.1 or reserve<0:messagebox.showerror("AT Commander","Battle limit/reserve must be >= 0; Move Delay >= .05; Battle Delay >= .1.");return
        self._save_settings(); did=self._selected_id(self.deck_var.get(),self.deck_rows); hid=self._selected_id(self.hero_var.get(),self.hero_rows)
        self.arena_runner=ArenaRunner(limit,delay,bdelay,did,hid,reserve,bool(self.continuous_var.get()),60,bool(self.auto_ads_var.get()),self.events.put,bypass_turn_limit=bool(self.arena_bypass_turn_limit_var.get())); self.session_started_mono=time.monotonic(); self.start_btn.configure(state="disabled");self.pause_btn.configure(state="normal");self.stop_btn.configure(state="normal");self.status_var.set("Starting");self._log(f"Starting Arena. Limit={limit}, move={delay:.2f}s, battle delay={bdelay:.2f}s, reserve={reserve}, continuous={bool(self.continuous_var.get())}, 30-turn cutoff={'bypassed' if self.arena_bypass_turn_limit_var.get() else 'enabled'}.");self.arena_runner.start()
    def toggle_pause(self):
        if self.arena_runner:self.paused=not self.paused;self.arena_runner.pause(self.paused);self.pause_btn.configure(text="Resume" if self.paused else "Pause")
    def stop_run(self):
        if self.arena_runner:self.arena_runner.stop();self.status_var.set("Stopping…")
    def stop_event(self):
        if self.event_runner:self.event_runner.stop();self._event_log("Stopping event automation…")
    def stop_adventure(self):
        if hasattr(self,"adventure_scan_stop"):self.adventure_scan_stop.set()
        if self.adventure_runner:self.adventure_runner.stop();self._append(self.adventure_log,"Stopping Adventure farm…")
    def stop_all(self):
        if hasattr(self,"reward_stop"):
            self.reward_stop.set();self.reward_due=float("inf")
        if hasattr(self,"deck_builder_stop"):self.deck_builder_stop.set()
        if hasattr(self,"app_update_stop"):self.app_update_stop.set()
        if hasattr(self,"client_update_stop"):self.client_update_stop.set()
        if hasattr(self,"game_panel_stop"):self.game_panel_stop.set()
        if hasattr(self,"ad_stop"):self.ad_stop.set()
        if hasattr(self,"adventure_scan_stop"):self.adventure_scan_stop.set()
        if hasattr(self,"command_stop"):self._routine_stop()
        if hasattr(self,"research_status"):self._research_pause()
        for runner in (self.arena_runner,self.event_runner,self.adventure_runner,self.swole_runner):
            try:
                if runner:runner.stop()
            except Exception:pass

    def close_app(self):
        if self._closing:return
        self._closing=True;self.stop_all()
        self.root.title(f"AT Commander {VERSION} — stopping active work…")
        def settle():
            reward_thread=getattr(self,"reward_thread",None)
            research_thread=getattr(self,"research_thread",None)
            busy=getattr(self,"research_busy",False) or getattr(self,"command_busy",False)
            busy=busy or reward_thread is not None and reward_thread.is_alive()
            busy=busy or research_thread is not None and research_thread.is_alive()
            browser_thread=getattr(self,"game_panel_thread",None)
            busy=busy or browser_thread is not None and browser_thread.is_alive()
            ad_thread=getattr(self,"ad_thread",None)
            busy=busy or ad_thread is not None and ad_thread.is_alive()
            scan_thread=getattr(self,"adventure_scan_thread",None)
            busy=busy or scan_thread is not None and scan_thread.is_alive()
            builder_thread=getattr(self,"deck_builder_thread",None)
            busy=busy or builder_thread is not None and builder_thread.is_alive()
            update_thread=getattr(self,"client_update_thread",None)
            busy=busy or update_thread is not None and update_thread.is_alive()
            app_update_thread=getattr(self,"app_update_thread",None)
            busy=busy or app_update_thread is not None and app_update_thread.is_alive()
            busy=busy or any(getattr(runner,"running",False) for runner in
                (self.arena_runner,self.event_runner,self.adventure_runner,self.swole_runner))
            if busy:self.root.after(100,settle)
            else:
                from .embedded_browser import close_owned_browser,configure
                from .ad_browser_focus import release_all_game_input_focus
                release_all_game_input_focus()
                state=close_owned_browser()
                if state.get("embedded") or state.get("reason")!="browser_closed":
                    self.learning_browser.close()
                    state=close_owned_browser()
                if state.get("embedded") or state.get("reason")!="browser_closed":
                    self.root.after(250,settle);return
                self._game_panel_closed=True;configure(None);self.root.destroy()
        settle()

    def start_event(self,mode):
        if self._command_blocks_runner():return
        if self.event_runner and getattr(self.event_runner,"running",False):messagebox.showinfo("AT Commander","An event automation is already running. Stop it first.");return
        p=load_profiles(); key=mode.lower()
        if mode!="Siege" and not p.get(key):messagebox.showinfo("Needs learning",f"{mode} start-battle is not learned yet. Open Diagnostics / Learn and capture one real {mode} fight.");self.nb.select(self.diag_tab);return
        deck=self.rumble_deck.get() if mode=="Rumble" else self.siege_deck.get(); did=self._selected_id(deck,self.deck_rows); island=self._selected_island_id(self.siege_island.get()) if mode=="Siege" else None; logic=self.rumble_logic.get() if mode=="Rumble" else self.siege_logic.get(); offdeck=self.rumble_offense_deck.get() if mode=="Rumble" else self.siege_offense_deck.get(); offhero=self.rumble_offense_hero.get() if mode=="Rumble" else self.siege_offense_hero.get(); odid=self._selected_id(offdeck,self.deck_rows); ohid=self._selected_id(offhero,self.hero_rows)
        targets=[]; fallback=True; poll=60
        if mode=="Siege":
            for v in (self.siege_target_a.get(),self.siege_target_b.get(),self.siege_target_c.get()):
                sid=self._selected_siege_target_id(v)
                if sid and sid not in targets:targets.append(sid)
            fallback=bool(self.siege_fallback_any.get())
            try:poll=max(10,int(self.siege_poll.get() or 60))
            except Exception:poll=60
            if not targets and not fallback:
                messagebox.showinfo("Siege","Choose at least one attack target or enable the any-living-island fallback.");return
        self._save_settings(); self.event_runner=EventBattleRunner(mode,float(self.delay_var.get() or .10),float(self.battle_delay_var.get() or 1.5),did,island,logic,odid,ohid,bool(self.auto_ads_var.get()),self.events.put,targets,fallback,poll);self.event_runner.start();self._event_log(f"Starting {mode} automation with {logic}…" if mode!="Siege" else f"Siege armed. Priorities={targets or ['any']} • fallback={fallback} • poll={poll}s.")

    def _selected_challenge_row(self):
        label=self.challenge_var.get()
        for r in self.challenge_rows:
            if f"[ID {r.get('id')}]" in label:return r
        return None

    def start_challenge(self):
        if self._command_blocks_runner():return
        if self.event_runner and getattr(self.event_runner,"running",False):messagebox.showinfo("Events","Another event automation is already running.");return
        row=self._selected_challenge_row()
        if not row:messagebox.showinfo("Challenges","Refresh Account and choose a detected Challenge, Clash, or Melee.");return
        if row.get("state")!="Active":messagebox.showinfo("Challenges",f"{row.get('name')} is not active yet.");return
        if not row.get("challenge_id") or row.get("level") is None:
            messagebox.showinfo("Challenges","The current challenge ID and level are incomplete. Refresh Account before starting.");return
        odid=self._selected_id(self.challenge_offense_deck.get(),self.deck_rows);ohid=self._selected_id(self.challenge_offense_hero.get(),self.hero_rows)
        preference=getattr(self,"melee_character_first_var",None)
        self.event_runner=ChallengeBattleRunner(str(row.get("id")),str(row.get("challenge_id")),str(row.get("name")),float(self.delay_var.get() or .10),float(self.battle_delay_var.get() or 1.5),self.challenge_logic.get(),odid,ohid,bool(self.auto_ads_var.get()),self.events.put,level=row.get("level"),melee_character_first=bool(preference.get()) if preference is not None else False);self.event_runner.start();self._event_log(f"Starting {row.get('name')} with the direct signed API…")

    def _save_melee_character_first(self):
        """Persist the checkbox without starting combat or altering other options."""
        preference=getattr(self,"melee_character_first_var",None)
        enabled=bool(preference.get()) if preference is not None else False
        current=load_settings();current["melee_character_first"]=enabled
        save_settings(current);self.settings["melee_character_first"]=enabled

    def toggle_siege_learn(self):
        """One-button capture for the exact successful fightGuildSiege request."""
        if self.learn_session and self.learn_key != "siege":
            messagebox.showinfo("Siege learn","Another learning capture is already active. Finish it first.");return
        if not self.learn_session:
            ATCommanderApp._start_legacy_learning_capture(self,"siege",siege=True)
            return
        path=self.learn_session.stop(); self.last_capture_path=path; self.last_capture_key="siege"
        self.learn_session=None; self.learn_key=None
        done=getattr(self,"learn_capture_done",None)
        if done is not None:done.set()
        if self.ad_stop.is_set() or getattr(self,"_closing",False):
            if not getattr(self,"_closing",False):self._event_log("Siege learning stopped. The partial trace was not installed.")
            return
        try:self.siege_learn_btn.configure(text="LEARN ONE SIEGE ATTACK")
        except Exception:pass
        try:
            auto, flow_names = install_siege_flow_profile(path)
            if not auto:
                auto=install_profile("siege",path); flow_names=[]
        except Exception as exc:
            auto=False; flow_names=[]; self._event_log(f"Siege learn auto-link error: {exc}")
        cands=profile_candidates_from_trace("siege",path)
        if auto:
            prof=load_profiles().get("siege") or {}; keys=sorted((prof.get("data") or {}).keys())
            if prof.get("flow"):
                self._event_log(f"Siege start flow learned. Sequence={flow_names}; final fields={keys}.")
                messagebox.showinfo("Siege learned",f"Siege start flow was learned:\n{' -> '.join(flow_names)}\n\nCommander will replay the setup calls before fightGuildSiege, then follow A/B/C targeting.")
            else:
                self._event_log(f"Siege fight-start learned. Captured fields={keys}.")
                messagebox.showinfo("Siege learned",f"Siege fight-start was learned. Captured request fields:\n{keys}\n\nYou can ARM / AUTO-COMPLETE SIEGE now.")
        elif cands:
            self._review_capture_popup("siege",path,cands)
        else:
            self._event_log("Siege learn finished but no fightGuildSiege request was recovered. The capture is saved locally; do not burn more attacks repeating it yet.")
            messagebox.showinfo("Siege capture saved","No replayable Siege start request was recovered. Do not spend another attack repeating this right now; the saved trace contains the recorder evidence for the next patch.")
        self._refresh_protocol_status()

    def apply_siege_island(self):
        island=self._selected_island_id(self.siege_island.get())
        if not island:messagebox.showinfo("Siege","Choose a defense island first.");return
        def work():
            try:ATClient(load_credentials()).set_siege_defense_island(island);self.events.put({"kind":"eventlog","message":f"Siege defense island {island} applied with the confirmed API call. No battle was started."})
            except Exception as e:self.events.put({"kind":"error","message":str(e)})
        threading.Thread(target=work,daemon=True).start()

    def refresh_swole_status(self):
        self.swole_status.set("Refreshing live Swole status…")
        def work():
            try:
                c=ATClient(load_credentials()); status=c.get_swole_status(); tasks=status.get("user_tasks") or {}
                vals=tasks.values() if isinstance(tasks,dict) else tasks if isinstance(tasks,list) else []
                task=None
                for t in vals:
                    if not isinstance(t,dict) or t.get("task_index") is None:continue
                    try:
                        if t.get("task_type") is not None and int(t.get("task_type"))!=2:continue
                    except Exception:pass
                    if t.get("start_time") or t.get("end_time"):task=t;break
                tickets=SwoleTrainingRunner._ticket_state(status)
                self.events.put({"kind":"swole_status_live","task":task,"squad":status.get("user_hero_squad") or {},"time":status.get("time") or time.time(),"fight_ready":tickets["active"],"swole_event":status.get("swole_event") or {}})
            except Exception as e:self.events.put({"kind":"error","message":f"Swole status refresh failed: {e}"})
        threading.Thread(target=work,daemon=True).start()

    def start_swole(self):
        if self._command_blocks_runner():return
        if self.swole_runner and self.swole_runner.running:return
        if not self.swole_plan:messagebox.showinfo("Swole","Add at least one training or fight step.");return
        try:poll=int(self.swole_poll.get())
        except:poll=30
        # Training and signed fight starts share the ordered schedule. Incomplete
        # live event data pauses a fight block and preserves the remaining queue.
        self.swole_runner=SwoleTrainingRunner(self.swole_plan,poll,bool(self.auto_ads_var.get()),self.events.put);self.swole_runner.start();self._swole_log("Swole schedule started.")

    def stop_swole(self):
        if self.swole_runner:self.swole_runner.stop()

    def _adventure_fill_id(self):
        try:
            island=int(self.adventure_island_num.get()); stage=int(self.adventure_stage_num.get())
            if island<1 or stage<1 or stage>3:raise ValueError
            mid=101+(island-1)*3+(stage-1)
            self.adventure_mission_id.set(str(mid));self.adventure_kind.set("Main Adventure");self.adventure_mission_choice.set("Main uses Island / Stage controls")
            self._append(self.adventure_log,f"Main Adventure {island}-{stage} → mission ID {mid}.")
        except Exception:messagebox.showerror("Adventure","Main Adventure Island must be >=1 and Stage must be 1-3.")

    def _refresh_adventure_campaign_missions(self):
        if not hasattr(self,"adventure_mission_combo"):return
        label=self.adventure_kind.get()
        if label=="Main Adventure":
            vals=["Main uses Island / Stage controls"];self.adventure_mission_combo["values"]=vals;self.adventure_mission_choice.set(vals[0]);return
        if label=="Custom / Event mission ID":
            vals=["Use Mission ID field"];self.adventure_mission_combo["values"]=vals;self.adventure_mission_choice.set(vals[0]);return
        row=find_adventure_campaign(self.adventure_campaign_rows,label)
        missions=(row or {}).get("missions") or []
        previous_mission=str(self.adventure_mission_id.get() or "")
        self.adventure_mission_label_map={}
        if missions:
            vals=[]
            for i,m in enumerate(missions,1):
                base=str(m.get("display") or m.get("name") or f"Stage {i}")
                label=base
                n=2
                while label in self.adventure_mission_label_map:
                    label=f"{base} ({n})";n+=1
                vals.append(label);self.adventure_mission_label_map[label]=str(m.get("id"))
        else:
            vals=["Stage map not exposed — use Diagnostics → Adventure battle to teach ONE live event start"]
            self.adventure_mission_id.set("")
        self.adventure_mission_combo["values"]=vals
        preserved=[label for label,mid in self.adventure_mission_label_map.items() if mid==previous_mission]
        if len(preserved)==1:self.adventure_mission_choice.set(preserved[0])
        elif self.adventure_mission_choice.get() not in vals:self.adventure_mission_choice.set(vals[0])
        self._adventure_mission_changed()

    def _adventure_campaign_changed(self):
        self._refresh_adventure_campaign_missions()
        if self.adventure_kind.get()=="Main Adventure":self._adventure_fill_id()
        elif self.adventure_kind.get().startswith(("Seasonal / Event:","Live Event:")):
            row=find_adventure_campaign(self.adventure_campaign_rows,self.adventure_kind.get())
            if row and row.get("missions"):
                src="learned native event start" if row.get("source")=="learned_event_mission" else "AT static data"
                self._append(self.adventure_log,f"Selected {row.get('name')}; {len(row.get('missions') or [])} mission/stage row(s) resolved from {src}.")
            else:self._append(self.adventure_log,"AT exposed this event island but did not expose a safe mission map. 9040-style current_campaign markers are not mission IDs. Use Diagnostics → Adventure battle and capture ONE manual event start; Commander will bind its real mission ID to this island automatically.")

    def _adventure_mission_changed(self):
        label=self.adventure_mission_choice.get() or ""
        mid=self.adventure_mission_label_map.get(label)
        if mid not in (None,""):
            self.adventure_mission_id.set(str(mid));return
        import re
        m=re.search(r"\[ID\s+([^\]]+)\]",label)
        if m:self.adventure_mission_id.set(str(m.group(1)))

    def start_adventure(self):
        if self._command_blocks_runner():return
        if self.adventure_runner and getattr(self.adventure_runner,"running",False):messagebox.showinfo("Adventure","Adventure automation is already running.");return
        if self.adventure_kind.get()=="Main Adventure":self._adventure_fill_id()
        else:self._adventure_mission_changed()
        try:mid=int(self.adventure_mission_id.get());limit=int(self.adventure_limit.get());reserve=int(self.adventure_reserve.get())
        except Exception:messagebox.showerror("Adventure","Check Mission ID, Battle limit, and Keep energy.");return
        if mid<=0 or limit<0 or reserve<0:messagebox.showerror("Adventure","Mission ID must be >0; limit/reserve must be >=0.");return
        try:complete,target=ATCommanderApp._adventure_completion_options(self)
        except ValueError as exc:messagebox.showerror("Adventure",str(exc));return
        self._save_settings()
        self._adventure_pause_message=None;self._adventure_error_message=None
        self.adventure_runner=AdventureRunner(mid,limit,float(self.delay_var.get() or .10),float(self.battle_delay_var.get() or 1.5),reserve,bool(self.adventure_continuous.get()),bool(self.auto_ads_var.get()),self.events.put,complete_islands=complete,completion_target=target);self.adventure_runner.start()
        action=(f"Completing available islands ({target}), then farming default mission {mid}" if complete else f"Starting farm on mission {mid}")
        self._append(self.adventure_log,f"{action}. Limit={limit or 'unlimited'}, reserve={reserve}. Arena/Swole may continue independently.")

    def _adventure_completion_options(self):
        mode=getattr(self,"adventure_mode",None);goal=getattr(self,"adventure_completion_goal",None)
        complete=mode is not None and mode.get()==ADVENTURE_MODES[1]
        label=goal.get() if goal is not None else "100% mastery"
        if label not in ADVENTURE_GOALS:raise ValueError("Choose Stage completion or 100% mastery.")
        return complete,ADVENTURE_GOALS[label]

    def _save_adventure_completion_options(self):
        complete,target=ATCommanderApp._adventure_completion_options(self)
        current=load_settings();current.update(adventure_complete_islands=complete,adventure_completion_target=target)
        save_settings(current);self.settings.update(adventure_complete_islands=complete,adventure_completion_target=target)

    def scan_adventure_islands(self):
        """Read-only current island preview, independent of the farm selection."""
        if getattr(self,"_closing",False):return
        worker=getattr(self,"adventure_scan_thread",None)
        if worker is not None and worker.is_alive():return
        try:_,target=ATCommanderApp._adventure_completion_options(self)
        except ValueError as exc:messagebox.showerror("Adventure scan",str(exc));return
        if not hasattr(self,"adventure_scan_stop"):self.adventure_scan_stop=threading.Event()
        self.adventure_scan_stop.clear();token=object();self._adventure_scan_token=token
        self.adventure_scan_status.set("Scanning current islands and stages… No fight has been started.")
        def progress(message):self.events.put({"kind":"adventure_scan_progress","message":message,"token":token})
        def work():
            try:
                from .adventure_completion import scan_adventure_islands
                stopped=self.adventure_scan_stop.is_set
                if stopped():return
                client=ATClient(load_credentials());progress("Reading current Adventure state…")
                live=client.init()
                if stopped():return
                if not isinstance(live,dict) or not live or api_rejected(live):raise ValueError("Unverified Adventure state")
                progress("Reading the island catalog…");campaigns=client.campaign_catalog()
                if stopped():return
                progress("Reading the stage catalog…");missions=client.mission_catalog()
                if stopped():return
                progress("Checking available islands and completion counters…")
                scan=scan_adventure_islands(live,campaigns,missions,target=target)
                if stopped():return
                self.events.put({"kind":"adventure_scan","scan":scan,"token":token})
            except Exception:
                if not self.adventure_scan_stop.is_set():self.events.put({"kind":"adventure_scan_failed","token":token,"message":"Island scan could not be verified. No fight or browser action was started."})
            finally:
                if self.adventure_scan_stop.is_set():self.events.put({"kind":"adventure_scan_stopped","token":token})
        self.adventure_scan_thread=threading.Thread(target=work,daemon=True);self.adventure_scan_thread.start()

    def _show_adventure_scan(self,scan):
        """Display the planner's public counters without changing farm controls."""
        if not isinstance(scan,dict) or not isinstance(scan.get("islands"),list):return
        tree=getattr(self,"adventure_island_tree",None)
        if tree is not None:
            for row_id in tree.get_children():tree.delete(row_id)
            ordered=sorted((island for island in scan["islands"] if isinstance(island,dict)),key=lambda island:island.get("state")=="Complete")
            for index,island in enumerate(ordered):
                if not isinstance(island,dict):continue
                stage=next((row.get("stage") for row in island.get("missions",[]) if isinstance(row,dict) and row.get("available") and not row.get("complete")),None)
                progress=f"{island.get('progress_percent','—')}% • {island.get('completed_stages','—')}/{island.get('total_stages','—')} stages"
                following=f"Stage {stage}" if stage is not None else str(island.get("state") or "Unknown")
                if island.get("reason"):following+=" • "+str(island["reason"])
                tree.insert("","end",iid=f"island-{index}",values=(str(island.get("name") or island.get("id") or "Island"),progress,following),tags=("complete" if island.get("state")=="Complete" else "pending",))
        next_mission=scan.get("next_mission")
        next_text=(f"Next: {next_mission.get('island')} stage {next_mission.get('stage')} (mission {next_mission.get('mission')})" if isinstance(next_mission,dict) else "All available islands complete; default farm is next" if scan.get("all_complete") is True else "No safe next stage; see blocked/locked islands")
        goal=next((label.lower() for label,target in ADVENTURE_GOALS.items() if target==scan.get("target")),"completion")
        self.adventure_scan_status.set(f"Verified {goal} scan: {scan.get('completed_stages','—')}/{scan.get('total_stages','—')} stages • {scan.get('progress_percent','—')}% • {next_text}")
        self._append(self.adventure_log,next_text)
        for warning in scan.get("warnings",[]):self._append(self.adventure_log,"Island scan: "+str(warning))

    def refresh_adventure(self):
        self._refresh_account_metadata(force_adventure_static=True)
        if self.adventure_kind.get()=="Main Adventure":self._adventure_fill_id()
        else:self._adventure_mission_changed()
        try:mid=int(self.adventure_mission_id.get())
        except Exception:messagebox.showerror("Adventure","Choose a valid Main island-stage, detected mission, or custom Mission ID first.");return
        selected_label=(f"Main {int(self.adventure_island_num.get())}-{int(self.adventure_stage_num.get())}" if self.adventure_kind.get()=="Main Adventure" else self.adventure_kind.get())
        self.adventure_completion.set(f"Refreshing {selected_label}…")
        def work():
            try:
                c=ATClient(load_credentials());data=c.get_player_prefs_snapshot(mid);prefs=data.get("player_prefs_data") or {}
                try:state=c.init()
                except Exception:state={}
                try:account=c.get_user_account()
                except Exception:account={}
                merged=dict(state or {})
                if isinstance(account.get("user_data"),dict):merged["user_data"]={**(merged.get("user_data") or {}),**(account.get("user_data") or {})}
                cur,maxe,recharge=find_adventure_energy(merged)
                ud=merged.get("user_data") or {};last=prefs.get("last_mission_played");auto=prefs.get("auto_battle_mission_id");won=ud.get("num_missions_won")
                markers={k:v for k,v in prefs.items() if str(k).startswith("current_campaign")}
                self.events.put({"kind":"adventure_progress_v2","selected":selected_label,"selected_mission":mid,"last_mission":last,"auto_mission":auto,"num_missions_won":won,"campaign_count":prefs.get("CAMPAIGN_COUNT_PLAYER_PREF"),"last_unlocked":prefs.get("last_mission_unlocked"),"prefs":markers,"energy":cur,"max_energy":maxe,"recharge":recharge})
            except Exception as e:self.events.put({"kind":"error","message":f"Adventure refresh failed: {e}"})
        threading.Thread(target=work,daemon=True).start()

    def refresh_inventory(self):
        def work():
            try:
                c=ATClient(load_credentials()); init=c.init()
                try: account=c.get_user_account()
                except Exception: account={}
                merged=dict(init or {})
                for key in ("user_data","common_fields"):
                    if isinstance(account.get(key),dict): merged[key]={**(merged.get(key) or {}),**account[key]}
                try:itemcat=c.item_catalog()
                except Exception:itemcat={}
                snap=build_inventory_snapshot(merged,item_catalog=itemcat);self.lifetime.update_snapshot(snap);self.events.put({"kind":"manual_snapshot","snapshot":snap,"lifetime":self.lifetime.to_dict(),"progress":scan_progress_metrics(account or merged),"medals":find_hero_medals(merged,self.hero_rows)})
            except Exception as e:self.events.put({"kind":"error","message":f"Refresh failed: {e}"})
        threading.Thread(target=work,daemon=True).start()

    def _claim_all_mail(self):
        """Claim every currently claimable reward mail using live mail ids.

        The old learned profile captured one literal mail_id, so replaying it later
        could only target an already-consumed message. Read the live mailbox and
        claim each reward-bearing row by its current mail_id/type instead.
        """
        if not messagebox.askyesno("Claim mail","Claim every currently available reward-bearing mail message?\n\nCommander reads the live mail IDs first; it does not replay the old captured mail ID."):
            return
        def work():
            try:
                c=ATClient(load_credentials())
                snapshots=[]
                for getter in (c.init,c.get_user_account):
                    try:snapshots.append(getter())
                    except Exception:pass
                rows=[];seen=set()
                def collect(obj,depth=0):
                    if depth>7:return
                    if isinstance(obj,dict):
                        mail=obj.get("user_mail")
                        if isinstance(mail,dict): vals=list(mail.values())
                        elif isinstance(mail,list): vals=mail
                        else: vals=[]
                        for row in vals:
                            if not isinstance(row,dict):continue
                            mid=row.get("mail_id")
                            if mid is None or str(mid) in seen:continue
                            seen.add(str(mid));rows.append(row)
                        for v in obj.values():
                            if isinstance(v,(dict,list)):collect(v,depth+1)
                    elif isinstance(obj,list):
                        for v in obj:
                            if isinstance(v,(dict,list)):collect(v,depth+1)
                for snap in snapshots:collect(snap)
                claimable=[]
                for row in rows:
                    reward=row.get("reward")
                    # Deletion is intentionally more conservative than claiming:
                    # any non-empty reward payload counts as valuable, including
                    # currencies or future reward shapes we do not recognize yet.
                    has_reward=bool(reward) or bool(row.get("rewards")) or bool(row.get("reward_data"))
                    try:status=int(row.get("status") or 0)
                    except Exception:status=0
                    if has_reward and status!=2:claimable.append(row)
                total=len(claimable)
                if not total:
                    self.events.put({"kind":"itemresult","message":"Mail refresh complete: no claimable reward mail is currently available."})
                    self.events.put({"kind":"inventory_progress","done":0,"total":0,"label":"Mail: nothing to claim"})
                    return
                done=0
                for row in claimable:
                    mid=str(row.get("mail_id"));mtype=str(row.get("type") or row.get("mail_type") or 1)
                    r=c.call("claimMessageRewards",{"mail_id":mid,"mail_type":mtype})
                    if isinstance(r,dict) and r.get("result") is False:
                        raise RuntimeError(f"AT rejected mail {mid}: {r.get('result_message') or 'unknown reason'}")
                    done+=1
                    self.events.put({"kind":"inventory_progress","done":done,"total":total,"label":f"Mail {done}/{total}"})
                self.events.put({"kind":"itemresult","message":f"Claim Mail complete: claimed {done} reward message(s)."})
                self.refresh_inventory()
            except Exception as e:
                self.events.put({"kind":"error","message":f"Claim Mail failed: {e}"})
        threading.Thread(target=work,daemon=True).start()

    def _delete_safe_mail(self):
        """Delete only claimed or rewardless mail using current live mail ids.

        The delete endpoint itself remains learned from one real manual delete;
        this method fixes the old literal-mail-id problem and refuses to delete
        unclaimed reward mail.
        """
        p=load_profiles().get("mail_delete")
        if not p:
            messagebox.showinfo("Delete mail needs one capture","Delete ONE expendable mail message manually in AT, then use Diagnostics / Learn → Delete mail. Commander will reuse only the learned request shape and substitute each current live mail ID; it will preserve unclaimed reward mail.")
            self.nb.select(self.diag_tab);return
        if not messagebox.askyesno("Delete safe mail","Delete all currently safe mail?\n\nSafe = already-claimed reward mail or mail with no reward.\nUnclaimed reward-bearing messages are preserved automatically."):
            return
        def work():
            try:
                c=ATClient(load_credentials())
                snapshots=[]
                for getter in (c.init,c.get_user_account):
                    try:snapshots.append(getter())
                    except Exception:pass
                rows=[];seen=set()
                def collect(obj,depth=0):
                    if depth>7:return
                    if isinstance(obj,dict):
                        mail=obj.get("user_mail")
                        vals=list(mail.values()) if isinstance(mail,dict) else mail if isinstance(mail,list) else []
                        for row in vals:
                            if not isinstance(row,dict):continue
                            mid=row.get("mail_id")
                            if mid is None or str(mid) in seen:continue
                            seen.add(str(mid));rows.append(row)
                        for v in obj.values():
                            if isinstance(v,(dict,list)):collect(v,depth+1)
                    elif isinstance(obj,list):
                        for v in obj:
                            if isinstance(v,(dict,list)):collect(v,depth+1)
                for snap in snapshots:collect(snap)
                safe=[];preserved=0
                for row in rows:
                    reward=row.get("reward")
                    has_reward=isinstance(reward,dict) and any(bool(reward.get(k)) for k in ("item","card","items","cards"))
                    try:status=int(row.get("status") or 0)
                    except Exception:status=0
                    if has_reward and status!=2:
                        preserved+=1;continue
                    safe.append(row)
                total=len(safe)
                if not total:
                    self.events.put({"kind":"itemresult","message":f"Delete Mail refresh complete: nothing safe to delete. Preserved {preserved} unclaimed reward message(s)."})
                    self.events.put({"kind":"inventory_progress","done":0,"total":0,"label":"Mail: nothing safe to delete"})
                    return
                done=0
                self.events.put({"kind":"inventory_progress","done":0,"total":total,"label":f"Delete mail 0/{total}"})
                for row in safe:
                    mid=str(row.get("mail_id"));mtype=str(row.get("type") or row.get("mail_type") or 1)
                    r=c.call_profile(p,{"mail_id":mid,"mail_type":mtype})
                    if isinstance(r,dict) and r.get("result") is False:
                        raise RuntimeError(f"AT rejected mail delete {mid}: {r.get('result_message') or 'unknown reason'}")
                    done+=1
                    self.events.put({"kind":"inventory_progress","done":done,"total":total,"label":f"Delete mail {done}/{total}"})
                self.events.put({"kind":"itemresult","message":f"Delete Mail complete: deleted {done} safe message(s); preserved {preserved} unclaimed reward message(s)."})
                self.refresh_inventory()
            except Exception as e:
                self.events.put({"kind":"error","message":f"Delete Mail failed: {e}"})
        threading.Thread(target=work,daemon=True).start()

    def _profile_action(self,key,ask_quantity=False):
        p=load_profiles().get(key)
        if not p:messagebox.showinfo("Needs learning",f"{key.replace('_',' ').title()} has not been learned yet. Use Diagnostics / Learn once.");self.nb.select(self.diag_tab);return
        qty=1
        if ask_quantity:
            qty=simpledialog.askinteger("AT Commander","Quantity / spend amount",minvalue=1)
            if not qty:return
        values={"quantity":qty,"amount":qty}
        if key=="nixon_spend" and "{purchase_id}" in json.dumps(p):
            pid=simpledialog.askstring("Spend Nixons","Pack / purchase ID from the learned shop action:")
            if not pid:return
            values["purchase_id"]=pid
        def work():
            try:r=ATClient(load_credentials()).call_profile(p,values);self.events.put({"kind":"itemresult","message":json.dumps(r,indent=2)[:3500]})
            except Exception as e:self.events.put({"kind":"error","message":str(e)})
        threading.Thread(target=work,daemon=True).start()
    def _paced_shop_stack(self, total, call_amount, progress_label, progress_prefix):
        """Buy ``total`` packs, preferring one native quantity request.

        False responses are safe to retry/split because AT explicitly rejected the
        purchase. Successful chunks are separated by the observed four-second shop
        cooldown so warning_16 never turns one success into an immediate abort.
        Unknown transport exceptions are intentionally not retried here because a
        timed-out purchase could have committed server-side.
        """
        total=max(1,int(total)); done=0; pending=[total]; last_success=0.0
        while pending:
            amount=max(1,int(pending.pop(0)))
            transient_tries=0
            while True:
                if last_success:
                    remain=(last_success+SHOP_COOLDOWN_SECONDS)-time.monotonic()
                    if remain>0:
                        self.events.put({"kind":"inventory_progress","done":done,"total":total,"label":f"{progress_prefix} {done}/{total} • cooldown {remain:.1f}s"})
                        time.sleep(remain)
                r=call_amount(amount)
                if not _shop_failed(r):
                    done+=amount; last_success=time.monotonic()
                    mode="native bulk" if amount>1 else "paced single"
                    self.events.put({"kind":"inventory_progress","done":min(done,total),"total":total,"label":f"{progress_prefix} {min(done,total)}/{total} • {mode}"})
                    break
                msg=_shop_result_message(r) or "AT rejected shop purchase"
                if _shop_warning16(r):
                    transient_tries+=1
                    if transient_tries>4:
                        raise RuntimeError(f"{msg} (still rate-limited after {transient_tries-1} paced retries)")
                    wait=SHOP_COOLDOWN_SECONDS
                    self.events.put({"kind":"inventory_progress","done":done,"total":total,"label":f"{progress_prefix} {done}/{total} • warning_16, retrying in {wait:.1f}s"})
                    time.sleep(wait)
                    # The rejected request bought nothing; retry the same chunk.
                    last_success=0.0
                    continue
                if amount<=1:
                    raise RuntimeError(msg)
                # Quantity shape was rejected for a non-rate reason. Split only
                # rejected work, so there is no risk of duplicating a completed buy.
                a=max(1,amount//2); b=amount-a
                self.events.put({"kind":"inventory_progress","done":done,"total":total,"label":f"{progress_prefix} {done}/{total} • bulk {amount} rejected; trying {a}+{b}"})
                pending[:0]=([a]+([b] if b else []))
                break
        return done

    def _adaptive_single_shop_loop(self,total,call_one,progress_prefix,*,start_delay=None,min_delay=.10,max_delay=4.25):
        """Use real single buys, measured start-to-start cadence and exact counts."""
        initial=self.settings.get("golden_shop_interval_seconds",.10) if start_delay is None else start_delay
        def progress(state):
            self.events.put({"kind":"inventory_progress","done":state.done,"total":state.total,
                             "label":f"{progress_prefix} {state.done}/{state.total} • {state.rate:.2f}/s • {state.status} • interval {state.interval:.2f}s"})
        try:
            state=run_single_purchases(total,call_one,on_progress=progress,initial_interval=float(initial),
                                       min_interval=min_delay,max_interval=max_delay)
        except PurchaseInterrupted as exc:
            self.events.put({"kind":"shop_pacing","interval":exc.progress.interval});raise
        self.events.put({"kind":"shop_pacing","interval":state.interval})
        return state.done,state.elapsed,state.warnings,state.interval

    def _spend_nixons(self):
        label=self.nixon_pack_var.get();import re
        m=re.search(r"\[ID\s+([^\]]+)\]",label);pid=m.group(1) if m else None
        if not pid:messagebox.showinfo("Spend Nixons","Choose a Nixon purchase first.");return
        row=next((r for r in self.nixon_pack_rows if str(r.get("id"))==str(pid)),{})
        fallback_costs={"1":1000,"83":3000,"65":5000,"66":50000}
        mc=re.search(r"—\s*([\d,]+)\s+Nixons",label)
        cost=int(row.get("cost") or fallback_costs.get(str(pid)) or (mc.group(1).replace(",","") if mc else 0) or 0)
        if cost<=0:messagebox.showerror("Spend Nixons","AT did not expose a usable Nixon price for that store item. Refresh Account and try again.");return
        buy_max=bool(self.nixon_buy_max.get());recycle_after=bool(self.nixon_recycle_after.get())
        try:qty=max(1,int(self.nixon_qty.get()))
        except Exception:messagebox.showerror("Spend Nixons","Quantity must be a whole number.");return
        balance=0
        for ent in ((self.current_snapshot or {}).get("entries") or {}).values():
            if ent.get("name")=="Nixon Coin":
                try:balance=int(ent.get("count") or 0)
                except Exception:pass
        if buy_max:
            if balance<cost:messagebox.showinfo("Spend Nixons",f"Not enough Nixon balance in the latest snapshot for {label}. Refresh Inventory first if the balance changed.");return
            qty=max(1,balance//cost)
        total_cost=qty*cost
        if balance and total_cost>balance:messagebox.showerror("Spend Nixons",f"That would cost {total_cost:,} Nixons, but the latest snapshot has {balance:,}.");return
        if not messagebox.askyesno("Spend Nixons",f"Buy {qty:,} × {label}?\n\nTotal cost: {total_cost:,} Nixons\n\n1.7.17 keeps the proven native stack path first. Your 212-pack live test completed without errors; this run now records exact elapsed time/rate so the next large test is measurable."+("\n\nThen recycle only loose level-1 Common/Rare/Epic cards, protecting saved-deck cards and foils." if recycle_after else "")):return
        common,rare,epic=bool(self.recycle_common.get()),bool(self.recycle_rare.get()),bool(self.recycle_epic.get());self._save_settings()
        def work():
            done_box=[0]
            try:
                started=time.monotonic()
                creds=load_credentials();c=ATClient(creds,timeout=20)
                self.events.put({"kind":"inventory_progress","done":0,"total":qty,"label":f"Nixon packs 0/{qty} • trying native stack"})
                self.events.put({"kind":"itemresult","message":f"Buying {qty:,} × {label}: native quantity stack first; paced fallback only if AT rejects the stack."})
                def call_amount(amount):
                    # expected_cost remains the per-pack price. The 1.7.14 test
                    # multiplied it by quantity, which was not the game's request shape.
                    return c.buy_store_item(pid,cost,amount)
                done=self._paced_shop_stack(qty,call_amount,"Nixon packs","Nixon packs")
                done_box[0]=done;elapsed=max(.001,time.monotonic()-started);rate=done/elapsed
                if recycle_after:
                    count=self._recycle_trash_worker(c,common,rare,epic);self.events.put({"kind":"itemresult","message":f"Nixon purchases complete: {done:,}/{qty:,} in {elapsed:.1f}s ({rate:.2f} packs/s). Recycled {count} safe trash card(s)."})
                else:self.events.put({"kind":"itemresult","message":f"Nixon purchases complete: {done:,}/{qty:,} × {label} in {elapsed:.1f}s ({rate:.2f} packs/s)."})
                self.refresh_inventory()
            except Exception as e:self.events.put({"kind":"error","message":f"Nixon purchase stopped after {done_box[0]:,}/{qty:,}: {e}"})
        threading.Thread(target=work,daemon=True).start()

    def _spend_golden_turds(self,name,balance):
        max_pulls=max(0,int(balance)//1000)
        if max_pulls<1:
            messagebox.showinfo("Golden Turds",f"Latest Golden Turd balance is {balance:,}; one Golden Pack costs 1,000.")
            return
        qty=simpledialog.askinteger("Golden Packs",f"Golden Turd balance: {balance:,}\nGolden Pack cost: 1,000\nAvailable pulls: {max_pulls:,}\n\nHow many Golden Packs?",minvalue=1,maxvalue=max_pulls)
        if not qty:return
        if not messagebox.askyesno("Golden Packs",f"Buy {qty:,} Golden Pack(s)?\n\nCost: {qty*1000:,} Golden Turds\n\nCommander uses one purchase per request and remembers the fastest working pace for future runs."):
            return
        def work():
            done_box=[0]
            try:
                c=ATClient(load_credentials(),timeout=20)
                self.events.put({"kind":"inventory_progress","done":0,"total":qty,"label":f"Golden Packs 0/{qty} • adaptive single-buy"})
                self.events.put({"kind":"itemresult","message":f"Buying {qty:,} Golden Pack(s) one at a time through buyStoreItem item 63 with adaptive pacing."})
                def call_one():
                    return c.call("buyStoreItem",{"cost_type":"2","item_id":"63","quantity":"1"})
                done,elapsed,warnings,final_delay=self._adaptive_single_shop_loop(qty,call_one,"Golden Packs")
                done_box[0]=done;rate=done/max(.001,elapsed)
                self.events.put({"kind":"itemresult","message":f"Golden Pack purchase complete: {done:,}/{qty:,} in {elapsed:.1f}s ({rate:.2f} packs/s), {warnings} warning_16 backoff(s), final delay {final_delay:.2f}s. Spent {done*1000:,} Golden Turds."})
                self.refresh_inventory()
            except PurchaseInterrupted as e:
                self.events.put({"kind":"error","message":f"Golden Pack purchase stopped after {e.progress.done:,}/{qty:,}: {e}"})
                self.refresh_inventory()
            except Exception as e:
                self.events.put({"kind":"error","message":f"Golden Pack purchase stopped after {done_box[0]:,}/{qty:,} ({type(e).__name__})."})
        threading.Thread(target=work,daemon=True).start()

    def _spend_selected(self):
        x=self.inventory_tree.selection()
        if not x:messagebox.showinfo("Spend item","Select a named spendable item/resource row first.");return
        vals=self.inventory_tree.item(x[0],"values");item_id=str(vals[3] if len(vals)>3 else "");name=str(vals[1] if len(vals)>1 else "selected item")
        if not item_id or item_id in {"nixon","gem","4001","21000","3900042","200008"}:
            messagebox.showinfo("Spend item","That row is a balance/shop currency rather than a generic useItem consumable. Use its dedicated game/shop action instead of sending a blind item-use request.");return
        raw_count=str(vals[2] if len(vals)>2 else "0").replace(",","")
        try:balance=int(float(raw_count))
        except Exception:balance=0
        if item_id=="200005":
            self._spend_golden_turds(name,balance);return
        cost=ITEM_USE_COSTS.get(item_id)
        choice=item_id in CHOICE_ITEM_IDS
        auto_choose=bool(self.auto_item_choice.get())
        if cost:
            max_uses=balance//cost if balance>=0 else 0
            if max_uses<1:messagebox.showinfo("Spend item",f"{name} uses {cost:,} resource units per pull. Latest balance: {balance:,}, so there is not enough for one pull.");return
            limit=1 if choice else max_uses
            prompt=f"{name} balance: {balance:,}\nCost per pull/use: {cost:,}\nAvailable pulls: {max_uses:,}\n\nHow many PULLS/USES?"
            if choice:prompt+="\n\nCommander opens one pull at a time so each reward choice can be confirmed before more resources are spent."
            qty=simpledialog.askinteger("Spend item — pulls, not stones",prompt,minvalue=1,maxvalue=limit)
            if not qty:return
            total=qty*cost
            confirm=f"Use {qty:,} pull(s) of {name} [ID {item_id}]?\n\nResource cost: {total:,} of your {balance:,}."
        else:
            qty=simpledialog.askinteger("Spend item",f"How many USES of {name}?\n\nLatest owned count: {balance:,}",minvalue=1,maxvalue=max(1,balance) if balance else None)
            if not qty:return
            confirm=f"Use {qty:,} × {name} [ID {item_id}]?"
        item_workers=1 if choice else max(1,min(12,int(self.nixon_workers.get() or 6)))
        if choice:confirm+=("\n\nAutomatic choice priority: Rogu → Characters → Items / Power Cards." if auto_choose else "\n\nCommander stops after this pull for you to select the offered reward.")
        elif qty>1:confirm+=f"\n\nParallel request workers: {min(item_workers,qty)}"
        if not messagebox.askyesno("Spend item",confirm):return
        def work():
            try:
                creds=load_credentials()
                if choice:
                    c=ATClient(creds)
                    try:catalog=c.card_catalog()
                    except Exception:catalog={}
                    live=c.init()
                    if not live or api_rejected(live):raise ValueError("Live inventory could not be verified; no item was opened.")
                    opts=self._extract_item_choice_options(live,catalog)
                    if live.get("active_choices") and not opts:raise ValueError("A pending reward could not be parsed; no additional item was opened.")
                    if opts:
                        msg="A reward choice is already pending. No additional item was opened."
                        done=0
                    else:
                        r=c.use_item(item_id,1)
                        if not r:raise ValueError("AT did not confirm item use; the item request was not retried.")
                        if api_rejected(r):raise ValueError("AT rejected item use; no reward choice was submitted.")
                        done=1;opts=self._extract_item_choice_options(r,catalog)
                        msg=f"Opened 1 pull of {name}."
                    if opts:
                        self.events.put({"kind":"item_choices","options":opts,"message":msg+" Reward options are ready."})
                        if auto_choose:
                            try:selected=choose_item_reward(opts)
                            except ValueError as exc:msg+=f" {exc} Use RESOLVE ITEM CHOICE."
                            else:
                                self._confirm_item_choice_worker(c,selected,catalog)
                                msg+=f" Selected {selected['name']} automatically."
                        else:msg+=" Use RESOLVE ITEM CHOICE."
                    else:msg+=" AT did not expose a recognizable choice list; no reward selection was guessed."
                    self.events.put({"kind":"inventory_progress","done":done,"total":1,"label":f"{name}: {done}/1 pull(s) opened"})
                    self.events.put({"kind":"itemresult","message":msg})
                else:
                    # Keep readable progress and use a resource-specific transport:
                    # Golden Turds use sequential native batches; other generic
                    # consumables retain the proven bounded one-pull path.
                    from concurrent.futures import ThreadPoolExecutor,as_completed
                    lock=threading.Lock(); done_box=[0]
                    self.events.put({"kind":"inventory_progress","done":0,"total":qty,"label":f"{name}: 0/{qty}"})
                    done=0
                    workers=min(item_workers,qty)
                    base=qty//workers;extra=qty%workers;chunks=[base+(1 if i<extra else 0) for i in range(workers)]
                    def use_chunk(n):
                        c=ATClient(creds);local=0
                        for _ in range(n):
                            r=c.use_item(item_id,1)
                            if isinstance(r,dict) and r.get("result") is False:
                                raise RuntimeError(str(r.get("result_message") or "AT rejected item use"))
                            local+=1
                            with lock:
                                done_box[0]+=1;d=done_box[0]
                            if d==qty or d==1 or d%max(1,qty//100)==0:
                                self.events.put({"kind":"inventory_progress","done":d,"total":qty,"label":f"{name}: {d}/{qty}"})
                        return local
                    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix="ATC-Item") as ex:
                        futs=[ex.submit(use_chunk,n) for n in chunks if n]
                        for f in as_completed(futs):done+=int(f.result())
                    msg=f"Item spend complete: {done:,}/{qty:,} pull/use(s) of {name}."
                    if cost:msg+=f" Resource cost: about {done*cost:,}."
                    self.events.put({"kind":"itemresult","message":msg})
                self.refresh_inventory()
            except ValueError as e:self.events.put({"kind":"error","message":f"Could not use {name}: {e}"})
            except Exception as e:self.events.put({"kind":"error","message":f"Could not use {name} ({type(e).__name__}); Commander stopped without replaying the item request."})
        threading.Thread(target=work,daemon=True).start()

    @staticmethod
    def _extract_item_choice_options(response,catalog=None):
        return extract_item_choices(response,catalog)

    @staticmethod
    def _item_choice_priority(row):
        return item_choice_priority(row)

    def _confirm_item_choice_worker(self,c,row,catalog=None):
        """Recheck a pending option, then use the already-proven selection call."""
        fresh=c.init()
        if api_rejected(fresh):raise ValueError("Could not verify the pending choice; no reward was selected.")
        options=self._extract_item_choice_options(fresh,catalog)
        if len({option.get('group') for option in options})>1:raise ValueError("Several choices are pending; resolve the current choice in the game before opening another item.")
        reward_ids=lambda option:[card.get('card_id') for card in option.get('cards') or []]
        current=next((option for option in options if option['id']==row['id'] and option.get('choice_type')==row.get('choice_type') and reward_ids(option)==reward_ids(row)),None)
        if current is None:raise ValueError("The pending reward changed; refresh and review it before selecting.")
        if current.get('choice_type') is None:raise ValueError("AT did not expose the choice type; no reward was selected.")
        response=c.call("confirmUserChoice",{"choice":current['id'],"type":current['choice_type']})
        if not item_choice_confirmed(response,current['choice_type']):
            # An incomplete response can still have committed. Read state once,
            # but never replay an uncertain selection mutation.
            if api_rejected(response):raise ValueError("AT rejected the reward selection. The pending choice was preserved.")
            response=c.init()
            if not item_choice_confirmed(response,current['choice_type']):raise ValueError("AT did not confirm the reward choice. It was not retried.")
        self.events.put({"kind":"item_choice_done","message":f"Selected {current['name']}."})
        return response

    def _resolve_item_choice(self):
        if not self.pending_item_choices:
            messagebox.showinfo("Item choice","No unresolved item-choice options are cached. Open one choice-style item first, then use this button.");return
        auto_choose=bool(self.auto_item_choice.get())
        def select_row(row):
            def work():
                try:
                    c=ATClient(load_credentials())
                    try:catalog=c.card_catalog()
                    except Exception:catalog={}
                    self._confirm_item_choice_worker(c,row,catalog);self.refresh_inventory()
                except ValueError as exc:self.events.put({"kind":"error","message":str(exc)})
                except Exception as exc:self.events.put({"kind":"error","message":f"Could not verify the reward selection ({type(exc).__name__}); no uncertain request was replayed."})
            threading.Thread(target=work,daemon=True).start()
        if auto_choose:
            try:recommended=choose_item_reward(self.pending_item_choices)
            except ValueError:pass
            else:select_row(recommended);return
        ranked=sorted(self.pending_item_choices,key=self._item_choice_priority)
        labels=[];label_to_row={}
        for i,r in enumerate(ranked):
            meta=[]
            if r.get("kind") and r.get("kind")!="Unknown":meta.append("Power Card" if r.get("kind")=="PowerCard" else str(r.get("kind")))
            if int(r.get("rarity") or 0)>0:meta.append(f"rarity {int(r.get('rarity'))}")
            suffix=(" — "+", ".join(meta)) if meta else ""
            text=f"{r['name']} [ID {r['id']}]{suffix}"+("  ← recommended" if i==0 else "")
            labels.append(text);label_to_row[text]=r
        win=tk.Toplevel(self.root);win.title("Resolve item choice");win.transient(self.root);win.resizable(False,False)
        ttk.Label(win,text="Choose the reward AT offered:",font=("Segoe UI Semibold",11)).pack(anchor="w",padx=12,pady=(12,3))
        ttk.Label(win,text="Recommendation order: Rogu → Characters → Items / Power Cards; then higher rarity, current catalog power, and health. Unknown types require manual review.",wraplength=560).pack(anchor="w",padx=12,pady=(0,7))
        var=tk.StringVar(value=labels[0]);cb=ttk.Combobox(win,textvariable=var,state="readonly",values=labels,width=68);cb.pack(padx=12,pady=(0,10))
        def go():
            row=label_to_row[var.get()];win.destroy()
            select_row(row)
        bar=ttk.Frame(win);bar.pack(fill="x",padx=12,pady=(0,12));ttk.Button(bar,text="CANCEL",command=win.destroy).pack(side="right");ttk.Button(bar,text="SELECT REWARD",style="Accent.TButton",command=go).pack(side="right",padx=6)

    def _recycle_trash_worker(self,c,common=True,rare=True,epic=True):
        """Recycle only conservative trash: level-1 Common/Rare/Epic, not in any saved deck, never foils."""
        allowed={rid for rid,on in ((1,common),(2,rare),(3,epic)) if on}
        if not allowed:return 0
        init=c.init(); units=init.get("user_units") or {}
        rows=list(units.values()) if isinstance(units,dict) else list(units) if isinstance(units,list) else []
        if not rows: raise RuntimeError("AT did not expose user_units in the live inventory, so safe recycle cannot choose cards without guessing.")
        decks=c.get_exact_decks(init).get("user_decks") or {}
        drows=decks.values() if isinstance(decks,dict) else decks if isinstance(decks,list) else []
        protected=set()
        for d in drows:
            if not isinstance(d,dict):continue
            for u in d.get("units") or []:
                if isinstance(u,dict) and u.get("unit_index") is not None: protected.add(str(u.get("unit_index")))
        try: catalog=c.card_catalog()
        except Exception as exc: raise RuntimeError(f"Could not load card rarity metadata, so recycle was cancelled safely: {exc}")
        candidates=[]
        for u in rows:
            if not isinstance(u,dict):continue
            idx=u.get("unit_index"); uid=u.get("unit_id")
            if idx is None or uid is None or str(idx) in protected:continue
            try: level=int(u.get("level") or 0)
            except Exception:continue
            if level!=1:continue
            try:
                if int(u.get("foil_id") or 0)>0:continue
            except Exception: pass
            meta=catalog.get(str(uid)) or {}
            try: rarity=int(u.get("rarity") or meta.get("rarity") or 0)
            except Exception:rarity=0
            if rarity in allowed:candidates.append(int(idx))
        for pos in range(0,len(candidates),100):
            batch=candidates[pos:pos+100]
            c.call("salvageUnitList",{"units":json.dumps(batch,separators=(",",":"))})
        return len(candidates)

    def _recycle_trash(self):
        common,rare,epic=bool(self.recycle_common.get()),bool(self.recycle_rare.get()),bool(self.recycle_epic.get())
        names=[n for n,on in (("Common",common),("Rare",rare),("Epic",epic)) if on]
        if not names:messagebox.showinfo("Recycle trash","Select at least one rarity.");return
        if not messagebox.askyesno("Recycle trash","Recycle loose level-1 "+"/".join(names)+" cards?\n\nCards used in ANY saved deck and all foils are protected. Legendary/Mythic are never included."):return
        def work():
            try:
                count=self._recycle_trash_worker(ATClient(load_credentials()),common,rare,epic);self.events.put({"kind":"itemresult","message":f"Recycle complete: {count} safe trash card(s) salvaged."});self.refresh_inventory()
            except Exception as e:self.events.put({"kind":"error","message":str(e)})
        threading.Thread(target=work,daemon=True).start()

    def _do_three_upgrades(self):
        if not messagebox.askyesno("Daily upgrades","Finish only the remaining Daily Upgrade Cards quest using loose level-1 Common/Rare/Epic cards?\n\nSaved-deck cards, foils, locked cards, Legendary and Mythic cards are protected. Commander verifies each actual level increase and stops if live quest progress is unknown."):return
        def work():
            done=0
            try:
                c=ATClient(load_credentials());init=c.init()
                daily=daily_upgrade_progress(init)
                if daily is None:
                    self.events.put({"kind":"itemresult","message":"AT did not expose a current Daily Upgrade Cards quest with verified progress. No card was touched."});return
                progress,maxp,needed=daily
                if needed<=0:
                    self.events.put({"kind":"itemresult","message":"Daily Upgrade Cards is already complete; no card was touched."});return
                decks=c.get_exact_decks(init);catalog=c.card_catalog()
                candidates=safe_upgrade_candidates(init,decks,catalog)
                if len(candidates)<needed:raise ValueError(f"Only {len(candidates)} safe loose Common/Rare/Epic card(s) were found, but {needed} upgrade(s) remain. No card was touched.")
                chosen=candidates[:needed]
                current_progress=progress
                for card in chosen:
                    # upgradeUnit(unit_index) is the established Cards.js action;
                    # no learned/frozen card ID is used for this mutation.
                    response=c.call("upgradeUnit",{"unit_index":card['unit_index']})
                    if api_rejected(response):raise ValueError("AT rejected the upgrade. Commander stopped without counting it or retrying it.")
                    confirmed=upgrade_confirmed(response,card['unit_index'],1)
                    if confirmed:done+=1
                    updated=c.init()
                    if not confirmed:
                        if not upgrade_confirmed(updated,card['unit_index'],1):raise ValueError("AT did not confirm an actual level increase; the upgrade was not retried.")
                        done+=1
                    self.events.put({"kind":"itemresult","message":f"Daily upgrade {done}/{needed}: {card['name']}."})
                    remaining=daily_upgrade_progress(updated)
                    if remaining is None:raise ValueError("The upgraded card was verified, but current Daily quest progress became unavailable. No additional card was touched.")
                    if remaining[0]<=current_progress:raise ValueError("The card level increased but the Daily quest did not advance. No additional card was touched.")
                    current_progress=remaining[0]
                    if remaining[2]<=0:break
                self.events.put({"kind":"itemresult","message":f"Daily upgrade automation complete: performed {done} safe upgrade(s); prior quest progress was {progress}/{maxp}."});self.refresh_inventory()
            except ValueError as e:self.events.put({"kind":"error","message":f"Daily upgrade stopped after {done} verified upgrade(s): {e}"});self.refresh_inventory()
            except Exception as e:self.events.put({"kind":"error","message":f"Daily upgrade stopped after {done} verified upgrade(s) ({type(e).__name__}); no uncertain upgrade was replayed."})
        threading.Thread(target=work,daemon=True).start()


    def _run_swole_protocol_lab(self):
        if CommandCenterMixin._browser_action_busy(self):
            if not getattr(self,"_closing",False):messagebox.showinfo("Protocol Lab","Wait for Game / Ads or stop active automation before running the diagnostic lab.")
            return
        self.ad_stop.clear()
        def work():
            try:
                if self.ad_stop.is_set():return
                self.events.put({"kind":"diaglog","message":"Protocol Lab starting. This uses the saved Swole Fight click, captures the real signed startChallenge call stack/source context, and blocks the request before AT receives it."})
                out=run_swole_signature_lab(lambda m:self.events.put({"kind":"diaglog","message":str(m)}),should_stop=self.ad_stop.is_set)
                self.events.put({"kind":"diaglog","message":f"Protocol Lab report ready: {out}"})
                self.events.put({"kind":"labdone","path":str(out)})
            except Exception:
                self.events.put({"kind":"error","message":"Protocol Lab stopped or could not be confirmed. Its click was not repeated."})
        self.ad_thread=threading.Thread(target=work,daemon=True);self.ad_thread.start()

    def _save_credentials_gui(self):
        c=load_credentials(); uid=simpledialog.askstring("AT User ID","AT user ID:",initialvalue=c.user_id or "")
        if not uid:return
        ph=simpledialog.askstring("AT Login Hash","AT login hash (stored locally; treat like a password):",show="*")
        if not ph:return
        save_credentials(uid,ph);self._refresh_account_badge();self._refresh_account_metadata();messagebox.showinfo("AT Commander","Credentials saved.")

    def export_selector_metadata(self):
        def work():
            try:
                init=ATClient(load_credentials()).init(); keep=("deck","hero","commander","siege","island","swole","troph","arena","pvp","medal","token","item","inventory")
                def scrub(o,path="",depth=0):
                    if depth>9:return None
                    if isinstance(o,dict):
                        out={}
                        for k,v in o.items():
                            lk=str(k).lower();p=f"{path}.{k}" if path else str(k)
                            if (any(x in lk for x in ("password","login_hash","password_hash","cookie","authorization","kong_vars"))
                                or "token" in lk or "session" in lk or "secret" in lk or lk.endswith("sig")):continue
                            child=scrub(v,p,depth+1)
                            if any(t in p.lower() for t in keep) or child not in (None,{},[]):out[k]=child if child is not None else v
                        return out
                    if isinstance(o,list):return [x for x in (scrub(v,f"{path}[]",depth+1) for v in o) if x not in (None,{},[])]
                    return o if any(t in path.lower() for t in keep) else None
                out=APP_DIR/"selector_metadata_sanitized.json";out.write_text(json.dumps(scrub(init) or {},indent=2),encoding="utf-8");self.events.put({"kind":"diaglog","message":f"Saved sanitized selector metadata: {out}"})
            except Exception as e:self.events.put({"kind":"error","message":str(e)})
        threading.Thread(target=work,daemon=True).start()

    def _open_learning_browser(self):
        self._open_game_panel()
        self.learn_browser_status.set("The saved game session opens inside Commander.")

    def _background_learning_browser(self):
        self._show_game_panel(False)
        self.learn_browser_status.set("Game panel hidden inside Commander; its saved session remains available.")

    def _close_learning_browser(self):
        if CommandCenterMixin._browser_action_busy(self):
            if not getattr(self,"_closing",False):messagebox.showinfo("Learning Browser","Wait for active work to finish, or use Stop All before closing the game browser.")
            return
        if getattr(self,"learn_session",None):
            messagebox.showinfo("Learning Browser","Finish the active capture before closing the browser."); return
        from .embedded_browser import close_owned_browser
        from .ad_browser_focus import release_all_game_input_focus
        release_all_game_input_focus();close_owned_browser();self.learning_browser.close()
        self._show_game_panel(False)
        self.learn_browser_status.set("Game closed. Your dedicated login profile is kept for next time."); self._diag_log("Game closed; persistent profile kept.")

    def start_learning(self):
        sel=self.learn_tree.selection()
        if not sel:messagebox.showinfo("Learn action","Select one action in the table first.");return
        key=self.learn_tree.item(sel[0],"tags")[0]; row=next(x for x in LEARN_ACTIONS if x[0]==key)
        if key in BUILTIN_ACTIONS:
            messagebox.showinfo("Already built in",f"{row[1]} uses a confirmed direct AT call in this build and does not need another learning capture.")
            return
        if self.learn_session:messagebox.showinfo("Learn action","A capture is already active. Finish it first.");return
        ATCommanderApp._start_legacy_learning_capture(self,key,row=row)

    def _start_legacy_learning_capture(self,key,*,row=None,siege=False):
        """Reserve startup and the manual recording interval on the ad worker."""
        if CommandCenterMixin._browser_action_busy(self):
            if not getattr(self,"_closing",False):messagebox.showinfo("Learn action","Wait for Game / Ads or stop active automation before starting a capture.")
            return
        self.ad_stop.clear();done=threading.Event();self.learn_capture_done=done
        def work():
            session=None;handed_off=False
            try:
                if self.ad_stop.is_set():return
                ok,msg=self.learning_browser.ensure_open()
                if self.ad_stop.is_set():return
                if not ok:
                    self.events.put({"kind":"learning_capture_ready","ok":False,"message":msg,"completion":done});return
                session=LearnSession(key);path=session.start()
                if self.ad_stop.is_set():return
                self.events.put({"kind":"learning_capture_ready","ok":True,"key":key,"row":row,"siege":siege,"path":path,"session":session,"completion":done,"message":msg})
                handed_off=True
                while not done.wait(.2):
                    if self.ad_stop.is_set():break
            except Exception:
                self.events.put({"kind":"learning_capture_ready","ok":False,"message":"The learning recorder could not be armed.","completion":done})
            finally:
                if session is not None and (self.ad_stop.is_set() or not handed_off or not done.is_set()):
                    try:session.stop()
                    except Exception:pass
                done.set()
                if self.ad_stop.is_set():self.events.put({"kind":"learning_capture_stopped","session":session,"completion":done})
        self.ad_thread=threading.Thread(target=work,daemon=True);self.ad_thread.start()

    def _remember_adventure_learned_mission(self):
        prof=(load_profiles().get("adventure_start") or {})
        captured=prof.get("captured_data") if isinstance(prof,dict) else {}
        mid=""
        if isinstance(captured,dict):
            for k,v in captured.items():
                if "mission" in str(k).lower() and v not in (None,""):
                    mid=str(v);break
        label=self.adventure_kind.get() if hasattr(self,"adventure_kind") else ""
        if not mid or not label.startswith(("Live Event:","Seasonal / Event:")):
            return False
        name=label.split(":",1)[-1].split(" •",1)[0].strip()
        if not name:return False
        st=load_settings(); maps=dict(st.get("adventure_event_missions") or {})
        maps[name.lower()]={"mission_id":mid,"learned_at":time.time()}
        st["adventure_event_missions"]=maps; save_settings(st); self.settings=st
        self._append(self.adventure_log,f"Learned {name} native Adventure mission {mid} from the successful browser start; 9040-style campaign markers will no longer be used as mission IDs.")
        self._refresh_account_metadata(force_adventure_static=False)
        return True

    def finish_learning(self):
        if not self.learn_session:messagebox.showinfo("Learn action","No capture is active.");return
        key=self.learn_key;path=self.learn_session.stop(); self.last_capture_key=key; self.last_capture_path=path; auto=False
        done=getattr(self,"learn_capture_done",None)
        if done is not None:done.set()
        if self.ad_stop.is_set() or getattr(self,"_closing",False):
            self.learn_session=None;self.learn_key=None
            if not getattr(self,"_closing",False):self.learn_status.set("Learning capture stopped. The partial trace was not installed.")
            return
        if key!="ad_flow":
            try:auto=install_profile(key,path)
            except Exception as exc:self._diag_log(f"Auto-link error: {exc}");auto=False
        self._diag_log(f"Capture stopped: {path}")
        self.learn_session=None;self.learn_key=None
        if key=="ad_flow":
            self.learn_status.set("Ad-flow capture saved locally. Learning Browser stays open for the next action.")
            messagebox.showinfo("Ad capture saved",f"Saved the sanitized legitimate ad-flow trace locally. No upload is needed unless we specifically need to inspect it later.\n\n{path}")
        elif auto:
            if key=="adventure_start":
                try:self._remember_adventure_learned_mission()
                except Exception as exc:self._diag_log(f"Could not bind learned Adventure mission to selected event: {exc}")
            self.learn_status.set(f"{key.replace('_',' ').title()} learned. Select the next action; keep using the same browser/login.")
            messagebox.showinfo("Action learned",f"{key.replace('_',' ').title()} was learned and installed. The Learning Browser stays open and logged in for the next action.")
        else:
            cands=profile_candidates_from_trace(key,path)
            self.learn_status.set("Capture saved locally. AT Commander needs you to choose which recorded request was the action; no upload required.")
            if cands:self._review_capture_popup(key,path,cands)
            else:messagebox.showinfo("Capture saved",f"No useful AT API request was detected during that capture. Keep the Learning Browser open and try the action once more.\n\nNothing needs to be uploaded.")
        self._refresh_protocol_status()

    def review_last_capture(self):
        if not self.last_capture_path or not Path(self.last_capture_path).exists():
            messagebox.showinfo("Review capture","No capture is available yet."); return
        key=self.last_capture_key; cands=profile_candidates_from_trace(key,self.last_capture_path)
        if not cands:messagebox.showinfo("Review capture","No candidate AT API requests were found in the last capture.");return
        self._review_capture_popup(key,self.last_capture_path,cands)

    def _review_capture_popup(self,key,path,cands):
        win=tk.Toplevel(self.root); win.title("Choose the action request"); win.geometry("850x430"); win.transient(self.root)
        ttk.Label(win,text=f"AT Commander could not choose automatically for: {key.replace('_',' ').title()}",font=("Segoe UI Semibold",12)).pack(anchor="w",padx=12,pady=(12,2)); ttk.Label(win,text="Choose the request that appeared when you performed the action. This stays local; you do not need to send a trace file.").pack(anchor="w",padx=12,pady=(0,8))
        tree=ttk.Treeview(win,columns=("score","message","fields"),show="headings"); tree.heading("score",text="Match"); tree.heading("message",text="AT API request"); tree.heading("fields",text="Fields"); tree.column("score",width=70,anchor="e"); tree.column("message",width=260); tree.column("fields",width=470); tree.pack(fill="both",expand=True,padx=12,pady=6)
        for i,c in enumerate(cands[:25]):tree.insert("","end",iid=str(i),values=(c.get("score"),c.get("message"),", ".join(c.get("keys") or [])))
        if cands:tree.selection_set("0")
        bar=ttk.Frame(win); bar.pack(fill="x",padx=12,pady=(4,12))
        def use_selected():
            sel=tree.selection()
            if not sel:return
            cand=cands[int(sel[0])]
            if install_candidate_profile(key,cand):
                self._diag_log(f"Manually linked {key} to API request {cand.get('message')}")
                if key=="adventure_start":
                    try:self._remember_adventure_learned_mission()
                    except Exception as exc:self._diag_log(f"Could not bind learned Adventure mission: {exc}")
                self._refresh_protocol_status(); self.learn_status.set(f"{key.replace('_',' ').title()} learned. Continue with the next action in the same browser."); win.destroy(); messagebox.showinfo("Action learned",f"Installed {cand.get('message')} for {key.replace('_',' ')}. Test it once before unattended use.")
            else:
                messagebox.showinfo("Mutation captured", "Commander saw the browser mutation, but it did not expose a replayable AT message name. Do not keep repeating the action. The sanitized local capture now contains the host/path/body clues needed for the next patch.")
        ttk.Button(bar,text="USE SELECTED REQUEST",style="Accent.TButton",command=use_selected).pack(side="right"); ttk.Button(bar,text="CANCEL / RETRY",command=win.destroy).pack(side="right",padx=6)

    def _arm_live_combat_capture(self,key):
        """Recovery-only capture for a moved/changed Fight control.

        Normal 1.7.22 Swole/Challenge startup does not require a manual fight
        once the button position has already been learned.
        """
        if key not in {"swole_battle","challenge_battle"}:
            return
        if CommandCenterMixin._browser_action_busy(self):
            if not getattr(self,"_closing",False):messagebox.showinfo("Live combat capture","Wait for Game / Ads or stop active automation before starting repair capture.")
            return
        label="Swole" if key=="swole_battle" else "Challenge / Clash / Melee"
        self.ad_stop.clear()
        def work():
            mode="Swole" if key=="swole_battle" else "Challenge"
            def target_log(message): self.events.put({"kind":"log","mode":mode,"message":str(message)})
            if self.ad_stop.is_set():return
            ok,msg=self.learning_browser.ensure_open()
            if self.ad_stop.is_set():return
            if not ok:target_log("REPAIR CAPTURE: browser preparation failed.");return
            self.learning_browser.show()
            if self.ad_stop.is_set():return
            target_log(f"REPAIR CAPTURE ARMED: manually start exactly ONE {label} fight only to relearn a moved Fight button. Normal 1.7.22 startup does not require this step.")
            row=capture_next_successful_api_request("startChallenge",timeout=60.0,should_stop=self.ad_stop.is_set)
            if self.ad_stop.is_set():return
            if not row.get("ok"):
                target_log("REPAIR CAPTURE: "+str(row.get("message") or "No successful startChallenge was captured."));return
            profiles=load_profiles(); prev=profiles.get(key) if isinstance(profiles.get(key),dict) else {}
            prof=dict(prev or {})
            click=row.get("click") if isinstance(row.get("click"),dict) else {}
            prof.update({
                "message":"startChallenge",
                "data":prof.get("data") or {},
                "browser_request_id":str(row.get("request_id") or ""),
                "browser_target_id":str(row.get("target_id") or ""),
                "captured_data":dict(row.get("action_fields") or {}),
                "native_captured_at":time.time(),
                "native_resource_type":str(row.get("resource_type") or ""),
                "native_field_names":list(row.get("field_names") or []),
                "native_initiator":dict(row.get("initiator") or {}),
                "browser_click_x":click.get("x"),
                "browser_click_y":click.get("y"),
                "browser_click_target_id":str(click.get("target_id") or row.get("target_id") or ""),
                "browser_click_target_url":str(click.get("target_url") or ""),
            })
            data=dict(prof.get("data") or {})
            for k,v in (row.get("action_fields") or {}).items():
                lk=str(k).lower()
                if lk=="challenge_id": data[k]="{challenge_id}"
                elif lk=="event_id": data[k]="{event_id}"
                elif lk=="level": data[k]="{level}"
                elif lk=="auto_battle": data[k]="0"
                elif k not in data:data[k]=v
            if self.ad_stop.is_set():return
            prof["data"]=data;profiles[key]=prof;save_profiles(profiles)
            names=", ".join(row.get("field_names") or []) or "(none exposed)"
            ini=row.get("initiator") or {}; ih=(ini.get("function") or ini.get("url") or ini.get("type") or "unknown")
            clicknote=(f" Fight-click repaired at ({click.get('x'):.0f},{click.get('y'):.0f}); future runs can reuse it without another manual fight." if click.get('x') is not None and click.get('y') is not None else " No fight-button click was resolved; background signing cannot be used until the position is repaired.")
            target_log(f"REPAIR CAPTURE SAVED: {row.get('resource_type') or 'request'} startChallenge. Fields: {names}. Initiator: {ih}.{clicknote}")
            target_log("Commander intentionally left the mini game visible after this repair capture so you can exit/finish that one repair fight normally. Put it back on the pre-fight screen and click BACKGROUND GAME. Normal startup does not require another fight.")
            self.events.put({"kind":"protocol_refresh"})
        def guarded_work():
            try:work()
            except Exception:self.events.put({"kind":"log","message":"Repair capture could not be confirmed. No capture or click was repeated."})
        self.ad_thread=threading.Thread(target=guarded_work,daemon=True);self.ad_thread.start()
        messagebox.showinfo("Repair Fight-button capture",f"This button is recovery-only.\n\nUse it only if Commander says the learned Fight control is missing or moved. In the mini AT browser, manually start exactly ONE {label} fight so Commander can repair the button position.\n\nNormal 1.7.22 startup does not require a manual fight.")

    def _open_app_dir(self):
        try:
            import os;os.startfile(APP_DIR)
        except Exception:pass

    def _save_settings(self):
        names={saved_name_key(key):label_parts(getattr(self,var).get())[1] or "" for var,key in DECK_BINDINGS}
        save_settings({
            **load_settings(),
            **names,
            "move_delay":self.delay_var.get(),"battle_delay":self.battle_delay_var.get(),
            "arena_limit":self.limit_var.get(),"arena_reserve_energy":self.reserve_var.get(),
            "arena_continuous":bool(self.continuous_var.get()),"auto_watch_ads":bool(self.auto_ads_var.get()),
            "arena_bypass_turn_limit":bool(self.arena_bypass_turn_limit_var.get()),
            "arena_deck_id":self._selected_id(self.deck_var.get(),self.deck_rows) or "",
            "arena_hero_id":self._selected_id(self.hero_var.get(),self.hero_rows) or "",
            "rumble_defense_deck_id":self._selected_id(self.rumble_deck.get(),self.deck_rows) or "",
            "rumble_offense_deck_id":self._selected_id(self.rumble_offense_deck.get(),self.deck_rows) or "",
            "rumble_offense_hero_id":self._selected_id(self.rumble_offense_hero.get(),self.hero_rows) or "",
            "siege_defense_deck_id":self._selected_id(self.siege_deck.get(),self.deck_rows) or "",
            "siege_offense_deck_id":self._selected_id(self.siege_offense_deck.get(),self.deck_rows) or "",
            "siege_offense_hero_id":self._selected_id(self.siege_offense_hero.get(),self.hero_rows) or "",
            "siege_island_id":self._selected_island_id(self.siege_island.get()) or "",
            "siege_target_a_id":self._selected_siege_target_id(self.siege_target_a.get()) or "",
            "siege_target_b_id":self._selected_siege_target_id(self.siege_target_b.get()) or "",
            "siege_target_c_id":self._selected_siege_target_id(self.siege_target_c.get()) or "",
            "siege_fallback_any":bool(self.siege_fallback_any.get()),"siege_poll_seconds":self.siege_poll.get(),
            "ad_click_sequence":self.settings.get("ad_click_sequence") or [],"ad_play_click":self.settings.get("ad_play_click") or {},
            "static_resource_urls":load_settings().get("static_resource_urls") or {},
            "challenge_offense_deck_id":self._selected_id(self.challenge_offense_deck.get(),self.deck_rows) or "",
            "challenge_offense_hero_id":self._selected_id(self.challenge_offense_hero.get(),self.hero_rows) or "",
            "rumble_logic":self.rumble_logic.get(),"siege_logic":self.siege_logic.get(),
            "challenge_logic":self.challenge_logic.get(),"swole_poll_seconds":self.swole_poll.get(),
            "melee_character_first":bool(self.melee_character_first_var.get()) if hasattr(self,"melee_character_first_var") else False,
            "adventure_limit":self.adventure_limit.get(),"adventure_reserve_energy":self.adventure_reserve.get(),
            "adventure_continuous":bool(self.adventure_continuous.get()),
            "adventure_complete_islands":getattr(self,"adventure_mode",None) is not None and self.adventure_mode.get()==ADVENTURE_MODES[1],
            "adventure_completion_target":ADVENTURE_GOALS.get(self.adventure_completion_goal.get(),"mastery") if hasattr(self,"adventure_completion_goal") else "mastery",
            "adventure_mission_id":str(self.adventure_mission_id.get()) if hasattr(self,"adventure_mission_id") else self.settings.get("adventure_mission_id",""),
            "adventure_farm_kind":self.adventure_kind.get() if hasattr(self,"adventure_kind") else self.settings.get("adventure_farm_kind","Main Adventure"),
            "adventure_island":self.adventure_island_num.get() if hasattr(self,"adventure_island_num") else self.settings.get("adventure_island",3),
            "adventure_stage":self.adventure_stage_num.get() if hasattr(self,"adventure_stage_num") else self.settings.get("adventure_stage",1),
            "adventure_event_missions":self.settings.get("adventure_event_missions") or {},
            "nixon_workers":max(1,min(12,int(self.nixon_workers.get() or 6))),
            "auto_item_choice":bool(self.auto_item_choice.get()),
            "golden_shop_interval_seconds":self.settings.get("golden_shop_interval_seconds",.10),
        })

    def _refresh_protocol_status(self):
        p=load_profiles()
        self.rumble_status.set("Ready" if p.get("rumble") else "Needs one Rumble capture")
        self.siege_status.set("Direct signed API ready • waits for the Battle phase")
        if hasattr(self,"learn_tree"):
            for x in self.learn_tree.get_children():self.learn_tree.delete(x)
            for key,name,what in LEARN_ACTIONS:
                status="BUILT IN" if key in BUILTIN_ACTIONS else ("READY" if p.get(key) else "NEEDS LEARNING")
                if key=="ad_flow":
                    what="Boost ads open and play automatically in the dedicated browser; the game must confirm the benefit."
                elif status=="BUILT IN" and not what.startswith("Built in:"):
                    what="Available through Commander's direct API tools; no teaching capture is required."
                elif status=="READY":
                    what="A recorded protocol is available. Use the app's controls; no new capture is required."
                self.learn_tree.insert("","end",values=(name,status,what),tags=(key,))

    def _drain_events(self):
        try:
            # A busy worker must not monopolize Tk or prevent Stop input.
            for _ in range(50):
                try:event=self.events.get_nowait()
                except queue.Empty:break
                try:self._handle_event(event)
                except Exception as error:
                    self.root.report_callback_exception(type(error),error,error.__traceback__)
        finally:
            try:self.root.after(100,self._drain_events)
            except tk.TclError:pass
    def _handle_event(self,e):
        if RewardCleanupMixin._reward_event(self,e):return
        if DeckBuilderMixin._deck_builder_event(self,e):return
        if AppUpdateMixin._app_update_event(self,e):return
        if ClientUpdateMixin._client_update_event(self,e):return
        if hasattr(self,"research_status") and self._research_event(e):return
        if self._command_event(e):return
        k=e.get("kind");mode=e.get("mode")
        if k=="selectors":
            self._apply_account_selectors(e.get("decks"),e.get("heroes"))
        elif k=="metadata":
            self._apply_account_selectors(e.get("decks") or [],e.get("heroes") or [])
            self.siege_island_rows=e.get("islands") or [];self.siege_target_rows=e.get("siege_targets") or [];self.progress_rows=e.get("progress") or [];self.hero_medal_rows=e.get("medals") or [];self.current_snapshot=e.get("snapshot") or {}
            islands=["Current / unchanged"]+[f"{r.get('name')} — {r.get('hp')}/{r.get('max_hp')} HP  [ID {r.get('id')}]" if r.get('max_hp') is not None else f"{r.get('name')}  [ID {r.get('id')}]" for r in self.siege_island_rows if str(r.get("resolved","1"))=="1"]
            if len(islands)==1:islands += ["Island 1","Island 2","Island 3","Island 4","Island 5"]
            self.siege_island_combo["values"]=islands
            targets=["Any / none"]+[f"{r.get('name')} — {r.get('hp')}/{r.get('max_hp')} HP  [ID {r.get('id')}]" for r in self.siege_target_rows]
            for cb in (self.siege_target_a_combo,self.siege_target_b_combo,self.siege_target_c_combo): cb["values"]=targets
            def _target_label(saved_id):
                return next((x for x in targets if saved_id and f"[ID {saved_id}]" in x),"Any / none")
            if self.siege_target_a.get()=="Any / none": self.siege_target_a.set(_target_label(str(self.settings.get("siege_target_a_id") or "")))
            if self.siege_target_b.get()=="Any / none": self.siege_target_b.set(_target_label(str(self.settings.get("siege_target_b_id") or "")))
            if self.siege_target_c.get()=="Any / none": self.siege_target_c.set(_target_label(str(self.settings.get("siege_target_c_id") or "")))
            phase=e.get("siege_phase"); attacks=e.get("siege_attacks"); max_attacks=e.get("siege_max_attacks")
            if phase is not None:
                phase_label={0:"Opt in",1:"Matchmaking",2:"Attack live",3:"Scoring",4:"Ended"}.get(int(phase),f"Phase {phase}")
                self.siege_status.set(f"{phase_label} • attacks {attacks}/{max_attacks}" if attacks is not None and max_attacks is not None else phase_label)
            self.nixon_pack_rows=e.get("nixon_packs") or []
            pvals=[f"{r['name']} — {int(r['cost']):,} Nixons [ID {r['id']}]" for r in self.nixon_pack_rows]
            if not pvals:pvals=["Basic Pack — 1,000 Nixons [ID 1]","Giggitywatts — 3,000 Nixons [ID 83]","Rare Basic Pack — 5,000 Nixons [ID 65]","Epic Basic Pack — 50,000 Nixons [ID 66]"]
            self.nixon_pack_combo["values"]=pvals
            if self.nixon_pack_var.get() not in pvals:self.nixon_pack_var.set(pvals[0])
            self.challenge_rows=e.get("challenges") or []
            cvals=[]
            now=int(time.time())
            for r in self.challenge_rows:
                state=str(r.get("state") or "")
                if state=="Active":
                    ce=r.get("energy"); cm=r.get("max_energy")
                    suffix=(f"ACTIVE • {ce}/{cm} energy" if ce is not None and cm is not None else "ACTIVE")
                else:
                    try:
                        sec=max(0,int(r.get("start_time") or 0)-now); suffix=f"starts in {sec//3600}h {(sec%3600)//60}m" if sec else state.upper()
                    except Exception:suffix=state.upper()
                cvals.append(f"{r.get('name')} — {suffix} [ID {r.get('id')}]")
            if not cvals:cvals=["No detected challenge"]
            self.challenge_combo["values"]=cvals
            if self.challenge_var.get() not in cvals:self.challenge_var.set(cvals[0])
            active_count=sum(1 for r in self.challenge_rows if r.get("state")=="Active")
            self.challenge_status.set(f"Direct API ready • {active_count} active and {max(0,len(self.challenge_rows)-active_count)} upcoming Challenge/Clash/Melee event(s)." if active_count else ("No active Challenge/Clash/Melee event; upcoming events are listed." if self.challenge_rows else "No Challenge/Clash/Melee event was exposed by the latest account refresh."))
            previous_adventure=self.adventure_kind.get()
            previous_campaign=find_adventure_campaign(self.adventure_campaign_rows,previous_adventure)
            self.adventure_campaign_rows=e.get("campaigns") or []
            avals=["Main Adventure"]
            for r in self.adventure_campaign_rows:
                if r.get("source")=="player_prefs_live_marker":
                    avals.append(f"Live Event: {r.get('name')} • stage mapping needed [Key {r.get('id')}]")
                elif r.get("source")=="learned_event_mission":
                    mid=((r.get("missions") or [{}])[0].get("id") if r.get("missions") else "?")
                    avals.append(f"Live Event: {r.get('name')} • learned mission {mid} [Key {r.get('id')}]")
                else:
                    avals.append(f"Seasonal / Event: {r.get('name')}{' • ACTIVE' if r.get('active') else ''} [Campaign {r.get('id') or '?'}]")
            avals.append("Custom / Event mission ID")
            self.adventure_kind_combo["values"]=avals
            self.adventure_kind.set(self._adventure_label_after_refresh(previous_adventure,avals,previous_campaign,self.adventure_campaign_rows))
            self._refresh_adventure_campaign_missions()
            ae=e.get("adventure_energy"); am=e.get("adventure_max_energy")
            if ae is not None:self.adventure_energy_var.set(f"Adventure energy: {int(ae):,} / {int(am):,}" if am is not None else f"Adventure energy: {int(ae):,} / —")
            ad=e.get("ad") or {}; bonus=ad.get("boost_level"); self.ad_var.set(f"Active • level {int(bonus)}" if isinstance(bonus,(int,float)) and bonus>0 else "Inactive" if bonus == 0 else "Unknown")
            if e.get("arena_energy") is not None:self.energy_var.set(str(e.get("arena_energy")))
            sr=e.get("swole_raw") or {}; tasks=sr.get("user_tasks") or {}; vals=tasks.values() if isinstance(tasks,dict) else tasks if isinstance(tasks,list) else []
            active=None
            for t in vals:
                if not isinstance(t,dict) or t.get("task_index") is None: continue
                try:
                    if t.get("task_type") is not None and int(t.get("task_type"))!=2: continue
                except Exception: pass
                active=t; break
            if active:
                hid=str(active.get("task_id_1") or ""); row=next((r for r in self.hero_rows if str(r.get("id"))==hid),{}); hn=row.get("name") or f"Hero {hid}"
                try: cur=int(row.get("training_level") or 0)
                except Exception: cur=0
                try: end=int(active.get("end_time") or 0); now=int(sr.get("time") or time.time())
                except Exception: end=0; now=int(time.time())
                if end and end<=now:
                    self.swole_status.set(f"Training complete • {hn} → level {cur+1} • ready to claim")
                elif end:
                    rem=max(0,end-now); hrs=rem//3600; mins=(rem%3600)//60
                    self.swole_status.set(f"Training {hn} toward level {cur+1} • {hrs}h {mins}m remaining")
                else:self.swole_status.set(f"Training {hn} toward level {cur+1}")
            else:self.swole_status.set("Ready • no active Swole training task")
            self.lifetime.ensure_baseline(self.current_snapshot);self.lifetime_data=self.lifetime.to_dict();self._render_inventory();self._render_stats();self._render_progress();self._refresh_protocol_status();self._refresh_account_badge()
            unresolved_decks=sum(r.get("resolved")!="1" for r in self.deck_rows);unresolved_heroes=sum(r.get("resolved")!="1" for r in self.hero_rows)
            if unresolved_decks or unresolved_heroes:self._diag_log(f"Still unresolved: {unresolved_decks} deck IDs, {unresolved_heroes} hero IDs. Export selector metadata if a named deck/hero is missing.")
        elif k=="status":
            msg=str(e.get("text") or "");
            if mode in ("Rumble","Siege","Challenge"):self._event_log(msg)
            elif mode=="Swole":self._swole_log(msg)
            elif mode=="Adventure":self._append(self.adventure_log,msg)
            else:self.status_var.set(msg);self._log(msg)
        elif k=="log":
            msg=str(e.get("message") or "");
            if mode in ("Rumble","Siege","Challenge"):self._event_log(msg)
            elif mode=="Swole":self._swole_log(msg)
            elif mode=="Adventure":self._append(self.adventure_log,msg)
            else:self._log(msg)
        elif k=="eventlog":self._event_log(str(e.get("message") or ""))
        elif k=="labdone":
            path=str(e.get("path") or "")
            self._diag_log(f"Protocol Lab finished: {path}")
            messagebox.showinfo("Protocol Lab","Safe Swole protocol report created. The startChallenge request was blocked before transmission, so no Swole energy should have been spent.\n\nUpload this file here for analysis:\n"+path)
        elif k=="diaglog":self._diag_log(str(e.get("message") or ""))
        elif k=="protocol_refresh":self._refresh_protocol_status()
        elif k=="inventory_progress":
            done=int(e.get("done") or 0);total=int(e.get("total") or 0);pct=(100.0*done/total) if total>0 else 0.0
            self.inventory_progress_var.set(pct);self.inventory_progress_text.set(str(e.get("label") or (f"{done}/{total}" if total else "")))
        elif k=="shop_pacing":
            interval=max(.10,min(4.25,float(e.get("interval") or .10)))
            self.settings["golden_shop_interval_seconds"]=interval
            stored=load_settings();stored["golden_shop_interval_seconds"]=interval;save_settings(stored)
        elif k=="itemresult":
            self.item_action_var.set(str(e.get("message") or "")[:1000])
            if getattr(self,"inventory_progress_var",None) is not None and self.inventory_progress_var.get()>=99.9:self.inventory_progress_text.set("Complete")
        elif k=="item_choices":
            self.pending_item_choices=list(e.get("options") or []);self.resolve_choice_btn.configure(state="normal" if self.pending_item_choices else "disabled");self.item_action_var.set(str(e.get("message") or "Choice options ready."))
        elif k=="item_choice_done":
            self.pending_item_choices=[];self.resolve_choice_btn.configure(state="disabled");self.item_action_var.set(str(e.get("message") or "Item choice resolved."))
        elif k=="session_start":self.session_data=e.get("tracker") or {};self.lifetime_data=e.get("lifetime") or self.lifetime_data;self._render_stats()
        elif k=="battle_start":
            if mode in ("Rumble","Siege","Challenge"):self._event_log(f"{mode} battle {e.get('number')} started vs {e.get('opponent','Unknown')}.")
            elif mode=="Swole":self._swole_log(f"Swole battle {e.get('number')} started on floor {e.get('level','—')} vs {e.get('opponent','Unknown')}.")
            elif mode=="Adventure":self._append(self.adventure_log,f"Battle {e.get('number')} started vs {e.get('opponent','Unknown')}.")
            else:self.status_var.set("Battling");self.opponent_var.set(f"Opponent: {e.get('opponent','Unknown')} | Battle #{e.get('number')}");self._log(f"Battle {e.get('number')} started vs {e.get('opponent','Unknown')}.")
        elif k=="move":
            target=f" onto UID {e.get('card_uid2')}" if e.get("card_uid2") is not None else "";verb="COMBO" if e.get("action")=="combo" else "PLAY";txt=f"Move {e.get('move')}: {verb} UID {e.get('card_uid')}{target} — {e.get('reason')}"
            if mode in ("Rumble","Siege","Challenge"):self._event_log(txt)
            elif mode=="Swole":self._swole_log(txt)
            elif mode=="Adventure":self._append(self.adventure_log,txt)
            else:self.action_var.set(txt);self._log(txt)
        elif k=="energy":
            if mode=="Adventure":
                cur=e.get("current"); mx=e.get("max")
                self.adventure_energy_var.set(f"Adventure energy: {int(cur):,} / {int(mx):,}" if cur is not None and mx is not None else f"Adventure energy: {cur} / —")
            else:self.energy_var.set(str(e.get("current")))
        elif k=="adventure_energy":
            cur=e.get("current"); mx=e.get("max")
            if cur is not None:self.adventure_energy_var.set(f"Adventure energy: {int(cur):,} / {int(mx):,}" if mx is not None else f"Adventure energy: {int(cur):,} / —")
        elif k=="battle_end":
            won=e.get("won");out="WIN" if won is True else "LOSS" if won is False else "COMPLETE";self.lifetime_data=e.get("lifetime") or self.lifetime_data
            if mode in ("Rumble","Siege","Challenge"):self._event_log(f"{mode} battle {e.get('number')} finished: {out} vs {e.get('opponent')} in {e.get('moves')} moves.")
            elif mode=="Swole":
                before=e.get("points_before");after=e.get("points_after")
                proof=(f" • server score {before} → {after}" if e.get("post_status_verified") else " • server score verification unavailable")
                self._swole_log(f"Swole battle {e.get('number')} finished: {out} in {e.get('moves')} moves{proof}.")
                if e.get("tickets_after") is not None and hasattr(self,"swole_tickets"):
                    self.swole_tickets.set(f"Swole tickets: {e['tickets_after']} / {self._swole_ticket_max if hasattr(self,'_swole_ticket_max') else '—'}")
            elif mode=="Adventure":self._append(self.adventure_log,f"Battle {e.get('number')} finished: {out} in {e.get('moves')} moves.")
            else:self.session_data=e.get("tracker") or self.session_data;self._log(f"Battle {e.get('number')} finished: {out} vs {e.get('opponent')} in {e.get('moves')} moves.");self._render_stats()
        elif k=="snapshot":self.session_data=e.get("tracker") or self.session_data;self.lifetime_data=e.get("lifetime") or self.lifetime_data;self.current_snapshot=(self.session_data or {}).get("current_snapshot") or self.current_snapshot;self._render_inventory();self._render_stats()
        elif k=="manual_snapshot":self.current_snapshot=e.get("snapshot") or self.current_snapshot;self.lifetime_data=e.get("lifetime") or self.lifetime_data;self.progress_rows=e.get("progress") or self.progress_rows;self.hero_medal_rows=e.get("medals") or self.hero_medal_rows;self._render_inventory();self._render_stats();self._render_progress()
        elif k=="swole_energy":
            cur=e.get("current");mx=e.get("max");cost=e.get("cost") or 1
            self._swole_ticket_max=mx
            if hasattr(self,"swole_tickets"):
                self.swole_tickets.set(f"Swole tickets: {cur if cur is not None else '—'} / {mx if mx is not None else '—'}")
            self._swole_log(f"Swole fight tickets: {cur if cur is not None else 'unknown'}/{mx if mx is not None else 'unknown'} • cost {cost}.")
        elif k=="swole_status_live":
            task=e.get("task"); squad=e.get("squad") or {}; now=int(e.get("time") or time.time()); sev=e.get("swole_event") or {}
            if hasattr(self,"swole_tickets"):
                cur=sev.get("energy");mx=sev.get("max_energy"); self.swole_tickets.set(f"Swole tickets: {cur if cur is not None else '—'} / {mx if mx is not None else '—'}")
            energy=(f" • fights {sev.get('energy')}/{sev.get('max_energy')}" if sev.get("energy") is not None and sev.get("max_energy") is not None else "")
            fight=("direct fight API ready" if e.get("fight_ready") else "fight paused until current active Swole data is available")+energy
            if task:
                hid=str(task.get("task_id_1") or ""); rows=squad.values() if isinstance(squad,dict) else squad if isinstance(squad,list) else []; hero=next((x for x in rows if isinstance(x,dict) and str(x.get("hero_id"))==hid),{})
                row=next((r for r in self.hero_rows if str(r.get("id"))==hid),{}); name=row.get("name") or f"Hero {hid}"
                try: cur=int(hero.get("training_level") or row.get("training_level") or 0)
                except Exception: cur=0
                try: end=int(task.get("end_time") or 0)
                except Exception: end=0
                if end and end<=now:self.swole_status.set(f"Training complete • {name} → level {cur+1} • ready to claim • {fight}")
                elif end:
                    rem=max(0,end-now); self.swole_status.set(f"Training {name} → level {cur+1} • {rem//3600}h {(rem%3600)//60}m remaining • {fight}")
                else:self.swole_status.set(f"Training {name} → level {cur+1} • {fight}")
            else:self.swole_status.set(f"No active Swole training task • {fight}")
            self._swole_log("Live Swole status refreshed from getHeroSquad + getUserAccount.")
        elif k in {"adventure_scan_progress","adventure_scan_failed","adventure_scan_stopped","adventure_scan"}:
            if getattr(self,"_closing",False):return
            token=e.get("token")
            if token is not None and token is not getattr(self,"_adventure_scan_token",None):return
            if k=="adventure_scan_stopped":
                self.adventure_scan_status.set("Island scan stopped. The default farm selection is unchanged.");return
            if token is not None and self.adventure_scan_stop.is_set():return
            if k=="adventure_scan":ATCommanderApp._show_adventure_scan(self,e.get("scan"))
            else:self.adventure_scan_status.set(str(e.get("message") or "Island scan unavailable."))
        elif k=="adventure_target":
            # The runner can resume a normal energy/unlock wait. A concrete
            # new target supersedes that wait; the GUI never schedules a retry.
            self._adventure_pause_message=None;self._adventure_error_message=None
            mission=e.get("mission_id")
            if e.get("farming"):
                text=f"Farming default mission {mission}"
            else:
                text=f"Next island: {e.get('island')} • stage {e.get('stage')} • mission {mission} • verified progress {e.get('wins','—')}/{e.get('target_wins','—')} • energy {e.get('energy_cost','—')}"
            self.adventure_completion.set(text);self._append(self.adventure_log,text)
        elif k=="adventure_completion_verified":
            text=f"Server verified: {e.get('island')} stage {e.get('stage')} • progress {e.get('wins_before','—')} → {e.get('wins_after','—')}/{e.get('target_wins','—')}"
            self.adventure_completion.set(text);self._append(self.adventure_log,text)
        elif k=="adventure_farm_resume":
            self._adventure_pause_message=None;self._adventure_error_message=None
            text=f"Completion goal finished. Resuming default farm mission {e.get('mission_id')}."
            self.adventure_completion.set(text);self._append(self.adventure_log,text)
        elif k=="adventure_paused":
            text="Adventure paused: "+str(e.get("message") or "Current stage completion could not be confirmed.")
            self._adventure_pause_message=text
            self.adventure_completion.set(text);self._append(self.adventure_log,text)
        elif k=="adventure_progress_v2":
            last=e.get("last_mission"); won=e.get("num_missions_won"); prefs=e.get("prefs") or {}; selected=e.get("selected"); count=e.get("campaign_count")
            cur=e.get("energy"); mx=e.get("max_energy")
            if cur is not None:self.adventure_energy_var.set(f"Adventure energy: {int(cur):,} / {int(mx):,}" if mx is not None else f"Adventure energy: {int(cur):,} / —")
            if not (getattr(self,"_adventure_pause_message",None) or getattr(self,"_adventure_error_message",None)):
                self.adventure_completion.set(f"Status refreshed • selected target: {selected} • energy {cur if cur is not None else '—'}/{mx if mx is not None else '—'}")
            self._append(self.adventure_log,f"Refresh: selected={selected} (mission {e.get('selected_mission')}), last played={last}, auto-battle target={e.get('auto_mission')}, total mission wins={won}, campaign-count marker={count}.")
            if prefs:self._append(self.adventure_log,"Campaign markers: "+", ".join(f"{a}={b}" for a,b in sorted(prefs.items())))
        elif k=="play_click_ready":
            rr=e.get("result") or {}
            if not rr.get("ok"):
                self._log("PLAY NOW TEACH: "+str(rr.get("message") or "Could not arm browser-scoped capture."));return
            done=e.get("completion")
            if done is None or done is not getattr(self,"ad_calibration_done",None) or done.is_set():return
            try:
                if getattr(self,"_closing",False) or self.ad_stop.is_set():done.set();return
                messagebox.showinfo("Teach Play Now — one manual click","In the small dedicated AT browser, click Play Now exactly ONCE yourself. Then return here and click OK.\n\nThis teaching step records only the click position inside Commander's browser. Automated runs will use Chromium-scoped input and will never move or click your Windows mouse.")
                if getattr(self,"_closing",False) or self.ad_stop.is_set():done.set();return
                out=finish_play_click_calibration()
                self.events.put({"kind":"play_click_captured","result":out,"completion":done})
            except Exception:done.set();raise
        elif k=="play_click_captured":
            rr=e.get("result") or {}
            done=e.get("completion")
            if done is None or done is not getattr(self,"ad_calibration_done",None) or done.is_set():return
            try:
                if getattr(self,"_closing",False) or self.ad_stop.is_set():return
                if rr.get("ok"):
                    self.settings["ad_play_click"]=rr.get("click") or {};self._save_settings();self._log("PLAY NOW TEACH: saved a browser-scoped Play Now coordinate. Future mini-window runs use Chromium input only; Windows cursor untouched.")
                    messagebox.showinfo("Play Now saved","Saved. Use TEST AD CLICKS. Automated Play Now/ad clicks stay inside Commander's mini browser and never move the Windows cursor.")
                else:self._log("PLAY NOW TEACH: "+str(rr.get("message") or "Could not capture the Play Now click."))
            finally:done.set()
        elif k=="learning_capture_ready":
            done=e.get("completion")
            if done is not getattr(self,"learn_capture_done",None):return
            if not e.get("ok"):
                if not getattr(self,"_closing",False):self._diag_log(str(e.get("message") or "Learning capture could not be armed."))
                return
            if done.is_set() or getattr(self,"_closing",False) or self.ad_stop.is_set():return
            self.learn_session=e["session"];self.learn_key=e["key"]
            self.learn_browser_status.set(e.get("message") or "Learning Browser ready.")
            if e.get("siege"):
                self.last_capture_path=e["path"];self.last_capture_key="siege"
                self.siege_learn_btn.configure(text="FINISH SIEGE LEARN")
                self._event_log("Siege learning armed. Manually start exactly ONE successful Siege fight, then click FINISH SIEGE LEARN.")
                messagebox.showinfo("Learn one Siege attack","In the already-open Learning Browser, manually start exactly ONE Siege fight against any living island. As soon as the fight opens, return here and click FINISH SIEGE LEARN.\n\nCommander is recording the successful fight-start request, including AT's combat_data.")
            else:
                row=e.get("row") or (e["key"],e["key"],"perform the selected action")
                self.learn_status.set(f"RECORDING {row[1]}. In the already-open Learning Browser, do exactly this once: {row[2]} Then click FINISH CAPTURE. Do not log out or close Edge between actions.")
                self._diag_log(f"Capture started for {row[1]} → {e['path']}")
        elif k=="learning_capture_stopped":
            if e.get("completion") is not getattr(self,"learn_capture_done",None):return
            if getattr(self,"learn_session",None) is e.get("session"):
                self.learn_session=None;self.learn_key=None
            if not getattr(self,"_closing",False):
                if hasattr(self,"learn_status"):self.learn_status.set("Learning capture stopped. The partial trace was not installed.")
                if hasattr(self,"siege_learn_btn"):self.siege_learn_btn.configure(text="LEARN ONE SIEGE ATTACK")
        elif k=="ad_test_result":
            r=e.get("result") or {}; lvl=r.get("boost_level")
            if r.get("ok"):
                self._log(f"TEST AD CLICKS: browser replay completed. Current verified boost level={lvl if lvl is not None else 'unknown'}. At 3/3 this is a mechanical replay test; AT may not offer another ad.")
            else:self._log("TEST AD CLICKS: "+str(r.get("message") or "Replay failed."))
        elif k=="ad_calibration_ready":
            r=e.get("result") or {}
            if not r.get("ok"):
                self._log("AD CALIBRATION: "+str(r.get("message") or "Could not arm click capture."));return
            done=e.get("completion")
            if done is None or done is not getattr(self,"ad_calibration_done",None) or done.is_set():return
            try:
                if getattr(self,"_closing",False) or self.ad_stop.is_set():return
                ad_kind=e.get("ad_kind","boost")
                messagebox.showinfo("Calibrate Ads — one manual ad",f"In the opened AT browser, complete exactly ONE legitimate {ad_kind} ad now:\n\nOpen its normal in-game ad offer, click Watch Ad, wait for playback, then close it with the normal X.\n\nDo not click unrelated game controls during calibration. When the ad is finished, return here and click OK.")
                if getattr(self,"_closing",False) or self.ad_stop.is_set():return
                rr=finish_ad_click_calibration()
                if getattr(self,"_closing",False) or self.ad_stop.is_set():return
                if rr.get("ok"):
                    if ad_kind=="boost":self.settings["ad_click_sequence"]=rr.get("sequence") or []
                    else:
                        sequences=dict(self.settings.get("rewarded_ad_sequences") or {});sequences[ad_kind]=rr.get("sequence") or [];self.settings["rewarded_ad_sequences"]=sequences
                    save_settings(self.settings);self._log(f"AD CALIBRATION: saved {rr.get('clicks')} click step(s) for {ad_kind}. Server credit will be checked after playback.")
                    command={"boost":"WATCH ADS / user-ad","mom":"buy-momfree","research":"research-ad"}.get(ad_kind,ad_kind)
                    messagebox.showinfo("Ad calibration saved",f"Saved {rr.get('clicks')} click step(s) for {ad_kind}.\n\nUse {command} to play this legitimate ad sequence and verify AT's credited benefit.")
                else:
                    self._log("AD CALIBRATION: "+str(rr.get("message") or "No usable click sequence was captured."));messagebox.showinfo("Ad calibration incomplete",str(rr.get("message") or "No usable click sequence was captured."))
            finally:done.set()
        elif k=="ad_watch_result":
            if e.get("boost_level") is not None:
                lvl=e["boost_level"];self.ad_var.set(f"Active • level {lvl}" if lvl>0 else "Inactive")
            if e.get("ok"):
                lvl=e.get("boost_level"); self.ad_var.set(f"Active • level {lvl}"); self._log(f"WATCH ADS: boost level {lvl} verified.")
            else:self._log("WATCH ADS: "+str(e.get("message") or "No active boost verified."))
        elif k in ("swole_fight_paused","swole_needs_battle_profile"):
            self._swole_log(str(e.get("message") or "Swole fight start still needs one capture."))
            self.swole_status.set("Schedule paused at fight step • current live data or signed start needs attention; remaining queue preserved")
        elif k=="swole_wait":self._swole_log(str(e.get("message") or ""))
        elif k=="finished":
            self.lifetime_data=e.get("lifetime") or self.lifetime_data
            if mode in ("Rumble","Siege","Challenge"):self._event_log(f"{mode} run complete.")
            elif mode=="Swole":
                remaining=e.get("remaining_queue") or []
                if remaining:
                    self.swole_plan=list(remaining); self._render_swole_queue(); self._swole_log(f"Swole schedule paused with {len(remaining)} step(s) remaining.")
                else:self._swole_log("Swole schedule stopped/completed.")
            elif mode=="Adventure":
                text=getattr(self,"_adventure_error_message",None) or getattr(self,"_adventure_pause_message",None)
                if not text and e.get("paused"):
                    text="Adventure paused: "+str(e.get("pause_reason") or e.get("message") or "Current stage completion could not be confirmed. Review the saved action before resuming.")
                    self._adventure_pause_message=text
                if not text:text="Adventure run stopped." if e.get("stopped") else "Adventure run finished."
                self.adventure_completion.set(text);self._append(self.adventure_log,text)
            else:self.session_data=e.get("tracker") or self.session_data;self.status_var.set("Stopped" if e.get("stopped") else "Complete");self._log("Session stopped." if e.get("stopped") else "Session complete.");self._reset_buttons();self._render_stats()
        elif k=="fatal":
            msg=str(e.get("message") or "Unknown error")
            if mode in ("Rumble","Siege","Challenge"):self._event_log("ERROR: "+msg)
            elif mode=="Swole":
                remaining=e.get("remaining_queue") or []
                if remaining:self.swole_plan=list(remaining);self._render_swole_queue()
                self._swole_log("ERROR: "+msg)
            elif mode=="Adventure":
                self._adventure_error_message=getattr(self,"_adventure_pause_message",None) or "Adventure paused after error: "+msg
                self.adventure_completion.set(self._adventure_error_message)
                self._append(self.adventure_log,"ERROR: "+msg)
            else:self.status_var.set("Error");self._log("ERROR: "+msg);self._reset_buttons();messagebox.showerror("AT Commander",msg)
        elif k=="error":
            msg=str(e.get("message") or "")
            self._diag_log(msg)
            if hasattr(self,"item_action_var") and any(x in msg.lower() for x in ("nixon","golden","item","pack","inventory","use ")):
                self.item_action_var.set(msg[:1000]); self.inventory_progress_text.set("Stopped — see message")

    def _render_inventory(self):
        entries=(self.current_snapshot or {}).get("entries") or {}; rows=sorted(entries.values(),key=lambda r:(str(r.get("category")),str(r.get("name")).lower()))
        for tree in [getattr(self,"inventory_tree",None),getattr(self,"live_tree",None)]:
            if not tree:continue
            for x in tree.get_children():tree.delete(x)
            for r in rows:
                name=str(r.get("name") or "")
                unresolved=(name.startswith("Unknown item (ID ") or bool(re.match(r"^(?:Item|Card) \d+$",name)))
                if not self.show_unknown_items.get() and unresolved:continue
                vals=(r.get("category"),r.get("name"),self._fmt(r.get("count",0))) if tree is self.live_tree else (r.get("category"),r.get("name"),self._fmt(r.get("count",0)),r.get("key"))
                tree.insert("","end",values=vals)
    def _render_stats(self):
        self._fill_stats(self.section_tree,diff_snapshots((self.session_data or {}).get("start_snapshot"),(self.session_data or {}).get("current_snapshot")),self.changed_only_var,self.search_var);self._fill_stats(self.lifetime_tree,diff_snapshots((self.lifetime_data or {}).get("baseline_snapshot"),(self.lifetime_data or {}).get("current_snapshot")),self.life_changed_only_var,self.life_search_var)
        t=(self.session_data or {}).get("totals") or {};b=int(t.get("battles") or 0);w=int(t.get("wins") or 0);l=int(t.get("losses") or 0);d=w+l;self.battles_var.set(str(b));self.record_var.set(f"{w}W / {l}L");self.rate_var.set(f"{w/d*100:.1f}%" if d else "—");self.stats_summary_var.set(f"Current section • {b} battles • {w}W/{l}L");a=((self.lifetime_data or {}).get("modes") or {}).get("Arena") or {};self.lifetime_summary_var.set(f"Persistent totals • Arena {a.get('battles',0)} battles • {a.get('wins',0)}W/{a.get('losses',0)}L")
    def _fill_stats(self,tree,rows,changed,search):
        for x in tree.get_children():tree.delete(x)
        needle=search.get().strip().lower()
        for r in rows:
            category=str(r.get("category") or "")
            name=str(r.get("name") or "")
            if category.lower()=="card": continue
            if name.startswith("Unknown item (ID ") or re.match(r"^(?:Item|Card) \d+$",name):continue
            ch=r["change"]
            if changed.get() and not ch:continue
            if needle and needle not in str(r["name"]).lower() and needle not in str(r["category"]).lower():continue
            tag="gain" if float(ch)>0 else "loss" if float(ch)<0 else "";sign="+" if float(ch)>0 else "";tree.insert("","end",values=(r["category"],r["name"],self._fmt(r["start"]),self._fmt(r["current"]),sign+self._fmt(ch)),tags=(tag,) if tag else ())
    def _render_progress(self):
        trophy=choose_trophy_metric(self.progress_rows)
        if trophy:self.trophy_var.set(self._fmt(trophy.get("value")));self.trophy_detail_var.set(f"Arena ladder / trophies: {self._fmt(trophy.get('value'))}")
        else:self.trophy_var.set("—");self.trophy_detail_var.set("Arena ladder / trophies: rating not present in the latest refresh response.")
        for x in self.medal_tree.get_children():self.medal_tree.delete(x)
        for r in self.hero_medal_rows:self.medal_tree.insert("","end",values=(r.get("hero"),self._fmt(r.get("count")),r.get("path")))
    @staticmethod
    def _fmt(v):
        try:f=float(v);return f"{int(f):,}" if f.is_integer() else f"{f:,.2f}"
        except:return str(v)
    def _reset_buttons(self):self.start_btn.configure(state="normal");self.pause_btn.configure(state="disabled",text="Pause");self.stop_btn.configure(state="disabled");self.paused=False
    def _tick_clock(self):
        if self._closing:return
        if hasattr(self,"reward_auto"):self._reward_tick()
        if hasattr(self,"command_schema"):self._command_tick()
        if hasattr(self,"research_status"):self._research_tick()
        self.root.after(1000,self._tick_clock)
    def _log(self,t):self._append(self.log,t)
    def _event_log(self,t):self._append(self.event_log,t)
    def _swole_log(self,t):self._append(self.swole_log,t)
    def _diag_log(self,t):self._append(self.diag_log,t)
    @staticmethod
    def _append(w,text):
        stamp=datetime.now().strftime("%H:%M:%S");w.configure(state="normal");w.insert("end",f"[{stamp}] {text}\n");w.see("end");w.configure(state="disabled")

def main():
    from .ui_runtime import MainThreadRuntime
    runtime=MainThreadRuntime(APP_DIR/'atcommander_crash.log')
    root=None;app=None
    try:
        root=tk.Tk();app=ATCommanderApp(root)
        runtime.attach(root,lambda category:app.events.put({"kind":"log","message":
            f"A GUI callback stopped ({category}); source locations were recorded in atcommander_crash.log."}))
        root.protocol("WM_DELETE_WINDOW",app.close_app);root.mainloop();return 75 if getattr(app,'_restart_requested',False) else 0
    finally:
        if root is not None:
            try:root.destroy()
            except tk.TclError:pass
        app=None;root=None
        runtime.close()
