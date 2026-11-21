"""Resizable activity views; all widgets and refreshes stay on the Tk thread."""
import tkinter as tk
from tkinter import ttk


def split_controls_log(parent, controls_height=360, *, accessible_scroll=False):
    panes = tk.PanedWindow(parent, orient="vertical", bg="#253443", sashwidth=9,
                           sashrelief="flat", borderwidth=0, opaqueresize=True)
    panes.pack(fill="both", expand=True, pady=(6, 0))
    scroll = ttk.Frame(panes)
    canvas = tk.Canvas(scroll, bg="#10151d", highlightthickness=0, height=controls_height)
    if accessible_scroll:
        # Use a real, wide dark scrollbar with a visible thumb. Native ttk themes
        # can render the old narrow trough almost indistinguishably from the page.
        bar=tk.Scrollbar(scroll,orient="vertical",command=canvas.yview,width=22,
                         bg="#668b91",activebackground="#8db8bc",troughcolor="#253443",
                         relief="flat",borderwidth=0,highlightthickness=0)
        horizontal=tk.Scrollbar(scroll,orient="horizontal",command=canvas.xview,width=18,
                                bg="#668b91",activebackground="#8db8bc",troughcolor="#253443",
                                relief="flat",borderwidth=0,highlightthickness=0)
        canvas.configure(yscrollcommand=bar.set,xscrollcommand=horizontal.set)
        scroll.rowconfigure(0,weight=1);scroll.columnconfigure(0,weight=1)
        canvas.grid(row=0,column=0,sticky="nsew");bar.grid(row=0,column=1,sticky="ns")
        horizontal.grid(row=1,column=0,sticky="ew")
        footer=ttk.Frame(scroll);footer.grid(row=2,column=0,columnspan=2,sticky="ew")
        ttk.Label(footer,text="Scroll settings · drag the right scrollbar or use ▲ / ▼").pack(side="left",padx=6)
        ttk.Button(footer,text="▲",width=4,command=lambda:canvas.yview_scroll(-1,"pages")).pack(side="right",padx=2)
        ttk.Button(footer,text="▼",width=4,command=lambda:canvas.yview_scroll(1,"pages")).pack(side="right",padx=2)
    else:
        bar=ttk.Scrollbar(scroll,orient="vertical",command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right",fill="y");canvas.pack(side="left",fill="both",expand=True)
    controls=ttk.Frame(canvas)
    item=canvas.create_window((0,0),window=controls,anchor="nw")
    def resize(event=None):
        width=canvas.winfo_width()
        if accessible_scroll:width=max(width,controls.winfo_reqwidth())
        canvas.itemconfigure(item,width=width)
        canvas.configure(scrollregion=canvas.bbox("all"))
    controls.bind("<Configure>",resize)
    canvas.bind("<Configure>",resize)
    def wheel(event):
        if event.delta:canvas.yview_scroll(-int(event.delta/120)or(-1 if event.delta>0 else 1),"units")
    canvas.bind("<MouseWheel>",wheel)
    if accessible_scroll:
        # A per-pane bindtag reaches form backgrounds/labels/buttons without
        # stealing wheel input from editable controls, dropdowns or live logs.
        tag="CommanderControls"+str(id(canvas))
        def tag_children(widget):
            for child in widget.winfo_children():
                if child.winfo_class()not in {"TCombobox","TSpinbox","Spinbox","Text","Listbox","Treeview","TEntry","Entry","Scrollbar","TScrollbar"}:
                    if tag not in child.bindtags():child.bindtags((*child.bindtags()[:-1],tag,child.bindtags()[-1]))
                tag_children(child)
        def configure_controls(event=None):resize();tag_children(controls)
        controls.bind("<Configure>",configure_controls)
        def scroll_form(event):wheel(event);return "break"
        canvas.bind_class(tag,"<MouseWheel>",scroll_form)
        def clean(event):
            if event.widget is canvas:canvas.unbind_class(tag,"<MouseWheel>")
        canvas.bind("<Destroy>",clean)
        panes.controls_horizontal=horizontal
    def scroll_to(widget):
        controls.update_idletasks();resize()
        y=widget.winfo_rooty()-controls.winfo_rooty()
        height=max(1,controls.winfo_height())
        canvas.yview_moveto(max(0,y-8)/height);canvas.xview_moveto(0)
    panes.controls_canvas=canvas;panes.controls_scrollbar=bar;panes.scroll_to=scroll_to
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
