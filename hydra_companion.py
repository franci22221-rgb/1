"""
Hydra Companion - Mod Profile Switcher  (Hydra-Launcher styled UI)
==================================================================
Single-file Python (tkinter) desktop app. Dark themed. 100% free, no license.

Required:  pip install requests
Optional:  pip install Pillow            (cover images)
           pip install tkinterdnd2       (drag-and-drop onto window)
           pip install pystray Pillow    (system-tray icon)

Run:       python hydra_companion.py
"""

import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import webbrowser
import tkinter as tk
import zipfile
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import quote

from hydra_core import (
    APP_DIR, CONFIG_PATH, DEFAULT_CONFIG, EXTRA_FOLDER_TEMPLATES,
    ICON_CACHE, LIB_CARD_SIZES, LOG_PATH, PROFILES_ROOT,
    apply_discovered_art, apply_load_order_prefix, backup_config_zip,
    detect_conflicts_v2, discover_game_art, dry_run_apply, export_mod_list,
    find_hydra_exe, folder_hash, guess_preset, human_size, ksp_dependency_hint,
    load_config, logger, profile_diff, profile_folder, profile_stats,
    real_mod_path, save_config, safe_name, setup_logging,
)
from hydra_sources import (
    TRUSTED_SOURCES, resolve_download, search_all,
)

try:
    import requests
except ImportError:
    print("Missing dependency. Please run:  pip install requests")
    sys.exit(1)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except Exception:
    HAS_DND = False

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except Exception:
    HAS_PIL = False

try:
    import pystray
    from pystray import Menu as TrayMenu, MenuItem as TrayItem
    HAS_TRAY = HAS_PIL
except Exception:
    HAS_TRAY = False


# Hydra-like palette
BG       = "#0f0f10"
SIDEBAR  = "#161618"
PANEL    = "#1a1a1d"
PANEL_2  = "#222226"
CARD_BG  = "#1c1c20"
ACCENT   = "#c89b3c"     # Hydra-ish gold
ACCENT_2 = "#e6b955"
HILITE   = "#2a2a2f"
TEXT     = "#f1f1f3"
MUTED    = "#9a9aa3"
BORDER   = "#2a2a2f"
SUCCESS  = "#5cd99a"
DANGER   = "#ef476f"

# Unified dark theme (replacing Thunderstore blue with consistent palette)
TS_HEADER  = ACCENT
TS_SIDEBAR = SIDEBAR
TS_MAIN    = BG
TS_LINK    = ACCENT_2
TS_BTN     = ACCENT

MODRINTH_API = "https://api.modrinth.com/v2"

# ---------- Widget helpers ----------
def style_button(btn, primary=False, danger=False, ghost=False):
    if primary:
        bg, fg, hov = ACCENT, "#1a1206", ACCENT_2
    elif danger:
        bg, fg, hov = DANGER, "#fff", "#ff6f8c"
    elif ghost:
        bg, fg, hov = SIDEBAR, TEXT, HILITE
    else:
        bg, fg, hov = PANEL_2, TEXT, HILITE
    btn.configure(bg=bg, fg=fg, activebackground=hov, activeforeground=fg,
                  relief="flat", bd=0, padx=14, pady=8, cursor="hand2",
                  font=("Segoe UI", 10, "bold" if primary else "normal"),
                  highlightthickness=0, highlightbackground=BORDER,
                  highlightcolor=ACCENT)
    btn.bind("<Enter>", lambda e: btn.configure(bg=hov, highlightbackground=ACCENT))
    btn.bind("<Leave>", lambda e: btn.configure(bg=bg, highlightbackground=BORDER))
    btn.bind("<Button-1>", lambda e: btn.configure(bg=HILITE))
    btn.bind("<ButtonRelease-1>", lambda e: btn.configure(bg=hov))


def style_entry(ent):
    ent.configure(bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat",
                  highlightthickness=1, highlightbackground=BORDER,
                  highlightcolor=ACCENT, font=("Segoe UI", 10))


def style_dialog(win):
    """Apply consistent dark theme to dialog windows."""
    win.configure(bg=PANEL)
    # Configure all child widgets to use dark theme
    def style_widget(widget):
        try:
            cls = widget.winfo_class()
            if cls == "Label":
                widget.configure(bg=PANEL, fg=TEXT)
            elif cls == "Button":
                style_button(widget)
            elif cls == "Entry":
                style_entry(widget)
            elif cls == "Frame":
                widget.configure(bg=PANEL)
            elif cls == "Toplevel":
                widget.configure(bg=PANEL)
            # Recursively style children
            for child in widget.winfo_children():
                style_widget(child)
        except Exception:
            pass
    style_widget(win)


