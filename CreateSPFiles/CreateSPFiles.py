import json
import os
import re
import sys
import difflib
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

CRF_ROOT = r"D:\Documents\CRFs"

# ============================================================
#               THEME
# ============================================================
FONT = "Segoe UI"
COLOR_BG = "#eef1f5"
COLOR_CARD = "#ffffff"
COLOR_HEADER = "#1f2d3d"
COLOR_TEXT = "#1f2d3d"
COLOR_MUTED = "#6b7280"
COLOR_BORDER = "#cbd5e1"
COLOR_ACCENT = "#2563eb"
COLOR_GREEN = "#16a34a"
COLOR_TEAL = "#0d9488"
COLOR_PURPLE = "#7c3aed"
COLOR_ORANGE = "#ea580c"
LABEL_FONT = (FONT, 10)

# How many recent sample URLs to keep in the dropdown
MAX_RECENT_URLS = 5
# How many recent CRF numbers to keep in the dropdown
MAX_RECENT_CRFS = 10


def _darken(hex_color, factor=0.88):
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return "#{:02x}{:02x}{:02x}".format(int(r * factor), int(g * factor), int(b * factor))


def make_button(parent, text, command, bg, font_size=10, bold=False, pady=8, padx=14):
    """Flat button with a hover effect."""
    hover = _darken(bg)
    btn = tk.Button(
        parent,
        text=text,
        command=command,
        font=(FONT, font_size, "bold" if bold else "normal"),
        bg=bg,
        fg="white",
        activebackground=hover,
        activeforeground="white",
        relief="flat",
        bd=0,
        padx=padx,
        pady=pady,
        cursor="hand2",
    )
    btn.bind("<Enter>", lambda e: btn.config(bg=hover))
    btn.bind("<Leave>", lambda e: btn.config(bg=bg))
    return btn


def make_entry(parent, **kwargs):
    return tk.Entry(
        parent,
        font=LABEL_FONT,
        bg="#ffffff",
        fg=COLOR_TEXT,
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=COLOR_BORDER,
        highlightcolor=COLOR_ACCENT,
        insertbackground=COLOR_TEXT,
        **kwargs
    )


def make_field_label(parent, text, bold=True, size=10, fg=COLOR_TEXT, bg=COLOR_CARD):
    return tk.Label(
        parent,
        text=text,
        font=(FONT, size, "bold" if bold else "normal"),
        bg=bg,
        fg=fg,
        anchor="w",
    )


def make_check(parent, text, variable, command=None, fg=COLOR_TEXT):
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        command=command,
        font=(FONT, 10),
        bg=COLOR_CARD,
        fg=fg,
        activebackground=COLOR_CARD,
        selectcolor="#ffffff",
        anchor="w",
    )


def make_card(parent, title):
    card = tk.Frame(parent, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
    tk.Label(
        card,
        text=title,
        font=(FONT, 11, "bold"),
        bg=COLOR_HEADER,
        fg="white",
        anchor="w",
        padx=16,
        pady=11,
    ).pack(fill="x")
    return card


def make_code_box(parent, **kwargs):
    return ScrolledText(
        parent,
        font=("Consolas", 10),
        bg="#fafbfc",
        fg=COLOR_TEXT,
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=COLOR_BORDER,
        highlightcolor=COLOR_ACCENT,
        insertbackground=COLOR_TEXT,
        selectbackground="#bfdbfe",
        padx=8,
        pady=8,
        **kwargs
    )

def bind_select_all(widget):
    def _select_all(event):
        event.widget.tag_add("sel", "1.0", "end-1c")
        return "break"
    widget.bind("<Control-a>", _select_all)
    widget.bind("<Control-A>", _select_all)

def bind_horizontal_wheel(widgets, scroll_fn):
    """Shift+wheel / touchpad two-finger sideways scroll -> scroll_fn(units)."""
    acc = [0.0]

    def handler(event):
        if event.num == 6:            # Linux: scroll left
            units = -4
        elif event.num == 7:          # Linux: scroll right
            units = 4
        else:                         # Windows: Shift+wheel or touchpad horizontal
            acc[0] += -event.delta / 120 * 4     # 4 chars per wheel notch
            units = int(acc[0])
            acc[0] -= units
        if units:
            scroll_fn(units)
        return "break"

    for w in widgets:
        for seq in ("<Shift-MouseWheel>", "<Button-6>", "<Button-7>"):
            try:
                w.bind(seq, handler)
            except tk.TclError:
                pass

def add_tooltip(widget, text):
    tip = {"w": None}

    def show(_e):
        if tip["w"]:
            return
        w = tk.Toplevel(widget)
        w.wm_overrideredirect(True)
        w.wm_geometry("+%d+%d" % (widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height() + 4))
        tk.Label(w, text=text, font=(FONT, 9), bg=COLOR_HEADER, fg="white", padx=8, pady=3).pack()
        tip["w"] = w

    def hide(_e):
        if tip["w"]:
            tip["w"].destroy()
            tip["w"] = None

    widget.bind("<Enter>", show, add="+")
    widget.bind("<Leave>", hide, add="+")

def with_width_pad(left_rows, right_rows):
    """Add one blank row to both sides, padded to the longest line on either side.
    Both panes then have the same scrollable width, so a shared scrollbar stays aligned."""
    width = max((len(t.expandtabs()) for t, _tag in left_rows + right_rows), default=0)
    if not width:
        return left_rows, right_rows
    pad = (" " * width, "equal")
    return left_rows + [pad], right_rows + [pad]    

def spin_value(var, default):
    """Read a Spinbox IntVar safely (empty or invalid text falls back to default)."""
    try:
        return max(0, int(var.get()))
    except (tk.TclError, ValueError):
        return default

def make_spin(parent, var, command):
    """Themed Spinbox (ttk, styled in apply_ttk_theme)."""
    sb = ttk.Spinbox(parent, from_=0, to=500, width=4, textvariable=var,
                     font=(FONT, 9), command=command)
    sb.bind("<Return>", lambda _e: command())
    sb.bind("<FocusOut>", lambda _e: command())
    return sb    

def comment_flags(rows):
    """For each (text, tag) row: True if the line is blank or a comment (-- , //, /* ... */)."""
    flags, in_block = [], False
    for text, _tag in rows:
        s = text.strip()
        if not s:
            flags.append(True)
        elif in_block:
            flags.append(True)
            if "*/" in s:
                in_block = False
        elif s.startswith("--") or s.startswith("//"):
            flags.append(True)
        elif s.startswith("/*"):
            flags.append(True)
            if "*/" not in s[2:]:
                in_block = True
        else:
            flags.append(False)
    return flags

def apply_ttk_theme():
    """Style the ttk widgets (Notebook tabs, Combobox) to match."""
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("TNotebook", background=COLOR_BG, borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        font=(FONT, 10, "bold"),
        padding=(14, 5),
        background="#dbe2ea",
        foreground=COLOR_MUTED,
        borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", COLOR_CARD)],
        foreground=[("selected", COLOR_ACCENT)],
        expand=[("selected", [0, 0, 0, 0])],  # clam grows the selected tab by default
    )
    style.configure(
        "TCombobox",
        padding=5,
        fieldbackground="#ffffff",
        bordercolor=COLOR_BORDER,
        lightcolor=COLOR_BORDER,
        darkcolor=COLOR_BORDER,
        arrowcolor=COLOR_MUTED,
    )
    style.configure(
        "TSpinbox",
        padding=4,
        fieldbackground="#ffffff",
        foreground=COLOR_TEXT,
        background="#dbe2ea",
        bordercolor=COLOR_BORDER,
        lightcolor=COLOR_BORDER,
        darkcolor=COLOR_BORDER,
        arrowcolor=COLOR_MUTED,
        arrowsize=12,
    )
    style.map(
        "TSpinbox",
        bordercolor=[("focus", COLOR_ACCENT)],
        lightcolor=[("focus", COLOR_ACCENT)],
        darkcolor=[("focus", COLOR_ACCENT)],
        background=[("active", COLOR_BORDER)],
    )


# ============================================================
#               PATHS / CONFIG
# ============================================================

def resource_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(relative_path):
    # Bundled files (icon) live in PyInstaller's temp folder when frozen.
    return os.path.join(getattr(sys, "_MEIPASS", resource_base_dir()), relative_path)


CONFIG_FILE = os.path.join(resource_base_dir(), "sp_generator_config.json")
CONFIG_KEYS = ("crf_root", "crf_number", "crf_numbers", "sit_folder", "sample_urls")


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            result = {}
            for k in CONFIG_KEYS:
                v = data.get(k)
                if k == "sample_urls":
                    # Accept list of strings; also migrate old single last_sample_url if present
                    if isinstance(v, list):
                        result[k] = [u for u in v if isinstance(u, str) and u.strip()]
                    elif isinstance(data.get("last_sample_url"), str) and data["last_sample_url"].strip():
                        result[k] = [data["last_sample_url"].strip()]
                    else:
                        result[k] = []
                elif isinstance(v, str):
                    result[k] = v
                elif k == "crf_numbers":
                    result[k] = [c for c in v if isinstance(c, str) and c.strip()] if isinstance(v, list) else []
            return result
    except (OSError, ValueError):
        pass
    return {}


def save_config(cfg):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)
    os.replace(tmp, CONFIG_FILE)  # atomic, so a crash can't corrupt the config


# ============================================================
#               SQL / URL HELPERS
# ============================================================

def extract_sp_name(sql_text):
    """
    Return the stored procedure name from common SQL Server syntax, e.g.
    CREATE PROCEDURE dbo.usp_Test, ALTER PROC [dbo].[usp_Test],
    or CREATE OR ALTER PROCEDURE [dbo].[usp_Test].
    """
    pattern = re.compile(
        r"\b(?:CREATE\s+OR\s+ALTER|CREATE|ALTER)\s+"
        r"(?:PROC(?:EDURE)?)\s+"
        r"(?P<name>(?:\[[^\]]+\]|[A-Za-z_#@][\w$#@]*)"
        r"(?:\s*\.\s*(?:\[[^\]]+\]|[A-Za-z_#@][\w$#@]*)){0,2})",
        re.IGNORECASE,
    )
    match = pattern.search(sql_text or "")
    if not match:
        return None

    # Use the final identifier only for the output filename.
    qualified_name = match.group("name")
    parts = re.findall(r"\[([^\]]+)\]|([A-Za-z_#@][\w$#@]*)", qualified_name)
    identifiers = [bracketed or plain for bracketed, plain in parts]
    return identifiers[-1] if identifiers else None


