import json
import os
import sys
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation
import re
import tkinter as tk
from tkinter import messagebox
from tkinter.scrolledtext import ScrolledText
import shutil
from tkinter import filedialog, messagebox

if getattr(sys, "frozen", False):
    # Running as a PyInstaller exe - use the folder the .exe lives in,
    # not the temp extraction folder (_MEI...) that __file__ would point to.
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TEMP_DIR = os.path.join(BASE_DIR, "Temp")
INPUT_FILE = os.path.join(TEMP_DIR, "input.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.txt")

# OUTPUT_FILE = os.path.join(BASE_DIR, "output.xlsx")
OUTPUT_FILE = None
CRF_ROOT = r"D:\Documents\CRFs"

HEADERS = [
    "Serial No.",
    "Source\nReference\n(ECRF/CRF)",
    "Developer\nName",
    "Module\nName",
    "Unique\nRequirement\nID",
    "Requirement\nType",
    "Requirement\nDescription",
    "Priority",
    "Test\nCase",
    "Status",
    "QA\nStatus",
    "Remarks",
]

COL_WIDTHS = {
    "A": 10.4, "B": 28.5, "C": 17.3, "D": 40.4, "E": 25, "F": 22.1,
    "G": 65.9, "H": 12, "I": 79.9, "J": 12.3, "K": 15.6, "L": 11.9,
}

# Same orange used by the original CRF template (Office theme accent6)
HEADER_FILL = PatternFill("solid", fgColor="F79646")
HEADER_FONT = Font(name="Calibri", size=12, bold=True, color="000000")
DATA_FONT = Font(name="Calibri", size=11)
DATA_FONT_BOLD = Font(name="Calibri", size=11, bold=True)

THIN = Side(style="thin")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

CENTER_WRAP = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT_WRAP = Alignment(horizontal="left", vertical="center", wrap_text=True)

# Bold columns in the data rows (matches the original CRF's look)
BOLD_COLS = {5, 6, 7, 8, 10, 11}
# Left-aligned / wrapped text columns (long free text)
LEFT_COLS = {7, 9, 12}

LISTS_HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="000000")

# (column_letter, header, [values...]) - matches the original "Lists" sheet
LISTS_COLUMNS = [
    ("B", "Priority", ["High", "Medium", "Low"]),
    ("D", "Status", ["Not Started", "In-Progress", "Completed", "Deployed"]),
    ("F", "Task Type", ["Change Request", "Bug Fix", "Enhancement", "Support"]),
    ("H", "QA Status", ["Pending", "Passed/Covered", "Failed"]),
    ("J", "UAT Status", ["Pending", "Approved", "Rejected"]),
    ("L", "Status", ["Pending", "In Progress", "Completed", "Delayed", "On Hold", "UAT", "Deployed"]),
    ("N", "Requirement Type", ["Functional", "Non-Functional", "Integration", "Security"]),
    ("P", "QA Status", ["Not Started", "In-Progress", "Completed"]),
]

# Maps Master RTM column letter -> Lists sheet column letter + number of values,
# reproducing the exact ranges the original workbook's dropdowns pointed to.
DROPDOWN_LINKS = {
    "F": ("N", 4),  # Requirement Type
    "H": ("B", 3),  # Priority
    "J": ("D", 4),  # Status
    "K": ("H", 3),  # QA Status
}

SHEET1_COLUMN_EXPLANATION = [
    "Sr# \u2013 Serial number",
    "Requirement ID \u2013 Unique ID (REQ-001, CRF-015, BUG-020)",
    "Module Name \u2013 Finance, GD, User Support, Reports, etc.",
    "Requirement Description \u2013 Short task detail",
    "Priority \u2013 High / Medium / Low",
    "Requested By \u2013 Business user / manager / team lead",
    "Developer Name \u2013 Assigned developer",
    "Start Date \u2013 Task start date",
    "Target Date \u2013 Planned completion",
    "Actual Completion Date \u2013 Real completion date",
    "Status \u2013 Pending / In Progress / Completed / Hold / QA / UAT",
    "% Complete \u2013 0% to 100%",
    "Task Type \u2013 New Change / Bug Fix / Enhancement / Support",
    "Dependency \u2013 DBA / API / User Input / Approval etc.",
    "Test Case ID \u2013 Linked testing reference",
    "QA Status \u2013 Pending / Passed / Failed",
    "UAT Status \u2013 Pending / Approved / Rejected",
    "Remarks \u2013 Any notes or blockers",
]

