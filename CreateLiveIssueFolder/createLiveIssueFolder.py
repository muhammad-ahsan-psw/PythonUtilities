import os
import re
import sys
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, filedialog, simpledialog, Toplevel, Listbox, Scrollbar, Frame, Label, Button, Text

CONFIG_FILE = "config.txt"

# ============================================================
#               CONFIG LOAD / SAVE
# ============================================================

def load_config():
    """Load parent directory, compiled directory, and last file from config.txt"""
    if not os.path.exists(CONFIG_FILE):
        return None, None, None

    parent_dir = None
    last_file = None
    compiled_dir = None

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
        for line in lines:
            if line.startswith("PARENT_DIR="):
                value = line.replace("PARENT_DIR=", "").strip()
                if os.path.isdir(value):
                    parent_dir = value
            if line.startswith("LAST_FILE="):
                value = line.replace("LAST_FILE=", "").strip()
                if os.path.exists(value):
                    last_file = value
            if line.startswith("COMPILED_DIR="):
                value = line.replace("COMPILED_DIR=", "").strip()
                if value:
                    compiled_dir = value

    return parent_dir, last_file, compiled_dir


def save_config(parent_dir=None, last_file=None, compiled_dir=None):
    """Save parent directory, compiled directory, and last opened file to config.txt"""

    old_parent, old_last_file, old_compiled_dir = load_config()

    parent_dir = parent_dir or old_parent or ""
    last_file = last_file or old_last_file or ""
    compiled_dir = compiled_dir or old_compiled_dir or ""

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(f"PARENT_DIR={parent_dir}\n")
        f.write(f"LAST_FILE={last_file}\n")
        f.write(f"COMPILED_DIR={compiled_dir}\n")


# ============================================================
#               COMPILED SCRIPTS LOGIC
# ============================================================

def get_quarter(month):
    """Return quarter number (1-4) for a given month (1-12)"""
    return (month - 1) // 3 + 1


# ============================================================
#               FOLDER NAME SANITIZATION
# ============================================================

INVALID_FOLDER_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_folder_name(name):
    """Replace Windows-invalid path characters with '-' so pasted titles
    (e.g. ticket titles containing '/', ':', '?', etc.) can be used safely
    as a folder name."""
    name = INVALID_FOLDER_CHARS.sub('-', name)
    name = re.sub(r'-{2,}', '-', name)   # collapse repeated dashes
    name = name.strip(' .-')             # Windows disallows trailing space/dot
    return name or "Untitled"


def append_to_compiled_files(compiled_dir, content, quarter_num, year):
    """
    Append `content` to three rolling merge files (used only for NEW issues):
    - <compiled_dir>/<year>/All_LiveIssues_Scripts_<year>_Q<n>.sql
    - <compiled_dir>/<year>/All_LiveIssues_Scripts_<year>.sql
    - <compiled_dir>/All_LiveIssues_Scripts.sql
    Creates any missing folders. Does nothing if compiled_dir is blank.
    """
    if not compiled_dir:
        return None

    year_str = str(year)
    quarter_str = f"Q{quarter_num}"

    year_dir = os.path.join(compiled_dir, year_str)
    os.makedirs(year_dir, exist_ok=True)

    quarter_file = os.path.join(year_dir, f"All_LiveIssues_Scripts_{year_str}_{quarter_str}.sql")
    year_file = os.path.join(year_dir, f"All_LiveIssues_Scripts_{year_str}.sql")
    main_file = os.path.join(compiled_dir, "All_LiveIssues_Scripts.sql")

    for target_file in (quarter_file, year_file, main_file):
        with open(target_file, "a", encoding="utf-8") as f:
            f.write("\n\n" + content + "\n")

    return quarter_file, year_file, main_file


def _replace_or_append_in_file(filepath, folder_name, new_content):
    """
    Inside one compiled file:
    - If a block for this folder_name already exists → replace it with new_content.
    - If not found → append new_content.
    Returns True if the file was modified (or created).
    """
    # Match any block that starts with "-- Live Issue -- {folder_name}"
    # regardless of the date that follows.
    # Stops at the next "-- Live Issue --" header or end of file.
    pattern = re.compile(
        rf"(?ms)"                                          # multiline + dotall
        rf"^-- Live Issue -- {re.escape(folder_name)}"     # exact folder name (date ignored)
        rf".*?"                                            # rest of header + body (non-greedy)
        rf"(?=\n-- Live Issue -- |\Z)"                     # stop before next issue or EOF
    )

    if not os.path.exists(filepath):
        # File does not exist yet → create it with the new content
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content.rstrip() + "\n")
        return True

    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    match = pattern.search(text)
    if match:
        # Replace the existing block
        # Keep a clean double-newline separation
        replacement = new_content.rstrip() + "\n\n"
        new_text = pattern.sub(replacement, text, count=1)
        # Clean up any accidental triple+ blank lines
        new_text = re.sub(r"\n{3,}", "\n\n", new_text)
    else:
        # Not found → append
        new_text = text.rstrip() + "\n\n" + new_content.rstrip() + "\n"

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_text)

    return True