def clean_sp_name(text):
    """'dbo.usp_Test' / '[dbo].[usp_Test].sql' -> 'usp_Test'"""
    text = (text or "").strip()
    if text.lower().endswith(".sql"):
        text = text[:-4]
    return text.split(".")[-1].strip("[] ")


def build_sp_urls(sample_url, sp_text):
    """Swap the .sql file name in a sample blob URL for each SP in sp_text."""
    match = re.match(r"^([^?#]*/)[^/?#]*\.sql([?#].*)?$", (sample_url or "").strip(), re.IGNORECASE)
    if not match:
        raise ValueError("The sample URL must end with a .sql file name.")
    folder, suffix = match.group(1), match.group(2) or ""

    urls, seen = [], set()
    for token in re.split(r"[\s,;]+", sp_text or ""):
        sp_name = clean_sp_name(token)
        if sp_name and sp_name.lower() not in seen and not sp_name.lower().endswith("_old"):
            seen.add(sp_name.lower())
            urls.append(folder + sp_name + ".sql" + suffix)
    return urls


def build_scripts_url(sample_url, crf_number, crf_folder_name):
    """
    Build the QA Scripts DeploymentQueries URL from a sample blob URL.
    Example:
      .../blob/master/QA%20Scripts/ECRF-6398/ECRF-6398-13072026_DeploymentQueries.sql
    Directory uses CRF number; filename uses the full CRF folder name (with date).
    """
    sample_url = (sample_url or "").strip()
    crf_number = (crf_number or "").strip()
    crf_folder_name = (crf_folder_name or "").strip()
    if not sample_url or not crf_number:
        return None

    match = re.match(r"^(.*?/blob/master/)", sample_url, re.IGNORECASE)
    if not match:
        return None

    base = match.group(1)
    file_stem = crf_folder_name or crf_number
    return base + "QA%20Scripts/" + crf_number + "/" + file_stem + "_DeploymentQueries.sql"


def read_sql(path):
    with open(path, "r", encoding="utf-8-sig") as file:
        return file.read()


def find_crf_folder(crf_root, crf_number):
    if not os.path.isdir(crf_root):
        raise FileNotFoundError("CRF root folder does not exist:\n" + crf_root)

    crf_number = crf_number.strip().upper()
    matches = []
    for folder in os.listdir(crf_root):
        full_path = os.path.join(crf_root, folder)
        if not os.path.isdir(full_path):
            continue
        name = folder.upper()
        if name == crf_number or name.startswith(crf_number + "-"):
            matches.append(full_path)

    if not matches:
        raise FileNotFoundError(
            "No CRF folder found for " + crf_number + " in:\n" + crf_root
        )
    if len(matches) > 1:
        raise ValueError(
            "Multiple CRF folders match. Enter the full CRF number:\n\n"
            + "\n".join(os.path.basename(path) for path in matches)
        )
    return matches[0]


def name_problem(expected, sql_text, label):
    """Return a message if the SP name inside sql_text differs from expected."""
    found = extract_sp_name(sql_text)
    if not found:
        return label + ": no CREATE/ALTER PROCEDURE found in the script."
    if found.lower() != expected.lower():
        return label + ": script is for '" + found + "' but expected '" + expected + "'."
    return None

# ============================================================
#               COMPARE WINDOW (full screen / multi-file)
# ============================================================

def replace_rows(old_chunk, new_chunk, threshold=0.6):
    """Align an uneven replace block: pair similar lines (shown as 'replace'),
    the rest become pure delete / insert."""
    left, right, j = [], [], 0
    for o in old_chunk:
        best, best_r = None, threshold
        for k in range(j, len(new_chunk)):
            r = difflib.SequenceMatcher(None, o, new_chunk[k], autojunk=False).ratio()
            if r > best_r:
                best, best_r = k, r
        if best is None:
            left.append((o, "delete"))
            right.append(("", "pad"))
            continue
        for t in new_chunk[j:best]:
            left.append(("", "pad"))
            right.append((t, "insert"))
        left.append((o, "replace"))
        right.append((new_chunk[best], "replace"))
        j = best + 1
    for t in new_chunk[j:]:
        left.append(("", "pad"))
        right.append((t, "insert"))
    return left, right


