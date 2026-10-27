"""Deck workshop: owned-card previews, editable drafts and verified renaming."""
from copy import deepcopy
import threading
import tkinter as tk
from tkinter import ttk
from .api import ATClient
from .credentials import load_credentials
from .account_state import exact_decks
from .command_center import CommandCenterMixin
from .deck_builder import RULES, owned_cards, suggest_deck, deck_stamp, rename_deck, save_draft
from .local_commands import CommandInputError, CommandUnavailable
from .utility_commands import build_service
from .log_view import split_controls_log


class DeckBuilderMixin:
    def _build_deck_builder(self):
        self.deck_builder_thread = None; self.deck_builder_stop = threading.Event()
        self.deck_builder_cache = None; self.deck_builder_draft = []; self.deck_builder_generation = 0
        self.deck_builder_status = tk.StringVar(value="Load your saved decks and owned cards to begin.")
        self.builder_slot = tk.StringVar(); self.builder_name = tk.StringVar()
        self.builder_rule = tk.StringVar(value=RULES[0]); self.builder_character = tk.StringVar()
        self.builder_trait = tk.StringVar(); self.builder_size = tk.StringVar(value="25")
        self.builder_rarity = tk.StringVar(value="3"); self.builder_pcs = tk.StringVar(value="0")
        self.builder_researched = tk.BooleanVar(value=True); self.builder_search = tk.StringVar()
        header = ttk.Frame(self.deck_builder_tab, padding=10); header.pack(fill="x")
        ttk.Label(header, text="Your deck workshop", style="Big.TLabel").pack(side="left")
        ttk.Button(header, text="Load / refresh decks & cards", command=lambda: self._builder_launch("load")).pack(side="right")
        ttk.Label(self.deck_builder_tab, textvariable=self.deck_builder_status, wraplength=1150).pack(fill="x", padx=10, pady=(0, 6))
        _, controls, body = split_controls_log(self.deck_builder_tab)
        rename = ttk.LabelFrame(controls, text="Saved slot • rename independently of building", padding=9)
        rename.pack(fill="x", pady=(0, 8))
        self.builder_slot_combo = ttk.Combobox(rename, textvariable=self.builder_slot, state="readonly", width=35)
        self.builder_slot_combo.pack(side="left", padx=(0, 9)); self.builder_slot_combo.bind("<<ComboboxSelected>>", lambda e: self._builder_slot_changed())
        ttk.Entry(rename, textvariable=self.builder_name, width=28).pack(side="left", padx=(0, 9))
        ttk.Button(rename, text="Rename saved deck", command=lambda: self._builder_launch("rename")).pack(side="left")
        ttk.Button(rename, text="Load slot into draft", command=self._builder_load_slot).pack(side="left", padx=9)
        options = ttk.LabelFrame(controls, text="Build from owned cards", padding=9); options.pack(fill="x")
        for column, (label, variable, values, width) in enumerate([
            ("Rule", self.builder_rule, RULES, 27), ("Character (optional)", self.builder_character, ("",), 23),
            ("Combo BGE / trait", self.builder_trait, ("",), 22)]):
            ttk.Label(options, text=label).grid(row=0, column=column, sticky="w")
            control = ttk.Combobox(options, textvariable=variable, values=values, state="readonly", width=width)
            control.grid(row=1, column=column, sticky="ew", padx=(0, 10))
            if column == 1: self.builder_character_combo = control
            if column == 2: self.builder_trait_combo = control
            options.columnconfigure(column, weight=1)
        counts = ttk.Frame(options); counts.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(9, 0))
        for label, variable, lo, hi in [("Cards", self.builder_size, 25, 35), ("Minimum rarity (1–5)", self.builder_rarity, 1, 5), ("Power cards", self.builder_pcs, 0, 35)]:
            ttk.Label(counts, text=label).pack(side="left", padx=(0, 5))
            ttk.Spinbox(counts, textvariable=variable, from_=lo, to=hi, width=5).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(counts, text="Only researched combos", variable=self.builder_researched).pack(side="left")
        buttons = ttk.Frame(controls, padding=(0, 9)); buttons.pack(fill="x")
        ttk.Button(buttons, text="Suggest a deck", command=lambda: self._builder_launch("suggest"), style="Accent.TButton").pack(side="left")
        ttk.Button(buttons, text="Save draft to selected slot", command=lambda: self._builder_launch("save")).pack(side="left", padx=8)
        ttk.Button(buttons, text="Stop", command=self.deck_builder_stop.set).pack(side="left")
        ttk.Label(controls, text="Saving replaces this slot’s cards and keeps its hero and name. Previous cards are backed up locally. Previewing spends nothing.", wraplength=1100).pack(anchor="w", pady=(0, 5))
        columns = ttk.Panedwindow(body, orient="horizontal"); columns.pack(fill="both", expand=True)
        left = ttk.Frame(columns, padding=6); right = ttk.Frame(columns, padding=6)
        columns.add(left, weight=1); columns.add(right, weight=1)
        top = ttk.Frame(left); top.pack(fill="x")
        ttk.Label(top, text="Owned cards • double-click to add").pack(side="left")
        entry = ttk.Entry(top, textvariable=self.builder_search, width=22); entry.pack(side="right")
        entry.bind("<KeyRelease>", lambda e: self._builder_render_pool())
        self.builder_pool_table = self._builder_table(left)
        self.builder_pool_table.bind("<Double-1>", lambda e: self._builder_add_card())
        self.builder_draft_label = tk.StringVar(value="Draft: 0 cards")
        top = ttk.Frame(right); top.pack(fill="x")
        ttk.Label(top, textvariable=self.builder_draft_label).pack(side="left")
        ttk.Button(top, text="Remove selected", command=self._builder_remove_cards).pack(side="right")
        self.builder_draft_table = self._builder_table(right)

    @staticmethod
    def _builder_table(parent):
        frame = ttk.Frame(parent); frame.pack(fill="both", expand=True, pady=(5, 0))
        table = ttk.Treeview(frame, columns=("name", "kind", "rarity", "level", "stats"), show="headings", height=7)
        for name, title, width in [("name", "Card", 170), ("kind", "Type", 75), ("rarity", "Tier", 40), ("level", "Lvl", 35), ("stats", "ATK / HP", 70)]:
            table.heading(name, text=title); table.column(name, width=width, minwidth=30)
        bar = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=bar.set); bar.pack(side="right", fill="y"); table.pack(fill="both", expand=True)
        return table

    def _builder_target(self):
        return self._selected_id(self.builder_slot.get(), self.deck_rows)

    def _builder_slot_changed(self):
        cache = self.deck_builder_cache; target = self._builder_target()
        if cache and target:
            self.builder_name.set(deck_stamp(cache["snapshot"], target)["name"])

    def _builder_load_slot(self):
        cache = self.deck_builder_cache; target = self._builder_target()
        if not cache or not target: return
        stamp = deck_stamp(cache["snapshot"], target)
        pool = {str(c["unit_index"]): c for c in cache["pool"]}
        if any(i not in pool for i in stamp["units"]):
            self.deck_builder_status.set("Some slot cards lack current static data. Refresh before editing; the saved deck was kept."); return
        self.deck_builder_draft = [pool[i] for i in stamp["units"]]
        self._builder_render_draft()

    def _builder_launch(self, action):
        if getattr(self, "_closing", False): return False
        if self.deck_builder_thread is not None and self.deck_builder_thread.is_alive(): return False
        if CommandCenterMixin._browser_action_busy(self):
            self.deck_builder_status.set("Finish or stop active automation before loading or editing decks."); return False
        cache = self.deck_builder_cache; target = self._builder_target()
        if action != "load" and not cache:
            self.deck_builder_status.set("Load your saved decks and owned cards first."); return False
        try:
            settings = {"rule": self.builder_rule.get(), "character": self.builder_character.get(), "trait": self.builder_trait.get(),
                "size": int(self.builder_size.get()), "min_rarity": int(self.builder_rarity.get()),
                "power_cards": int(self.builder_pcs.get()), "researched_only": bool(self.builder_researched.get())} if action == "suggest" else None
            if action in {"rename", "save"} and not target: raise CommandInputError("Choose a saved deck slot first.")
            expected = deck_stamp(cache["snapshot"], target) if target and cache else None
            name = self.builder_name.get(); draft = deepcopy(self.deck_builder_draft)
        except (ValueError, CommandInputError, CommandUnavailable) as error:
            self.deck_builder_status.set(str(error)); return False
        self.deck_builder_stop.clear(); self.deck_builder_status.set({"load": "Loading owned cards and exact saved slots…", "suggest": "Building a draft from owned cards…", "rename": "Renaming and verifying the saved deck…", "save": "Saving and verifying the reviewed draft…"}[action])
        def work():
            try:
                if action == "suggest":
                    answer = suggest_deck(cache["snapshot"], cache["catalog"], cache["recipes"], **settings, should_stop=self.deck_builder_stop.is_set)
                else:
                    client = ATClient(load_credentials(), timeout=45, should_stop=self.deck_builder_stop.is_set)
                    if action == "load":
                        snapshot = client.init()
                        exact = client.get_exact_decks(snapshot); snapshot = {**snapshot, **exact}
                        warnings = []
                        try: catalog = client.card_catalog()
                        except Exception:
                            catalog = {}; warnings.append("Card data unavailable")
                        if self.deck_builder_stop.is_set(): raise CommandUnavailable("Deck loading stopped.")
                        try: recipes = client.combo_catalog()
                        except Exception:
                            recipes = {}; warnings.append("Recipe data unavailable")
                        pool = owned_cards(snapshot, catalog, should_stop=self.deck_builder_stop.is_set)
                        answer = {"snapshot": snapshot, "catalog": catalog, "recipes": recipes, "pool": pool,
                                  "warning": "; ".join(warnings)}
                    else:
                        service = build_service(client); service.stop = self.deck_builder_stop
                        answer = rename_deck(service, target, name, expected) if action == "rename" else save_draft(service, target, draft, expected)
                self.events.put({"kind": "deck_builder_done", "action": action, "answer": answer})
            except (CommandInputError, CommandUnavailable) as error:
                self.events.put({"kind": "deck_builder_done", "action": action, "error": str(error)})
            except Exception:
                self.events.put({"kind": "deck_builder_done", "action": action, "error": "This deck operation could not be verified. No request was repeated. Refresh before editing again."})
        self.deck_builder_thread = threading.Thread(target=work, daemon=True, name="Commander deck workshop")
        self.deck_builder_thread.start(); return True

    def _deck_builder_event(self, event):
        if event.get("kind") != "deck_builder_done": return False
        if getattr(self, "_closing", False): return True
        action = event["action"]
        if event.get("error"):
            self.deck_builder_status.set(event["error"])
            if action in {"save", "rename"}: self.deck_builder_cache = None
            return True
        answer = event["answer"]
        if action == "load":
            self.deck_builder_cache = answer; self.deck_builder_draft = []; self._builder_render_draft()
            self._apply_account_selectors(exact_decks(answer["snapshot"]))
            self.builder_character_combo["values"] = [""] + sorted({c["name"] for c in answer["pool"] if c["kind"] == "character"})
            from .cm_reviews import _traits
            self.builder_trait_combo["values"] = [""] + sorted({t for c in answer["catalog"].values() if isinstance(c, dict) for t in _traits(c)})
            self._builder_render_pool(); self._builder_slot_changed()
            self.deck_builder_status.set(f"Loaded {len(answer['pool'])} owned card instances. Choose a slot to rename or build a draft.")
            if answer.get("warning"):
                self.deck_builder_status.set(answer["warning"] + ". Saved slots are loaded; renaming remains available. Refresh to enable missing builder data.")
        elif action == "suggest":
            self.deck_builder_draft = answer["cards"]; self._builder_render_draft()
            coverage = f" Matching combo coverage: {answer['coverage']}%." if answer.get("coverage") is not None else ""
            self.deck_builder_status.set(answer["note"] + coverage + " Review before saving.")
        else:
            self.deck_builder_cache["snapshot"] = answer["snapshot"]
            self._apply_account_selectors(exact_decks(answer["snapshot"])); self._builder_slot_changed()
            self.deck_builder_status.set(answer["message"])
        return True

    @staticmethod
    def _builder_insert(table, card):
        table.insert("", "end", iid=str(card["unit_index"]), values=(card["name"], card["kind"], card["rarity"], card["level"], f"{card['attack']} / {card['health']}"))

    def _builder_render_pool(self):
        table = self.builder_pool_table; table.delete(*table.get_children())
        if not self.deck_builder_cache: return
        query = self.builder_search.get().strip().casefold()
        selected = [r for r in self.deck_builder_cache["pool"] if not query or query in (r["name"] + " " + " ".join(r["traits"])).casefold()]
        for card in selected[:500]: self._builder_insert(table, card)
        # Limit widget allocation; the complete pool remains available through search.

    def _builder_render_draft(self):
        table = self.builder_draft_table; table.delete(*table.get_children())
        for card in self.deck_builder_draft: self._builder_insert(table, card)
        self.builder_draft_label.set(f"Draft: {len(self.deck_builder_draft)} cards • 25–35 to save")

    def _builder_add_card(self):
        if not self.deck_builder_cache or len(self.deck_builder_draft) >= 35: return
        existing = {str(c["unit_index"]) for c in self.deck_builder_draft}
        pool = {str(c["unit_index"]): c for c in self.deck_builder_cache["pool"]}
        for identity in self.builder_pool_table.selection():
            if identity not in existing and len(self.deck_builder_draft) < 35:
                self.deck_builder_draft.append(pool[identity]); existing.add(identity)
        self._builder_render_draft()

    def _builder_remove_cards(self):
        selected = set(self.builder_draft_table.selection())
        self.deck_builder_draft = [r for r in self.deck_builder_draft if str(r["unit_index"]) not in selected]
        self._builder_render_draft()
