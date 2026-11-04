import json
import os
import re
import sys
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
CONFIG_KEYS = ("crf_root", "crf_number", "sit_folder", "sample_urls")


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
        if sp_name and sp_name.lower() not in seen:
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

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def save_settings(self):
        try:
            save_config({
                "crf_root": self.crf_root_var.get().strip(),
                "crf_number": self.crf_var.get().strip(),
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

    def _build_ui(self):
        # ---------- Top card: CRF & SP details ----------
        card = make_card(self.root, "CRF & Stored Procedure")
        card.pack(fill="x", padx=14, pady=(14, 8))

        top = tk.Frame(card, bg=COLOR_CARD)
        top.pack(fill="x", padx=18, pady=14)
        top.columnconfigure(1, weight=1)
        top.columnconfigure(2, minsize=170)  # keeps the right-hand buttons the same width

        make_field_label(top, "CRF Number").grid(row=0, column=0, sticky="w", padx=(0, 14), pady=5)
        crf_entry = make_entry(top, textvariable=self.crf_var)
        crf_entry.grid(row=0, column=1, sticky="ew", pady=5, ipady=5)
        crf_entry.bind("<Return>", lambda _event: self.resolve_sit_folder())
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

        make_button(bottom, "Verify SQL Files", self.verify_files, COLOR_PURPLE).pack(side="left")
        make_button(bottom, "Open Folder", self.open_output_folder, COLOR_TEAL).pack(side="left", padx=(8, 0))
        make_button(bottom, "Clear", self.clear_inputs, COLOR_ORANGE).pack(side="left", padx=(8, 0))
        make_button(bottom, "Generate URLs", self.open_url_generator, COLOR_ACCENT).pack(side="left", padx=(8, 0))
        self.generate_button = make_button(
            bottom, "Generate SQL Files", self.generate, COLOR_GREEN,
            font_size=12, bold=True, pady=10, padx=24
        )
        self.generate_button.pack(side="right")

        # ---------- Notebook: Modified / Backup ----------
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        modified_frame = tk.Frame(self.notebook, bg=COLOR_CARD)
        backup_frame = tk.Frame(self.notebook, bg=COLOR_CARD)
        self.notebook.add(modified_frame, text="Modified SP")
        self.notebook.add(backup_frame, text="Backup SP")

        self.modified_text = make_code_box(modified_frame, wrap=tk.NONE, undo=True)
        self.modified_text.pack(fill="both", expand=True, padx=10, pady=10)

        self.backup_text = make_code_box(backup_frame, wrap=tk.NONE, undo=True)
        self.backup_text.pack(fill="both", expand=True, padx=10, pady=10)

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
        except Exception as ex:
            messagebox.showerror("Read Failed", str(ex))

    def clear_inputs(self):
        # if not messagebox.askyesno("Clear", "Clear both SQL tabs and the SP name?"):
        #     return
        self.modified_text.delete("1.0", tk.END)
        self.backup_text.delete("1.0", tk.END)
        self.sp_name_var.set("")
        self.is_new_var.set(False)
        self.rename_from_script_var.set(False)
        self.rename_cb.grid_remove()
        self._opened_sp_path = None
        self.set_update_mode(False)
        messagebox.showinfo(
            "Clear",
            "The form has been clear to Generate SQL Files mode."
        )

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

        problems, checked, old_files = [], 0, []
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
            messagebox.showinfo(
                "Verified",
                "All " + str(checked) + " SQL file(s) match their stored procedure names."
                + old_note,
            )

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
                if old_path.lower() != new_path.lower() and os.path.isfile(old_path):
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