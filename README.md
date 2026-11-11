# PythonUtilities

Small Windows desktop tools that speed up day-to-day development work: setting up CRF and Live Issue folders, generating and comparing stored-procedure scripts, preparing SIT deployments, and producing RTM and PDF documents.

Each tool lives in its own folder and is a single Python script with a Tkinter (or Qt) interface. Most of them use only the standard library.

## Tools

| Tool | What it does | Extra packages |
|---|---|---|
| [AISanitizer](AISanitizer) | **AI Safe Sanitizer.** Before code or logs are pasted into an AI tool, it masks company, server and project names, hosts, IPs, emails and URL paths with placeholders such as `[HOST_1]`. Afterwards it restores the original values in the AI's reply. It can also watch the clipboard and sanitize anything you copy. | none |
| [ASPX_Validator](ASPX_Validator) | Checks ASPX / ASCX markup (pasted in or loaded from a file) for errors and warnings such as unclosed tables, a missing `runat="server"`, unregistered custom controls and ID typos. Each result has a line number and a suggested fix, and clicking an issue jumps to that line. | none |
| [CreateCRFFolder](CreateCRFFolder) | **Folder Structure Creator.** Creates the folder structure for a new CRF: a main folder with development and SIT subfolders, each containing a `Backup` folder. | none |
| [CreateLiveIssueFolder](CreateLiveIssueFolder) | **Live Issue Folder Structure Creator.** Creates a dated folder for a live issue under year and month folders, optionally with a `Backup` folder and `test.sql`. It warns about duplicate issues and supports back-dated issues. | none |
| [CreateSPFiles](CreateSPFiles) | **Stored Procedure SQL File Generator.** Generates the stored-procedure `.sql` files for a CRF and compares each modified SP with its backup side by side. The compare has context lines, separate handling of comment-only changes, and F7/F8 navigation. It also generates SP URLs for QA And Production Deployments. | none |
| [EmptyFiles](EmptyFiles) | Empties every `.sql` file in a folder and all its subfolders (the files are kept but set to 0 bytes). **This cannot be undone.** | none |
| [FilesComparer](FilesComparer) | **PyCompare**, a Beyond Compare-style tool for comparing folders and files, locally or over FTP/FTPS. Both panes are editable and change blocks can be copied across. It has Dark, Sage Dark and Light themes. See [below](#filescomparer-pycompare). | none |
| [RTMSheetPopulator](RTMSheetPopulator) | **RTM Excel Generator.** Builds `RTM-<CRF>.xlsx` from JSON input, with formatted headers and dropdown lists. See [below](#rtmsheetpopulator-first-time-usage). | `openpyxl` |
| [RichPDFGenerator](RichPDFGenerator) | **CRF PDF Generator.** A rich-text editor with formatting, tables and images that produces a PDF named after the CRF number. It can also open existing PDFs. | `PySide6` |
| [SITDeployer](SITDeployer) | **SIT Compare Launcher.** Reads the Git status of a project to find the changed files, maps the local development path to the matching SIT folder, and opens a Beyond Compare folder compare against the chosen FTP profile. A manual mode opens a plain folder compare. | none (needs Git and Beyond Compare installed) |

## Requirements

- Windows 10/11
- Python 3.8 or newer
- Extra packages only for the tools that need them:

```bash
pip install openpyxl     # RTMSheetPopulator
pip install PySide6      # RichPDFGenerator
pip install pillow       # iconGenerator.py (icon creation)
pip install pyinstaller  # building .exe files
```

## Running a tool

```bash
cd <ToolFolder>
python <script>.py
```

Most tools remember their last-used folders in a settings file next to the script or `.exe` (`config.txt` or a `*_config.json`).

## Building an .exe

Run these from inside the tool's folder. The first build creates a `<script>.spec` file, and later builds can reuse it:

```bash
pyinstaller --clean <script>.spec
```

`.spec` files and the `build/` and `dist/` folders are in `.gitignore`. The finished `.exe` ends up in `dist\`.

| Tool | Command |
|---|---|
| AISanitizer | `pyinstaller --onefile --noconsole --clean --icon content_sanitizer.ico --add-data "content_sanitizer.ico;." --add-data "content_sanitizer.png;." AISanitizer.py` |
| ASPX_Validator | `pyinstaller --onefile --noconsole --clean aspxValidator.py` |
| CreateCRFFolder | `pyinstaller --onefile --noconsole --clean --icon CRFFolder.ico --add-data "CRFFolder.ico;." --add-data "CRFFolder.png;." createCRFFolder.py` |
| CreateLiveIssueFolder | `pyinstaller --onefile --noconsole --clean --icon LiveIssue.ico --add-data "LiveIssue.ico;." --add-data "LiveIssue.png;." createLiveIssueFolder.py` |
| CreateSPFiles | `pyinstaller --onefile --noconsole --clean --icon CreateSPFiles.ico --add-data "CreateSPFiles.png;." CreateSPFiles.py` |
| EmptyFiles | `pyinstaller --onefile --noconsole --clean emptyFiles.py` |
| FilesComparer | `pyinstaller --onefile --noconsole --clean --icon CompareFiles.ico --add-data "CompareFiles.ico;." --add-data "CompareFiles.png;." FilesComparer.py` |
| RTMSheetPopulator | `pyinstaller --onefile --noconsole --clean --icon RTM.ico --add-data "RTM.ico;." --add-data "RTM.png;." RTMSheetPopulator.py` |
| RichPDFGenerator | `pyinstaller --onefile --noconsole --clean --icon CRFPDFGenerator.ico RichPDFGenerator.py` |
| SITDeployer | `pyinstaller --onefile --noconsole --clean --icon SITCompare.ico --add-data "SITCompare.ico;." --add-data "SITCompare.png;." SITDeployer.py` |

- `--icon` sets the icon of the `.exe` file itself.
- `--add-data` bundles the icon files so the running window can show them as well.
- Leave out `--noconsole` while debugging, so errors are printed to a console window.

## Creating icons

Each tool folder that has an icon also has an `iconGenerator.py`, which turns a `.png` into a multi-size `.ico` (16, 32, 48 and 256 px):

1. Put the image in the tool's folder.
2. Update the image and icon file names in `iconGenerator.py`.
3. Run it:

   ```bash
   py iconGenerator.py
   ```

4. Rebuild the `.exe` so the new icon is picked up.

## FilesComparer (PyCompare)

```bash
python FilesComparer.py                          # home screen
python FilesComparer.py <left> <right>           # two folders or two files
python FilesComparer.py D:\work ftp://user@host/srv
```

- **Folder compare:** a recursive tree showing identical, different, left-only and right-only items. Folders can be compared by size and date, size only, or content, with ignore patterns. Items can be copied or deleted on either side.
- **File compare:** side-by-side and editable, with word-level highlights, All / Diffs / Context views, ignore whitespace or case, and a line-ending selector.
- **FTP / FTPS:** saved connection profiles, a remote folder browser, and upload or download straight from the folder view. Remote files can be edited and saved back. SFTP is not supported, because it would need the third-party `paramiko` package.

## RTMSheetPopulator (First Time Usage)

**RTM Excel Generator** builds `RTM-<CRF>.xlsx` from JSON input, with formatted headers and dropdown lists.

**First Time Command for ChatGPT:**

Copy the complete contents of your CRF document and paste them below this prompt in ChatGPT to generate the required JSON.

```text
Convert the following CRF document into JSON for my RTM Excel Generator.

Use the following JSON structure and requirements:
- Extract CRF number, developer, module, and all requirements.
- Each requirement must include type, description, priority, and test_cases.
- Generate relevant test cases for every requirement.
- Do not miss or invent any requirements.
- Return valid JSON only, without Markdown or explanations.

{
  "crf_no": "ECRF-XXXX-XXXXXXXX",
  "developer": "Muhammad Ahsan",
  "module": "Module Name",
  "requirements": [
    {
      "type": "Functional",
      "description": "Requirement description",
      "priority": "Medium",
      "test_cases": [
        "Test case 1",
        "Test case 2"
      ]
    }
  ]
}

CRF DOCUMENT:
[PASTE CRF CONTENT HERE]
```

**Generate RTM:**
1. Copy the JSON generated by ChatGPT.
2. Paste or load it into `RTMSheetPopulator.py`.
3. Generate the Excel file.

**Output:** `RTM-<CRF>.xlsx`

| Shortcut | Action |
|---|---|
| F7 / F8 | Previous / next difference |
| Alt+← / Alt+→ | Copy the current block left / right |
| Ctrl+F, F3 / Shift+F3 | Find in the active pane, next / previous match |
| Ctrl+S, Ctrl+Z | Save, undo |
| F5 | Re-compare / reload |
| Ctrl+D / Ctrl+N | New folder compare / new file compare |