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

if getattr(sys, "frozen", False):
    # Running as a PyInstaller exe - use the folder the .exe lives in,
    # not the temp extraction folder (_MEI...) that __file__ would point to.
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

INPUT_FILE = os.path.join(BASE_DIR, "input.json")
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

    for filename in os.listdir(BASE_DIR):
        m = pattern.match(filename)
        if m:
            numbers.append(int(m.group(1)))

    return max(numbers, default=0) + 1


def save_json_if_changed(old_text, new_text):
    if os.path.exists(INPUT_FILE) and old_text.strip() != new_text.strip():
        backup_file = os.path.join(
            BASE_DIR,
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


def move_rtm_clicked():
    crf = ent_move.get().strip()
    if not crf:
        messagebox.showwarning("CRF Required", "Enter a CRF number, e.g. ECRF-6572")
        return

    rtm_dir = os.path.join(BASE_DIR, "RTM Sheets")
    files = []
    if os.path.isdir(rtm_dir):
        files = [
            f for f in os.listdir(rtm_dir)
            if f.upper().startswith("RTM-")
            and f.lower().endswith(".xlsx")
            and _crf_matches(f[4:-5], crf)
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

    src = os.path.join(rtm_dir, files[0])
    dest = os.path.join(CRF_ROOT, folders[0], files[0])

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

    messagebox.showinfo("Moved", f"RTM sheet moved to:\n\n{dest}")

def generate_clicked():

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
        messagebox.showinfo(
            "Success",
            f"RTM generated successfully.\n\n{output_file}"
        )


if __name__ == "__main__":

    root = tk.Tk()

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

    lbl = tk.Label(
        root,
        text="JSON Input",
        font=("Calibri", 12, "bold")
    )

    lbl.pack(anchor="w", padx=10, pady=(10, 5))

    txt_json = ScrolledText(
        root,
        font=("Consolas", 10),
        wrap=tk.NONE,
        undo=True
    )

    txt_json.pack(
        fill="both",
        expand=True,
        padx=10,
        pady=5
    )

    txt_json.insert(
        "1.0",
        load_json_text()
    )

    btn = tk.Button(
        root,
        text="Generate RTM",
        bg="#28a745",
        fg="white",
        activebackground="#218838",
        activeforeground="white",
        font=("Calibri", 12, "bold"),
        padx=20,
        pady=8,
        command=generate_clicked
    )

    btn.pack(pady=10)

    move_frame = tk.Frame(root)
    move_frame.place(relx=1.0, rely=1.0, anchor="se", x=-10, y=-10)

    tk.Label(move_frame, text="Move RTM for CRF:", font=("Calibri", 11)).pack(side="left", padx=(0, 5))

    ent_move = tk.Entry(move_frame, width=22, font=("Calibri", 11))
    ent_move.pack(side="left", padx=(0, 5))
    ent_move.bind("<Return>", lambda e: move_rtm_clicked())

    tk.Button(
        move_frame,
        text="Move to CRF Folder",
        bg="#007bff",
        fg="white",
        activebackground="#0069d9",
        activeforeground="white",
        font=("Calibri", 11, "bold"),
        padx=12,
        pady=3,
        command=move_rtm_clicked,
    ).pack(side="left")

    root.mainloop()