"""Commander update button: downloads on a worker, installs only on next launch."""
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from .app_updater import prepare_update, runtime_fingerprint, UpdateError
from . import __version__


class AppUpdateMixin:
    def _build_app_update(self, parent):
        self.app_update_stop = threading.Event()
        self.app_update_thread = None
        self.app_update_restart_pending=False
        try:self._running_source_fingerprint=runtime_fingerprint(Path(__file__).resolve().parent.parent)
        except Exception:self._running_source_fingerprint=None
        self.app_update_status = tk.StringVar(value=f"Running {__version__} • Updates from E7t7t7 / ATCommander")
        bar = ttk.Frame(parent, style="Card.TFrame", padding=(12, 8))
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Commander", style="Card.TLabel", font=("Segoe UI Semibold", 10)).pack(side="left")
        ttk.Label(bar, textvariable=self.app_update_status, style="Muted.Card.TLabel").pack(side="left", padx=14)
        self.app_update_button = ttk.Button(bar, text="Update Commander", command=self._start_app_update)
        self.app_update_button.pack(side="right")

    def _start_app_update(self):
        if getattr(self, "_closing", False): return
        if getattr(self,'app_update_restart_pending',False):
            self._restart_for_update();return
        worker = self.app_update_thread
        if worker is not None and worker.is_alive():
            self.app_update_stop.set()
            self.app_update_status.set("Cancelling the app download…")
            return
        self.app_update_stop.clear()
        self.app_update_button.configure(text="Cancel download")
        self.app_update_status.set("Checking Commander on GitHub…")

        def work():
            try:
                result = prepare_update(Path(__file__).resolve().parent.parent,
                    progress=lambda text: self.events.put({"kind": "app_update_progress", "text": text}),
                    should_stop=self.app_update_stop.is_set,running_fingerprint=getattr(self,'_running_source_fingerprint',None))
                self.events.put({"kind": "app_update_done", **result})
            except UpdateError as error:
                self.events.put({"kind": "app_update_done", "message": str(error)})
            except Exception:
                self.events.put({"kind": "app_update_done", "message": "The app update could not finish. Your current installation was kept."})

        self.app_update_thread = threading.Thread(target=work, daemon=True, name="Commander app update")
        self.app_update_thread.start()

    def _app_update_event(self, event):
        if event.get("kind") not in {"app_update_progress", "app_update_done"}: return False
        if getattr(self, "_closing", False): return True
        self.app_update_status.set(event.get("text") or event.get("message") or "Update check finished.")
        if event["kind"] == "app_update_done":
            self.app_update_restart_pending=bool(event.get('ready') or event.get('restart_required'))
            self.app_update_button.configure(text="Restart to apply" if self.app_update_restart_pending else "Update Commander")
        return True

    def _restart_for_update(self):
        if getattr(self,'_closing',False):return
        self._restart_requested=True
        self.app_update_status.set('Stopping active work safely, then reopening Commander…')
        self.close_app()