class ToolTip:
    """Simple tooltip widget for dark theme."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        widget.bind("<Enter>", self.show_tip)
        widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        if self.tip_window or not self.text:
            return
        x, y, _, _ = self.widget.bbox("insert")
        x = x + self.widget.winfo_rootx() + 25
        y = y + self.widget.winfo_rooty() + 25
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=self.text, bg=PANEL_2, fg=TEXT,
                        font=("Segoe UI", 9), padx=8, pady=4,
                        relief="solid", borderwidth=1)
        label.pack()

    def hide_tip(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


BaseTk = TkinterDnD.Tk if HAS_DND else tk.Tk


# ---------- Main App ----------
class HydraCompanion(BaseTk):
    PAGES = ("home", "catalogue", "library", "downloads", "settings",
             "game_profiles", "game_hub", "game_detail")

    def __init__(self):
        super().__init__()
        self.title("Hydra Companion")
        self.geometry("1280x820")
        self.minsize(1100, 720)
        self.configure(bg=BG)

        setup_logging()
        self.cfg = load_config()
        self._catalogue_query_var = tk.StringVar()
        self._cover_imgs = {}     # game -> (size, PhotoImage)
        self._tile_letters = {}   # game -> PhotoImage placeholder
        self._search_results = []
        self._download_log = []
        self.current_page = "library"
        self.detail_game = None
        self.detail_profile = None
        self._hub_tab = tk.StringVar(value="installed")
        self._hub_online_query = tk.StringVar()
        self._hub_search_debounce = None
        self._sidebar_btns = {}
        self._tray = None
        self.dl_progress = None
        self._search_debounce_id = None
        self._nav_back_stack = []     # history for back navigation
        self._nav_forward_stack = []  # history for forward navigation
        self._nav_current = None      # current page state
        self._nav_navigating = False  # prevent re-entry while navigating
        self._refresh_all_game_art(save=False)

        self._build_styles()
        self._build_layout()
        self.show_page("library")

        self.bind_all("<Control-Return>", lambda e: self._quick_apply())
        # Back/forward with Alt+Left/Right (works everywhere)
        self.bind_all("<Alt-Left>", lambda e: self._nav_go_back())
        self.bind_all("<Alt-Right>", lambda e: self._nav_go_forward())
        # Mouse side buttons: on Windows Button-4/5 are XButton1/XButton2,
        # on Linux (X11) they are Button-8/9.
        if sys.platform == "win32":
            for btn in ("<Button-4>",):
                try:
                    self.bind_all(btn, lambda e: self._nav_go_back())
                except Exception:
                    pass
            for btn in ("<Button-5>",):
                try:
                    self.bind_all(btn, lambda e: self._nav_go_forward())
                except Exception:
                    pass
        else:
            for btn in ("<Button-8>",):
                try:
                    self.bind_all(btn, lambda e: self._nav_go_back())
                except Exception:
                    pass
            for btn in ("<Button-9>",):
                try:
                    self.bind_all(btn, lambda e: self._nav_go_forward())
                except Exception:
                    pass

        if HAS_DND:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)

        if HAS_TRAY:
            self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        else:
            self.protocol("WM_DELETE_WINDOW", self.destroy)

        if not self.cfg.get("first_run_complete"):
            self.after(300, self._first_run_wizard)
        elif not self.cfg.get("hydra_launcher_path"):
            found = find_hydra_exe()
            if found:
                self.cfg["hydra_launcher_path"] = found
                save_config(self.cfg)

    # ===== Styles =====
    def _build_styles(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure("TCombobox", fieldbackground=PANEL_2, background=PANEL_2,
                    foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER)
        self.option_add("*TCombobox*Listbox*Background", PANEL_2)
        self.option_add("*TCombobox*Listbox*Foreground", TEXT)
        self.option_add("*TCombobox*Listbox*selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox*selectForeground", "#1a1206")
        s.configure("Dark.Treeview", background=PANEL_2,
                    fieldbackground=PANEL_2, foreground=TEXT, rowheight=28,
                    borderwidth=0, font=("Segoe UI", 10))
        s.configure("Dark.Treeview.Heading", background=PANEL,
                    foreground=ACCENT_2, font=("Segoe UI", 10, "bold"),
                    borderwidth=0)
        s.map("Dark.Treeview", background=[("selected", ACCENT)],
              foreground=[("selected", "#1a1206")])
        s.configure("Dark.Horizontal.TProgressbar",
                    background=ACCENT, troughcolor=PANEL_2, borderwidth=0)
        s.configure("Vertical.TScrollbar", background=PANEL_2,
                    troughcolor=SIDEBAR, bordercolor=BORDER,
                    arrowcolor=MUTED, lightcolor=PANEL_2,
                    darkcolor=PANEL_2, gripcount=0, thickness=12)
        s.map("Vertical.TScrollbar",
              background=[("active", ACCENT), ("!active", PANEL_2)],
              arrowcolor=[("active", "#1a1206"), ("!active", MUTED)])
        s.configure("TCheckbutton", background=PANEL, foreground=TEXT,
                    focuscolor=PANEL)
        s.map("TCheckbutton", background=[("active", PANEL)],
              foreground=[("active", TEXT)])

    # ===== Layout =====
    def _build_layout(self):
        # Root: sidebar + main
        self.sidebar = tk.Frame(self, bg=SIDEBAR, width=240)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.main = tk.Frame(self, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)

        self._build_sidebar()
        self._build_header()

        self.content = tk.Frame(self.main, bg=BG)
        self.content.pack(fill="both", expand=True, padx=28, pady=(8, 16))

        # Status bar
        self.status_var = tk.StringVar(value="Ready.")
        tk.Label(self, textvariable=self.status_var, bg=SIDEBAR,
                 fg=MUTED, anchor="w", padx=18, pady=6,
                 font=("Segoe UI", 9)).pack(fill="x", side="bottom")

    def _build_sidebar(self):
        # Profile pill
        p = tk.Frame(self.sidebar, bg=SIDEBAR)
        p.pack(fill="x", padx=12, pady=(16, 18))
        tile = tk.Label(p, text="HC", bg=ACCENT, fg="#1a1206", width=3,
                        font=("Segoe UI", 12, "bold"), padx=8, pady=4)
        tile.pack(side="left")
        tk.Label(p, text="hydra_companion", bg=SIDEBAR, fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(side="left",
                                                    padx=(10, 0))

        # Nav buttons
        for key, label, tip in [("home", "  Home", "Quick actions and recent activity"),
                                 ("catalogue", "  Catalogue", "Search mods from trusted sources"),
                                 ("library", "  Library", "Manage your games and profiles"),
                                 ("downloads", "  Downloads", "View download history"),
                                 ("settings", "  Settings", "Configure API keys and preferences")]:
            b = tk.Button(self.sidebar, text=label, anchor="w",
                          command=lambda k=key: self.show_page(k))
            b.configure(bg=SIDEBAR, fg=TEXT, activebackground=HILITE,
                        activeforeground=TEXT, relief="flat", bd=0,
                        padx=18, pady=10, cursor="hand2",
                        font=("Segoe UI", 11), highlightthickness=0,
                        anchor="w")
            b.pack(fill="x", padx=8, pady=1)
            ToolTip(b, tip)
            self._sidebar_btns[key] = b

        # GAMES section
        tk.Label(self.sidebar, text="GAMES", bg=SIDEBAR, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w",
                                                   padx=20, pady=(18, 4))
        add = tk.Button(self.sidebar, text="  + Add Game",
                        command=lambda: self._open_game_form(None))
        style_button(add, ghost=True)
        add.configure(anchor="w")
        add.pack(fill="x", padx=8, pady=(0, 6))

        filt = tk.Entry(self.sidebar)
        style_entry(filt)
        filt.pack(fill="x", padx=12, pady=(0, 8))
        filt.insert(0, "")
        self._game_filter = filt
        filt.bind("<KeyRelease>", lambda e: self._refresh_sidebar_games())

        self._sidebar_games = tk.Frame(self.sidebar, bg=SIDEBAR)
        self._sidebar_games.pack(fill="both", expand=True, padx=4)
        self._refresh_sidebar_games()

    def _refresh_sidebar_games(self):
        for w in self._sidebar_games.winfo_children():
            w.destroy()
        q = self._game_filter.get().strip().lower()
        for name in self.cfg["games"]:
            if q and q not in name.lower():
                continue
            b = tk.Button(self._sidebar_games, text=f"  • {name}",
                          anchor="w",
                          command=lambda n=name: self.show_page(
                              "game_profiles", game=n))
            b.configure(bg=SIDEBAR, fg=MUTED, activebackground=HILITE,
                        activeforeground=TEXT, relief="flat", bd=0,
                        padx=18, pady=4, cursor="hand2",
                        font=("Segoe UI", 10), highlightthickness=0,
                        anchor="w")
            b.pack(fill="x", pady=1)

    def _build_header(self):
        self.header = tk.Frame(self.main, bg=BG, padx=28, pady=18)
        self.header.pack(fill="x")
        self.back_btn = tk.Button(self.header, text="←",
                                  command=lambda: self.show_page("library"))
        style_button(self.back_btn, ghost=True)
        # back_btn shown only on detail page
        self.page_title = tk.Label(self.header, text="Library", bg=BG,
                                   fg=TEXT, font=("Segoe UI", 22, "bold"))
        self.page_title.pack(side="left")
        # Right side: search
        right = tk.Frame(self.header, bg=BG)
        right.pack(side="right")
        self.header_search = tk.Entry(right, width=34)
        style_entry(self.header_search)
        self.header_search.pack(side="right", ipady=4)
        self.header_search.bind("<Return>", lambda e: self._header_search())
        tk.Label(right, text=" 🔎 ", bg=BG, fg=MUTED,
                 font=("Segoe UI", 11)).pack(side="right")

    # ===== Navigation history =====
    def _nav_go_back(self):
        if not self._nav_back_stack:
            return
        entry = self._nav_back_stack.pop()
        if self._nav_current:
            self._nav_forward_stack.append(self._nav_current)
        self._nav_navigating = True
        self.show_page(entry["page"], **entry.get("kw", {}))
        self._nav_navigating = False

    def _nav_go_forward(self):
        if not self._nav_forward_stack:
            return
        entry = self._nav_forward_stack.pop()
        if self._nav_current:
            self._nav_back_stack.append(self._nav_current)
        self._nav_navigating = True
        self.show_page(entry["page"], **entry.get("kw", {}))
        self._nav_navigating = False

    # ===== Page dispatcher =====
    def show_page(self, page, **kw):
        if page == "game_detail":
            page = "game_profiles"
        if page in ("game_profiles", "game_hub"):
            self.detail_game = kw.get("game") or self.detail_game
            if not self.detail_game:
                page = "library"
            if page == "game_hub":
                self.detail_profile = (kw.get("profile") or self.detail_profile
                                       or self._default_profile())
        # Track navigation history for back/forward mouse buttons
        nav_entry = {"page": page}
        nav_kw = {}
        if page in ("game_profiles", "game_hub") and self.detail_game:
            nav_kw["game"] = self.detail_game
        if page == "game_hub" and self.detail_profile:
            nav_kw["profile"] = self.detail_profile
        if nav_kw:
            nav_entry["kw"] = nav_kw
        if not self._nav_navigating:
            if self._nav_current:
                self._nav_back_stack.append(self._nav_current)
                if len(self._nav_back_stack) > 50:
                    self._nav_back_stack = self._nav_back_stack[-50:]
            self._nav_forward_stack.clear()
        self._nav_current = nav_entry

        self.current_page = page
        immersive = page in ("game_profiles", "game_hub")
        if immersive:
            self.sidebar.pack_forget()
            self.header.pack_forget()
            self.content.configure(bg=TS_MAIN)
            self.main.configure(bg=TS_MAIN)
        else:
            self.content.configure(bg=BG)
            self.main.configure(bg=BG)
            if not self.sidebar.winfo_ismapped():
                self.sidebar.pack(side="left", fill="y")
            if not self.header.winfo_ismapped():
                self.header.pack(fill="x")
        for k, b in self._sidebar_btns.items():
            b.configure(bg=HILITE if k == page else SIDEBAR,
                        fg=ACCENT if k == page else TEXT)
        self.back_btn.pack_forget()
        if not immersive:
            titles = {"home": "Home", "catalogue": "Catalogue",
                      "library": "Library", "downloads": "Downloads",
                      "settings": "Settings"}
            self.page_title.configure(text=titles.get(page, ""))
        if page in ("library", "catalogue"):
            self.header_search.delete(0, "end")

        if self._search_debounce_id:
            try:
                self.after_cancel(self._search_debounce_id)
            except Exception:
                pass
            self._search_debounce_id = None

        for w in self.content.winfo_children():
            w.destroy()
        getattr(self, f"_page_{page}")()

        if page == "catalogue" and self._catalogue_query_var.get().strip():
            self.after(100, self._do_catalogue_search)

    def _header_search(self):
        q = self.header_search.get().strip()
        if not q:
            return
        if self.current_page == "library":
            self._refresh_library(q)
        else:
            if self.current_page != "catalogue":
                self.show_page("catalogue")
            self._catalogue_query_var.set(q)
            self._do_catalogue_search()

    # ===== Scrollable container helper =====
    def _scrollable(self, parent, bg=None):
        bg = bg or BG
        wrap = tk.Frame(parent, bg=bg)
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg=bg, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview,
                           style="Vertical.TScrollbar")
        inner = tk.Frame(canvas, bg=bg)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def on_cfg(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(win, width=max(canvas.winfo_width(), 1))

        def _wheel(e):
            canvas.yview_scroll(int(-e.delta / 120), "units")

        inner.bind("<Configure>", on_cfg)
        canvas.bind("<Configure>", on_cfg)
        canvas.bind("<Enter>", lambda _e: canvas.bind("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind("<MouseWheel>"))
        return inner

    def _library_card_preset(self):
        label = self.cfg.get("library_card_size", "Normal")
        if hasattr(self, "_lib_size"):
            try:
                label = self._lib_size.get() or label
            except tk.TclError:
                pass
        return LIB_CARD_SIZES.get(label, LIB_CARD_SIZES["Normal"])

    def _on_lib_card_size(self, _e=None):
        if hasattr(self, "_lib_size"):
            self.cfg["library_card_size"] = self._lib_size.get()
            save_config(self.cfg)
        self._cover_imgs.clear()
        self._refresh_library()

    def _schedule_catalogue_search(self, event=None):
        if event and event.keysym in ("Up", "Down", "Left", "Right",
                                     "Shift_L", "Shift_R", "Control_L",
                                     "Control_R", "Alt_L", "Alt_R"):
            return
        if self._search_debounce_id:
            try:
                self.after_cancel(self._search_debounce_id)
            except Exception:
                pass
        self._search_debounce_id = self.after(450, self._do_catalogue_search)

    def _catalogue_sources(self):
        return ["All sources"] + list(TRUSTED_SOURCES)

    def _active_nexus_domain(self):
        tg = getattr(self, "_cat_target_game", None)
        if tg is not None:
            name = tg.get()
            if name and name in self.cfg["games"]:
                return self.cfg["games"][name].get("nexus_domain", "").strip()
        ag = self.cfg.get("active_game")
        if ag and ag in self.cfg["games"]:
            return self.cfg["games"][ag].get("nexus_domain", "").strip()
        return ""

    # ===================================================================
    # PAGE: HOME
    # ===================================================================
    def _page_home(self):
        c = self.content
        tk.Label(c, text="Welcome back", bg=BG, fg=MUTED,
                 font=("Segoe UI", 11)).pack(anchor="w")
        tk.Label(c, text="Quick actions", bg=BG, fg=TEXT,
                 font=("Segoe UI", 18, "bold")).pack(anchor="w",
                                                    pady=(0, 12))
        grid = tk.Frame(c, bg=BG)
        grid.pack(fill="x", pady=8)
        for i, (lbl, cmd) in enumerate([
            ("Open Library", lambda: self.show_page("library")),
            ("Search Catalogue", lambda: self.show_page("catalogue")),
            ("Open Settings", lambda: self.show_page("settings")),
            ("Launch Hydra", self.launch_hydra),
        ]):
            b = tk.Button(grid, text=lbl, command=cmd, width=22)
            style_button(b, primary=(i == 0))
            b.grid(row=0, column=i, padx=(0, 12), pady=4, ipady=8)

        # Recently applied
        tk.Label(c, text="\nRecently applied profiles", bg=BG, fg=TEXT,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w",
                                                    pady=(20, 6))
        for name, g in list(self.cfg["games"].items())[:6]:
            row = tk.Frame(c, bg=PANEL)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=name, bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 11, "bold"),
                     padx=14, pady=10).pack(side="left")
            ap = g.get("applied_profile") or "—"
            tk.Label(row, text=f"  {ap}", bg=PANEL, fg=ACCENT_2,
                     font=("Segoe UI", 10)).pack(side="left")
            b = tk.Button(row, text="Open",
                          command=lambda n=name:
                          self.show_page("game_profiles", game=n))
            style_button(b, ghost=True)
            b.pack(side="right", padx=10, pady=6)

    # ===================================================================
    # PAGE: LIBRARY  (big game cover cards in a grid)
    # ===================================================================
    def _page_library(self):
        # Top controls row
        top = tk.Frame(self.content, bg=BG)
        top.pack(fill="x", pady=(0, 12))
        tk.Label(top, text="Sort by", bg=BG, fg=MUTED,
                 font=("Segoe UI", 10)).pack(side="left")
        self._lib_sort = ttk.Combobox(top, state="readonly", width=14,
                                      values=["Most recent", "A → Z",
                                              "Favourites first"])
        self._lib_sort.set("Most recent")
        self._lib_sort.pack(side="left", padx=8)
        self._lib_sort.bind("<<ComboboxSelected>>",
                            lambda e: self._refresh_library())

        tk.Label(top, text="Card size", bg=BG, fg=MUTED,
                 font=("Segoe UI", 10)).pack(side="left", padx=(16, 0))
        self._lib_size = ttk.Combobox(
            top, state="readonly", width=12,
            values=list(LIB_CARD_SIZES.keys()))
        self._lib_size.set(self.cfg.get("library_card_size", "Normal"))
        self._lib_size.pack(side="left", padx=8)
        self._lib_size.bind("<<ComboboxSelected>>", self._on_lib_card_size)

        fav_pill = tk.Frame(top, bg=PANEL_2)
        fav_pill.pack(side="left", padx=14)
        n_fav = sum(1 for g in self.cfg["games"].values() if g.get("favorite"))
        tk.Label(fav_pill, text="♥ Favourites", bg=PANEL_2, fg=TEXT,
                 padx=12, pady=6,
                 font=("Segoe UI", 10)).pack(side="left")
        tk.Label(fav_pill, text=f" {n_fav}", bg=PANEL_2, fg=ACCENT,
                 padx=10, pady=6,
                 font=("Segoe UI", 10, "bold")).pack(side="left")

        addb = tk.Button(top, text="+ Add Game",
                         command=lambda: self._open_game_form(None))
        style_button(addb, primary=True)
        addb.pack(side="right")

        # Scrollable grid
        self._lib_grid_holder = self._scrollable(self.content)
        self._refresh_library()

    def _refresh_library(self, query=""):
        if not hasattr(self, "_lib_grid_holder"):
            return
        for w in self._lib_grid_holder.winfo_children():
            w.destroy()
        games = list(self.cfg["games"].items())
        if query:
            q = query.lower()
            games = [(n, g) for n, g in games if q in n.lower()]
        sort = self._lib_sort.get() if hasattr(self, "_lib_sort") else "A → Z"
        if sort == "A → Z":
            games.sort(key=lambda kv: kv[0].lower())
        elif sort == "Favourites first":
            games.sort(key=lambda kv: (not kv[1].get("favorite"),
                                       kv[0].lower()))
        if not games:
            tk.Label(self._lib_grid_holder,
                     text="No games yet. Click + Add Game to begin.",
                     bg=BG, fg=MUTED,
                     font=("Segoe UI", 12)).pack(pady=40)
            return

        card_w, card_h, cols = self._library_card_preset()
        try:
            avail = max(self._lib_grid_holder.winfo_width(), 800)
            cols = max(2, min(cols, avail // max(card_w + 16, 1)))
        except tk.TclError:
            pass
        wrap = tk.Frame(self._lib_grid_holder, bg=BG)
        wrap.pack(fill="x", padx=2, pady=4)
        for i, (name, g) in enumerate(games):
            r, c = divmod(i, cols)
            card = self._build_game_card(wrap, name, g, card_w, card_h)
            card.grid(row=r, column=c, padx=8, pady=8, sticky="nw")

    def _build_game_card(self, parent, name, g, w, h):
        foot_h = 36
        cover_h = max(h - foot_h, 80)
        card = tk.Frame(parent, bg=CARD_BG, bd=0, highlightthickness=1,
                        highlightbackground=BORDER, width=w, height=h)
        card.pack_propagate(False)
        card.configure(cursor="hand2")

        cover = tk.Frame(card, bg=PANEL_2, width=w, height=cover_h)
        cover.pack()
        cover.pack_propagate(False)

        img = self._cover_image(name, g, w, cover_h, banner=False)
        if img is not None:
            lbl = tk.Label(cover, image=img, bg=PANEL_2, bd=0)
            lbl.image = img
            lbl.pack(expand=True)
        else:
            letter = (name[:1] or "?").upper()
            fsize = max(18, min(52, w // 3))
            tk.Label(cover, text=letter, bg=PANEL_2, fg=ACCENT,
                     font=("Segoe UI", fsize, "bold")).pack(expand=True)

        foot = tk.Frame(card, bg=CARD_BG, height=foot_h)
        foot.pack(fill="x")
        foot.pack_propagate(False)
        tk.Label(foot, text=name, bg=CARD_BG, fg=TEXT,
                 font=("Segoe UI", 10, "bold"),
                 padx=10, pady=8, anchor="w").pack(side="left", fill="x")

        ap = g.get("applied_profile")
        if ap:
            pill = tk.Label(cover, text=f" ● {ap[:14]} ",
                            bg="#1a1a1d", fg=ACCENT,
                            font=("Segoe UI", 8, "bold"))
            pill.place(x=6, y=6)

        fav = "♥" if g.get("favorite") else "♡"
        fb = tk.Button(cover, text=fav,
                       command=lambda n=name: self._toggle_fav(n))
        fb.configure(bg="#1a1a1d", fg=ACCENT, relief="flat", bd=0,
                     padx=4, pady=0, cursor="hand2",
                     activebackground="#1a1a1d",
                     font=("Segoe UI", 10, "bold"))
        fb.place(relx=1.0, x=-6, y=6, anchor="ne")

        def open_(_e=None):
            self.show_page("game_profiles", game=name)

        def bind_click(widget):
            widget.bind("<Button-1>", open_)
            for child in widget.winfo_children():
                if not isinstance(child, tk.Button):
                    bind_click(child)

        def show_context_menu(event):
            menu = tk.Menu(self, tearoff=0, bg=PANEL_2, fg=TEXT,
                          activebackground=ACCENT, activeforeground="#1a1206",
                          font=("Segoe UI", 10))
            menu.add_command(label=f"Open {name}", command=lambda: self.show_page("game_profiles", game=name))
            menu.add_separator()
            profiles = g.get("profiles", ["Vanilla"])
            for prof in profiles:
                if prof.startswith("_AutoBackup_"):
                    continue
                menu.add_command(label=f"Apply: {prof}",
                              command=lambda p=prof: self._quick_apply_profile(name, p))
            menu.post(event.x_root, event.y_root)

        card.bind("<Button-3>", show_context_menu)
        bind_click(card)
        return card

    def _refresh_all_game_art(self, save=True):
        changed = False
        for name, g in self.cfg.get("games", {}).items():
            if not g.get("game_folder"):
                continue
            before = (g.get("cover_path"), g.get("banner_path"))
            apply_discovered_art(g, name)
            if (g.get("cover_path"), g.get("banner_path")) != before:
                changed = True
        if changed and save:
            save_config(self.cfg)
        if changed and hasattr(self, "_cover_imgs"):
            self._cover_imgs.clear()

    def _cover_image(self, name, game, w, h, banner=False):
        if not HAS_PIL:
            return None
        if isinstance(game, str):
            game = {"icon_url": game, "cover_path": "", "banner_path": ""}
        path = ""
        if banner:
            path = game.get("banner_path") or game.get("cover_path") or ""
        else:
            path = game.get("cover_path") or game.get("banner_path") or ""
        url = (game.get("icon_url") or "").strip()
        key = (name, w, h, path or url, banner)
        if key in self._cover_imgs:
            return self._cover_imgs[key]
        try:
            if path and Path(path).is_file():
                im = Image.open(path).convert("RGB")
            elif url:
                ext = Path(url.split("?")[0]).suffix or ".img"
                cache = ICON_CACHE / (safe_name(name) + ext)
                if not cache.exists():
                    r = requests.get(url, timeout=10)
                    r.raise_for_status()
                    cache.write_bytes(r.content)
                im = Image.open(cache).convert("RGB")
            else:
                return None
            im = im.resize((max(w, 1), max(h, 1)), Image.LANCZOS)
            ph = ImageTk.PhotoImage(im)
            self._cover_imgs[key] = ph
            return ph
        except Exception as exc:
            logger.debug("Cover load failed for %s: %s", name, exc)
            return None

    def _toggle_fav(self, name):
        g = self.cfg["games"].get(name)
        if not g:
            return
        g["favorite"] = not g.get("favorite", False)
        save_config(self.cfg)
        self._refresh_library()

    def _quick_apply_profile(self, game_name, profile_name):
        """Quickly apply a profile from the library right-click menu."""
        self.detail_game = game_name
        self.detail_profile = profile_name
        self.cfg["active_game"] = game_name
        self.cfg["active_profile"] = profile_name
        save_config(self.cfg)
        self.apply_profile(confirm=True)

    def _is_mod_favorite(self, mod_key):
        """Check if a mod is marked as favorite."""
        fav_mods = self.cfg.get("favorite_mods", [])
        return mod_key in fav_mods

    def _toggle_mod_favorite(self, mod_key, row_widget):
        """Toggle favorite status of a mod."""
        fav_mods = self.cfg.get("favorite_mods", [])
        if mod_key in fav_mods:
            fav_mods.remove(mod_key)
        else:
            fav_mods.append(mod_key)
        self.cfg["favorite_mods"] = fav_mods
        save_config(self.cfg)
        # Refresh the view to show updated favorite status
        self._hub_build_installed()

    # ===================================================================
    # PAGE: CATALOGUE (mod search)
    # ===================================================================
    def _page_catalogue(self):
        wrap = tk.Frame(self.content, bg=BG)
        wrap.pack(fill="both", expand=True)

        body = tk.Frame(wrap, bg=BG)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, minsize=300, weight=0)
        body.grid_rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="nsew")

        right = tk.Frame(body, bg=PANEL, width=300,
                         highlightbackground=BORDER, highlightthickness=1)
        right.grid(row=0, column=1, sticky="ns")
        right.grid_propagate(False)

        srow = tk.Frame(left, bg=BG)
        srow.pack(fill="x", pady=(0, 12))
        self._catalogue_query = tk.Entry(
            srow, textvariable=self._catalogue_query_var)
        style_entry(self._catalogue_query)
        self._catalogue_query.pack(side="left", fill="x", expand=True,
                                   ipady=4)
        self._catalogue_query.bind(
            "<Return>", lambda e: self._do_catalogue_search())
        self._catalogue_query_var.trace_add(
            "write", lambda *_a: self._schedule_catalogue_search())
        
        # Search history dropdown
        hist_btn = tk.Button(srow, text="▼", command=self._show_search_history,
                           font=("Segoe UI", 8))
        hist_btn.configure(bg=PANEL_2, fg=MUTED, relief="flat", bd=0,
                          padx=6, pady=4, cursor="hand2")
        hist_btn.pack(side="left", padx=(4, 0))
        ToolTip(hist_btn, "Recent searches")
        
        go = tk.Button(srow, text="Search",
                       command=self._do_catalogue_search)
        style_button(go, primary=True)
        go.pack(side="left", padx=(8, 0))

        tk.Label(left,
                 text="Type to search Modrinth, CurseForge & Nexus (auto). "
                      "Use the sidebar to pick source / game →",
                 bg=BG, fg=MUTED,
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 6))
        self._cat_summary = tk.Label(
            left, text="Type 2+ characters — searches all trusted sources.",
            bg=BG, fg=ACCENT_2, font=("Segoe UI", 10, "bold"))
        self._cat_summary.pack(anchor="w", pady=(0, 8))

        self._catalogue_results = self._scrollable(left)

        side = tk.Frame(right, bg=PANEL)
        side.pack(fill="both", expand=True)

        tk.Label(side, text="SOURCES & FILTERS", bg=PANEL, fg=ACCENT_2,
                 font=("Segoe UI", 11, "bold")).pack(
                     anchor="w", padx=16, pady=(14, 8))

        tk.Label(side, text="Sort by", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16)
        self._cat_sort_var = tk.StringVar(value="Relevance")
        sort_row = tk.Frame(side, bg=PANEL)
        sort_row.pack(fill="x", padx=12, pady=(4, 10))
        for s in ("Relevance", "Downloads", "Newest"):
            tk.Radiobutton(
                sort_row, text=s, variable=self._cat_sort_var, value=s,
                bg=PANEL, fg=TEXT, selectcolor=PANEL_2,
                activebackground=PANEL, activeforeground=ACCENT,
                font=("Segoe UI", 9),
                command=self._do_catalogue_search,
            ).pack(anchor="w")

        tk.Label(side, text="Search source", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16,
                                                    pady=(6, 4))
        self._cat_source = tk.StringVar(
            value=self.cfg.get("catalogue_source", "All sources"))
        src_box = tk.Frame(side, bg=PANEL_2, highlightbackground=BORDER,
                           highlightthickness=1)
        src_box.pack(fill="x", padx=12, pady=(0, 8))
        for src in self._catalogue_sources():
            tk.Radiobutton(
                src_box, text=src, variable=self._cat_source, value=src,
                bg=PANEL_2, fg=TEXT, selectcolor=HILITE,
                activebackground=PANEL_2, activeforeground=ACCENT,
                anchor="w", padx=8, pady=2,
                font=("Segoe UI", 10),
                command=self._do_catalogue_search,
            ).pack(fill="x")

        self._cat_official = tk.BooleanVar(
            value=self.cfg.get("official_sources_only", True))
        tk.Checkbutton(
            side, text="Trusted sources only",
            variable=self._cat_official, bg=PANEL, fg=TEXT,
            selectcolor=PANEL_2, activebackground=PANEL,
            activeforeground=TEXT, font=("Segoe UI", 9),
            command=self._do_catalogue_search,
        ).pack(anchor="w", padx=16, pady=(0, 10))

        tk.Label(side, text="Game ID (CurseForge #)", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16)
        self._cat_gid = tk.Entry(side)
        style_entry(self._cat_gid)
        ag = self.cfg.get("active_game")
        if ag and ag in self.cfg["games"]:
            self._cat_gid.insert(0, self.cfg["games"][ag].get("game_id", ""))
        self._cat_gid.pack(fill="x", padx=12, pady=(4, 8))

        tk.Label(side, text="Nexus domain", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16)
        self._cat_nexus = tk.Entry(side)
        style_entry(self._cat_nexus)
        nd = self._active_nexus_domain()
        if nd:
            self._cat_nexus.insert(0, nd)
        self._cat_nexus.pack(fill="x", padx=12, pady=(4, 10))

        tk.Label(side, text="Download into", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16)
        games = list(self.cfg["games"].keys())
        self._cat_target_game = tk.StringVar(
            value=ag if ag in games else (games[0] if games else ""))
        g_row = tk.Frame(side, bg=PANEL)
        g_row.pack(fill="x", padx=12, pady=(4, 4))
        if games:
            om = tk.OptionMenu(g_row, self._cat_target_game, *games,
                               command=lambda *_a: self._cat_refresh_profile())
            om.configure(bg=PANEL_2, fg=TEXT, activebackground=HILITE,
                         activeforeground=TEXT, relief="flat",
                         highlightthickness=0, width=28)
            om["menu"].configure(bg=PANEL_2, fg=TEXT)
            om.pack(fill="x")
        else:
            tk.Label(g_row, text="(add a game first)", bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w")

        self._cat_target_prof = tk.StringVar(value="Vanilla")
        p_row = tk.Frame(side, bg=PANEL)
        p_row.pack(fill="x", padx=12, pady=(0, 10))
        self._cat_prof_menu = tk.OptionMenu(p_row, self._cat_target_prof,
                                            "Vanilla")
        self._cat_prof_menu.configure(bg=PANEL_2, fg=TEXT,
                                      activebackground=HILITE,
                                      activeforeground=TEXT, relief="flat",
                                      highlightthickness=0, width=28)
        self._cat_prof_menu["menu"].configure(bg=PANEL_2, fg=TEXT)
        self._cat_prof_menu.pack(fill="x")
        self._cat_refresh_profile()

        self.dl_progress = ttk.Progressbar(
            side, style="Dark.Horizontal.TProgressbar",
            mode="determinate", length=240)
        self.dl_progress.pack(fill="x", padx=12, pady=(8, 6))
        self._cat_status = tk.Label(side, text="Pick a source, then search.",
                                    bg=PANEL, fg=MUTED, wraplength=260,
                                    justify="left", padx=16,
                                    font=("Segoe UI", 9))
        self._cat_status.pack(anchor="w", pady=(0, 12))

    def _cat_refresh_profile(self, _e=None):
        g = self._cat_target_game.get()
        if g and g in self.cfg["games"]:
            profs = self.cfg["games"][g]["profiles"]
            if hasattr(self, "_cat_prof_menu"):
                menu = self._cat_prof_menu["menu"]
                menu.delete(0, "end")
                for p in profs:
                    menu.add_command(
                        label=p,
                        command=lambda v=p: self._cat_target_prof.set(v))
            if profs:
                self._cat_target_prof.set(profs[0])
            self.cfg["active_game"] = g
            gid = self.cfg["games"][g].get("game_id", "")
            if hasattr(self, "_cat_gid"):
                self._cat_gid.delete(0, "end")
                if gid:
                    self._cat_gid.insert(0, gid)
        self._cat_fill_nexus_domain()

    def _cat_fill_nexus_domain(self):
        if not hasattr(self, "_cat_nexus"):
            return
        dom = self._active_nexus_domain()
        self._cat_nexus.delete(0, "end")
        if dom:
            self._cat_nexus.insert(0, dom)

    def _do_catalogue_search(self):
        if self.current_page != "catalogue":
            return
        self._search_debounce_id = None
        q = self._catalogue_query_var.get().strip()
        if len(q) < 2:
            if hasattr(self, "_cat_summary"):
                self._cat_summary.configure(
                    text="Type at least 2 characters to search…")
            return
        # Save to search history
        self._save_search_history(q)
        src = self._cat_source.get() if hasattr(self, "_cat_source") else "All sources"
        gid = self._cat_gid.get().strip() if hasattr(self, "_cat_gid") else ""
        nexus_dom = self._cat_nexus.get().strip() if hasattr(self, "_cat_nexus") else ""
        game_name = self._cat_target_game.get() if hasattr(self, "_cat_target_game") else ""
        self.cfg["catalogue_source"] = src
        self.cfg["official_sources_only"] = bool(self._cat_official.get())
        save_config(self.cfg)
        self._cat_summary.configure(text=f"Searching '{q}' on {src}…")
        if hasattr(self, "_catalogue_results"):
            for w in self._catalogue_results.winfo_children():
                w.destroy()
        threading.Thread(target=self._catalogue_run,
                         args=(src, q, gid, nexus_dom, game_name),
                         daemon=True).start()

    def _save_search_history(self, query):
        """Save search query to history (max 10)."""
        history = self.cfg.get("search_history", [])
        if query in history:
            history.remove(query)
        history.insert(0, query)
        self.cfg["search_history"] = history[:10]
        save_config(self.cfg)

    def _show_search_history(self):
        """Show dropdown with recent searches."""
        history = self.cfg.get("search_history", [])
        if not history:
            return
        
        x = self._catalogue_query.winfo_rootx()
        y = self._catalogue_query.winfo_rooty() + self._catalogue_query.winfo_height()
        
        menu = tk.Menu(self, tearoff=0, bg=PANEL_2, fg=TEXT,
                      activebackground=ACCENT, activeforeground="#1a1206",
                      font=("Segoe UI", 10))
        for h in history:
            menu.add_command(label=h, command=lambda q=h: self._use_search_history(q))
        menu.post(x, y)
        
        # Close menu when clicking elsewhere
        def close_menu(e):
            menu.unpost()
        self.bind("<Button-1>", close_menu, add="+")

    def _use_search_history(self, query):
        """Use a search from history."""
        self._catalogue_query_var.set(query)
        self._do_catalogue_search()

    def _catalogue_run(self, src, q, gid, nexus_dom, game_name):
        try:
            results, notes = search_all(
                self.cfg, q, gid, nexus_dom, game_name, src)
            hints = []
            for r in results[:8]:
                dep = ksp_dependency_hint(r.get("name", ""))
                if dep:
                    hints.append(f"{r['name']}: needs {', '.join(dep)}")
            if hints:
                notes = (notes or []) + hints[:2]
        except Exception as exc:
            msg = str(exc)
            logger.exception("Catalogue search failed")
            self.after(0, lambda m=msg: self._cat_summary.configure(
                text=f"Error: {m}"))
            return
        sort = (self._cat_sort_var.get()
                if hasattr(self, "_cat_sort_var") else "Relevance")
        if sort == "Downloads":
            results.sort(key=lambda r: r.get("downloads", 0), reverse=True)
        self.after(0, lambda: self._cat_render(results, q, notes))

    def _cat_render(self, results, q, notes=None):
        self._search_results = results
        note_txt = ""
        if notes:
            note_txt = "  |  " + "; ".join(notes[:3])
        self._cat_summary.configure(
            text=f"{len(results):,} results for '{q}'{note_txt}")
        if not hasattr(self, "_catalogue_results"):
            return
        for w in self._catalogue_results.winfo_children():
            w.destroy()
        if not results:
            tk.Label(self._catalogue_results,
                     text="No mods found. Try another name or add API keys "
                          "in Settings for CurseForge / Nexus.",
                     bg=BG, fg=MUTED, font=("Segoe UI", 11),
                     wraplength=700, justify="left").pack(pady=24, padx=8)
            return
        for r in results:
            self._cat_result_row(self._catalogue_results, r)

    def _cat_result_row(self, parent, r):
        row = tk.Frame(parent, bg=PANEL, padx=0, pady=0)
        row.pack(fill="x", pady=6)
        # Thumb (letter tile)
        thumb_w, thumb_h = 96, 96
        thumb = tk.Label(row, text=(r["name"][:1] or "?").upper(),
                         bg=PANEL_2, fg=ACCENT,
                         font=("Segoe UI", 28, "bold"),
                         width=int(thumb_w / 14),
                         height=int(thumb_h / 30))
        thumb.pack(side="left", padx=0, pady=0)

        info = tk.Frame(row, bg=PANEL, padx=18, pady=14)
        info.pack(side="left", fill="both", expand=True)
        tk.Label(info, text=r["name"], bg=PANEL, fg=TEXT,
                 anchor="w", font=("Segoe UI", 12, "bold")
                 ).pack(anchor="w")
        alt = r.get("alt_sources", [])
        src_txt = r["source"]
        if alt:
            src_txt += " (+ " + ", ".join(s for s in alt if s != r["source"]) + ")"
        tk.Label(info,
                 text=f"by {r['author']}    {r.get('downloads', 0):,} downloads"
                      f"    {src_txt}",
                 bg=PANEL, fg=MUTED, anchor="w",
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 0))
        dep = ksp_dependency_hint(r.get("name", ""))
        if dep:
            tk.Label(info, text="Often needs: " + ", ".join(dep),
                     bg=PANEL, fg=ACCENT_2, anchor="w",
                     font=("Segoe UI", 9)).pack(anchor="w")

        right = tk.Frame(row, bg=PANEL)
        right.pack(side="right", padx=14)
        if r.get("web_url"):
            ob = tk.Button(right, text="Open",
                           command=lambda u=r["web_url"]: webbrowser.open(u))
            style_button(ob, ghost=True)
            ob.pack(pady=(0, 4))
            ToolTip(ob, "Open mod page in browser")
        if r.get("manual_only"):
            tk.Label(right, text="Manual", bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 9)).pack()
            ToolTip(right, "Manual installation required")
        else:
            dl = tk.Button(right, text="⬇ Install",
                           command=lambda mod=r: self._cat_download(mod))
            style_button(dl, primary=True)
            dl.pack()
            ToolTip(dl, "Download and install to selected profile")

    def _cat_download(self, mod):
        game = self._cat_target_game.get()
        prof = self._cat_target_prof.get()
        if not (game and prof):
            messagebox.showinfo("Pick a target",
                                "Select a game + profile in the sidebar.")
            return
        self._cat_status.configure(text=f"Resolving '{mod['name']}'…",
                                   fg=MUTED)
        threading.Thread(target=self._do_download,
                         args=(mod, game, prof), daemon=True).start()

    def _default_profile(self):
        g = self.cfg["games"].get(self.detail_game, {})
        profs = g.get("profiles", ["Vanilla"])
        ap = g.get("applied_profile")
        if ap and ap in profs:
            return ap
        return profs[0] if profs else "Vanilla"

    def _hub_installed_count(self):
        if not (self.detail_game and self.detail_profile):
            return 0
        folder = profile_folder(self.detail_game, self.detail_profile)
        if not folder.exists():
            return 0
        return sum(1 for p in folder.rglob("*") if p.is_file())

    def _ts_button(self, parent, text, command, primary=False, danger=False,
                   outline=False):
        if primary:
            bg, fg, hov = TS_BTN, "#fff", "#3d8eef"
        elif danger:
            bg, fg, hov = DANGER, "#fff", "#ff6f8c"
        elif outline:
            bg, fg, hov = TS_SIDEBAR, TEXT, HILITE
        else:
            bg, fg, hov = "#2a475e", TEXT, "#3d5a73"
        b = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                      activebackground=hov, activeforeground=fg,
                      relief="flat", bd=0, padx=14, pady=8, cursor="hand2",
                      font=("Segoe UI", 10))
        return b

    # ===================================================================
    # PAGE: GAME PROFILES (Thunderstore-style profile picker)
    # ===================================================================
    def _page_game_profiles(self):
        name = self.detail_game
        if not name or name not in self.cfg["games"]:
            tk.Label(self.content, text="Game not found.", bg=BG,
                     fg=MUTED).pack(pady=40)
            return
        g = self.cfg["games"][name]

        head = tk.Frame(self.content, bg=TS_HEADER)
        head.pack(fill="x")
        tk.Label(head, text="Profile selection", bg=TS_HEADER, fg="#fff",
                 font=("Segoe UI", 22, "bold")).pack(
                     anchor="w", padx=28, pady=(22, 4))
        tk.Label(head, text="Profiles help to organise mods easily",
                 bg=TS_HEADER, fg="#d0e4ff",
                 font=("Segoe UI", 11)).pack(anchor="w", padx=28, pady=(0, 18))

        nav = tk.Frame(self.content, bg=TS_SIDEBAR)
        nav.pack(fill="x")
        back = tk.Label(nav, text="←  Back to game selection", bg=TS_SIDEBAR,
                        fg=TS_LINK, cursor="hand2",
                        font=("Segoe UI", 11, "underline"))
        back.pack(anchor="w", padx=24, pady=12)
        back.bind("<Button-1>",
                  lambda e: self.show_page("library"))

        scroll_wrap = tk.Frame(self.content, bg=TS_MAIN)
        scroll_wrap.pack(fill="both", expand=True)
        body = self._scrollable(scroll_wrap)
        try:
            body.configure(bg=TS_MAIN)
        except tk.TclError:
            pass
        for w in body.winfo_children():
            w.destroy()

        for prof in g.get("profiles", ["Vanilla"]):
            if prof.startswith("_AutoBackup_"):
                continue
            block = tk.Frame(body, bg=TS_MAIN)
            block.pack(fill="x", padx=28, pady=(18, 0))
            tk.Label(block, text=prof, bg=TS_MAIN, fg=TS_LINK,
                     font=("Segoe UI", 18, "bold")).pack(anchor="w")
            tk.Frame(block, bg="#2a475e", height=1).pack(fill="x", pady=(8, 12))
            row = tk.Frame(block, bg=TS_MAIN)
            row.pack(fill="x", pady=(0, 8))
            self._ts_button(
                row, "Select profile",
                lambda p=prof: self.show_page("game_hub", game=name, profile=p),
                primary=True).pack(side="left", padx=(0, 8))
            self._ts_button(
                row, "Rename",
                lambda p=prof: self._dp_rename_profile(p),
                outline=True).pack(side="left", padx=4)
            self._ts_button(
                row, "Duplicate",
                lambda p=prof: self._dp_duplicate_profile(p),
                outline=True).pack(side="left", padx=4)
            self._ts_button(
                row, "Create new", self._dp_new_profile,
                outline=True).pack(side="left", padx=4)
            self._ts_button(
                row, "Import / Update", self._dp_import,
                outline=True).pack(side="left", padx=4)
            self._ts_button(
                row, "Delete",
                lambda p=prof: self._dp_delete_profile(p),
                danger=True).pack(side="left", padx=4)

        foot = tk.Frame(body, bg=TS_MAIN)
        foot.pack(fill="x", padx=28, pady=24)
        self._ts_button(foot, "+ Create new profile", self._dp_new_profile,
                         outline=True).pack(side="left")

    def _dp_rename_profile(self, prof):
        self.detail_profile = prof
        self._dp_rename()

    def _dp_duplicate_profile(self, prof):
        """Duplicate an existing profile with a new name."""
        new_name = self._prompt_name("Duplicate profile", f"{prof} (copy)")
        if not new_name:
            return
        g = self.cfg["games"][self.detail_game]
        if new_name in g.get("profiles", []):
            messagebox.showerror("Error", "Profile already exists.")
            return
        src = profile_folder(self.detail_game, prof)
        dst = profile_folder(self.detail_game, new_name)
        if src.exists():
            shutil.copytree(src, dst)
        g.setdefault("profiles", []).append(new_name)
        save_config(self.cfg)
        self.show_page("game_profiles", game=self.detail_game)

    def _dp_delete_profile(self, prof):
        self.detail_profile = prof
        self._dp_delete()

    # ===================================================================
    # PAGE: GAME HUB (Thunderstore-style mod manager)
    # ===================================================================
    def _page_game_hub(self):
        name = self.detail_game
        if not name or name not in self.cfg["games"]:
            return
        g = self.cfg["games"][name]
        if not self.detail_profile:
            self.detail_profile = self._default_profile()

        root = tk.Frame(self.content, bg=TS_MAIN)
        root.pack(fill="both", expand=True)
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(0, weight=1)

        sb = tk.Frame(root, bg=TS_SIDEBAR, width=248)
        sb.grid(row=0, column=0, sticky="ns")
        sb.grid_propagate(False)

        tk.Frame(sb, bg=TS_SIDEBAR, height=12).pack()

        start_row = tk.Frame(sb, bg=TS_SIDEBAR)
        start_row.pack(fill="x", padx=12, pady=(4, 16))
        self._ts_button(
            start_row, "▶  Start modded",
            self._hub_start_modded, primary=True).pack(fill="x")

        tk.Label(sb, text="MODS", bg=TS_SIDEBAR, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16,
                                                    pady=(8, 6))
        n_inst = self._hub_installed_count()
        tab_row = tk.Frame(sb, bg=TS_SIDEBAR)
        tab_row.pack(fill="x", padx=8)

        def tab_btn(label, tab, count=""):
            fr = tk.Frame(tab_row, bg=TS_SIDEBAR)
            fr.pack(fill="x", pady=2)
            sel = self._hub_tab.get() == tab
            b = tk.Button(
                fr, text=f"  {label}  {count}",
                command=lambda: self._hub_switch_tab(tab),
                bg="#2a475e" if sel else TS_SIDEBAR,
                fg="#fff" if sel else MUTED,
                activebackground="#2a475e", relief="flat", anchor="w",
                padx=12, pady=10, font=("Segoe UI", 11))
            b.pack(fill="x")

        tab_btn("Installed", "installed", str(n_inst))
        tab_btn("Online", "online", "")

        tk.Label(sb, text="OTHER", bg=TS_SIDEBAR, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16,
                                                    pady=(16, 6))
        for lbl, cmd in [
            ("Open profile folder", self._dp_open_folder),
            ("Apply to game folder", self._dp_apply),
            ("Advanced tools…", self._hub_advanced_menu),
            ("Edit game", lambda: self._open_game_form(name)),
        ]:
            lk = tk.Label(sb, text=f"  {lbl}", bg=TS_SIDEBAR, fg=TEXT,
                          cursor="hand2", font=("Segoe UI", 10))
            lk.pack(anchor="w", padx=12, pady=5)
            lk.bind("<Button-1>", lambda e, c=cmd: c())

        foot = tk.Frame(sb, bg="#0f1419")
        foot.pack(side="bottom", fill="x")
        tk.Label(foot, text=f"{name}  /  {self.detail_profile}",
                 bg="#0f1419", fg=MUTED, font=("Segoe UI", 9)
                 ).pack(anchor="w", padx=12, pady=(10, 2))
        ch = tk.Label(foot, text="Change profile", bg="#0f1419", fg=TS_LINK,
                      cursor="hand2", font=("Segoe UI", 9, "underline"))
        ch.pack(anchor="w", padx=12, pady=(0, 10))
        ch.bind("<Button-1>",
                lambda e: self.show_page("game_profiles", game=name))

        main = tk.Frame(root, bg=TS_MAIN)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        top = tk.Frame(main, bg=TS_MAIN)
        top.pack(fill="x", padx=20, pady=(12, 8))
        back = tk.Label(top, text="← Library", bg=TS_MAIN, fg=TS_LINK,
                        cursor="hand2", font=("Segoe UI", 10, "underline"))
        back.pack(side="left")
        back.bind("<Button-1>", lambda e: self.show_page("library"))
        tk.Label(top, text=name.upper(), bg=TS_MAIN, fg=TEXT,
                 font=("Segoe UI", 14, "bold")).pack(side="left", padx=16)

        banner_fr = tk.Frame(main, bg=TS_MAIN, height=100)
        banner_fr.pack(fill="x", padx=20, pady=(0, 8))
        banner_fr.pack_propagate(False)
        img = self._cover_image(name + "_hub", g, 900, 100, banner=True)
        if img:
            tk.Label(banner_fr, image=img, bg=TS_MAIN).pack(fill="both",
                                                           expand=True)
        else:
            tk.Label(banner_fr, text=name, bg=TS_MAIN, fg=ACCENT,
                     font=("Segoe UI", 28, "bold")).pack(expand=True)

        self._hub_main = tk.Frame(main, bg=TS_MAIN)
        self._hub_main.pack(fill="both", expand=True, padx=12, pady=8)
        self._hub_build_tab()

    def _hub_switch_tab(self, tab):
        self._hub_tab.set(tab)
        self.show_page("game_hub", game=self.detail_game,
                       profile=self.detail_profile)

    def _hub_build_tab(self):
        for w in self._hub_main.winfo_children():
            w.destroy()
        if self._hub_tab.get() == "online":
            self._hub_build_online()
        else:
            self._hub_build_installed()

    def _hub_build_installed(self):
        wrap = self._scrollable(self._hub_main, bg=TS_MAIN)
        folder = profile_folder(self.detail_game, self.detail_profile)
        files = sorted(folder.rglob("*")) if folder.exists() else []
        files = [p for p in files if p.is_file()]
        if not files:
            empty = tk.Frame(wrap, bg=TS_MAIN)
            empty.pack(expand=True, pady=80)
            tk.Label(empty, text="!", bg=TS_MAIN, fg=TEXT,
                     font=("Segoe UI", 48)).pack()
            tk.Label(empty,
                     text="Looks like you don't have any mods installed",
                     bg=TS_MAIN, fg=TEXT,
                     font=("Segoe UI", 16, "bold")).pack(pady=(12, 8))
            go = tk.Label(empty, text="Click the Online tab on the left, "
                        "or click here.", bg=TS_MAIN, fg=TS_LINK,
                        cursor="hand2", font=("Segoe UI", 11, "underline"))
            go.pack()
            go.bind("<Button-1>",
                    lambda e: self._hub_switch_tab("online"))
            return
        for p in files:
            rel = str(p.relative_to(folder))
            row = tk.Frame(wrap, bg="#1e2329")
            row.pack(fill="x", pady=4, padx=4)
            
            # Favorite button
            fav_key = f"{self.detail_game}/{self.detail_profile}/{rel}"
            is_fav = self._is_mod_favorite(fav_key)
            fav_btn = tk.Button(row, text="★" if is_fav else "☆",
                              command=lambda fk=fav_key, btn=None: self._toggle_mod_favorite(fk, row),
                              bg="#1e2329", fg=ACCENT if is_fav else MUTED,
                              relief="flat", bd=0, padx=8, cursor="hand2",
                              font=("Segoe UI", 12))
            fav_btn.pack(side="left", padx=(8, 4))
            ToolTip(fav_btn, "Mark as favorite")
            
            tk.Label(row, text=Path(rel).name, bg="#1e2329", fg=TEXT,
                     font=("Segoe UI", 11, "bold"),
                     padx=14, pady=10).pack(side="left")
            tk.Label(row, text=human_size(p.stat().st_size), bg="#1e2329",
                     fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
            st = "disabled" if p.suffix == ".disabled" else "ON"
            tk.Label(row, text=st, bg="#1e2329", fg=SUCCESS if st == "ON"
                     else MUTED, padx=8).pack(side="right", padx=8)

    def _hub_build_online(self):
        top = tk.Frame(self._hub_main, bg=TS_MAIN)
        top.pack(fill="x", pady=(0, 8))
        ent = tk.Entry(top, textvariable=self._hub_online_query,
                       bg="#1e2329", fg=TEXT, insertbackground=TEXT,
                       relief="flat", font=("Segoe UI", 11))
        ent.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 8))
        ent.bind("<Return>", lambda e: self._hub_online_search())
        self._hub_online_query.trace_add(
            "write", lambda *_a: self._hub_schedule_search())
        self._ts_button(top, "Search", self._hub_online_search,
                        primary=True).pack(side="left")
        self._hub_online_status = tk.Label(
            self._hub_main, text="Search all trusted sources — installs into "
            f"{self.detail_profile}", bg=TS_MAIN, fg=MUTED,
            font=("Segoe UI", 10))
        self._hub_online_status.pack(anchor="w", pady=(0, 6))
        self._hub_online_results = self._scrollable(self._hub_main, bg=TS_MAIN)

    def _hub_schedule_search(self):
        if self._hub_search_debounce:
            try:
                self.after_cancel(self._hub_search_debounce)
            except Exception:
                pass
        self._hub_search_debounce = self.after(450, self._hub_online_search)

    def _hub_online_search(self):
        if self.current_page != "game_hub":
            return
        q = self._hub_online_query.get().strip()
        if len(q) < 2:
            if hasattr(self, "_hub_online_status"):
                self._hub_online_status.configure(
                    text="Type at least 2 characters to search…")
            return
        g = self.cfg["games"].get(self.detail_game, {})
        self._hub_online_status.configure(text=f"Searching for '{q}'…")
        for w in self._hub_online_results.winfo_children():
            w.destroy()
        threading.Thread(
            target=self._hub_online_run,
            args=(q, g.get("game_id", ""), g.get("nexus_domain", "")),
            daemon=True).start()

    def _hub_online_run(self, q, gid, nexus_dom):
        try:
            results, notes = search_all(
                self.cfg, q, gid, nexus_dom, self.detail_game, "All sources")
        except Exception as exc:
            self.after(0, lambda: self._hub_online_status.configure(
                text=f"Error: {exc}"))
            return
        self.after(0, lambda: self._hub_render_online(results, q, notes))

    def _hub_render_online(self, results, q, notes=None):
        if not hasattr(self, "_hub_online_results"):
            return
        for w in self._hub_online_results.winfo_children():
            w.destroy()
        note = ("  |  " + "; ".join(notes[:2])) if notes else ""
        self._hub_online_status.configure(
            text=f"{len(results)} results for '{q}'{note}")
        if not results:
            tk.Label(self._hub_online_results,
                     text="No mods found. Try another search term.",
                     bg=TS_MAIN, fg=MUTED, font=("Segoe UI", 11)).pack(pady=40)
            return
        for r in results:
            row = tk.Frame(self._hub_online_results, bg="#1e2329")
            row.pack(fill="x", pady=5, padx=4)
            info = tk.Frame(row, bg="#1e2329")
            info.pack(side="left", fill="x", expand=True, padx=12, pady=12)
            tk.Label(info, text=r["name"], bg="#1e2329", fg=TEXT,
                     font=("Segoe UI", 12, "bold"), anchor="w").pack(anchor="w")
            tk.Label(info,
                     text=f"{r.get('author','?')}  ·  {r['source']}  ·  "
                          f"{r.get('downloads',0):,} downloads",
                     bg="#1e2329", fg=MUTED, font=("Segoe UI", 9)
                     ).pack(anchor="w", pady=(4, 0))
            acts = tk.Frame(row, bg="#1e2329")
            acts.pack(side="right", padx=12, pady=12)
            if r.get("web_url"):
                self._ts_button(
                    acts, "Open", lambda u=r["web_url"]: webbrowser.open(u),
                    outline=True).pack(side="top", pady=2)
            if not r.get("manual_only"):
                self._ts_button(
                    acts, "Install",
                    lambda mod=r: self._hub_install(mod),
                    primary=True).pack(side="top", pady=2)

    def _hub_install(self, mod):
        self.status_var.set(f"Installing {mod['name']}…")
        threading.Thread(
            target=self._do_download,
            args=(mod, self.detail_game, self.detail_profile),
            daemon=True).start()
        self.after(2000, lambda: self.show_page(
            "game_hub", game=self.detail_game, profile=self.detail_profile))

    def _hub_start_modded(self):
        self.cfg["active_game"] = self.detail_game
        self.cfg["active_profile"] = self.detail_profile
        save_config(self.cfg)
        self.apply_profile(confirm=True)

    def _hub_advanced_menu(self):
        win = tk.Toplevel(self)
        win.title("Advanced")
        win.geometry("320x420")
        win.transient(self)
        style_dialog(win)
        for lbl, cmd in [
            ("Dry-run preview", self._dp_dry_run),
            ("Diff profile vs live", self._dp_diff),
            ("Undo last apply", self._dp_undo_apply),
            ("Clone profile", self._dp_clone),
            ("Load order", self._dp_load_order),
            ("Export mod list", self._dp_export_list),
            ("Export zip", self._dp_export),
            ("Conflict check", self._dp_conflicts),
            ("Check updates", self._dp_check_updates),
            ("Rescan art", self._dp_rescan_art),
        ]:
            b = tk.Button(win, text=lbl, command=lambda c=cmd: (c(), win.destroy()))
            style_button(b)
            b.pack(fill="x", padx=16, pady=4)

    # ----- detail page handlers (shared with game hub) -----

    def _dp_rescan_art(self):
        g = self.cfg["games"].get(self.detail_game)
        if not g or not g.get("game_folder"):
            messagebox.showinfo("Rescan", "No game folder set.")
            return
        apply_discovered_art(g, self.detail_game)
        save_config(self.cfg)
        self._cover_imgs.clear()
        self.show_page("game_hub", game=self.detail_game,
                       profile=self.detail_profile)
        cp = g.get("cover_path", "")
        messagebox.showinfo(
            "Images updated",
            f"Cover: {Path(cp).name if cp else 'not found'}\n"
            f"Banner: {Path(g.get('banner_path') or cp).name if cp else 'not found'}")

    def _dp_open_folder(self):
        if self.detail_game and self.detail_profile:
            self._open_in_explorer(profile_folder(self.detail_game,
                                                  self.detail_profile))

    def _dp_dry_run(self):
        g = self.cfg["games"][self.detail_game]
        src = profile_folder(self.detail_game, self.detail_profile)
        dst = real_mod_path(g)
        dr = dry_run_apply(src, dst)
        msg = (f"Would copy {dr['copy_count']} item(s) from profile.\n"
               f"Would remove {dr['remove_count']} item(s) from live folder.\n"
               f"Backup will be created automatically.")
        messagebox.showinfo("Dry-run preview", msg)

    def _dp_diff(self):
        g = self.cfg["games"][self.detail_game]
        d = profile_diff(real_mod_path(g),
                         profile_folder(self.detail_game, self.detail_profile))
        msg = (f"Only in live ({len(d['only_live'])}):\n"
               + "\n".join(f"  + {x}" for x in d["only_live"][:12])
               + f"\n\nOnly in profile ({len(d['only_profile'])}):\n"
               + "\n".join(f"  - {x}" for x in d["only_profile"][:12])
               + f"\n\nChanged size ({len(d['changed'])}):\n"
               + "\n".join(f"  ~ {x}" for x in d["changed"][:12]))
        messagebox.showinfo("Profile diff", msg[:4000])

    def _dp_undo_apply(self):
        g = self.cfg["games"].get(self.detail_game, {})
        last = g.get("last_backup_profile")
        if not last:
            messagebox.showinfo("Undo", "No backup profile recorded yet.")
            return
        self.detail_profile = last
        self.cfg["active_game"] = self.detail_game
        self.cfg["active_profile"] = last
        save_config(self.cfg)
        self.apply_profile(confirm=True)

    def _dp_load_order(self):
        folder = profile_folder(self.detail_game, self.detail_profile)
        files = sorted(p.name for p in folder.iterdir() if p.is_file())
        if not files:
            messagebox.showinfo("Load order", "No files in profile.")
            return
        win = tk.Toplevel(self)
        win.title("Load order")
        win.geometry("420x360")
        style_dialog(win)
        lb = tk.Listbox(win, bg=PANEL_2, fg=TEXT, selectbackground=ACCENT,
                        font=("Consolas", 10))
        lb.pack(fill="both", expand=True, padx=12, pady=12)
        for f in files:
            lb.insert("end", f)

        def move(d):
            sel = lb.curselection()
            if not sel:
                return
            i = sel[0]
            j = i + d
            if j < 0 or j >= lb.size():
                return
            v = lb.get(i)
            lb.delete(i)
            lb.insert(j, v)
            lb.selection_set(j)

        row = tk.Frame(win, bg=PANEL)
        row.pack(fill="x", padx=12, pady=(0, 12))
        for txt, d in [("↑ Up", -1), ("↓ Down", 1)]:
            b = tk.Button(row, text=txt, command=lambda dd=d: move(dd))
            style_button(b)
            b.pack(side="left", padx=4)

        def save():
            order = [lb.get(i) for i in range(lb.size())]
            apply_load_order_prefix(folder, order)
            win.destroy()
            self._dp_refresh_files()

        b = tk.Button(row, text="Save order", command=save)
        style_button(b, primary=True)
        b.pack(side="right")

    def _dp_export_list(self):
        folder = profile_folder(self.detail_game, self.detail_profile)
        text = export_mod_list(self.detail_game, self.detail_profile, folder)
        out = filedialog.asksaveasfilename(
            defaultextension=".md",
            initialfile=f"{safe_name(self.detail_game)}_mods.md")
        if out:
            Path(out).write_text(text, encoding="utf-8")
            messagebox.showinfo("Exported", f"Saved → {out}")

    def _dp_apply(self):
        self.cfg["active_game"] = self.detail_game
        self.cfg["active_profile"] = self.detail_profile
        save_config(self.cfg)
        self.apply_profile()

    def _quick_apply(self):
        if self.detail_game and self.detail_profile:
            self.cfg["active_game"] = self.detail_game
            self.cfg["active_profile"] = self.detail_profile
            save_config(self.cfg)
        self.apply_profile()

    def _dp_new_profile(self):
        new = self._prompt_name("New profile name", "")
        if not new:
            return
        g = self.cfg["games"][self.detail_game]
        if new in g["profiles"]:
            messagebox.showinfo("Exists", f"'{new}' already exists.")
            return
        g["profiles"].append(new)
        profile_folder(self.detail_game, new)
        save_config(self.cfg)
        self.show_page("game_hub", game=self.detail_game,
                       profile=self.detail_profile)

    def _dp_clone(self):
        new = self._prompt_name("Clone to (new name)",
                                f"{self.detail_profile}_copy")
        if not new:
            return
        g = self.cfg["games"][self.detail_game]
        if new in g["profiles"]:
            messagebox.showinfo("Exists", f"'{new}' already exists.")
            return
        src = profile_folder(self.detail_game, self.detail_profile)
        dst = profile_folder(self.detail_game, new)
        try:
            for item in src.iterdir():
                target = dst / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target)
        except Exception as e:
            messagebox.showerror("Clone failed", str(e))
            return
        g["profiles"].append(new)
        g["notes"][new] = g["notes"].get(self.detail_profile, "")
        save_config(self.cfg)
        self.detail_profile = new
        self.show_page("game_hub", game=self.detail_game,
                       profile=self.detail_profile)

    def _dp_rename(self):
        if self.detail_profile == "Vanilla":
            messagebox.showinfo("Protected", "Vanilla cannot be renamed.")
            return
        new = self._prompt_name("Rename profile to", self.detail_profile)
        if not new:
            return
        g = self.cfg["games"][self.detail_game]
        if new in g["profiles"]:
            messagebox.showinfo("Exists", f"'{new}' already exists.")
            return
        old_p = profile_folder(self.detail_game, self.detail_profile)
        new_p = PROFILES_ROOT / safe_name(self.detail_game) / safe_name(new)
        try:
            old_p.rename(new_p)
        except Exception as e:
            messagebox.showerror("Rename failed", str(e))
            return
        g["profiles"][g["profiles"].index(self.detail_profile)] = new
        if self.detail_profile in g["notes"]:
            g["notes"][new] = g["notes"].pop(self.detail_profile)
        if g.get("applied_profile") == self.detail_profile:
            g["applied_profile"] = new
        save_config(self.cfg)
        self.detail_profile = new
        self.show_page("game_hub", game=self.detail_game,
                       profile=self.detail_profile)

    def _dp_export(self):
        src = profile_folder(self.detail_game, self.detail_profile)
        out = filedialog.asksaveasfilename(
            defaultextension=".zip",
            initialfile=f"{safe_name(self.detail_game)}_"
                        f"{safe_name(self.detail_profile)}.zip")
        if not out:
            return
        try:
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                for p in src.rglob("*"):
                    if p.is_file():
                        z.write(p, p.relative_to(src))
            messagebox.showinfo("Exported", f"Saved -> {out}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    def _dp_import(self):
        src = filedialog.askopenfilename(
            title="Import profile .zip — or Cancel to pick a folder",
            filetypes=[("Zip", "*.zip"), ("All", "*.*")])
        is_dir = False
        if not src:
            src = filedialog.askdirectory(title="Pick a profile folder")
            if not src:
                return
            is_dir = True
        name = self._prompt_name("Import as profile name", Path(src).stem)
        if not name:
            return
        g = self.cfg["games"][self.detail_game]
        if name in g["profiles"]:
            messagebox.showinfo("Exists", f"'{name}' already exists.")
            return
        dst = profile_folder(self.detail_game, name)
        try:
            if is_dir:
                for item in Path(src).iterdir():
                    target = dst / item.name
                    if item.is_dir():
                        shutil.copytree(item, target, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, target)
            else:
                with zipfile.ZipFile(src) as z:
                    z.extractall(dst)
        except Exception as e:
            messagebox.showerror("Import failed", str(e))
            return
        g["profiles"].append(name)
        save_config(self.cfg)
        self.detail_profile = name
        self.show_page("game_hub", game=self.detail_game,
                       profile=self.detail_profile)

    def _dp_restore(self):
        g = self.cfg["games"][self.detail_game]
        backups = [p for p in g["profiles"] if p.startswith("_AutoBackup_")]
        if not backups:
            messagebox.showinfo("No backups", "No auto-backups yet.")
            return
        target = (self.detail_profile if self.detail_profile in backups
                  else backups[-1])
        if not messagebox.askyesno("Restore?",
                                   f"Apply backup '{target}' to {self.detail_game}?"):
            return
        self.detail_profile = target
        self.cfg["active_game"] = self.detail_game
        self.cfg["active_profile"] = target
        save_config(self.cfg)
        self.apply_profile(confirm=False)

    def _dp_check_updates(self):
        folder = profile_folder(self.detail_game, self.detail_profile)
        files = [p for p in folder.rglob("*")
                 if p.is_file() and p.suffix.lower() in (".jar", ".zip")]
        if not files:
            messagebox.showinfo("Nothing to check", "No .jar/.zip files.")
            return
        self.status_var.set(f"Checking {len(files)} files on Modrinth…")
        threading.Thread(target=self._do_check_updates,
                         args=(files,), daemon=True).start()

    def _dp_conflicts(self):
        folder = profile_folder(self.detail_game, self.detail_profile)
        msgs = detect_conflicts_v2(folder)
        if not msgs:
            messagebox.showinfo("No conflicts", "Nothing duplicated.")
            return
        messagebox.showwarning("Conflicts", "\n".join(msgs[:20]))

    def _dp_toggle(self):
        messagebox.showinfo(
            "Manage files",
            "Open the profile folder to enable/disable mods "
            "(add or remove .disabled on filenames).")
        self._dp_open_folder()

    def _dp_del_file(self):
        self._dp_open_folder()

    def _dp_delete(self):
        if self.detail_profile == "Vanilla":
            messagebox.showinfo("Protected", "Vanilla can't be deleted.")
            return
        if not messagebox.askyesno("Delete?",
                                   f"Delete '{self.detail_profile}' and ALL files?"):
            return
        g = self.cfg["games"][self.detail_game]
        try:
            shutil.rmtree(profile_folder(self.detail_game,
                                         self.detail_profile),
                          ignore_errors=True)
        except Exception:
            pass
        g["profiles"].remove(self.detail_profile)
        g["notes"].pop(self.detail_profile, None)
        self.detail_profile = "Vanilla"
        save_config(self.cfg)
        self.show_page("game_hub", game=self.detail_game,
                       profile=self.detail_profile)

    # ===================================================================
    # PAGE: DOWNLOADS
    # ===================================================================
    def _page_downloads(self):
        tk.Label(self.content, text="Recent downloads", bg=BG, fg=TEXT,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w",
                                                    pady=(0, 12))
        if not self._download_log:
            tk.Label(self.content,
                     text="No downloads yet. Search the Catalogue to grab mods.",
                     bg=BG, fg=MUTED,
                     font=("Segoe UI", 11)).pack(anchor="w", pady=20)
            return
        for entry in reversed(self._download_log):
            row = tk.Frame(self.content, bg=PANEL)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=entry["name"], bg=PANEL, fg=TEXT,
                     padx=14, pady=10,
                     font=("Segoe UI", 11, "bold")).pack(side="left")
            tk.Label(row,
                     text=f"  {entry['game']} / {entry['profile']}"
                          f"   {entry['when']}",
                     bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 10)).pack(side="left")

    # ===================================================================
    # PAGE: SETTINGS
    # ===================================================================
    def _page_settings(self):
        w = tk.Frame(self.content, bg=PANEL, padx=24, pady=24)
        w.pack(fill="both", expand=True)
        tk.Label(w, text="API Keys (optional)", bg=PANEL, fg=ACCENT_2,
                 font=("Segoe UI", 13, "bold")).pack(anchor="w")
        tk.Label(w,
                 text="Modrinth needs no key. Paste a CurseForge key to "
                      "unlock that source. Get one free at "
                      "https://console.curseforge.com/",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
                 wraplength=820, justify="left").pack(anchor="w",
                                                     pady=(4, 12))

        tk.Label(w, text="CurseForge API Key", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.in_cf_key = tk.Entry(w, show="*")
        style_entry(self.in_cf_key)
        self.in_cf_key.insert(0, self.cfg.get("curseforge_api_key", ""))
        self.in_cf_key.pack(anchor="w", pady=(4, 12), fill="x")

        tk.Label(w, text="Nexus Mods API Key", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.in_nexus_key = tk.Entry(w, show="*")
        style_entry(self.in_nexus_key)
        self.in_nexus_key.insert(0, self.cfg.get("nexus_api_key", ""))
        self.in_nexus_key.pack(anchor="w", pady=(4, 12), fill="x")

        tk.Label(w, text="Hydra Launcher Executable Path", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        row = tk.Frame(w, bg=PANEL)
        row.pack(fill="x", pady=(4, 12))
        self.in_hydra = tk.Entry(row)
        style_entry(self.in_hydra)
        self.in_hydra.insert(0, self.cfg.get("hydra_launcher_path", ""))
        self.in_hydra.pack(side="left", fill="x", expand=True)
        b = tk.Button(row, text="Browse", command=self._browse_hydra)
        style_button(b)
        b.pack(side="left", padx=(8, 0))

        self.var_symlink = tk.BooleanVar(value=self.cfg.get("use_symlinks",
                                                            False))
        ttk.Checkbutton(w, text="Use SYMLINKS on Apply (faster; Windows "
                                "may need Administrator)",
                        variable=self.var_symlink).pack(anchor="w",
                                                        pady=(0, 12))

        tk.Label(w, text="Enabled search sources", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(8, 4))
        self._src_vars = {}
        en = self.cfg.setdefault("enabled_sources", {})
        for src in TRUSTED_SOURCES:
            v = tk.BooleanVar(value=en.get(src, True))
            self._src_vars[src] = v
            ttk.Checkbutton(w, text=src, variable=v).pack(anchor="w")

        br = tk.Frame(w, bg=PANEL)
        br.pack(fill="x", pady=(12, 0))
        bz = tk.Button(br, text="Backup config + profiles (.zip)",
                       command=self._backup_zip)
        style_button(bz)
        bz.pack(side="left", padx=(0, 8))
        bf = find_hydra_exe()
        if bf and not self.cfg.get("hydra_launcher_path"):
            self.in_hydra.insert(0, bf)

        sb = tk.Button(w, text="Save Settings", command=self.save_settings)
        style_button(sb, primary=True)
        sb.pack(anchor="w", pady=(12, 0))

        feat = (f"\nDrag-drop on window: "
                f"{'ENABLED' if HAS_DND else 'install tkinterdnd2'}"
                f"\nCover images: "
                f"{'ENABLED' if HAS_PIL else 'install Pillow'}"
                f"\nSystem tray: "
                f"{'ENABLED' if HAS_TRAY else 'install pystray + Pillow'}"
                f"\n\nConfig: {CONFIG_PATH}"
                f"\nProfiles: {PROFILES_ROOT}"
                f"\nLog: {LOG_PATH}"
                f"\nHotkey: Ctrl+Enter to apply active profile")
        tk.Label(w, text=feat, bg=PANEL, fg=MUTED, font=("Consolas", 9),
                 justify="left").pack(anchor="w", pady=(20, 0))

    def save_settings(self):
        self.cfg["curseforge_api_key"] = self.in_cf_key.get().strip()
        self.cfg["nexus_api_key"] = self.in_nexus_key.get().strip()
        self.cfg["hydra_launcher_path"] = self.in_hydra.get().strip()
        self.cfg["use_symlinks"] = bool(self.var_symlink.get())
        if hasattr(self, "_src_vars"):
            for src, var in self._src_vars.items():
                self.cfg.setdefault("enabled_sources", {})[src] = bool(var.get())
        save_config(self.cfg)
        messagebox.showinfo("Saved", "Settings stored.")

    def _backup_zip(self):
        out = filedialog.asksaveasfilename(
            defaultextension=".zip",
            initialfile="hydra_companion_backup.zip")
        if not out:
            return
        try:
            backup_config_zip(Path(out))
            messagebox.showinfo("Backup", f"Saved → {out}")
        except Exception as exc:
            messagebox.showerror("Backup failed", str(exc))

    def _browse_hydra(self):
        f = filedialog.askopenfilename(title="Pick Hydra Launcher executable")
        if f:
            self.in_hydra.delete(0, "end")
            self.in_hydra.insert(0, f)

    # ===================================================================
    # GAME FORM (Add / Edit) — modal
    # ===================================================================
    def _open_game_form(self, name):
        win = tk.Toplevel(self)
        win.title("Add Game" if not name else f"Edit — {name}")
        win.geometry("560x560")
        style_dialog(win)
        win.transient(self)
        win.grab_set()

        g = self.cfg["games"].get(name) if name else {}

        def add_row(label, default="", browse=None, multiline=False):
            tk.Label(win, text=label, bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w",
                                                padx=22, pady=(10, 2))
            if multiline:
                ent = tk.Text(win, height=4, bg=PANEL_2, fg=TEXT,
                              insertbackground=TEXT, relief="flat",
                              highlightthickness=1,
                              highlightbackground=BORDER,
                              highlightcolor=ACCENT,
                              font=("Consolas", 9))
                ent.pack(fill="x", padx=22)
                if default:
                    ent.insert("1.0", default)
            else:
                row = tk.Frame(win, bg=PANEL)
                row.pack(fill="x", padx=22)
                ent = tk.Entry(row)
                style_entry(ent)
                ent.pack(side="left", fill="x", expand=True)
                if default:
                    ent.insert(0, default)
                if browse:
                    b = tk.Button(row, text="Browse",
                                  command=lambda: browse(ent))
                    style_button(b)
                    b.pack(side="left", padx=(8, 0))
            return ent

        def pick_folder(ent):
            d = filedialog.askdirectory(parent=win,
                                        title="Pick game root folder")
            if d:
                ent.delete(0, "end")
                ent.insert(0, d)
                art = discover_game_art(d)
                if art.get("cover_path"):
                    messagebox.showinfo(
                        "Art found",
                        f"Cover: {Path(art['cover_path']).name}\n"
                        f"Banner: {Path(art.get('banner_path', '')).name}",
                        parent=win)

        e_name = add_row("Game Name", name or "")
        e_folder = add_row("Game Folder (root)", g.get("game_folder", ""),
                           browse=pick_folder)
        e_sub = add_row("Mods Subfolder (default: mods)",
                        g.get("mod_subfolder", "mods"))
        e_id = add_row("Game ID (modrinth slug / curseforge gameId)",
                       g.get("game_id", ""))
        e_nexus = add_row("Nexus domain (example: kerbalspaceprogram)",
                          g.get("nexus_domain", ""))
        e_ver = add_row("Version filter (optional)", g.get("game_version_filter", ""))
        e_tags = add_row("Tags (comma separated)", ",".join(g.get("tags", [])))
        art = (discover_game_art(g.get("game_folder", ""), name or "")
               if g.get("game_folder") else {})
        art_hint = ""
        if art.get("cover_path"):
            art_hint = f"\nDetected cover: {Path(art['cover_path']).name}"
        if art.get("banner_path") and art.get("banner_path") != art.get("cover_path"):
            art_hint += f"\nDetected banner: {Path(art['banner_path']).name}"
        tk.Label(win, text="Cover art (auto from game folder)" + art_hint,
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
                 justify="left").pack(anchor="w", padx=22, pady=(8, 2))
        e_icon = add_row("Optional URL fallback (only if no local image)",
                         g.get("icon_url", ""))

        def apply_preset():
            p = guess_preset(e_name.get())
            if not p:
                messagebox.showinfo("Preset", "No preset for this game name.",
                                    parent=win)
                return
            e_sub.delete(0, "end")
            e_sub.insert(0, p.get("mod_subfolder", "mods"))
            e_id.delete(0, "end")
            e_id.insert(0, p.get("game_id", ""))
            e_nexus.delete(0, "end")
            e_nexus.insert(0, p.get("nexus_domain", ""))
            messagebox.showinfo("Preset", "Applied game template.", parent=win)

        pr = tk.Frame(win, bg=PANEL)
        pr.pack(fill="x", padx=22, pady=4)
        bp = tk.Button(pr, text="Apply game preset (KSP / Minecraft / ROUNDS)",
                       command=apply_preset)
        style_button(bp, ghost=True)
        bp.pack(anchor="w")

        extras = "\n".join(f"{e['name']}|{e['real_path']}"
                           for e in g.get("extra_folders", []))
        e_extra = add_row("Extra folders to swap "
                          "(one per line: name|absolute_path)",
                          extras, multiline=True)

        def save():
            nm = e_name.get().strip()
            fld = e_folder.get().strip()
            sub = e_sub.get().strip() or "mods"
            gid = e_id.get().strip()
            nexus_dom = e_nexus.get().strip().lower()
            ver_f = e_ver.get().strip()
            tags = [t.strip() for t in e_tags.get().split(",") if t.strip()]
            icon = e_icon.get().strip()
            ex_raw = e_extra.get("1.0", "end").strip()
            if not nm or not fld:
                messagebox.showerror("Missing", "Name and Game Folder required.",
                                     parent=win)
                return
            extras_list = []
            for line in ex_raw.splitlines():
                if "|" in line:
                    a, p = line.split("|", 1)
                    a, p = a.strip(), p.strip()
                    if a and p:
                        extras_list.append({"name": a, "real_path": p,
                                            "profile_subdir": safe_name(a)})
            try:
                (Path(fld) / sub).mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

            discovered = discover_game_art(fld, nm)

            game_data = self.cfg["games"].get(nm, {"profiles": ["Vanilla"],
                                                  "notes": {}})
            if name and name != nm:
                # rename folders
                old_dir = PROFILES_ROOT / safe_name(name)
                new_dir = PROFILES_ROOT / safe_name(nm)
                if old_dir.exists():
                    try:
                        old_dir.rename(new_dir)
                    except Exception:
                        pass
                game_data = self.cfg["games"].pop(name, game_data)
            game_data.update({"game_folder": fld, "mod_subfolder": sub,
                              "game_id": gid, "nexus_domain": nexus_dom,
                              "game_version_filter": ver_f, "tags": tags,
                              "icon_url": icon,
                              "cover_path": discovered.get("cover_path", ""),
                              "banner_path": discovered.get("banner_path", ""),
                              "extra_folders": extras_list})
            game_data.setdefault("profiles", ["Vanilla"])
            game_data.setdefault("notes", {})
            if "Vanilla" not in game_data["profiles"]:
                game_data["profiles"].insert(0, "Vanilla")
            self.cfg["games"][nm] = game_data
            profile_folder(nm, "Vanilla")
            save_config(self.cfg)
            self._cover_imgs.clear()
            self._refresh_sidebar_games()
            if self.current_page == "library":
                self._refresh_library()
            elif self.current_page in ("game_hub", "game_profiles"):
                self.detail_game = nm
                self.show_page("game_hub", game=self.detail_game,
                               profile=self.detail_profile)
            win.destroy()

        def delete():
            if not name:
                win.destroy()
                return
            if not messagebox.askyesno("Delete game?",
                                       f"Remove '{name}'? Profile folders "
                                       "on disk are kept.",
                                       parent=win):
                return
            self.cfg["games"].pop(name, None)
            save_config(self.cfg)
            self._refresh_sidebar_games()
            self.show_page("library")
            win.destroy()

        btns = tk.Frame(win, bg=PANEL)
        btns.pack(fill="x", padx=22, pady=18)
        b1 = tk.Button(btns, text="Save", command=save)
        style_button(b1, primary=True)
        b1.pack(side="left")
        if name:
            b2 = tk.Button(btns, text="Delete Game", command=delete)
            style_button(b2, danger=True)
            b2.pack(side="right")
        b3 = tk.Button(btns, text="Cancel", command=win.destroy)
        style_button(b3, ghost=True)
        b3.pack(side="right", padx=(0, 8))

    def _prompt_name(self, title, default=""):
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("400x140")
        style_dialog(win)
        win.transient(self)
        win.grab_set()
        tk.Label(win, text=title, bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(pady=(18, 6),
                                                    padx=18, anchor="w")
        ent = tk.Entry(win)
        style_entry(ent)
        ent.pack(fill="x", padx=18, ipady=4)
        ent.insert(0, default)
        ent.focus()
        result = {"v": None}

        def ok():
            result["v"] = ent.get().strip()
            win.destroy()

        ent.bind("<Return>", lambda e: ok())
        row = tk.Frame(win, bg=PANEL)
        row.pack(fill="x", padx=18, pady=14)
        b = tk.Button(row, text="OK", command=ok)
        style_button(b, primary=True)
        b.pack(side="right")
        b2 = tk.Button(row, text="Cancel", command=win.destroy)
        style_button(b2, ghost=True)
        b2.pack(side="right", padx=(0, 8))
        self.wait_window(win)
        return result["v"]

    # ===================================================================
    # Search / download workers (shared)
    # ===================================================================
    def _do_download(self, mod, game, prof):
        try:
            url, fname = resolve_download(mod, self.cfg)
            dest = profile_folder(game, prof) / fname
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length", 0))
                done = 0
                h = hashlib.sha256()
                if self.dl_progress is not None:
                    self.after(0, lambda: self.dl_progress.configure(
                        value=0, maximum=max(total, 1)))
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        h.update(chunk)
                        done += len(chunk)
                        if self.dl_progress is not None:
                            self.after(0, lambda v=done:
                                       self.dl_progress.configure(value=v))
            digest = h.hexdigest()[:12]
            logger.info("Downloaded %s sha256:%s…", dest.name, digest)
            self._download_log.append({
                "name": dest.name, "game": game, "profile": prof,
                "when": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "hash": digest})
            self.after(0, lambda: self._cat_status.configure(
                text=f"Downloaded → {dest.name} ({digest}…)", fg=SUCCESS))
            self.after(0, lambda: self.status_var.set(
                f"Downloaded {dest.name} into {game}/{prof}"))
        except Exception as exc:
            msg = str(exc)
            logger.warning("Download failed: %s", msg)
            self.after(0, lambda m=msg: self._cat_status.configure(
                text=f"Failed: {m}", fg=DANGER))

    def _do_check_updates(self, files):
        updates = []
        for p in files:
            try:
                with open(p, "rb") as fh:
                    digest = hashlib.sha1(fh.read()).hexdigest()
                r = requests.get(
                    f"{MODRINTH_API}/version_file/{digest}?algorithm=sha1",
                    timeout=20)
                if r.status_code != 200:
                    continue
                cur = r.json()
                pid = cur.get("project_id")
                if not pid:
                    continue
                vs = requests.get(f"{MODRINTH_API}/project/{pid}/version",
                                  timeout=20).json()
                if not vs:
                    continue
                latest = vs[0]
                if latest.get("id") == cur.get("id"):
                    continue
                primary = next((f for f in latest["files"] if f.get("primary")),
                               latest["files"][0])
                updates.append({"old_path": p,
                                "new_name": primary["filename"],
                                "url": primary["url"],
                                "version": latest.get("version_number", "?")})
            except Exception:
                continue

        def show():
            if not updates:
                self.status_var.set("All up to date.")
                messagebox.showinfo("Up to date", "No updates available.")
                return
            msg = "Updates available:\n\n" + "\n".join(
                f"• {u['old_path'].name} → {u['new_name']} ({u['version']})"
                for u in updates) + "\n\nDownload all?"
            if messagebox.askyesno("Updates", msg):
                threading.Thread(target=self._apply_updates,
                                 args=(updates,), daemon=True).start()
        self.after(0, show)

    def _apply_updates(self, updates):
        for u in updates:
            try:
                r = requests.get(u["url"], timeout=60)
                r.raise_for_status()
                new_path = u["old_path"].with_name(u["new_name"])
                new_path.write_bytes(r.content)
                if new_path != u["old_path"]:
                    u["old_path"].unlink(missing_ok=True)
            except Exception:
                continue
        self.after(0, lambda: self.status_var.set(
            f"Updated {len(updates)} file(s)."))
        if self.current_page == "game_hub":
            self.after(0, self._dp_refresh_files)

    # ===================================================================
    # Apply engine (uses self.cfg active_game/active_profile)
    # ===================================================================
    def apply_profile(self, confirm=True):
        game = self.cfg.get("active_game")
        prof = self.cfg.get("active_profile")
        if not (game and prof):
            messagebox.showinfo("Pick first", "Open a game and pick a profile.")
            return
        g = self.cfg["games"][game]
        real = real_mod_path(g)
        if not real.exists():
            real.mkdir(parents=True, exist_ok=True)
        src = profile_folder(game, prof)
        mode = "SYMLINK" if self.cfg.get("use_symlinks") else "COPY"
        if confirm and not messagebox.askyesno(
                "Apply profile?",
                f"Auto-backup {real}\nEmpty it, {mode} from\n{src}\n\nContinue?"):
            return
        threading.Thread(target=self._do_apply,
                         args=(game, prof, g, real, src),
                         daemon=True).start()

    def _do_apply(self, game, prof, g, real, src):
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"_AutoBackup_{ts}"
            errors = []
            backup_dir = profile_folder(game, backup_name)
            backed = self._copy_tree_into(real, backup_dir, errors)
            if backed > 0:
                if backup_name not in g["profiles"]:
                    g["profiles"].append(backup_name)
                g["last_backup_profile"] = backup_name
                save_config(self.cfg)
            else:
                try:
                    backup_dir.rmdir()
                except OSError:
                    pass
            self._empty_dir(real, errors)
            copied = self._mirror_into(src, real, errors)

            for ef in g.get("extra_folders", []):
                ef_real = Path(ef["real_path"])
                ef_real.mkdir(parents=True, exist_ok=True)
                ef_src = src / ef["profile_subdir"]
                ef_src.mkdir(parents=True, exist_ok=True)
                self._copy_tree_into(ef_real,
                                     backup_dir / ef["profile_subdir"],
                                     errors)
                self._empty_dir(ef_real, errors)
                self._mirror_into(ef_src, ef_real, errors)

            g["applied_profile"] = prof
            g["applied_hash"] = folder_hash(real)
            save_config(self.cfg)

            msg = (f"Profile '{prof}' applied to {game}.\n"
                   f"{copied} item(s) into {real}.\n\n"
                   "Open Hydra and press Play.")
            if errors:
                msg += "\n\nWarnings:\n - " + "\n - ".join(errors[:6])
            self.after(0, lambda: messagebox.showinfo("Applied", msg))
            if self.current_page == "game_hub":
                self.after(0, lambda: self.show_page(
                    "game_hub", game=self.detail_game,
                    profile=self.detail_profile))
            elif self.current_page == "library":
                self.after(0, self._refresh_library)
        except Exception as exc:
            err = str(exc)
            self.after(0, lambda m=err: messagebox.showerror("Apply failed", m))

    def _mirror_into(self, src, dst, errors):
        n = 0
        use_sym = self.cfg.get("use_symlinks", False)
        for item in src.iterdir():
            target = dst / item.name
            try:
                if use_sym:
                    if target.exists() or target.is_symlink():
                        target.unlink()
                    os.symlink(item, target,
                               target_is_directory=item.is_dir())
                else:
                    if item.is_dir():
                        shutil.copytree(item, target, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, target)
                n += 1
            except Exception as e:
                errors.append(f"{item.name}: {e}")
        return n

    def _copy_tree_into(self, src, dst, errors):
        if not src.exists():
            return 0
        dst.mkdir(parents=True, exist_ok=True)
        n = 0
        for item in src.iterdir():
            try:
                target = dst / item.name
                if item.is_symlink():
                    continue
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target)
                n += 1
            except Exception as e:
                errors.append(f"backup {item.name}: {e}")
        return n

    def _empty_dir(self, folder, errors):
        if not folder.exists():
            return
        for item in list(folder.iterdir()):
            self._safe_remove(item, errors)

    @staticmethod
    def _safe_remove(path, errors, retries=3):
        last = None
        for _ in range(retries):
            try:
                if path.is_symlink() or path.is_file():
                    path.unlink()
                else:
                    shutil.rmtree(path)
                return
            except PermissionError as e:
                last = e
                time.sleep(0.4)
            except Exception as e:
                last = e
                break
        errors.append(f"Could not remove {path.name}: {last}")

    # ===================================================================
    # Misc
    # ===================================================================
    def _open_in_explorer(self, p):
        try:
            if sys.platform.startswith("win"):
                os.startfile(p)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{p}"')
            else:
                os.system(f'xdg-open "{p}"')
        except Exception as e:
            messagebox.showerror("Open failed", str(e))

    def launch_hydra(self):
        path = self.cfg.get("hydra_launcher_path", "").strip()
        if not path:
            messagebox.showinfo("Set Hydra path",
                                "Pick the Hydra Launcher exe in Settings.")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                import subprocess
                subprocess.Popen([path])
        except Exception as e:
            messagebox.showerror("Launch failed", str(e))

    def _on_drop(self, event):
        if not (self.detail_game and self.detail_profile):
            self.status_var.set("Drop ignored — open a game first.")
            return
        dest = profile_folder(self.detail_game, self.detail_profile)
        out, buf, in_b = [], "", False
        for ch in event.data:
            if ch == "{":
                in_b = True
            elif ch == "}":
                in_b = False
                out.append(buf)
                buf = ""
            elif ch == " " and not in_b:
                if buf:
                    out.append(buf)
                    buf = ""
            else:
                buf += ch
        if buf:
            out.append(buf)
        added = 0
        for src in out:
            try:
                s = Path(src)
                if s.is_dir():
                    shutil.copytree(s, dest / s.name, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, dest / s.name)
                added += 1
            except Exception:
                pass
        self.status_var.set(f"Dropped {added} item(s) into "
                            f"{self.detail_game}/{self.detail_profile}.")
        if self.current_page == "game_hub":
            self.after(0, lambda: self.show_page(
                "game_hub", game=self.detail_game,
                profile=self.detail_profile))

    def _hide_to_tray(self):
        if not HAS_TRAY:
            self.destroy()
            return
        self.withdraw()
        if self._tray is not None:
            return
        img = Image.new("RGBA", (64, 64), (15, 15, 16, 255))
        for x in range(64):
            for y in range(64):
                if (x - 32) ** 2 + (y - 32) ** 2 < 22 ** 2:
                    img.putpixel((x, y), (200, 155, 60, 255))

        def show(_i=None, _it=None):
            self.after(0, lambda: (self.deiconify(), self.lift()))

        def apply_now(_i=None, _it=None):
            self.after(0, lambda: self.apply_profile(confirm=False))

        def quit_(_i=None, _it=None):
            try:
                self._tray.stop()
            except Exception:
                pass
            self.after(0, self.destroy)

        menu = TrayMenu(TrayItem("Show", show, default=True),
                        TrayItem("Apply Active Profile", apply_now),
                        TrayItem("Quit", quit_))
        self._tray = pystray.Icon("HydraCompanion", img,
                                  "Hydra Companion", menu)
        threading.Thread(target=self._tray.run, daemon=True).start()


    def _first_run_wizard(self):
        if self.cfg.get("first_run_complete"):
            return
        win = tk.Toplevel(self)
        win.title("Welcome to Hydra Companion")
        win.geometry("520x380")
        style_dialog(win)
        win.transient(self)
        win.grab_set()
        tk.Label(win,
                 text="Quick setup",
                 bg=PANEL, fg=ACCENT_2,
                 font=("Segoe UI", 16, "bold")).pack(pady=(20, 8))
        tk.Label(win,
                 text="1. Add a game (folder + mods path)\n"
                      "2. Search Catalogue (Modrinth, CurseForge, Nexus,\n"
                      "   SpaceDock, GitHub, Manual/Forum links)\n"
                      "3. Build profiles, then APPLY before launching Hydra\n\n"
                      "Optional: paste API keys in Settings for CurseForge/Nexus.",
                 bg=PANEL, fg=TEXT, justify="left",
                 font=("Segoe UI", 10), padx=24).pack(anchor="w")
        row = tk.Frame(win, bg=PANEL)
        row.pack(fill="x", padx=24, pady=16)
        tk.Label(row, text="Hydra exe", bg=PANEL, fg=MUTED).pack(anchor="w")
        ent = tk.Entry(row)
        style_entry(ent)
        ent.pack(fill="x", pady=4)
        found = find_hydra_exe()
        if found:
            ent.insert(0, found)

        def done(open_form=False):
            self.cfg["hydra_launcher_path"] = ent.get().strip()
            self.cfg["first_run_complete"] = True
            save_config(self.cfg)
            win.destroy()
            if open_form:
                self._open_game_form(None)

        bf = tk.Frame(win, bg=PANEL)
        bf.pack(fill="x", padx=24, pady=8)
        b1 = tk.Button(bf, text="Add my first game",
                       command=lambda: done(True))
        style_button(b1, primary=True)
        b1.pack(side="left")
        b2 = tk.Button(bf, text="Skip for now", command=lambda: done(False))
        style_button(b2, ghost=True)
        b2.pack(side="right")


def main():
    HydraCompanion().mainloop()


if __name__ == "__main__":
    main()
