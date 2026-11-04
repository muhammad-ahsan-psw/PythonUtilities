import tkinter as tk
from tkinter import messagebox, filedialog
import re

class ASPXValidator:
    def __init__(self, root):
        self.root = root
        self.result_text_lines = []
        self.setup_gui()
        
        # --- Validation configuration ---
        self.ISSUE_TYPES = {
            "ERROR": "error_line",
            "WARNING": "warning_line",
            "INFO": "info_line"
        }
        
        self.SUGGESTIONS = {
            "Possible typo in ID: '1b' instead of 'lb'": 
                "Change '1b' to 'lb' in control IDs for better readability (1 vs l).",
            "Missing runat='server' on custom control":
                "Add runat='server' attribute to custom controls: <FraiAJAXControls:Label runat=\"server\" ... />",
            "Custom controls detected (FraiAJAXControls)":
                "Ensure FraiAJAXControls are properly registered in web.config or with <%@ Register %> directive.",
            "Unclosed <table> tag detected":
                "Add closing </table> tag to properly close the table structure.",
            "Table structure may be incomplete (missing <tr> or <td>)":
                "Ensure tables have proper <tr> and <td> structure for valid HTML.",
            "Possible unclosed <td> tag":
                "Check if <td> tags are properly closed with </td> in the table structure.",
            "Missing <%@ Page %> or <%@ Control %> directive":
                "Add a Page directive for .aspx or a Control directive for .ascx, e.g.:\n"
                "<%@ Page Language=\"C#\" AutoEventWireup=\"true\" CodeFile=\"YourPage.aspx.cs\" Inherits=\"YourNamespace.YourPageClass\" %>",
            "HTML structure incomplete (<html> or </html> missing)":
                "Ensure your page has <html> and </html> tags enclosing the content.",
            "Missing <head> tag":
                "Add a <head> section, e.g. <head> ... </head> before <body>.",
            "Missing <body> tag":
                "Add a <body> section, e.g. <body> ... </body> after <head>.",
            "Missing <form runat='server'>":
                "Add a server form: <form runat=\"server\"> ... </form> wrapping your controls.",
            "Missing ScriptManager control (required for UpdatePanel)":
                "Add a ScriptManager for UpdatePanel support: <asp:ScriptManager runat=\"server\" ID=\"ScriptManager1\" />",
            "No ScriptManager found":
                "If you use AJAX/UpdatePanel, add a ScriptManager at top of page.",
            "No UpdatePanel found":
                "If using AJAX, wrap content in <asp:UpdatePanel runat=\"server\"> ... </asp:UpdatePanel>.",
            "No ASP.NET server controls (<asp:...> or custom controls) found":
                "Add server controls, e.g. <asp:Label runat=\"server\" ID=\"lblTest\" Text=\"Test\" />"
        }

    def setup_gui(self):
        """Initialize the GUI components"""
        self.root.title("ASPX/ASCX Validator with Line Numbers & Suggestions")
        self.root.geometry("1200x800")
        # two columns: main content and tiny buttons column
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=0)
        self.root.rowconfigure(1, weight=1)
        self.root.rowconfigure(4, weight=1)
        self.root.rowconfigure(6, weight=1)

        # Header label (left) and button frame (right)
        tk.Label(self.root, text="Paste your ASPX/ASCX code below:").grid(
            row=0, column=0, sticky="w", padx=5, pady=5
        )

        # Buttons in column 1
        button_frame = tk.Frame(self.root)
        button_frame.grid(row=0, column=1, sticky="e", padx=5, pady=5)
        tk.Button(button_frame, text="Load File", command=self.load_file, 
                 bg="lightgreen", font=("Arial", 10)).pack(side=tk.RIGHT, padx=5)
        tk.Button(button_frame, text="Clear", command=self.clear_validation, 
                 bg="lightyellow", font=("Arial", 10)).pack(side=tk.RIGHT, padx=5)
        tk.Button(button_frame, text="Validate", command=self.validate_aspx, 
                 bg="lightblue", font=("Arial", 12)).pack(side=tk.RIGHT, padx=5)

        # Frame for line numbers + input (spans column 0)
        frame_input = tk.Frame(self.root)
        frame_input.grid(row=1, column=0, columnspan=2, sticky="nsew")
        frame_input.columnconfigure(1, weight=1)
        frame_input.rowconfigure(0, weight=1)

        # Line numbers
        self.line_numbers = tk.Text(frame_input, width=6, padx=5, takefocus=0, 
                                   border=0, background="#f0f0f0", state='disabled')
        self.line_numbers.grid(row=0, column=0, sticky='ns')

        # Text input
        self.text_input = tk.Text(frame_input, width=120, height=25, wrap="none")
        self.text_input.grid(row=0, column=1, sticky="nsew")
        
        # Configure tags for different issue types & syntax highlighting
        self.text_input.tag_config("error_line", background="#ffcccc")
        self.text_input.tag_config("warning_line", background="#fff0cc")
        self.text_input.tag_config("info_line", background="#e6f3ff")
        self.text_input.tag_config("jump_highlight", background="yellow")
        self.text_input.tag_config("directive", foreground="purple")
        self.text_input.tag_config("tag", foreground="blue")
        self.text_input.tag_config("attribute", foreground="red")
        
        self.text_input.bind("<KeyRelease>", self.on_text_change)

        # Scrollbar (vertical)
        self.scroll = tk.Scrollbar(frame_input, orient="vertical", command=self.on_text_scroll)
        self.scroll.grid(row=0, column=2, sticky='ns')
        self.text_input.config(yscrollcommand=self.on_input_scroll)
        self.line_numbers.config(yscrollcommand=self.on_input_scroll)

        # Validation results (spans columns)
        tk.Label(self.root, text="Validation Result (click issue to jump to code):").grid(
            row=3, column=0, sticky="w", padx=5, pady=5
        )

        self.result_text = tk.Text(self.root, width=120, height=10, wrap="none")
        self.result_text.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=5, pady=5)

        # Suggestions
        tk.Label(self.root, text="Suggested Fix:").grid(
            row=5, column=0, sticky="w", padx=5, pady=5
        )

        self.suggestion_text = tk.Text(self.root, width=120, height=10, wrap="word")
        self.suggestion_text.grid(row=6, column=0, columnspan=2, sticky="nsew", padx=5, pady=5)

        # Initialize line numbers and result tags
        self.update_line_numbers()
        self.setup_syntax_highlighting()

    def find_lines_with_pattern(self, content, pattern):
        """Helper to find lines containing pattern (case-insensitive)"""
        return [i for i, line in enumerate(content, start=1) 
                if pattern in line.lower()]

    def _gather_tag_from(self, lines, start_index):
        """
        Starting at start_index (0-based index into lines), accumulate lines until we find a '>'
        (end of tag). Return the full tag text and the end line index (1-based).
        """
        full = ""
        end_line = start_index
        total = len(lines)
        while end_line < total:
            part = lines[end_line]
            full += part + ("\n" if end_line < total - 1 else "")
            if ">" in part:
                return full, end_line + 1  # return 1-based line number for end
            end_line += 1
        # reached EOF without '>'
        return full, None

    def detect_unclosed_tags(self, content):
        """
        Multi-line-aware detection of unclosed/mismatched tags.
        We parse tags by accumulating lines until '>' found, then process tag token.
        """
        issues = []
        stack = []  # (tag_name, line_no)
        # Known void elements (don't need closing)
        void_tags = {'br', 'hr', 'img', 'input', 'meta', 'link', 'area', 'base', 'col', 'embed', 'param', 'source', 'track', 'wbr'}
        total_lines = len(content)

        i = 0
        while i < total_lines:
            line = content[i]
            # search for '<' occurrences in line
            idx = 0
            while True:
                lt = line.find('<', idx)
                if lt == -1:
                    break
                # skip comments and directives here; they are handled elsewhere
                if line[lt:lt+4].lower().startswith('<!--'):
                    # skip comment until '-->' possibly over multiple lines
                    end_found = False
                    # check current and subsequent lines
                    j = i
                    comment_rem = line[lt+4:]
                    if '-->' in comment_rem:
                        idx = lt + 4
                        end_found = True
                    else:
                        k = i + 1
                        while k < total_lines:
                            if '-->' in content[k]:
                                end_found = True
                                break
                            k += 1
                    # advance idx past this '<' to continue scanning
                    idx = lt + 1
                    if not end_found:
                        # comment never closed (rare)
                        issues.append(("Unclosed HTML comment", [i+1], "WARNING"))
                    continue

                # Attempt to gather full tag from this point
                # But first ensure it's not a server directive <%@
                if line[lt:lt+2] == '<%':
                    # skip directives and other server-side tokens here
                    # find closing %> in this or following lines
                    # (we handle directives separately)
                    if '%>' in line[lt:]:
                        idx = lt + 2
                        continue
                    else:
                        k = i + 1
                        found = False
                        while k < total_lines:
                            if '%>' in content[k]:
                                found = True
                                break
                            k += 1
                        if not found:
                            issues.append(("Unclosed server directive <%...%>", [i+1], "ERROR"))
                        idx = lt + 2
                        continue

                # Now accumulate tag text across lines
                tag_text, tag_end_line = self._gather_tag_from(content, i)
                if tag_end_line is None:
                    # didn't find '>' until EOF
                    # derive tag name from what's present
                    m = re.match(r'<\s*/?\s*([a-z0-9:-]+)', tag_text, re.IGNORECASE)
                    tag_name = m.group(1).lower() if m else None
                    issues.append((f"Unclosed tag detected (no '>') starting with <{tag_name or '?'}>", [i+1], "ERROR"))
                    i = total_lines  # end parsing
                    break
                # We have full tag_text (may contain multiple tags if line had multiple '<', but we start at i)
                # Find the first tag in tag_text (starting at original lt position)
                # Build substring from lt to first '>'
                # But simpler: parse tags in current line only by scanning with regex on tag_text chunk
                # We'll parse tag occurrences in the combined chunk
                sub = tag_text
                for m in re.finditer(r'<\s*(/?)\s*([a-z0-9:-]+)([^>]*)>', sub, re.IGNORECASE):
                    is_closing = bool(m.group(1))
                    tag_name = m.group(2).lower()
                    rest = m.group(3) or ""
                    is_self_closing = rest.strip().endswith('/') or tag_name in void_tags
                    # Ignore server-side constructs like <% ... %> or <%= ... %>
                    if tag_name.startswith('%') or tag_name.startswith('?'):
                        continue
                    # push/pop stack as appropriate
                    if is_closing:
                        if stack and stack[-1][0] == tag_name:
                            stack.pop()
                        elif any(t == tag_name for t, _ in stack):
                            # mismatched nesting
                            issues.append((f"Unclosed/mismatched HTML tag detected: <{tag_name}>", [i+1], "WARNING"))
                            # remove first occurrence of tag_name in stack (from top)
                            for idxs in range(len(stack)-1, -1, -1):
                                if stack[idxs][0] == tag_name:
                                    stack.pop(idxs)
                                    break
                        else:
                            issues.append((f"Closing tag without opening: </{tag_name}>", [i+1], "WARNING"))
                    else:
                        if not is_self_closing:
                            # opening tag; record line where it started (approximate to i+1)
                            stack.append((tag_name, i+1))
                # move i to the tag_end_line - 1 (because loop increments i)
                i = tag_end_line - 1
                # stop scanning further '<' on this original line because we've consumed to tag_end_line
                break
            i += 1

        # remaining open tags are unclosed
        for tag_name, ln in stack:
            issues.append((f"Possible unclosed HTML tag: <{tag_name}>", [ln], "WARNING"))
        return issues

    def validate_directive(self, content):
        """Validate Page/Control directives (detect multi-line directive spans)."""
        issues = []
        full_text = "\n".join(content)
        directive_found = False
        for match in re.finditer(r'<%@\s*(page|control)\b', full_text, re.IGNORECASE):
            directive_found = True
            # find the closing %> after match
            end_match = re.search(r'%>', full_text[match.end():], re.IGNORECASE)
            start_char = match.start()
            if end_match:
                end_char = match.end() + end_match.end()
                # map to lines
                start_line = full_text[:start_char].count("\n") + 1
                end_line = full_text[:end_char].count("\n") + 1
                # highlight full directive span (from start_line start to end_line end)
                try:
                    self.text_input.tag_add("directive", f"{start_line}.0", f"{end_line}.end")
                except Exception:
                    # fallback: mark start line
                    self.text_input.tag_add("directive", f"{start_line}.0", f"{start_line}.end")
            else:
                # no %> found
                start_line = full_text[:start_char].count("\n") + 1
                issues.append(("Unclosed directive <%@ ... %>", [start_line], "ERROR"))
        if not directive_found:
            issues.append(("Missing <%@ Page %> or <%@ Control %> directive", [1], "ERROR"))
        return issues

    def validate_html_structure(self, content):
        """Validate basic HTML structure with improved detection."""
        issues = []
        
        # Table checks
        table_lines = self.find_lines_with_pattern(content, "<table")
        table_end_lines = self.find_lines_with_pattern(content, "</table>")
        if table_lines and not table_end_lines:
            issues.append(("Unclosed <table> tag detected", table_lines, "ERROR"))
        elif table_end_lines and not table_lines:
            issues.append(("Closing </table> without opening tag", table_end_lines, "ERROR"))
        else:
            # if both present, highlight them
            for ln in table_lines + table_end_lines:
                self.text_input.tag_add("tag", f"{ln}.0", f"{ln}.end")
        
        # tr/td structure warning
        tr_lines = self.find_lines_with_pattern(content, "<tr")
        td_lines = self.find_lines_with_pattern(content, "<td")
        if table_lines and (not tr_lines or not td_lines):
            issues.append(("Table structure may be incomplete (missing <tr> or <td>)", table_lines, "WARNING"))

        # HTML tag presence
        html_lines = self.find_lines_with_pattern(content, "<html")
        html_end_lines = self.find_lines_with_pattern(content, "</html>")
        if not html_lines and not html_end_lines:
            issues.append(("HTML structure incomplete (<html> or </html> missing)", [1], "WARNING"))
        elif not html_lines:
            issues.append(("Missing <html> start tag", html_end_lines or [1], "ERROR"))
        elif not html_end_lines:
            issues.append(("Missing </html> end tag", html_lines or [len(content)], "ERROR"))
        else:
            for ln in html_lines + html_end_lines:
                self.text_input.tag_add("info_line", f"{ln}.0", f"{ln}.end")

        # head and body presence (info)
        head_lines = self.find_lines_with_pattern(content, "<head")
        if not head_lines:
            issues.append(("Missing <head> tag", [], "INFO"))
        else:
            for ln in head_lines:
                self.text_input.tag_add("info_line", f"{ln}.0", f"{ln}.end")

        body_lines = self.find_lines_with_pattern(content, "<body")
        if not body_lines:
            issues.append(("Missing <body> tag", [], "INFO"))
        else:
            for ln in body_lines:
                self.text_input.tag_add("info_line", f"{ln}.0", f"{ln}.end")

        return issues
    
    def detect_specific_issues(self, content):
        """Detect specific issues in the provided code"""
        issues = []
        for i, line in enumerate(content, start=1):
            line_lower = line.lower()
            # Possible unclosed <td>
            if "<td" in line_lower and "</td>" not in line_lower:
                next_lines = content[i:i+3]  # check next 3 lines
                td_closed = any("</td>" in ln.lower() for ln in next_lines)
                if not td_closed:
                    issues.append(("Possible unclosed <td> tag", [i], "WARNING"))
            # ID typo check: id="1b" or id='1b' or id= 1b (loose)
            if re.search(r'id\s*=\s*["\']?1b["\']?', line, re.IGNORECASE):
                issues.append(("Possible typo in ID: '1b' instead of 'lb'", [i], "WARNING"))
            # Custom control without runat=server
            if "fraiajaxcontrols:" in line_lower and 'runat=' not in line_lower:
                # But ensure this isn't a multi-line attribute list; we check the full tag span
                # Gather full tag to verify if runat appears later before '>'
                # We'll attempt to gather the tag from this line
                tag_text, end_line = self._gather_tag_from(content, i-1)
                if end_line is None or 'runat=' not in tag_text.lower():
                    issues.append(("Missing runat='server' on custom control", [i], "ERROR"))
        return issues

    def validate_aspnet_specific(self, content):
        """Validate ASP.NET specific elements"""
        issues = []
        # Server form
        form_lines = self.find_lines_with_pattern(content, 'form runat="server"')
        if not form_lines:
            issues.append(("Missing <form runat='server'>", [], "WARNING"))
        else:
            for ln in form_lines:
                self.text_input.tag_add("info_line", f"{ln}.0", f"{ln}.end")

        # ScriptManager and UpdatePanel
        sm_lines = self.find_lines_with_pattern(content, "<asp:scriptmanager")
        up_lines = self.find_lines_with_pattern(content, "<asp:updatepanel")
        if up_lines and not sm_lines:
            issues.append(("Missing ScriptManager control (required for UpdatePanel)", up_lines, "ERROR"))
        elif not sm_lines:
            issues.append(("No ScriptManager found", [], "INFO"))
        else:
            for ln in sm_lines:
                self.text_input.tag_add("info_line", f"{ln}.0", f"{ln}.end")

        if not up_lines:
            issues.append(("No UpdatePanel found", [], "INFO"))
        else:
            for ln in up_lines:
                self.text_input.tag_add("info_line", f"{ln}.0", f"{ln}.end")

        # ASP.NET server controls and custom controls
        asp_lines = self.find_lines_with_pattern(content, "<asp:")
        custom_control_lines = self.find_lines_with_pattern(content, "pralajaxcontrols:") + self.find_lines_with_pattern(content, "fraiajaxcontrols:")
        if not asp_lines and not custom_control_lines:
            issues.append(("No ASP.NET server controls (<asp:...> or custom controls) found", [], "INFO"))
        else:
            for ln in asp_lines + custom_control_lines:
                self.text_input.tag_add("info_line", f"{ln}.0", f"{ln}.end")
            if custom_control_lines:
                issues.append(("Custom controls detected (FraiAJAXControls/PralAJAXControls)", custom_control_lines, "INFO"))
        return issues

    def validate_aspx(self):
        """Main validation function"""
        content = self.text_input.get("1.0", tk.END).splitlines()
        if not content or (len(content) == 1 and not content[0].strip()):
            messagebox.showwarning("Validation", "Please paste your ASPX/ASCX code.")
            return

        # Clear previous highlights and results
        self.clear_highlights()
        self.result_text.delete("1.0", tk.END)
        self.suggestion_text.delete("1.0", tk.END)
        self.result_text_lines.clear()

        # Apply basic syntax highlighting
        self.apply_syntax_highlighting()

        # Run all validations
        all_issues = []
        all_issues.extend(self.validate_directive(content))
        all_issues.extend(self.validate_html_structure(content))
        all_issues.extend(self.validate_aspnet_specific(content))
        all_issues.extend(self.detect_unclosed_tags(content))
        all_issues.extend(self.detect_specific_issues(content))

        # Display results with color coding and clickable tags
        error_count = warning_count = info_count = 0

        # Ensure result tags exist
        self.result_text.tag_configure("error", foreground="red", font=("Arial", 10, "bold"))
        self.result_text.tag_configure("warning", foreground="orange", font=("Arial", 10, "bold"))
        self.result_text.tag_configure("info", foreground="blue", font=("Arial", 10))
        self.result_text.tag_configure("summary", foreground="green", font=("Arial", 10, "bold"))

        for idx, (msg, lines, severity) in enumerate(all_issues, start=1):
            if severity == "ERROR":
                color_tag = "error"
                error_count += 1
            elif severity == "WARNING":
                color_tag = "warning"
                warning_count += 1
            else:
                color_tag = "info"
                info_count += 1

            # Insert index + severity + message, and tag the message range
            self.result_text.insert(tk.END, f"{idx}. ", "")
            self.result_text.insert(tk.END, f"[{severity}] ", color_tag)
            start_index = self.result_text.index(tk.INSERT)
            self.result_text.insert(tk.END, f"{msg}\n")
            end_index = self.result_text.index(tk.INSERT)

            tagname = f"issue_{idx-1}"   # zero-based index for mapping
            self.result_text.tag_add(tagname, start_index, end_index)
            # bind tag click to jump_to_issue with captured index
            self.result_text.tag_bind(tagname, "<Button-1>", lambda e, issue_idx=idx-1: self.jump_to_issue(issue_idx))

            # record issues for navigation
            self.result_text_lines.append((msg, lines, severity))

        # Add summary
        if all_issues:
            self.result_text.insert(tk.END, f"\n--- Summary: {error_count} errors, {warning_count} warnings, {info_count} info items ---", "summary")
        else:
            self.result_text.insert(tk.END, "No issues found! Your code looks good.", "summary")
            self.result_text_lines = []

    def clear_validation(self):
        """Clear all validation results and highlights"""
        self.clear_highlights()
        self.result_text.delete("1.0", tk.END)
        self.suggestion_text.delete("1.0", tk.END)
        self.text_input.delete("1.0", tk.END)
        self.text_input.clear()
        self.result_text_lines.clear()
        self.apply_syntax_highlighting()  # Reapply just syntax highlighting

    def clear_highlights(self):
        """Clear all text highlights"""
        for tag_name in ["error_line", "warning_line", "info_line", "jump_highlight", "directive", "tag", "attribute"]:
            self.text_input.tag_remove(tag_name, "1.0", tk.END)

    def jump_to_issue(self, issue_idx):
        """Jump to the line for the given issue index (called by tag binding)."""
        try:
            if 0 <= issue_idx < len(self.result_text_lines):
                msg, lines, severity = self.result_text_lines[issue_idx]
                if lines:
                    target_line = lines[0]
                    total_lines = max(1, int(self.text_input.index('end-1c').split('.')[0]))
                    fraction = max(0.0, min(1.0, (target_line - 5) / total_lines))
                    self.text_input.yview_moveto(fraction)
                    self.text_input.tag_remove("jump_highlight", "1.0", tk.END)
                    self.text_input.tag_add("jump_highlight", f"{target_line}.0", f"{target_line}.end")
                    self.text_input.focus()
                # show suggestion
                self.suggestion_text.delete("1.0", tk.END)
                suggestion = self.SUGGESTIONS.get(msg, "No specific suggestion available. Review the code for potential issues.")
                self.suggestion_text.insert(tk.END, suggestion)
        except Exception as e:
            messagebox.showerror("Navigation Error", f"Could not jump to the selected error: {e}")

    def update_line_numbers(self):
        """Update the line numbers on the left panel"""
        self.line_numbers.config(state='normal')
        self.line_numbers.delete('1.0', tk.END)
        count = int(self.text_input.index('end-1c').split('.')[0])
        lines = "\n".join(str(i) for i in range(1, count + 1))
        self.line_numbers.insert('1.0', lines)
        self.line_numbers.config(state='disabled')

    def on_text_scroll(self, *args):
        """Called by scrollbar; sync both text widgets"""
        self.text_input.yview(*args)
        self.line_numbers.yview(*args)

    def on_input_scroll(self, *args):
        """Called by text widgets' yscrollcommand; sync scrollbar and the line numbers"""
        try:
            frac = float(args[0])
        except Exception:
            frac = 0.0
        self.scroll.set(*args)
        self.text_input.yview_moveto(frac)
        self.line_numbers.yview_moveto(frac)

    def on_text_change(self, event=None):
        """Handle text changes with auto-update of line numbers and optional auto-validate"""
        self.update_line_numbers()
        
        # Auto-validate after a short delay (only for small files)
        if hasattr(self, '_auto_validate_job'):
            self.root.after_cancel(self._auto_validate_job)
        
        content = self.text_input.get("1.0", "end-1c")
        if len(content.splitlines()) < 200:  # safe threshold
            self._auto_validate_job = self.root.after(1200, self.validate_aspx)

    def setup_syntax_highlighting(self):
        """Configure result box tags"""
        self.result_text.tag_configure("error", foreground="red", font=("Arial", 10, "bold"))
        self.result_text.tag_configure("warning", foreground="orange", font=("Arial", 10, "bold"))
        self.result_text.tag_configure("info", foreground="blue", font=("Arial", 10))
        self.result_text.tag_configure("summary", foreground="green", font=("Arial", 10, "bold"))

    def apply_syntax_highlighting(self):
        """Apply very basic syntax highlighting for directives and tags"""
        # Clear existing syntax tags
        for tag in ["directive", "tag", "attribute"]:
            self.text_input.tag_remove(tag, "1.0", tk.END)
        
        content = self.text_input.get("1.0", tk.END)
        
        # Highlight directives (single-line or multi-line)
        for match in re.finditer(r'(<%@.*?%>)', content, re.IGNORECASE | re.DOTALL):
            start = f"1.0+{match.start()}c"
            end = f"1.0+{match.end()}c"
            try:
                self.text_input.tag_add("directive", start, end)
            except Exception:
                pass
        
        # Highlight tag names (simplified)
        for match in re.finditer(r'</?\s*([a-z0-9:-]+)', content, re.IGNORECASE):
            start = f"1.0+{match.start()}c"
            end = f"1.0+{match.end()}c"
            try:
                self.text_input.tag_add("tag", start, end)
            except Exception:
                pass

    def load_file(self):
        """Load ASPX/ASCX file from disk"""
        file_path = filedialog.askopenfilename(
            filetypes=[
                ("ASP.NET files", "*.aspx *.ascx *.master"),
                ("ASPX files", "*.aspx"),
                ("ASCX files", "*.ascx"),
                ("Master pages", "*.master"),
                ("All files", "*.*")
            ]
        )
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.text_input.delete("1.0", tk.END)
                self.text_input.insert("1.0", content)
                self.update_line_numbers()
                self.clear_validation()  # Clear previous results and reapply syntax
                # Auto-validate loaded file
                self.root.after(500, self.validate_aspx)
            except Exception as e:
                messagebox.showerror("File Error", f"Could not load file: {str(e)}")

def main():
    root = tk.Tk()
    app = ASPXValidator(root)
    root.mainloop()

if __name__ == "__main__":
    main()
