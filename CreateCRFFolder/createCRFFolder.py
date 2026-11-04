import glob
import os
import shutil
import sys
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox

CONFIG_FILE = "config.txt"

SEPARATOR = "-------------------------------------------------------------------------------------------------------------------------------------------------------------------------"

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


def _darken(hex_color, factor=0.88):
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return "#{:02x}{:02x}{:02x}".format(int(r * factor), int(g * factor), int(b * factor))


def make_button(parent, text, command, bg, font_size=10, bold=False, pady=9):
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
        padx=14,
        pady=pady,
        cursor="hand2",
    )
    btn.bind("<Enter>", lambda e: btn.config(bg=hover))
    btn.bind("<Leave>", lambda e: btn.config(bg=bg))
    return btn


def make_entry(parent, **kwargs):
    return tk.Entry(
        parent,
        font=(FONT, 10),
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


def make_field_label(parent, text):
    return tk.Label(
        parent,
        text=text,
        font=(FONT, 10, "bold"),
        bg=COLOR_CARD,
        fg=COLOR_TEXT,
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


# ============================================================
#               CONFIG LOAD / SAVE
# ============================================================

def resource_path(relative_path):
    """Get the absolute path to a bundled resource."""
    try:
        # PyInstaller creates a temporary folder and stores its path in _MEIPASS
        base_path = sys._MEIPASS
    except AttributeError:
        # Running the Python script directly
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


def load_config():
    """Load parent directory and last created folder from config.txt"""
    if not os.path.exists(CONFIG_FILE):
        return None, None

    parent_dir = None
    last_folder = None

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        for line in f.readlines():
            if line.startswith("PARENT_DIR="):
                value = line.replace("PARENT_DIR=", "").strip()
                if os.path.isdir(value):
                    parent_dir = value
            if line.startswith("LAST_FOLDER="):
                value = line.replace("LAST_FOLDER=", "").strip()
                if os.path.isdir(value):
                    last_folder = value

    return parent_dir, last_folder


def save_config(parent_dir=None, last_folder=None):
    """Save parent directory and last created CRF folder to config.txt"""

    old_parent, old_last_folder = load_config()

    parent_dir = parent_dir or old_parent or ""
    last_folder = last_folder or old_last_folder or ""

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(f"PARENT_DIR={parent_dir}\n")
        f.write(f"LAST_FOLDER={last_folder}\n")


# ============================================================
#               BUTTON ACTIONS
# ============================================================

def copy_crf_file(folder_name, dest_folder):
    """Look for a CRF PDF in Downloads matching folder_name and copy it to dest_folder.
    Returns True if a file was copied, False otherwise."""
    downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
    if not os.path.isdir(downloads_dir):
        return False

    pattern = os.path.join(downloads_dir, f"{folder_name}*.pdf")
    matches = glob.glob(pattern)

    if not matches:
        return False

    # If multiple matches, take the most recently modified one
    src_file = max(matches, key=os.path.getmtime)
    dest_file = os.path.join(dest_folder, os.path.basename(src_file))

    try:
        shutil.copy2(src_file, dest_file)
        return True
    except Exception:
        return False


def choose_directory():
    """Ask user for parent directory and save it."""
    chosen_dir = filedialog.askdirectory(title="Select Parent Directory")
    if chosen_dir:
        save_config(parent_dir=chosen_dir)
        parent_dir_var.set(chosen_dir)


def check_and_create():
    folder_name = entry.get().strip()

    if not folder_name:
        messagebox.showerror("Error", "Please enter a folder name")
        return

    parent_dir = parent_dir_var.get().strip()
    if not parent_dir:
        messagebox.showerror("Error", "Please select a parent directory")
        return

    main_folder = os.path.join(parent_dir, folder_name)

    # Check if folder exists
    if os.path.exists(main_folder):
        result = messagebox.askyesno("Folder Exists", f"'{folder_name}' already exists.\nDo you still want to create the folder structure inside it?")
        if not result:
            return  # user cancelled
    else:
        result = messagebox.askyesno("Create Folder?", f"'{folder_name}' does not exist.\nDo you want to create it?")
        if not result:
            return  # user cancelled

    try:
        # Create main folder if not exists
        os.makedirs(main_folder, exist_ok=True)

        # Create Dev Scripts with Backup
        dev_path = os.path.join(main_folder, "Dev Scripts")
        os.makedirs(os.path.join(dev_path, "Backup"), exist_ok=True)

        # Create empty test.sql inside Dev Scripts
        test_file = os.path.join(dev_path, "test.sql")
        open(test_file, 'w').close()

        # Create SIT Scripts with Backup
        sit_path = os.path.join(main_folder, "SIT Scripts")
        os.makedirs(os.path.join(sit_path, "Backup"), exist_ok=True)

        # File paths
        queries_file = os.path.join(main_folder, f"{folder_name}_Queries.sql")
        deployment_file = os.path.join(main_folder, f"{folder_name}_DeploymentQueries.sql")

        # Get current date formatted as YYYY-MM-DD
        current_date = datetime.now().strftime("%Y-%m-%d")

        # Boilerplate content for Queries.sql
        boilerplate = f"""-- {folder_name} -- [{current_date}]

-- CUS-9045343   - EO for getting GDs for Assessment after Completing Examination

-- DEV
-- CUS-7895487  - AC/DC MIS
-- CUS-1654897\t- EO CEU
-- cus_kapr_eo1\t- EO
-- CUS-1569875 \t- Lab Manager/ Lab Officer
-- DEV

-- SIT
-- CUS-1234569   - AC/DC MIS
-- CUS-1478963   - AO, QA, PA, AO PRV   - Muhammad Ahsan 2
-- CUS-1478925   - AO, QA, PA, AO PRV   - Muhammad Ahsan 5
-- CUS-5686956   - AO, QA, PA, AO PRV\t- Muhammad Ahsan AO QA Shed
-- cus_kpqi_acmis - AO PRV - Data available
-- CUS-3784782    - AO PRV\t- Data available
-- SIT

-- QA (Test)
-- CUS-0293090 - Normal AO with 100 GDs
-- QA (Test)

{SEPARATOR}
{SEPARATOR}
{SEPARATOR}
{SEPARATOR}
"""

        # Write boilerplate to Queries.sql
        with open(queries_file, 'w', encoding='utf-8') as f:
            f.write(boilerplate)

        boilerplateD = f"""-- {folder_name}

{SEPARATOR}
{SEPARATOR}
{SEPARATOR}
{SEPARATOR}
"""
        # Write boilerplate to DeploymentQueries.sql
        with open(deployment_file, 'w', encoding='utf-8') as f:
            f.write(boilerplateD)

        # Save config (base parent dir + this newly created folder as the "last folder")
        save_config(parent_dir=parent_dir, last_folder=main_folder)

        # Try to copy matching CRF PDF from Downloads
        file_moved = copy_crf_file(folder_name, main_folder)

        success_msg = f"Folder structure and SQL files created at:\n{main_folder}"
        if file_moved:
            success_msg += "\n\nCRF file copied as well."

        messagebox.showinfo("Success", success_msg)

    except Exception as e:
        messagebox.showerror("Error", f"Failed to create structure:\n{str(e)}")


def open_folder():
    """Open the last created CRF folder, similar to 'Open File' for scripts."""
    _, last_folder = load_config()

    # If a recent folder exists, offer to open it directly
    if last_folder:
        ask = messagebox.askyesno(
            "Open Recent Folder",
            f"Open the last created folder?\n\n{last_folder}"
        )
        if ask:
            os.startfile(last_folder)
            return

    # Otherwise let the user browse for a folder
    folder_path = filedialog.askdirectory(title="Select CRF Folder to Open")

    if folder_path:
        save_config(last_folder=folder_path)
        os.startfile(folder_path)

def copy_crf_pdf_now():
    """Manually copy a matching CRF PDF from Downloads into the last created folder
    (or let the user choose a folder)."""
    _, last_folder = load_config()

    target_folder = None

    if last_folder and os.path.isdir(last_folder):
        ask = messagebox.askyesno(
            "Copy CRF PDF",
            f"Copy matching PDF into the last created folder?\n\n{last_folder}"
        )
        if ask:
            target_folder = last_folder

    if not target_folder:
        target_folder = filedialog.askdirectory(title="Select CRF Folder to copy PDF into")
        if not target_folder:
            return  # user cancelled

    folder_name = os.path.basename(target_folder)

    if copy_crf_file(folder_name, target_folder):
        messagebox.showinfo("Success", f"CRF PDF copied into:\n{target_folder}")
        # Remember this folder as the last one
        save_config(last_folder=target_folder)
    else:
        messagebox.showwarning(
            "Not Found",
            f"No matching PDF found in Downloads for:\n\"{folder_name}*.pdf\""
        )

# ============================================================
#               GUI SETUP
# ============================================================

root = tk.Tk()
root.title("Folder Structure Creator")
root.geometry("640x450")
root.minsize(560, 410)
root.configure(bg=COLOR_BG)

# Prefer ICO
try:
    root.iconbitmap(resource_path("CRFFolder.ico"))
except Exception:
    # Fallback to PNG
    try:
        icon_img = tk.PhotoImage(file=resource_path("CRFFolder.png"))
        root.iconphoto(True, icon_img)
    except Exception:
        pass

# Load last config (parent dir + last created folder)
last_dir, last_folder = load_config()

card = make_card(root, "Create CRF Folder Structure")
card.pack(fill="both", expand=True, padx=14, pady=14)

form = tk.Frame(card, bg=COLOR_CARD)
form.pack(fill="both", expand=True, padx=20, pady=16)

# --- Folder name ---
make_field_label(form, "Folder Name").pack(fill="x", pady=(0, 4))
entry = make_entry(form)
entry.pack(fill="x", ipady=6, pady=(0, 14))
entry.focus_set()

# --- Parent directory ---
make_field_label(form, "Parent Directory").pack(fill="x", pady=(0, 4))
parent_row = tk.Frame(form, bg=COLOR_CARD)
parent_row.pack(fill="x", pady=(0, 18))

parent_dir_var = tk.StringVar(value=last_dir if last_dir else "")
parent_dir_entry = make_entry(parent_row, textvariable=parent_dir_var)
parent_dir_entry.pack(side="left", fill="x", expand=True, ipady=6)

browse_button = make_button(parent_row, "Browse", choose_directory, COLOR_ACCENT, pady=5)
browse_button.pack(side="left", padx=(8, 0))

# --- Actions ---
actions = tk.Frame(form, bg=COLOR_CARD)
actions.pack(fill="x", pady=(12, 16))

create_button = make_button(
    actions, "Check & Create Folder", check_and_create, COLOR_GREEN,
    font_size=10, bold=True, pady=9
)
create_button.pack(side="left", expand=True, fill="x", padx=(0, 6))

open_button = make_button(
    actions, "Open Folder", open_folder, COLOR_TEAL,
    font_size=10, bold=True, pady=9
)
open_button.pack(side="left", expand=True, fill="x", padx=6)

copy_button = make_button(
    actions, "Copy CRF PDF", copy_crf_pdf_now, COLOR_ACCENT,
    font_size=10, bold=True, pady=9
)
copy_button.pack(side="left", expand=True, fill="x", padx=(6, 0))

# --- Footer tip pinned to the bottom ---
hint_label = tk.Label(
    form,
    text="Creates Dev Scripts, SIT Scripts and the Queries files. A matching CRF PDF in Downloads is copied in.",
    font=(FONT, 9),
    bg="#f1f5f9",
    fg=COLOR_MUTED,
    pady=8,
    wraplength=520,
)
hint_label.pack(side="bottom", fill="x")

root.mainloop()