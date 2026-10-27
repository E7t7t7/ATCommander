"""A quiet dark palette and presentation-only labels for the desktop app."""
import re
from tkinter import ttk

BACKGROUND="#10151d"
SURFACE="#19212c"
TEXT="#edf2f7"
MUTED="#a0adbd"
ACCENT="#81d7c0"

def apply_style(root):
    root.configure(bg=BACKGROUND)
    style=ttk.Style(root)
    try:style.theme_use("clam")
    except Exception:pass
    style.configure(".",font=("Segoe UI",10))
    style.configure("TFrame",background=BACKGROUND)
    style.configure("Card.TFrame",background=SURFACE)
    style.configure("Nav.TFrame",background="#141c26")
    style.configure("TLabelframe",background=SURFACE,bordercolor="#303c4d")
    style.configure("TLabelframe.Label",background=SURFACE,foreground=MUTED)
    style.configure("TLabel",background=BACKGROUND,foreground=TEXT,font=("Segoe UI",10))
    style.configure("Card.TLabel",background=SURFACE,foreground=TEXT)
    style.configure("Muted.Card.TLabel",background=SURFACE,foreground=MUTED,font=("Segoe UI",9))
    style.configure("Big.Card.TLabel",background=SURFACE,foreground=TEXT,font=("Segoe UI Semibold",16))
    style.configure("Header.TLabel",background=BACKGROUND,foreground=TEXT,font=("Segoe UI Semibold",21))
    style.configure("Nav.TLabel",background="#141c26",foreground="#8da194",font=("Segoe UI Semibold",9))
    style.configure("TNotebook",background=BACKGROUND,borderwidth=0)
    style.configure("TNotebook.Tab",background=SURFACE,foreground=MUTED,padding=(13,8))
    style.map("TNotebook.Tab",background=[("selected","#2a423d")],foreground=[("selected",TEXT)])
    style.layout("Nav.TNotebook.Tab",[])
    style.configure("Treeview",background="#151d27",fieldbackground="#151d27",foreground="#dce5ef",rowheight=28,borderwidth=0)
    style.map("Treeview",background=[("selected","#284b45")],foreground=[("selected","#f4fbf8")])
    style.configure("Treeview.Heading",background="#24303e",foreground="#c8d4e2",font=("Segoe UI Semibold",9),padding=(8,8))
    style.configure("TButton",padding=(11,7),font=("Segoe UI Semibold",9),background="#283543",foreground=TEXT,borderwidth=0)
    style.map("TButton",background=[("active","#37495a"),("disabled","#202b37")],foreground=[("disabled","#788799")])
    style.configure("Accent.TButton",padding=(14,8),background="#315e54",foreground="#effff9")
    style.map("Accent.TButton",background=[("active","#40796b"),("disabled","#263b37")])
    style.configure("Danger.TButton",background="#52353c",foreground="#ffe5e9")
    style.map("Danger.TButton",background=[("active","#71434d")])
    style.configure("Nav.TButton",anchor="w",padding=(12,10),background="#141c26",foreground="#bac7d6",font=("Segoe UI",10))
    style.map("Nav.TButton",background=[("active","#233140")])
    style.configure("Nav.Active.TButton",anchor="w",padding=(12,10),background="#29463f",foreground="#b6f0da",font=("Segoe UI Semibold",10))
    style.map("Nav.Active.TButton",background=[("active","#345b50")])
    for name in ("TEntry","TCombobox","TSpinbox"):
        style.configure(name,fieldbackground="#121b26",background="#293747",foreground=TEXT,insertcolor=TEXT,arrowcolor=MUTED,bordercolor="#344358",padding=5)
    style.map("TCombobox",fieldbackground=[("readonly","#121b26")],foreground=[("readonly",TEXT)])
    style.configure("TCheckbutton",background=BACKGROUND,foreground="#c4d0dd",padding=(0,3))
    style.map("TCheckbutton",background=[("active",SURFACE)],foreground=[("active",TEXT)])
    root.option_add("*TCombobox*Listbox.background","#19232f")
    root.option_add("*TCombobox*Listbox.foreground",TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground","#315e54")

_LABELS={
    "RESEARCH EVERY OWNED COMBO":"Research your combos",
    "ADVENTURE ISLAND / STAGE FARM":"Adventure",
    "SWOLE TRAINING + FIGHTS":"Swole",
    "CHALLENGES / CLASH / MELEE":"Challenges, Clash & Melee",
    "CM REVIEWS":"Combo Mastery reviews",
    "AUTOMATION ROUTINES":"Your routines",
    "LEGITIMATE REWARDED ADS":"Ad rewards",
    "LEARNING BROWSER":"Game connection",
    "COMMAND CENTER":"Command center",
    "AUTO-COMPLETE RUMBLE":"Run Rumble",
    "ARM / AUTO-COMPLETE SIEGE":"Run Siege",
    "START ADVENTURE":"Start Adventure",
    "REFRESH LIVE CM DATA":"Refresh reviews",
}

def friendly_label(text):
    if text in _LABELS:return _LABELS[text]
    if not isinstance(text,str) or not text.isupper() or len(text)<3 or "\n" in text:return text
    value=text[:1]+text[1:].lower()
    return re.sub(r"\b(cm|api|xp|sfc|id|ui)\b",lambda match:match[0].upper(),value)

def soften_labels(widget):
    """Change widget captions only; never touch values, logs or API strings."""
    try:
        if widget.winfo_class() in {"TButton","TLabel","TLabelframe"}:
            text=widget.cget("text");label=friendly_label(text)
            if text!=label:widget.configure(text=label)
        elif widget.winfo_class()=="Listbox":
            widget.configure(bg="#151d27",fg=TEXT,selectbackground="#315e54")
        for child in widget.winfo_children():soften_labels(child)
    except Exception:pass