SHEET1_COLOR_CODING = [
    "Green = Completed",
    "Yellow = In Progress",
    "Red = Delayed",
    "Gray = On Hold",
]

SHEET1_APPLY_FILTERS = ["Developer Name", "Status", "Priority", "Module"]
last_output_file = None
selected_crf_folder = None
allow_any_rtm = None          # will hold the BooleanVar

def resource_path(relative_path):
    if getattr(sys, "frozen", False):
        base_path = sys._MEIPASS
    else:
        base_path = BASE_DIR

    return os.path.join(base_path, relative_path)

def build_lists_sheet(wb):
    ws = wb.create_sheet("Lists")
    for letter, header, values in LISTS_COLUMNS:
        cell = ws[f"{letter}1"]
        cell.value = header
        cell.font = LISTS_HEADER_FONT
        cell.fill = HEADER_FILL
        for i, val in enumerate(values, start=2):
            ws[f"{letter}{i}"] = val
        ws.column_dimensions[letter].width = 17.66 if letter == "N" else 13
    return ws


def build_sheet1(wb):
    ws = wb.create_sheet("Sheet1")
    headers = [("A1", "Column Explanation"), ("C1", "Color Coding"), ("E1", "Apply filters")]
    for coord, text in headers:
        cell = ws[coord]
        cell.value = text
        cell.font = LISTS_HEADER_FONT
        cell.fill = HEADER_FILL

    for i, text in enumerate(SHEET1_COLUMN_EXPLANATION, start=2):
        ws[f"A{i}"] = text
    for i, text in enumerate(SHEET1_COLOR_CODING, start=2):
        ws[f"C{i}"] = text
    for i, text in enumerate(SHEET1_APPLY_FILTERS, start=2):
        ws[f"E{i}"] = text

    ws.column_dimensions["A"].width = 60.33
    ws.column_dimensions["B"].width = 22.44
    ws.column_dimensions["C"].width = 23.33
    ws.column_dimensions["D"].width = 13
    ws.column_dimensions["E"].width = 25.44
    return ws


def style_header(ws):
    for col, text in enumerate(HEADERS, start=1):
        cell = ws.cell(row=1, column=col, value=text)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER_WRAP
        cell.border = BORDER
    for letter, width in COL_WIDTHS.items():
        ws.column_dimensions[letter].width = width
    ws.row_dimensions[1].height = 44.25


def add_dropdown(ws, master_col_letter, lists_col_letter, value_count, last_row):
    dv = DataValidation(
        type="list",
        formula1=f"Lists!${lists_col_letter}$2:${lists_col_letter}${value_count + 1}",
        allow_blank=True,
    )
    ws.add_data_validation(dv)
    dv.add(f"{master_col_letter}2:{master_col_letter}{last_row}")

def load_json_text():
    if os.path.exists(INPUT_FILE):
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return "{}"

def get_next_backup_number():
    pattern = re.compile(r"input(\d+)\.json$", re.IGNORECASE)

    numbers = []

    for filename in os.listdir(TEMP_DIR):
        m = pattern.match(filename)
        if m:
            numbers.append(int(m.group(1)))

    return max(numbers, default=0) + 1


def save_json_if_changed(old_text, new_text):
    os.makedirs(TEMP_DIR, exist_ok=True)

    if os.path.exists(INPUT_FILE) and old_text.strip() != new_text.strip():
        backup_file = os.path.join(
            TEMP_DIR,
            f"input{get_next_backup_number()}.json"
        )
        with open(backup_file, "w", encoding="utf-8") as f:
            f.write(old_text)

    with open(INPUT_FILE, "w", encoding="utf-8") as f:
        f.write(new_text)

