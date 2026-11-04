import os
import tkinter as tk
from tkinter import filedialog, messagebox

CONFIG_FILE = "config.txt"

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
COLOR_RED = "#dc2626"


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
#               CONFIG
# ============================================================

def load_last_folder():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            saved_path = f.read().strip()
            if os.path.exists(saved_path):
                folder_path.set(saved_path)


def save_last_folder(path):
    with open(CONFIG_FILE, "w") as f:
        f.write(path)


# ============================================================
#               ACTIONS
# ============================================================

def browse_folder():
    folder_selected = filedialog.askdirectory(
        initialdir=folder_path.get() if folder_path.get() else os.getcwd()
    )
    if folder_selected:
        folder_path.set(folder_selected)
        save_last_folder(folder_selected)


def empty_sql_files():
    folder = folder_path.get()

    if not folder:
        messagebox.showwarning("Warning", "Please select a folder first.")
        return

    if not os.path.exists(folder):
        messagebox.showerror("Error", "Selected folder does not exist.")
        return

    confirm = messagebox.askyesno(
        "Confirm",
        f"Empty ALL .sql files in:\n\n{folder}\n\nThis cannot be undone."
    )

    if not confirm:
        return

    count = 0
    errors = []

    for filename in os.listdir(folder):
        if filename.lower().endswith(".sql"):
            file_path = os.path.join(folder, filename)
            try:
                with open(file_path, "w"):
                    pass
                count += 1
            except Exception as e:
                errors.append(f"{filename}: {str(e)}")

    result = f"Successfully emptied {count} .sql file(s)."

    if errors:
        result += "\n\nErrors:\n" + "\n".join(errors)

    messagebox.showinfo("Completed", result)


# ============================================================
#               GUI SETUP
# ============================================================
root = tk.Tk()
root.title("Empty .SQL Files Tool")
root.geometry("640x340")
root.minsize(560, 320)
root.configure(bg=COLOR_BG)

folder_path = tk.StringVar()

card = make_card(root, "Empty .SQL Files")
card.pack(fill="both", expand=True, padx=14, pady=14)

form = tk.Frame(card, bg=COLOR_CARD)
form.pack(fill="both", expand=True, padx=20, pady=16)

make_field_label(form, "Folder (last used is auto-loaded)").pack(fill="x", pady=(0, 4))

folder_row = tk.Frame(form, bg=COLOR_CARD)
folder_row.pack(fill="x", pady=(0, 18))

folder_entry = make_entry(folder_row, textvariable=folder_path)
folder_entry.pack(side="left", fill="x", expand=True, ipady=6)

browse_button = make_button(folder_row, "Browse", browse_folder, COLOR_ACCENT, pady=5)
browse_button.pack(side="left", padx=(8, 0))

empty_button = make_button(
    form, "Empty All .SQL Files", empty_sql_files, COLOR_RED,
    font_size=11, bold=True, pady=9
)
empty_button.config(width=22)
empty_button.pack()  # no fill: stays small and centered

# --- Footer warning pinned to the bottom ---
hint_label = tk.Label(
    form,
    text="Empties every .sql file directly inside the folder (subfolders are not touched). This cannot be undone.",
    font=(FONT, 9),
    bg="#fef2f2",
    fg="#991b1b",
    pady=8,
    wraplength=520,
)
hint_label.pack(side="bottom", fill="x")

# Load last used folder on startup
load_last_folder()

root.mainloop()