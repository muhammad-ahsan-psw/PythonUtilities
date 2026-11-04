import json
import os
import re
import sys
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

APP_TITLE = "AI Safe Sanitizer"

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "ai_sanitizer_config.json")
CLIP_POLL_MS = 600

# --------------------------------------------------------------------------
# Detection rules
# --------------------------------------------------------------------------
PLACEHOLDER_RE = re.compile(r"\[([A-Z]+_\d+)\]")
FULL_PH_RE = re.compile(r"\[[A-Z]+_\d+\]")
SKIP_VALUES = {"true", "false", "null", "none", "undefined"}
SAFE_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
AUTH_SCHEMES = {"bearer", "basic", "digest", "token", "negotiate", "apikey", "ntlm"}

SECRET_KEYS = (
    r"(?:password|passwd|pwd|passphrase|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|credential|signature|auth[_-]?key)"
)
TLDS = (
    "com|net|org|io|co|gov|edu|mil|info|biz|dev|app|cloud|local|internal|corp|lan|"
    "intranet|int|pk|uk|us|in|ae|sa|de|fr|cn|ru|xyz|online|site|tech|me|ai"
)

PEM_RE = re.compile(r"-----BEGIN ([A-Z ]*PRIVATE KEY)-----.*?-----END \1-----", re.S)
HEADER_RE = re.compile(
    r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key|"
    r"x-auth-token|x-csrf-token|x-xsrf-token|x-amz-security-token|x-access-token|"
    r"ocp-apim-subscription-key)(\s*[\"']?\s*:\s*[\"']?)([^\r\n\"']+)"
)
HOSTHDR_RE = re.compile(
    r"(?i)\b(host|hostname|x-forwarded-host)(\s*[\"']?\s*:\s*[\"']?)([A-Za-z0-9._-]+)(:\d+)?"
)
CURL_U_RE = re.compile(r"(?i)(\s(?:-u|--user)\s+[\"']?)([^\s\"']+)")
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
CONN_RE = re.compile(
    r"(?i)\b(server|data source|address|addr|network address|user id|uid|user name|"
    r"username|initial catalog|database)(\s*=\s*)([^;\"'\r\n]+)"
)
KV_RE = re.compile(
    r"(?i)([\"']?[\w.-]*" + SECRET_KEYS + r"[\w.-]*[\"']?\s*[:=]\s*)"
    r"(\[[A-Z]+_\d+\]|\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&}\]\"']+)"
)
XML_RE = re.compile(
    r"(?is)(<([\w:.-]*" + SECRET_KEYS + r"[\w:.-]*)(?:\s[^>]*)?>)([^<]+)(</\2\s*>)"
)
URL_RE = re.compile(
    r"(?i)\b((?:https?|ftps?|wss?|sftp|ssh))://"
    r"(?:([^\s/@:\"'<>]+)(?::([^\s/@\"'<>]*))?@)?"
    r"([^\s/:?#\"'<>@]+)(:\d+)?([^\s\"'<>]*)"
)
QUERY_RE = re.compile(
    r"(?i)([?&;][^=&#\s]*?(?:key|sig|code|auth|session|sid|otp|pass)[^=&#\s]*=)([^&#\s]*)"
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
UNC_RE = re.compile(r"(?<![\\\w])\\\\([A-Za-z0-9._-]+)(?=\\)")
PROFILE_RE = re.compile(r"(?i)([A-Z]:\\Users\\|/home/|/Users/)([^\\/\s\"'<>|]+)")
MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
IPV6_RE = re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}\b")
IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
)
DOMAIN_RE = re.compile(
    r"(?<![\w.-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+(?:" + TLDS + r")\b(?![\w-])"
)
TOKEN_RE = re.compile(
    r"\b(?=[A-Za-z0-9+_-]*\d)(?=[A-Za-z0-9+_-]*[A-Za-z])[A-Za-z0-9+_-]{32,}={0,2}"
)

