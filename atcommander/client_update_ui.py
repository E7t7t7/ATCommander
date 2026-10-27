"""Background public-client checking; no account or combat action is started."""
import threading
import tkinter as tk
from tkinter import ttk
from .client_updater import update_public_client_profile
from .public_client_version import PublicClientVersionError

class ClientUpdateMixin:
    def _build_client_update(self,parent):
        self.client_update_stop=threading.Event();self.client_update_thread=None
        self.client_update_status=tk.StringVar(value="Game connection ready to check")
        bar=ttk.Frame(parent,style="Card.TFrame",padding=(12,8));bar.pack(fill="x",pady=(0,8))
        ttk.Label(bar,text="Game client",style="Card.TLabel",font=("Segoe UI Semibold",10)).pack(side="left")
        ttk.Label(bar,textvariable=self.client_update_status,style="Muted.Card.TLabel").pack(side="left",padx=14)
        self.client_update_button=ttk.Button(bar,text="Check / update game client",command=self._start_client_update)
        self.client_update_button.pack(side="right")

    def _start_client_update(self):
        if getattr(self,"_closing",False):return
        worker=getattr(self,"client_update_thread",None)
        if worker is not None and worker.is_alive():
            self.client_update_stop.set();self.client_update_status.set("Cancelling the client check…");return
        self.client_update_stop.clear()
        self.client_update_button.configure(text="Cancel check")
        self.client_update_status.set("Checking the official game client…")
        def work():
            try:
                answer=update_public_client_profile(
                    progress=lambda text:self.events.put({"kind":"client_update_progress","text":text}),
                    should_stop=self.client_update_stop.is_set)
                self.events.put({"kind":"client_update_done","message":answer["message"]})
            except PublicClientVersionError as error:
                self.events.put({"kind":"client_update_done","message":str(error),"error":error.reason!="client_update_cancelled"})
            except Exception:
                self.events.put({"kind":"client_update_done","message":"The client check could not finish. Your existing profile was kept.","error":True})
        self.client_update_thread=threading.Thread(target=work,daemon=True,name="AT Client Update")
        self.client_update_thread.start()

    def _client_update_event(self,event):
        if event.get("kind") not in {"client_update_progress","client_update_done"}:return False
        if getattr(self,"_closing",False):return True
        self.client_update_status.set(event.get("text") or event.get("message") or "Client check finished.")
        if event["kind"]=="client_update_done":
            self.client_update_button.configure(text="Check / update game client")
        return True
