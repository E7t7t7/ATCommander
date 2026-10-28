"""Resizable activity views; all widgets and refreshes stay on the Tk thread."""
import tkinter as tk
from tkinter import ttk


def split_controls_log(parent, controls_height=360):
    panes = tk.PanedWindow(parent, orient="vertical", bg="#253443", sashwidth=9,
                           sashrelief="flat", borderwidth=0, opaqueresize=True)
    panes.pack(fill="both", expand=True, pady=(6, 0))
    scroll = ttk.Frame(panes)
    canvas = tk.Canvas(scroll, bg="#10151d", highlightthickness=0, height=controls_height)
    bar = ttk.Scrollbar(scroll, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
    controls = ttk.Frame(canvas)
    item = canvas.create_window((0, 0), window=controls, anchor="nw")
    controls.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=e.width))
    def wheel(event):
        if event.delta: canvas.yview_scroll(-int(event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
    # A dedicated scroll surface avoids stealing combobox/list mouse wheels.
    canvas.bind("<MouseWheel>", wheel)
    bottom = ttk.Frame(panes)
    panes.add(scroll, minsize=100, height=controls_height, stretch="always")
    panes.add(bottom, minsize=110, height=180, stretch="always")
    return panes, controls, bottom


def logbox(parent, height=14, pady=(0, 0)):
    frame = ttk.Frame(parent, style="Card.TFrame", padding=7)
    frame.pack(fill="both", expand=True, pady=pady)
    toolbar = ttk.Frame(frame, style="Card.TFrame"); toolbar.pack(fill="x", pady=(0, 4))
    ttk.Label(toolbar, text="Activity", style="Muted.Card.TLabel").pack(side="left")
    text_frame = ttk.Frame(frame); text_frame.pack(fill="both", expand=True)
    text = tk.Text(text_frame, bg="#151d27", fg="#c4d0dd", insertbackground="#fff", relief="flat",
                   font=("Segoe UI", 9), wrap="word", height=height, padx=6, pady=6, spacing1=3)
    scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=text.yview)
    scrollbar.pack(side="right", fill="y"); text.pack(side="left", fill="both", expand=True)
    text.configure(state="disabled", yscrollcommand=scrollbar.set)
    ttk.Button(toolbar, text="Open larger log", command=lambda: open_log_window(text)).pack(side="right")
    return text


def open_log_window(source):
    existing = getattr(source, "_log_window", None)
    if existing is not None and existing.winfo_exists():
        existing.deiconify(); existing.lift(); return existing
    window = tk.Toplevel(source); source._log_window = window
    window.title("Commander activity • live log"); window.geometry("960x540"); window.minsize(420, 220)
    window.configure(bg="#10151d")
    follow = tk.BooleanVar(window, value=True)
    header = ttk.Frame(window, padding=8); header.pack(fill="x")
    ttk.Label(header, text="Live activity • drag any edge to resize").pack(side="left")
    ttk.Checkbutton(header, text="Follow latest", variable=follow).pack(side="right")
    frame = ttk.Frame(window, padding=8); frame.pack(fill="both", expand=True)
    text = tk.Text(frame, bg="#151d27", fg="#c4d0dd", relief="flat", wrap="word", font=("Segoe UI", 10), padx=8, pady=8)
    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    scrollbar.pack(side="right", fill="y"); text.pack(side="left", fill="both", expand=True)
    text.configure(yscrollcommand=scrollbar.set, state="disabled")
    window._live_text = text
    last = [None]; scheduled = [None]
    def refresh():
        try:
            content = source.get("end-1500l", "end-1c")
            if content != last[0]:
                position = text.yview()[0]; text.configure(state="normal")
                text.delete("1.0", "end"); text.insert("end", content); text.configure(state="disabled")
                if follow.get(): text.see("end")
                else: text.yview_moveto(position)
                last[0] = content
            scheduled[0] = window.after(400, refresh)
        except tk.TclError: pass
    def close():
        if scheduled[0] is not None:
            try: window.after_cancel(scheduled[0])
            except tk.TclError: pass
        window.destroy(); source._log_window = None
    window.protocol("WM_DELETE_WINDOW", close)
    refresh()
    return window