def resource_path(relative_path):
    # Bundled files (icon) live in PyInstaller's temp folder when frozen.
    return os.path.join(getattr(sys, "_MEIPASS", BASE_DIR), relative_path)
    

class Sanitizer:
    def __init__(self):
        self.rev = {}      # "[HOST_1]" -> original
        self.fwd = {}      # (category, original) -> "[HOST_1]"
        self.counts = {}
        self.stats = {}
        self.irreversible = False
        self.o = {}

    # ---- mapping ---------------------------------------------------------
    def reset(self):
        self.rev, self.fwd, self.counts, self.stats = {}, {}, {}, {}

    def load_map(self, data):
        for p, v in data.items():
            m = re.fullmatch(r"\[([A-Z]+)_(\d+)\]", p)
            if m and isinstance(v, str):
                cat, n = m.group(1), int(m.group(2))
                self.rev[p] = v
                self.fwd[(cat, v)] = p
                self.counts[cat] = max(self.counts.get(cat, 0), n)

    def restore(self, text):
        unknown = set()
        for _ in range(5):  # nested placeholders (e.g. host containing a custom term)
            def repl(m):
                p = m.group(0)
                if p in self.rev:
                    return self.rev[p]
                unknown.add(p)
                return p
            new = PLACEHOLDER_RE.sub(repl, text)
            if new == text:
                break
            text = new
        return text, sorted(unknown)

    # ---- helpers ---------------------------------------------------------
    def ph(self, cat, value):
        self.stats[cat] = self.stats.get(cat, 0) + 1
        if self.irreversible:
            return "xxx"
        key = (cat, value)
        p = self.fwd.get(key)
        if p is None:
            n = self.counts.get(cat, 0) + 1
            self.counts[cat] = n
            p = f"[{cat}_{n}]"
            self.fwd[key] = p
            self.rev[p] = value
        return p

    @staticmethod
    def _masked(v):
        v = v.strip().strip("\"'")
        return (not v) or v.lower() in SKIP_VALUES or v == "xxx" or bool(FULL_PH_RE.fullmatch(v))

    # ---- callbacks -------------------------------------------------------
    def _header(self, m):
        name, sep, val = m.groups()
        scheme = ""
        parts = val.strip().split(None, 1)
        if name.lower().endswith("authorization") and len(parts) == 2 and parts[0].lower() in AUTH_SCHEMES:
            scheme, val = parts[0] + " ", parts[1]
        if self._masked(val):
            return m.group(0)
        return f"{name}{sep}{scheme}{self.ph('SECRET', val.strip())}"

    def _hosthdr(self, m):
        name, sep, host, port = m.groups()
        if not self.o["hosts"] or host.lower() in SAFE_HOSTS or self._masked(host):
            return m.group(0)
        return f"{name}{sep}{self.ph('HOST', host)}{port or ''}"

    def _curl_u(self, m):
        if self._masked(m.group(2)):
            return m.group(0)
        return m.group(1) + self.ph("CREDS", m.group(2))

    def _conn(self, m):
        key, sep, val = m.groups()
        if self._masked(val):
            return m.group(0)
        k = key.lower()
        if k in ("user id", "uid", "user name", "username"):
            cat = "USER"
        elif k in ("initial catalog", "database"):
            cat = "DB"
        else:
            cat = "HOST"
            if not self.o["hosts"]:
                return m.group(0)
        return f"{key}{sep}{self.ph(cat, val.strip())}"

    def _kv(self, m):
        prefix, val = m.groups()
        if self._masked(val):
            return m.group(0)
        q = val[0] if val[0] in "\"'" else ""
        return prefix + q + self.ph("SECRET", val.strip("\"'")) + q

    def _xml(self, m):
        open_tag, _name, val, close = m.groups()
        if self._masked(val):
            return m.group(0)
        return open_tag + self.ph("SECRET", val.strip()) + close

    def _query(self, m):
        if self._masked(m.group(2)):
            return m.group(0)
        return m.group(1) + self.ph("SECRET", m.group(2))

    def _url(self, m):
        scheme, user, pw, host, port, rest = m.groups()
        out = scheme + "://"
        if user is not None:
            out += user if self._masked(user) else self.ph("USER", user)
            if pw is not None:
                out += ":" + (pw if self._masked(pw) else self.ph("SECRET", pw))
            out += "@"
        is_ip = bool(IPV4_RE.fullmatch(host))
        do_mask = self.o["ips"] if is_ip else self.o["hosts"]
        if do_mask and host.lower() not in SAFE_HOSTS and not self._masked(host):
            out += self.ph("IP" if is_ip else "HOST", host)
        else:
            out += host
        out += port or ""
        rest = QUERY_RE.sub(self._query, rest)
        if self.o["paths"]:
            mm = re.match(r"(/[^?#]*)(.*)", rest, re.S)
            if mm and len(mm.group(1)) > 1 and not self._masked(mm.group(1)[1:]):
                rest = "/" + self.ph("PATH", mm.group(1)[1:]) + mm.group(2)
        return out + rest

    def _simple(self, cat, safe=SAFE_HOSTS):
        def cb(m):
            v = m.group(0)
            if v.lower() in safe or self._masked(v):
                return v
            return self.ph(cat, v)
        return cb

    # ---- main ------------------------------------------------------------
    def sanitize(self, text, opts, irreversible):
        self.o, self.irreversible, self.stats = opts, irreversible, {}

        for term in sorted(opts.get("custom", []), key=len, reverse=True):
            text = re.sub(re.escape(term), lambda m: self.ph("CUSTOM", m.group(0)), text, flags=re.I)

        text = PEM_RE.sub(lambda m: self.ph("KEY", m.group(0)), text)
        text = HEADER_RE.sub(self._header, text)
        text = HOSTHDR_RE.sub(self._hosthdr, text)
        text = CURL_U_RE.sub(self._curl_u, text)
        text = JWT_RE.sub(lambda m: self.ph("TOKEN", m.group(0)), text)
        text = CONN_RE.sub(self._conn, text)
        text = KV_RE.sub(self._kv, text)
        text = XML_RE.sub(self._xml, text)
        text = URL_RE.sub(self._url, text)

        if opts["emails"]:
            text = EMAIL_RE.sub(self._simple("EMAIL", set()), text)

        text = UNC_RE.sub(
            lambda m: "\\\\" + (m.group(1) if self._masked(m.group(1)) else self.ph("HOST", m.group(1))), text
        )
        text = PROFILE_RE.sub(
            lambda m: m.group(0)
            if m.group(2).lower() in ("public", "default", "all users") or self._masked(m.group(2))
            else m.group(1) + self.ph("USER", m.group(2)),
            text,
        )
        text = MAC_RE.sub(self._simple("MAC", set()), text)
        if opts["ips"]:
            text = IPV6_RE.sub(self._simple("IP", set()), text)
            text = IPV4_RE.sub(self._simple("IP"), text)
        if opts["hosts"]:
            text = DOMAIN_RE.sub(self._simple("HOST"), text)

        text = TOKEN_RE.sub(self._simple("TOKEN", set()), text)
        return text

    def summary(self):
        if not self.stats:
            return "Nothing sensitive detected."
        total = sum(self.stats.values())
        parts = ", ".join(f"{k} x{v}" for k, v in sorted(self.stats.items()))
        return f"Masked {total} item(s): {parts}"


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
DEFAULT_CFG = {
    "custom_terms": "",
    "mask_hosts": True,
    "mask_ips": True,
    "mask_emails": True,
    "mask_paths": False,
    "reversible": True,
}


