import sys
import re
import uuid
from pathlib import Path
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtPdf import QPdfDocument

from PySide6.QtCore import Qt, QSize, QMarginsF
from PySide6.QtGui import (
    QAction,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextImageFormat,
    QTextListFormat,
    QFont,
    QPageSize,
    QPageLayout
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QFileDialog,
    QMessageBox,
    QToolBar,
    QSpinBox,
    QLabel,
    QDialog,
    QDialogButtonBox,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
)
from PySide6.QtPrintSupport import QPrinter


APP_TITLE = "CRF PDF Generator"
CRF_PATTERN = re.compile(r"\bECRF-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b", re.IGNORECASE)
CRF_ROOT = Path(r"D:\Documents\CRFs")

class TableDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Insert Table")
        self.resize(650, 450)

        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Rows:"))

        self.rows = QSpinBox()
        self.rows.setRange(1, 50)
        self.rows.setValue(3)
        controls.addWidget(self.rows)

        controls.addWidget(QLabel("Columns:"))

        self.columns = QSpinBox()
        self.columns.setRange(1, 20)
        self.columns.setValue(3)
        controls.addWidget(self.columns)

        self.create_button = QPushButton("Create Grid")
        self.create_button.clicked.connect(self.create_grid)
        controls.addWidget(self.create_button)
        controls.addStretch()

        layout.addLayout(controls)

        self.table = QTableWidget(3, 3)
        self.table.setHorizontalHeaderLabels(["Column 1", "Column 2", "Column 3"])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def create_grid(self):
        rows = self.rows.value()
        columns = self.columns.value()

        self.table.setRowCount(rows)
        self.table.setColumnCount(columns)

        for c in range(columns):
            self.table.setHorizontalHeaderItem(
                c, QTableWidgetItem(f"Column {c + 1}")
            )

        for r in range(rows):
            for c in range(columns):
                if self.table.item(r, c) is None:
                    self.table.setItem(r, c, QTableWidgetItem(""))

        self.table.resizeColumnsToContents()

    def get_html(self):
        rows = self.table.rowCount()
        columns = self.table.columnCount()

        html = [
            '<table border="1" cellspacing="0" cellpadding="5" '
            'style="border-collapse:collapse; width:100%;">'
        ]

        headers = []
        for c in range(columns):
            item = self.table.horizontalHeaderItem(c)
            headers.append(item.text() if item else f"Column {c + 1}")

        html.append("<tr>")
        for header in headers:
            html.append(
                '<td style="font-weight:bold; background:#eeeeee;">'
                + self.escape_html(header)
                + "</td>"
            )
        html.append("</tr>")

        for r in range(rows):
            html.append("<tr>")
            for c in range(columns):
                item = self.table.item(r, c)
                value = item.text() if item else ""
                html.append("<td>" + self.escape_html(value) + "</td>")
            html.append("</tr>")

        html.append("</table><p></p>")
        return "".join(html)

    @staticmethod
    def escape_html(value):
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )


