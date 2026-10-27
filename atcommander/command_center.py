"""Dark command browser, serial routines, and local change notifications."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

from .api import ATClient
from .credentials import load_credentials
from .diagnostic_privacy import safe_text, sanitize_report
from .utility_commands import build_service
from .local_commands import CommandInputError


class SchemaClient:
    """Registry construction performs no network or credential reads."""
    def init(self):
        return {}


def notification_digest(text):
    """Compare actual state, excluding per-request timestamps and echoes."""
    try:data=json.loads(text)
    except ValueError:data=text
    transient={"request","time","server_time","client_time","captured_at","last_update_time"}
    def stable(value):
        if isinstance(value,dict):return {k:stable(v) for k,v in value.items() if k not in transient}
        if isinstance(value,list):return [stable(v) for v in value]
        return value
    return hashlib.sha256(json.dumps(stable(data),sort_keys=True).encode()).hexdigest()


def daily_result_text(data):
    """Show the user actions and availability, without transport arguments."""
    lines=[]
    for title,key in (("Planned actions","steps"),("Completed actions","completed"),
                      ("Skipped actions","skipped"),("Unresolved requirements","unresolved"),
                      ("Daily quests still incomplete","remaining")):
        records=data.get(key,[]) if isinstance(data,dict) else []
        if not isinstance(records,list) or not records:continue
        lines.append(title+":")
        for row in records:
            if not isinstance(row,dict):continue
            label=row.get("label") or row.get("step") or row.get("quest") or "Action"
            detail=row.get("reason") or row.get("message")
            if row.get("remaining") is not None:
                label=f"{label} ({row['remaining']} remaining)"
            lines.append("• "+str(label)+(": "+str(detail) if detail else ""))
        lines.append("")
    return "\n".join(lines).rstrip()


class CommandCenterMixin:
    def _build_command_center(self):
        self.command_schema = build_service(SchemaClient())
        self.command_busy = False
        self.command_stop = threading.Event()
        self.routine_steps = []
        saved_routine=self.command_schema.store.read().get("routine", [])
        for row in saved_routine if isinstance(saved_routine,list) else []:
            try:
                spec = self.command_schema.spec(row["command"])
                if not spec.destructive:
                    self.routine_steps.append({"command":spec.name,"args":spec.parse_args(row.get("args",{}))})
            except (ValueError, KeyError, TypeError):
                continue
        self.routine_enabled = False
        self.routine_next = 0
        self.subscription_next = {}
        self.subscription_hash = {}
        self.subscription_busy = False
        self.command_result_text = ""
        self.command_artifacts = []
        self.command_group = tk.StringVar(value="All")
        self.command_search = tk.StringVar()
        self.command_status = tk.StringVar(value=f"{len(self.command_schema.specs())} local commands • no Discord dependency")
        self.command_args = {}
        head = ttk.Frame(self.commands_tab, style="Card.TFrame", padding=12)
        head.pack(fill="x", pady=(10, 8))
        ttk.Label(head, text="COMMAND CENTER", style="Big.Card.TLabel").pack(anchor="w")
        ttk.Label(head, textvariable=self.command_status, style="Muted.Card.TLabel").pack(anchor="w", pady=(2, 7))
        filters = ttk.Frame(head, style="Card.TFrame"); filters.pack(fill="x")
        ttk.Combobox(filters, textvariable=self.command_group, state="readonly", values=["All"] + self.command_schema.groups(), width=18).pack(side="left")
        ttk.Entry(filters, textvariable=self.command_search, width=42).pack(side="left", padx=8)
        ttk.Label(filters, text="Search names or descriptions", style="Muted.Card.TLabel").pack(side="left")
        self.command_group.trace_add("write", lambda *_: self._command_filter())
        self.command_search.trace_add("write", lambda *_: self._command_filter())
        body = ttk.Panedwindow(self.commands_tab, orient="horizontal"); body.pack(fill="both", expand=True)
        left = ttk.Frame(body, style="Card.TFrame", padding=8)
        right = ttk.Frame(body, style="Card.TFrame", padding=12)
        body.add(left, weight=2); body.add(right, weight=3)
        self.command_tree = ttk.Treeview(left, columns=("name", "description"), show="headings", height=16)
        self.command_tree.heading("name", text="Command"); self.command_tree.heading("description", text="Purpose")
        self.command_tree.column("name", width=160, stretch=False); self.command_tree.column("description", width=300)
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.command_tree.yview)
        self.command_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y"); self.command_tree.pack(fill="both", expand=True)
        self.command_tree.bind("<<TreeviewSelect>>", lambda *_: self._command_selected())
        self.command_description = tk.StringVar(value="Select a command to see its arguments.")
        ttk.Label(right, textvariable=self.command_description, style="Card.TLabel", wraplength=620).pack(anchor="w", fill="x")
        self.command_form_wrap = ttk.Frame(right, style="Card.TFrame"); self.command_form_wrap.pack(fill="x", pady=10)
        self.command_form_canvas = tk.Canvas(self.command_form_wrap,bg="#101c2f",height=1,highlightthickness=0,borderwidth=0)
        self.command_form_scroll = ttk.Scrollbar(self.command_form_wrap,orient="vertical",command=self.command_form_canvas.yview)
        self.command_form_canvas.configure(yscrollcommand=self.command_form_scroll.set)
        self.command_form_canvas.pack(side="left",fill="x",expand=True)
        self.command_form = ttk.Frame(self.command_form_canvas, style="Card.TFrame")
        self.command_form_window = self.command_form_canvas.create_window((0,0),window=self.command_form,anchor="nw")
        self.command_form.bind("<Configure>",lambda _:self._command_form_layout())
        self.command_form_canvas.bind("<Configure>",lambda event:self.command_form_canvas.itemconfigure(self.command_form_window,width=event.width))
        self.command_form_canvas.bind("<MouseWheel>",self._command_form_wheel)
        buttons = ttk.Frame(right, style="Card.TFrame"); buttons.pack(fill="x")
        self.command_run_button = ttk.Button(buttons, text="RUN COMMAND", style="Accent.TButton", command=self._command_run)
        self.command_run_button.pack(side="left")
        ttk.Button(buttons, text="ADD TO ROUTINE", command=self._routine_add).pack(side="left", padx=8)
        ttk.Button(buttons, text="SAVE RESULT", command=self._command_save_result).pack(side="right")
        self.command_artifact_button=ttk.Button(buttons,text="OPEN STAT SHEET",command=self._command_open_artifact,state="disabled")
        self.command_artifact_button.pack(side="right",padx=5)
        self.command_output = tk.Text(right, bg="#08111e", fg="#dce7f4", insertbackground="#fff", wrap="word", relief="flat", font=("Consolas", 10))
        self.command_output.pack(fill="both", expand=True, pady=(10, 0)); self.command_output.configure(state="disabled")
        self._command_filter()
        self._build_routines()

    def _command_filter(self):
        if not hasattr(self, "command_tree"):
            return
        previous = self.command_tree.selection()
        for iid in self.command_tree.get_children():
            self.command_tree.delete(iid)
        group = self.command_group.get(); query = self.command_search.get().casefold()
        for spec in self.command_schema.specs(None if group == "All" else group):
            if query and query not in (spec.name + " " + spec.description).casefold():
                continue
            self.command_tree.insert("", "end", iid=spec.name, values=(spec.name, spec.description))
        if previous and self.command_tree.exists(previous[0]):
            self.command_tree.selection_set(previous[0])

    def _command_selected(self):
        selected = self.command_tree.selection()
        if not selected:
            return
        spec = self.command_schema.spec(selected[0])
        self.command_description.set(spec.description + ("\nThis action changes game or local state." if spec.action else ""))
        for child in self.command_form.winfo_children():
            child.destroy()
        self.command_args = {}
        defaults = self.command_schema.store.read().get("play_defaults", {}) if spec.name == "play" else {}
        for i, arg in enumerate(spec.args):
            ttk.Label(self.command_form, text=(arg.label or arg.name.replace("_", " ").title()) + (" *" if arg.required else ""), style="Card.TLabel").grid(row=i, column=0, sticky="w", pady=3)
            default = defaults.get(arg.name, arg.default)
            value = tk.StringVar(value="" if default is None else str(default))
            self.command_args[arg.name] = value
            if arg.choices:
                widget = ttk.Combobox(self.command_form, textvariable=value, values=arg.choices, state="readonly", width=35)
            else:
                widget = ttk.Entry(self.command_form, textvariable=value, width=40, show="*" if arg.name=="login_hash" else "")
            widget.grid(row=i, column=1, sticky="ew", padx=(12, 0), pady=3)
            widget.bind("<MouseWheel>",self._command_form_wheel)
        self.command_form.columnconfigure(1, weight=1)
        self.command_form.update_idletasks();self._command_form_layout();self.command_form_canvas.yview_moveto(0)

    def _command_form_layout(self):
        height=max(1,self.command_form.winfo_reqheight())
        self.command_form_canvas.configure(height=min(320,height),scrollregion=self.command_form_canvas.bbox("all"))
        if height>320:self.command_form_scroll.pack(side="right",fill="y")
        else:self.command_form_scroll.pack_forget()

    def _command_form_wheel(self,event):
        if self.command_form.winfo_reqheight()>320:
            self.command_form_canvas.yview_scroll(-int(event.delta/120),"units")
            return "break"

    def _command_values(self):
        selected = self.command_tree.selection()
        if not selected:
            raise CommandInputError("Select a command first.")
        spec = self.command_schema.spec(selected[0])
        return spec, spec.parse_args({name: value.get() for name, value in self.command_args.items()})

    def _command_run(self):
        try:
            spec, values = self._command_values()
        except ValueError as exc:
            return messagebox.showerror("Command arguments", str(exc))
        if self.command_busy:
            return messagebox.showinfo("Command Center", "A command or routine is already running.")
        if spec.action and CommandCenterMixin._browser_action_busy(self,include_commands=False):
            return messagebox.showinfo("Command Center", "Wait for Game / Ads or stop active automation before changing account, deck or resource state.")
        if spec.destructive and not messagebox.askyesno("Review action", f"Run {spec.name}?\n\n{json.dumps(sanitize_report(values), indent=2)}\n\n{spec.description}"):
            return
        if spec.spends_resources and not messagebox.askyesno("Review purchase", f"Run {spec.name} with these limits?\n\n{json.dumps(values, indent=2)}"):
            return
        self._command_launch([(spec.name, values)], False)

    def _command_launch(self, steps, routine):
        if getattr(self,"_closing",False) or getattr(self,"command_busy",False):return False
        mutates=any(self.command_schema.spec(name).action for name,_ in steps)
        if mutates and CommandCenterMixin._browser_action_busy(self,include_commands=False):
            if not routine:
                messagebox.showinfo("Command Center","Wait for Game / Ads or stop active automation before starting this command.")
            return False
        self.command_busy = True; self.command_stop.clear()
        self.command_mutating=mutates
        self.command_run_button.configure(state="disabled")
        self.command_status.set("Running local routine…" if routine else f"Running {steps[0][0]}…")
        def work():
            outputs = [];artifacts=[]
            try:
                credentials=load_credentials()
                service=getattr(self,"command_service",None)
                if service is None or service.client.credentials != credentials:
                    service = build_service(ATClient(credentials), store=self.command_schema.store,
                        emit=lambda event:self.events.put({"kind":"command_log","message":event.get("message",str(event)) if isinstance(event,dict) else str(event)}))
                    self.command_service=service
                service.stop = self.command_stop
                for name, args in steps:
                    if self.command_stop.is_set():
                        outputs.append("Stopped before the next command."); break
                    answer = service.execute(name, args)
                    if isinstance(answer.data,dict):
                        artifacts.extend(row for row in answer.data.get("artifacts",[]) if isinstance(row,dict) and row.get("type")=="image/svg+xml")
                    body=daily_result_text(answer.data) if name in {"daily-plan","daily-run"} else answer.details or json.dumps(answer.rows, indent=2, ensure_ascii=False)
                    outputs.append(f"{name}\n{answer.message}\n" + body)
                self.events.put({"kind": "command_result", "text": "\n\n".join(outputs), "ok": True, "routine": routine,"artifacts":artifacts})
            except Exception as exc:
                private = getattr(getattr(locals().get("service"), "context", None), "private", ())
                outputs.append("Stopped: " + safe_text(str(exc), private))
                self.events.put({"kind": "command_result", "text": "\n\n".join(outputs), "ok": False, "routine": routine,"artifacts":artifacts})
        threading.Thread(target=work, daemon=True).start()
        return True

    def _command_event(self, event):
        if event.get("kind")=="command_log":
            self._append(self.routine_log,event["message"]);return True
        if event.get("kind") == "command_result":
            self.command_busy = False; self.command_run_button.configure(state="normal")
            self.command_result_text = event["text"]
            self.command_artifacts=event.get("artifacts",[])
            self.command_artifact_button.configure(state="normal" if self.command_artifacts else "disabled")
            self.command_output.configure(state="normal"); self.command_output.delete("1.0", "end")
            self.command_output.insert("1.0", self.command_result_text); self.command_output.configure(state="disabled")
            self.command_status.set("Completed." if event["ok"] else "Stopped. Review the result before running again.")
            if event.get("routine") or event["text"].startswith("daily-plan\n"):
                self._append(self.routine_log,event["text"])
            if event.get("routine"):
                if not event["ok"]:
                    self.routine_enabled = False
                self.routine_next = time.monotonic() + getattr(self,"routine_every",300)
                self._routine_status()
            return True
        if event.get("kind") == "subscription_result":
            self.subscription_busy = False
            for name, digest, text in event.get("checks", []):
                old = self.subscription_hash.get(name)
                self.subscription_hash[name] = digest
                if old is not None and old != digest:
                    self._append(self.routine_log, f"{name} changed: {text}")
            if event.get("error"):
                self._append(self.routine_log, event["error"])
            return True
        return False

    def _command_save_result(self):
        if not self.command_result_text:
            return
        target = filedialog.asksaveasfilename(title="Save command result", defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if target:
            from pathlib import Path
            Path(target).write_text(self.command_result_text, encoding="utf-8")

    def _command_open_artifact(self):
        from pathlib import Path
        import os
        if not self.command_artifacts:return
        path=Path(self.command_artifacts[-1]["path"])
        if path.suffix.lower()==".svg" and path.is_file():
            os.startfile(str(path))

    def _build_routines(self):
        from .automation_profiles import AutomationProfiles, BUILTIN_DAILY, DailyOptions
        from dataclasses import asdict
        self.automation_profiles=AutomationProfiles(self.command_schema.store,self.command_schema)
        saved=self.command_schema.store.read()
        if self.routine_steps and len(self.automation_profiles.names())==1:
            try:self.automation_profiles.save("Previous routine",self.routine_steps)
            except ValueError:pass
        head = ttk.Frame(self.routines_tab, style="Card.TFrame", padding=12); head.pack(fill="x", pady=(10, 8))
        ttk.Label(head, text="AUTOMATION ROUTINES", style="Big.Card.TLabel").pack(anchor="w")
        ttk.Label(head, text="Add commands from Command Center. Steps run in order and stop on an error. Repeating routines stay on this computer while Commander is open. Saved routines start disarmed when the app opens.", style="Muted.Card.TLabel", wraplength=1200).pack(anchor="w", pady=(3, 8))
        profiles=ttk.Frame(head,style="Card.TFrame");profiles.pack(fill="x",pady=(0,8))
        ttk.Label(profiles,text="Profile",style="Card.TLabel").pack(side="left",padx=(0,8))
        selection=saved.get("automation_selected_profile",BUILTIN_DAILY)
        if selection not in self.automation_profiles.names():selection=BUILTIN_DAILY
        self.routine_profile=tk.StringVar(value=selection)
        self.routine_profile_combo=ttk.Combobox(profiles,textvariable=self.routine_profile,values=self.automation_profiles.names(),state="readonly",width=30)
        self.routine_profile_combo.pack(side="left")
        self.routine_profile_combo.bind("<<ComboboxSelected>>",lambda _:self._routine_load_profile())
        ttk.Button(profiles,text="SAVE AS…",command=self._routine_save_profile).pack(side="left",padx=8)
        ttk.Button(profiles,text="DELETE PROFILE",command=self._routine_delete_profile).pack(side="left")
        self.daily_frame=ttk.LabelFrame(head,text="Best Daily limits per run",padding=8);self.daily_frame.pack(fill="x",pady=(0,8))
        defaults=asdict(DailyOptions())
        try:daily=asdict(DailyOptions.parse(saved.get("daily_profile_options",{})))
        except ValueError:daily=defaults
        self.daily_vars={}
        labels=(("nixon_budget","Nixons"),("cm_stones_budget","CM stones"),("arena_reserve","Keep Arena energy"),
                ("adventure_reserve","Keep Adventure energy"),("swole_reserve","Keep Swole tickets"),("max_battles","Total battle limit"),
                ("event_battles","Per-event limit"),("research_pairs","Research pairs"),("research_speedup_hours","Research speedup hours"))
        for index,(key,label) in enumerate(labels):
            row,column=divmod(index,3);column*=2
            self.daily_vars[key]=tk.StringVar(value=str(daily[key]))
            ttk.Label(self.daily_frame,text=label).grid(row=row,column=column,sticky="w",padx=(0,8),pady=3)
            ttk.Entry(self.daily_frame,textvariable=self.daily_vars[key],width=9).grid(row=row,column=column+1,sticky="w",padx=(0,25),pady=3)
        flags=ttk.Frame(self.daily_frame);flags.grid(row=3,column=0,columnspan=6,sticky="w",pady=(7,0))
        for key,label in (("watch_ads","Keep real ad benefits active"),("include_events","Include available events")):
            self.daily_vars[key]=tk.BooleanVar(value=daily[key]);ttk.Checkbutton(flags,text=label,variable=self.daily_vars[key]).pack(side="left",padx=(0,18))
        self.daily_vars["adventure_level"]=tk.StringVar(value=daily["adventure_level"])
        ttk.Label(self.daily_frame,text="Reads live quest requirements, claims rewards, and uses only these budgets. Unavailable or unsupported dailies are reported.",style="Muted.TLabel").grid(row=4,column=0,columnspan=6,sticky="w",pady=(6,0))
        self.routine_state = tk.StringVar(value="Disarmed"); self.routine_interval = tk.StringVar(value="300")
        buttons = ttk.Frame(head, style="Card.TFrame"); buttons.pack(fill="x")
        ttk.Button(buttons,text="PREVIEW DAILY",command=self._routine_preview_daily).pack(side="left",padx=(0,8))
        ttk.Button(buttons, text="RUN ONCE", style="Accent.TButton", command=lambda: self._routine_start(False)).pack(side="left")
        ttk.Button(buttons, text="ARM REPEAT", command=lambda: self._routine_start(True)).pack(side="left", padx=8)
        ttk.Button(buttons, text="STOP ROUTINE", command=self._routine_stop).pack(side="left")
        ttk.Label(buttons, text="Repeat seconds", style="Card.TLabel").pack(side="left", padx=(18, 6))
        ttk.Entry(buttons, textvariable=self.routine_interval, width=8).pack(side="left")
        ttk.Label(buttons, textvariable=self.routine_state, style="Card.TLabel").pack(side="right")
        self.routine_list = tk.Listbox(self.routines_tab, bg="#08111e", fg="#dce7f4", selectbackground="#243755", relief="flat", height=6, font=("Segoe UI", 10))
        self.routine_list.pack(fill="x", pady=(0, 8))
        controls = ttk.Frame(self.routines_tab); controls.pack(fill="x")
        ttk.Button(controls, text="REMOVE STEP", command=self._routine_remove).pack(side="left")
        ttk.Button(controls, text="CLEAR", command=self._routine_clear).pack(side="left", padx=8)
        ttk.Button(controls, text="DAILY READ CHECK", command=self._routine_daily).pack(side="left")
        self.routine_log = self._logbox(self.routines_tab, height=8, pady=(10, 0))
        self._routine_load_profile()

    def _routine_daily_options(self):
        from .automation_profiles import DailyOptions
        from dataclasses import asdict
        options=asdict(DailyOptions.parse({key:var.get() for key,var in self.daily_vars.items()}))
        self.command_schema.store.update("daily_profile_options",options)
        return options

    def _routine_load_profile(self):
        self._routine_stop()
        try:self.routine_steps=self.automation_profiles.load(self.routine_profile.get())
        except ValueError as exc:return messagebox.showerror("Automation profile",str(exc))
        self.command_schema.store.update("automation_selected_profile",self.routine_profile.get())
        from .automation_profiles import BUILTIN_DAILY
        if self.routine_profile.get()==BUILTIN_DAILY:
            self.daily_frame.pack(fill="x",pady=(0,8),before=self.daily_frame.master.winfo_children()[-1])
            self.routine_steps[0]["args"]={"options":json.dumps(self._routine_daily_options())}
        else:
            self.daily_frame.pack_forget()
            daily=next((r for r in self.routine_steps if r["command"]=="daily-run"),None)
            if daily:
                try:
                    from .automation_profiles import DailyOptions
                    from dataclasses import asdict
                    options=asdict(DailyOptions.parse(daily.get("args",{}).get("options","{}")))
                    for key,value in options.items():self.daily_vars[key].set(value)
                    self.daily_frame.pack(fill="x",pady=(0,8),before=self.daily_frame.master.winfo_children()[-1])
                except ValueError:pass
        self._routine_render()

    def _routine_save_profile(self):
        name=simpledialog.askstring("Save automation profile","Profile name",parent=self.root)
        if not name:return
        try:
            self._routine_sync_daily()
            self.automation_profiles.save(name,self.routine_steps)
        except ValueError as exc:return messagebox.showerror("Automation profile",str(exc))
        self.routine_profile_combo.configure(values=self.automation_profiles.names())
        self.routine_profile.set(name);self.command_schema.store.update("automation_selected_profile",name)
        self._append(self.routine_log,f"Saved profile: {name}")

    def _routine_delete_profile(self):
        try:self.automation_profiles.delete(self.routine_profile.get())
        except ValueError as exc:return messagebox.showerror("Automation profile",str(exc))
        from .automation_profiles import BUILTIN_DAILY
        self.routine_profile_combo.configure(values=self.automation_profiles.names())
        self.routine_profile.set(BUILTIN_DAILY);self._routine_load_profile()

    def _routine_sync_daily(self):
        from .automation_profiles import BUILTIN_DAILY
        if self.routine_profile.get().casefold()==BUILTIN_DAILY.casefold():
            self.routine_steps=self.automation_profiles.load(BUILTIN_DAILY)
        for row in self.routine_steps:
            if row["command"]=="daily-run":row["args"]={"options":json.dumps(self._routine_daily_options())}
        self._routine_render()

    def _routine_preview_daily(self):
        if self.command_busy:return
        try:options=self._routine_daily_options()
        except ValueError as exc:return messagebox.showerror("Best Daily limits",str(exc))
        self._command_launch([("daily-plan",{"options":json.dumps(options)})],False)

    def _routine_render(self):
        self.routine_list.delete(0, "end")
        for row in self.routine_steps:
            self.routine_list.insert("end", "Best Daily — live quest plan using the limits above" if row["command"]=="daily-run" else row["command"] + "  " + json.dumps(row.get("args", {}), ensure_ascii=False))
        self.command_schema.store.update("routine", self.routine_steps)

    def _routine_add(self):
        if not self._routine_editable():return
        try:
            spec, args = self._command_values()
            if spec.destructive:
                raise CommandInputError("Guild membership changes and destructive commands run individually after review.")
            self._routine_stop(); self.routine_steps.append({"command": spec.name, "args": args}); self._routine_render()
            self.command_status.set(f"Added {spec.name} to the saved routine.")
        except ValueError as exc:
            messagebox.showerror("Routine", str(exc))

    def _routine_remove(self):
        if not self._routine_editable():return
        selected = self.routine_list.curselection()
        if selected:
            self._routine_stop()
            self.routine_steps.pop(selected[0]); self._routine_render()

    def _routine_clear(self):
        if not self._routine_editable():return
        self._routine_stop()
        self.routine_steps = []; self._routine_render()

    def _routine_daily(self):
        if not self._routine_editable():return
        self._routine_stop()
        self.routine_steps = [{"command": name, "args": {}} for name in ("user-quest", "research-info", "user-ad", "item-list", "events-current", "rumble-match", "siege-match")]
        self._routine_render()

    def _routine_editable(self):
        from .automation_profiles import BUILTIN_DAILY
        if self.routine_profile.get().casefold()==BUILTIN_DAILY.casefold():
            messagebox.showinfo("Best Daily", "Use Save As to create your own profile before changing Best Daily's steps. Its limits can be adjusted above.")
            return False
        return True

    def _routine_start(self, repeat):
        if self.command_busy or not self.routine_steps:
            return messagebox.showinfo("Routine", "Add steps first and wait for the active command to finish.")
        try:
            self._routine_sync_daily()
            interval = int(self.routine_interval.get())
            if interval < 60:
                raise ValueError()
            parsed = [(row["command"], self.command_schema.spec(row["command"]).parse_args(row.get("args", {}))) for row in self.routine_steps]
        except (ValueError, KeyError):
            return messagebox.showerror("Routine", "Check saved arguments and use a repeat interval of at least 60 seconds.")
        spending = [name for name, _ in parsed if self.command_schema.spec(name).spends_resources]
        if any(self.command_schema.spec(name).action for name,_ in parsed) and CommandCenterMixin._browser_action_busy(self,include_commands=False):
            return messagebox.showinfo("Routine", "Wait for Game / Ads or stop active automation before running a routine that changes account state or spends resources.")
        if spending and not messagebox.askyesno("Review routine", "This routine spends resources:\n" + "\n".join(spending) + ("\n\nIt will repeat until stopped." if repeat else "")):
            return
        if self._command_launch(parsed, True):
            self.routine_every = interval; self.routine_enabled = repeat; self._routine_status()

    def _routine_status(self):
        self.routine_state.set("Repeating" if self.routine_enabled else "Disarmed")

    def _browser_action_busy(self,*,include_commands=True,include_runners=True,ignore_thread=None):
        """Keep browser startup/input exclusive until its existing worker settles."""
        if getattr(self,"_closing",False):return True
        if getattr(self,"learn_session",None) is not None:return True
        for name in ("game_panel_thread","ad_thread","research_thread","deck_builder_thread"):
            worker=getattr(self,name,None)
            if worker is not None and worker is not ignore_thread and worker.is_alive():return True
        if getattr(self,"research_busy",False):return True
        if include_commands and getattr(self,"command_busy",False):return True
        if include_runners and any(getattr(getattr(self,name,None),"running",False) for name in
                ("arena_runner","event_runner","adventure_runner","swole_runner")):return True
        return False

    def _command_blocks_runner(self):
        if CommandCenterMixin._browser_action_busy(self,include_commands=False,include_runners=False):
            if not getattr(self,"_closing",False):
                messagebox.showinfo("AT Commander","Wait for Game / Ads or the research worker to finish, or use Stop All before starting another battle runner.")
            return True
        if getattr(self,"command_busy",False) and getattr(self,"command_mutating",False):
            messagebox.showinfo("AT Commander","Wait for the current command/routine, or use Stop All before starting another battle runner.")
            return True
        return False

    def _routine_stop(self):
        self.routine_enabled = False; self.command_stop.set(); self._routine_status()

    def _command_tick(self):
        if self.routine_enabled and not self.command_busy and time.monotonic() >= self.routine_next:
            mutates = any(self.command_schema.spec(r["command"]).action for r in self.routine_steps)
            if mutates and CommandCenterMixin._browser_action_busy(self,include_commands=False):
                self.routine_next = time.monotonic() + 10
                self.routine_state.set("Waiting for active automation / Game / Ads")
            else:
                self._command_launch([(r["command"], r.get("args", {})) for r in self.routine_steps], True)
        if self.subscription_busy or self.command_busy:
            return
        subscriptions = self.command_schema.store.read().get("subscriptions", {})
        due = [name for name, row in subscriptions.items() if row.get("enabled") and time.monotonic() >= self.subscription_next.get(name, 0)]
        for feed in self.command_schema.store.read().get("feeds",[]):
            name="feed:"+feed
            if time.monotonic()>=self.subscription_next.get(name,0):due.append(name)
        if not due:
            return
        for name in due:
            self.subscription_next[name] = time.monotonic() + max(60, int(subscriptions.get(name,{}).get("interval", 300)))
        self.subscription_busy = True
        def poll():
            checks = []
            try:
                client = ATClient(load_credentials()); service = build_service(client, store=self.command_schema.store)
                mapping = {"guild-joins": "guild-roster", "guild-leaves": "guild-roster", "rumble-match": "rumble-match", "rumble-stats": "rumble-stats", "shop-mastery": "buy-mastery", "siege-match": "siege-match"}
                for name in due:
                    if name.startswith("feed:"):
                        answer=service.execute("guild-activity")
                        text=answer.details or json.dumps(answer.rows,sort_keys=True)
                    elif name == "game-update":
                        text = json.dumps(sanitize_report(client.get_static_files()), sort_keys=True)
                    else:
                        answer = service.execute(mapping[name])
                        text = answer.details or json.dumps(answer.rows, sort_keys=True)
                    digest = notification_digest(text)
                    checks.append((name, digest, text[:5000] if name.startswith("feed:") else "Open Command Center to inspect the new data."))
                self.events.put({"kind": "subscription_result", "checks": checks})
            except Exception as exc:
                self.events.put({"kind": "subscription_result", "checks": checks, "error": "Subscription check failed: " + safe_text(str(exc), getattr(getattr(locals().get("service"), "context", None), "private", ()))})
        threading.Thread(target=poll, daemon=True).start()
