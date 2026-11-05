import fnmatch
import json
import os
import re
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
import traceback

APP_TITLE = "SIT Compare Launcher"
DEFAULT_REMOTE_ROOT = "Projects"
LOCAL_ROOT_MARKERS = ("development", "dev")   # local folders that sit above the project tree

if getattr(sys, "frozen", False):
    # Running as a PyInstaller exe - keep the config next to the .exe,
    # not in the temp extraction folder (_MEI...).
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "sit_compare_config.json")


def resource_path(relative_path):
    # Bundled files (icon) live in PyInstaller's temp folder when frozen.
    return os.path.join(getattr(sys, "_MEIPASS", BASE_DIR), relative_path)


MAX_RECENT = 20
MAX_REMOTE_HISTORY = 30
DEFAULT_PROFILE = "sitFTP"

# How a remote file is addressed on the command line. {profile} is the saved
# Beyond Compare FTP profile name, {path} is "<remote folder>/<relative path>".
# Editable in the config file if your Beyond Compare version wants a
# different syntax (no rebuild needed).
DEFAULT_REMOTE_TEMPLATE = "profile:{profile}?{path}"

CONFIRM_ABOVE = 30             # ask before opening more tabs than this
WARMUP_MS = 1500
NEXT_LAUNCH_DELAY_MS = 800

# ============================================================
#               THEME (shared with SP File Generator)
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
COLOR_RED = "#dc2626"
LABEL_FONT = (FONT, 10)


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
        disabledforeground="#e5e7eb",
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


def make_hint(parent, text):
    return tk.Label(parent, text=text, font=(FONT, 9), bg=COLOR_CARD, fg=COLOR_MUTED, anchor="w")


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
    """Style the ttk widgets (Combobox) to match."""
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "TCombobox",
        padding=5,
        fieldbackground="#ffffff",
        bordercolor=COLOR_BORDER,
        lightcolor=COLOR_BORDER,
        darkcolor=COLOR_BORDER,
        arrowcolor=COLOR_MUTED,
    )

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def load_config():
    cfg = {
        "beyond_compare_path": "",
        "ftp_profile": DEFAULT_PROFILE,
        "remote_path_template": DEFAULT_REMOTE_TEMPLATE,
        "excluded_types": "",
        "excluded_files": "",
        "remote_folders": [],
        "projects": [],
    }

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in cfg:
                if key in data:
                    cfg[key] = data[key]
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        # Corrupt file: keep a copy instead of silently overwriting it.
        try:
            os.replace(CONFIG_FILE, CONFIG_FILE + ".bad")
        except OSError:
            pass

    projects = []
    for p in cfg["projects"] if isinstance(cfg["projects"], list) else []:
        if isinstance(p, dict) and p.get("path"):
            projects.append(
                {
                    "path": p["path"],
                    "remote_folder": p.get("remote_folder", ""),
                    "manual": bool(p.get("manual", False)),
                }
            )
    cfg["projects"] = projects

    if not isinstance(cfg["excluded_types"], str):
        cfg["excluded_types"] = ""
    if not isinstance(cfg["excluded_files"], str):
        cfg["excluded_files"] = ""

    history = []
    if isinstance(cfg["remote_folders"], list):
        history = [r for r in cfg["remote_folders"] if isinstance(r, str) and r.strip()]
    for p in projects:  # include remote folders already saved against projects
        if p["remote_folder"] and p["remote_folder"] not in history:
            history.append(p["remote_folder"])
    cfg["remote_folders"] = history[:MAX_REMOTE_HISTORY]
    return cfg


def save_config(cfg):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)
    os.replace(tmp, CONFIG_FILE)  # atomic, so a crash can't corrupt the config

def is_bc_running(bc):
    exe = os.path.basename(bc)
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.decode("utf-8", "replace")
    except OSError:
        return False
    return exe.lower() in out.lower()

# --------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------
class GitError(Exception):
    pass