class RichTextEditor(QTextEdit):
    """Rich document editor with images and tables."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setAcceptDrops(True)
        self.setUndoRedoEnabled(True)
        self.setPlaceholderText(
            "Paste your CRF content here...\n\n"
            "Ctrl+V can preserve rich text and images from supported applications."
        )

    def insertFromMimeData(self, source):
        if source.hasHtml():
            raw_html = source.html()

            try:
                debug_dir = Path.home() / "Documents" / "PasteDebug"
                debug_dir.mkdir(parents=True, exist_ok=True)
                (debug_dir / "raw_paste.html").write_text(raw_html, encoding="utf-8")
            except Exception:
                pass

            cleaned = self.sanitize_pasted_html(raw_html)

            try:
                (debug_dir / "cleaned_paste.html").write_text(cleaned, encoding="utf-8")
            except Exception:
                pass

            self.insertHtml(cleaned)
        elif source.hasText():
            self.insertPlainText(source.text())
        else:
            super().insertFromMimeData(source)

    @staticmethod
    def sanitize_pasted_html(html):
        html = re.sub(r"<!--\[if[^>]*>.*?<!\[endif\]-->", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
        html = re.sub(r"<xml>.*?</xml>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<v:[^>]*>.*?</v:[^>]*>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"</?v:[^>]*/?>", "", html, flags=re.IGNORECASE)
        html = re.sub(r"</?o:[^>]*/?>", "", html, flags=re.IGNORECASE)
        html = re.sub(r'<div class="qF8_5".*?</div>', "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<button\b.*?</button>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"\s*!important", "", html, flags=re.IGNORECASE)

        def clean_style(match):
            style_content = match.group(1)
            declarations = []
            for d in style_content.split(";"):
                d = d.strip()
                if not d:
                    continue
                if re.match(r"^(width|height)\s*:", d, re.IGNORECASE):
                    continue
                # Drop ALL border-related declarations here — Qt mishandles
                # Outlook's per-side longhand/shorthand border CSS. We add
                # our own simple, explicit borders further below instead.
                if re.match(r"^border(-[a-z]+)*\s*:", d, re.IGNORECASE) and \
                   not d.lower().startswith("border-collapse"):
                    continue
                declarations.append(d)
            if not declarations:
                return ""
            return f'style="{"; ".join(declarations)}"'

        html = re.sub(r'style="([^"]*)"', clean_style, html, flags=re.IGNORECASE)

        html = re.sub(r'\swidth="\d+(\.\d+)?(px)?"', "", html, flags=re.IGNORECASE)
        html = re.sub(r'\sheight="\d+(\.\d+)?(px)?"', "", html, flags=re.IGNORECASE)
        html = re.sub(r'\sclass="Mso[^"]*"', "", html, flags=re.IGNORECASE)
        html = re.sub(r'\sstyle=""', "", html)
        html = re.sub(r'\sclass=""', "", html)

        # Force simple, explicit borders on every table/cell instead of
        # relying on Outlook's border CSS, which Qt renders inconsistently.
        def add_table_attrs(m):
            tag = re.sub(r'\sborder="[^"]*"', "", m.group(0), flags=re.IGNORECASE)
            return tag[:-1] + ' border="1" cellspacing="0" cellpadding="4">'

        html = re.sub(r"<table\b[^>]*>", add_table_attrs, html, flags=re.IGNORECASE)

        def add_cell_border(m):
            tag = m.group(0)
            if 'style="' in tag:
                return re.sub(r'style="', 'style="border:1px solid black; ', tag, count=1)
            return tag[:-1] + ' style="border:1px solid black;">'

        html = re.sub(r"<t[dh]\b[^>]*>", add_cell_border, html, flags=re.IGNORECASE)

        return html

    def insert_image_file(self, filename):
        if not filename:
            return

        # Use Qt document resources so the image remains part of the rich document.
        from PySide6.QtGui import QImageReader

        reader = QImageReader(filename)
        image = reader.read()

        if image.isNull():
            raise ValueError("The selected file is not a readable image.")

        document = self.document()
        resource_name = f"image://local/{Path(filename).name}_{id(image)}"
        document.addResource(QTextDocument.ResourceType.ImageResource, resource_name, image)

        cursor = self.textCursor()
        fmt = QTextImageFormat()
        fmt.setName(resource_name)

        # Keep very large screenshots manageable inside the editor.
        max_width = max(300, self.viewport().width() - 50)
        if image.width() > max_width:
            ratio = max_width / image.width()
            fmt.setWidth(max_width)
            fmt.setHeight(image.height() * ratio)
        else:
            fmt.setWidth(image.width())
            fmt.setHeight(image.height())

        cursor.insertImage(fmt)
        self.setTextCursor(cursor)

    def insert_qimage(self, image, name):
        resource_name = f"image://pdf/{name}_{uuid.uuid4().hex}"
        self.document().addResource(QTextDocument.ResourceType.ImageResource, resource_name, image)

        fmt = QTextImageFormat()
        fmt.setName(resource_name)

        max_width = max(300, self.viewport().width() - 50)
        ratio = min(1.0, max_width / image.width())
        fmt.setWidth(image.width() * ratio)
        fmt.setHeight(image.height() * ratio)

        cursor = self.textCursor()
        cursor.insertImage(fmt)
        cursor.insertBlock()
        self.setTextCursor(cursor)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(APP_TITLE)
        self.resize(1000, 750)

        self.last_output_dir = str(Path.home() / "Documents")
        self._output_manually_edited = False
        self._last_generated_path = None

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # Document information
        form = QFormLayout()

        self.crf_number = QLineEdit()
        self.crf_number.setPlaceholderText("Auto-detected from content, or enter manually")
        form.addRow("CRF Number:", self.crf_number)

        self.title = QLineEdit()
        self.title.setPlaceholderText("Optional document title")
        form.addRow("Document Title:", self.title)

        main_layout.addLayout(form)

        # Toolbar
        self.toolbar = QToolBar("Formatting")
        self.toolbar.setIconSize(QSize(18, 18))
        self.addToolBar(self.toolbar)

        self.setup_toolbar()

        # Editor
        self.editor = RichTextEditor()
        main_layout.addWidget(self.editor)

        # Output
        output_layout = QHBoxLayout()

        output_layout.addWidget(QLabel("Output PDF:"))

        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText("PDF filename will be based on the CRF number")
        output_layout.addWidget(self.output_path)

        browse = QPushButton("Browse")
        browse.clicked.connect(self.browse_output)
        output_layout.addWidget(browse)

        main_layout.addLayout(output_layout)

        # Bottom buttons
        buttons = QHBoxLayout()

        load_button = QPushButton("Load PDF")
        load_button.setMinimumHeight(38)
        load_button.clicked.connect(self.load_pdf)
        buttons.addWidget(load_button)

        buttons.addStretch()

        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.clear_document)
        buttons.addWidget(clear_button)

        generate_button = QPushButton("Generate PDF")
        generate_button.setMinimumHeight(38)
        generate_button.clicked.connect(self.generate_pdf)
        buttons.addWidget(generate_button)

        self.open_button = QPushButton("Open PDF")
        self.open_button.setMinimumHeight(38)
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_generated_pdf)
        buttons.addWidget(self.open_button)

        main_layout.addLayout(buttons)

        # Automatic CRF detection
        self.editor.textChanged.connect(self.auto_detect_crf)
        self.output_path.textEdited.connect(self.mark_output_edited)

        self.statusBar().showMessage("Ready")

    def setup_toolbar(self):
        bold = QAction("B", self)
        bold.setCheckable(True)
        bold.triggered.connect(self.toggle_bold)
        self.toolbar.addAction(bold)

        italic = QAction("I", self)
        italic.setCheckable(True)
        italic.triggered.connect(self.toggle_italic)
        self.toolbar.addAction(italic)

        underline = QAction("U", self)
        underline.setCheckable(True)
        underline.triggered.connect(self.toggle_underline)
        self.toolbar.addAction(underline)

        self.toolbar.addSeparator()

        normal = QAction("Normal", self)
        normal.triggered.connect(lambda: self.set_font_size(11))
        self.toolbar.addAction(normal)

        h1 = QAction("H1", self)
        h1.triggered.connect(lambda: self.set_heading(20))
        self.toolbar.addAction(h1)

        h2 = QAction("H2", self)
        h2.triggered.connect(lambda: self.set_heading(16))
        self.toolbar.addAction(h2)

        self.toolbar.addSeparator()

        bullet = QAction("• Bullets", self)
        bullet.triggered.connect(self.toggle_bullets)
        self.toolbar.addAction(bullet)

        numbered = QAction("1. Numbering", self)
        numbered.triggered.connect(self.toggle_numbering)
        self.toolbar.addAction(numbered)

        self.toolbar.addSeparator()

        image_action = QAction("Insert Image", self)
        image_action.triggered.connect(self.insert_image)
        self.toolbar.addAction(image_action)

        table_action = QAction("Insert Table", self)
        table_action.triggered.connect(self.insert_table)
        self.toolbar.addAction(table_action)

        self.toolbar.addSeparator()

        left = QAction("Left", self)
        left.triggered.connect(lambda: self.editor.setAlignment(Qt.AlignmentFlag.AlignLeft))
        self.toolbar.addAction(left)

        center = QAction("Center", self)
        center.triggered.connect(lambda: self.editor.setAlignment(Qt.AlignmentFlag.AlignCenter))
        self.toolbar.addAction(center)

        right = QAction("Right", self)
        right.triggered.connect(lambda: self.editor.setAlignment(Qt.AlignmentFlag.AlignRight))
        self.toolbar.addAction(right)

    def merge_format(self, fmt):
        cursor = self.editor.textCursor()

        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)

        cursor.mergeCharFormat(fmt)
        self.editor.setTextCursor(cursor)

    def toggle_bold(self):
        fmt = QTextCharFormat()
        current = self.editor.currentCharFormat().fontWeight()
        fmt.setFontWeight(
            QFont.Weight.Normal
            if current == QFont.Weight.Bold
            else QFont.Weight.Bold
        )
        self.merge_format(fmt)

    def toggle_italic(self):
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self.editor.currentCharFormat().fontItalic())
        self.merge_format(fmt)

    def toggle_underline(self):
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self.editor.currentCharFormat().fontUnderline())
        self.merge_format(fmt)

    def set_font_size(self, size):
        fmt = QTextCharFormat()
        fmt.setFontPointSize(size)
        self.merge_format(fmt)

    def set_heading(self, size):
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.SelectionType.BlockUnderCursor)

        fmt = QTextCharFormat()
        fmt.setFontPointSize(size)
        fmt.setFontWeight(QFont.Weight.Bold)
        cursor.mergeCharFormat(fmt)

        self.editor.setTextCursor(cursor)

    def toggle_bullets(self):
        cursor = self.editor.textCursor()
        cursor.createList(QTextListFormat.Style.ListDisc)

    def toggle_numbering(self):
        cursor = self.editor.textCursor()
        cursor.createList(QTextListFormat.Style.ListDecimal)

    def insert_image(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select Image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)",
        )

        if not filename:
            return

        try:
            self.editor.insert_image_file(filename)
        except Exception as exc:
            QMessageBox.critical(self, "Image Error", str(exc))

    def insert_table(self):
        dialog = TableDialog(self)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            cursor = self.editor.textCursor()
            cursor.insertHtml(dialog.get_html())
            self.editor.setTextCursor(cursor)

    def auto_detect_crf(self):
        content = self.editor.toPlainText()
        match = CRF_PATTERN.search(content)

        if match:
            detected = match.group(0).upper()

            # Only replace when the user has not manually changed the field.
            if not self.crf_number.hasFocus() or not self.crf_number.text().strip():
                self.crf_number.setText(detected)
                self.auto_fill_output_path()

    def mark_output_edited(self):
        self._output_manually_edited = True

    def auto_fill_output_path(self):
        if self._output_manually_edited:
            return

        crf = self.crf_number.text().strip()
        if not crf:
            return

        crf_folder = CRF_ROOT / crf
        default_name = self.safe_filename(crf) + ".pdf"
        self.output_path.setText(str(crf_folder / default_name))

    def open_generated_pdf(self):
        if self._last_generated_path and self._last_generated_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_generated_path)))
        else:
            QMessageBox.warning(
                self,
                "File Not Found",
                "The generated PDF could not be found. It may have been moved or deleted.",
            )
            self.open_button.setEnabled(False)

    def load_pdf(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select PDF",
            self.last_output_dir,
            "PDF Files (*.pdf)",
        )

        if not filename:
            return

        pdf = QPdfDocument(self)
        pdf.load(filename)

        if pdf.status() != QPdfDocument.Status.Ready or pdf.pageCount() == 0:
            QMessageBox.critical(
                self,
                "PDF Error",
                "Could not open the PDF. It may be corrupt or password-protected.",
            )
            return

        stem = Path(filename).stem
        image_pages = 0

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for page in range(pdf.pageCount()):
                # Prefer the text layer so the content is selectable/copyable.
                text = pdf.getAllText(page).text().strip()
                if text:
                    self.editor.insertPlainText(text + "\n\n")
                else:
                    # No text layer (scanned page): fall back to an image.
                    image_pages += 1
                    size = pdf.pagePointSize(page)
                    image = pdf.render(
                        page, QSize(int(size.width() * 2), int(size.height() * 2))
                    )
                    self.editor.insert_qimage(image, f"{stem}_p{page + 1}")
        finally:
            QApplication.restoreOverrideCursor()
            pdf.close()

        # Fallback: take the CRF number from the filename if content had none.
        match = CRF_PATTERN.search(stem)
        if match and not self.crf_number.text().strip():
            self.crf_number.setText(match.group(0).upper())
            self.auto_fill_output_path()

        self.last_output_dir = str(Path(filename).parent)

        message = f"Loaded: {filename}"
        if image_pages:
            message += f" ({image_pages} page(s) had no text and were inserted as images)"
        self.statusBar().showMessage(message)

    def browse_output(self):
        suggested = self.crf_number.text().strip() or "CRF_Document"
        suggested = self.safe_filename(suggested) + ".pdf"

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save PDF",
            str(Path(self.last_output_dir) / suggested),
            "PDF Files (*.pdf)",
        )

        if filename:
            self.output_path.setText(filename)
            self.last_output_dir = str(Path(filename).parent)

    def clear_document(self):
        answer = QMessageBox.question(
            self,
            "Clear Document",
            "Are you sure you want to clear the current document?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if answer == QMessageBox.StandardButton.Yes:
            self.editor.clear()
            self.crf_number.clear()
            self.title.clear()
            self.output_path.clear()
            self._output_manually_edited = False
            self._last_generated_path = None
            self.open_button.setEnabled(False)

    @staticmethod
    def fit_images_to_page(doc, max_width=670):
        for block in _iter_blocks(doc):
           for it in block:
                frag = it.fragment()
                fmt = frag.charFormat()
                if not frag.isValid() or not fmt.isImageFormat():
                    continue
                img_fmt = fmt.toImageFormat()
                w, h = img_fmt.width(), img_fmt.height()

                if w <= 0 or h <= 0:
                    res = doc.resource(
                        QTextDocument.ResourceType.ImageResource,
                        QUrl(img_fmt.name()),
                    )
                    if res is None or res.isNull():
                        continue
                    w, h = res.width(), res.height()

                if w > max_width:
                    img_fmt.setWidth(max_width)
                    img_fmt.setHeight(h * max_width / w)
                    cur = QTextCursor(doc)
                    cur.setPosition(frag.position())
                    cur.setPosition(frag.position() + frag.length(),
                                    QTextCursor.MoveMode.KeepAnchor)
                    cur.setCharFormat(img_fmt)

    @staticmethod
    def safe_filename(name):
        name = re.sub(r'[<>:"/\\|?*]', "_", name)
        return name.strip().rstrip(".") or "CRF_Document"

    @staticmethod
    def next_available_path(path):
        counter = 2
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")

        while candidate.exists():
            counter += 1
            candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")

        return candidate

    def generate_pdf(self):
        if not self.editor.toPlainText().strip() and not self.editor.document().blockCount():
            QMessageBox.warning(
                self,
                "No Content",
                "Please paste or enter some content before generating the PDF.",
            )
            return

        crf = self.crf_number.text().strip()

        if not crf:
            match = CRF_PATTERN.search(self.editor.toPlainText())
            if match:
                crf = match.group(0).upper()
                self.crf_number.setText(crf)

        if not crf:
            QMessageBox.warning(
                self,
                "CRF Number Required",
                "CRF Number was not detected. Please enter it manually.",
            )
            self.crf_number.setFocus()
            return

        # In case the field is still empty (e.g. user typed CRF number but
        # tabbed away before the live auto-fill ran), try once more here.
        if not self.output_path.text().strip():
            self.auto_fill_output_path()

        output = self.output_path.text().strip()

        if not output:
            QMessageBox.warning(
                self,
                "Output Path Required",
                "Please enter or confirm an output path before generating the PDF.",
            )
            self.output_path.setFocus()
            return

        if not output.lower().endswith(".pdf"):
            output += ".pdf"

        output_path = Path(output)

        if output_path.exists():
            answer = QMessageBox.question(
                self,
                "File Already Exists",
                f"A file already exists at:\n\n{output_path}\n\n"
                "Do you want to generate a new copy? The existing file will be kept as-is.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )

            if answer != QMessageBox.StandardButton.Yes:
                self.statusBar().showMessage("Generation cancelled — existing file kept.")
                return

            output_path = self.next_available_path(output_path)
            self.output_path.setText(str(output_path))

        if not output_path.parent.exists():
            QMessageBox.critical(
                self,
                "Folder Not Found",
                f"The output folder does not exist:\n\n{output_path.parent}\n\n"
                "Please create it first, then generate the PDF again.",
            )
            return

        try:
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(str(output_path))

            page_layout = QPageLayout(
                QPageSize(QPageSize.PageSizeId.A4),
                QPageLayout.Orientation.Portrait,
                QMarginsF(15, 15, 15, 15),
                QPageLayout.Unit.Millimeter,
            )
            printer.setPageLayout(page_layout)

            # Add title only if supplied, without disturbing the user's pasted
            # document content/order.
            if self.title.text().strip():
                doc = QTextDocument()
                title_html = (
                    '<div style="text-align:center; margin-bottom:15px;">'
                    '<span style="font-size:18pt; font-weight:bold;">'
                    + self.escape_html(self.title.text().strip())
                    + "</span></div>"
                )
                doc.setHtml(title_html + self.editor.document().toHtml())
            else:
                doc = self.editor.document().clone()

            # Keep the editor's rich document as the source so text, formatting,
            # images and tables remain in their original sequence.
            self.fit_images_to_page(doc)
            doc.print_(printer)

            self.output_path.setText(str(output_path))
            self.last_output_dir = str(output_path.parent)
            self._last_generated_path = output_path
            self.open_button.setEnabled(True)

            QMessageBox.information(
                self,
                "PDF Generated",
                f"PDF generated successfully.\n\n{output_path}",
            )
            self.statusBar().showMessage(f"Generated: {output_path}")

        except Exception as exc:
            QMessageBox.critical(
                self,
                "PDF Generation Error",
                f"Could not generate the PDF.\n\n{exc}",
            )

    @staticmethod
    def escape_html(value):
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

def _iter_blocks(doc):
    block = doc.begin()
    while block.isValid():
        yield block
        block = block.next()
        
def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()