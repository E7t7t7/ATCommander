"""GUI installation feedback and console-free subprocess startup."""
from __future__ import annotations

import hashlib
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .diagnostic_privacy import safe_text

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / '.venv'
PY = VENV / 'Scripts' / 'python.exe'
PYW = VENV / 'Scripts' / 'pythonw.exe'
MARKER = VENV / '.atc_ready'


def requirements_signature() -> str:
    return hashlib.sha256((ROOT / 'requirements.txt').read_bytes()).hexdigest()


def runtime_ready() -> bool:
    try:
        return PY.exists() and PYW.exists() and MARKER.read_text(encoding='utf-8').strip() == requirements_signature()
    except OSError:
        return False


def hidden_process_options() -> dict:
    """Hide every console-capable child, even when no pythonw is available."""
    options = {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
    if hasattr(subprocess, 'STARTUPINFO'):
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        options['startupinfo'] = startup
    return options


def launch_app():
    with (ROOT / 'atcommander_crash.log').open('a', encoding='utf-8') as errors:
        return subprocess.Popen(
            [str(PYW if PYW.exists() else PY), str(ROOT / 'app.py')],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=errors, **hidden_process_options(),
        )


class Bootstrap:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('AT Commander Setup')
        self.root.geometry('680x390')
        self.root.configure(bg='#111827')
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Setup.Horizontal.TProgressbar', troughcolor='#172235', background='#60a5fa', bordercolor='#172235')
        tk.Label(self.root, text='AT Commander', bg='#111827', fg='#f1f5f9', font=('Segoe UI Semibold', 20)).pack(anchor='w', padx=22, pady=(20, 3))
        self.status = tk.StringVar(value='Preparing your local installation…')
        tk.Label(self.root, textvariable=self.status, bg='#111827', fg='#94a3b8', font=('Segoe UI', 10)).pack(anchor='w', padx=22)
        self.text = tk.Text(self.root, bg='#0b1220', fg='#cbd5e1', font=('Consolas', 9), relief='flat', height=12, padx=10, pady=8)
        self.text.pack(fill='both', expand=True, padx=22, pady=16)
        self.bar = ttk.Progressbar(self.root, style='Setup.Horizontal.TProgressbar', mode='indeterminate')
        self.bar.pack(fill='x', padx=22, pady=(0, 20))
        self.bar.start(12)
        threading.Thread(target=self.work, daemon=True).start()

    def log(self, text):
        message = safe_text(str(text)).rstrip() + '\n'
        self.root.after(0, lambda: (self.text.insert('end', message), self.text.see('end')))

    def run(self, command, label):
        self.root.after(0, lambda: self.status.set(label))
        process = subprocess.Popen(
            command, cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
            **hidden_process_options(),
        )
        for line in process.stdout or []:
            self.log(line)
        code = process.wait()
        if code:
            raise RuntimeError(f'{label} failed (exit {code}).')

    def work(self):
        try:
            if not PY.exists():
                self.run([sys.executable, '-m', 'venv', str(VENV)], 'Creating local Python environment…')
            if not runtime_ready():
                self.run([str(PY), '-m', 'pip', 'install', '-q', '-r', str(ROOT / 'requirements.txt')], 'Installing required components…')
                MARKER.write_text(requirements_signature(), encoding='utf-8')
            self.root.after(0, lambda: self.status.set('Ready — opening AT Commander…'))
            process = launch_app()
            try:
                code = process.wait(timeout=1.2)
            except subprocess.TimeoutExpired:
                code = None
            if code not in (None, 0):
                MARKER.unlink(missing_ok=True)
                raise RuntimeError('AT Commander could not finish starting. Review atcommander_crash.log in this folder.')
            self.root.after(0, self.root.destroy)
        except Exception as error:
            message = safe_text(str(error))
            self.log(message)
            # Bind now: Python clears the exception variable after this block.
            self.root.after(0, lambda text=message: (self.bar.stop(), self.status.set('Setup needs attention'), messagebox.showerror('AT Commander', text + '\n\nDetails are shown in this window.')))

    def go(self):
        self.root.mainloop()


def main():
    from .app_updater import startup, UpdateError
    try:
        startup(ROOT)
    except UpdateError as error:
        messagebox.showerror('AT Commander', str(error))
        return
    if runtime_ready():
        process = launch_app()
        try:
            code = process.wait(timeout=1.2)
        except subprocess.TimeoutExpired:
            code = None
        if code not in (None, 0):
            MARKER.unlink(missing_ok=True)
            messagebox.showerror('AT Commander', 'AT Commander could not finish starting. Review atcommander_crash.log in this folder. Open the EXE again to run setup.')
    else:
        Bootstrap().go()