def load_config():
    cfg = dict(DEFAULT_CFG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k, default in DEFAULT_CFG.items():
                if k in data and isinstance(data[k], type(default)):
                    cfg[k] = data[k]
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        try:
            os.replace(CONFIG_FILE, CONFIG_FILE + ".bad")
        except OSError:
            pass
    return cfg


def save_config(cfg):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)
    os.replace(tmp, CONFIG_FILE)


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.san = Sanitizer()
        self._last_clip = None

        root.title(APP_TITLE)
        self._center(root, 1100, 700)

        self.custom_var = tk.StringVar(value=self.cfg["custom_terms"])
        self.hosts_var = tk.BooleanVar(value=self.cfg["mask_hosts"])
        self.ips_var = tk.BooleanVar(value=self.cfg["mask_ips"])
        self.emails_var = tk.BooleanVar(value=self.cfg["mask_emails"])
        self.paths_var = tk.BooleanVar(value=self.cfg["mask_paths"])
        self.reversible_var = tk.BooleanVar(value=self.cfg["reversible"])
        self.watch_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Paste text on the left and click Sanitize.")

        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(CLIP_POLL_MS, self._poll_clipboard)

    @staticmethod
    def _center(root, width, height):
        root.update_idletasks()
        x = (root.winfo_screenwidth() // 2) - (width // 2)
        y = (root.winfo_screenheight() // 2) - (height // 2)
        root.geometry(f"{width}x{height}+{x}+{y}")

    # ---- layout ----------------------------------------------------------
    def _build_ui(self):
        label_font = ("Calibri", 11)
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=(10, 4))
        top.columnconfigure(1, weight=1)

        tk.Label(top, text="Always mask:", font=label_font).grid(row=0, column=0, sticky="w")
        tk.Entry(top, textvariable=self.custom_var, font=label_font).grid(row=0, column=1, sticky="ew", padx=5)
        tk.Label(top, text="company / server / project names; separate with ;", font=("Calibri", 9), fg="gray40").grid(
            row=0, column=2, sticky="w"
        )

        opts = tk.Frame(top)
        opts.grid(row=1, column=0, columnspan=3, sticky="w", pady=4)
        for text, var in (
            ("Hosts / domains", self.hosts_var),
            ("IPs", self.ips_var),
            ("Emails", self.emails_var),
            ("URL paths", self.paths_var),
            ("Reversible (placeholders). Untick for plain xxx", self.reversible_var),
        ):
            tk.Checkbutton(opts, text=text, variable=var, font=("Calibri", 10)).pack(side="left", padx=(0, 12))
        tk.Checkbutton(
            opts,
            text="Watch clipboard (auto-sanitize anything I copy)",
            variable=self.watch_var,
            command=self.on_watch_toggle,
            font=("Calibri", 10, "bold"),
            fg="#A71D2A",
        ).pack(side="left", padx=(12, 0))

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=10, pady=4)

        # Tab 1: sanitize
        t1 = tk.Frame(nb)
        nb.add(t1, text="  Sanitize (before sending)  ")
        self.in_box, self.out_box = self._pane_pair(t1, "Original (paste here)", "Sanitized (safe to send)")
        b1 = tk.Frame(t1)
        b1.grid(row=1, column=0, columnspan=2, pady=6)
        self._btn(b1, "Sanitize  >", self.sanitize_clicked, "#28a745", "#218838", bold=True)
        self._btn(b1, "Paste && Sanitize && Copy", self.paste_sanitize_copy, "#28a745", "#218838")
        self._btn(b1, "Copy Sanitized", lambda: self._copy(self.out_box), "#87CEEB", "#6BB9DC", dark=True)
        self._btn(b1, "Clear", lambda: self._clear(self.in_box, self.out_box), "#FFD8A8", "#F5C48A", dark=True)
        self._btn(b1, "Save Mapping", self.save_mapping, "#1E7B34", "#17632A")
        self._btn(b1, "Load Mapping", self.load_mapping, "#1E7B34", "#17632A")
        self._btn(b1, "Reset Mapping", self.reset_mapping, "#A71D2A", "#8B1722")

        # Tab 2: restore
        t2 = tk.Frame(nb)
        nb.add(t2, text="  Restore (AI reply)  ")
        self.ai_box, self.restored_box = self._pane_pair(t2, "AI response (paste here)", "Restored with real values")
        b2 = tk.Frame(t2)
        b2.grid(row=1, column=0, columnspan=2, pady=6)
        self._btn(b2, "Restore Originals  >", self.restore_clicked, "#28a745", "#218838", bold=True)
        self._btn(b2, "Paste && Restore", self.paste_restore, "#28a745", "#218838")
        self._btn(b2, "Copy Restored", lambda: self._copy(self.restored_box), "#87CEEB", "#6BB9DC", dark=True)
        self._btn(b2, "Clear", lambda: self._clear(self.ai_box, self.restored_box), "#FFD8A8", "#F5C48A", dark=True)

        tk.Label(self.root, textvariable=self.status_var, anchor="w", font=("Calibri", 10), fg="gray25").pack(
            fill="x", padx=12, pady=(0, 8)
        )

    def _pane_pair(self, parent, left_title, right_title):
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)
        boxes = []
        for col, title in enumerate((left_title, right_title)):
            f = tk.Frame(parent)
            f.grid(row=0, column=col, sticky="nsew", padx=4, pady=4)
            tk.Label(f, text=title, font=("Calibri", 11, "bold")).pack(anchor="w")
            box = ScrolledText(f, font=("Consolas", 9), wrap="none")
            box.pack(fill="both", expand=True)
            boxes.append(box)
        return boxes

    @staticmethod
    def _btn(parent, text, cmd, bg, active, bold=False, dark=False):
        tk.Button(
            parent,
            text=text.replace("&&", "&"),
            command=cmd,
            bg=bg,
            fg="black" if dark else "white",
            activebackground=active,
            activeforeground="black" if dark else "white",
            font=("Calibri", 10, "bold" if bold else "normal"),
            padx=10,
            pady=3,
        ).pack(side="left", padx=4)

    # ---- helpers ---------------------------------------------------------
    @staticmethod
    def _get(box):
        return box.get("1.0", "end-1c")

    @staticmethod
    def _set(box, text):
        box.delete("1.0", "end")
        box.insert("1.0", text)

    def _clear(self, *boxes):
        for b in boxes:
            b.delete("1.0", "end")

    def _copy(self, box):
        self.root.clipboard_clear()
        self.root.clipboard_append(self._get(box))
        self.root.update()
        self._last_clip = self._get(box)
        self.status_var.set("Copied to clipboard.")

    def _options(self):
        custom = [t.strip() for t in re.split(r"[;,\n]+", self.custom_var.get()) if t.strip()]
        return {
            "custom": custom,
            "hosts": self.hosts_var.get(),
            "ips": self.ips_var.get(),
            "emails": self.emails_var.get(),
            "paths": self.paths_var.get(),
        }

    def _run_sanitize(self, text):
        out = self.san.sanitize(text, self._options(), not self.reversible_var.get())
        self.save_settings()
        return out

    def save_settings(self):
        self.cfg.update(
            custom_terms=self.custom_var.get().strip(),
            mask_hosts=self.hosts_var.get(),
            mask_ips=self.ips_var.get(),
            mask_emails=self.emails_var.get(),
            mask_paths=self.paths_var.get(),
            reversible=self.reversible_var.get(),
        )
        try:
            save_config(self.cfg)
        except OSError as ex:
            self.status_var.set(f"WARNING: could not save config: {ex}")

    # ---- actions ---------------------------------------------------------
    def sanitize_clicked(self):
        text = self._get(self.in_box)
        if not text.strip():
            return
        self._set(self.out_box, self._run_sanitize(text))
        self.status_var.set(self.san.summary() + "  - always skim the result before sending.")

    def paste_sanitize_copy(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showinfo("Clipboard", "Clipboard is empty or not text.")
            return
        self._set(self.in_box, text)
        out = self._run_sanitize(text)
        self._set(self.out_box, out)
        self._copy(self.out_box)
        self.status_var.set(self.san.summary() + "  - sanitized text is now on your clipboard.")

    def restore_clicked(self):
        text = self._get(self.ai_box)
        if not text.strip():
            return
        if not self.san.rev:
            messagebox.showinfo("Restore", "No mapping in memory. Sanitize something first or load a saved mapping.")
            return
        out, unknown = self.san.restore(text)
        self._set(self.restored_box, out)
        msg = "Restored."
        if unknown:
            msg += f" Unknown placeholders left as-is: {', '.join(unknown)}"
        self.status_var.set(msg)

    def paste_restore(self):
        try:
            self._set(self.ai_box, self.root.clipboard_get())
        except tk.TclError:
            messagebox.showinfo("Clipboard", "Clipboard is empty or not text.")
            return
        self.restore_clicked()

    def save_mapping(self):
        if not self.san.rev:
            messagebox.showinfo("Mapping", "Nothing to save yet.")
            return
        if not messagebox.askokcancel(
            "Save Mapping",
            "The mapping file contains your REAL secrets in plain text.\n"
            "Keep it local, never send it to an AI, and delete it when done.",
        ):
            return
        path = filedialog.asksaveasfilename(
            title="Save mapping", defaultextension=".json", initialfile="sanitizer_map.json", initialdir=BASE_DIR
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.san.rev, f, indent=2, ensure_ascii=False)
            self.status_var.set(f"Mapping saved: {path}")

    def load_mapping(self):
        path = filedialog.askopenfilename(title="Load mapping", filetypes=[("JSON", "*.json"), ("All", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.san.load_map(data)
            self.status_var.set(f"Mapping loaded: {len(self.san.rev)} entries in memory.")
        except (OSError, ValueError) as ex:
            messagebox.showerror("Mapping", f"Could not load mapping:\n\n{ex}")

    def reset_mapping(self):
        if messagebox.askyesno("Reset Mapping", "Forget all placeholder mappings? Old AI replies can no longer be restored."):
            self.san.reset()
            self.status_var.set("Mapping cleared.")

    # ---- clipboard watcher ----------------------------------------------
    def on_watch_toggle(self):
        if self.watch_var.get():
            try:
                self._last_clip = self.root.clipboard_get()  # ignore what is already copied
            except tk.TclError:
                self._last_clip = None
            self.status_var.set("Clipboard watch ON: new copied text is replaced with its sanitized version.")
        else:
            self.status_var.set("Clipboard watch OFF.")

    def _poll_clipboard(self):
        try:
            if self.watch_var.get():
                try:
                    clip = self.root.clipboard_get()
                except tk.TclError:
                    clip = None
                if clip and clip != self._last_clip:
                    out = self._run_sanitize(clip)
                    self._set(self.in_box, clip)
                    self._set(self.out_box, out)
                    if out != clip:
                        self.root.clipboard_clear()
                        self.root.clipboard_append(out)
                        self.root.update()
                    self._last_clip = out
                    self.status_var.set("Clipboard: " + self.san.summary())
        finally:
            self.root.after(CLIP_POLL_MS, self._poll_clipboard)

    def on_close(self):
        self.save_settings()
        self.root.destroy()


def main():
    root = tk.Tk()
    root.report_callback_exception = lambda exc, val, tb: messagebox.showerror(
        "Unexpected error", "".join(traceback.format_exception(exc, val, tb))
    )

    # Prefer ICO, fall back to PNG
    try:
        root.iconbitmap(resource_path("content_sanitizer.ico"))
    except Exception:
        try:
            root.iconphoto(True, tk.PhotoImage(file=resource_path("content_sanitizer.png")))
        except Exception:
            pass

    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()