def run_git(project, *args):
    try:
        result = subprocess.run(
            ["git", "-C", project, *args],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        raise GitError("Git was not found. Install Git or add it to PATH.")

    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise GitError(message or "Git command failed.")

    return result.stdout.decode("utf-8", "replace")


def get_changed_files(project):
    """Staged + unstaged + untracked files under the project folder.

    Returns (files, skipped):
      files   -> [(status, absolute_local_path)]
      skipped -> [(status, relative_path)] for entries with no file on disk
                 (deleted files, submodules) so nothing waits on them.
    """
    top = os.path.normpath(run_git(project, "rev-parse", "--show-toplevel").strip())
    out = run_git(project, "status", "--porcelain=v1", "-z", "-uall", "--", ".")

    entries = out.split("\0")
    files, skipped = [], []

    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue

        status, rel = entry[:2], entry[3:]

        # Renames/copies are followed by the original path - skip it.
        if "R" in status or "C" in status:
            i += 1

        local = os.path.normpath(os.path.join(top, rel))
        if os.path.isfile(local):
            files.append((status, local))
        else:
            skipped.append((status, rel))

    return files, skipped


def status_label(status):
    if status == "PK":
        return "PICK"
    return "NEW" if status == "??" or "A" in status else "MOD"


def parse_exclusions(text):
    """'.dll; pdb, Web.config, bin/*' -> lowercase wildcard masks."""
    masks = []
    for part in re.split(r"[;,\s]+", text.lower()):
        if not part:
            continue
        if part.startswith("."):
            part = "*" + part                 # .dll  -> *.dll
        elif not any(c in part for c in "*?./"):
            part = "*." + part                # dll   -> *.dll
        masks.append(part)                    # Web.config / bin/* stay as typed
    return masks

def parse_file_exclusions(text, base=None):
    """'App_Code/Constants.cs; Web.config, *.min.js' -> lowercase masks.
    Also accepts paths pasted from the log output (~\\Development\\proj\\...)."""
    prefix = log_path(base, "").replace("\\", "/").lower() if base else None
    masks = []
    for part in re.split(r"[;,\n]+", text.lower()):
        part = part.strip().replace("\\", "/")
        if prefix and part.startswith(prefix):
            part = part[len(prefix):]
        part = re.sub(r"^(\./)+", "", part).lstrip("/")
        if part:
            masks.append(part)
    return masks

def is_excluded(rel, masks):
    rel = rel.lower()
    name = rel.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatchcase(name, m) or fnmatch.fnmatchcase(rel, m) for m in masks
    )

def _prefix_word(name):
    """'PCS API' / 'PCS Build' / 'PCS' -> 'PCS' - everyone names their local
    API folder differently, but SIT only has one folder per prefix."""
    return re.split(r"[\s_-]+", name.strip())[0] or name


def default_remote(path):
    """D:\\GIT\\Development\\eCustomsServices\\CustomsWebService_v1
       -> Projects/eCustomsServices/CustomsWebService_v1
       D:\\Code\\Published APIs\\PCS API  -> Projects/PCS"""
    parts = os.path.normpath(path).replace("\\", "/").split("/")
    rest = [_prefix_word(parts[-1])]        # fallback: first word of the folder name
    for i in range(len(parts) - 2, -1, -1): # last marker wins, never the folder itself
        if parts[i].lower() in LOCAL_ROOT_MARKERS:
            rest = parts[i + 1:]
            break
    return "/".join([DEFAULT_REMOTE_ROOT] + rest)

def log_path(base, rel):
    """D:\\GIT\\Development\\e-customs + App_Code/Constants.cs
       -> ~\\Development\\e-customs\\App_Code\\Constants.cs"""
    parts = os.path.normpath(base).replace("\\", "/").split("/")
    head = [parts[-1]]                      # fallback: project folder name
    for i in range(len(parts) - 2, -1, -1):  # last marker wins
        if parts[i].lower() in LOCAL_ROOT_MARKERS:
            head = parts[i:]
            break
    return "~\\" + "\\".join(head + rel.split("/"))

# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()

        self._queue = []
        self._opened = 0
        self._skipped = 0
        self._excluded = 0

        root.title(APP_TITLE)
        root.minsize(900, 640)
        root.configure(bg=COLOR_BG)
        apply_ttk_theme()
        self._center(root, 1000, 760)

        self.bc_var = tk.StringVar(value=self.cfg["beyond_compare_path"])
        self.profile_var = tk.StringVar(value=self.cfg["ftp_profile"])
        self.project_var = tk.StringVar()
        self.remote_var = tk.StringVar()
        self.manual_var = tk.BooleanVar(value=False)
        self.exclude_var = tk.StringVar(value=self.cfg["excluded_types"])
        self.exclude_files_var = tk.StringVar(value=self.cfg["excluded_files"])

        self._build_ui()
        self.refresh_list()

        # Pre-select the most recent project.
        if self.cfg["projects"]:
            self.listbox.selection_set(0)
            self._fill_from_project(self.cfg["projects"][0])

        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---- layout ----------------------------------------------------------
    def _build_ui(self):
        # ---------- Top card: settings ----------
        card = make_card(self.root, "Compare Settings")
        card.pack(fill="x", padx=14, pady=(14, 8))

        frm = tk.Frame(card, bg=COLOR_CARD)
        frm.pack(fill="x", padx=18, pady=14)
        frm.columnconfigure(1, weight=1)
        frm.columnconfigure(2, minsize=190)  # keeps the right-hand buttons/hints the same width

        # Beyond Compare path
        make_field_label(frm, "Beyond Compare").grid(row=0, column=0, sticky="w", padx=(0, 14), pady=5)
        ent_bc = make_entry(frm, textvariable=self.bc_var)
        ent_bc.grid(row=0, column=1, sticky="ew", pady=5, ipady=5)
        ent_bc.bind("<FocusOut>", lambda e: self.save_settings())
        ent_bc.bind("<Return>", lambda e: self.save_settings())
        make_button(frm, "Browse", self.browse_bc, COLOR_ACCENT, pady=6).grid(
            row=0, column=2, sticky="ew", padx=(10, 0), pady=5
        )

        # FTP profile
        make_field_label(frm, "FTP Profile").grid(row=1, column=0, sticky="w", padx=(0, 14), pady=5)
        ent_profile = make_entry(frm, textvariable=self.profile_var)
        ent_profile.grid(row=1, column=1, sticky="ew", pady=5, ipady=5)
        ent_profile.bind("<FocusOut>", lambda e: self.save_settings())
        ent_profile.bind("<Return>", lambda e: self.save_settings())

        # Recent projects
        make_field_label(frm, "Recent Projects").grid(row=2, column=0, sticky="nw", padx=(0, 14), pady=5)
        list_frame = tk.Frame(frm, bg=COLOR_CARD)
        list_frame.grid(row=2, column=1, sticky="ew", pady=5)
        list_frame.columnconfigure(0, weight=1)

        self.listbox = tk.Listbox(
            list_frame,
            height=6,
            font=("Consolas", 10),
            exportselection=False,
            bg="#fafbfc",
            fg=COLOR_TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=COLOR_BORDER,
            highlightcolor=COLOR_ACCENT,
            selectbackground="#bfdbfe",
            selectforeground=COLOR_TEXT,
            activestyle="none",
        )
        self.listbox.grid(row=0, column=0, sticky="ew")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.config(yscrollcommand=scroll.set)
        self.listbox.bind("<<ListboxSelect>>", self.on_list_select)

        make_button(frm, "Remove", self.remove_selected, COLOR_RED, pady=6).grid(
            row=2, column=2, sticky="new", padx=(10, 0), pady=5
        )

        # Project folder
        make_field_label(frm, "Project Folder").grid(row=3, column=0, sticky="w", padx=(0, 14), pady=5)
        make_entry(frm, textvariable=self.project_var).grid(row=3, column=1, sticky="ew", pady=5, ipady=5)
        make_button(frm, "Browse", self.browse_project, COLOR_ACCENT, pady=6).grid(
            row=3, column=2, sticky="ew", padx=(10, 0), pady=5
        )

        # Remote folder (pick from previously used ones, or type a custom one)
        make_field_label(frm, "Remote Folder").grid(row=4, column=0, sticky="w", padx=(0, 14), pady=5)
        self.remote_combo = ttk.Combobox(
            frm, textvariable=self.remote_var, font=LABEL_FONT, values=self.cfg["remote_folders"]
        )
        self.remote_combo.grid(row=4, column=1, sticky="ew", pady=5, ipady=3)
        self.remote_combo.bind("<<ComboboxSelected>>", lambda e: self.save_remote_folder())
        self.remote_combo.bind("<FocusOut>", lambda e: self.save_remote_folder())
        self.remote_combo.bind("<Return>", lambda e: self.save_remote_folder())
        make_hint(frm, "e.g. Projects/PCS").grid(row=4, column=2, sticky="w", padx=(10, 0))

        # Excluded file types
        make_field_label(frm, "Exclude Types").grid(row=5, column=0, sticky="w", padx=(0, 14), pady=5)
        ent_exclude = make_entry(frm, textvariable=self.exclude_var)
        ent_exclude.grid(row=5, column=1, sticky="ew", pady=5, ipady=5)
        ent_exclude.bind("<FocusOut>", lambda e: self.save_settings())
        ent_exclude.bind("<Return>", lambda e: self.save_settings())
        make_hint(frm, "e.g. .dll; .pdb; Web.config").grid(row=5, column=2, sticky="w", padx=(10, 0))

        # Excluded specific files
        make_field_label(frm, "Exclude Files").grid(row=6, column=0, sticky="w", padx=(0, 14), pady=5)
        ent_exclude_files = make_entry(frm, textvariable=self.exclude_files_var)
        ent_exclude_files.grid(row=6, column=1, sticky="ew", pady=5, ipady=5)
        ent_exclude_files.bind("<FocusOut>", lambda e: self.save_settings())
        ent_exclude_files.bind("<Return>", lambda e: self.save_settings())
        make_hint(frm, "e.g. App_Code/Constants.cs").grid(row=6, column=2, sticky="w", padx=(10, 0))

        # Non-git projects (e.g. published API folders)
        make_check(
            frm,
            "Not a git project - compare the whole folder side by side",
            self.manual_var,
            command=self.save_manual_flag,
        ).grid(row=7, column=1, sticky="w", pady=(6, 0))

        # ---------- Bottom action bar (packed first so it is never pushed off-screen) ----------
        bottom = tk.Frame(self.root, bg=COLOR_BG)
        bottom.pack(side="bottom", fill="x", padx=14, pady=(0, 14))

        self.btn_logs = make_button(bottom, "Generate Logs", self.generate_logs, COLOR_PURPLE)
        self.btn_logs.pack(side="left", padx=(6, 0))
        self.btn_clear = make_button(bottom, "Clear Logs", self.clear_log, COLOR_ORANGE)
        self.btn_clear.pack(side="left", padx=(6, 0))
        self.btn_open = make_button(
            bottom,
            "Check Git & Open in Beyond Compare",
            self.check_and_open,
            COLOR_GREEN,
            font_size=10,
            bold=True,
            pady=10,
            padx=13,
        )
        self.btn_open.pack(side="right")

        # ---------- Log card ----------
        log_card = make_card(self.root, "Log")
        log_card.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self.log_box = make_code_box(log_card, height=12, wrap="none", state="disabled")
        self.log_box.pack(fill="both", expand=True, padx=10, pady=10)

    @staticmethod
    def _center(root, width, height):
        root.update_idletasks()
        x = (root.winfo_screenwidth() // 2) - (width // 2)
        y = (root.winfo_screenheight() // 2) - (height // 2)
        root.geometry(f"{width}x{height}+{x}+{y}")

    # ---- logging ---------------------------------------------------------
    def log(self, text):
        self.log_box.config(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def clear_log(self):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")

    # ---- config helpers --------------------------------------------------
    def save(self):
        try:
            save_config(self.cfg)
        except OSError as ex:
            self.log(f"WARNING: could not save config: {ex}")

    def save_settings(self):
        """Persist Beyond Compare path + FTP profile."""
        self.cfg["beyond_compare_path"] = self.bc_var.get().strip()
        self.cfg["ftp_profile"] = self.profile_var.get().strip() or DEFAULT_PROFILE
        self.cfg["excluded_types"] = self.exclude_var.get().strip()
        self.cfg["excluded_files"] = self.exclude_files_var.get().strip()
        self.save()

    def _find_project(self, path):
        key = os.path.normcase(os.path.normpath(path))
        for p in self.cfg["projects"]:
            if os.path.normcase(os.path.normpath(p["path"])) == key:
                return p
        return None

    def save_remote_folder(self):
        path = self.project_var.get().strip()
        if not path:
            return
        project = self._find_project(path)
        if project is not None:
            project["remote_folder"] = self.remote_var.get().strip()
            self.remember_remote(project["remote_folder"])
            self.save()

    def remember_remote(self, folder):
        folder = folder.strip()
        if not folder:
            return
        history = self.cfg["remote_folders"]
        if folder in history:
            history.remove(folder)
        history.insert(0, folder)
        del history[MAX_REMOTE_HISTORY:]
        self.remote_combo["values"] = history

    def save_manual_flag(self):
        self._update_button_text()
        project = self._find_project(self.project_var.get().strip())
        if project is not None:
            project["manual"] = self.manual_var.get()
            self.save()

    def _update_button_text(self):
        self.btn_open.config(
            text="Open Folder Compare in Beyond Compare"
            if self.manual_var.get()
            else "Check Git & Open in Beyond Compare"
        )

    def open_folder_compare(self, bc, project, profile):
        """Non-git projects: skip picking files one by one and just open the
        local project folder against its SIT folder as a single folder
        compare - Beyond Compare shows the diffs itself."""
        template = self.cfg.get("remote_path_template") or DEFAULT_REMOTE_TEMPLATE
        remote_folder = self.remote_var.get().strip().replace("\\", "/").strip("/")
        remote = template.format(profile=profile, path=remote_folder)

        self.log(f"Local : {project}")
        self.log(f"Remote: {remote}")

        try:
            subprocess.Popen(
                [bc, project, remote],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as ex:
            self.log(f"ERROR: could not start Beyond Compare: {ex}")
            messagebox.showerror("Beyond Compare", f"Could not start Beyond Compare:\n\n{ex}")
            return

        self.log("Opened as a folder compare.")

    def register_project(self, path, remote_folder=None, manual=None):
        """Add/promote a project to the top of the recent list and save now."""
        path = os.path.normpath(path)
        project = self._find_project(path)

        if project is None:
            project = {"path": path, "remote_folder": default_remote(path), "manual": False}
        else:
            self.cfg["projects"].remove(project)

        if remote_folder is not None:
            project["remote_folder"] = remote_folder
            self.remember_remote(remote_folder)

        if manual is not None:
            project["manual"] = manual

        self.cfg["projects"].insert(0, project)
        del self.cfg["projects"][MAX_RECENT:]
        self.save()

        self.refresh_list()
        self.listbox.selection_set(0)
        self._fill_from_project(project)

    def _fill_from_project(self, project):
        self.project_var.set(project["path"])
        self.remote_var.set(project.get("remote_folder", ""))
        self.manual_var.set(project.get("manual", False))
        self._update_button_text()

    def refresh_list(self):
        self.listbox.delete(0, "end")
        for p in self.cfg["projects"]:
            self.listbox.insert("end", p["path"])

    # ---- events ----------------------------------------------------------
    def browse_bc(self):
        initial = os.path.dirname(self.bc_var.get().strip()) or None
        path = filedialog.askopenfilename(
            title="Select BCompare.exe",
            initialdir=initial,
            filetypes=[("Beyond Compare", "BCompare.exe"), ("Executables", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self.bc_var.set(os.path.normpath(path))
            self.save_settings()  # saved immediately, not on "Open"

    def browse_project(self):
        initial = None
        if self.cfg["projects"]:
            initial = os.path.dirname(self.cfg["projects"][0]["path"])
        path = filedialog.askdirectory(title="Select project folder", initialdir=initial, mustexist=True)
        if path:
            is_new = self._find_project(path) is None
            self.register_project(path)  # saved immediately, not on "Open"
            if is_new:
                self.log("New project added. Default Remote Folder set to 'Projects/<folder>'. Adjust casing if needed, then click Check Git.")
                self.remote_combo.focus_set()
                self.remote_combo.selection_range(0, "end")

    def on_list_select(self, _event=None):
        selection = self.listbox.curselection()
        if selection:
            self._fill_from_project(self.cfg["projects"][selection[0]])

    def remove_selected(self):
        selection = self.listbox.curselection()
        if not selection:
            return
        del self.cfg["projects"][selection[0]]
        self.save()
        self.refresh_list()
        self.project_var.set("")
        self.remote_var.set("")
        self.manual_var.set(False)
        self._update_button_text()

    def on_close(self):
        self.save_settings()
        self.save_remote_folder()
        self.root.destroy()

    # ---- main action -----------------------------------------------------
    def check_and_open(self):
        bc = self.bc_var.get().strip()
        project = self.project_var.get().strip()
        profile = self.profile_var.get().strip()

        if not bc or not os.path.isfile(bc):
            messagebox.showerror("Beyond Compare", "Select a valid BCompare.exe path.")
            return
        if not project or not os.path.isdir(project):
            messagebox.showerror("Project Folder", "Select a valid project folder.")
            return
        if not profile:
            messagebox.showerror("FTP Profile", "Enter the Beyond Compare FTP profile name.")
            return

        manual = self.manual_var.get()
        self.save_settings()
        self.register_project(project, self.remote_var.get().strip(), manual)

        self.clear_log()
        self.log(f"Project : {os.path.normpath(project)}")

        if manual:
            self.open_folder_compare(bc, os.path.normpath(project), profile)
            return

        try:
            files, skipped = get_changed_files(os.path.normpath(project))
        except GitError as ex:
            message = str(ex)
            if "not a git repository" in message.lower():
                message += "\n\nTip: tick 'Not a git project' for folders like published API output."
            self.log(f"GIT ERROR: {message}")
            messagebox.showerror("Git Error", message)
            return

        for status, rel in skipped:
            self.log(f"SKIP  [{status.strip() or '?'}] {rel}  (no local file - deleted?)")

        masks = parse_exclusions(self.exclude_var.get()) + parse_file_exclusions(self.exclude_files_var.get(), os.path.normpath(project))
        base = os.path.normpath(project)
        kept, excluded = [], []
        for status, local in files:
            rel = os.path.relpath(local, base).replace("\\", "/")
            if is_excluded(rel, masks):
                excluded.append((status, rel))
                #self.log(f"EXCL  [{status_label(status)}] {rel}")
            else:
                kept.append((status, local))
        files = kept

        if not files:
            if excluded:
                self.log(f"All {len(excluded)} changed file(s) match the excluded types:")
                for status, rel in excluded:
                    self.log(f"EXCL  [{status_label(status)}] {rel}")
                self.log("-" * 60)
                self.log("Nothing opened. Adjust 'Exclude Types' if any of these should be compared.")
                messagebox.showinfo("Nothing to Compare", "All changed files match the excluded types.\n\nSee the log for the list.")
            else:
                self.log("No changed files found.")
                messagebox.showinfo("Nothing to Compare", "No staged, unstaged or new files were found.")
            return

        if len(files) > CONFIRM_ABOVE and not messagebox.askyesno(
            "Many Files",
            f"{len(files)} changed files found.\n\nOpen all of them in Beyond Compare?",
        ):
            return

        # Build the (local, remote) pairs.
        template = self.cfg.get("remote_path_template") or DEFAULT_REMOTE_TEMPLATE
        remote_folder = self.remote_var.get().strip().replace("\\", "/").strip("/")
        base = os.path.normpath(project)

        self._queue = []
        try:
            for status, local in files:
                rel = os.path.relpath(local, base).replace("\\", "/")
                remote_path = "/".join(part for part in (remote_folder, rel) if part)
                remote = template.format(profile=profile, path=remote_path)
                self._queue.append((status, local, rel, remote))
        except (KeyError, IndexError, ValueError) as ex:
            messagebox.showerror(
                "Config Error",
                f"remote_path_template in the config file is invalid:\n\n{ex}\n\n"
                "It may only use {profile} and {path}.",
            )
            return

        self.log(f"Found {len(self._queue)} file(s). Opening in Beyond Compare...")
        first = self._queue[0]
        self.log(f"Example  left : {first[1]}")
        self.log(f"Example  right: {first[3]}")
        self.log("-" * 60)

        self._opened = 0
        self._skipped = len(skipped)
        self._excluded = len(excluded)
        self.btn_open.config(state="disabled")

        if is_bc_running(bc):
            self.log("Beyond Compare already running - opening as tabs.")
            self._launch_next(bc)
        else:
            self.log("Starting Beyond Compare, waiting for it to be ready...")
            try:
                subprocess.Popen(
                    [bc],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as ex:
                self.log(f"ERROR: could not start Beyond Compare: {ex}")
                messagebox.showerror("Beyond Compare", f"Could not start Beyond Compare:\n\n{ex}")
                self._queue = []
                self.btn_open.config(state="normal")
                return
            self.root.after(WARMUP_MS, lambda: self._launch_next(bc))

    def _launch_next(self, bc):
        if not self._queue:
            self.log("-" * 60)
            self.log(f"Done. Opened {self._opened} file(s), skipped {self._skipped}, excluded {self._excluded}.")
            self.btn_open.config(state="normal")
            return

        status, local, rel, remote = self._queue.pop(0)

        try:
            subprocess.Popen(
                [bc, local, remote],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as ex:
            self.log(f"ERROR: could not start Beyond Compare: {ex}")
            messagebox.showerror("Beyond Compare", f"Could not start Beyond Compare:\n\n{ex}")
            self._queue = []
            self.btn_open.config(state="normal")
            return

        self._opened += 1
        self.log(f"OPEN  [{status_label(status)}] {rel}")

        self.root.after(NEXT_LAUNCH_DELAY_MS, lambda: self._launch_next(bc))

    def generate_logs(self):
        project = self.project_var.get().strip()

        if not project or not os.path.isdir(project):
            messagebox.showerror("Project Folder", "Select a valid project folder.")
            return

        if self.manual_var.get():
            messagebox.showinfo(
                "Generate Logs",
                "For a non-Git project, this button needs a defined rule "
                "for which files to include in the deployment log."
            )
            return

        base = os.path.normpath(project)
        try:
            files, skipped = get_changed_files(base)
        except GitError as ex:
            messagebox.showerror("Git Error", str(ex))
            return

        masks = parse_exclusions(self.exclude_var.get()) + parse_file_exclusions(self.exclude_files_var.get(), base)
        rels = []
        excluded = []
        for status, local in files:
            rel = os.path.relpath(local, base).replace("\\", "/")
            if not is_excluded(rel, masks):
                rels.append(rel)
            else:
                excluded.append(rel)
        rels.extend(rel for _, rel in skipped)   # deleted files still belong in the log

        if not rels:
            messagebox.showinfo("Generate Logs", "No changed files found.")
            return

        rels.sort(key=lambda r: (os.path.dirname(r).lower(), r.lower()))

        lines, last_dir = [], None
        for rel in rels:
            d = os.path.dirname(rel).lower()
            if last_dir is not None and d != last_dir:
                lines.append("")                 # blank line between folders
            lines.append(log_path(base, rel))
            last_dir = d

        if self.log_box.get("1.0", "end").strip():
            self.log("")                         # blank line between projects
        self.log(f"# {os.path.basename(base)}")
        self.log("")
        if excluded:
            self.log(f"# Excluded files ({len(excluded)}):")
            for rel in sorted(excluded):
                self.log(f"# - {rel}")
            self.log("")
        self.log("\n".join(lines))

def main():
    root = tk.Tk()

    root.report_callback_exception = lambda exc, val, tb: messagebox.showerror(
        "Unexpected error", "".join(traceback.format_exception(exc, val, tb))
    )
    # Prefer ICO, fall back to PNG
    try:
        root.iconbitmap(resource_path("SITCompare.ico"))
    except Exception:
        try:
            root.iconphoto(True, tk.PhotoImage(file=resource_path("SITCompare.png")))
        except Exception:
            pass

    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()