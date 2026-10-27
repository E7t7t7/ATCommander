"""Keep cyclic Tk cleanup on its owning thread and record safe crash stacks."""
from __future__ import annotations

import faulthandler
import gc
from pathlib import Path
import threading
import traceback


class MainThreadRuntime:
    def __init__(self, log_path):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("The GUI runtime must be created on the main thread.")
        self.owner=threading.get_ident();self.gc_was_enabled=gc.isenabled()
        self.fault_was_enabled=faulthandler.is_enabled();self.file=None;self.root=None
        self.after_id=None;self.closed=False
        # Tk objects can be part of cycles. Automatic collection otherwise runs
        # on whichever thread crosses Python's allocation threshold, including
        # a research worker, where Tcl's finalizers are unsafe.
        gc.disable()
        try:
            self.file=Path(log_path).open('a',encoding='utf-8',buffering=1)
            if not self.fault_was_enabled:faulthandler.enable(file=self.file,all_threads=True)
        except OSError:
            self.file=None

    def record_exception(self, exception_type, tb):
        """Record category and source positions; never message, locals or data."""
        if self.file is None:return
        self.file.write('Unhandled GUI exception: '+exception_type.__name__+'\n')
        for frame in traceback.extract_tb(tb):
            self.file.write(f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}\n')
        self.file.flush()

    def attach(self, root, on_error=None):
        self.root=root
        def report(exception_type, value, tb):
            self.record_exception(exception_type,tb)
            if on_error is not None:on_error(exception_type.__name__)
        root.report_callback_exception=report
        self.after_id=root.after(5000,self.collect)

    def collect(self):
        if threading.get_ident()!=self.owner:
            raise RuntimeError("GUI cleanup must run on the main thread.")
        self.after_id=None
        if self.closed:return
        gc.collect()
        if self.root is not None:self.after_id=self.root.after(5000,self.collect)

    def close(self):
        if threading.get_ident()!=self.owner:
            raise RuntimeError("GUI cleanup must run on the main thread.")
        self.closed=True
        if self.after_id is not None and self.root is not None:
            try:self.root.after_cancel(self.after_id)
            except Exception:pass
        self.after_id=None;self.root=None
        gc.collect()
        if self.gc_was_enabled:gc.enable()
        if self.file is not None:
            if not self.fault_was_enabled:faulthandler.disable()
            self.file.close();self.file=None