def replace_in_compiled_files(compiled_dir, folder_name, content, quarter_num, year):
    """
    Replace (or append if missing) the issue block for `folder_name` inside the
    three compiled files. Used by the Update Scripts flow.
    Returns the three file paths, or None if compiled_dir is blank.
    """
    if not compiled_dir:
        return None

    year_str = str(year)
    quarter_str = f"Q{quarter_num}"

    year_dir = os.path.join(compiled_dir, year_str)
    os.makedirs(year_dir, exist_ok=True)

    quarter_file = os.path.join(year_dir, f"All_LiveIssues_Scripts_{year_str}_{quarter_str}.sql")
    year_file = os.path.join(year_dir, f"All_LiveIssues_Scripts_{year_str}.sql")
    main_file = os.path.join(compiled_dir, "All_LiveIssues_Scripts.sql")

    for target_file in (quarter_file, year_file, main_file):
        _replace_or_append_in_file(target_file, folder_name, content)

    return quarter_file, year_file, main_file


# ============================================================
#               DUPLICATE DETECTION & CLEANING
# ============================================================

# Matches a full issue block. Captures the folder name in group 1.
ISSUE_BLOCK_RE = re.compile(
    r"(?ms)"
    r"(^-- Live Issue -- (.*?)(?: -- \[.*?\])?\s*\n"   # header line, capture name
    r".*?"                                              # body
    r"(?=\n-- Live Issue -- |\Z))"                      # up to next header or EOF
)


def _extract_issue_blocks(text):
    """
    Return a list of (folder_name, full_block_text) in the order they appear.
    """
    blocks = []
    for m in ISSUE_BLOCK_RE.finditer(text):
        full_block = m.group(1).rstrip()
        name = m.group(2).strip()
        if name:
            blocks.append((name, full_block))
    return blocks


def _analyze_duplicates(filepath):
    """
    Return a dict: { folder_name: count } for names that appear more than once.
    Also returns total unique issues and total blocks.
    """
    if not os.path.exists(filepath):
        return {}, 0, 0

    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    blocks = _extract_issue_blocks(text)
    counts = {}
    for name, _ in blocks:
        counts[name] = counts.get(name, 0) + 1

    duplicates = {name: cnt for name, cnt in counts.items() if cnt > 1}
    return duplicates, len(counts), len(blocks)


def _clean_duplicates_keep_last(filepath):
    """
    Keep only the LAST occurrence of each issue (by folder name).
    Rebuilds the file. Returns (removed_count, remaining_count).
    """
    if not os.path.exists(filepath):
        return 0, 0

    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    blocks = _extract_issue_blocks(text)
    if not blocks:
        return 0, 0

    # Walk from the end so the first time we see a name is the last occurrence
    seen = set()
    kept_reversed = []
    for name, block in reversed(blocks):
        if name not in seen:
            seen.add(name)
            kept_reversed.append(block)

    kept = list(reversed(kept_reversed))
    new_text = "\n\n".join(kept) + "\n"

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_text)

    removed = len(blocks) - len(kept)
    return removed, len(kept)


def get_compiled_file_paths(compiled_dir):
    """
    Return a list of existing compiled .sql files under the compiled directory.
    Includes the all-years file and every year/quarter file that exists.
    """
    if not compiled_dir or not os.path.isdir(compiled_dir):
        return []

    paths = []

    # All-years file
    main_file = os.path.join(compiled_dir, "All_LiveIssues_Scripts.sql")
    if os.path.isfile(main_file):
        paths.append(main_file)

    # Year folders
    for entry in sorted(os.listdir(compiled_dir)):
        year_path = os.path.join(compiled_dir, entry)
        if not os.path.isdir(year_path):
            continue
        # Yearly file
        yearly = os.path.join(year_path, f"All_LiveIssues_Scripts_{entry}.sql")
        if os.path.isfile(yearly):
            paths.append(yearly)
        # Quarterly files
        for fname in sorted(os.listdir(year_path)):
            if fname.startswith(f"All_LiveIssues_Scripts_{entry}_Q") and fname.endswith(".sql"):
                paths.append(os.path.join(year_path, fname))

    return paths


