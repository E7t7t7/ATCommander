"""Dedicated owned-combo research queue with visible budgets and progress."""
from __future__ import annotations

import json
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from .api import ATClient
from .credentials import load_credentials
from .utility_commands import build_service
from .diagnostic_privacy import safe_text
from .command_center import CommandCenterMixin

RESEARCH_NETWORK_RETRY_DELAYS=(15,30,60)


class ResearchCenterMixin:
    def _build_research_center(self):
        self.research_auto=False;self.research_next=0;self.research_busy=False
        self._research_generation=0;self._research_frozen_launch=None;self._research_pending_retry=None
        self.research_render_generation=0;self.research_render_after=None;self.research_thread=None
        saved=self.settings.get("research_queue_options",{})
        head=ttk.Frame(self.research_tab,style="Card.TFrame",padding=14);head.pack(fill="x",pady=(10,8))
        ttk.Label(head,text="RESEARCH EVERY OWNED COMBO",style="Big.Card.TLabel").pack(anchor="w")
        ttk.Label(head,text="Queue unresearched recipes from your owned cards. Completed pairs are excluded and active research is respected. Budgets apply to the whole queue, including resumed runs.",style="Muted.Card.TLabel",wraplength=1150).pack(anchor="w",pady=(4,10))
        tierrow=ttk.Frame(head,style="Card.TFrame");tierrow.pack(fill="x")
        ttk.Label(tierrow,text="Input tiers",style="Card.TLabel").pack(side="left",padx=(0,12))
        self.research_tiers={}
        for value,label in ((1,"Common"),(2,"Rare"),(3,"Epic"),(4,"Legendary"),(5,"Mythic")):
            var=tk.BooleanVar(value=value in saved.get("tiers",[4,5]));self.research_tiers[value]=var
            ttk.Checkbutton(tierrow,text=label,variable=var).pack(side="left",padx=5)
        self.research_match=tk.StringVar(value=saved.get("tier_match","either"))
        ttk.Label(tierrow,text="Match",style="Card.TLabel").pack(side="left",padx=(18,6))
        ttk.Combobox(tierrow,textvariable=self.research_match,values=("either","both"),state="readonly",width=9).pack(side="left")
        options=ttk.Frame(head,style="Card.TFrame");options.pack(fill="x",pady=(12,0))
        manual=self.settings.get("research_manual_speedup_limits",{}) if saved.get("use_all_speedups",False) else saved
        self.research_hours=tk.StringVar(value=str(manual.get("max_speedup_hours",24)))
        self.research_reserve=tk.StringVar(value=str(manual.get("reserve_hours",100)))
        self.research_pairs=tk.StringVar(value=str(saved.get("max_pairs",100000)))
        self.research_hour_entries=[]
        for label,var in (("Speedup budget (hours)",self.research_hours),("Keep speedups (hours)",self.research_reserve),("Maximum combos",self.research_pairs)):
            ttk.Label(options,text=label,style="Card.TLabel").pack(side="left",padx=(0,7))
            entry=ttk.Entry(options,textvariable=var,width=8);entry.pack(side="left",padx=(0,18))
            if var is not self.research_pairs:self.research_hour_entries.append(entry)
        flags=ttk.Frame(head,style="Card.TFrame");flags.pack(fill="x",pady=(12,0))
        self.research_speedups=tk.BooleanVar(value=saved.get("speedups",True))
        self.research_ads=tk.BooleanVar(value=saved.get("ads",True))
        self.research_rogu=tk.BooleanVar(value=saved.get("prioritize_rogu",True))
        for label,var in (("Use speedups",self.research_speedups),("Keep real ad benefits active",self.research_ads),("Prioritize Rogu recipes",self.research_rogu)):
            button=ttk.Checkbutton(flags,text=label,variable=var);button.pack(side="left",padx=(0,18))
            if var is self.research_speedups:self.research_speedups_button=button
        allrow=ttk.Frame(head,style="Card.TFrame");allrow.pack(fill="x",pady=(9,0))
        self.research_all_speedups=tk.BooleanVar(value=saved.get("use_all_speedups",False))
        ttk.Checkbutton(allrow,text="Use all available speedups (no reserve)",variable=self.research_all_speedups,
                        command=self._research_speedup_mode_changed).pack(side="left",padx=(0,18))
        ttk.Label(allrow,text="Ignores the hour budget and reserve for selected recipes. New Queue applies changed settings.",
                  style="Muted.Card.TLabel").pack(side="left")
        self._research_speedup_mode_changed()
        controls=ttk.Frame(self.research_tab);controls.pack(fill="x",pady=(0,8))
        ttk.Button(controls,text="PREVIEW QUEUE",command=lambda:self._research_launch(True)).pack(side="left")
        ttk.Button(controls,text="START / RESUME",command=lambda:self._research_launch(False),style="Accent.TButton").pack(side="left",padx=8)
        ttk.Button(controls,text="NEW QUEUE",command=lambda:self._research_launch(False,resume=False)).pack(side="left",padx=(0,8))
        ttk.Button(controls,text="CHECK SAVED ACTION",command=lambda:self._research_launch(False,reconcile=True)).pack(side="left",padx=(0,8))
        ttk.Button(controls,text="PAUSE",command=self._research_pause).pack(side="left")
        self.research_status=tk.StringVar(value="Paused. Preview the queue to see its current recipes and available speedups.")
        ttk.Label(controls,textvariable=self.research_status).pack(side="left",padx=14)
        table=ttk.Frame(self.research_tab);table.pack(fill="both",expand=True)
        self.research_tree=ttk.Treeview(table,columns=("combo","input1","input2","tier","state"),show="headings",height=12)
        for key,label,width in (("combo","Combo",280),("input1","First input",230),("input2","Second input",230),("tier","Tiers",100),("state","State",130)):
            self.research_tree.heading(key,text=label);self.research_tree.column(key,width=width)
        scroll=ttk.Scrollbar(table,orient="vertical",command=self.research_tree.yview)
        self.research_tree.configure(yscrollcommand=scroll.set);scroll.pack(side="right",fill="y");self.research_tree.pack(fill="both",expand=True)
        self.research_log=self._logbox(self.research_tab,height=8,pady=(10,0))

    def _research_speedup_mode_changed(self):
        use_all=self.research_all_speedups.get()
        if use_all:self.research_speedups.set(True)
        self.research_speedups_button.configure(state="disabled" if use_all else "normal")
        for entry in self.research_hour_entries:entry.configure(state="disabled" if use_all else "normal")

    def _research_values(self):
        tiers=[tier for tier,var in self.research_tiers.items() if var.get()]
        if not tiers:raise ValueError("Select at least one input tier.")
        use_all=self.research_all_speedups.get()
        values={"tiers":tiers,"tier_match":self.research_match.get(),"speedups":use_all or self.research_speedups.get(),
                "ads":self.research_ads.get(),"prioritize_rogu":self.research_rogu.get(),
                "use_all_speedups":use_all,
                "max_speedup_hours":0 if use_all else int(self.research_hours.get()),
                "reserve_hours":0 if use_all else int(self.research_reserve.get()),
                "max_pairs":int(self.research_pairs.get())}
        if not 0<=values["max_speedup_hours"]<=1000000 or not 0<=values["reserve_hours"]<=1000000 or not 1<=values["max_pairs"]<=100000:
            raise ValueError("Use a speedup budget/reserve of 0–1,000,000 hours and 1–100,000 combos, or select Use all available speedups.")
        try:
            manual={"max_speedup_hours":int(self.research_hours.get()),"reserve_hours":int(self.research_reserve.get())}
            if all(0<=value<=1000000 for value in manual.values()):self.settings["research_manual_speedup_limits"]=manual
        except ValueError:pass
        self.settings["research_queue_options"]=values
        from .settings import save_settings
        save_settings(self.settings)
        return values

    def _research_launch(self,preview,resume=True,reconcile=False,*,_frozen=None):
        if getattr(self,"_closing",False):return False
        if getattr(self,"command_busy",False) or getattr(self,"research_busy",False):return False
        if CommandCenterMixin._browser_action_busy(self):
            if _frozen is None:messagebox.showinfo("Research","Wait for Game / Ads or stop active automation before starting the research queue.")
            return False
        if _frozen is not None:
            if (preview or reconcile or not getattr(self,"research_auto",False) or self.command_stop.is_set()
                    or _frozen.get("generation")!=getattr(self,"_research_generation",0)):
                return False
            launch=dict(_frozen);resume=launch["resume"]
        else:
            try:options=None if reconcile else self._research_values()
            except ValueError as exc:
                messagebox.showerror("Research settings",str(exc));return False
            self._research_generation=getattr(self,"_research_generation",0)+1
            launch={"generation":self._research_generation,"options_json":None if reconcile else json.dumps(options),
                    "resume":bool(resume),"retries":0,"run":not preview and not reconcile}
        self._research_pending_retry=None
        self._research_frozen_launch=dict(launch) if launch["run"] else None
        self.research_auto=not preview and not reconcile;self.research_busy=True
        self.command_busy=True;self.command_mutating=not preview and not reconcile;self.command_stop.clear()
        self.command_run_button.configure(state="disabled")
        self.research_status.set("Checking the saved action without spending resources…" if reconcile else
                                 "Building the current queue…" if preview else "Researching within the selected speedup settings…")
        def work():
            service=None
            def result(**payload):
                self.events.put({"kind":"research_result","preview":preview,"reconcile":reconcile,"launch":launch,**payload})
            try:
                if self.command_stop.is_set():result(cancelled=True);return
                service=build_service(ATClient(load_credentials(),timeout=45),store=self.command_schema.store,
                    emit=lambda message:self.events.put({"kind":"research_log","generation":launch["generation"],"message":message.get("message",str(message)) if isinstance(message,dict) else str(message)}))
                service.stop=self.command_stop
                if self.command_stop.is_set():result(cancelled=True);return
                args={} if reconcile else {"options":launch["options_json"]}
                if not preview and not reconcile:args["resume"]=resume
                answer=service.execute("research-reconcile" if reconcile else "research-plan" if preview else "research-run",args)
                result(answer=answer)
            except Exception as exc:
                result(error=safe_text(str(exc),getattr(getattr(service,"context",None),"private",())),
                       network_retryable=getattr(exc,"network_retryable",False) is True,
                       queue_replaced=getattr(exc,"queue_replaced",False) is True)
        self.research_thread=threading.Thread(target=work,daemon=True,name="AT Research")
        self.research_thread.start()
        return True

    def _research_pause(self):
        self.research_auto=False
        self._research_pending_retry=None;self._research_frozen_launch=None
        if self.research_busy:self.command_stop.set()
        self._research_cancel_render()
        self.research_status.set("Paused; the current request will settle before the queue stops.")

    def _research_cancel_render(self):
        self.research_render_generation=getattr(self,"research_render_generation",0)+1
        pending=getattr(self,"research_render_after",None)
        self.research_render_after=None
        if pending is not None:
            try:self.root.after_cancel(pending)
            except tk.TclError:pass

    def _research_render_rows(self,rows):
        """Keep Stop and other Tk callbacks responsive for a large queue."""
        self._research_cancel_render()
        generation=self.research_render_generation
        remaining=list(self.research_tree.get_children());rows=rows[:5000]
        def chunk(offset=0):
            self.research_render_after=None
            if generation!=self.research_render_generation or getattr(self,"_closing",False):return
            if remaining:
                batch=remaining[:100];del remaining[:100]
                self.research_tree.delete(*batch)
                self.research_render_after=self.root.after(1,lambda:chunk(offset));return
            for row in rows[offset:offset+50]:
                self.research_tree.insert("","end",values=(row.get("name",row.get("combo_id","")),
                    row.get("card1_name",row.get("card1","")),row.get("card2_name",row.get("card2","")),
                    f"{row.get('rarity1','')} / {row.get('rarity2','')}",row.get("status","Queued")))
            if offset+50<len(rows):self.research_render_after=self.root.after(1,lambda:chunk(offset+50))
        chunk()

    def _research_event(self,event):
        if event.get("kind")=="research_log":
            if event.get("generation",getattr(self,"_research_generation",0))!=getattr(self,"_research_generation",0):return True
            self._append(self.research_log,event["message"]);return True
        if event.get("kind")!="research_result":return False
        launch=event.get("launch")
        if launch is not None and launch.get("generation")!=getattr(self,"_research_generation",0):return True
        self.research_busy=False;self.command_busy=False;self.command_run_button.configure(state="normal")
        if event.get("cancelled"):
            self.research_auto=False;self._research_pending_retry=None;self._research_frozen_launch=None
            self.research_status.set("Paused before the next Research request.");return True
        if event.get("error"):
            retryable=(event.get("network_retryable") is True and isinstance(launch,dict) and launch.get("run") is True
                       and not event.get("preview") and not event.get("reconcile") and getattr(self,"research_auto",False)
                       and not self.command_stop.is_set() and not getattr(self,"_closing",False))
            if retryable:
                retries=launch["retries"]
                if retries<len(RESEARCH_NETWORK_RETRY_DELAYS):
                    delay=RESEARCH_NETWORK_RETRY_DELAYS[retries]
                    retry={**launch,"resume":launch["resume"] or event.get("queue_replaced") is True,"retries":retries+1}
                    self._research_pending_retry=retry;self._research_frozen_launch=dict(retry)
                    self.research_next=time.monotonic()+delay
                    text=f"Research connection interrupted; retry {retries+1}/{len(RESEARCH_NETWORK_RETRY_DELAYS)} in {delay}s. Saved settings and queue intent retained."
                    self.research_status.set(text);self._append(self.research_log,text);return True
                self.research_auto=False;self._research_pending_retry=None;self._research_frozen_launch=None
                text=f"Research paused after {len(RESEARCH_NETWORK_RETRY_DELAYS)} automatic read retries. Check the connection and use Start / Resume when ready."
                self.research_status.set(text);self._append(self.research_log,text);return True
            self._research_pending_retry=None;self._research_frozen_launch=None
            self.research_auto=False;self.research_status.set("Stopped. "+event["error"])
            self._append(self.research_log,event["error"]);return True
        self._research_pending_retry=None
        if isinstance(launch,dict) and launch.get("run") is True and getattr(self,"research_auto",False) and not self.command_stop.is_set():
            self._research_frozen_launch={**launch,"resume":True,"retries":0}
        answer=event["answer"]
        self.research_status.set(("Paused. " if self.command_stop.is_set() else "")+answer.message)
        self._append(self.research_log,answer.message)
        if "speedups_available_hours" in answer.data:
            available=answer.data.get("speedups_available_hours")
            hours=f"{available:g} hours" if isinstance(available,(int,float)) else "not requested / unavailable"
            self._append(self.research_log,f"Available speedups: {hours}. Free chambers: {', '.join(map(str,answer.data.get('free_slots',[]))) or 'none'}.")
        for blocked in answer.data.get("blocked_slots",[]):
            self._append(self.research_log,f"Chamber {blocked.get('slot_index')}: {blocked.get('reason','unavailable')}")
        if not self.command_stop.is_set() and (answer.data.get("rows_truncated") or len(answer.rows)>5000):
            self._append(self.research_log,"Showing the first 5,000 recipes. The complete selected queue is retained for research; the count includes every selected pair.")
        if not self.command_stop.is_set():self._research_render_rows(answer.rows)
        state=answer.data.get("status")
        if state not in {"waiting","running"} or self.command_stop.is_set():self.research_auto=False
        self.research_next=time.monotonic()+30
        return True

    def _research_tick(self):
        if getattr(self,"_closing",False):return
        if getattr(self,"research_auto",False) and self.command_stop.is_set():
            self.research_auto=False;self._research_pending_retry=None;self._research_frozen_launch=None;return
        if self.research_auto and not self.research_busy and not self.command_busy and time.monotonic()>=self.research_next:
            if CommandCenterMixin._browser_action_busy(self):
                self.research_next=time.monotonic()+5
                return
            launch=getattr(self,"_research_pending_retry",None) or getattr(self,"_research_frozen_launch",None)
            if launch is None:self._research_launch(False)
            elif not self._research_launch(False,_frozen=launch):self.research_next=time.monotonic()+5