def generate_rtm_excel(json_text=None):
    if json_text is None:
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.loads(json_text)

    crf_no = data.get("crf_no", "").strip()

    if not crf_no:
        raise ValueError("crf_no is missing.")

    requirements = data.get("requirements") or []
    if not requirements:
        raise ValueError("requirements list is empty.")

    output_dir = os.path.join(BASE_DIR, "RTM Sheets")
    os.makedirs(output_dir, exist_ok=True)

    output_file = os.path.join(
        output_dir,
        f"RTM-{crf_no}.xlsx"
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Master RTM 1.0"
    style_header(ws)

    start_row = 2
    row_idx = start_row

    for i, req in enumerate(requirements, start=1):
        req_id = f"Requirement -{i}"
        priority = req.get("priority") or "Medium"
        status = req.get("status") or "Deployed"
        qa_status = req.get("qa_status") or "Pending"
        remarks = req.get("remarks", "")

        # All test cases for one requirement live in a single bulleted cell,
        # not on separate rows - this is what the source CRF does.
        test_case_text = "\n".join(f"\u2022 {tc}" for tc in req.get("test_cases", []))

        row_values = [
            row_idx - 1,            # Serial No.
            data["crf_no"],
            data["developer"],
            data["module"],
            req_id,
            req["type"],
            req["description"],
            priority,
            test_case_text,
            status,
            qa_status,
            remarks,
        ]

        for col, value in enumerate(row_values, start=1):
            cell = ws.cell(row=row_idx, column=col, value=value)
            cell.border = BORDER
            cell.font = DATA_FONT_BOLD if col in BOLD_COLS else DATA_FONT
            cell.alignment = LEFT_WRAP if col in LEFT_COLS else CENTER_WRAP

        row_idx += 1

    end_row = row_idx - 1

    # Serial No. / Source Reference / Developer / Module apply to the whole
    # CRF, so they're merged down the full block of requirement rows -
    # not per-requirement and not per-test-case.
    for col in (1, 2, 3, 4):
        ws.merge_cells(start_row=start_row, start_column=col, end_row=end_row, end_column=col)
        ws.cell(row=start_row, column=col).alignment = CENTER_WRAP

    add_dropdown(ws, "F", *DROPDOWN_LINKS["F"], last_row=end_row)
    add_dropdown(ws, "H", *DROPDOWN_LINKS["H"], last_row=end_row)
    add_dropdown(ws, "J", *DROPDOWN_LINKS["J"], last_row=end_row)
    add_dropdown(ws, "K", *DROPDOWN_LINKS["K"], last_row=end_row)

    build_lists_sheet(wb)
    build_sheet1(wb)

    # Ask before overwriting an existing Excel file
    if os.path.exists(output_file):
        if not messagebox.askyesno(
            "Overwrite",
            f"{os.path.basename(output_file)} already exists.\n\nOverwrite it?"
        ):
            return None

    wb.save(output_file)
    print(f"Excel generated: {output_file}")
    return output_file

def _crf_matches(name, crf):
    # "ECRF-6572" matches "ECRF-6572-27082026" but not "ECRF-65721"
    name, crf = name.upper(), crf.upper()
    return name == crf or name.startswith(crf + "-")

def short_crf(crf_no):
    """'ECRF-6572-27082026' -> 'ECRF-6572' (falls back to the full value)."""
    m = re.match(r"^([A-Za-z]*CRF-\d+)", (crf_no or "").strip(), re.IGNORECASE)
    return m.group(1) if m else (crf_no or "").strip()

def load_last_file():
    """Return LAST_FILE from config.txt if it still exists on disk."""
    if not os.path.exists(CONFIG_FILE):
        return None
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("LAST_FILE="):
                value = line.replace("LAST_FILE=", "", 1).strip()
                return value if os.path.isfile(value) else None
    return None


def save_last_file(path):
    """Write LAST_FILE to config.txt, leaving any other lines untouched."""
    lines = []
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            lines = [l.rstrip("\n") for l in f if not l.startswith("LAST_FILE=")]
    lines.append(f"LAST_FILE={path}")
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def remember_output(path):
    """Track the last created/moved Excel file for this session and for the next launch."""
    global last_output_file
    last_output_file = path
    try:
        save_last_file(path)
    except OSError:
        pass  # config is a convenience; never block the tool

def browse_crf_folder():
    """Let the user manually choose a CRF folder."""
    global selected_crf_folder
    folder = filedialog.askdirectory(
        title="Select CRF Folder",
        initialdir=CRF_ROOT if os.path.isdir(CRF_ROOT) else BASE_DIR
    )
    if folder:
        selected_crf_folder = folder
        # Put the folder name into the textbox for clarity
        ent_move.delete(0, tk.END)
        ent_move.insert(0, os.path.basename(folder))


def move_rtm_clicked():
    global selected_crf_folder

    crf = ent_move.get().strip()
    force_any = allow_any_rtm.get() if allow_any_rtm else False

    if not crf and not selected_crf_folder:
        messagebox.showwarning("CRF Required", "Enter a CRF number or browse a folder.")
        return

    rtm_dir = os.path.join(BASE_DIR, "RTM Sheets")
    if not os.path.isdir(rtm_dir):
        messagebox.showerror("Not Found", f"RTM Sheets folder does not exist:\n\n{rtm_dir}")
        return

    # Collect candidate RTM files
    all_rtms = [
        f for f in os.listdir(rtm_dir)
        if f.upper().startswith("RTM-") and f.lower().endswith(".xlsx")
    ]

    if not all_rtms:
        messagebox.showerror("Not Found", f"No RTM sheets found in:\n\n{rtm_dir}")
        return

    if force_any:
        # No filename matching – pick the most recently modified file
        files = sorted(
            all_rtms,
            key=lambda f: os.path.getmtime(os.path.join(rtm_dir, f)),
            reverse=True
        )
        chosen = files[0]
        if len(files) > 1:
            # Inform the user which one is being used
            if not messagebox.askyesno(
                "Multiple RTM Sheets",
                f"Multiple RTM sheets found.\n\n"
                f"Using the most recent one:\n{chosen}\n\n"
                f"Continue?"
            ):
                return
        files = [chosen]
    else:
        # Strict matching (original behaviour)
        files = [
            f for f in all_rtms
            if _crf_matches(f[4:-5], crf)
        ]
        if not files:
            messagebox.showerror("Not Found", f"No RTM sheet found for {crf} in:\n\n{rtm_dir}")
            return
        if len(files) > 1:
            messagebox.showerror(
                "Multiple Matches",
                "Enter the full CRF number including the date.\n\n" + "\n".join(files),
            )
            return

    # Destination folder
    if selected_crf_folder and os.path.isdir(selected_crf_folder):
        dest_folder = selected_crf_folder
    else:
        if force_any:
            messagebox.showwarning(
                "Folder Required",
                "When 'Allow any RTM' is checked, please Browse and select the destination folder."
            )
            return

        folders = []
        if os.path.isdir(CRF_ROOT):
            folders = [
                d for d in os.listdir(CRF_ROOT)
                if os.path.isdir(os.path.join(CRF_ROOT, d)) and _crf_matches(d, crf)
            ]

        if not folders:
            messagebox.showerror("Folder Not Found", f"No folder found for {crf} in:\n\n{CRF_ROOT}")
            return
        if len(folders) > 1:
            messagebox.showerror(
                "Multiple Folders",
                "Enter the full CRF number including the date.\n\n" + "\n".join(folders),
            )
            return
        dest_folder = os.path.join(CRF_ROOT, folders[0])

    src = os.path.join(rtm_dir, files[0])
    dest = os.path.join(dest_folder, files[0])

    try:
        if os.path.exists(dest):
            if not messagebox.askyesno(
                "Overwrite",
                f"{files[0]} already exists in:\n\n{os.path.dirname(dest)}\n\nOverwrite it?",
            ):
                return
            os.remove(dest)
        shutil.move(src, dest)
    except PermissionError:
        messagebox.showerror("File In Use", "Close the Excel file and try again.")
        return
    except Exception as ex:
        messagebox.showerror("Move Failed", str(ex))
        return

    remember_output(dest)
    #selected_crf_folder = None
    #allow_any_rtm.set(False)
    messagebox.showinfo("Moved", f"RTM sheet moved to:\n\n{dest}")

def generate_clicked():
    global selected_crf_folder
    text = txt_json.get("1.0", tk.END)

    try:
        parsed = json.loads(text)
        formatted = json.dumps(parsed, indent=4, ensure_ascii=False)
    except Exception as ex:
        messagebox.showerror(
            "Invalid JSON",
            str(ex)
        )
        return

    old_text = load_json_text()          # read BEFORE overwriting INPUT_FILE
    save_json_if_changed(old_text, formatted)

    try:
        output_file = generate_rtm_excel(formatted)
    except Exception as ex:
        messagebox.showerror(
            "RTM Generation Failed",
            str(ex)
        )
        return

    if output_file:
        remember_output(output_file)
        selected_crf_folder = None
        ent_move.delete(0, tk.END)
        ent_move.insert(0, short_crf(parsed.get("crf_no", "")))
        messagebox.showinfo(
            "Success",
            f"RTM generated successfully.\n\n{output_file}"
        )

def open_excel_clicked():
    path = last_output_file

    # Same session: open directly. After a restart: ask, using the path saved in config.txt.
    if not path or not os.path.isfile(path):
        path = None
        saved = load_last_file()
        if saved and messagebox.askyesno(
            "Open Last Excel File",
            f"Open the last created Excel file?\n\n{saved}"
        ):
            path = saved

        # Declined, or nothing saved: let the user pick a file
        if not path:
            rtm_dir = os.path.join(BASE_DIR, "RTM Sheets")
            path = filedialog.askopenfilename(
                title="Select RTM Excel File",
                initialdir=rtm_dir if os.path.isdir(rtm_dir) else BASE_DIR,
                filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
            )
            if not path:
                return

    try:
        os.startfile(path)
    except Exception as ex:
        messagebox.showerror("Open Failed", str(ex))      

# ============================================================
#               THEME
# ============================================================
FONT = "Segoe UI"
COLOR_BG = "#eef1f5"
COLOR_CARD = "#ffffff"
COLOR_HEADER = "#1f2d3d"
COLOR_TEXT = "#1f2d3d"
COLOR_BORDER = "#cbd5e1"
COLOR_ACCENT = "#2563eb"
COLOR_GREEN = "#16a34a"
COLOR_TEAL = "#0d9488"
COLOR_PURPLE = "#7c3aed"


def _darken(hex_color, factor=0.88):
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return "#{:02x}{:02x}{:02x}".format(int(r * factor), int(g * factor), int(b * factor))


def make_button(parent, text, command, bg, font_size=10, bold=False, pady=9, padx=14):
    """Flat button with a hover effect."""
    hover = _darken(bg)
    btn = tk.Button(
        parent, text=text, command=command,
        font=(FONT, font_size, "bold" if bold else "normal"),
        bg=bg, fg="white", activebackground=hover, activeforeground="white",
        relief="flat", bd=0, padx=padx, pady=pady, cursor="hand2",
    )
    btn.bind("<Enter>", lambda e: btn.config(bg=hover))
    btn.bind("<Leave>", lambda e: btn.config(bg=bg))
    return btn


def make_entry(parent, **kwargs):
    return tk.Entry(
        parent, font=(FONT, 10), bg="#ffffff", fg=COLOR_TEXT,
        relief="flat", bd=0, highlightthickness=1,
        highlightbackground=COLOR_BORDER, highlightcolor=COLOR_ACCENT,
        insertbackground=COLOR_TEXT, **kwargs
    )


def make_field_label(parent, text):
    return tk.Label(parent, text=text, font=(FONT, 10, "bold"),
                    bg=COLOR_CARD, fg=COLOR_TEXT, anchor="w")


def make_card(parent, title):
    card = tk.Frame(parent, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
    tk.Label(card, text=title, font=(FONT, 11, "bold"), bg=COLOR_HEADER,
             fg="white", anchor="w", padx=16, pady=11).pack(fill="x")
    return card

if __name__ == "__main__":

    root = tk.Tk()
    root.configure(bg=COLOR_BG)

    # Prefer ICO
    try:
        root.iconbitmap(resource_path("RTM.ico"))
    except Exception:
        # Fallback to PNG
        try:
            icon_img = tk.PhotoImage(file=resource_path("RTM.png"))
            root.iconphoto(True, icon_img)
        except Exception:
            pass

    root.title("RTM Excel Generator")
    root.update_idletasks()

    width = 1200
    height = 700

    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)

    root.geometry(f"{width}x{height}+{x}+{y}")
    root.minsize(900, 560)

    card = make_card(root, "RTM Excel Generator")
    card.pack(fill="both", expand=True, padx=14, pady=14)

    body = tk.Frame(card, bg=COLOR_CARD)
    body.pack(fill="both", expand=True, padx=18, pady=14)

    # ---------- Action bar ----------
    actions = tk.Frame(body, bg=COLOR_CARD)
    actions.pack(side="bottom", fill="x", pady=(14, 0))

    # Left side – primary actions
    left_actions = tk.Frame(actions, bg=COLOR_CARD)
    left_actions.pack(side="left")

    make_button(
        left_actions, "Generate RTM", generate_clicked, COLOR_GREEN,
        font_size=10, bold=True, pady=9, padx=18
    ).pack(side="left")

    make_button(
        left_actions, "Open Excel", open_excel_clicked, COLOR_PURPLE,
        font_size=10, bold=True, pady=9, padx=16
    ).pack(side="left", padx=(8, 0))

    # Right side – Move section
    move_frame = tk.Frame(actions, bg=COLOR_CARD)
    move_frame.pack(side="right")

    # Checkbox row
    chk_frame = tk.Frame(move_frame, bg=COLOR_CARD)
    chk_frame.pack(side="top", anchor="e", pady=(0, 4))

    allow_any_rtm = tk.BooleanVar(value=False)
    chk = tk.Checkbutton(
        chk_frame,
        text="Allow any RTM into selected folder",
        variable=allow_any_rtm,
        font=(FONT, 9),
        bg=COLOR_CARD,
        fg=COLOR_TEXT,
        activebackground=COLOR_CARD,
        selectcolor=COLOR_CARD,
        cursor="hand2",
    )
    chk.pack(side="right")

    # Controls row
    ctrl_frame = tk.Frame(move_frame, bg=COLOR_CARD)
    ctrl_frame.pack(side="top")

    make_field_label(ctrl_frame, "Move RTM for CRF").pack(side="left", padx=(0, 8))

    ent_move = make_entry(ctrl_frame, width=16)
    ent_move.pack(side="left", ipady=5, padx=(0, 6))
    ent_move.bind("<Return>", lambda e: move_rtm_clicked())

    make_button(
        ctrl_frame, "Browse", browse_crf_folder, COLOR_ACCENT,
        font_size=10, bold=True, pady=8, padx=12
    ).pack(side="left", padx=(0, 6))

    make_button(
        ctrl_frame, "Move to CRF Folder", move_rtm_clicked, COLOR_ACCENT,
        font_size=10, bold=True, pady=8, padx=12
    ).pack(side="left")

    # ---------- JSON input ----------
    make_field_label(body, "JSON Input").pack(fill="x", pady=(0, 4))

    txt_json = ScrolledText(
        body,
        font=("Consolas", 10),
        wrap=tk.NONE,
        undo=True,
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
    )
    txt_json.pack(fill="both", expand=True)
    txt_json.insert("1.0", load_json_text())

    root.mainloop()