def show_duplicates_report_and_clean():
    """
    1. Scan all compiled files and build a report of duplicates.
    2. Show the report to the user.
    3. If user confirms, clean each file keeping only the last occurrence.
    """
    compiled_dir = compiled_dir_var.get().strip()
    if not compiled_dir:
        messagebox.showerror(
            "Error",
            "Please set the Compiled Scripts Directory first."
        )
        return

    files = get_compiled_file_paths(compiled_dir)
    if not files:
        messagebox.showinfo(
            "No Files",
            "No compiled SQL files were found in the configured directory."
        )
        return

    # ---------- Build report ----------
    report_lines = []
    files_with_dupes = []
    total_dupe_instances = 0

    for fp in files:
        dups, unique_count, total_blocks = _analyze_duplicates(fp)
        rel = os.path.relpath(fp, compiled_dir)
        if dups:
            files_with_dupes.append(fp)
            report_lines.append(f"FILE: {rel}")
            report_lines.append(f"  Total blocks: {total_blocks}  |  Unique issues: {unique_count}")
            report_lines.append("  Duplicates:")
            for name, cnt in sorted(dups.items(), key=lambda x: -x[1]):
                report_lines.append(f"    • {name}  →  {cnt} times")
                total_dupe_instances += (cnt - 1)  # extras that will be removed
            report_lines.append("")
        else:
            report_lines.append(f"FILE: {rel}")
            report_lines.append(f"  No duplicates  (blocks: {total_blocks}, unique: {unique_count})")
            report_lines.append("")

    if not files_with_dupes:
        messagebox.showinfo(
            "No Duplicates Found",
            "All compiled files are clean — no duplicate issues detected."
        )
        return

    # ---------- Show report dialog ----------
    dialog = Toplevel(root)
    dialog.title("Duplicate Issues Found")
    dialog.geometry("720x480")
    dialog.transient(root)
    dialog.grab_set()

    Label(
        dialog,
        text="The following duplicates were found.\n"
             "Cleaning will keep only the LAST occurrence of each issue and remove the older ones.",
        font=("Arial", 11),
        justify="left"
    ).pack(pady=(12, 6), padx=12, anchor="w")

    frame = Frame(dialog)
    frame.pack(fill="both", expand=True, padx=12, pady=4)

    scrollbar = Scrollbar(frame)
    scrollbar.pack(side="right", fill="y")

    text_widget = Text(
        frame,
        font=("Consolas", 10),
        yscrollcommand=scrollbar.set,
        wrap="none"
    )
    text_widget.pack(side="left", fill="both", expand=True)
    scrollbar.config(command=text_widget.yview)

    text_widget.insert("1.0", "\n".join(report_lines))
    text_widget.config(state="disabled")

    result = {"confirmed": False}

    def on_clean():
        result["confirmed"] = True
        dialog.destroy()

    def on_cancel():
        dialog.destroy()

    btn_frame = Frame(dialog)
    btn_frame.pack(pady=10)

    Button(
        btn_frame,
        text=f"Clean Duplicates  (will remove ~{total_dupe_instances} older copies)",
        command=on_clean,
        font=("Arial", 10),
        bg="#c0392b",
        fg="white",
        padx=8
    ).pack(side="left", padx=6)

    Button(
        btn_frame,
        text="Cancel",
        command=on_cancel,
        font=("Arial", 10),
        width=10
    ).pack(side="left", padx=6)

    # Center
    dialog.update_idletasks()
    x = root.winfo_rootx() + (root.winfo_width() // 2) - (dialog.winfo_width() // 2)
    y = root.winfo_rooty() + (root.winfo_height() // 2) - (dialog.winfo_height() // 2)
    dialog.geometry(f"+{x}+{y}")

    root.wait_window(dialog)

    if not result["confirmed"]:
        return

    # ---------- Perform cleaning ----------
    summary = []
    for fp in files_with_dupes:
        removed, remaining = _clean_duplicates_keep_last(fp)
        rel = os.path.relpath(fp, compiled_dir)
        summary.append(f"• {rel}\n    Removed {removed} older copy/copies  →  {remaining} issues kept")

    messagebox.showinfo(
        "Cleaning Complete",
        "Duplicates have been removed.\nOnly the last occurrence of each issue was kept.\n\n"
        + "\n".join(summary)
    )


# ============================================================
#               BUTTON ACTIONS
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

def choose_directory():
    """Ask user for the base parent directory and save it."""
    chosen_dir = filedialog.askdirectory(title="Select Base Parent Directory")
    if chosen_dir:
        save_config(parent_dir=chosen_dir)
        parent_dir_var.set(chosen_dir)


def choose_compiled_directory():
    """Ask user for the compiled scripts directory and save it."""
    chosen_dir = filedialog.askdirectory(title="Select Compiled Scripts Directory")
    if chosen_dir:
        save_config(compiled_dir=chosen_dir)
        compiled_dir_var.set(chosen_dir)


def choose_from_multiple_matches(matches):
    """
    Show a dialog listing multiple matching issue folders so the user can
    pick which one to load. Returns the chosen path, or None if cancelled.
    matches: list of (display_label, full_path) tuples.
    """
    if not matches:
        return None

    dialog = Toplevel(root)
    dialog.title("Multiple Matches Found")
    dialog.geometry("650x320")
    dialog.transient(root)
    dialog.grab_set()

    Label(
        dialog,
        text="Multiple issue folders match your search.\nSelect the one you want to load:",
        font=("Arial", 11),
        justify="left"
    ).pack(pady=(12, 6), padx=12, anchor="w")

    frame = Frame(dialog)
    frame.pack(fill="both", expand=True, padx=12, pady=4)

    scrollbar = Scrollbar(frame)
    scrollbar.pack(side="right", fill="y")

    listbox = Listbox(
        frame,
        font=("Consolas", 10),
        yscrollcommand=scrollbar.set,
        selectmode="single",
        activestyle="dotbox"
    )
    listbox.pack(side="left", fill="both", expand=True)
    scrollbar.config(command=listbox.yview)

    for label, _ in matches:
        listbox.insert(tk.END, label)

    listbox.selection_set(0)
    listbox.focus_set()

    chosen = {"path": None}

    def on_ok():
        sel = listbox.curselection()
        if sel:
            chosen["path"] = matches[sel[0]][1]
        dialog.destroy()

    def on_cancel():
        dialog.destroy()

    def on_double_click(event):
        on_ok()

    listbox.bind("<Double-Button-1>", on_double_click)
    listbox.bind("<Return>", lambda e: on_ok())

    btn_frame = Frame(dialog)
    btn_frame.pack(pady=10)

    Button(btn_frame, text="Load Selected", command=on_ok,
           font=("Arial", 10), bg="blue", fg="white", width=14).pack(side="left", padx=6)
    Button(btn_frame, text="Cancel", command=on_cancel,
           font=("Arial", 10), width=10).pack(side="left", padx=6)

    # Center the dialog over the main window
    dialog.update_idletasks()
    x = root.winfo_rootx() + (root.winfo_width() // 2) - (dialog.winfo_width() // 2)
    y = root.winfo_rooty() + (root.winfo_height() // 2) - (dialog.winfo_height() // 2)
    dialog.geometry(f"+{x}+{y}")

    root.wait_window(dialog)
    return chosen["path"]


def load_existing_issue_file(main_folder):
    """Ask before loading an existing Scripts.sql and populate the UI."""
    queries_file = os.path.join(main_folder, "Scripts.sql")

    result = messagebox.askyesno(
        "Folder Already Exists",
        f"'{os.path.basename(main_folder)}' already exists.\n\n"
        "Do you want to load the existing Scripts.sql?"
    )

    if not result:
        return False

    if not os.path.exists(queries_file):
        messagebox.showwarning(
            "Scripts File Not Found",
            f"The issue folder exists, but Scripts.sql was not found:\n\n{main_folder}"
        )
        return False

    try:
        with open(queries_file, "r", encoding="utf-8") as f:
            existing_content = f.read()

        # Extract folder name and issue date from the existing header.
        header_match = re.search(
            r"-- Live Issue -- (.*?) -- \[(\d{2}-[A-Za-z]{3}-\d{4})\]",
            existing_content
        )

        if header_match:
            entry.delete(0, tk.END)
            entry.insert(0, header_match.group(1))
            issue_date_var.set(header_match.group(2))

        # Load existing complete file content into Additional Content.
        content_text.delete("1.0", tk.END)
        content_text.insert("1.0", existing_content)

        # Remember the existing Scripts.sql for the Update Scripts action.
        loaded_scripts_file_var.set(queries_file)

        # Switch the main action button from Create to Update.
        create_button.config(
            text="Update Scripts",
            command=update_existing_scripts
        )

        messagebox.showinfo(
            "Scripts Loaded",
            "Existing Scripts.sql has been loaded.\n\n"
            "You can now modify the content and click 'Update Scripts' "
            "to save the latest changes."
        )
        return True

    except Exception as e:
        messagebox.showerror(
            "Error",
            f"Failed to load the existing Scripts.sql file:\n{str(e)}"
        )
        return False


def update_existing_scripts():
    """Update the previously loaded Scripts.sql with the latest UI content.
    Also replaces the same issue block inside the three compiled files.
    """
    queries_file = loaded_scripts_file_var.get().strip()

    if not queries_file:
        messagebox.showerror(
            "Error",
            "No existing Scripts.sql file is loaded."
        )
        return

    if not os.path.exists(queries_file):
        messagebox.showerror(
            "Error",
            f"The Scripts.sql file no longer exists:\n{queries_file}"
        )
        return

    folder_title = entry.get().strip()
    if not folder_title:
        messagebox.showerror("Error", "Please enter a folder name")
        return

    folder_name = sanitize_folder_name(folder_title)

    issue_date_text = issue_date_var.get().strip()

    try:
        issue_date = datetime.strptime(issue_date_text, "%d-%b-%Y")
    except ValueError:
        messagebox.showerror(
            "Invalid Date",
            "Please enter the Live Issue Date in DD-Mon-YYYY format.\n\n"
            "Example: 10-Aug-2026"
        )
        return

    issue_date_formatted = issue_date.strftime("%d-%b-%Y")

    # Use the current Additional Content as the latest script content.
    latest_content = content_text.get("1.0", tk.END).strip()

    # Remove an existing Live Issue header/separator if the complete
    # Scripts.sql was loaded into Additional Content.
    latest_content = re.sub(
        r"^-- Live Issue -- .*? -- \[\d{2}-[A-Za-z]{3}-\d{4}\]\s*\n"
        r"-{10,}\s*\n?",
        "",
        latest_content,
        count=1
    )

    # Remove the trailing separator if present.
    latest_content = re.sub(
        r"\s*-{10,}\s*$",
        "",
        latest_content
    ).strip()

    boilerplate = f"""-- Live Issue -- {folder_name} -- [{issue_date_formatted}]
-------------------------------------------------------------------------------------------------------------------------------------------------------------------------
"""

    if latest_content:
        final_text = boilerplate + "\n" + latest_content + "\n"
    else:
        final_text = boilerplate

    final_text += "\n" + "-------------------------------------------------------------------------------------------------------------------------------------------------------------------------"

    try:
        # 1. Overwrite the individual Scripts.sql
        with open(queries_file, "w", encoding="utf-8") as f:
            f.write(final_text)

        # 2. Replace (or append if missing) inside the three compiled files
        compiled_dir = compiled_dir_var.get().strip()
        now = datetime.now()
        quarter_num = get_quarter(now.month)
        compiled_paths = replace_in_compiled_files(
            compiled_dir,
            folder_name,
            final_text,
            quarter_num,
            now.year
        )

        save_config(
            parent_dir=parent_dir_var.get().strip(),
            last_file=queries_file,
            compiled_dir=compiled_dir
        )

        if compiled_paths:
            quarter_file, year_file, main_file = compiled_paths
            messagebox.showinfo(
                "Scripts Updated",
                f"Scripts.sql has been updated successfully:\n\n"
                f"{queries_file}\n\n"
                f"Replaced/updated in compiled files:\n"
                f"- {quarter_file}\n"
                f"- {year_file}\n"
                f"- {main_file}"
            )
        else:
            messagebox.showinfo(
                "Scripts Updated",
                f"Scripts.sql has been updated successfully:\n\n{queries_file}"
            )

    except Exception as e:
        messagebox.showerror(
            "Error",
            f"Failed to update Scripts.sql:\n{str(e)}"
        )


def reset_to_create_mode():
    """Reset the UI from Update Scripts mode back to Check & Create Folder mode."""
    loaded_scripts_file_var.set("")

    create_button.config(
        text="Check & Create Folder",
        command=check_and_create
    )

    content_text.delete("1.0", tk.END)
    issue_date_var.set(datetime.now().strftime("%d-%b-%Y"))

    messagebox.showinfo(
        "Reset",
        "The form has been reset to Check & Create Folder mode."
    )


def check_and_create():
    folder_title = entry.get().strip()

    if not folder_title:
        messagebox.showerror("Error", "Please enter a folder name")
        return

    folder_name = sanitize_folder_name(folder_title)

    base_parent_dir = parent_dir_var.get().strip()
    if not base_parent_dir:
        messagebox.showerror("Error", "Please select a parent directory")
        return

    # --- AUTOMATED MONTH FOLDER LOGIC ---
    now = datetime.now()
    # format %m gives '06', using lstrip("0") turns it into '6'
    month_num = now.strftime("%m").lstrip("0")
    month_str = now.strftime("%b-%Y") # e.g., 'Jun-2026'
    month_folder_name = f"{month_num}-{month_str}" # e.g., '6-Jun-2026'

    # Target path becomes: base_path/6-Jun-2026/your_folder_name
    month_folder_path = os.path.join(base_parent_dir, month_folder_name)
    main_folder = os.path.join(month_folder_path, folder_name)
    # -------------------------------------

    # --- SEARCH ALL MONTH FOLDERS FOR EXISTING ISSUES ---
    matches = []  # list of (display_label, full_path)
    if os.path.isdir(base_parent_dir):
        search_name = folder_name.lower()
        for existing_month in sorted(os.listdir(base_parent_dir)):
            month_path = os.path.join(base_parent_dir, existing_month)
            if not os.path.isdir(month_path):
                continue
            for existing_issue in os.listdir(month_path):
                issue_path = os.path.join(month_path, existing_issue)
                if os.path.isdir(issue_path) and search_name in existing_issue.lower():
                    # Show "month_folder / issue_folder" so user can distinguish
                    display = f"{existing_month}  /  {existing_issue}"
                    matches.append((display, issue_path))

    # If exact path already exists for current month, make sure it's included
    # (it will be if the search matched; otherwise add it for consistency)
    if os.path.exists(main_folder):
        exact_display = f"{month_folder_name}  /  {folder_name}"
        if not any(p == main_folder for _, p in matches):
            matches.insert(0, (exact_display, main_folder))

    # --- HANDLE MATCHES ---
    if matches:
        if len(matches) == 1:
            # Single match – same behaviour as before
            chosen_path = matches[0][1]
        else:
            # Multiple matches – let the user choose
            chosen_path = choose_from_multiple_matches(matches)
            if not chosen_path:
                return  # user cancelled

        load_existing_issue_file(chosen_path)
        return

    # No existing match found – proceed with create flow
    # Issue folder doesn't exist. Now check if the MONTH folder is completely new.
    if not os.path.exists(month_folder_path):
        result = messagebox.askyesno(
            "New Month Detected",
            f"This is the first folder for this month.\n"
            f"Do you want to create the new month folder: "
            f"'{month_folder_name}' and the issue folder '{folder_name}' inside it?"
        )
        if not result:
            return
    else:
        # Month folder exists, issue folder is new — confirm before creating.
        result = messagebox.askyesno(
            "Create Folder",
            f"Create new folder '{folder_name}' in {month_folder_name}?"
        )
        if not result:
            return

    try:
        # os.makedirs automatically creates the month_folder_path parent directory if it doesn't exist
        os.makedirs(main_folder, exist_ok=True)

        # Optional backup folder and test.sql
        if create_backup_var.get():
            backup_folder = os.path.join(main_folder, "Backup")
            os.makedirs(backup_folder, exist_ok=True)

            test_file = os.path.join(main_folder, "test.sql")
            if not os.path.exists(test_file):
                open(test_file, "w", encoding="utf-8").close()

        # File paths
        queries_file = os.path.join(main_folder, "Scripts.sql")

        # Get the configurable Live Issue Date from the UI.
        # The field defaults to today's date but can be changed to a back date.
        issue_date_text = issue_date_var.get().strip()

        try:
            issue_date = datetime.strptime(issue_date_text, "%d-%b-%Y")
        except ValueError:
            messagebox.showerror(
                "Invalid Date",
                "Please enter the Live Issue Date in DD-Mon-YYYY format.\n\n"
                "Example: 10-Aug-2026"
            )
            return

        issue_date_formatted = issue_date.strftime("%d-%b-%Y")

        # Boilerplate content for Queries.sql
        boilerplate = f"""-- Live Issue -- {folder_name} -- [{issue_date_formatted}]
-------------------------------------------------------------------------------------------------------------------------------------------------------------------------
"""

        # Extra user content
        extra_content = content_text.get("1.0", tk.END).strip()
        if extra_content:
            final_text = boilerplate + "\n" + extra_content + "\n"
        else:
            final_text = boilerplate

        # append end line
        final_text += "\n" + "-------------------------------------------------------------------------------------------------------------------------------------------------------------------------"

        # Write to file
        with open(queries_file, "w", encoding="utf-8") as f:
            f.write(final_text)

        # --- APPEND TO COMPILED (QUARTER / YEAR / ALL-YEARS) FILES ---
        # (Only for brand-new issues)
        compiled_dir = compiled_dir_var.get().strip()
        quarter_num = get_quarter(now.month)
        compiled_paths = append_to_compiled_files(compiled_dir, final_text, quarter_num, now.year)

        # Save to config (We save the base parent dir, but track the specific file created)
        save_config(parent_dir=base_parent_dir, last_file=queries_file, compiled_dir=compiled_dir)

        if compiled_paths:
            quarter_file, year_file, main_file = compiled_paths
            messagebox.showinfo(
                "Success",
                f"Folder structure and SQL files created at:\n{main_folder}\n\n"
                f"Appended to compiled scripts:\n"
                f"- {quarter_file}\n"
                f"- {year_file}\n"
                f"- {main_file}"
            )
        else:
            messagebox.showinfo(
                "Success",
                f"Folder structure and SQL files created at:\n{main_folder}\n\n"
                f"(No Compiled Scripts Directory set, so merged files were not updated.)"
            )

    except Exception as e:
        messagebox.showerror("Error", f"Failed to create structure:\n{str(e)}")


def open_file():
    # Load config
    _, last_file, _ = load_config()

    # If recent file exists
    if last_file:
        ask = messagebox.askyesno(
            "Open Recent File",
            f"Open the last created file?\n\n{last_file}"
        )
        if ask:
            os.startfile(last_file)
            return

    # Otherwise ask user to choose a file
    file_path = filedialog.askopenfilename(
        title="Select SQL File",
        filetypes=[("SQL files", "*.sql"), ("All files", "*.*")]
    )

    if file_path:
        save_config(last_file=file_path)
        os.startfile(file_path)


def open_issue_folder():
    """
    Open the last opened issue folder in Windows Explorer (like Open File).
      1. Uses the currently loaded Scripts.sql (Update mode), else the last file in config
      2. Asks to open that issue's folder
      3. If declined or nothing is recorded, lets the user pick an issue folder
    """
    # Find the last issue folder
    queries_file = loaded_scripts_file_var.get().strip()
    if not queries_file:
        _, last_file, _ = load_config()
        queries_file = last_file or ""

    last_folder = os.path.dirname(queries_file) if queries_file else ""

    if last_folder and os.path.isdir(last_folder):
        ask = messagebox.askyesno(
            "Open Last Issue Folder",
            f"Open the last issue folder?\n\n{last_folder}"
        )
        if ask:
            try:
                os.startfile(last_folder)
            except Exception as e:
                messagebox.showerror("Error", f"Could not open folder:\n{str(e)}")
            return

    # Otherwise let the user choose an issue folder
    parent_dir = parent_dir_var.get().strip()
    initial_dir = parent_dir if os.path.isdir(parent_dir) else None

    chosen_folder = filedialog.askdirectory(
        title="Select Issue Folder",
        initialdir=initial_dir
    )

    if chosen_folder:
        chosen_folder = os.path.normpath(chosen_folder)

        # Remember it as the "last" issue if it has a Scripts.sql
        scripts_path = os.path.join(chosen_folder, "Scripts.sql")
        if os.path.isfile(scripts_path):
            save_config(last_file=scripts_path)

        try:
            os.startfile(chosen_folder)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open folder:\n{str(e)}")


# ============================================================
#               GUI SETUP
# ============================================================
FONT = "Segoe UI"
COLOR_BG = "#eef1f5"
COLOR_CARD = "#ffffff"
COLOR_HEADER = "#1f2d3d"
COLOR_TEXT = "#1f2d3d"
COLOR_MUTED = "#6b7280"
COLOR_BORDER = "#cbd5e1"
COLOR_ACCENT = "#2563eb"

root = tk.Tk()
root.title("Live Issue Folder Structure Creator")
root.geometry("1280x720")
root.minsize(1200, 680)
root.configure(bg=COLOR_BG)

try:
    root.iconbitmap(resource_path("LiveIssue.ico"))
except Exception:
    try:
        icon_img = tk.PhotoImage(file=resource_path("LiveIssue.png"))
        root.iconphoto(True, icon_img)
    except Exception:
        pass

# Tracks an existing Scripts.sql loaded for editing/updating.
loaded_scripts_file_var = tk.StringVar(value="")

last_dir, last_file, last_compiled_dir = load_config()


# ---------- UI helpers ----------
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
    header = tk.Label(
        card,
        text=title,
        font=(FONT, 11, "bold"),
        bg=COLOR_HEADER,
        fg="white",
        anchor="w",
        padx=16,
        pady=11,
    )
    header.pack(fill="x")
    return card


# ---------- Main layout ----------
main_container = tk.Frame(root, bg=COLOR_BG)
main_container.pack(fill="both", expand=True, padx=14, pady=14)

# ---------- Left card: Additional Content ----------
left_pane = make_card(main_container, "Additional Content (Optional)")
left_pane.pack(side="left", fill="both", expand=True, padx=(0, 7))

text_wrap = tk.Frame(left_pane, bg=COLOR_CARD)
text_wrap.pack(fill="both", expand=True, padx=12, pady=12)

content_scroll = tk.Scrollbar(text_wrap)
content_scroll.pack(side="right", fill="y")

content_text = tk.Text(
    text_wrap,
    font=("Consolas", 10),
    wrap="word",
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
    yscrollcommand=content_scroll.set,
)
content_text.pack(side="left", fill="both", expand=True)
content_scroll.config(command=content_text.yview)

# ---------- Right card: Details & Actions ----------
right_pane = make_card(main_container, "Issue Details & Configuration")
right_pane.pack(side="right", fill="y", padx=(7, 0))
right_pane.config(width=450)
right_pane.pack_propagate(False)

form = tk.Frame(right_pane, bg=COLOR_CARD)
form.pack(fill="both", expand=True, padx=20, pady=16)

# --- Folder Name ---
make_field_label(form, "Folder Name").pack(fill="x", pady=(0, 4))
entry = make_entry(form)
entry.pack(fill="x", ipady=6, pady=(0, 14))

# --- Live Issue Date ---
make_field_label(form, "Live Issue Date (DD-Mon-YYYY)").pack(fill="x", pady=(0, 4))
issue_date_var = tk.StringVar(value=datetime.now().strftime("%d-%b-%Y"))
issue_date_entry = make_entry(form, textvariable=issue_date_var)
issue_date_entry.pack(fill="x", ipady=6)
tk.Label(
    form,
    text="Defaults to today. Change for back-dated issues.",
    font=(FONT, 8),
    bg=COLOR_CARD,
    fg=COLOR_MUTED,
    anchor="w",
).pack(fill="x", pady=(3, 14))

# --- Parent Directory ---
make_field_label(form, "Parent Directory (Base Root)").pack(fill="x", pady=(0, 4))
parent_row = tk.Frame(form, bg=COLOR_CARD)
parent_row.pack(fill="x", pady=(0, 14))
parent_dir_var = tk.StringVar(value=last_dir if last_dir else "")
parent_dir_entry = make_entry(parent_row, textvariable=parent_dir_var)
parent_dir_entry.pack(side="left", fill="x", expand=True, ipady=6)
browse_button = make_button(parent_row, "Browse", choose_directory, COLOR_ACCENT, pady=5)
browse_button.pack(side="left", padx=(8, 0))

# --- Compiled Directory ---
make_field_label(form, "Compiled Scripts Directory").pack(fill="x", pady=(0, 4))
compiled_row = tk.Frame(form, bg=COLOR_CARD)
compiled_row.pack(fill="x", pady=(0, 14))
compiled_dir_var = tk.StringVar(value=last_compiled_dir if last_compiled_dir else "")
compiled_dir_entry = make_entry(compiled_row, textvariable=compiled_dir_var)
compiled_dir_entry.pack(side="left", fill="x", expand=True, ipady=6)
compiled_browse_button = make_button(compiled_row, "Browse", choose_compiled_directory, COLOR_ACCENT, pady=5)
compiled_browse_button.pack(side="left", padx=(8, 0))

# --- Backup checkbox ---
create_backup_var = tk.BooleanVar()
backup_checkbox = tk.Checkbutton(
    form,
    text="Also create Backup folder and test.sql file",
    variable=create_backup_var,
    font=(FONT, 10),
    bg=COLOR_CARD,
    fg=COLOR_TEXT,
    activebackground=COLOR_CARD,
    selectcolor="#ffffff",
    anchor="w",
)
backup_checkbox.pack(fill="x", pady=(0, 16))

# --- Primary action ---
create_button = make_button(
    form, "Check & Create Folder", check_and_create, "#16a34a",
    font_size=12, bold=True, pady=13
)
create_button.pack(fill="x", pady=(0, 10))

# --- Secondary actions (2x2 grid, equal widths) ---
actions = tk.Frame(form, bg=COLOR_CARD)
actions.pack(fill="x")
actions.columnconfigure(0, weight=1, uniform="actions")
actions.columnconfigure(1, weight=1, uniform="actions")

reset_button = make_button(actions, "Reset", reset_to_create_mode, "#ea580c")
reset_button.grid(row=0, column=0, sticky="ew", padx=(0, 5), pady=(0, 10))

open_button = make_button(actions, "Open File", open_file, "#7c3aed")
open_button.grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=(0, 10))

open_folder_button = make_button(actions, "Open Issue Folder", open_issue_folder, "#0d9488")
open_folder_button.grid(row=1, column=0, sticky="ew", padx=(0, 5))

clean_dupes_button = make_button(actions, "Clean Duplicates", show_duplicates_report_and_clean, "#dc2626")
clean_dupes_button.grid(row=1, column=1, sticky="ew", padx=(5, 0))

# --- Footer tip pinned to the bottom ---
hint_label = tk.Label(
    form,
    text="Tip: \"Open Issue Folder\" reopens the last issue you created or loaded.",
    font=(FONT, 9),
    bg="#f1f5f9",
    fg=COLOR_MUTED,
    pady=8,
)
hint_label.pack(side="bottom", fill="x")

root.mainloop()