def paint_inline(left_w, right_w, left_rows, right_rows):
    """Highlight the exact characters that differ on paired 'replace' rows."""
    for w in (left_w, right_w):
        w.configure(state="normal")
        w.tag_remove("inline", "1.0", tk.END)
    for idx, ((lt, ltag), (rt, rtag)) in enumerate(zip(left_rows, right_rows), start=1):
        if ltag != "replace" or rtag != "replace":
            continue
        sm = difflib.SequenceMatcher(None, lt, rt, autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                continue
            if i2 > i1:
                left_w.tag_add("inline", "%d.%d" % (idx, i1), "%d.%d" % (idx, i2))
            if j2 > j1:
                right_w.tag_add("inline", "%d.%d" % (idx, j1), "%d.%d" % (idx, j2))
    for w in (left_w, right_w):
        w.configure(state="disabled")

def build_diff_rows(old, new):
    """Align two scripts side by side.
    Returns (left_rows, right_rows, change_rows); rows are (text, tag), equal length."""
    if not old.strip():
        lines = new.splitlines()
        right = [(line, "insert") for line in lines] or [("", "equal")]
        left = [("(no backup — New SP)", "pad")] + [("", "pad")] * (len(right) - 1)
        change_rows = list(range(len(lines)))
        return left, right, change_rows

    old_lines, new_lines = old.splitlines(), new.splitlines()
    left, right = [], []
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            left += [(t, "equal") for t in old_lines[i1:i2]]
            right += [(t, "equal") for t in new_lines[j1:j2]]
        elif tag == "replace" and (i2 - i1) == (j2 - j1):
            left += [(t, "replace") for t in old_lines[i1:i2]]
            right += [(t, "replace") for t in new_lines[j1:j2]]
        elif tag == "replace":                      # NEW
            l, r = replace_rows(old_lines[i1:i2], new_lines[j1:j2])
            left += l
            right += r
        else:  # delete / insert / uneven replace
            left += [(t, "delete") for t in old_lines[i1:i2]] + [("", "pad")] * (j2 - j1)
            right += [("", "pad")] * (i2 - i1) + [(t, "insert") for t in new_lines[j1:j2]]

    change_rows = [
        i for i, ((_a, lt), (_b, rt)) in enumerate(zip(left, right))
        if lt != "equal" or rt != "equal"
    ]
    return left, right, change_rows


def group_hunks(change_rows):
    """Consecutive changed rows -> blocks [(first_row, last_row), ...]."""
    hunks = []
    for row in change_rows:
        if hunks and row == hunks[-1][1] + 1:
            hunks[-1] = (hunks[-1][0], row)
        else:
            hunks.append((row, row))
    return hunks


class CompareWindow(tk.Toplevel):
    """Resizable side-by-side compare window.
    items: list of (name, old_text, new_text). Not transient on purpose, so the
    window keeps its own minimize / maximize buttons."""

    def __init__(self, app, items, start=0, fullscreen=False):
        super().__init__(app.root)
        self.app = app                      # shares the Diffs / Context settings with the main window
        self.items = items
        self.index = 0
        self.left_rows, self.right_rows = [], []
        self.change_rows, self.hunks = [], []
        self.display_to_full = []
        self.nav_index = -1
        self._syncing = False
        self._xsyncing = False

        self.title("Compare Files")
        self.geometry("1400x850")
        self.minsize(900, 500)
        self.configure(bg=COLOR_BG)
        self._build()
        if fullscreen:
            self.set_fullscreen(True)
        else:
            try:
                self.state("zoomed")        # open maximized; min/max/restore still work
            except tk.TclError:
                pass
        self.show_item(start)
        self.focus_set()

    # ---------- UI ----------
    def _build(self):
        app = self.app

        # Row 1: file navigation
        nav = tk.Frame(self, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
        nav.pack(fill="x", padx=10, pady=(10, 0))
        inner = tk.Frame(nav, bg=COLOR_CARD)
        inner.pack(fill="x", padx=10, pady=8)

        make_button(inner, "◀ Prev File", lambda: self.show_item(self.index - 1),
                    COLOR_PURPLE, pady=4, padx=10, font_size=9).pack(side="left")
        make_button(inner, "Next File ▶", lambda: self.show_item(self.index + 1),
                    COLOR_PURPLE, pady=4, padx=10, font_size=9).pack(side="left", padx=(6, 12))

        names = [name for name, _old, _new in self.items]
        # measure the longest name so the dropdown never truncates
        max_chars = max((len(n) for n in names), default=40)
        # Sensible range: never narrower than 45, never wider than 70
        width = min(max(max_chars + 1, 45), 70)

        self.file_combo = ttk.Combobox(
            inner, state="readonly", font=LABEL_FONT, width=width,
            values=names,
        )
        self.file_combo.pack(side="left", padx=(0, 8))
        self.file_combo.bind("<<ComboboxSelected>>", lambda _e: self.show_item(self.file_combo.current()))

        self.status_lbl = tk.Label(inner, font=(FONT, 9, "bold"), bg=COLOR_CARD, fg=COLOR_MUTED)
        self.status_lbl.pack(side="left", padx=(12, 0))

        self.fs_btn = make_button(inner, "Full Screen (F11)", self.toggle_fullscreen,
                                  COLOR_TEAL, pady=4, padx=10, font_size=9)
        self.fs_btn.pack(side="right")

        # Row 2: view controls (variables shared with the main Diff tab)
        bar = tk.Frame(self, bg=COLOR_CARD)
        bar.pack(fill="x", padx=10, pady=(8, 0))
        for text, mode in (("All", "all"), ("Diffs", "diffs")):
            tk.Radiobutton(
                bar, text=text, variable=app._diff_mode, value=mode, command=self.repaint,
                font=(FONT, 9, "bold"), bg=COLOR_CARD, fg=COLOR_TEXT, activebackground=COLOR_CARD,
                selectcolor="#dbeafe", indicatoron=False, padx=10, pady=3, relief="flat", borderwidth=1,
            ).pack(side="left", padx=(0, 2))
        tk.Checkbutton(
            bar, text="+ Context", variable=app._diff_context, command=self.on_context_toggle,
            font=(FONT, 9, "bold"), bg=COLOR_CARD, fg=COLOR_TEXT, activebackground=COLOR_CARD,
            selectcolor="#dbeafe", indicatoron=False, padx=10, pady=3, relief="flat", borderwidth=1,
        ).pack(side="left", padx=(0, 8))
        make_button(bar, "Prev Change", self.prev_change, COLOR_TEAL,
                    pady=3, padx=10, font_size=9).pack(side="left", padx=(0, 4))
        make_button(bar, "Next Change", self.next_change, COLOR_TEAL,
                    pady=3, padx=10, font_size=9).pack(side="left")
        tk.Label(
            bar, text="F7/F8 prev/next change   Ctrl+←/→ prev/next file   F11 full screen",
            font=(FONT, 9), bg=COLOR_CARD, fg=COLOR_MUTED,
        ).pack(side="right")

        # Row 3: context spinboxes
        bar2 = tk.Frame(self, bg=COLOR_CARD)
        bar2.pack(fill="x", padx=10, pady=(6, 0))

        def title(text, padx):
            tk.Label(bar2, text=text, font=(FONT, 9, "bold"), bg=COLOR_CARD, fg=COLOR_TEXT).pack(
                side="left", padx=padx)

        def spin(label, var):
            tk.Label(bar2, text=label, font=(FONT, 9), bg=COLOR_CARD, fg=COLOR_TEXT).pack(
                side="left", padx=(0, 4))
            make_spin(bar2, var, self.repaint).pack(side="left", padx=(0, 12))

        title("Context lines:", (0, 8))
        spin("Before", app._ctx_before)
        spin("After", app._ctx_after)
        title("Comment-only changes:", (8, 8))
        spin("Before", app._ctx_cmt_before)
        spin("After", app._ctx_cmt_after)

        # Panes: one shared vertical scrollbar, one horizontal scrollbar per side
        panes = tk.Frame(self, bg=COLOR_CARD)
        panes.pack(fill="both", expand=True, padx=10, pady=10)
        panes.columnconfigure(0, weight=1)
        panes.columnconfigure(1, weight=1)
        panes.rowconfigure(1, weight=1)

        make_field_label(panes, "Backup (old)").grid(row=0, column=0, sticky="w", pady=(0, 4))
        make_field_label(panes, "Modified (new)").grid(row=0, column=1, sticky="w", padx=(8, 0), pady=(0, 4))

        self.left = self._make_text(panes)
        self.left.grid(row=1, column=0, sticky="nsew", padx=(0, 4))
        self.right = self._make_text(panes)
        self.right.grid(row=1, column=1, sticky="nsew", padx=(4, 0))

        self.vbar = ttk.Scrollbar(panes, orient="vertical", command=self._yview)
        self.vbar.grid(row=1, column=2, sticky="ns")
        self.hbar = ttk.Scrollbar(panes, orient="horizontal", command=self._xview)
        self.hbar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        self.left.configure(xscrollcommand=self._on_xscroll, yscrollcommand=self._on_yscroll)
        self.right.configure(xscrollcommand=self._on_xscroll, yscrollcommand=self._on_yscroll)
        bind_horizontal_wheel(
            (self.left, self.right),
            lambda n: self._xview("scroll", n, "units"),
        )

        self.bind("<F8>", lambda _e: self.next_change())
        self.bind("<F7>", lambda _e: self.prev_change())
        self.bind("<Control-Right>", lambda _e: self.show_item(self.index + 1))
        self.bind("<Control-Left>", lambda _e: self.show_item(self.index - 1))
        self.bind("<F11>", self.toggle_fullscreen)
        self.bind("<Escape>", lambda _e: self.set_fullscreen(False))

    def _make_text(self, parent):
        w = tk.Text(
            parent, font=("Consolas", 10), bg="#fafbfc", fg=COLOR_TEXT, relief="flat", bd=0,
            wrap=tk.NONE, highlightthickness=1, highlightbackground=COLOR_BORDER,
            highlightcolor=COLOR_ACCENT, selectbackground="#bfdbfe", padx=8, pady=8,
        )
        w.tag_configure("equal", background="#fafbfc", foreground=COLOR_TEXT)
        w.tag_configure("insert", background="#dcfce7", foreground="#14532d")
        w.tag_configure("delete", background="#fee2e2", foreground="#7f1d1d")
        w.tag_configure("replace", background="#fef9c3", foreground="#713f12")
        w.tag_configure("pad", background="#f1f5f9", foreground="#94a3b8")
        # w.tag_configure("inline", background="#fde047", foreground="#422006")
        w.tag_configure("current_change", background="#93c5fd")
        # w.tag_raise("sel")

        w.tag_configure("inline", background="#facc15", foreground="#422006")
        w.tag_raise("inline")          # above the line-level replace colour
        w.tag_raise("current_change")  # keep navigation highlight on top
        w.tag_raise("sel")

        w.configure(state="disabled")
        return w

    # ---------- scrolling ----------
    def _yview(self, *args):
        self.left.yview(*args)
        self.right.yview(*args)

    def _xview(self, *args):
        self.left.xview(*args)
        self.right.xview(*args)

    def _on_yscroll(self, first, last):
        self.vbar.set(first, last)
        if self._syncing:
            return
        self._syncing = True
        try:
            self.left.yview_moveto(first)
            self.right.yview_moveto(first)
        finally:
            self._syncing = False

    def _on_xscroll(self, first, last):
        self.hbar.set(first, last)
        if self._xsyncing:
            return
        self._xsyncing = True
        try:
            self.left.xview_moveto(first)
            self.right.xview_moveto(first)
        finally:
            self._xsyncing = False

    def toggle_fullscreen(self, _event=None):
        self.set_fullscreen(not bool(self.attributes("-fullscreen")))

    def set_fullscreen(self, on):
        self.attributes("-fullscreen", on)
        self.fs_btn.config(text="Exit Full Screen (Esc)" if on else "Full Screen (F11)")

    # ---------- content ----------
    def show_item(self, i):
        i %= len(self.items)
        self.index = i
        name, old, new = self.items[i]
        self.left_rows, self.right_rows, self.change_rows = build_diff_rows(old, new)
        self.hunks = group_hunks(self.change_rows)
        self.nav_index = -1

        self.file_combo.current(i)
        self.title("Compare Files - %s  (%d / %d)" % (name, i + 1, len(self.items)))
        if not old.strip():
            info = "new SP"
        elif self.hunks:
            info = "%d change block(s)" % len(self.hunks)
        else:
            info = "identical"
        self.status_lbl.config(text="%d / %d    %s" % (i + 1, len(self.items), info))

        self.repaint()
        self.after_idle(self.next_change)   # jump to the first change

    def on_context_toggle(self):
        if self.app._diff_context.get() and self.app._diff_mode.get() == "all":
            self.app._diff_mode.set("diffs")
        self.repaint()

    def repaint(self):
        app = self.app
        n = len(self.left_rows)
        if app._diff_mode.get() == "all" or not self.change_rows:
            show = list(range(n))
        elif not app._diff_context.get():
            show = list(self.change_rows)
        else:
            left_cmt = comment_flags(self.left_rows)
            right_cmt = comment_flags(self.right_rows)
            before = spin_value(app._ctx_before, 5)
            after = spin_value(app._ctx_after, 5)
            c_before = spin_value(app._ctx_cmt_before, 2)
            c_after = spin_value(app._ctx_cmt_after, 2)
            keep = set()
            for h0, h1 in self.hunks:
                only_comments = all(left_cmt[r] and right_cmt[r] for r in range(h0, h1 + 1))
                b, a = (c_before, c_after) if only_comments else (before, after)
                keep.update(range(max(0, h0 - b), min(n - 1, h1 + a) + 1))
            show = sorted(keep)

        self.display_to_full = show
        left_view, right_view = with_width_pad(
            [self.left_rows[r] for r in show], [self.right_rows[r] for r in show]
        )
        self._paint(self.left, left_view)
        self._paint(self.right, right_view)
        paint_inline(self.left, self.right, left_view, right_view)

    @staticmethod
    def _paint(widget, rows):
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        for text, tag in rows:
            widget.insert(tk.END, text + "\n", tag)
        widget.configure(state="disabled")

    # ---------- change navigation (block-wise) ----------
    def next_change(self):
        self._step(1)

    def prev_change(self):
        self._step(-1)

    def _step(self, direction):
        if not self.hunks:
            return
        if self.nav_index < 0:
            self.nav_index = 0 if direction > 0 else len(self.hunks) - 1
        else:
            self.nav_index = (self.nav_index + direction) % len(self.hunks)
        first, last = self.hunks[self.nav_index]
        try:
            d_first = self.display_to_full.index(first) + 1
            d_last = self.display_to_full.index(last) + 1
        except ValueError:
            return
        for w in (self.left, self.right):
            w.tag_remove("current_change", "1.0", tk.END)
            w.tag_add("current_change", "%d.0" % d_first, "%d.end" % d_last)
        self._yview("%d.0" % max(1, d_first - 3))
        self.after(1200, lambda: [w.tag_remove("current_change", "1.0", tk.END)
                                  for w in (self.left, self.right)])

# ============================================================
#               MAIN APP
# ============================================================

class SPFileGenerator:
    def __init__(self, root):
        self.root = root
        self.root.title("Stored Procedure SQL File Generator")
        self.root.geometry("1100x780")
        self.root.minsize(900, 640)
        self.root.configure(bg=COLOR_BG)
        apply_ttk_theme()

        cfg = load_config()
        self.crf_root_var = tk.StringVar(value=cfg.get("crf_root") or CRF_ROOT)
        self.crf_var = tk.StringVar(value=cfg.get("crf_number", ""))
        self.sit_folder_var = tk.StringVar(value=cfg.get("sit_folder", ""))
        self.sp_name_var = tk.StringVar()
        self.is_new_var = tk.BooleanVar(value=False)
        # When True and is_new: on Generate, use SP name from Modified tab content
        # and rename the on-disk file (replace old filename with the new name).
        self.rename_from_script_var = tk.BooleanVar(value=False)
        # Path of the SP file that was opened (so we can delete/rename it if name changes).
        self._opened_sp_path = None
        # Recent sample URLs for the URL generator dropdown (most recent first)
        self.sample_urls = list(cfg.get("sample_urls") or [])

        # Recent CRF numbers for the dropdown (most recent first)
        self.crf_numbers = list(cfg.get("crf_numbers") or [])
        last_crf = cfg.get("crf_number", "").strip()
        if last_crf and last_crf.lower() not in [c.lower() for c in self.crf_numbers]:
            self.crf_numbers.insert(0, last_crf)   # migrate existing single saved value

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def save_settings(self):
        try:
            save_config({
                "crf_root": self.crf_root_var.get().strip(),
                "crf_number": self.crf_var.get().strip(),
                "crf_numbers": self.crf_numbers[:MAX_RECENT_CRFS],
                "sit_folder": self.sit_folder_var.get().strip(),
                "sample_urls": self.sample_urls[:MAX_RECENT_URLS],
            })
        except OSError:
            pass  # settings are a convenience; never block the tool

    def remember_sample_url(self, url):
        """Push url to the front of recent sample URLs (deduped) and persist."""
        url = (url or "").strip()
        if not url:
            return
        # Keep unique, most-recent first
        self.sample_urls = [url] + [u for u in self.sample_urls if u.lower() != url.lower()]
        self.sample_urls = self.sample_urls[:MAX_RECENT_URLS]
        self.save_settings()

    def remember_crf_number(self, crf):
        """Push crf to the front of the recent CRF numbers (deduped) and persist."""
        crf = (crf or "").strip()
        if not crf:
            return
        self.crf_numbers = ([crf] + [c for c in self.crf_numbers if c.lower() != crf.lower()])[:MAX_RECENT_CRFS]
        self.crf_combo["values"] = self.crf_numbers
        self.save_settings()

    def on_close(self):
        self.save_settings()
        self.root.destroy()

    def open_output_folder(self):
        output_dir = self.sit_folder_var.get().strip()
        if not output_dir or not os.path.isdir(output_dir):
            messagebox.showwarning(
                "Output Folder Required",
                "Select or find the exact CRF SIT output folder first.",
            )
            return
        os.startfile(output_dir)

    def open_fullscreen_diff(self):
        modified = self.modified_text.get("1.0", "end-1c")
        backup = "" if self.is_new_var.get() else self.backup_text.get("1.0", "end-1c")
        if not modified.strip():
            messagebox.showwarning("Modified SP Required", "Enter or open the modified SP script.")
            return
        name = clean_sp_name(self.sp_name_var.get()) or extract_sp_name(modified) or "Current SP"
        CompareWindow(self, [(name, backup, modified)], fullscreen=True)

    def open_compare_files(self):
        output_dir = self.sit_folder_var.get().strip()
        if not output_dir or not os.path.isdir(output_dir):
            messagebox.showwarning(
                "Output Folder Required",
                "Select or find the exact CRF SIT output folder first.",
            )
            return

        backup_dir = os.path.join(output_dir, "Backup")
        items = []
        for name in sorted(os.listdir(output_dir), key=str.lower):
            low = name.lower()
            if not low.endswith(".sql") or low.endswith("_old.sql"):
                continue
            new_path = os.path.join(output_dir, name)
            if not os.path.isfile(new_path):
                continue
            backup_path = os.path.join(backup_dir, name)
            try:
                if os.path.isfile(backup_path):
                    old_text, label = read_sql(backup_path), name[:-4]
                else:
                    old_text, label = "", name[:-4] + "  [New]"   # no backup -> new SP
                items.append((label, old_text, read_sql(new_path)))
            except Exception as ex:
                messagebox.showerror("Read Failed", name + ": " + str(ex))
                return

        # Modified SPs first, new SPs below; alphabetical within each group
        items.sort(key=lambda item: (item[0].endswith("  [New]"), item[0].lower()))

        if not items:
            messagebox.showinfo(
                "Compare Files",
                "No SQL files found in the SIT Scripts folder.",
            )
            return

        current = clean_sp_name(self.sp_name_var.get()).lower()
        start = next(
            (i for i, item in enumerate(items)
             if item[0].replace("  [New]", "").lower() == current),
            0,
        )
        CompareWindow(self, items, start)    

    def _build_ui(self):
        # ---------- Top card: CRF & SP details ----------
        card = make_card(self.root, "CRF & Stored Procedure")
        card.pack(fill="x", padx=14, pady=(14, 8))

        top = tk.Frame(card, bg=COLOR_CARD)
        top.pack(fill="x", padx=18, pady=14)
        top.columnconfigure(1, weight=1)
        top.columnconfigure(2, minsize=170)  # keeps the right-hand buttons the same width

        make_field_label(top, "CRF Number").grid(row=0, column=0, sticky="w", padx=(0, 14), pady=5)
        self.crf_combo = ttk.Combobox(top, textvariable=self.crf_var, font=LABEL_FONT,
                                      values=self.crf_numbers)
        crf_entry = self.crf_combo
        crf_entry.grid(row=0, column=1, sticky="ew", pady=5, ipady=5)
        crf_entry.bind("<Return>", lambda _event: self.resolve_sit_folder())
        crf_entry.bind("<<ComboboxSelected>>", lambda _event: self.resolve_sit_folder())
        make_button(top, "Find CRF SIT Folder", self.resolve_sit_folder, COLOR_TEAL, pady=6).grid(
            row=0, column=2, sticky="ew", padx=(10, 0), pady=5
        )

        make_field_label(top, "CRF Root").grid(row=1, column=0, sticky="w", padx=(0, 14), pady=5)
        make_entry(top, textvariable=self.crf_root_var).grid(row=1, column=1, sticky="ew", pady=5, ipady=5)
        make_button(top, "Browse", self.browse_crf_root, COLOR_ACCENT, pady=6).grid(
            row=1, column=2, sticky="ew", padx=(10, 0), pady=5
        )

        make_field_label(top, "Output SIT Folder").grid(row=2, column=0, sticky="w", padx=(0, 14), pady=5)
        make_entry(top, textvariable=self.sit_folder_var).grid(row=2, column=1, sticky="ew", pady=5, ipady=5)
        make_button(top, "Browse", self.browse_sit_folder, COLOR_ACCENT, pady=6).grid(
            row=2, column=2, sticky="ew", padx=(10, 0), pady=5
        )

        make_field_label(top, "SP Name").grid(row=3, column=0, sticky="w", padx=(0, 14), pady=5)
        sp_entry = make_entry(top, textvariable=self.sp_name_var)
        sp_entry.grid(row=3, column=1, sticky="ew", pady=5, ipady=5)
        sp_entry.bind("<Return>", lambda _event: self.open_sp_files())
        make_button(top, "Open SP Files", self.open_sp_files, COLOR_TEAL, bold=True, pady=6).grid(
            row=3, column=2, sticky="ew", padx=(10, 0), pady=5
        )

        make_check(
            top,
            "New SP (do not create Backup folder/file)",
            self.is_new_var,
            command=self.on_new_toggle,
        ).grid(row=4, column=1, sticky="w", pady=(6, 0))

        # Shown only when an existing single-file SP was opened (is_new).
        # If checked: on Generate use the SP name from the Modified tab script
        # and rename the on-disk file (remove old filename, write under new name).
        self.rename_cb = make_check(
            top,
            "Rename file to match SP name in Modified tab (replace old file)",
            self.rename_from_script_var,
            fg="#9a3412",
        )
        self.rename_cb.grid(row=5, column=1, sticky="w", pady=(0, 0))
        self.rename_cb.grid_remove()  # hidden until an existing New SP is loaded

        note = (
            "Enter the SP name and click Open SP Files: the modified script is read from SIT Scripts and "
            "the previous version from SIT Scripts\\Backup (if it exists; otherwise it is treated as a new SP). "
            "To create files, paste the scripts in the tabs; the name is detected from CREATE/ALTER PROCEDURE "
            "when SP Name is empty. When a single-file (New) SP is opened, the rename checkbox appears — "
            "check it to take the procedure name from the Modified tab and rename the file on disk."
        )
        note_label = tk.Label(
            top,
            text=note,
            wraplength=900,
            justify="left",
            font=(FONT, 9),
            bg="#f1f5f9",
            fg=COLOR_MUTED,
            padx=10,
            pady=8,
            anchor="w",
        )
        note_label.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        top.bind("<Configure>", lambda e: note_label.config(wraplength=max(300, e.width - 30)))

        # ---------- Bottom action bar (packed first so it is never pushed off-screen) ----------
        bottom = tk.Frame(self.root, bg=COLOR_BG)
        bottom.pack(side="bottom", fill="x", padx=14, pady=(0, 14))

        # make_button(bottom, "Verify SQL Files", self.verify_files, COLOR_PURPLE).pack(side="left")
        # make_button(bottom, "Open Folder", self.open_output_folder, COLOR_TEAL).pack(side="left", padx=(8, 0))
        # make_button(bottom, "Clear", self.clear_inputs, COLOR_ORANGE).pack(side="left", padx=(8, 0))
        # make_button(bottom, "Delete SP", self.delete_sp_files, "#dc2626").pack(side="left", padx=(8, 0))
        # make_button(bottom, "Generate URLs", self.open_url_generator, COLOR_ACCENT).pack(side="left", padx=(8, 0))
        # make_button(bottom, "Compare Files", self.open_compare_files, COLOR_PURPLE).pack(side="left", padx=(8, 0))
        # self.generate_button = make_button(
        #     bottom, "Generate SQL Files", self.generate, COLOR_GREEN,
        #     font_size=12, bold=True, pady=10, padx=24
        # )
        # self.generate_button.pack(side="right")

        # File actions
        file_actions = tk.Frame(bottom, bg=COLOR_BG)
        file_actions.pack(side="left")

        make_button(file_actions, "Verify SQL Files", self.verify_files, COLOR_PURPLE).pack(side="left")
        make_button(file_actions, "Open Folder", self.open_output_folder, COLOR_TEAL).pack(side="left", padx=(8, 0))
        make_button(file_actions, "Generate URLs", self.open_url_generator, COLOR_ACCENT).pack(side="left", padx=(8, 0))
        make_button(file_actions, "Compare Files", self.open_compare_files, COLOR_PURPLE).pack(side="left", padx=(8, 0))

        # Separator between file actions and cleanup actions
        tk.Frame(bottom, bg=COLOR_BORDER, width=5).pack(side="left", fill="y", padx=14, pady=5)

        # Cleanup actions
        cleanup_actions = tk.Frame(bottom, bg=COLOR_BG)
        cleanup_actions.pack(side="left")

        make_button(cleanup_actions, "Clear", self.clear_inputs, COLOR_ORANGE).pack(side="left")
        make_button(cleanup_actions, "Delete SP", self.delete_sp_files, "#dc2626").pack(side="left", padx=(8, 0))

        # Main action, kept at the far right
        self.generate_button = make_button(
            bottom,"Generate SQL Files",self.generate,COLOR_GREEN,
            font_size=11,bold=True,pady=8.5,padx=20,
        )
        self.generate_button.pack(side="right")

        # Separator between cleanup actions and the main action
        tk.Frame(bottom, bg=COLOR_BORDER, width=1).pack(
            side="right", fill="y", padx=14, pady=5
        )

        # ---------- Notebook: Modified / Backup / Diff ----------
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        modified_frame = tk.Frame(self.notebook, bg=COLOR_CARD)
        backup_frame = tk.Frame(self.notebook, bg=COLOR_CARD)
        diff_frame = tk.Frame(self.notebook, bg=COLOR_CARD)

        self.notebook.add(modified_frame, text="Modified SP")
        self.notebook.add(backup_frame, text="Backup SP")
        self.notebook.add(diff_frame, text="Diff")

        self.modified_text = make_code_box(modified_frame, wrap=tk.NONE, undo=True)
        self.modified_text.pack(fill="both", expand=True, padx=10, pady=10)

        self.backup_text = make_code_box(backup_frame, wrap=tk.NONE, undo=True)
        self.backup_text.pack(fill="both", expand=True, padx=10, pady=10)

        # ---- Diff tab: side-by-side (Backup | Modified) ----
        diff_toolbar = tk.Frame(diff_frame, bg=COLOR_CARD)
        diff_toolbar.pack(fill="x", padx=10, pady=(10, 0))

        # View mode: All / Only Diffs / Context
        self._diff_mode = tk.StringVar(value="all")
        self._diff_context = tk.BooleanVar(value=False)
        self._ctx_before = tk.IntVar(value=5)
        self._ctx_after = tk.IntVar(value=5)
        self._ctx_cmt_before = tk.IntVar(value=2)
        self._ctx_cmt_after = tk.IntVar(value=2)
        mode_row = tk.Frame(diff_toolbar, bg=COLOR_CARD)
        mode_row.pack(side="left")

        bind_select_all(self.modified_text)
        bind_select_all(self.backup_text)

        def _mode_btn(text, mode):
            return tk.Radiobutton(
                mode_row,
                text=text,
                variable=self._diff_mode,
                value=mode,
                command=self._apply_diff_view,
                font=(FONT, 9, "bold"),
                bg=COLOR_CARD,
                fg=COLOR_TEXT,
                activebackground=COLOR_CARD,
                selectcolor="#dbeafe",
                indicatoron=False,
                padx=10,
                pady=3,
                relief="flat",
                borderwidth=1,
            )

        _mode_btn("All", "all").pack(side="left", padx=(0, 2))
        _mode_btn("Diffs", "diffs").pack(side="left", padx=(0, 2))
        tk.Checkbutton(
            mode_row,
            text="+ Context",
            variable=self._diff_context,
            command=self._on_context_toggle,
            font=(FONT, 9, "bold"),
            bg=COLOR_CARD,
            fg=COLOR_TEXT,
            activebackground=COLOR_CARD,
            selectcolor="#dbeafe",
            indicatoron=False,
            padx=10,
            pady=3,
            relief="flat",
            borderwidth=1,
        ).pack(side="left", padx=(0, 8))

        make_button(
            diff_toolbar, "Prev Change", self.diff_prev_change,
            COLOR_TEAL, pady=3, padx=10, font_size=9,
        ).pack(side="left", padx=(0, 4))
        make_button(
            diff_toolbar, "Next Change", self.diff_next_change,
            COLOR_TEAL, pady=3, padx=10, font_size=9,
        ).pack(side="left", padx=(0, 8))

        make_button(
            diff_toolbar, "Refresh Diff", self.refresh_diff,
            COLOR_ACCENT, pady=3, padx=10, font_size=9,
        ).pack(side="left", padx=(0, 4))
        fs_btn = make_button(
            diff_toolbar, "⛶", self.open_fullscreen_diff,
            COLOR_PURPLE, pady=1, padx=8, font_size=12,
        )
        fs_btn.pack(side="left")
        add_tooltip(fs_btn, "Full screen compare of this SP")

        tk.Label(
            diff_toolbar,
            text="  Red = removed   Yellow = changed   Green = added",
            font=(FONT, 9), bg=COLOR_CARD, fg=COLOR_MUTED,
        ).pack(side="left", padx=(8, 0))

        ctx_bar = tk.Frame(diff_frame, bg=COLOR_CARD)
        ctx_bar.pack(fill="x", padx=10, pady=(6, 0))

        def _ctx_title(text, padx):
            tk.Label(ctx_bar, text=text, font=(FONT, 9, "bold"), bg=COLOR_CARD, fg=COLOR_TEXT).pack(
                side="left", padx=padx
            )

        def _spin(label, var):
            tk.Label(ctx_bar, text=label, font=(FONT, 9), bg=COLOR_CARD, fg=COLOR_TEXT).pack(
                side="left", padx=(0, 4)
            )
            make_spin(ctx_bar, var, self._apply_diff_view).pack(side="left", padx=(0, 12))

        _ctx_title("Context lines:", (0, 8))
        _spin("Before", self._ctx_before)
        _spin("After", self._ctx_after)
        _ctx_title("Comment-only changes:", (8, 8))
        _spin("Before", self._ctx_cmt_before)
        _spin("After", self._ctx_cmt_after)

        # Full aligned diff rows (before filtering by All/Diffs/Context)
        self._diff_full_left = []
        self._diff_full_right = []
        self._diff_change_rows = []   # row indices in the FULL lists that are changes
        self._diff_nav_index = -1     # index into _diff_change_rows for Next/Prev
        self._diff_display_to_full = []  # maps visible row -> full row index

        # ---- Side-by-side panes ----
        diff_panes = tk.Frame(diff_frame, bg=COLOR_CARD)
        diff_panes.pack(fill="both", expand=True, padx=10, pady=10)
        diff_panes.columnconfigure(0, weight=1)
        diff_panes.columnconfigure(1, weight=1)
        diff_panes.rowconfigure(1, weight=1)

        make_field_label(diff_panes, "Backup (old)", size=10).grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        make_field_label(diff_panes, "Modified (new)", size=10).grid(
            row=0, column=1, sticky="w", padx=(8, 0), pady=(0, 4)
        )

        self.diff_left = make_code_box(diff_panes, wrap=tk.NONE, undo=False)
        self.diff_left.grid(row=1, column=0, sticky="nsew", padx=(0, 4))
        self.diff_right = make_code_box(diff_panes, wrap=tk.NONE, undo=False)
        self.diff_right.grid(row=1, column=1, sticky="nsew", padx=(4, 0))

        # Shared horizontal scrollbar: both panes scroll sideways together
        self._xsyncing = False
        self.diff_hbar = ttk.Scrollbar(diff_panes, orient="horizontal", command=self._diff_xview)
        self.diff_hbar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.diff_left.configure(xscrollcommand=self._on_diff_xscroll)
        self.diff_right.configure(xscrollcommand=self._on_diff_xscroll)
        bind_horizontal_wheel(
            (self.diff_left, self.diff_right),
            lambda n: self._diff_xview("scroll", n, "units"),
        )

        # Read-only feel for diff panes
        for w in (self.diff_left, self.diff_right):
            w.configure(state="disabled")

        self._configure_diff_tags()
        self._bind_diff_scroll_sync()

    def browse_crf_root(self):
        folder = filedialog.askdirectory(
            title="Select CRF root folder",
            initialdir=self.crf_root_var.get() if os.path.isdir(self.crf_root_var.get()) else None,
        )
        if folder:
            self.crf_root_var.set(folder)

    def browse_sit_folder(self):
        folder = filedialog.askdirectory(title="Select CRF SIT output folder")
        if folder:
            self.sit_folder_var.set(folder)

    def resolve_sit_folder(self):
        crf = self.crf_var.get().strip()
        if not crf:
            messagebox.showwarning("CRF Required", "Enter a CRF number first.")
            return

        try:
            crf_folder = find_crf_folder(self.crf_root_var.get().strip(), crf)
        except Exception as ex:
            messagebox.showerror("CRF Folder Not Found", str(ex))
            return

        self.remember_crf_number(crf)

        # The CRF deployment folder is named "SIT Scripts".
        candidates = [
            os.path.join(crf_folder, "SIT Scripts"),
        ]
        existing = [path for path in candidates if os.path.isdir(path)]
        if len(existing) == 1:
            self.sit_folder_var.set(existing[0])
        elif len(existing) > 1:
            messagebox.showwarning(
                "Select SIT Folder",
                "More than one SIT folder was found. Please select the correct one using Browse.",
            )
        else:
            messagebox.showwarning(
                "SIT Folder Not Found",
                "No SIT Scripts folder was found under:\n\n"
                + crf_folder
                + "\n\nUse Browse to select the exact CRF SIT folder.",
            )

    def on_new_toggle(self):
        if self.is_new_var.get():
            self.backup_text.delete("1.0", tk.END)
            # Keep rename checkbox visible only if we previously opened an existing file
            if self._opened_sp_path:
                self.rename_cb.grid()
            else:
                self.rename_cb.grid_remove()
                self.rename_from_script_var.set(False)
        else:
            self.rename_cb.grid_remove()
            self.rename_from_script_var.set(False)

    def set_update_mode(self, update):
        """'Update SQL Files' while editing existing files, 'Generate SQL Files' otherwise."""
        self.generate_button.config(text="Update SQL Files" if update else "Generate SQL Files")

    def _set_text(self, widget, content):
        widget.delete("1.0", tk.END)
        widget.insert("1.0", content)

    def open_sp_files(self):
        output_dir = self.sit_folder_var.get().strip()
        if not output_dir or not os.path.isdir(output_dir):
            messagebox.showwarning(
                "Output Folder Required",
                "Enter a CRF number and click Find CRF SIT Folder first.",
            )
            return

        sp_name = clean_sp_name(self.sp_name_var.get())
        if not sp_name:
            messagebox.showwarning("SP Name Required", "Enter the stored procedure name.")
            return
        self.sp_name_var.set(sp_name)

        modified_path = os.path.join(output_dir, sp_name + ".sql")
        backup_path = os.path.join(output_dir, "Backup", sp_name + ".sql")

        self.modified_text.delete("1.0", tk.END)
        self.backup_text.delete("1.0", tk.END)
        self.set_update_mode(False)
        self._opened_sp_path = None
        self.rename_from_script_var.set(False)
        self.rename_cb.grid_remove()

        if not os.path.isfile(modified_path):
            messagebox.showwarning(
                "SP Not Found",
                "No file found for " + sp_name + " in:\n\n" + output_dir
                + "\n\nPaste the scripts in the tabs and click Update SQL Files to create it.",
            )
            return

        try:
            self._set_text(self.modified_text, read_sql(modified_path))
            self.set_update_mode(True)
            self._opened_sp_path = modified_path
            if os.path.isfile(backup_path):
                self._set_text(self.backup_text, read_sql(backup_path))
                self.is_new_var.set(False)
                self.notebook.select(0)
                self.refresh_diff()
                if not self.is_new_var.get() and self.backup_text.get("1.0", "end-1c").strip():
                    # optional: jump to Diff only when something actually changed
                    old_l = self.backup_text.get("1.0", "end-1c").splitlines()
                    new_l = self.modified_text.get("1.0", "end-1c").splitlines()
                    if old_l != new_l:
                        self.notebook.select(2)  # Diff tab index
                self.rename_cb.grid_remove()
                self.rename_from_script_var.set(False)
            else:
                # No backup file -> single file, treated as a new SP.
                self.is_new_var.set(True)
                self.rename_cb.grid()  # show the rename option
                messagebox.showinfo(
                    "New SP",
                    "No Backup file exists for " + sp_name + ".\n\n"
                    "Opened the single SIT Scripts file and marked it as a New SP.\n\n"
                    "If you change the procedure name inside the Modified tab, check\n"
                    "\"Rename file to match SP name in Modified tab\" so the file is\n"
                    "renamed (old file replaced) on Update.",
                )
                self.refresh_diff()

        except Exception as ex:
            messagebox.showerror("Read Failed", str(ex))

    def clear_inputs(self, notify=True):
        # if not messagebox.askyesno("Clear", "Clear both SQL tabs and the SP name?"):
        #     return
        self._diff_full_left = []
        self._diff_full_right = []
        self._diff_change_rows = []
        self._diff_nav_index = -1
        self._diff_display_to_full = []
        self._diff_mode.set("all")
        self._diff_context.set(False)
        self._set_diff_text(self.diff_left, [])
        self._set_diff_text(self.diff_right, [])
        self.modified_text.delete("1.0", tk.END)
        self.backup_text.delete("1.0", tk.END)
        self.sp_name_var.set("")
        self.is_new_var.set(False)
        self.rename_from_script_var.set(False)
        self.rename_cb.grid_remove()
        self._opened_sp_path = None
        self.set_update_mode(False)
        self.notebook.select(0)
        if notify:
            messagebox.showinfo(
                "Clear",
                "The form has been cleared to Generate SQL Files mode."
            )

    def delete_sp_files(self):
        """Delete the SIT Scripts file and its Backup file (whichever exist) for the SP Name."""
        output_dir = self.sit_folder_var.get().strip()
        if not output_dir or not os.path.isdir(output_dir):
            messagebox.showwarning(
                "Output Folder Required",
                "Select or find the exact CRF SIT output folder first.",
            )
            return

        sp_name = clean_sp_name(self.sp_name_var.get())
        if not sp_name:
            messagebox.showwarning("SP Name Required", "Enter the stored procedure name.")
            return

        targets = [
            path for path in (
                os.path.join(output_dir, sp_name + ".sql"),
                os.path.join(output_dir, "Backup", sp_name + ".sql"),
            ) if os.path.isfile(path)
        ]
        if not targets:
            messagebox.showinfo("Nothing to Delete", "No files found for " + sp_name + ".")
            return

        if not messagebox.askyesno(
            "Delete SP Files",
            "Permanently delete these file(s)?\n\n" + "\n".join(targets)
            + "\n\nThis cannot be undone.",
            icon="warning",
        ):
            return

        try:
            for path in targets:
                os.remove(path)
        except OSError as ex:
            messagebox.showerror("Delete Failed", str(ex))
            return

        self.clear_inputs(notify=False)
        messagebox.showinfo("Deleted", "Deleted:\n\n" + "\n".join(targets))

    def show_list_dialog(self, title, summary, rows):
        """rows: list of (sp_name, status) tuples."""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.minsize(420, 300)
        win.configure(bg=COLOR_BG)
        win.transient(self.root)

        tk.Label(
            win, text=summary, font=(FONT, 10), bg=COLOR_BG, fg=COLOR_TEXT,
            justify="left", wraplength=520, anchor="w",
        ).pack(fill="x", padx=14, pady=(14, 6))

        style = ttk.Style()
        style.configure(
            "SP.Treeview", font=(FONT, 10), rowheight=24,
            background="#fafbfc", fieldbackground="#fafbfc", foreground=COLOR_TEXT,
        )
        style.configure("SP.Treeview.Heading", font=(FONT, 10, "bold"))

        table = tk.Frame(win, bg=COLOR_BG)
        table.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        tree = ttk.Treeview(
            table, columns=("name", "status"), show="headings",
            style="SP.Treeview", selectmode="extended",
        )
        tree.heading("name", text="SP Name", anchor="w")
        tree.heading("status", text="Status", anchor="w")
        tree.column("name", width=400, anchor="w")
        tree.column("status", width=90, anchor="w", stretch=False)

        scroll = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        for name, status in rows:
            tree.insert("", "end", values=(name, status))

        def copy():
            selected = tree.selection()
            if not selected:
                messagebox.showinfo("Nothing Selected", "Select one or more SPs first.", parent=win)
                return
            names = [tree.item(iid, "values")[0] for iid in selected]
            win.clipboard_clear()
            win.clipboard_append("\n".join(names))

        def select_all(_event=None):
            tree.selection_set(tree.get_children())
            return "break"  # stop further handling

        def on_double_click(event):
            iid = tree.identify_row(event.y)
            if not iid:
                return
            sp_name = tree.item(iid, "values")[0]
            if not sp_name:
                return
            # Same behaviour as the "Open SP Files" button
            self.sp_name_var.set(sp_name)
            win.destroy()  # close verify dialog so focus returns to main window
            self.open_sp_files()

        tree.bind("<Double-1>", on_double_click)
        tree.bind("<Control-a>", select_all)
        tree.bind("<Control-A>", select_all)  # Caps Lock / some layouts

        # Focus the tree so keyboard shortcuts work immediately
        tree.focus_set()

        btns = tk.Frame(win, bg=COLOR_BG)
        btns.pack(fill="x", padx=14, pady=(0, 14))
        make_button(btns, "Close", win.destroy, COLOR_ACCENT).pack(side="right")
        make_button(btns, "Copy", copy, COLOR_TEAL).pack(side="right", padx=(0, 8))

        # Center on the screen
        w, h = 580, 460
        x = (win.winfo_screenwidth() - w) // 2
        y = (win.winfo_screenheight() - h) // 2
        win.geometry("{}x{}+{}+{}".format(w, h, x, y))
        win.grab_set()

    def verify_files(self):
        """Check that every .sql file name matches the SP name inside it
        (SIT Scripts and Backup folders), plus the open tabs if SP Name is set.
        Files ending with _old.sql are skipped (used for change tracking) and
        only listed as a note in the final popup."""
        output_dir = self.sit_folder_var.get().strip()
        if not output_dir or not os.path.isdir(output_dir):
            messagebox.showwarning(
                "Output Folder Required",
                "Select or find the exact CRF SIT output folder first.",
            )
            return

        # problems, checked, old_files, sp_names = [], 0, [], []
        problems, checked, old_files = [], 0, []
        names_by_tag = {"SIT Scripts": set(), "Backup": set()}

        for folder, tag in ((output_dir, "SIT Scripts"), (os.path.join(output_dir, "Backup"), "Backup")):
            if not os.path.isdir(folder):
                continue
            for name in sorted(os.listdir(folder)):
                if not name.lower().endswith(".sql"):
                    continue
                # Skip _old files used for backup / change tracking
                if name.lower().endswith("_old.sql"):
                    old_files.append(tag + "\\" + name)
                    continue
                checked += 1
                names_by_tag[tag].add(name[:-4])
                try:
                    content = read_sql(os.path.join(folder, name))
                except Exception as ex:
                    problems.append(tag + "\\" + name + ": could not read (" + str(ex) + ")")
                    continue
                issue = name_problem(name[:-4], content, tag + "\\" + name)
                if issue:
                    problems.append(issue)

        sp_name = clean_sp_name(self.sp_name_var.get())
        if sp_name:
            tabs = [("Modified SP tab", self.modified_text.get("1.0", "end-1c"))]
            if not self.is_new_var.get():
                tabs.append(("Backup SP tab", self.backup_text.get("1.0", "end-1c")))
            for label, text in tabs:
                if text.strip():
                    issue = name_problem(sp_name, text, label)
                    if issue:
                        problems.append(issue)

        old_note = ""
        if old_files:
            old_note = (
                "\n\nNote: " + str(len(old_files)) + " _old file(s) skipped (change tracking):\n"
                + "\n".join(old_files)
            )

        if problems:
            messagebox.showerror(
                "Verification Failed",
                "Possible wrong file copied:\n\n" + "\n".join(problems) + old_note,
            )
        else:
            new_sps = sorted(names_by_tag["SIT Scripts"] - names_by_tag["Backup"], key=str.lower)
            modified_sps = sorted(names_by_tag["Backup"], key=str.lower)
            rows = [(n, "New") for n in new_sps] + [(n, "Modified") for n in modified_sps]

            summary = (
                "All " + str(checked) + " SQL file(s) match their stored procedure names.\n"
                + "New: " + str(len(new_sps))
                + "    Modified: " + str(len(modified_sps))
                + old_note
            )
            self.show_list_dialog("Verified", summary, rows)
            # if len(rows) > 10:
            #     self.show_list_dialog(
            #         "Verified",
            #         "All " + str(checked) + " SQL file(s) match their stored procedure names."
            #         + old_note,
            #         rows,
            #     )
            # else:
            #     messagebox.showinfo(
            #         "Verified",
            #         "All " + str(checked) + " SQL file(s) match their stored procedure names:\n\n"
            #         + "\n".join(rows)
            #         + old_note,
            #     )

    def open_url_generator(self):
        win = tk.Toplevel(self.root)
        win.title("Generate SP URLs")
        win.geometry("900x700")
        win.minsize(700, 560)
        win.configure(bg=COLOR_BG)
        win.transient(self.root)

        def _crf_folder_name():
            """Derive CRF folder basename from Output SIT Folder (…/ECRF-xxx-…/SIT Scripts)."""
            sit = self.sit_folder_var.get().strip()
            if not sit:
                return ""
            parent = os.path.dirname(sit)
            if os.path.basename(sit).lower() == "sit scripts" and parent:
                return os.path.basename(parent)
            return ""

        card = make_card(win, "Generate SP URLs")
        card.pack(fill="both", expand=True, padx=14, pady=14)

        body = tk.Frame(card, bg=COLOR_CARD)
        body.pack(fill="both", expand=True, padx=18, pady=14)

        make_field_label(body, "Sample URL (any one SP of this CRF)").pack(fill="x", pady=(0, 4))

        # Editable Combobox so the user can type a new URL or pick a recent one
        url_var = tk.StringVar()
        url_combo = ttk.Combobox(
            body,
            textvariable=url_var,
            font=LABEL_FONT,
            values=self.sample_urls,
        )
        url_combo.pack(fill="x", ipady=3)
        if self.sample_urls:
            url_combo.set(self.sample_urls[0])  # pre-select most recent

        # ---- Scripts (DeploymentQueries) URL options ----
        scripts_row = tk.Frame(body, bg=COLOR_CARD)
        scripts_row.pack(fill="x", pady=(12, 0))
        scripts_row.columnconfigure(2, weight=1)

        include_scripts_var = tk.BooleanVar(value=True)
        crf_name_var = tk.StringVar(value=_crf_folder_name() or self.crf_var.get().strip())

        def on_scripts_toggle():
            crf_name_entry.config(state="normal" if include_scripts_var.get() else "disabled")

        make_check(
            scripts_row,
            "Include Scripts URL (DeploymentQueries)",
            include_scripts_var,
            command=on_scripts_toggle,
        ).grid(row=0, column=0, sticky="w")
        make_field_label(scripts_row, "CRF Name").grid(row=0, column=1, sticky="w", padx=(18, 8))
        crf_name_entry = make_entry(scripts_row, textvariable=crf_name_var)
        crf_name_entry.grid(row=0, column=2, sticky="ew", ipady=4)

        make_field_label(body, "SP list (one per line, or comma separated)").pack(fill="x", pady=(14, 4))
        sp_box = make_code_box(body, height=10, wrap=tk.NONE)
        sp_box.pack(fill="both", expand=True)

        bind_select_all(sp_box)

        make_field_label(body, "Generated URLs").pack(fill="x", pady=(14, 4))
        out_box = make_code_box(body, height=10, wrap=tk.NONE)
        out_box.pack(fill="both", expand=True)

        def run():
            try:
                sample = url_var.get().strip()
                urls = build_sp_urls(sample, sp_box.get("1.0", "end-1c"))
            except ValueError as ex:
                messagebox.showwarning("Invalid Input", str(ex), parent=win)
                return

            lines = []
            if include_scripts_var.get():
                # Scripts / DeploymentQueries URL (CRF Number from main window, CRF Name from this dialog)
                scripts_url = build_scripts_url(
                    sample,
                    self.crf_var.get().strip(),
                    crf_name_var.get().strip(),
                )
                if scripts_url:
                    lines.append(scripts_url)
                else:
                    messagebox.showwarning(
                        "Scripts URL Skipped",
                        "Could not build the Scripts URL. Make sure the CRF Number is filled in "
                        "on the main window and the sample URL contains /blob/master/.",
                        parent=win,
                    )
            lines.extend(urls)

            self._set_text(out_box, "\n".join(lines))
            # Persist the successfully used sample URL and refresh the dropdown
            self.remember_sample_url(sample)
            url_combo["values"] = self.sample_urls
            url_combo.set(sample)

        def copy():
            win.clipboard_clear()
            win.clipboard_append(out_box.get("1.0", "end-1c"))

        btns = tk.Frame(body, bg=COLOR_CARD)
        btns.pack(fill="x", pady=(14, 0))
        make_button(btns, "Generate URLs", run, COLOR_GREEN, bold=True, pady=8, padx=18).pack(side="right")
        make_button(btns, "Copy", copy, COLOR_ACCENT, pady=8, padx=18).pack(side="right", padx=(0, 8))

    def generate(self):
        output_dir = self.sit_folder_var.get().strip()
        modified_sql = self.modified_text.get("1.0", "end-1c").strip()
        backup_sql = self.backup_text.get("1.0", "end-1c").strip()

        if not output_dir or not os.path.isdir(output_dir):
            messagebox.showwarning(
                "Output Folder Required",
                "Select or find the exact CRF SIT output folder first.",
            )
            return
        if not modified_sql:
            messagebox.showwarning("Modified SP Required", "Enter or open the modified SP script.")
            self.notebook.select(0)
            return

        # Prefer name from Modified tab when rename checkbox is checked (New SP path).
        use_script_name = (
            self.is_new_var.get()
            and self.rename_from_script_var.get()
        )
        if use_script_name:
            sp_name = extract_sp_name(modified_sql)
            if not sp_name:
                messagebox.showerror(
                    "SP Name Not Found",
                    "Rename is checked, but no CREATE/ALTER PROCEDURE name was found in the "
                    "Modified tab. Fix the script or uncheck the rename option.",
                )
                self.notebook.select(0)
                return
        else:
            sp_name = clean_sp_name(self.sp_name_var.get()) or extract_sp_name(modified_sql)
            if not sp_name:
                messagebox.showerror(
                    "SP Name Not Found",
                    "Could not detect the stored procedure name. Enter it in SP Name or ensure the script "
                    "contains CREATE PROCEDURE, CREATE PROC, ALTER PROCEDURE, ALTER PROC, or "
                    "CREATE OR ALTER PROCEDURE.",
                )
                self.notebook.select(0)
                return

        # Catch a wrongly pasted script before anything is written.
        # When using script name for rename, the Modified tab is the source of truth — skip mismatch check.
        problems = []
        if not use_script_name:
            issue = name_problem(sp_name, modified_sql, "Modified SP")
            if issue:
                problems.append(issue)
        if not self.is_new_var.get() and backup_sql:
            issue = name_problem(sp_name, backup_sql, "Backup SP")
            if issue:
                problems.append(issue)
        if problems and not messagebox.askyesno(
            "SP Name Mismatch",
            "\n".join(problems) + "\n\nYou may have copied the wrong file. Write the files anyway?",
        ):
            return

        if not self.is_new_var.get() and not backup_sql:
            if not messagebox.askyesno(
                "Backup SP Empty",
                "The SP is not marked as new, but the Backup SP tab is empty.\n\n"
                "Continue and update only the modified SQL file?",
            ):
                self.notebook.select(1)
                return

        modified_path = os.path.join(output_dir, sp_name + ".sql")
        backup_dir = os.path.join(output_dir, "Backup")
        backup_path = os.path.join(backup_dir, sp_name + ".sql")

        # If renaming: old file path is the one we opened (or the previous SP Name).
        old_path = self._opened_sp_path
        if use_script_name and not old_path:
            # Fallback: previous name from the SP Name field
            old_name = clean_sp_name(self.sp_name_var.get())
            if old_name and old_name.lower() != sp_name.lower():
                candidate = os.path.join(output_dir, old_name + ".sql")
                if os.path.isfile(candidate):
                    old_path = candidate

        if (
            use_script_name
            and os.path.exists(modified_path)
            and (
                not old_path
                or os.path.normcase(os.path.normpath(old_path))
                != os.path.normcase(os.path.normpath(modified_path))
            )
        ):
            if not messagebox.askyesno(
                "File Exists",
                sp_name + ".sql already exists in the SIT Scripts folder.\n\nOverwrite it?",
            ):
                return

        try:
            # Write new/updated file under the (possibly new) name
            existed = os.path.exists(modified_path)
            with open(modified_path, "w", encoding="utf-8", newline="") as file:
                file.write(modified_sql.rstrip() + "\n")

            written = [("Updated: " if existed else "Created: ") + modified_path]

            # If rename was requested and the old file has a different path, remove it
            if use_script_name and old_path:
                old_path = os.path.normpath(old_path)
                new_path = os.path.normpath(modified_path)
                if os.path.normcase(old_path) != os.path.normcase(new_path) and os.path.isfile(old_path):
                    os.remove(old_path)
                    written.append("Removed old file: " + old_path)

            if not self.is_new_var.get() and backup_sql:
                existed = os.path.exists(backup_path)
                os.makedirs(backup_dir, exist_ok=True)
                with open(backup_path, "w", encoding="utf-8", newline="") as file:
                    file.write(backup_sql.rstrip() + "\n")
                written.append(("Updated: " if existed else "Created: ") + backup_path)
        except Exception as ex:
            messagebox.showerror("Update Failed", str(ex))
            return

        self.sp_name_var.set(sp_name)
        self._opened_sp_path = modified_path
        self.set_update_mode(True)
        self.save_settings()
        messagebox.showinfo("Success", "SQL file(s) saved:\n\n" + "\n".join(written))
        self.refresh_diff()

    def _configure_diff_tags(self):
        """Color tags used by the Diff tab (Beyond Compare style)."""
        for widget in (self.diff_left, self.diff_right):
            widget.tag_configure("equal", background="#fafbfc", foreground=COLOR_TEXT)
            widget.tag_configure("insert", background="#dcfce7", foreground="#14532d")   # green
            widget.tag_configure("delete", background="#fee2e2", foreground="#7f1d1d")   # red
            widget.tag_configure("replace", background="#fef9c3", foreground="#713f12")  # yellow
            widget.tag_configure("pad", background="#f1f5f9", foreground="#94a3b8")
            widget.tag_configure("inline", background="#fde047", foreground="#422006")
            widget.tag_configure("current_change", background="#93c5fd")  # blue highlight for nav

            widget.tag_raise("inline")          # above the line-level replace colour
            widget.tag_raise("current_change")  # keep navigation highlight on top
            widget.tag_raise("sel")

           
    def _bind_diff_scroll_sync(self):
        """Keep left/right Diff panes scrolling together vertically."""
        def sync_from_left(*_args):
            first, last = self.diff_left.yview()
            self.diff_right.yview_moveto(first)

        def sync_from_right(*_args):
            first, last = self.diff_right.yview()
            self.diff_left.yview_moveto(first)

        # Mouse wheel on either pane moves both
        def on_mousewheel(event, source):
            # Windows / Linux
            delta = -1 * (event.delta // 120) if event.delta else (-1 if event.num == 4 else 1)
            source.yview_scroll(delta, "units")
            other = self.diff_right if source is self.diff_left else self.diff_left
            other.yview_moveto(source.yview()[0])
            return "break"

        self.diff_left.bind("<MouseWheel>", lambda e: on_mousewheel(e, self.diff_left))
        self.diff_right.bind("<MouseWheel>", lambda e: on_mousewheel(e, self.diff_right))
        # Linux
        for w in (self.diff_left, self.diff_right):
            w.bind("<Button-4>", lambda e, src=w: on_mousewheel(e, src))
            w.bind("<Button-5>", lambda e, src=w: on_mousewheel(e, src))

    def _diff_xview(self, *args):
        self.diff_left.xview(*args)
        self.diff_right.xview(*args)

    def _on_diff_xscroll(self, first, last):
        self.diff_hbar.set(first, last)
        if self._xsyncing:
            return
        self._xsyncing = True
        try:
            self.diff_left.xview_moveto(first)
            self.diff_right.xview_moveto(first)
        finally:
            self._xsyncing = False

    def _set_diff_text(self, widget, lines_with_tags):
        """lines_with_tags: list of (line_text, tag_name)."""
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        for text, tag in lines_with_tags:
            start = widget.index("end-1c")
            widget.insert(tk.END, text + "\n")
            end = widget.index("end-1c")
            widget.tag_add(tag, start, end)
        widget.configure(state="disabled")

    def _append_replace_block(self, left_rows, right_rows, old_chunk, new_chunk):
        """
        Color a replace hunk properly:
        - same number of lines → yellow (line-level edit)
        - different counts → red on left, green on right (structural add/remove)
        """
        if len(old_chunk) == len(new_chunk) and len(old_chunk) > 0:
            for o, n in zip(old_chunk, new_chunk):
                left_rows.append((o, "replace"))
                right_rows.append((n, "replace"))
            return

        # Structural change: pair similar lines, rest is delete / insert
        l, r = replace_rows(old_chunk, new_chunk)
        left_rows.extend(l)
        right_rows.extend(r)

    def refresh_diff(self):
        """Rebuild the full Diff alignment from Modified / Backup tabs, then apply view mode."""
        old = self.backup_text.get("1.0", "end-1c")
        new = self.modified_text.get("1.0", "end-1c")

        left_rows = []
        right_rows = []

        if self.is_new_var.get() or not old.strip():
            left_rows = [("(no backup — New SP)", "pad")]
            right_rows = [(line, "equal") for line in new.splitlines()] or [("", "equal")]
            self._diff_full_left = left_rows
            self._diff_full_right = right_rows
            self._diff_change_rows = []
            self._diff_nav_index = -1
            self._apply_diff_view()
            return

        old_lines = old.splitlines()
        new_lines = new.splitlines()
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for line in old_lines[i1:i2]:
                    left_rows.append((line, "equal"))
                for line in new_lines[j1:j2]:
                    right_rows.append((line, "equal"))
            elif tag == "replace":
                self._append_replace_block(
                    left_rows, right_rows,
                    old_lines[i1:i2], new_lines[j1:j2],
                )
            elif tag == "delete":
                for line in old_lines[i1:i2]:
                    left_rows.append((line, "delete"))
                    right_rows.append(("", "pad"))
            elif tag == "insert":
                for line in new_lines[j1:j2]:
                    left_rows.append(("", "pad"))
                    right_rows.append((line, "insert"))

        # Change rows = any row that is not pure equal on both sides
        change_rows = []
        for idx, ((lt, ltag), (rt, rtag)) in enumerate(zip(left_rows, right_rows)):
            if ltag != "equal" or rtag != "equal":
                change_rows.append(idx)

        self._diff_full_left = left_rows
        self._diff_full_right = right_rows
        self._diff_change_rows = change_rows
        self._diff_nav_index = -1
        self._apply_diff_view()

    def _on_context_toggle(self):
        # Context only makes sense on top of Diffs, so turning it on from "All" switches to Diffs.
        if self._diff_context.get() and self._diff_mode.get() == "all":
            self._diff_mode.set("diffs")
        self._apply_diff_view()

    def _apply_diff_view(self):
        """Filter full diff into All / Only Diffs / Context and paint the panes."""
        left = list(self._diff_full_left)
        right = list(self._diff_full_right)
        if not left and not right:
            self._set_diff_text(self.diff_left, [])
            self._set_diff_text(self.diff_right, [])
            self._diff_display_to_full = []
            return

        mode = self._diff_mode.get()
        n = max(len(left), len(right))
        # Pad if somehow unequal (should not happen)
        while len(left) < n:
            left.append(("", "pad"))
        while len(right) < n:
            right.append(("", "pad"))

        if mode == "all":
            show = list(range(n))
        elif not self._diff_context.get():
            show = list(self._diff_change_rows)
            if not show:
                show = list(range(n))  # nothing changed — show all
        else:
            if not self._diff_change_rows:
                show = list(range(n))
            else:
                left_cmt = comment_flags(left)
                right_cmt = comment_flags(right)
                before = spin_value(self._ctx_before, 5)
                after = spin_value(self._ctx_after, 5)
                c_before = spin_value(self._ctx_cmt_before, 2)
                c_after = spin_value(self._ctx_cmt_after, 2)

                keep = set()
                for h0, h1 in self._diff_hunks():
                    only_comments = all(left_cmt[i] and right_cmt[i] for i in range(h0, h1 + 1))
                    b_ctx, a_ctx = (c_before, c_after) if only_comments else (before, after)
                    keep.update(range(max(0, h0 - b_ctx), min(n - 1, h1 + a_ctx) + 1))
                show = sorted(keep)

        self._diff_display_to_full = show
        left_view, right_view = with_width_pad(
            [left[i] for i in show], [right[i] for i in show]
        )
        self._set_diff_text(self.diff_left, left_view)
        self._set_diff_text(self.diff_right, right_view)
        paint_inline(self.diff_left, self.diff_right, left_view, right_view)

    def _diff_hunks(self):
        """Group consecutive changed rows into blocks: [(first_row, last_row), ...] in full-list indices."""
        hunks = []
        for row in self._diff_change_rows:
            if hunks and row == hunks[-1][1] + 1:
                hunks[-1] = (hunks[-1][0], row)
            else:
                hunks.append((row, row))
        return hunks

    def _scroll_to_display_row(self, display_row, end_row=None):
        """Scroll both panes to display_row and flash the whole block (display_row..end_row)."""
        if display_row < 0:
            return
        line = display_row + 1
        end_line = (end_row if end_row is not None else display_row) + 1
        for w in (self.diff_left, self.diff_right):
            w.configure(state="normal")
            w.see("%d.0" % line)
            w.tag_remove("current_change", "1.0", tk.END)
            w.tag_add("current_change", "%d.0" % line, "%d.end" % end_line)
            w.configure(state="disabled")
        self.root.after(
            1200,
            lambda: (
                self.diff_left.tag_remove("current_change", "1.0", tk.END),
                self.diff_right.tag_remove("current_change", "1.0", tk.END),
            ),
        )

    def diff_next_change(self):
        hunks = self._diff_hunks()
        if not hunks:
            return
        self._diff_nav_index = (self._diff_nav_index + 1) % len(hunks)
        self._goto_change_nav(hunks[self._diff_nav_index])

    def diff_prev_change(self):
        hunks = self._diff_hunks()
        if not hunks:
            return
        if self._diff_nav_index < 0:
            self._diff_nav_index = len(hunks) - 1
        else:
            self._diff_nav_index = (self._diff_nav_index - 1) % len(hunks)
        self._goto_change_nav(hunks[self._diff_nav_index])

    def _goto_change_nav(self, hunk):
        """Scroll to a block (first_row, last_row) given in full-list indices."""
        first, last = hunk
        if first not in self._diff_display_to_full:
            self._diff_mode.set("all")
            self._apply_diff_view()
        try:
            d_first = self._diff_display_to_full.index(first)
            d_last = self._diff_display_to_full.index(last)
        except ValueError:
            return
        self._scroll_to_display_row(d_first, d_last)

if __name__ == "__main__":
    root = tk.Tk()
    # Prefer ICO, fall back to PNG
    try:
        root.iconbitmap(resource_path("SPGenerator.ico"))
    except Exception:
        try:
            root.iconphoto(True, tk.PhotoImage(file=resource_path("SPGenerator.png")))
        except Exception:
            pass
    app = SPFileGenerator(root)
    root.mainloop()