#!/usr/bin/env python3
"""PyCompare - a Beyond Compare style folder / file comparison tool with FTP support.

Single file, standard library only (tkinter + ftplib). No pip install needed.

    python pycompare.py                        # home screen
    python pycompare.py <left> <right>         # two folders -> folder compare, two files -> file compare
    python pycompare.py C:\\work ftp://me@host/srv

Remote paths are written as URLs and may be used anywhere a folder or file path is accepted:

    ftp://user@host/path            plain FTP
    ftps://user@host:21/path        FTP over explicit TLS (AUTH TLS)

SFTP (SSH) is NOT supported: it needs the third-party `paramiko` package, which cannot be
imported from the standard library. Everything else here runs on a clean Python 3.8+.

Optional window icon: put CompareFiles.ico (Windows) and/or CompareFiles.png next to this script.
With PyInstaller:  pyinstaller --noconsole --onefile --icon CompareFiles.ico ^
                       --add-data "CompareFiles.ico;." --add-data "CompareFiles.png;." pycompare.py

Layout of this file
    Part 1   text comparison engine            (pure logic, no UI, no network)
    Part 2   filesystem layer: local + FTP     (pure logic, no UI)
    Part 3   folder comparison                 (pure logic, no UI)
    Part 4   user interface                    (tkinter)
"""
from __future__ import annotations

import base64
import calendar
import codecs
import difflib
import fnmatch
import ftplib
import json
import os
import posixpath
import queue
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field, replace
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import quote, unquote, urlsplit

APP_NAME = "PyCompare"
APP_VERSION = "2.0"
IS_WINDOWS = os.name == "nt"


# ============================================================
#   PART 1 - TEXT COMPARISON ENGINE  (no UI, no network)
# ============================================================

BINARY_SNIFF_BYTES = 8192
_SPLIT_LINES = re.compile(r"\r\n|\r|\n")


@dataclass
class TextFile:
    """A file loaded for comparison."""
    lines: List[str] = field(default_factory=list)
    encoding: str = "utf-8"
    eol: str = "CRLF" if IS_WINDOWS else "LF"     # CRLF / LF / CR / Mixed
    trailing_newline: bool = True
    binary: bool = False
    size: int = 0
    exists: bool = True
    raw: bytes = b""          # kept only for binary files (for byte comparison)

    def describe(self) -> str:
        if not self.exists:
            return "(missing)"
        if self.binary:
            return "binary, %s" % format_size(self.size)
        return "%s, %s, %s lines" % (self.encoding.upper(), self.eol, f"{len(self.lines):,}")


def format_size(n: Optional[int]) -> str:
    if n is None:
        return ""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def looks_binary(data: bytes) -> bool:
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE, codecs.BOM_UTF8)):
        return False
    return b"\x00" in data[:BINARY_SNIFF_BYTES]


def decode_bytes(data: bytes) -> Tuple[str, str]:
    """Return (text, encoding_name)."""
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16"), "utf-16"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "cp1252"


def detect_eol(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    kinds = [name for name, n in (("CRLF", crlf), ("LF", lf), ("CR", cr)) if n]
    if not kinds:
        return "CRLF" if IS_WINDOWS else "LF"
    return kinds[0] if len(kinds) == 1 else "Mixed"


def split_lines(text: str) -> Tuple[List[str], bool]:
    """Split on CRLF / LF / CR only (not on form feed etc.). Returns (lines, had_trailing_newline)."""
    if text == "":
        return [], True
    parts = _SPLIT_LINES.split(text)
    trailing = parts[-1] == ""
    if trailing:
        parts.pop()
    return parts, trailing


def parse_text_bytes(data: bytes) -> TextFile:
    """Build a TextFile from raw bytes (works the same for local and remote files)."""
    if looks_binary(data):
        return TextFile(binary=True, size=len(data), raw=data)
    text, encoding = decode_bytes(data)
    lines, trailing = split_lines(text)
    return TextFile(lines=lines, encoding=encoding, eol=detect_eol(text),
                    trailing_newline=trailing, size=len(data), raw=data)


def build_text_bytes(lines: Sequence[str], encoding: str = "utf-8", eol: str = "",
                     trailing_newline: bool = True) -> bytes:
    """Serialise lines back to bytes, keeping encoding and line endings."""
    sep = {"CRLF": "\r\n", "LF": "\n", "CR": "\r"}.get(eol, "\r\n" if IS_WINDOWS else "\n")
    text = sep.join(lines)
    if lines and trailing_newline:
        text += sep
    return text.encode(encoding)


# ============================================================
#               LINE DIFF
# ============================================================

WS_NONE, WS_EDGES, WS_CHANGES, WS_ALL = "none", "edges", "changes", "all"
WS_MODES = (WS_NONE, WS_EDGES, WS_CHANGES, WS_ALL)

_WS_RUN = re.compile(r"\s+")


@dataclass(frozen=True)
class DiffOptions:
    ignore_whitespace: str = WS_NONE     # none | edges | changes | all
    ignore_case: bool = False

    @property
    def is_default(self) -> bool:
        return self.ignore_whitespace == WS_NONE and not self.ignore_case


def normalize_line(line: str, opts: DiffOptions) -> str:
    if opts.ignore_whitespace == WS_EDGES:
        line = line.strip()
    elif opts.ignore_whitespace == WS_CHANGES:
        line = _WS_RUN.sub(" ", line.strip())
    elif opts.ignore_whitespace == WS_ALL:
        line = _WS_RUN.sub("", line)
    if opts.ignore_case:
        line = line.lower()
    return line


EQUAL, REPLACE, DELETE, INSERT, PAD = "equal", "replace", "delete", "insert", "pad"


@dataclass
class Row:
    """One aligned display row. ltext/rtext is None where that side has no line (padding)."""
    tag: str                       # equal | replace | delete | insert
    lno: Optional[int]
    rno: Optional[int]
    ltext: Optional[str]
    rtext: Optional[str]

    @property
    def ltag(self) -> str:
        return PAD if self.ltext is None else self.tag

    @property
    def rtag(self) -> str:
        return PAD if self.rtext is None else self.tag

    def text(self, side: str) -> Optional[str]:
        return self.ltext if side == "left" else self.rtext

    def number(self, side: str) -> Optional[int]:
        return self.lno if side == "left" else self.rno


def diff_lines(left: Sequence[str], right: Sequence[str],
               opts: DiffOptions = DiffOptions()) -> List[Row]:
    """Align two line lists. Replace blocks are paired line-by-line; any surplus
    becomes delete / insert rows."""
    lk = [normalize_line(x, opts) for x in left]
    rk = [normalize_line(x, opts) for x in right]

    # Trim the common prefix / suffix first: keeps SequenceMatcher fast on big files
    # with few changes.
    n_l, n_r = len(lk), len(rk)
    pre = 0
    while pre < n_l and pre < n_r and lk[pre] == rk[pre]:
        pre += 1
    suf = 0
    while suf < n_l - pre and suf < n_r - pre and lk[n_l - 1 - suf] == rk[n_r - 1 - suf]:
        suf += 1

    rows: List[Row] = []

    def eq_row(i: int, j: int) -> Row:
        return Row(EQUAL, i + 1, j + 1, left[i], right[j])

    for k in range(pre):
        rows.append(eq_row(k, k))

    mid_l = lk[pre:n_l - suf]
    mid_r = rk[pre:n_r - suf]
    if mid_l or mid_r:
        sm = difflib.SequenceMatcher(None, mid_l, mid_r, autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            i1 += pre
            i2 += pre
            j1 += pre
            j2 += pre
            if op == "equal":
                for off in range(i2 - i1):
                    rows.append(eq_row(i1 + off, j1 + off))
                continue
            n_old, n_new = i2 - i1, j2 - j1
            paired = min(n_old, n_new)
            for off in range(paired):
                rows.append(Row(REPLACE, i1 + off + 1, j1 + off + 1, left[i1 + off], right[j1 + off]))
            for off in range(paired, n_old):
                rows.append(Row(DELETE, i1 + off + 1, None, left[i1 + off], None))
            for off in range(paired, n_new):
                rows.append(Row(INSERT, None, j1 + off + 1, None, right[j1 + off]))

    for k in range(suf):
        rows.append(eq_row(n_l - suf + k, n_r - suf + k))
    return rows


Hunk = Tuple[int, int]      # (first_row, last_row) inclusive


def find_hunks(rows: Sequence[Row]) -> List[Hunk]:
    """Consecutive changed rows -> blocks."""
    hunks: List[Hunk] = []
    for idx, row in enumerate(rows):
        if row.tag == EQUAL:
            continue
        if hunks and hunks[-1][1] == idx - 1:
            hunks[-1] = (hunks[-1][0], idx)
        else:
            hunks.append((idx, idx))
    return hunks


def hunk_kind(rows: Sequence[Row], hunk: Hunk) -> str:
    """Dominant colour for a hunk: insert / delete / replace."""
    tags = {rows[i].tag for i in range(hunk[0], hunk[1] + 1)}
    if tags == {INSERT}:
        return INSERT
    if tags == {DELETE}:
        return DELETE
    return REPLACE


# ---------- intra-line (word level) highlights ----------

_TOKEN = re.compile(r"\w+|\s+|[^\w\s]")
Span = Tuple[int, int]


def intraline_spans(a: str, b: str, min_ratio: float = 0.35, max_len: int = 3000
                    ) -> Tuple[List[Span], List[Span]]:
    """Character spans that differ between two paired lines (word-level tokens).
    Returns ([], []) when the lines are too different for highlights to be useful."""
    if a == b or len(a) > max_len or len(b) > max_len:
        return [], []
    ta = [(m.group(), m.start()) for m in _TOKEN.finditer(a)]
    tb = [(m.group(), m.start()) for m in _TOKEN.finditer(b)]
    sm = difflib.SequenceMatcher(None, [t for t, _ in ta], [t for t, _ in tb], autojunk=False)
    if sm.ratio() < min_ratio:
        return [], []
    sa: List[Span] = []
    sb: List[Span] = []

    def end_of(tokens: List[Tuple[str, int]], i: int) -> int:
        return tokens[i - 1][1] + len(tokens[i - 1][0])

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        if i2 > i1:
            sa.append((ta[i1][1], end_of(ta, i2)))
        if j2 > j1:
            sb.append((tb[j1][1], end_of(tb, j2)))
    return sa, sb


# ---------- All / Diffs / Context filtering ----------

DEFAULT_COMMENT_PREFIXES = ("--", "//", "#")


def comment_flags(texts: Sequence[Optional[str]],
                  prefixes: Sequence[str] = DEFAULT_COMMENT_PREFIXES) -> List[bool]:
    """True for rows that are blank / padding or comment-only (line comments or /* ... */)."""
    flags: List[bool] = []
    in_block = False
    for text in texts:
        s = (text or "").strip()
        if not s:
            flags.append(True)
        elif in_block:
            flags.append(True)
            if "*/" in s:
                in_block = False
        elif any(s.startswith(p) for p in prefixes if p):
            flags.append(True)
        elif s.startswith("/*"):
            flags.append(True)
            if "*/" not in s[2:]:
                in_block = True
        else:
            flags.append(False)
    return flags


@dataclass(frozen=True)
class ViewSettings:
    mode: str = "all"                 # all | diffs
    context: bool = False
    before: int = 5
    after: int = 5
    comment_before: int = 2
    comment_after: int = 2
    comment_prefixes: Tuple[str, ...] = DEFAULT_COMMENT_PREFIXES


def visible_rows(rows: Sequence[Row], hunks: Sequence[Hunk], view: ViewSettings) -> List[int]:
    """Indices of rows to display (same behaviour as the original CRF compare window,
    plus configurable comment prefixes)."""
    n = len(rows)
    change_rows = [i for h0, h1 in hunks for i in range(h0, h1 + 1)]
    if view.mode == "all" or not change_rows:
        return list(range(n))
    if not view.context:
        return change_rows
    left_c = comment_flags([r.ltext for r in rows], view.comment_prefixes)
    right_c = comment_flags([r.rtext for r in rows], view.comment_prefixes)
    keep = set()
    for h0, h1 in hunks:
        only_comments = all(left_c[r] and right_c[r] for r in range(h0, h1 + 1))
        b, a = ((view.comment_before, view.comment_after) if only_comments
                else (view.before, view.after))
        keep.update(range(max(0, h0 - b), min(n - 1, h1 + a) + 1))
    return sorted(keep)


# ============================================================
#   EDITING SUPPORT
#   The panes are always editable, in every view mode. A filtered view hides
#   rows, so the pane text is only PART of the file. These helpers map the
#   edited pane text back onto the whole file.
# ============================================================

# (pane_start, pane_end, row_start, row_end) - all inclusive, pane_* are 0-based
# positions within the displayed rows, row_* are indices into `rows`.
Run = Tuple[int, int, int, int]


def display_runs(display: Sequence[int]) -> List[Run]:
    """Group displayed row indices into runs that are contiguous in the underlying rows.

    In "All" mode this is a single run covering the whole file. In "Diffs"/"Context"
    mode there is one run per visible block, and the gaps between them are the hidden
    rows that the user cannot see or edit."""
    runs: List[Run] = []
    for pane_pos, row_idx in enumerate(display):
        if runs and runs[-1][3] == row_idx - 1 and runs[-1][1] == pane_pos - 1:
            ps, _pe, rs, _re = runs[-1]
            runs[-1] = (ps, pane_pos, rs, row_idx)
        else:
            runs.append((pane_pos, pane_pos, row_idx, row_idx))
    return runs


def drop_untouched_padding(lines: Sequence[str], is_padding: Sequence[bool]) -> List[str]:
    """Remove alignment filler rows the user left alone.

    A filler row is dropped only when it is still empty AND still carries the padding
    mark. Typing into a filler row turns it into a real line of the file."""
    out: List[str] = []
    for i, line in enumerate(lines):
        if line == "" and i < len(is_padding) and is_padding[i]:
            continue
        out.append(line)
    return out


def rebuild_file_lines(rows: Sequence[Row], runs: Sequence[Run], side: str,
                       run_lines: Sequence[Sequence[str]]) -> List[str]:
    """Stitch the edited visible blocks back together with the hidden rows in between."""
    out: List[str] = []
    prev_row_end = -1
    for k, (_ps, _pe, rs, re_) in enumerate(runs):
        for i in range(prev_row_end + 1, rs):
            t = rows[i].text(side)
            if t is not None:
                out.append(t)
        if k < len(run_lines):
            out.extend(run_lines[k])
        prev_row_end = re_
    for i in range(prev_row_end + 1, len(rows)):
        t = rows[i].text(side)
        if t is not None:
            out.append(t)
    return out


def strip_width_pad(line: str, width_pad: int) -> str:
    """Remove the invisible width-padding spaces from the pane's final line."""
    pad = " " * width_pad
    if width_pad and line.endswith(pad):
        return line[:-width_pad]
    if width_pad and line.startswith(pad):
        return line[width_pad:]
    return line


def nearest_line_number(rows: Sequence[Row], display: Sequence[int], d: int, side: str) -> Optional[int]:
    """File line number at display position d (or the closest numbered row, for filler rows)."""
    if not display:
        return None
    d = max(0, min(d, len(display) - 1))
    for k in list(range(d, len(display))) + list(range(d - 1, -1, -1)):
        n = rows[display[k]].number(side)
        if n is not None:
            return n
    return None


def display_index_for_number(rows: Sequence[Row], display: Sequence[int], side: str,
                             number: int) -> Optional[int]:
    for k, i in enumerate(display):
        if rows[i].number(side) == number:
            return k
    return None


# ---------- merging ----------

def apply_hunk(rows: Sequence[Row], hunk: Hunk, direction: str,
               left: Sequence[str], right: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Copy one change block. direction 'ltr' makes the right side match the left
    for that block; 'rtl' does the opposite. Returns new (left_lines, right_lines)."""
    h0, h1 = hunk
    block = rows[h0:h1 + 1]
    if direction == "ltr":
        src = [r.ltext for r in block if r.ltext is not None]
        before = sum(1 for r in rows[:h0] if r.rtext is not None)
        replaced = sum(1 for r in block if r.rtext is not None)
        new_right = list(right[:before]) + src + list(right[before + replaced:])
        return list(left), new_right
    if direction == "rtl":
        src = [r.rtext for r in block if r.rtext is not None]
        before = sum(1 for r in rows[:h0] if r.ltext is not None)
        replaced = sum(1 for r in block if r.ltext is not None)
        new_left = list(left[:before]) + src + list(left[before + replaced:])
        return new_left, list(right)
    raise ValueError("direction must be 'ltr' or 'rtl'")


# ============================================================
#   PART 2 - FILESYSTEM LAYER: LOCAL AND FTP  (no UI)
#
#   Everything above the filesystem layer (folder compare, file compare,
#   merging, saving) talks to a FileSystem object and never touches os.* or
#   ftplib directly, so a local folder and an FTP folder behave the same way.
# ============================================================

FTP_TIMEOUT = 30.0
FTP_SCHEMES = ("ftp", "ftps")
MAX_REMOTE_READ = 64 * 1024 * 1024


class RemoteError(Exception):
    """Any failure talking to a remote server (connection, login or command)."""


@dataclass
class Node:
    """One directory entry, local or remote."""
    name: str
    path: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0
    is_link: bool = False


# ------------------------------------------------------------
#   Remote URLs:  ftp://user@host:port/path
# ------------------------------------------------------------

@dataclass(frozen=True)
class RemoteRef:
    scheme: str
    host: str
    port: int
    user: str
    path: str

    @property
    def key(self) -> str:
        return "%s://%s@%s:%d" % (self.scheme, self.user, self.host, self.port)

    def url(self, path: Optional[str] = None) -> str:
        p = self.path if path is None else path
        port = "" if self.port == default_port(self.scheme) else ":%d" % self.port
        user = quote(self.user, safe="") + "@" if self.user and self.user != "anonymous" else ""
        return "%s://%s%s%s%s" % (self.scheme, user, self.host, port, p if p.startswith("/") else "/" + p)


def default_port(scheme: str) -> int:
    return 21


def is_remote_path(path: str) -> bool:
    low = (path or "").strip().lower()
    return any(low.startswith(s + "://") for s in FTP_SCHEMES)


def parse_remote(path: str) -> Optional[RemoteRef]:
    """Parse ftp://user@host:port/path. Returns None for local paths."""
    if not is_remote_path(path):
        return None
    parts = urlsplit(path.strip())
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    if not host:
        raise RemoteError("No host in %r" % path)
    port = parts.port or default_port(scheme)
    user = unquote(parts.username) if parts.username else "anonymous"
    remote = unquote(parts.path) or "/"
    if not remote.startswith("/"):
        remote = "/" + remote
    if len(remote) > 1:
        remote = remote.rstrip("/")
    return RemoteRef(scheme, host, port, user, remote)


# ------------------------------------------------------------
#   FTP connection profiles
# ------------------------------------------------------------

@dataclass
class FtpProfile:
    name: str = ""
    scheme: str = "ftp"                 # ftp | ftps (explicit TLS, AUTH TLS)
    host: str = ""
    port: int = 21
    user: str = "anonymous"
    password: str = ""
    save_password: bool = False
    passive: bool = True
    encoding: str = "utf-8"
    start_dir: str = "/"
    tls_verify: bool = False            # internal servers usually have self-signed certificates
    time_offset_hours: float = 0.0      # for servers that report local time instead of UTC

    def ref(self, path: Optional[str] = None) -> RemoteRef:
        return RemoteRef(self.scheme, self.host, self.port, self.user, path or self.start_dir or "/")

    def matches(self, ref: RemoteRef) -> bool:
        return (self.scheme == ref.scheme and self.host.lower() == ref.host.lower()
                and self.port == ref.port and self.user == ref.user)

    # ---- persistence (passwords are obfuscated, NOT encrypted - see to_dict) ----
    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        if self.save_password and self.password:
            # base64 only keeps the password out of plain sight in the config file.
            # It is NOT encryption and does not protect against anyone with file access.
            d["password"] = "b64:" + base64.b64encode(self.password.encode("utf-8")).decode("ascii")
        else:
            d["password"] = ""
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "FtpProfile":
        p = FtpProfile()
        for key, value in d.items():
            if hasattr(p, key):
                setattr(p, key, value)
        if isinstance(p.password, str) and p.password.startswith("b64:"):
            try:
                p.password = base64.b64decode(p.password[4:]).decode("utf-8")
            except Exception:
                p.password = ""
        try:
            p.port = int(p.port)
        except (TypeError, ValueError):
            p.port = default_port(p.scheme)
        return p


# ------------------------------------------------------------
#   FileSystem interface
# ------------------------------------------------------------

class FileSystem:
    is_remote = False
    case_sensitive = True
    sep = "/"

    def label(self, path: str) -> str:
        return path

    def join(self, path: str, *names: str) -> str:
        raise NotImplementedError

    def dirname(self, path: str) -> str:
        raise NotImplementedError

    def basename(self, path: str) -> str:
        raise NotImplementedError

    def listdir(self, path: str) -> List[Node]:
        raise NotImplementedError

    def isdir(self, path: str) -> bool:
        raise NotImplementedError

    def isfile(self, path: str) -> bool:
        raise NotImplementedError

    def exists(self, path: str) -> bool:
        raise NotImplementedError

    def stat(self, path: str) -> Optional[Tuple[int, float]]:
        raise NotImplementedError

    def read_bytes(self, path: str) -> bytes:
        raise NotImplementedError

    def write_bytes(self, path: str, data: bytes) -> None:
        raise NotImplementedError

    def makedirs(self, path: str) -> None:
        raise NotImplementedError

    def remove(self, path: str) -> None:
        raise NotImplementedError

    def rmtree(self, path: str) -> None:
        raise NotImplementedError

    def invalidate(self, path: str = "") -> None:
        pass

    def close(self) -> None:
        pass

    def fold(self, name: str) -> str:
        return name if self.case_sensitive else name.lower()


class LocalFS(FileSystem):
    is_remote = False
    case_sensitive = not IS_WINDOWS
    sep = os.sep

    def join(self, path: str, *names: str) -> str:
        return os.path.join(path, *names)

    def dirname(self, path: str) -> str:
        return os.path.dirname(path)

    def basename(self, path: str) -> str:
        return os.path.basename(path.rstrip("/\\")) or path

    def listdir(self, path: str) -> List[Node]:
        out: List[Node] = []
        with os.scandir(path) as it:
            for e in it:
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                    st = e.stat(follow_symlinks=False)
                    size, mtime = (0 if is_dir else st.st_size), st.st_mtime
                except OSError:
                    is_dir, size, mtime = False, 0, 0.0
                out.append(Node(e.name, e.path, is_dir, size, mtime, e.is_symlink()))
        return out

    def isdir(self, path: str) -> bool:
        return os.path.isdir(path)

    def isfile(self, path: str) -> bool:
        return os.path.isfile(path)

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def stat(self, path: str) -> Optional[Tuple[int, float]]:
        try:
            st = os.stat(path)
            return st.st_size, st.st_mtime
        except OSError:
            return None

    def read_bytes(self, path: str) -> bytes:
        with open(path, "rb") as fh:
            return fh.read()

    def write_bytes(self, path: str, data: bytes) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".pycompare.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)

    def makedirs(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)

    def remove(self, path: str) -> None:
        os.remove(path)

    def rmtree(self, path: str) -> None:
        shutil.rmtree(path)


# ------------------------------------------------------------
#   Parsing of FTP directory listings
# ------------------------------------------------------------

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

_DOS_LINE = re.compile(
    r"^(?P<m>\d{2})-(?P<d>\d{2})-(?P<y>\d{2,4})\s+(?P<hh>\d{2}):(?P<mm>\d{2})(?P<ap>[AaPp][Mm])?\s+"
    r"(?P<dir><DIR>|[\d,]+)\s+(?P<name>.+?)\s*$")


def parse_mlsd_time(value: str, offset_hours: float = 0.0) -> float:
    """MLSD/MDTM timestamps are UTC: YYYYMMDDHHMMSS[.fraction]."""
    value = (value or "").strip().split(".")[0]
    if len(value) < 14 or not value[:14].isdigit():
        return 0.0
    try:
        tm = (int(value[0:4]), int(value[4:6]), int(value[6:8]),
              int(value[8:10]), int(value[10:12]), int(value[12:14]), 0, 1, -1)
        return calendar.timegm(tm) - offset_hours * 3600.0
    except (ValueError, OverflowError):
        return 0.0


def parse_unix_time(month: str, day: str, last: str, now: Optional[float] = None) -> float:
    """'Nov 8 15:04' (current year, local time) or 'Nov 8 2025'. Minute precision at best."""
    mon = _MONTHS.get(month.lower()[:3])
    if not mon:
        return 0.0
    try:
        dayn = int(day)
    except ValueError:
        return 0.0
    now = time.time() if now is None else now
    try:
        if ":" in last:
            hh, mm = (int(x) for x in last.split(":")[:2])
            year = time.localtime(now).tm_year
            guess = time.mktime((year, mon, dayn, hh, mm, 0, 0, 1, -1))
            if guess - now > 86400 * 2:          # listing without a year is never in the future
                guess = time.mktime((year - 1, mon, dayn, hh, mm, 0, 0, 1, -1))
            return guess
        return time.mktime((int(last), mon, dayn, 0, 0, 0, 0, 1, -1))
    except (ValueError, OverflowError):
        return 0.0


def parse_list_line(line: str, now: Optional[float] = None) -> Optional[Tuple[str, bool, int, float, bool]]:
    """Parse one LIST line -> (name, is_dir, size, mtime, is_link). None if unparseable."""
    line = line.rstrip("\r\n")
    if not line.strip():
        return None

    dos = _DOS_LINE.match(line)
    if dos:
        name = dos.group("name")
        if name in (".", ".."):
            return None
        is_dir = dos.group("dir").upper() == "<DIR>"
        size = 0 if is_dir else int(dos.group("dir").replace(",", ""))
        year = int(dos.group("y"))
        year += 2000 if year < 70 else (1900 if year < 100 else 0)
        hour = int(dos.group("hh"))
        ap = (dos.group("ap") or "").lower()
        if ap == "pm" and hour != 12:
            hour += 12
        elif ap == "am" and hour == 12:
            hour = 0
        try:
            mtime = time.mktime((year, int(dos.group("m")), int(dos.group("d")),
                                 hour, int(dos.group("mm")), 0, 0, 1, -1))
        except (ValueError, OverflowError):
            mtime = 0.0
        return name, is_dir, size, mtime, False

    if line[0] not in "-dlbcps":
        return None
    parts = line.split(None, 8)
    # Usual unix layout: perms links owner group size month day time name
    # Some servers drop the group column, giving 8 fields instead of 9.
    for name_field in (8, 7):
        if len(parts) <= name_field:
            continue
        size_i, mon_i, day_i, last_i = name_field - 4, name_field - 3, name_field - 2, name_field - 1
        if not parts[size_i].isdigit():
            continue
        if parts[mon_i].lower()[:3] not in _MONTHS:
            continue
        name = parts[name_field]
        is_link = line[0] == "l"
        if is_link and " -> " in name:
            name = name.split(" -> ", 1)[0]
        if name in (".", ".."):
            return None
        is_dir = line[0] == "d"
        return (name, is_dir, 0 if is_dir else int(parts[size_i]),
                parse_unix_time(parts[mon_i], parts[day_i], parts[last_i], now), is_link)
    return None


# ------------------------------------------------------------
#   FTP / FTPS filesystem
# ------------------------------------------------------------

class FtpFS(FileSystem):
    """A FileSystem backed by one FTP (or FTPS) connection.

    All calls are serialised with a lock, so a background folder scan and the UI can
    share one instance. A dropped connection is re-established once per call."""

    is_remote = True
    case_sensitive = True
    sep = "/"

    def __init__(self, profile: FtpProfile, timeout: float = FTP_TIMEOUT) -> None:
        self.profile = profile
        self.timeout = timeout
        self._ftp: Optional[ftplib.FTP] = None
        self._lock = threading.RLock()
        self._cache: Dict[str, List[Node]] = {}
        self._mlsd: Optional[bool] = None       # None = not probed yet
        self._root = "/"

    # ---------- connection ----------
    def _new_connection(self) -> ftplib.FTP:
        p = self.profile
        try:
            if p.scheme == "ftps":
                context = ssl.create_default_context()
                if not p.tls_verify:
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                ftp: ftplib.FTP = ftplib.FTP_TLS(timeout=self.timeout, context=context)
            else:
                ftp = ftplib.FTP(timeout=self.timeout)
            ftp.encoding = p.encoding or "utf-8"
            ftp.connect(p.host, p.port)
            ftp.login(p.user or "anonymous", p.password or "")
            if isinstance(ftp, ftplib.FTP_TLS):
                ftp.prot_p()
            ftp.set_pasv(bool(p.passive))
            try:
                ftp.voidcmd("TYPE I")
            except ftplib.all_errors:
                pass
            try:
                self._root = ftp.pwd() or "/"
            except ftplib.all_errors:
                self._root = "/"
            return ftp
        except ssl.SSLError as ex:
            raise RemoteError("TLS error talking to %s: %s" % (p.host, ex)) from ex
        except socket.timeout as ex:
            raise RemoteError("Timed out connecting to %s:%d" % (p.host, p.port)) from ex
        except ftplib.all_errors as ex:          # already includes OSError
            raise RemoteError("Cannot connect to %s:%d - %s" % (p.host, p.port, ex)) from ex

    def connect(self) -> None:
        with self._lock:
            if self._ftp is None:
                self._ftp = self._new_connection()

    def _run(self, fn: Callable[[ftplib.FTP], Any], retry: bool = True) -> Any:
        """Run one FTP command, reconnecting once if the connection went away."""
        with self._lock:
            if self._ftp is None:
                self._ftp = self._new_connection()
            try:
                return fn(self._ftp)
            except (ftplib.error_temp, ftplib.error_proto, OSError, EOFError) as ex:
                if not retry:
                    raise RemoteError(str(ex)) from ex
                try:
                    self._ftp.close()
                except Exception:
                    pass
                self._ftp = None
                try:
                    self._ftp = self._new_connection()
                    return fn(self._ftp)
                except ftplib.error_perm as ex2:
                    raise RemoteError(str(ex2)) from ex2
                except ftplib.all_errors as ex2:     # already includes OSError
                    raise RemoteError(str(ex2)) from ex2
            except ftplib.error_perm as ex:
                raise RemoteError(str(ex)) from ex

    def close(self) -> None:
        with self._lock:
            if self._ftp is not None:
                try:
                    self._ftp.quit()
                except Exception:
                    try:
                        self._ftp.close()
                    except Exception:
                        pass
                self._ftp = None

    # ---------- paths ----------
    def label(self, path: str) -> str:
        return self.profile.ref(path).url()

    def join(self, path: str, *names: str) -> str:
        return posixpath.join(path or "/", *names)

    def dirname(self, path: str) -> str:
        parent = posixpath.dirname(path.rstrip("/")) or "/"
        return parent

    def basename(self, path: str) -> str:
        return posixpath.basename(path.rstrip("/")) or "/"

    def home(self) -> str:
        return self._root or "/"

    # ---------- listing ----------
    def _listdir_uncached(self, path: str) -> List[Node]:
        path = path or "/"
        nodes: List[Node] = []
        if self._mlsd is not False:
            try:
                entries = self._run(lambda f: list(f.mlsd(path, facts=["type", "size", "modify"])))
                self._mlsd = True
                for name, facts in entries:
                    kind = (facts.get("type") or "").lower()
                    if kind in ("cdir", "pdir") or name in (".", ".."):
                        continue
                    is_dir = kind == "dir"
                    try:
                        size = int(facts.get("size") or 0)
                    except ValueError:
                        size = 0
                    nodes.append(Node(name, self.join(path, name), is_dir, 0 if is_dir else size,
                                      parse_mlsd_time(facts.get("modify", ""),
                                                      self.profile.time_offset_hours)))
                return nodes
            except RemoteError:
                if self._mlsd is True:
                    raise
                self._mlsd = False      # server has no MLSD: fall back to LIST
        lines: List[str] = []
        self._run(lambda f: f.retrlines("LIST " + path if path != "/" else "LIST /", lines.append))
        now = time.time()
        for line in lines:
            parsed = parse_list_line(line, now)
            if not parsed:
                continue
            name, is_dir, size, mtime, is_link = parsed
            nodes.append(Node(name, self.join(path, name), is_dir, size, mtime, is_link))
        return nodes

    def listdir(self, path: str) -> List[Node]:
        path = path or "/"
        with self._lock:
            cached = self._cache.get(path)
            if cached is not None:
                return list(cached)
            nodes = self._listdir_uncached(path)
            self._cache[path] = nodes
            return list(nodes)

    def invalidate(self, path: str = "") -> None:
        with self._lock:
            if not path:
                self._cache.clear()
            else:
                self._cache.pop(path, None)
                self._cache.pop(self.dirname(path), None)

    def _find(self, path: str) -> Optional[Node]:
        parent = self.dirname(path)
        name = self.basename(path)
        if path in ("/", ""):
            return Node("/", "/", True)
        try:
            for node in self.listdir(parent):
                if node.name == name:
                    return node
        except RemoteError:
            return None
        return None

    def isdir(self, path: str) -> bool:
        if path in ("/", ""):
            return True
        node = self._find(path)
        if node is not None:
            return node.is_dir
        try:                                     # not in the parent listing: ask the server
            here = self._run(lambda f: f.pwd())
            self._run(lambda f: f.cwd(path))
            self._run(lambda f: f.cwd(here))
            return True
        except RemoteError:
            return False

    def isfile(self, path: str) -> bool:
        node = self._find(path)
        return bool(node and not node.is_dir)

    def exists(self, path: str) -> bool:
        return path in ("/", "") or self._find(path) is not None

    def stat(self, path: str) -> Optional[Tuple[int, float]]:
        node = self._find(path)
        if node is None:
            return None
        size, mtime = node.size, node.mtime
        if not node.is_dir and (size == 0 or mtime == 0.0):
            try:                                 # sharpen a coarse LIST timestamp
                if mtime == 0.0:
                    reply = self._run(lambda f: f.sendcmd("MDTM " + path))
                    if reply[:3] == "213":
                        mtime = parse_mlsd_time(reply[3:], self.profile.time_offset_hours)
                if size == 0:
                    size = int(self._run(lambda f: f.size(path)) or 0)
            except (RemoteError, ValueError, TypeError):
                pass
        return size, mtime

    # ---------- transfers ----------
    def read_bytes(self, path: str, progress: Optional[Callable[[int], None]] = None) -> bytes:
        chunks: List[bytes] = []
        total = [0]

        def collect(block: bytes) -> None:
            total[0] += len(block)
            if total[0] > MAX_REMOTE_READ:
                raise RemoteError("Remote file is larger than %s" % format_size(MAX_REMOTE_READ))
            chunks.append(block)
            if progress:
                progress(total[0])

        self._run(lambda f: f.retrbinary("RETR " + path, collect))
        return b"".join(chunks)

    def write_bytes(self, path: str, data: bytes, progress: Optional[Callable[[int], None]] = None) -> None:
        parent = self.dirname(path)
        if parent and parent != "/":
            self.makedirs(parent)
        stream = _ProgressReader(data, progress)
        self._run(lambda f: f.storbinary("STOR " + path, stream))
        self.invalidate(path)

    def makedirs(self, path: str) -> None:
        path = path.rstrip("/")
        if not path or path == "/":
            return
        parts = [p for p in path.split("/") if p]
        current = ""
        for part in parts:
            current += "/" + part
            if self.isdir(current):
                continue
            try:
                self._run(lambda f, c=current: f.mkd(c))
            except RemoteError:
                if not self.isdir(current):
                    raise
            self.invalidate(current)

    def remove(self, path: str) -> None:
        self._run(lambda f: f.delete(path))
        self.invalidate(path)

    def rmtree(self, path: str) -> None:
        for node in self.listdir(path):
            if node.is_dir:
                self.rmtree(node.path)
            else:
                self.remove(node.path)
        self._run(lambda f: f.rmd(path))
        self.invalidate(path)


class _ProgressReader:
    """File-like wrapper so storbinary can report upload progress."""

    def __init__(self, data: bytes, progress: Optional[Callable[[int], None]] = None) -> None:
        self._data = data
        self._pos = 0
        self._progress = progress

    def read(self, size: int = 8192) -> bytes:
        chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        if chunk and self._progress:
            self._progress(self._pos)
        return chunk


# ------------------------------------------------------------
#   Cross-filesystem helpers
# ------------------------------------------------------------

def copy_file(src_fs: FileSystem, src: str, dst_fs: FileSystem, dst: str) -> None:
    if not src_fs.is_remote and not dst_fs.is_remote:
        parent = os.path.dirname(dst)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.copy2(src, dst)
        return
    dst_fs.write_bytes(dst, src_fs.read_bytes(src))


def copy_tree(src_fs: FileSystem, src: str, dst_fs: FileSystem, dst: str) -> None:
    dst_fs.makedirs(dst)
    for node in src_fs.listdir(src):
        target = dst_fs.join(dst, node.name)
        if node.is_dir:
            copy_tree(src_fs, node.path, dst_fs, target)
        else:
            copy_file(src_fs, node.path, dst_fs, target)


# ============================================================
#   PART 3 - FOLDER COMPARISON  (no UI; works on any FileSystem)
# ============================================================

DEFAULT_IGNORES = (".git", ".svn", ".vs", "__pycache__", "node_modules", "*.pyc", "Thumbs.db", ".DS_Store")

S_SAME, S_DIFF, S_LEFT_ONLY, S_RIGHT_ONLY, S_MISMATCH = (
    "same", "diff", "left_only", "right_only", "mismatch")

CMP_QUICK, CMP_SIZE, CMP_CONTENT = "quick", "size", "content"

LOCAL_MTIME_TOLERANCE = 2.0          # FAT / copy rounding
REMOTE_MTIME_TOLERANCE = 120.0       # FTP listings are minute-precision at best


@dataclass(frozen=True)
class FolderOptions:
    compare: str = CMP_QUICK                       # quick (size+date) | size | content
    ignore_patterns: Tuple[str, ...] = DEFAULT_IGNORES
    text_opts: DiffOptions = DiffOptions()         # applied to content compare of text files
    mtime_tolerance: Optional[float] = None        # None -> pick automatically


@dataclass
class FolderEntry:
    name: str
    parts: Tuple[str, ...]                 # path relative to the roots
    is_dir: bool
    status: str = S_SAME
    lpath: Optional[str] = None
    rpath: Optional[str] = None
    lsize: Optional[int] = None
    rsize: Optional[int] = None
    lmtime: Optional[float] = None
    rmtime: Optional[float] = None
    newer: Optional[str] = None            # 'left' | 'right' for differing files
    children: List["FolderEntry"] = field(default_factory=list)
    error: str = ""

    @property
    def rel(self) -> str:
        return "/".join(self.parts)


@dataclass
class FolderResult:
    left_root: str
    right_root: str
    root: FolderEntry
    counts: Dict[str, int] = field(default_factory=dict)


class Cancelled(Exception):
    pass


def _ignored(name: str, patterns: Sequence[str]) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatch(low, p.lower()) for p in patterns if p)


def _scan(fs: Optional[FileSystem], path: Optional[str], patterns: Sequence[str],
          fold: Callable[[str], str], strict: bool = False) -> Tuple[Dict[str, Node], str]:
    """Return ({folded_name: Node}, error_message).

    `strict` is used for the two root folders: if a root cannot be listed (wrong
    password, server down, path gone) the caller must fail loudly, because treating
    it as an empty folder would report every file on the other side as "only here"."""
    if fs is None or not path:
        return {}, ""
    try:
        nodes = fs.listdir(path)
    except (OSError, RemoteError) as ex:
        if strict:
            raise
        return {}, str(ex)
    out: Dict[str, Node] = {}
    for node in nodes:
        if _ignored(node.name, patterns):
            continue
        out[fold(node.name)] = node
    return out, ""


def _bytes_equal_streaming(a: str, b: str) -> bool:
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            ca, cb = fa.read(65536), fb.read(65536)
            if ca != cb:
                return False
            if not ca:
                return True


def _content_equal(lfs: FileSystem, lpath: str, rfs: FileSystem, rpath: str,
                   opts: FolderOptions) -> bool:
    if opts.text_opts.is_default:
        if not lfs.is_remote and not rfs.is_remote:
            return _bytes_equal_streaming(lpath, rpath)
        return lfs.read_bytes(lpath) == rfs.read_bytes(rpath)
    ta = parse_text_bytes(lfs.read_bytes(lpath))
    tb = parse_text_bytes(rfs.read_bytes(rpath))
    if ta.binary or tb.binary:
        return ta.binary and tb.binary and ta.raw == tb.raw
    return ([normalize_line(x, opts.text_opts) for x in ta.lines]
            == [normalize_line(x, opts.text_opts) for x in tb.lines])


def files_equal(ln: Node, rn: Node, opts: FolderOptions,
                lfs: FileSystem, rfs: FileSystem, tolerance: float) -> bool:
    try:
        if opts.compare == CMP_CONTENT:
            if opts.text_opts.is_default and ln.size != rn.size:
                return False
            return _content_equal(lfs, ln.path, rfs, rn.path, opts)
        if ln.size != rn.size:
            return False
        if opts.compare == CMP_SIZE:
            return True
        if not ln.mtime or not rn.mtime:
            return True                       # no usable timestamps: fall back to size
        return abs(ln.mtime - rn.mtime) <= tolerance
    except (OSError, RemoteError):
        return False


def compare_folders(left_root: str, right_root: str, opts: FolderOptions = FolderOptions(),
                    lfs: Optional[FileSystem] = None, rfs: Optional[FileSystem] = None,
                    progress: Optional[Callable[[int], None]] = None,
                    is_cancelled: Optional[Callable[[], bool]] = None) -> FolderResult:
    """Recursively compare two folders, local or remote. Either root may be '' to
    browse one side only."""
    lfs = lfs or LocalFS()
    rfs = rfs or LocalFS()
    counter = [0]
    patterns = opts.ignore_patterns
    insensitive = not (lfs.case_sensitive and rfs.case_sensitive)
    fold = (lambda s: s.lower()) if insensitive else (lambda s: s)
    tolerance = opts.mtime_tolerance
    if tolerance is None:
        tolerance = (REMOTE_MTIME_TOLERANCE if (lfs.is_remote or rfs.is_remote)
                     else LOCAL_MTIME_TOLERANCE)

    def tick() -> None:
        counter[0] += 1
        if is_cancelled and is_cancelled():
            raise Cancelled()
        if progress and counter[0] % 25 == 0:
            progress(counter[0])

    def build(parts: Tuple[str, ...], lpath: Optional[str], rpath: Optional[str],
              strict: bool = False) -> List[FolderEntry]:
        lnodes, lerr = _scan(lfs, lpath, patterns, fold, strict)
        rnodes, rerr = _scan(rfs, rpath, patterns, fold, strict)
        entries: List[FolderEntry] = []
        keys = sorted(set(lnodes) | set(rnodes),
                      key=lambda k: (not (lnodes.get(k) or rnodes[k]).is_dir, k.lower()))
        for k in keys:
            tick()
            ln, rn = lnodes.get(k), rnodes.get(k)
            base = ln or rn
            assert base is not None
            e = FolderEntry(
                name=base.name, parts=parts + (base.name,), is_dir=base.is_dir,
                lpath=ln.path if ln else None, rpath=rn.path if rn else None,
                lsize=None if (ln is None or ln.is_dir) else ln.size,
                rsize=None if (rn is None or rn.is_dir) else rn.size,
                lmtime=ln.mtime if ln else None, rmtime=rn.mtime if rn else None,
            )
            if ln and rn and ln.is_dir != rn.is_dir:
                e.status = S_MISMATCH
                e.is_dir = False
            elif ln and rn:
                if ln.is_dir:
                    e.children = build(e.parts, ln.path, rn.path)
                    e.status = S_SAME if all(c.status == S_SAME for c in e.children) else S_DIFF
                elif files_equal(ln, rn, opts, lfs, rfs, tolerance):
                    e.status = S_SAME
                else:
                    e.status = S_DIFF
                    if ln.mtime and rn.mtime and abs(ln.mtime - rn.mtime) > tolerance:
                        e.newer = "left" if ln.mtime > rn.mtime else "right"
            else:
                e.status = S_LEFT_ONLY if ln else S_RIGHT_ONLY
                if base.is_dir:
                    e.children = build(e.parts, ln.path if ln else None, rn.path if rn else None)
                    for c in walk_entries(e.children):
                        c.status = e.status
            entries.append(e)
        if lerr or rerr:
            entries.append(FolderEntry(name="(cannot read folder)", parts=parts + ("(cannot read folder)",),
                                       is_dir=False, status=S_MISMATCH, error=lerr or rerr))
        return entries

    root = FolderEntry(name="", parts=(), is_dir=True, lpath=left_root or None, rpath=right_root or None)
    root.children = build((), left_root or None, right_root or None, strict=True)
    root.status = S_SAME if all(c.status == S_SAME for c in root.children) else S_DIFF
    result = FolderResult(left_root, right_root, root)
    result.counts = summarize(root)
    if progress:
        progress(counter[0])
    return result


def walk_entries(entries: Sequence[FolderEntry]) -> Iterator[FolderEntry]:
    for e in entries:
        yield e
        yield from walk_entries(e.children)


def summarize(root: FolderEntry) -> Dict[str, int]:
    """File counts per status (folders are not counted)."""
    counts = {S_SAME: 0, S_DIFF: 0, S_LEFT_ONLY: 0, S_RIGHT_ONLY: 0, S_MISMATCH: 0}
    for e in walk_entries(root.children):
        if not e.is_dir:
            counts[e.status] = counts.get(e.status, 0) + 1
    return counts


# ---------- filtering for the folder view ----------

FILTER_ALL, FILTER_DIFFS, FILTER_SAME, FILTER_LEFT, FILTER_RIGHT = (
    "all", "diffs", "same", "left", "right")

_FILTER_STATUSES = {
    FILTER_DIFFS: {S_DIFF, S_LEFT_ONLY, S_RIGHT_ONLY, S_MISMATCH},
    FILTER_SAME: {S_SAME},
    FILTER_LEFT: {S_LEFT_ONLY},
    FILTER_RIGHT: {S_RIGHT_ONLY},
}


def entry_matches(entry: FolderEntry, flt: str) -> bool:
    """A file matches by its own status; a folder matches if any descendant file does."""
    if flt == FILTER_ALL:
        return True
    wanted = _FILTER_STATUSES[flt]
    if entry.is_dir:
        return any(entry_matches(c, flt) for c in entry.children)
    return entry.status in wanted


# ---------- file operations used by the folder view ----------

def copy_entry(entry: FolderEntry, from_side: str, left_root: str, right_root: str,
               lfs: FileSystem, rfs: FileSystem) -> str:
    """Copy an entry from one side to the other (local<->remote included)."""
    if from_side == "left":
        src_fs, src, dst_fs, dst_root = lfs, entry.lpath, rfs, right_root
    else:
        src_fs, src, dst_fs, dst_root = rfs, entry.rpath, lfs, left_root
    if not src:
        raise FileNotFoundError("Nothing to copy on the %s side." % from_side)
    if not dst_root:
        raise ValueError("The destination folder is not set.")
    dst = dst_fs.join(dst_root, *entry.parts)
    if entry.is_dir:
        copy_tree(src_fs, src, dst_fs, dst)
    else:
        copy_file(src_fs, src, dst_fs, dst)
    dst_fs.invalidate(dst)
    return dst


def delete_entry(entry: FolderEntry, side: str, lfs: FileSystem, rfs: FileSystem) -> None:
    fs = lfs if side == "left" else rfs
    path = entry.lpath if side == "left" else entry.rpath
    if not path:
        return
    if entry.is_dir:
        fs.rmtree(path)
    else:
        fs.remove(path)
    fs.invalidate(path)


# ============================================================
#   PART 4 - USER INTERFACE
# ============================================================

MAX_HISTORY = 15
MAX_RECENT = 12
LARGE_FILE_BYTES = 25 * 1024 * 1024
MAX_INTRALINE_ROWS = 2500
MAX_FIND_MATCHES = 5000
EDIT_DEBOUNCE_MS = 300          # how long after the last keystroke the panes are re-compared

# ------------------------------------------------------------
#   Themes
# ------------------------------------------------------------

THEMES: Dict[str, Dict[str, Any]] = {
    "Dark": dict(
        dark=True,
        bg="#1e1e1e", panel="#252526", panel2="#2d2d30", fg="#d4d4d4", muted="#8b949e",
        border="#3c3c3c", accent="#0e639c", accent_fg="#ffffff", hover="#3e4451", entry_bg="#2b2b2b",
        text_bg="#1e1e1e", text_fg="#d4d4d4", gutter_bg="#252526", gutter_fg="#6e7681", sel="#264f78",
        ins_bg="#173a22", ins_fg="#c8f0d0", del_bg="#4b1d1d", del_fg="#f6c4c4",
        chg_bg="#46391a", chg_fg="#f3e3ae", pad_bg="#262626", pad_fg="#555555",
        intra_ins="#2f7a46", intra_del="#9b3030", intra_chg="#8f7a1d",
        current="#1f5a9e", find="#6b5b00", find_cur="#b58900",
        s_same="#9da5b4", s_diff="#e5c07b", s_left="#61afef", s_right="#98c379", s_mismatch="#e06c75",
    ),
    "Sage Dark": dict(
        dark=True,
        bg="#2a2a2a", panel="#303030", panel2="#383838", fg="#b8c4b0", muted="#8a9384",
        border="#454545", accent="#5c7a52", accent_fg="#f0f4ec", hover="#3f4a3c", entry_bg="#333333",
        text_bg="#2a2a2a", text_fg="#b8c4b0", gutter_bg="#303030", gutter_fg="#76806f", sel="#3f5438",
        ins_bg="#1f3a22", ins_fg="#bfe0bd", del_bg="#48221f", del_fg="#e8bdb6",
        chg_bg="#43391c", chg_fg="#ded0a2", pad_bg="#2f2f2f", pad_fg="#5a5f57",
        intra_ins="#3c7a44", intra_del="#8f3c33", intra_chg="#897420",
        current="#44633c", find="#6a5c20", find_cur="#b08b2a",
        s_same="#8a9384", s_diff="#d9bd7e", s_left="#8fb3cc", s_right="#a3c389", s_mismatch="#d99b90",
    ),
    "Light": dict(
        dark=False,
        bg="#f0f0f0", panel="#ffffff", panel2="#e4e6e9", fg="#1f2933", muted="#6b7280",
        border="#c8ccd0", accent="#2563eb", accent_fg="#ffffff", hover="#d6e0f2", entry_bg="#ffffff",
        text_bg="#fdfdfd", text_fg="#1f2933", gutter_bg="#eef0f2", gutter_fg="#8a929a", sel="#bfdbfe",
        ins_bg="#dcfce7", ins_fg="#14532d", del_bg="#fee2e2", del_fg="#7f1d1d",
        chg_bg="#fef9c3", chg_fg="#713f12", pad_bg="#f1f5f9", pad_fg="#94a3b8",
        intra_ins="#86efac", intra_del="#fca5a5", intra_chg="#fde047",
        current="#93c5fd", find="#fde68a", find_cur="#fb923c",
        s_same="#4b5563", s_diff="#b45309", s_left="#1d4ed8", s_right="#15803d", s_mismatch="#b91c1c",
    ),
}
DEFAULT_THEME = "Dark"

# The active palette. Mutated in place so every module-level reference stays valid.
T: Dict[str, Any] = dict(THEMES[DEFAULT_THEME])


def pick_font(root: tk.Misc, candidates: List[str], fallback: str) -> str:
    available = set(tkfont.families(root))
    for name in candidates:
        if name in available:
            return name
    return fallback


class Fonts:
    """Shared fonts. Changing the size of `mono` updates every pane at once."""

    def __init__(self, root: tk.Misc, mono_size: int) -> None:
        self.ui_family = pick_font(root, ["Segoe UI", "Helvetica Neue", "Noto Sans", "DejaVu Sans"],
                                   "TkDefaultFont")
        mono_family = pick_font(
            root, ["Cascadia Mono", "Consolas", "JetBrains Mono", "Menlo", "DejaVu Sans Mono", "Courier New"],
            "Courier")
        self.ui = (self.ui_family, 10)
        self.ui_bold = (self.ui_family, 10, "bold")
        self.mono = tkfont.Font(root=root, family=mono_family, size=mono_size)


FONTS: Fonts  # created in App.__init__


def apply_ttk_theme(root: tk.Misc) -> None:
    s = ttk.Style(root)
    try:
        s.theme_use("clam")
    except tk.TclError:
        pass
    s.configure(
        ".", background=T["bg"], foreground=T["fg"], fieldbackground=T["entry_bg"],
        bordercolor=T["border"], lightcolor=T["border"], darkcolor=T["border"], troughcolor=T["panel"],
        focuscolor=T["accent"], insertcolor=T["fg"], font=FONTS.ui,
        selectbackground=T["sel"], selectforeground=T["fg"],
    )
    s.configure("TFrame", background=T["bg"])
    s.configure("Panel.TFrame", background=T["panel"])
    s.configure("TLabel", background=T["bg"], foreground=T["fg"])
    s.configure("Panel.TLabel", background=T["panel"], foreground=T["fg"])
    s.configure("Muted.TLabel", background=T["bg"], foreground=T["muted"])
    s.configure("Title.TLabel", background=T["bg"], foreground=T["fg"], font=(FONTS.ui_family, 20, "bold"))
    s.configure("Status.TLabel", background=T["panel2"], foreground=T["muted"], padding=(10, 4))

    s.configure("TButton", background=T["panel2"], foreground=T["fg"], bordercolor=T["border"],
                padding=(10, 4), relief="flat", focusthickness=0)
    s.map("TButton", background=[("pressed", T["accent"]), ("active", T["hover"]), ("disabled", T["panel"])],
          foreground=[("disabled", T["muted"])])
    s.configure("Accent.TButton", background=T["accent"], foreground=T["accent_fg"], bordercolor=T["accent"],
                padding=(14, 8), font=FONTS.ui_bold)
    s.map("Accent.TButton", background=[("active", T["accent"]), ("pressed", T["accent"])])

    s.configure("Toolbutton", background=T["panel2"], foreground=T["fg"], bordercolor=T["border"],
                padding=(9, 3), relief="flat", focusthickness=0)
    s.map("Toolbutton", background=[("selected", T["accent"]), ("pressed", T["accent"]), ("active", T["hover"])],
          foreground=[("selected", T["accent_fg"])])

    s.configure("TEntry", fieldbackground=T["entry_bg"], foreground=T["fg"], insertcolor=T["fg"],
                bordercolor=T["border"], padding=3)
    s.map("TEntry", bordercolor=[("focus", T["accent"])], lightcolor=[("focus", T["accent"])],
          darkcolor=[("focus", T["accent"])])
    s.configure("TCombobox", fieldbackground=T["entry_bg"], background=T["panel2"], foreground=T["fg"],
                arrowcolor=T["fg"], bordercolor=T["border"], padding=3)
    s.map("TCombobox",
          fieldbackground=[("readonly", T["entry_bg"])], foreground=[("readonly", T["fg"])],
          selectbackground=[("readonly", T["entry_bg"])], selectforeground=[("readonly", T["fg"])],
          bordercolor=[("focus", T["accent"])])
    s.configure("TSpinbox", fieldbackground=T["entry_bg"], background=T["panel2"], foreground=T["fg"],
                arrowcolor=T["fg"], bordercolor=T["border"], padding=2)
    s.map("TSpinbox", bordercolor=[("focus", T["accent"])])

    for name in ("TCheckbutton", "TRadiobutton"):
        s.configure(name, background=T["bg"], foreground=T["fg"], indicatorcolor=T["entry_bg"],
                    bordercolor=T["border"], focusthickness=0)
        s.map(name, background=[("active", T["bg"])],
              indicatorcolor=[("selected", T["accent"]), ("!selected", T["entry_bg"])])
    s.configure("Panel.TCheckbutton", background=T["panel"])
    s.map("Panel.TCheckbutton", background=[("active", T["panel"])],
          indicatorcolor=[("selected", T["accent"]), ("!selected", T["entry_bg"])])

    s.configure("TNotebook", background=T["bg"], borderwidth=0, tabmargins=(4, 4, 4, 0))
    s.configure("TNotebook.Tab", background=T["panel2"], foreground=T["muted"], padding=(14, 6), borderwidth=0)
    s.map("TNotebook.Tab", background=[("selected", T["panel"]), ("active", T["hover"])],
          foreground=[("selected", T["fg"])], expand=[("selected", [0, 0, 0, 0])])

    s.configure("Treeview", background=T["text_bg"], fieldbackground=T["text_bg"], foreground=T["fg"],
                rowheight=24, borderwidth=0, bordercolor=T["border"])
    s.map("Treeview", background=[("selected", T["sel"])], foreground=[("selected", T["fg"])])
    s.configure("Treeview.Heading", background=T["panel2"], foreground=T["fg"], relief="flat", padding=5,
                font=FONTS.ui_bold)
    s.map("Treeview.Heading", background=[("active", T["hover"])])

    for orient in ("Vertical", "Horizontal"):
        s.configure(orient + ".TScrollbar", background=T["panel2"], troughcolor=T["panel"],
                    bordercolor=T["panel"], arrowcolor=T["fg"], lightcolor=T["panel2"],
                    darkcolor=T["panel2"], gripcount=0)
        s.map(orient + ".TScrollbar", background=[("active", T["hover"])])
    s.configure("TSeparator", background=T["border"])
    s.configure("Horizontal.TProgressbar", background=T["accent"], troughcolor=T["panel"],
                bordercolor=T["border"], lightcolor=T["accent"], darkcolor=T["accent"])

    root.option_add("*TCombobox*Listbox.background", T["entry_bg"])
    root.option_add("*TCombobox*Listbox.foreground", T["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", T["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", T["accent_fg"])


def retheme_combobox(cb: ttk.Combobox) -> None:
    """Re-colour the drop-down list of a combobox that was already opened once."""
    try:
        cb.tk.call(str(cb) + ".popdown.f.l", "configure", "-background", T["entry_bg"], "-foreground", T["fg"],
                   "-selectbackground", T["accent"], "-selectforeground", T["accent_fg"])
    except tk.TclError:
        pass


def style_menu(menu: tk.Menu) -> None:
    menu.configure(bg=T["panel"], fg=T["fg"], activebackground=T["accent"], activeforeground=T["accent_fg"],
                   disabledforeground=T["muted"], bd=0, activeborderwidth=0, relief="flat")


def set_titlebar_dark(win: tk.Misc, dark: bool) -> None:
    """Dark title bar on Windows 10/11 (no-op elsewhere)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        windll = getattr(ctypes, "windll")
        win.update_idletasks()
        hwnd = windll.user32.GetParent(win.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):          # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old id)
            if windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                   ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


# ------------------------------------------------------------
#   Config, icon, small helpers
# ------------------------------------------------------------

def config_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".pycompare.json")


def load_config() -> Dict[str, Any]:
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(cfg: Dict[str, Any]) -> None:
    tmp = config_path() + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        os.replace(tmp, config_path())
    except OSError:
        pass        # settings are a convenience; never block the tool


def resource_path(relative: str) -> str:
    """Bundled files (icon) live in PyInstaller's temp folder when frozen."""
    return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), relative)


ICON_NAMES = ("CompareFiles", "pycompare")       # looked up as <name>.ico / <name>.png


def apply_window_icon(root: tk.Tk) -> None:
    """Use CompareFiles.ico (Windows) or CompareFiles.png next to the script; skip if absent."""
    try:
        for name in ICON_NAMES:
            ico = resource_path(name + ".ico")
            if os.name == "nt" and os.path.isfile(ico):
                root.iconbitmap(ico)
                return
            png = resource_path(name + ".png")
            if os.path.isfile(png):
                img = tk.PhotoImage(file=png)
                root.iconphoto(True, img)
                setattr(root, "_icon_ref", img)      # keep a reference against garbage collection
                return
    except Exception:
        pass


def spin_value(var: tk.IntVar, default: int) -> int:
    try:
        return max(0, int(var.get()))
    except (tk.TclError, ValueError):
        return default


def make_spin(parent: tk.Misc, var: tk.IntVar, command: Callable[[], None]) -> ttk.Spinbox:
    sb = ttk.Spinbox(parent, from_=0, to=500, width=4, textvariable=var, command=command)
    sb.bind("<Return>", lambda _e: command())
    sb.bind("<FocusOut>", lambda _e: command())
    return sb


def reveal_in_file_manager(path: str) -> None:
    try:
        if os.name == "nt":
            if os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path] if os.path.isfile(path) else ["open", path])
        else:
            subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
    except OSError as ex:
        messagebox.showerror("Open Location", str(ex))


def fmt_time(ts: Optional[float]) -> str:
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except (OSError, ValueError, OverflowError):
        return ""


def short_path(path: str, width: int = 58) -> str:
    if len(path) <= width:
        return path
    return path[:width // 3 - 1] + "…" + path[-(width - width // 3):]


WS_LABELS = {
    "Compare whitespace": WS_NONE,
    "Ignore leading/trailing": WS_EDGES,
    "Ignore whitespace changes": WS_CHANGES,
    "Ignore all whitespace": WS_ALL,
}
WS_LABEL_BY_MODE = {v: k for k, v in WS_LABELS.items()}

COMPARE_LABELS = {
    "Quick (size + date)": CMP_QUICK,
    "Size only": CMP_SIZE,
    "Content (byte / text)": CMP_CONTENT,
}
COMPARE_LABEL_BY_MODE = {v: k for k, v in COMPARE_LABELS.items()}

STATUS_TEXT = {
    S_SAME: "Identical",
    S_DIFF: "Different",
    S_LEFT_ONLY: "Left only",
    S_RIGHT_ONLY: "Right only",
    S_MISMATCH: "Type mismatch",
}


# ------------------------------------------------------------
#   Running slow work (FTP) without freezing the window
# ------------------------------------------------------------

def run_async(widget: tk.Misc, work: Callable[[], Any], on_done: Callable[[Any], None],
              on_error: Optional[Callable[[Exception], None]] = None, poll_ms: int = 50) -> None:
    """Run `work` on a worker thread; deliver the result on the tkinter thread."""
    q: "queue.Queue[Tuple[str, Any]]" = queue.Queue()

    def runner() -> None:
        try:
            q.put(("ok", work()))
        except Exception as ex:                     # reported to the user, never swallowed
            q.put(("err", ex))

    def poll() -> None:
        try:
            kind, payload = q.get_nowait()
        except queue.Empty:
            if widget.winfo_exists():
                widget.after(poll_ms, poll)
            return
        if not widget.winfo_exists():
            return
        if kind == "ok":
            on_done(payload)
        elif on_error is not None:
            on_error(payload)
        else:
            messagebox.showerror("Error", str(payload))

    threading.Thread(target=runner, daemon=True).start()
    widget.after(poll_ms, poll)


# ------------------------------------------------------------
#   Dialogs
# ------------------------------------------------------------

class ModalDialog(tk.Toplevel):
    """Themed modal dialog base."""

    def __init__(self, parent: tk.Misc, title: str, width: int = 0, height: int = 0) -> None:
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.configure(bg=T["bg"])
        self.transient(parent.winfo_toplevel())
        self.resizable(bool(width), bool(height))
        self.result: Any = None
        self.body = ttk.Frame(self, padding=14)
        self.body.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda _e: self.cancel())
        self._want = (width, height)

    def show(self) -> Any:
        self.update_idletasks()
        w = self._want[0] or self.winfo_reqwidth()
        h = self._want[1] or self.winfo_reqheight()
        parent = self.master
        try:
            x = parent.winfo_rootx() + max(0, (parent.winfo_width() - w) // 2)
            y = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 3)
        except tk.TclError:
            x = y = 120
        self.geometry("%dx%d+%d+%d" % (w, h, max(0, x), max(0, y)))
        self.deiconify()
        set_titlebar_dark(self, bool(T["dark"]))
        self.grab_set()
        self.wait_window(self)
        return self.result

    def cancel(self) -> None:
        self.result = None
        self.destroy()


class PasswordDialog(ModalDialog):
    def __init__(self, parent: tk.Misc, ref: "RemoteRef") -> None:
        super().__init__(parent, "FTP Password")
        ttk.Label(self.body, text="Password for %s" % ref.url("/")).pack(anchor="w")
        self.var = tk.StringVar()
        self.save_var = tk.BooleanVar(value=False)
        entry = ttk.Entry(self.body, textvariable=self.var, show="•", width=38)
        entry.pack(fill="x", pady=(8, 6))
        entry.bind("<Return>", lambda _e: self.ok())
        ttk.Checkbutton(self.body, text="Remember for next time (obfuscated in the settings file, "
                                        "not encrypted)", variable=self.save_var).pack(anchor="w")
        row = ttk.Frame(self.body)
        row.pack(fill="x", pady=(14, 0))
        ttk.Button(row, text="Cancel", command=self.cancel).pack(side="right")
        ttk.Button(row, text="Connect", style="Accent.TButton", command=self.ok).pack(side="right", padx=(0, 8))
        entry.focus_set()

    def ok(self) -> None:
        self.result = (self.var.get(), bool(self.save_var.get()))
        self.destroy()


class FtpProfilesDialog(ModalDialog):
    """Create / edit / delete saved FTP connections."""

    FIELDS = (("name", "Name"), ("host", "Host"), ("port", "Port"), ("user", "User"),
              ("password", "Password"), ("start_dir", "Start folder"), ("encoding", "Encoding"),
              ("time_offset_hours", "Server time offset (hours)"))

    def __init__(self, parent: tk.Misc, app: "App") -> None:
        super().__init__(parent, "FTP Connections", 760, 470)
        self.app = app
        self.profiles: List[FtpProfile] = [replace(p) for p in app.profiles]
        self.current = -1
        self.vars: Dict[str, tk.StringVar] = {}

        outer = self.body
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        left = ttk.Frame(outer)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(left, columns=("host",), show="tree", height=12)
        self.tree.column("#0", width=190, stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._select())
        btns = ttk.Frame(left)
        btns.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(btns, text="New", command=self._new).pack(side="left")
        ttk.Button(btns, text="Delete", command=self._delete).pack(side="left", padx=(6, 0))

        form = ttk.Frame(outer)
        form.grid(row=0, column=1, sticky="nsew")
        form.columnconfigure(1, weight=1)
        row = 0
        self.scheme_var = tk.StringVar(value="ftp")
        ttk.Label(form, text="Protocol").grid(row=row, column=0, sticky="w", pady=3)
        prow = ttk.Frame(form)
        prow.grid(row=row, column=1, sticky="w", pady=3)
        ttk.Radiobutton(prow, text="FTP", value="ftp", variable=self.scheme_var,
                        command=self._scheme_changed).pack(side="left")
        ttk.Radiobutton(prow, text="FTPS (explicit TLS)", value="ftps", variable=self.scheme_var,
                        command=self._scheme_changed).pack(side="left", padx=(10, 0))
        row += 1
        for key, label in self.FIELDS:
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 10))
            var = tk.StringVar()
            self.vars[key] = var
            entry = ttk.Entry(form, textvariable=var, show="•" if key == "password" else "")
            entry.grid(row=row, column=1, sticky="ew", pady=3)
            row += 1
        self.save_pw = tk.BooleanVar(value=False)
        self.passive = tk.BooleanVar(value=True)
        self.verify = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="Save password (obfuscated in the settings file, not encrypted)",
                        variable=self.save_pw).grid(row=row, column=1, sticky="w", pady=(6, 0))
        row += 1
        ttk.Checkbutton(form, text="Passive mode (recommended)", variable=self.passive).grid(
            row=row, column=1, sticky="w")
        row += 1
        self.verify_cb = ttk.Checkbutton(form, text="FTPS: verify the server certificate",
                                         variable=self.verify)
        self.verify_cb.grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(form, text="SFTP (SSH) is not supported - it needs the third-party paramiko package.",
                  style="Muted.TLabel").grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 0))

        bar = ttk.Frame(outer)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.status = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.status, style="Muted.TLabel").pack(side="left")
        ttk.Button(bar, text="Close", command=self.cancel).pack(side="right")
        ttk.Button(bar, text="Save", style="Accent.TButton", command=self._apply).pack(side="right", padx=(0, 8))
        ttk.Button(bar, text="Test connection", command=self._test).pack(side="right", padx=(0, 8))

        self._refresh()
        if self.profiles:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
        else:
            self._new()
        self._scheme_changed()

    # ---- helpers
    def _refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, p in enumerate(self.profiles):
            self.tree.insert("", "end", iid=str(i), text="  %s" % (p.name or p.host or "(unnamed)"))

    def _scheme_changed(self) -> None:
        state = "!disabled" if self.scheme_var.get() == "ftps" else "disabled"
        self.verify_cb.state([state])

    def _load(self, p: FtpProfile) -> None:
        self.scheme_var.set(p.scheme)
        for key, _label in self.FIELDS:
            self.vars[key].set(str(getattr(p, key, "")))
        self.save_pw.set(p.save_password)
        self.passive.set(p.passive)
        self.verify.set(p.tls_verify)
        self._scheme_changed()

    def _collect(self) -> FtpProfile:
        p = FtpProfile()
        p.scheme = self.scheme_var.get()
        p.name = self.vars["name"].get().strip()
        p.host = self.vars["host"].get().strip()
        try:
            p.port = int(self.vars["port"].get().strip() or default_port(p.scheme))
        except ValueError:
            p.port = default_port(p.scheme)
        p.user = self.vars["user"].get().strip() or "anonymous"
        p.password = self.vars["password"].get()
        p.start_dir = self.vars["start_dir"].get().strip() or "/"
        p.encoding = self.vars["encoding"].get().strip() or "utf-8"
        try:
            p.time_offset_hours = float(self.vars["time_offset_hours"].get().strip() or 0)
        except ValueError:
            p.time_offset_hours = 0.0
        p.save_password = bool(self.save_pw.get())
        p.passive = bool(self.passive.get())
        p.tls_verify = bool(self.verify.get())
        if not p.name:
            p.name = "%s@%s" % (p.user, p.host) if p.host else "New connection"
        return p

    def _select(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        self.current = int(sel[0])
        self._load(self.profiles[self.current])

    def _new(self) -> None:
        self.current = -1
        self.tree.selection_remove(*self.tree.selection())
        self._load(FtpProfile(name="", host="", port=21, user="anonymous", start_dir="/"))
        self.status.set("Enter the details and press Save.")

    def _delete(self) -> None:
        if self.current < 0 or self.current >= len(self.profiles):
            return
        name = self.profiles[self.current].name
        if not messagebox.askyesno("Delete Connection", "Delete the connection %r?" % name, parent=self):
            return
        del self.profiles[self.current]
        self.current = -1
        self._refresh()
        self._new()
        self._commit()

    def _apply(self) -> None:
        p = self._collect()
        if not p.host:
            messagebox.showwarning("Host Required", "Enter the FTP server host name or address.", parent=self)
            return
        if 0 <= self.current < len(self.profiles):
            self.profiles[self.current] = p
        else:
            self.profiles.append(p)
            self.current = len(self.profiles) - 1
        self._refresh()
        self.tree.selection_set(str(self.current))
        self._commit()
        self.status.set("Saved %s." % p.name)

    def _commit(self) -> None:
        self.app.set_profiles(self.profiles)
        self.result = True

    def _test(self) -> None:
        p = self._collect()
        if not p.host:
            messagebox.showwarning("Host Required", "Enter the FTP server host name or address.", parent=self)
            return
        self.status.set("Connecting to %s…" % p.host)

        def work() -> str:
            fs = FtpFS(p)
            try:
                fs.connect()
                entries = fs.listdir(p.start_dir or "/")
                return "Connected. %s contains %d item(s)." % (p.start_dir or "/", len(entries))
            finally:
                fs.close()

        run_async(self, work, lambda msg: self.status.set(msg),
                  lambda ex: self.status.set("Failed: %s" % ex))


class RemoteBrowserDialog(ModalDialog):
    """Browse a remote server and pick a folder (or a file)."""

    def __init__(self, parent: tk.Misc, app: "App", profile: FtpProfile, pick_file: bool = False) -> None:
        super().__init__(parent, "Browse %s" % (profile.name or profile.host), 720, 480)
        self.app = app
        self.profile = profile
        self.pick_file = pick_file
        self.fs = FtpFS(profile)
        self.path = profile.start_dir or "/"
        self.path_var = tk.StringVar(value=self.path)
        self.status = tk.StringVar(value="Connecting…")

        top = ttk.Frame(self.body)
        top.pack(fill="x")
        ttk.Button(top, text="↑ Up", command=self.go_up).pack(side="left")
        entry = ttk.Entry(top, textvariable=self.path_var)
        entry.pack(side="left", fill="x", expand=True, padx=8)
        entry.bind("<Return>", lambda _e: self.go(self.path_var.get()))
        ttk.Button(top, text="Go", command=lambda: self.go(self.path_var.get())).pack(side="left")

        mid = ttk.Frame(self.body)
        mid.pack(fill="both", expand=True, pady=10)
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(mid, columns=("size", "modified"), show="tree headings")
        self.tree.heading("#0", text="Name", anchor="w")
        self.tree.heading("size", text="Size", anchor="w")
        self.tree.heading("modified", text="Modified", anchor="w")
        self.tree.column("#0", width=360, stretch=True)
        self.tree.column("size", width=90, anchor="e", stretch=False)
        self.tree.column("modified", width=160, stretch=False)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", lambda _e: self._enter())
        self.tree.bind("<Return>", lambda _e: self._enter())

        bar = ttk.Frame(self.body)
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.status, style="Muted.TLabel").pack(side="left")
        ttk.Button(bar, text="Cancel", command=self.cancel).pack(side="right")
        ttk.Button(bar, text="Select file" if pick_file else "Use this folder",
                   style="Accent.TButton", command=self.choose).pack(side="right", padx=(0, 8))
        self._nodes: Dict[str, Node] = {}
        self.after(30, lambda: self.go(self.path))

    def go(self, path: str) -> None:
        path = (path or "/").strip() or "/"
        self.status.set("Loading %s…" % path)
        self.tree.delete(*self.tree.get_children())
        self._nodes.clear()

        def work() -> List[Node]:
            return self.fs.listdir(path)

        def done(nodes: List[Node]) -> None:
            self.path = path
            self.path_var.set(path)
            for node in sorted(nodes, key=lambda n: (not n.is_dir, n.name.lower())):
                if not node.is_dir and not self.pick_file:
                    continue
                iid = self.tree.insert("", "end", text=("📁 " if node.is_dir else "📄 ") + node.name,
                                       values=("" if node.is_dir else format_size(node.size),
                                               fmt_time(node.mtime)))
                self._nodes[iid] = node
            self.status.set("%d item(s)" % len(self._nodes))

        run_async(self, work, done, lambda ex: self.status.set("Failed: %s" % ex))

    def go_up(self) -> None:
        parent = posixpath.dirname(self.path.rstrip("/")) or "/"
        self.go(parent)

    def _enter(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        node = self._nodes.get(sel[0])
        if node and node.is_dir:
            self.go(node.path)
        elif node and self.pick_file:
            self.choose()

    def choose(self) -> None:
        sel = self.tree.selection()
        node = self._nodes.get(sel[0]) if sel else None
        if self.pick_file:
            if not node or node.is_dir:
                messagebox.showinfo("Select a file", "Pick a file from the list.", parent=self)
                return
            self.result = self.profile.ref(node.path).url()
        else:
            target = node.path if (node and node.is_dir) else self.path
            self.result = self.profile.ref(target).url()
        self.destroy()

    def cancel(self) -> None:
        try:
            self.fs.close()
        except Exception:
            pass
        super().cancel()

    def destroy(self) -> None:
        try:
            self.fs.close()
        except Exception:
            pass
        super().destroy()


# ------------------------------------------------------------
#   Where a path lives: a local folder or a remote server
# ------------------------------------------------------------

@dataclass
class FsTarget:
    """A path plus everything needed to open a FileSystem for it on a worker thread."""
    path: str                                   # exactly what the user typed (local path or URL)
    root: str                                   # the path inside that filesystem
    profile: Optional[FtpProfile] = None        # set for remote targets

    @property
    def is_remote(self) -> bool:
        return self.profile is not None

    def open(self) -> FileSystem:
        if self.profile is None:
            return LocalFS()
        return FtpFS(self.profile)

    def url_for(self, path: str) -> str:
        return self.profile.ref(path).url() if self.profile else path

    def display(self) -> str:
        return self.path


EMPTY_TARGET = FsTarget("", "")


# ============================================================
#               FOLDER COMPARE TAB
# ============================================================

class FolderTab(ttk.Frame):
    kind = "folder"

    def __init__(self, app: "App", left: str = "", right: str = "") -> None:
        super().__init__(app.nb)
        self.app = app
        self.result: Optional[FolderResult] = None
        self._items: Dict[str, FolderEntry] = {}
        self._cancel = threading.Event()
        self._scan_id = 0
        self.ltarget = EMPTY_TARGET
        self.rtarget = EMPTY_TARGET
        self.keymap: Dict[str, Callable[[], Any]] = {"<F5>": self.compare}

        fo = app.cfg.get("folder_opts", {})
        self.lvar = tk.StringVar(value=left)
        self.rvar = tk.StringVar(value=right)
        self.flt = tk.StringVar(value=FILTER_ALL)
        self.cmp_var = tk.StringVar(value=COMPARE_LABEL_BY_MODE.get(fo.get("compare", CMP_QUICK),
                                                                    "Quick (size + date)"))
        self.ignore_var = tk.StringVar(value=fo.get("ignore", "; ".join(DEFAULT_IGNORES)))
        self.ws_var = tk.StringVar(value=WS_LABEL_BY_MODE.get(fo.get("ws", WS_NONE), "Compare whitespace"))
        self.case_var = tk.BooleanVar(value=bool(fo.get("case", False)))
        self.status_var = tk.StringVar(value="Choose two folders and press Compare (F5).")

        self._build()
        self.apply_theme()
        if left or right:
            self.after(50, self.compare)

    # ---------- UI ----------
    def _build(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", padx=12, pady=(12, 6))
        top.columnconfigure(1, weight=1)

        hist = self.app.history("folder")
        ttk.Label(top, text="Left").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=3)
        self.lcombo = ttk.Combobox(top, textvariable=self.lvar, values=hist)
        self.lcombo.grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Button(top, text="Browse…", command=lambda: self._browse(self.lvar)).grid(
            row=0, column=2, padx=(8, 0), pady=3)
        ttk.Button(top, text="Remote ▾", command=lambda: self._remote_menu(self.lvar)).grid(
            row=0, column=3, padx=(6, 0), pady=3)
        ttk.Button(top, text="⇄ Swap", command=self.swap).grid(row=0, column=4, padx=(8, 0), pady=3, sticky="ew")

        ttk.Label(top, text="Right").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=3)
        self.rcombo = ttk.Combobox(top, textvariable=self.rvar, values=hist)
        self.rcombo.grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Button(top, text="Browse…", command=lambda: self._browse(self.rvar)).grid(
            row=1, column=2, padx=(8, 0), pady=3)
        ttk.Button(top, text="Remote ▾", command=lambda: self._remote_menu(self.rvar)).grid(
            row=1, column=3, padx=(6, 0), pady=3)
        self.compare_btn = ttk.Button(top, text="Compare  (F5)", style="Accent.TButton", command=self.compare)
        self.compare_btn.grid(row=1, column=4, padx=(8, 0), pady=3, sticky="ew")
        for cb in (self.lcombo, self.rcombo):
            cb.bind("<Return>", lambda _e: self.compare())

        opts = ttk.Frame(self)
        opts.pack(fill="x", padx=12, pady=(2, 4))
        ttk.Label(opts, text="Compare by").pack(side="left", padx=(0, 6))
        self.cmp_combo = ttk.Combobox(opts, textvariable=self.cmp_var, values=list(COMPARE_LABELS),
                                      state="readonly", width=20)
        self.cmp_combo.pack(side="left", padx=(0, 14))
        ttk.Label(opts, text="Ignore").pack(side="left", padx=(0, 6))
        ign = ttk.Entry(opts, textvariable=self.ignore_var, width=32)
        ign.pack(side="left", padx=(0, 14))
        ign.bind("<Return>", lambda _e: self.compare())
        ttk.Label(opts, text="Text (content mode):").pack(side="left", padx=(0, 6))
        self.ws_combo = ttk.Combobox(opts, textvariable=self.ws_var, values=list(WS_LABELS),
                                     state="readonly", width=24)
        self.ws_combo.pack(side="left", padx=(0, 8))
        ttk.Checkbutton(opts, text="Ignore case", variable=self.case_var).pack(side="left")

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=12, pady=(4, 6))
        for text, value in (("All", FILTER_ALL), ("Differences", FILTER_DIFFS), ("Same", FILTER_SAME),
                            ("Left only", FILTER_LEFT), ("Right only", FILTER_RIGHT)):
            ttk.Radiobutton(bar, text=text, value=value, variable=self.flt, style="Toolbutton",
                            command=self.populate).pack(side="left", padx=(0, 3))
        ttk.Button(bar, text="Expand all", command=lambda: self._set_open(True)).pack(side="left", padx=(14, 3))
        ttk.Button(bar, text="Collapse all", command=lambda: self._set_open(False)).pack(side="left", padx=3)
        ttk.Label(bar, text="Enter / double-click: compare file     Ctrl+→ / Ctrl+←: copy to right / left",
                  style="Muted.TLabel").pack(side="right")

        ttk.Label(self, textvariable=self.status_var, style="Status.TLabel", anchor="w").pack(
            side="bottom", fill="x")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        cols = ("status", "lsize", "lmod", "rsize", "rmod")
        self.tree = ttk.Treeview(body, columns=cols, selectmode="extended")
        self.tree.heading("#0", text="Name", anchor="w")
        for col, text, width, anchor in (("status", "Status", 130, "w"), ("lsize", "Left size", 85, "e"),
                                         ("lmod", "Left modified", 150, "w"), ("rsize", "Right size", 85, "e"),
                                         ("rmod", "Right modified", 150, "w")):
            self.tree.heading(col, text=text, anchor="w")
            self.tree.column(col, width=width, anchor=anchor, stretch=False)  # type: ignore[arg-type]
        self.tree.column("#0", width=380, stretch=True)
        vbar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        hbar = ttk.Scrollbar(body, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")

        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Return>", lambda _e: self.open_selected())
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Control-a>", self._select_all)
        self.keymap["<Control-Right>"] = lambda: self.copy_selected("left")
        self.keymap["<Control-Left>"] = lambda: self.copy_selected("right")

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Compare files", command=self.open_selected)
        self.menu.add_separator()
        self.menu.add_command(label="Copy to right  →", command=lambda: self.copy_selected("left"))
        self.menu.add_command(label="Copy to left  ←", command=lambda: self.copy_selected("right"))
        self.menu.add_separator()
        self.menu.add_command(label="Delete left", command=lambda: self.delete_selected("left"))
        self.menu.add_command(label="Delete right", command=lambda: self.delete_selected("right"))
        self.menu.add_separator()
        self.menu.add_command(label="Show left in file manager", command=lambda: self.reveal("left"))
        self.menu.add_command(label="Show right in file manager", command=lambda: self.reveal("right"))
        self.menu.add_command(label="Copy relative path", command=self.copy_rel_path)
        self.remote_menu = tk.Menu(self, tearoff=0)

    def apply_theme(self) -> None:
        for status, key in ((S_SAME, "s_same"), (S_DIFF, "s_diff"), (S_LEFT_ONLY, "s_left"),
                            (S_RIGHT_ONLY, "s_right"), (S_MISMATCH, "s_mismatch")):
            self.tree.tag_configure(status, foreground=T[key])
        style_menu(self.menu)
        style_menu(self.remote_menu)
        for cb in (self.lcombo, self.rcombo, self.cmp_combo, self.ws_combo):
            retheme_combobox(cb)

    # ---------- choosing folders ----------
    def _browse(self, var: tk.StringVar) -> None:
        start = var.get() if os.path.isdir(var.get()) else None
        folder = filedialog.askdirectory(title="Select folder", initialdir=start)
        if folder:
            var.set(os.path.normpath(folder))

    def _remote_menu(self, var: tk.StringVar) -> None:
        self.app.popup_remote_menu(self, self.remote_menu, var, pick_file=False)

    def swap(self) -> None:
        left, right = self.lvar.get(), self.rvar.get()
        self.lvar.set(right)
        self.rvar.set(left)
        if self.result:
            self.compare()

    def folder_options(self) -> FolderOptions:
        patterns = tuple(p.strip() for p in self.ignore_var.get().replace(",", ";").split(";") if p.strip())
        return FolderOptions(
            compare=COMPARE_LABELS.get(self.cmp_var.get(), CMP_QUICK),
            ignore_patterns=patterns,
            text_opts=DiffOptions(WS_LABELS.get(self.ws_var.get(), WS_NONE), bool(self.case_var.get())),
        )

    # ---------- comparing ----------
    def compare(self) -> None:
        left, right = self.lvar.get().strip(), self.rvar.get().strip()
        if not left and not right:
            messagebox.showwarning("Folders Required", "Choose a left and a right folder first.")
            return
        try:
            ltarget = self.app.target(left, self)
            rtarget = self.app.target(right, self)
        except RemoteError as ex:
            messagebox.showerror("Remote Path", str(ex))
            return
        if ltarget is None or rtarget is None:
            return                                   # the password prompt was cancelled
        for label, target in (("Left", ltarget), ("Right", rtarget)):
            if target.path and not target.is_remote and not os.path.isdir(target.root):
                messagebox.showerror("Folder Not Found", "%s folder does not exist:\n\n%s"
                                     % (label, target.path))
                return
        self.ltarget, self.rtarget = ltarget, rtarget

        opts = self.folder_options()
        self.app.cfg["folder_opts"] = {
            "compare": opts.compare, "ignore": self.ignore_var.get(),
            "ws": opts.text_opts.ignore_whitespace, "case": opts.text_opts.ignore_case,
        }
        for path in (left, right):
            if path:
                self.app.remember_path("folder", path)
        self.app.add_recent("folder", left, right)
        self.lcombo.configure(values=self.app.history("folder"))
        self.rcombo.configure(values=self.app.history("folder"))
        self.app.update_tab_title(self)

        self._cancel.set()                      # stop any scan still running
        self._cancel = threading.Event()
        cancel = self._cancel
        self._scan_id += 1
        scan_id = self._scan_id
        q: "queue.Queue[tuple]" = queue.Queue()
        self.compare_btn.state(["disabled"])
        self.status_var.set("Connecting…" if (ltarget.is_remote or rtarget.is_remote) else "Comparing…")
        t0 = time.time()

        def worker() -> None:
            lfs = rfs = None
            try:
                lfs = ltarget.open()
                rfs = rtarget.open()
                res = compare_folders(ltarget.root, rtarget.root, opts, lfs, rfs,
                                      progress=lambda n: q.put(("progress", n)),
                                      is_cancelled=cancel.is_set)
                q.put(("done", res))
            except Cancelled:
                q.put(("cancelled", None))
            except Exception as ex:
                q.put(("error", str(ex)))
            finally:
                for fs in (lfs, rfs):
                    if fs is not None:
                        try:
                            fs.close()
                        except Exception:
                            pass

        threading.Thread(target=worker, daemon=True).start()
        self.after(60, self._poll, q, scan_id, t0)

    def _poll(self, q: "queue.Queue[tuple]", scan_id: int, t0: float) -> None:
        if not self.winfo_exists() or scan_id != self._scan_id:
            return
        finished = False
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "progress":
                    self.status_var.set("Comparing… %s items scanned" % f"{payload:,}")
                elif kind == "done":
                    self.result = payload
                    self.populate()
                    c = payload.counts
                    total = sum(c.values())
                    self.status_var.set(
                        "%s files   |   %s identical   %s different   %s left only   %s right only"
                        "   |   %.1fs" % (f"{total:,}", f"{c['same']:,}", f"{c['diff'] + c['mismatch']:,}",
                                          f"{c['left_only']:,}", f"{c['right_only']:,}", time.time() - t0))
                    finished = True
                elif kind == "error":
                    messagebox.showerror("Compare Failed", str(payload))
                    self.status_var.set("Compare failed: %s" % payload)
                    finished = True
                elif kind == "cancelled":
                    finished = True
        except queue.Empty:
            pass
        if finished:
            self.compare_btn.state(["!disabled"])
        else:
            self.after(60, self._poll, q, scan_id, t0)

    def populate(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._items.clear()
        if not self.result:
            return
        flt = self.flt.get()

        def insert(parent: str, entries: List[FolderEntry]) -> None:
            for e in entries:
                if not entry_matches(e, flt):
                    continue
                status = STATUS_TEXT.get(e.status, e.status)
                if e.status == S_DIFF and e.newer and not e.is_dir:
                    status += " (%s newer)" % e.newer
                icon = "📁 " if e.is_dir else "📄 "
                iid = self.tree.insert(
                    parent, "end", text=icon + e.name, tags=(e.status,),
                    open=bool(e.is_dir and (flt != FILTER_ALL or e.status != S_SAME)),
                    values=(status, format_size(e.lsize), fmt_time(e.lmtime if not e.is_dir else None),
                            format_size(e.rsize), fmt_time(e.rmtime if not e.is_dir else None)),
                )
                self._items[iid] = e
                if e.children:
                    insert(iid, e.children)

        insert("", self.result.root.children)

    def _set_open(self, value: bool) -> None:
        def walk(iid: str) -> None:
            for child in self.tree.get_children(iid):
                self.tree.item(child, open=value)
                walk(child)
        walk("")

    def _select_all(self, _e: Any = None) -> str:
        def collect(iid: str) -> List[str]:
            out: List[str] = []
            for child in self.tree.get_children(iid):
                out.append(child)
                if self.tree.item(child, "open"):
                    out.extend(collect(child))
            return out
        self.tree.selection_set(collect(""))
        return "break"

    # ---------- selection helpers ----------
    def _selected(self) -> List[FolderEntry]:
        return [self._items[i] for i in self.tree.selection() if i in self._items]

    def _selected_top(self) -> List[FolderEntry]:
        """Selected entries, dropping any whose parent folder is also selected."""
        ents = self._selected()
        folders = {e.parts for e in ents if e.is_dir}
        return [e for e in ents if not any(e.parts[:n] in folders for n in range(1, len(e.parts)))]

    def _on_right_click(self, event: Any) -> None:
        iid = self.tree.identify_row(event.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        if not self.tree.selection():
            return
        self.menu.tk_popup(event.x_root, event.y_root)

    def _on_double_click(self, event: Any) -> Optional[str]:
        if self.tree.identify_region(event.x, event.y) == "heading":
            return None
        iid = self.tree.identify_row(event.y)
        e = self._items.get(iid)
        if e and not e.is_dir:
            self.open_selected()
            return "break"
        return None

    def open_selected(self) -> None:
        ents = [e for e in self._selected() if not e.is_dir]
        if not ents:
            return
        e = ents[0]
        lt, rt = self.ltarget, self.rtarget
        lp = e.lpath or (LocalFS().join(lt.root, *e.parts) if (lt.path and not lt.is_remote)
                         else (posixpath.join(lt.root, *e.parts) if lt.path else ""))
        rp = e.rpath or (LocalFS().join(rt.root, *e.parts) if (rt.path and not rt.is_remote)
                         else (posixpath.join(rt.root, *e.parts) if rt.path else ""))
        self.app.new_file_compare(lt.url_for(lp) if lp else "", rt.url_for(rp) if rp else "")

    # ---------- file operations ----------
    def _run_fs_op(self, title: str, work: Callable[[FileSystem, FileSystem], List[str]]) -> None:
        lt, rt = self.ltarget, self.rtarget
        self.status_var.set(title + "…")

        def job() -> List[str]:
            lfs = rfs = None
            try:
                lfs = lt.open()
                rfs = rt.open()
                return work(lfs, rfs)
            finally:
                for fs in (lfs, rfs):
                    if fs is not None:
                        try:
                            fs.close()
                        except Exception:
                            pass

        def done(errors: List[str]) -> None:
            if errors:
                messagebox.showerror(title + " Failed", "\n".join(errors[:10]))
            self.compare()

        def failed(ex: Exception) -> None:
            messagebox.showerror(title + " Failed", str(ex))
            self.status_var.set("%s failed: %s" % (title, ex))

        run_async(self, job, done, failed)

    def copy_selected(self, from_side: str) -> None:
        ents = [e for e in self._selected_top() if (e.lpath if from_side == "left" else e.rpath)]
        if not ents:
            return
        to_side = "right" if from_side == "left" else "left"
        lt, rt = self.ltarget, self.rtarget
        if not (rt.path if from_side == "left" else lt.path):
            messagebox.showwarning("Copy", "The %s folder is not set." % to_side)
            return
        overwrite = sum(1 for e in ents if (e.rpath if from_side == "left" else e.lpath))
        msg = "Copy %d item(s) from %s to %s?" % (len(ents), from_side, to_side)
        if overwrite:
            msg += "\n\n%d existing item(s) will be overwritten." % overwrite
        if not messagebox.askyesno("Copy", msg):
            return

        def work(lfs: FileSystem, rfs: FileSystem) -> List[str]:
            errors: List[str] = []
            for e in ents:
                try:
                    copy_entry(e, from_side, lt.root, rt.root, lfs, rfs)
                except Exception as ex:
                    errors.append("%s: %s" % (e.rel, ex))
            return errors

        self._run_fs_op("Copy", work)

    def delete_selected(self, side: str) -> None:
        ents = [e for e in self._selected_top() if (e.lpath if side == "left" else e.rpath)]
        if not ents:
            return
        names = "\n".join("  " + e.rel for e in ents[:8]) + ("\n  …" if len(ents) > 8 else "")
        if not messagebox.askyesno(
                "Delete", "PERMANENTLY delete %d item(s) from the %s side?\n\n%s\n\nThis cannot be undone."
                % (len(ents), side, names), icon="warning"):
            return

        def work(lfs: FileSystem, rfs: FileSystem) -> List[str]:
            errors: List[str] = []
            for e in ents:
                try:
                    delete_entry(e, side, lfs, rfs)
                except Exception as ex:
                    errors.append("%s: %s" % (e.rel, ex))
            return errors

        self._run_fs_op("Delete", work)

    def reveal(self, side: str) -> None:
        target = self.ltarget if side == "left" else self.rtarget
        for e in self._selected()[:1]:
            path = e.lpath if side == "left" else e.rpath
            if not path:
                continue
            if target.is_remote:
                messagebox.showinfo("Remote Item", target.url_for(path))
            else:
                reveal_in_file_manager(path)

    def copy_rel_path(self) -> None:
        rels = [e.rel for e in self._selected()]
        if rels:
            self.clipboard_clear()
            self.clipboard_append("\n".join(rels))

    def on_close(self) -> bool:
        self._cancel.set()
        return True

    def title(self) -> str:
        names = []
        for path in (self.lvar.get().strip(), self.rvar.get().strip()):
            if not path:
                continue
            ref = parse_remote(path) if is_remote_path(path) else None
            names.append((posixpath.basename(ref.path.rstrip("/")) or ref.host) if ref
                         else (os.path.basename(path.rstrip("/\\")) or path))
        return "📁 " + (" ↔ ".join(names) if names else "Folder compare")


# ============================================================
#               FILE COMPARE TAB
# ============================================================

class FileTab(ttk.Frame):
    """Side-by-side file compare. Both panes are always editable: type in them
    directly, or copy whole change blocks across with Alt+→ / Alt+←."""

    kind = "file"

    def __init__(self, app: "App", left: str = "", right: str = "") -> None:
        super().__init__(app.nb)
        self.app = app
        fo = app.cfg.get("file_opts", {})

        self.lpath = tk.StringVar(value=left)
        self.rpath = tk.StringVar(value=right)
        self.linfo = tk.StringVar()
        self.rinfo = tk.StringVar()
        self.status_var = tk.StringVar(value="")
        self.mode_var = tk.StringVar(value=fo.get("mode", "all"))
        self.ctx_var = tk.BooleanVar(value=bool(fo.get("context", False)))
        self.before = tk.IntVar(value=int(fo.get("before", 5)))
        self.after_ = tk.IntVar(value=int(fo.get("after", 5)))
        self.cbefore = tk.IntVar(value=int(fo.get("cbefore", 2)))
        self.cafter = tk.IntVar(value=int(fo.get("cafter", 2)))
        self.prefix_var = tk.StringVar(value=fo.get("prefixes", ",".join(DEFAULT_COMMENT_PREFIXES)))
        self.ws_var = tk.StringVar(value=WS_LABEL_BY_MODE.get(fo.get("ws", WS_NONE), "Compare whitespace"))
        self.case_var = tk.BooleanVar(value=bool(fo.get("case", False)))
        self.find_var = tk.StringVar()
        self.leol = tk.StringVar(value="Auto")
        self.reol = tk.StringVar(value="Auto")

        self.ltarget = EMPTY_TARGET
        self.rtarget = EMPTY_TARGET
        self.lf = TextFile(exists=False)
        self.rf = TextFile(exists=False)
        self.left_lines: List[str] = []
        self.right_lines: List[str] = []
        self.dirty = {"left": False, "right": False}
        self.undo_stack: List[tuple] = []
        self.rows: List[Row] = []
        self.hunks: List[Hunk] = []
        self.display: List[int] = []
        self.runs: List[Run] = []
        self.nav_index = -1
        self.binary = False
        self.loading = False
        self._ysync = False
        self._xsync = False
        self._flash_job: Optional[str] = None
        self._matches: List[tuple] = []
        self._match_pos = -1
        self._find_query = ""
        self._edit_job: Optional[str] = None
        self._edit_sides: set = set()
        self._burst_snapshot = False
        self._painting = False
        self._wpad = 1
        self.active_side = "left"           # the pane Find / F3 searches: the one last focused
        self.find_label = tk.StringVar(value="Find in left  (F3)")

        self.keymap: Dict[str, Callable[[], Any]] = {
            "<F5>": self.reload, "<F7>": self.prev_change, "<F8>": self.next_change,
            "<Control-s>": self.save, "<Control-z>": self.undo, "<Control-f>": self.focus_find,
            "<F3>": lambda: self.find_next(1), "<Shift-F3>": lambda: self.find_next(-1),
            "<Alt-Right>": lambda: self.copy_block("ltr"),
            "<Alt-Left>": lambda: self.copy_block("rtl"),
        }

        self._build()
        self.apply_theme()
        self.load()

    # ---------- UI ----------
    def _build(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=(10, 0))
        for text, value in (("All", "all"), ("Diffs", "diffs")):
            ttk.Radiobutton(bar, text=text, value=value, variable=self.mode_var, style="Toolbutton",
                            command=self._on_opts_change).pack(side="left", padx=(0, 2))
        ttk.Checkbutton(bar, text="+ Context", variable=self.ctx_var, style="Toolbutton",
                        command=self._on_ctx_toggle).pack(side="left", padx=(0, 10))
        ttk.Button(bar, text="◀ Prev  (F7)", command=self.prev_change).pack(side="left", padx=(0, 3))
        ttk.Button(bar, text="Next ▶  (F8)", command=self.next_change).pack(side="left", padx=(0, 12))
        self.btn_rtl = ttk.Button(bar, text="← Copy block  (Alt+←)", command=lambda: self.copy_block("rtl"))
        self.btn_rtl.pack(side="left", padx=(0, 3))
        self.btn_ltr = ttk.Button(bar, text="Copy block →  (Alt+→)", command=lambda: self.copy_block("ltr"))
        self.btn_ltr.pack(side="left", padx=(0, 3))
        self.btn_undo = ttk.Button(bar, text="Undo", command=self.undo)
        self.btn_undo.pack(side="left", padx=(0, 12))
        self.btn_save = ttk.Button(bar, text="Save  (Ctrl+S)", command=self.save)
        self.btn_save.pack(side="left", padx=(0, 3))
        ttk.Button(bar, text="⇄ Swap", command=self.swap).pack(side="left", padx=(0, 3))
        ttk.Button(bar, text="Reload  (F5)", command=self.reload).pack(side="left")

        find = ttk.Frame(bar)
        find.pack(side="right")
        ttk.Label(find, textvariable=self.find_label).pack(side="left", padx=(0, 4))
        self.find_entry = ttk.Entry(find, textvariable=self.find_var, width=20)
        self.find_entry.pack(side="left")
        self.find_entry.bind("<Return>", lambda _e: self.find_next(1))
        self.find_entry.bind("<Shift-Return>", lambda _e: self.find_next(-1))
        ttk.Button(find, text="▲", width=3, command=lambda: self.find_next(-1)).pack(side="left", padx=(3, 0))
        ttk.Button(find, text="▼", width=3, command=lambda: self.find_next(1)).pack(side="left", padx=(2, 0))

        opt = ttk.Frame(self)
        opt.pack(fill="x", padx=10, pady=(6, 0))
        self.ws_combo = ttk.Combobox(opt, textvariable=self.ws_var, values=list(WS_LABELS),
                                     state="readonly", width=24)
        self.ws_combo.pack(side="left", padx=(0, 8))
        self.ws_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_opts_change())
        ttk.Checkbutton(opt, text="Ignore case", variable=self.case_var,
                        command=self._on_opts_change).pack(side="left", padx=(0, 14))
        ttk.Label(opt, text="Context lines:").pack(side="left", padx=(0, 6))
        ttk.Label(opt, text="Before").pack(side="left", padx=(0, 3))
        make_spin(opt, self.before, self._on_opts_change).pack(side="left", padx=(0, 8))
        ttk.Label(opt, text="After").pack(side="left", padx=(0, 3))
        make_spin(opt, self.after_, self._on_opts_change).pack(side="left", padx=(0, 14))
        ttk.Label(opt, text="Comment-only changes:").pack(side="left", padx=(0, 6))
        ttk.Label(opt, text="Before").pack(side="left", padx=(0, 3))
        make_spin(opt, self.cbefore, self._on_opts_change).pack(side="left", padx=(0, 8))
        ttk.Label(opt, text="After").pack(side="left", padx=(0, 3))
        make_spin(opt, self.cafter, self._on_opts_change).pack(side="left", padx=(0, 10))
        ttk.Label(opt, text="Comment prefixes").pack(side="left", padx=(0, 3))
        pe = ttk.Entry(opt, textvariable=self.prefix_var, width=9)
        pe.pack(side="left")
        pe.bind("<Return>", lambda _e: self._on_opts_change())
        pe.bind("<FocusOut>", lambda _e: self._on_opts_change())

        ttk.Label(self, textvariable=self.status_var, style="Status.TLabel", anchor="w").pack(
            side="bottom", fill="x")

        panes = ttk.Frame(self)
        panes.pack(fill="both", expand=True, padx=10, pady=8)
        panes.columnconfigure(1, weight=1)
        panes.columnconfigure(3, weight=1)
        panes.rowconfigure(1, weight=1)

        self._header(panes, 0, "left", self.lpath, self.linfo, self.leol)
        self._header(panes, 2, "right", self.rpath, self.rinfo, self.reol)

        self.gl = self._make_gutter(panes)
        self.left = self._make_text(panes)
        self.gr = self._make_gutter(panes)
        self.right = self._make_text(panes)
        self.gl.grid(row=1, column=0, sticky="ns")
        self.left.grid(row=1, column=1, sticky="nsew")
        self.gr.grid(row=1, column=2, sticky="ns", padx=(4, 0))
        self.right.grid(row=1, column=3, sticky="nsew")

        self.vbar = ttk.Scrollbar(panes, orient="vertical", command=self._yview)
        self.vbar.grid(row=1, column=4, sticky="ns")
        self.overview = tk.Canvas(panes, width=14, highlightthickness=0, bd=0, cursor="hand2")
        self.overview.grid(row=1, column=5, sticky="ns", padx=(2, 0))
        self.hbar = ttk.Scrollbar(panes, orient="horizontal", command=self._xview)
        self.hbar.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(4, 0))

        self._panes = (self.gl, self.left, self.gr, self.right)
        for w in self._panes:
            w.configure(yscrollcommand=self._on_yscroll)
        for w in (self.left, self.right):
            w.configure(xscrollcommand=self._on_xscroll)
        for w in self._panes:
            w.bind("<MouseWheel>", self._on_wheel)
            w.bind("<Button-4>", self._on_wheel)
            w.bind("<Button-5>", self._on_wheel)
        for side, w in (("left", self.left), ("right", self.right)):
            w.bind("<Control-a>", self._select_all)
            w.bind("<FocusIn>", lambda _e, s=side: self._set_active(s), add="+")
            w.bind("<Control-End>", self._ctrl_end)
            w.bind("<KeyPress>", self._guard_pad_line)
            w.bind("<<Modified>>", lambda _e, s=side: self._on_modified(s))
        for g in (self.gl, self.gr):
            g.bind("<Button-1>", lambda _e: "break")
        self.overview.bind("<Button-1>", self._on_overview_click)
        self.overview.bind("<B1-Motion>", self._on_overview_click)
        self.overview.bind("<Configure>", lambda _e: self._draw_overview())

    def _header(self, parent: ttk.Frame, col: int, side: str, var: tk.StringVar, info: tk.StringVar,
                eol_var: tk.StringVar) -> None:
        box = ttk.Frame(parent)
        box.grid(row=0, column=col, columnspan=2, sticky="ew", pady=(0, 4), padx=(0 if col == 0 else 6, 0))
        box.columnconfigure(0, weight=1)
        combo = ttk.Combobox(box, textvariable=var, values=self.app.history("file"))
        combo.grid(row=0, column=0, sticky="ew")
        combo.bind("<Return>", lambda _e: self.reload())
        ttk.Button(box, text="…", width=3, command=lambda: self._browse(side)).grid(row=0, column=1, padx=(5, 0))
        ttk.Button(box, text="Remote ▾", command=lambda: self._remote(side)).grid(row=0, column=2, padx=(5, 0))
        eol = ttk.Combobox(box, textvariable=eol_var, values=("Auto", "CRLF", "LF", "CR"),
                           state="readonly", width=6)
        eol.grid(row=0, column=3, padx=(5, 0))
        eol.bind("<<ComboboxSelected>>", lambda _e: self._on_eol_change(side))
        ttk.Label(box, textvariable=info, style="Muted.TLabel").grid(row=1, column=0, columnspan=4,
                                                                    sticky="w", pady=(2, 0))
        setattr(self, side + "_combo", combo)
        setattr(self, side + "_eol_combo", eol)

    def _make_text(self, parent: tk.Misc) -> tk.Text:
        return tk.Text(parent, font=FONTS.mono, wrap="none", relief="flat", bd=0, highlightthickness=1,
                       padx=8, pady=6, undo=False, cursor="xterm")

    def _make_gutter(self, parent: tk.Misc) -> tk.Text:
        w = tk.Text(parent, font=FONTS.mono, wrap="none", relief="flat", bd=0, highlightthickness=0,
                    padx=6, pady=6, width=5, cursor="arrow", takefocus=0)
        w.configure(state="disabled")
        return w

    def apply_theme(self) -> None:
        for w in (self.left, self.right):
            w.configure(bg=T["text_bg"], fg=T["text_fg"], selectbackground=T["sel"],
                        inactiveselectbackground=T["sel"], highlightbackground=T["border"],
                        highlightcolor=T["accent"], insertbackground=T["fg"])
            w.tag_configure("equal", background=T["text_bg"], foreground=T["text_fg"])
            w.tag_configure("pad", background=T["pad_bg"], foreground=T["pad_fg"])
            w.tag_configure("insert", background=T["ins_bg"], foreground=T["ins_fg"])
            w.tag_configure("delete", background=T["del_bg"], foreground=T["del_fg"])
            w.tag_configure("replace", background=T["chg_bg"], foreground=T["chg_fg"])
            w.tag_configure("intra_insert", background=T["intra_ins"])
            w.tag_configure("intra_delete", background=T["intra_del"])
            w.tag_configure("intra_replace", background=T["intra_chg"])
            w.tag_configure("find", background=T["find"])
            w.tag_configure("find_cur", background=T["find_cur"], foreground="#000000")
            w.tag_configure("current_change", background=T["current"])
            w.tag_raise("sel")
        self._mark_active_pane()
        for g in (self.gl, self.gr):
            g.configure(bg=T["gutter_bg"], fg=T["gutter_fg"], selectbackground=T["gutter_bg"])
            g.tag_configure("gnum", justify="right", foreground=T["gutter_fg"])
        self.overview.configure(bg=T["gutter_bg"])
        for combo in (self.ws_combo, getattr(self, "left_combo"), getattr(self, "right_combo"),
                      getattr(self, "left_eol_combo"), getattr(self, "right_eol_combo")):
            retheme_combobox(combo)
        self._draw_overview()

    # ---------- loading ----------
    def _browse(self, side: str) -> None:
        var = self.lpath if side == "left" else self.rpath
        current = var.get()
        start = os.path.dirname(current) if (current and not is_remote_path(current)) else None
        path = filedialog.askopenfilename(title="Select %s file" % side, initialdir=start or None)
        if not path:
            return
        if not self._confirm_discard():
            return
        var.set(os.path.normpath(path))
        self.load()

    def _remote(self, side: str) -> None:
        var = self.lpath if side == "left" else self.rpath
        self.app.popup_remote_menu(self, self.app.shared_remote_menu, var, pick_file=True,
                                   after=lambda: self._confirm_discard() and self.load())

    def _confirm_discard(self) -> bool:
        self._flush_edit()
        if not (self.dirty["left"] or self.dirty["right"]):
            return True
        return messagebox.askyesno("Unsaved Changes", "Discard your unsaved changes?")

    def reload(self) -> None:
        if self._confirm_discard():
            self.load()

    def load(self) -> None:
        if self._edit_job is not None:
            self.after_cancel(self._edit_job)
        self._edit_job, self._edit_sides, self._burst_snapshot = None, set(), False
        self.leol.set("Auto")
        self.reol.set("Auto")
        lp, rp = self.lpath.get().strip(), self.rpath.get().strip()
        for p in (lp, rp):
            if p and not is_remote_path(p) and os.path.isfile(p) and os.path.getsize(p) > LARGE_FILE_BYTES:
                if not messagebox.askyesno(
                        "Large File", "%s is larger than %d MB.\nComparing it may be slow. Continue?"
                        % (os.path.basename(p), LARGE_FILE_BYTES // (1024 * 1024))):
                    return
        try:
            ltarget = self.app.target(lp, self)
            rtarget = self.app.target(rp, self)
        except RemoteError as ex:
            messagebox.showerror("Remote Path", str(ex))
            return
        if ltarget is None or rtarget is None:
            return
        self.ltarget, self.rtarget = ltarget, rtarget
        for p in (lp, rp):
            if p:
                self.app.remember_path("file", p)
        self.app.add_recent("file", lp, rp)
        self.loading = True
        self.status_var.set("Loading…")

        def work() -> Dict[str, TextFile]:
            out: Dict[str, TextFile] = {}
            for side, target in (("left", ltarget), ("right", rtarget)):
                if not target.path:
                    out[side] = TextFile(exists=False)
                    continue
                fs = target.open()
                try:
                    if not fs.isfile(target.root):
                        out[side] = TextFile(exists=False)
                    else:
                        out[side] = parse_text_bytes(fs.read_bytes(target.root))
                finally:
                    try:
                        fs.close()
                    except Exception:
                        pass
            return out

        def done(res: Dict[str, TextFile]) -> None:
            self.loading = False
            self.lf, self.rf = res["left"], res["right"]
            self.left_lines, self.right_lines = list(self.lf.lines), list(self.rf.lines)
            self.dirty = {"left": False, "right": False}
            self.undo_stack.clear()
            self.nav_index = -1
            self.binary = self.lf.binary or self.rf.binary
            self.recompute()
            self.after_idle(self.next_change)

        def failed(ex: Exception) -> None:
            self.loading = False
            messagebox.showerror("Read Failed", str(ex))
            self.status_var.set("Read failed: %s" % ex)

        run_async(self, work, done, failed)

    # ---------- diff / paint ----------
    def _diff_options(self) -> DiffOptions:
        return DiffOptions(WS_LABELS.get(self.ws_var.get(), WS_NONE), bool(self.case_var.get()))

    def _view(self) -> ViewSettings:
        prefixes = tuple(p.strip() for p in self.prefix_var.get().split(",") if p.strip())
        return ViewSettings(
            mode=self.mode_var.get(), context=bool(self.ctx_var.get()),
            before=spin_value(self.before, 5), after=spin_value(self.after_, 5),
            comment_before=spin_value(self.cbefore, 2), comment_after=spin_value(self.cafter, 2),
            comment_prefixes=prefixes or DEFAULT_COMMENT_PREFIXES,
        )

    def recompute(self) -> None:
        self._flush_edit()
        self._save_options()
        if self.binary:
            self.rows, self.hunks, self.display, self.runs = [], [], [], []
            self._paint_message()
            self._update_info()
            return
        self.rows = diff_lines(self.left_lines, self.right_lines, self._diff_options())
        self.hunks = find_hunks(self.rows)
        if self.nav_index >= len(self.hunks):
            self.nav_index = -1
        self.repaint()

    def _save_options(self) -> None:
        self.app.cfg["file_opts"] = {
            "mode": self.mode_var.get(), "context": bool(self.ctx_var.get()),
            "before": spin_value(self.before, 5), "after": spin_value(self.after_, 5),
            "cbefore": spin_value(self.cbefore, 2), "cafter": spin_value(self.cafter, 2),
            "prefixes": self.prefix_var.get(), "ws": WS_LABELS.get(self.ws_var.get(), WS_NONE),
            "case": bool(self.case_var.get()),
        }

    def _on_opts_change(self) -> None:
        self._flush_edit()
        self.recompute()

    def _on_ctx_toggle(self) -> None:
        if self.ctx_var.get() and self.mode_var.get() == "all":
            self.mode_var.set("diffs")
        self._on_opts_change()

    def repaint(self) -> None:
        if self.binary:
            return
        self._save_options()
        self.display = visible_rows(self.rows, self.hunks, self._view())
        self.runs = display_runs(self.display)
        width = 0
        for i in self.display:
            r = self.rows[i]
            width = max(width, len((r.ltext or "").expandtabs()), len((r.rtext or "").expandtabs()))
        self._wpad = max(width, 1)
        digits = len(str(max(len(self.left_lines), len(self.right_lines), 1)))
        self._fill(self.left, self.gl, "left", digits)
        self._fill(self.right, self.gr, "right", digits)
        self._apply_intraline()
        self._clear_find()
        self._draw_overview()
        self._update_info()

    def _fill(self, text: tk.Text, gutter: tk.Text, side: str, digits: int) -> None:
        self._painting = True
        try:
            for name in list(text.mark_names()):
                if name[:1] in ("s", "e") and name[1:].isdigit():
                    text.mark_unset(name)
            gutter.configure(state="normal", width=digits + 1)
            text.delete("1.0", "end")
            gutter.delete("1.0", "end")
            buf: List[str] = []
            cur: Optional[str] = None
            nums: List[str] = []
            for i in self.display:
                r = self.rows[i]
                tag = r.ltag if side == "left" else r.rtag
                no = r.number(side)
                nums.append("" if no is None else str(no))
                if tag != cur and buf:
                    text.insert("end", "\n".join(buf) + "\n", cur or "equal")
                    buf = []
                cur = tag
                buf.append(r.text(side) or "")
            if buf:
                text.insert("end", "\n".join(buf) + "\n", cur or "equal")
            # Invisible final line: spaces that equalise the horizontal scroll range of
            # both panes. It sits outside every segment mark, so it is never part of the file.
            text.insert("end", " " * self._wpad, "equal")
            text.configure(state="normal", cursor="xterm")      # editable again after a binary view
            for k, (pane_start, pane_end, _rs, _re) in enumerate(self.runs):
                text.mark_set("s%d" % k, "%d.0" % (pane_start + 1))
                text.mark_gravity("s%d" % k, "left")
                text.mark_set("e%d" % k, "%d.0" % (pane_end + 2))
                text.mark_gravity("e%d" % k, "left")
            if nums:
                gutter.insert("end", "\n".join(nums) + "\n", "gnum")
            gutter.configure(state="disabled")
            text.edit_modified(False)
        finally:
            self._painting = False

    def _apply_intraline(self) -> None:
        done = 0
        for d, i in enumerate(self.display):
            r = self.rows[i]
            if r.tag != REPLACE or r.ltext is None or r.rtext is None:
                continue
            done += 1
            if done > MAX_INTRALINE_ROWS:
                break
            sa, sb = intraline_spans(r.ltext, r.rtext)
            for s, e in sa:
                self.left.tag_add("intra_replace", "%d.%d" % (d + 1, s), "%d.%d" % (d + 1, e))
            for s, e in sb:
                self.right.tag_add("intra_replace", "%d.%d" % (d + 1, s), "%d.%d" % (d + 1, e))
        for w in (self.left, self.right):
            w.tag_raise("find")
            w.tag_raise("find_cur")
            w.tag_raise("current_change")
            w.tag_raise("sel")

    def _paint_message(self) -> None:
        same = self.lf.raw == self.rf.raw
        self._painting = True
        try:
            for w, f in ((self.left, self.lf), (self.right, self.rf)):
                w.configure(state="normal")
                w.delete("1.0", "end")
                w.insert("end", "\n  Binary comparison\n  %s\n\n  %s" % (
                    f.describe(), "Files are identical." if same else "Files differ."), "equal")
                w.edit_modified(False)
                w.configure(state="disabled", cursor="arrow")   # nothing editable to show
            for g in (self.gl, self.gr):
                g.configure(state="normal")
                g.delete("1.0", "end")
                g.configure(state="disabled")
            self.overview.delete("all")
        finally:
            self._painting = False

    def _update_info(self) -> None:
        default_eol = "CRLF" if IS_WINDOWS else "LF"

        def side_text(meta: TextFile, target: FsTarget, path_var: tk.StringVar, side: str,
                      eol_var: tk.StringVar) -> str:
            bits = []
            if target.is_remote:
                bits.append("remote")
            if not meta.exists:
                bits.append("(new file)" if path_var.get().strip() else "(no file)")
            else:
                bits.append(meta.describe())
                if meta.eol == "Mixed" and eol_var.get() == "Auto":
                    bits.append("→ saves as " + default_eol)
            if eol_var.get() != "Auto":
                bits.append("→ saves as " + eol_var.get())
            if self.dirty[side]:
                bits.append("● modified")
            return "   ".join(bits)

        self.linfo.set(side_text(self.lf, self.ltarget, self.lpath, "left", self.leol))
        self.rinfo.set(side_text(self.rf, self.rtarget, self.rpath, "right", self.reol))
        if self.loading:
            pass
        elif self.binary:
            self.status_var.set("Binary files — " + ("identical" if self.lf.raw == self.rf.raw else "different"))
        elif self.hunks:
            self.status_var.set("%d difference block(s)   |   type in either pane to edit; "
                                "Alt+→ / Alt+← copy a block" % len(self.hunks))
        elif self.left_lines == self.right_lines:
            self.status_var.set("Files are identical")
        else:
            self.status_var.set("No differences under the current options (whitespace / case ignored)")
        for b in (self.btn_ltr, self.btn_rtl):
            b.state(["!disabled"] if not self.binary else ["disabled"])
        self.btn_undo.state(["!disabled"] if self.undo_stack else ["disabled"])
        self.btn_save.state(["!disabled"] if (self.dirty["left"] or self.dirty["right"]) else ["disabled"])
        self.app.update_tab_title(self)

    # ---------- scrolling ----------
    def _yview(self, *args: Any) -> None:
        self.left.yview(*args)

    def _xview(self, *args: Any) -> None:
        self.left.xview(*args)
        self.right.xview(*args)

    def _on_yscroll(self, first: Any, last: Any) -> None:
        self.vbar.set(first, last)
        if not self._ysync:
            self._ysync = True
            try:
                for w in self._panes:
                    w.yview_moveto(float(first))
            finally:
                self._ysync = False
        self._draw_view()

    def _on_xscroll(self, first: Any, last: Any) -> None:
        self.hbar.set(first, last)
        if self._xsync:
            return
        self._xsync = True
        try:
            self.left.xview_moveto(float(first))
            self.right.xview_moveto(float(first))
        finally:
            self._xsync = False

    def _on_wheel(self, event: Any) -> str:
        if event.state & 0x4:                                   # Ctrl+wheel = zoom
            up = event.num == 4 or getattr(event, "delta", 0) > 0
            self.app.change_font(1 if up else -1)
            return "break"
        if event.num == 4:
            units = -3
        elif event.num == 5:
            units = 3
        else:
            d = event.delta
            units = -int(d / 120) * 3 if abs(d) >= 120 else (-d or 0)
        if event.state & 0x1:                                   # Shift+wheel = sideways
            self._xview("scroll", units, "units")
        else:
            self._yview("scroll", units, "units")
        return "break"

    def _select_all(self, event: Any) -> str:
        event.widget.tag_add("sel", "1.0", "end-1c")
        return "break"

    # ---------- overview strip ----------
    def _draw_overview(self) -> None:
        c = self.overview
        c.delete("all")
        total = len(self.display)
        h = c.winfo_height()
        if not total or h <= 1:
            return
        colors = {INSERT: T["intra_ins"], DELETE: T["intra_del"], REPLACE: T["intra_chg"]}
        for hunk in self.hunks:
            d0 = bisect_left(self.display, hunk[0])
            d1 = bisect_right(self.display, hunk[1]) - 1
            if d0 > d1:
                continue
            y0 = d0 / total * h
            y1 = max(y0 + 3, (d1 + 1) / total * h)
            c.create_rectangle(1, y0, 13, y1, fill=colors[hunk_kind(self.rows, hunk)], outline="")
        self._draw_view()

    def _draw_view(self) -> None:
        c = self.overview
        c.delete("view")
        h = c.winfo_height()
        if h <= 1:
            return
        first, last = self.left.yview()
        c.create_rectangle(0, first * h, 13, max(first * h + 4, last * h), outline=T["fg"], tags="view")

    def _on_overview_click(self, event: Any) -> None:
        h = max(1, self.overview.winfo_height())
        first, last = self.left.yview()
        self._yview("moveto", max(0.0, min(1.0, event.y / h - (last - first) / 2)))

    # ---------- change navigation ----------
    def next_change(self) -> None:
        self._step(1)

    def prev_change(self) -> None:
        self._step(-1)

    def _step(self, direction: int) -> None:
        self._flush_edit()
        if not self.hunks:
            return
        if self.nav_index < 0:
            self.nav_index = 0 if direction > 0 else len(self.hunks) - 1
        else:
            self.nav_index = (self.nav_index + direction) % len(self.hunks)
        self._show_hunk(self.nav_index)

    def _show_hunk(self, idx: int) -> None:
        first, last = self.hunks[idx]
        d_first = bisect_left(self.display, first)
        d_last = bisect_right(self.display, last) - 1
        if d_first > d_last or d_first >= len(self.display):
            return
        for w in (self.left, self.right):
            w.tag_remove("current_change", "1.0", "end")
            w.tag_add("current_change", "%d.0" % (d_first + 1), "%d.end" % (d_last + 1))
        self._yview("%d.0" % max(1, d_first - 2))
        if self._flash_job:
            self.after_cancel(self._flash_job)
        self._flash_job = self.after(1400, self._clear_flash)
        self.status_var.set("Difference %d of %d" % (idx + 1, len(self.hunks)))

    def _clear_flash(self) -> None:
        self._flash_job = None
        for w in (self.left, self.right):
            w.tag_remove("current_change", "1.0", "end")

    def _hunk_at_view(self) -> int:
        if not self.hunks:
            return -1
        top = int(str(self.left.index("@0,0")).split(".")[0]) - 1
        top = max(0, min(top, max(0, len(self.display) - 1)))
        full_top = self.display[top] if self.display else 0
        for n, (_h0, h1) in enumerate(self.hunks):
            if h1 >= full_top:
                return n
        return len(self.hunks) - 1

    # ---------- editing ----------
    def _on_modified(self, side: str) -> None:
        w = self.left if side == "left" else self.right
        if not w.edit_modified():
            return                                  # the reset we do ourselves also fires this event
        w.edit_modified(False)
        if self._painting or self.binary or self.loading:
            return
        self._schedule_edit(side)

    def _schedule_edit(self, side: str) -> None:
        self._edit_sides.add(side)
        if self._edit_job is None:
            # first keystroke of a burst: snapshot so Ctrl+Z takes the whole burst back
            self.undo_stack.append((list(self.left_lines), list(self.right_lines), dict(self.dirty)))
            del self.undo_stack[:-50]
            self._burst_snapshot = True
        else:
            self.after_cancel(self._edit_job)
        self._edit_job = self.after(EDIT_DEBOUNCE_MS, self._commit_edit)

    def _flush_edit(self) -> None:
        if self._edit_job is not None:
            self.after_cancel(self._edit_job)
            self._commit_edit()

    def _extract_lines(self, side: str) -> List[str]:
        """Read one edited pane back into a full list of file lines.

        Only the visible blocks live in the pane, so each block is read between its
        marks and the hidden rows in between are taken from the last diff."""
        w = self.left if side == "left" else self.right
        if not self.runs:
            # Both files are empty, so there are no rows to anchor to: the pane holds
            # nothing but the width-padding line, and anything typed into it is the
            # whole new content.
            lines = str(w.get("1.0", "end-1c")).split("\n")
            if lines:
                tail = strip_width_pad(lines.pop(), self._wpad)
                if tail:
                    lines.append(tail)
            return lines
        run_lines: List[List[str]] = []
        for k in range(len(self.runs)):
            _ps, _pe, row_start, row_end = self.runs[k]
            try:
                start = str(w.index("s%d" % k))
                end = str(w.index("e%d" % k))
            except tk.TclError:
                # Should not happen; keep the block's current text rather than lose it.
                run_lines.append([t for t in (self.rows[i].text(side)
                                              for i in range(row_start, row_end + 1)) if t is not None])
                continue
            lines = str(w.get(start, end)).split("\n")
            if lines and lines[-1] == "":
                lines.pop()
            first = int(start.split(".")[0])
            pads = ["pad" in w.tag_names("%d.0" % (first + i)) for i in range(len(lines))]
            run_lines.append(drop_untouched_padding(lines, pads))
        return rebuild_file_lines(self.rows, self.runs, side, run_lines)

    def _cursor_anchor(self) -> Optional[tuple]:
        try:
            focused = self.focus_get()
        except (KeyError, tk.TclError):
            return None
        if focused is not self.left and focused is not self.right:
            return None
        side = "left" if focused is self.left else "right"
        pane = self.left if side == "left" else self.right
        line, col = (int(x) for x in str(pane.index("insert")).split("."))
        number = nearest_line_number(self.rows, self.display, line - 1, side)
        return (side, number, col) if number else None

    def _restore_anchor(self, anchor: Optional[tuple], y: float) -> None:
        self._yview("moveto", y)
        if not anchor:
            return
        side, number, col = anchor
        d = display_index_for_number(self.rows, self.display, side, number)
        if d is None:
            return
        w = self.left if side == "left" else self.right
        try:
            w.mark_set("insert", "%d.%d" % (d + 1, col))
            w.see("insert")
            w.focus_set()
        except tk.TclError:
            pass

    def _commit_edit(self) -> None:
        """Pull the edited pane text back into the line lists, then re-diff and re-align."""
        self._edit_job = None
        sides, self._edit_sides = self._edit_sides, set()
        took_snapshot, self._burst_snapshot = self._burst_snapshot, False
        changed = False
        if sides and not self.binary:
            anchor = self._cursor_anchor()
            y = self.left.yview()[0]
            for side in sides:
                new = self._extract_lines(side)
                if new != (self.left_lines if side == "left" else self.right_lines):
                    if side == "left":
                        self.left_lines = new
                    else:
                        self.right_lines = new
                    self.dirty[side] = True
                    changed = True
            if changed:
                self.rows = diff_lines(self.left_lines, self.right_lines, self._diff_options())
                self.hunks = find_hunks(self.rows)
                if self.nav_index >= len(self.hunks):
                    self.nav_index = -1
                self.repaint()
                self._restore_anchor(anchor, y)
        if not changed and took_snapshot and self.undo_stack:
            self.undo_stack.pop()

    def _pad_line(self) -> int:
        return len(self.display) + 1

    def _guard_pad_line(self, event: Any) -> Optional[str]:
        """Keep typing out of the invisible width-padding line at the bottom."""
        if self.binary or not self.display:
            return None
        w = event.widget
        try:
            line = int(str(w.index("insert")).split(".")[0])
        except tk.TclError:
            return None
        if line < self._pad_line():
            return None
        modifies = (event.keysym in ("Return", "BackSpace", "Delete", "Tab")
                    or (event.char and event.char >= " ")
                    or (event.state & 0x4 and event.keysym.lower() in ("v", "x")))
        if modifies:
            w.mark_set("insert", "%d.end" % max(1, self._pad_line() - 1))
        return None

    def _ctrl_end(self, event: Any) -> Optional[str]:
        if not self.display:
            return None
        event.widget.mark_set("insert", "%d.end" % max(1, self._pad_line() - 1))
        event.widget.see("insert")
        return "break"

    # ---------- merging ----------
    def copy_block(self, direction: str) -> None:
        self._flush_edit()
        if self.binary or not self.hunks:
            return
        idx = self.nav_index if 0 <= self.nav_index < len(self.hunks) else self._hunk_at_view()
        self.undo_stack.append((list(self.left_lines), list(self.right_lines), dict(self.dirty)))
        del self.undo_stack[:-50]
        self.left_lines, self.right_lines = apply_hunk(
            self.rows, self.hunks[idx], direction, self.left_lines, self.right_lines)
        self.dirty["right" if direction == "ltr" else "left"] = True
        self.nav_index = idx - 1            # lands on the next block after the merged one disappears
        self.recompute()
        self._step(1)

    def undo(self) -> None:
        self._flush_edit()
        if not self.undo_stack:
            return
        self.left_lines, self.right_lines, self.dirty = self.undo_stack.pop()
        self.nav_index = -1
        self.recompute()

    def swap(self) -> None:
        self._flush_edit()
        lp, rp = self.lpath.get(), self.rpath.get()
        le, re_ = self.leol.get(), self.reol.get()
        self.lpath.set(rp)
        self.rpath.set(lp)
        self.leol.set(re_)
        self.reol.set(le)
        self.ltarget, self.rtarget = self.rtarget, self.ltarget
        self.lf, self.rf = self.rf, self.lf
        self.left_lines, self.right_lines = self.right_lines, self.left_lines
        self.dirty = {"left": self.dirty["right"], "right": self.dirty["left"]}
        self.undo_stack.clear()
        self.nav_index = -1
        self.recompute()

    # ---------- saving ----------
    def save(self) -> bool:
        self._flush_edit()
        ok = True
        for side in ("left", "right"):
            if self.dirty[side]:
                ok = self._save_side(side) and ok
        self.recompute()
        return ok

    def _save_side(self, side: str) -> bool:
        var = self.lpath if side == "left" else self.rpath
        target = self.ltarget if side == "left" else self.rtarget
        path = var.get().strip()
        if not path:
            path = filedialog.asksaveasfilename(title="Save %s file as" % side)
            if not path:
                return False
            var.set(os.path.normpath(path))
            new_target = self.app.target(var.get(), self)
            if new_target is None:
                return False
            target = new_target
            if side == "left":
                self.ltarget = target
            else:
                self.rtarget = target
        meta = self.lf if side == "left" else self.rf
        other = self.rf if side == "left" else self.lf
        src = meta if meta.exists else (other if other.exists and not other.binary else meta)
        lines = self.left_lines if side == "left" else self.right_lines
        eol_var = self.leol if side == "left" else self.reol
        eol = src.eol if src.exists else ""
        if eol_var.get() in ("CRLF", "LF", "CR"):
            eol = eol_var.get()
        data = build_text_bytes(lines, src.encoding if src.exists else "utf-8", eol,
                                src.trailing_newline if src.exists else True)
        self.status_var.set("Saving %s…" % target.display())
        self.update_idletasks()
        fs = None
        try:
            fs = target.open()
            fs.write_bytes(target.root, data)
        except (OSError, RemoteError) as ex:
            messagebox.showerror("Save Failed", "%s\n\n%s" % (target.display(), ex))
            return False
        finally:
            if fs is not None:
                try:
                    fs.close()
                except Exception:
                    pass
        fresh = parse_text_bytes(data)
        if side == "left":
            self.lf = fresh
        else:
            self.rf = fresh
        self.dirty[side] = False
        eol_var.set("Auto")
        self.app.remember_path("file", var.get().strip())
        return True

    # ---------- find ----------
    def focus_find(self) -> None:
        self.find_entry.focus_set()
        self.find_entry.selection_range(0, "end")

    def _clear_find(self) -> None:
        self._matches, self._match_pos, self._find_query = [], -1, ""

    def _pane(self, side: str) -> tk.Text:
        return self.left if side == "left" else self.right

    def _mark_active_pane(self) -> None:
        for side in ("left", "right"):
            active = side == self.active_side
            self._pane(side).configure(highlightthickness=2 if active else 1,
                                       highlightbackground=T["accent"] if active else T["border"])

    def _set_active(self, side: str) -> None:
        """Find / F3 work on the pane you were last in."""
        if side == self.active_side:
            return
        self.active_side = side
        self.find_label.set("Find in %s  (F3)" % side)
        for w in (self.left, self.right):
            w.tag_remove("find", "1.0", "end")
            w.tag_remove("find_cur", "1.0", "end")
        self._clear_find()                  # the next F3 searches the newly active pane
        self._mark_active_pane()

    def _collect_matches(self, query: str) -> List[tuple]:
        out: List[tuple] = []
        count = tk.IntVar(self)
        for w in (self.left, self.right):
            w.tag_remove("find", "1.0", "end")
            w.tag_remove("find_cur", "1.0", "end")
        w = self._pane(self.active_side)
        pad_line = self._pad_line()
        pos = "1.0"
        while len(out) < MAX_FIND_MATCHES:
            idx = w.search(query, pos, stopindex="end", nocase=True, count=count)
            if not idx:
                break
            line, col = (int(x) for x in str(idx).split("."))
            if self.display and line >= pad_line:
                break                       # the invisible padding line is not file content
            end = "%s+%dc" % (idx, max(1, count.get()))
            w.tag_add("find", idx, end)
            out.append((line, col, str(idx), end))
            pos = end
        return out

    def _cursor_pos(self) -> Tuple[int, int]:
        try:
            line, col = (int(x) for x in str(self._pane(self.active_side).index("insert")).split("."))
            return line, col
        except (tk.TclError, ValueError):
            return 1, 0

    def find_next(self, direction: int) -> None:
        query = self.find_var.get()
        if not query:
            self.focus_find()           # F3 on an empty box jumps to it so you can type
            return
        fresh = query != self._find_query
        if fresh:
            self._matches = self._collect_matches(query)
            self._find_query = query
        if not self._matches:
            self.status_var.set("Not found in %s pane: %s" % (self.active_side, query))
            return
        w = self._pane(self.active_side)
        w.tag_remove("find_cur", "1.0", "end")
        if fresh:
            # First jump of a new search goes to the match nearest the cursor
            here = self._cursor_pos()
            positions = [(m[0], m[1]) for m in self._matches]
            if direction > 0:
                self._match_pos = bisect_left(positions, here) - 1
            else:
                self._match_pos = bisect_left(positions, here)
        self._match_pos = (self._match_pos + direction) % len(self._matches)
        line, col, idx, end = self._matches[self._match_pos]
        w.tag_add("find_cur", idx, end)
        w.mark_set("insert", end if direction > 0 else idx)
        w.see(idx)
        wrapped = ""
        if not fresh and len(self._matches) > 1:
            if direction > 0 and self._match_pos == 0:
                wrapped = "  (wrapped to top)"
            elif direction < 0 and self._match_pos == len(self._matches) - 1:
                wrapped = "  (wrapped to bottom)"
        self.status_var.set("Match %d of %d in %s pane%s" % (
            self._match_pos + 1, len(self._matches), self.active_side, wrapped))

    # ---------- tab protocol ----------
    def _on_eol_change(self, side: str) -> None:
        self.dirty[side] = True
        self._update_info()

    def on_close(self) -> bool:
        self._flush_edit()
        if not (self.dirty["left"] or self.dirty["right"]):
            return True
        answer = messagebox.askyesnocancel("Unsaved Changes", "Save your changes before closing?")
        if answer is None:
            return False
        if answer:
            return self.save()
        return True

    def title(self) -> str:
        def name_of(path: str) -> str:
            path = path.strip()
            if not path:
                return ""
            if is_remote_path(path):
                try:
                    ref = parse_remote(path)
                    return posixpath.basename(ref.path) if ref else path
                except RemoteError:
                    return path
            return os.path.basename(path)

        a, b = name_of(self.lpath.get()), name_of(self.rpath.get())
        name = a if (a == b and a) else " ↔ ".join(x for x in (a, b) if x) or "File compare"
        star = "*" if (self.dirty["left"] or self.dirty["right"]) else ""
        return "📄 " + name + star


# ============================================================
#               HOME TAB
# ============================================================

class HomeTab(ttk.Frame):
    kind = "home"

    def __init__(self, app: "App") -> None:
        super().__init__(app.nb)
        self.app = app
        self.keymap: Dict[str, Callable[[], Any]] = {}

        center = ttk.Frame(self)
        center.pack(fill="both", expand=True, padx=40, pady=30)
        ttk.Label(center, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(center, text="Compare folders and files, locally or over FTP. Edit either side and "
                               "merge changes across.", style="Muted.TLabel").pack(anchor="w", pady=(2, 18))

        btns = ttk.Frame(center)
        btns.pack(anchor="w")
        ttk.Button(btns, text="📁  Folder Compare   (Ctrl+D)", style="Accent.TButton",
                   command=lambda: app.new_folder_compare()).pack(side="left", padx=(0, 10))
        ttk.Button(btns, text="📄  File Compare   (Ctrl+N)", style="Accent.TButton",
                   command=lambda: app.new_file_compare()).pack(side="left", padx=(0, 10))
        ttk.Button(btns, text="🌐  FTP Connections…", command=app.manage_profiles).pack(side="left")

        row = ttk.Frame(center)
        row.pack(fill="x", pady=(26, 6))
        ttk.Label(row, text="Recent sessions  (double-click to reopen)").pack(side="left")
        ttk.Button(row, text="Clear", command=self.clear_recent).pack(side="right")

        body = ttk.Frame(center)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(body, columns=("kind", "left", "right"), show="headings", height=10)
        for col, text, width in (("kind", "Type", 70), ("left", "Left", 400), ("right", "Right", 400)):
            self.tree.heading(col, text=text, anchor="w")
            self.tree.column(col, width=width, anchor="w", stretch=(col != "kind"))  # type: ignore[arg-type]
        sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", self._reopen)
        self.tree.bind("<Return>", self._reopen)
        self.refresh()

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for r in self.app.cfg.get("recent", []):
            self.tree.insert("", "end", values=(r.get("kind", ""), r.get("left", ""), r.get("right", "")))

    def clear_recent(self) -> None:
        self.app.cfg["recent"] = []
        self.refresh()

    def _reopen(self, _e: Any = None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        kind, left, right = self.tree.item(sel[0], "values")
        if kind == "folder":
            self.app.new_folder_compare(left, right)
        else:
            self.app.new_file_compare(left, right)

    def apply_theme(self) -> None:
        pass

    def on_close(self) -> bool:
        return False

    def title(self) -> str:
        return "🏠 Home"


# ============================================================
#               APPLICATION
# ============================================================

class App:
    def __init__(self, root: tk.Tk, cfg: Dict[str, Any]) -> None:
        global FONTS
        self.root = root
        self.cfg = cfg
        root.title(APP_NAME)
        apply_window_icon(root)
        root.geometry(cfg.get("geometry", "1500x900"))
        root.minsize(1000, 620)

        name = cfg.get("theme", DEFAULT_THEME)
        T.clear()
        T.update(THEMES.get(name, THEMES[DEFAULT_THEME]))
        self.theme_name = name if name in THEMES else DEFAULT_THEME
        FONTS = Fonts(root, int(cfg.get("font_size", 10)))
        apply_ttk_theme(root)
        root.configure(bg=T["bg"])

        self.profiles: List[FtpProfile] = [FtpProfile.from_dict(d)
                                           for d in cfg.get("ftp_profiles", []) if isinstance(d, dict)]
        self._session_passwords: Dict[str, str] = {}

        self._build_chrome()
        self.home = HomeTab(self)
        self.nb.add(self.home, text=self.home.title())

        for seq in ("<F5>", "<F7>", "<F8>", "<F3>", "<Shift-F3>", "<Control-s>", "<Control-z>",
                    "<Control-f>", "<Control-Right>", "<Control-Left>", "<Alt-Right>", "<Alt-Left>"):
            root.bind(seq, lambda e, s=seq: self._on_key(s, e))
        root.bind("<Control-n>", lambda _e: self.new_file_compare())
        root.bind("<Control-d>", lambda _e: self.new_folder_compare())
        root.bind("<Control-w>", lambda _e: self.close_tab())
        for seq in ("<Control-equal>", "<Control-plus>"):
            root.bind(seq, lambda _e: self.change_font(1))
        root.bind("<Control-minus>", lambda _e: self.change_font(-1))
        root.bind("<Control-0>", lambda _e: self.change_font(0))
        root.protocol("WM_DELETE_WINDOW", self.on_exit)
        root.after(30, lambda: set_titlebar_dark(root, bool(T["dark"])))

    # ---------- chrome ----------
    def _build_chrome(self) -> None:
        self.topbar = ttk.Frame(self.root, style="Panel.TFrame")
        self.topbar.pack(fill="x")
        self.menus: List[tk.Menu] = []
        self.menubuttons: List[tk.Menubutton] = []

        def menubutton(text: str) -> tk.Menu:
            mb = tk.Menubutton(self.topbar, text=text, relief="flat", bd=0, padx=12, pady=6, takefocus=0)
            mb.pack(side="left")
            menu = tk.Menu(mb, tearoff=0)
            mb.configure(menu=menu)
            self.menus.append(menu)
            self.menubuttons.append(mb)
            return menu

        m = menubutton("File")
        m.add_command(label="New Folder Compare\tCtrl+D", command=lambda: self.new_folder_compare())
        m.add_command(label="New File Compare\tCtrl+N", command=lambda: self.new_file_compare())
        m.add_separator()
        m.add_command(label="FTP Connections…", command=self.manage_profiles)
        m.add_separator()
        m.add_command(label="Close Tab\tCtrl+W", command=self.close_tab)
        m.add_command(label="Exit", command=self.on_exit)

        m = menubutton("View")
        self.theme_var = tk.StringVar(value=self.theme_name)
        theme_menu = tk.Menu(m, tearoff=0)
        self.menus.append(theme_menu)
        for name in THEMES:
            theme_menu.add_radiobutton(label=name, value=name, variable=self.theme_var,
                                       command=lambda n=name: self.set_theme(n))
        m.add_cascade(label="Theme", menu=theme_menu)
        m.add_separator()
        m.add_command(label="Increase font size\tCtrl++", command=lambda: self.change_font(1))
        m.add_command(label="Decrease font size\tCtrl+-", command=lambda: self.change_font(-1))
        m.add_command(label="Reset font size\tCtrl+0", command=lambda: self.change_font(0))

        m = menubutton("Help")
        m.add_command(label="Keyboard shortcuts", command=self.show_shortcuts)
        m.add_command(label="About", command=self.show_about)

        self.theme_btn = ttk.Button(self.topbar, text="", command=self.toggle_theme)
        self.theme_btn.pack(side="right", padx=8, pady=3)

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=4, pady=(4, 4))
        self.nb.bind("<Button-2>", self._on_tab_middle_click)
        self.nb.bind("<Button-3>", self._on_tab_right_click)
        self.tab_menu = tk.Menu(self.root, tearoff=0)
        self.tab_menu.add_command(label="Close tab", command=self.close_tab)
        self.menus.append(self.tab_menu)
        self.shared_remote_menu = tk.Menu(self.root, tearoff=0)
        self.menus.append(self.shared_remote_menu)
        self._style_chrome()

    def _style_chrome(self) -> None:
        for menu in self.menus:
            style_menu(menu)
        for mb in self.menubuttons:
            mb.configure(bg=T["panel"], fg=T["fg"], activebackground=T["hover"], activeforeground=T["fg"])
        self.theme_btn.configure(text="☀  Light" if T["dark"] else "🌙  Dark")

    # ---------- tabs ----------
    def tabs(self) -> List[Any]:
        return [self.nb.nametowidget(t) for t in self.nb.tabs()]

    def current_tab(self) -> Optional[Any]:
        sel = self.nb.select()
        return self.nb.nametowidget(sel) if sel else None

    def _add_tab(self, tab: Any) -> None:
        self.nb.add(tab, text=tab.title())
        self.nb.select(tab)

    def update_tab_title(self, tab: Any) -> None:
        try:
            self.nb.tab(tab, text=tab.title())
        except tk.TclError:
            pass

    def new_folder_compare(self, left: str = "", right: str = "") -> None:
        self._add_tab(FolderTab(self, left, right))

    def new_file_compare(self, left: str = "", right: str = "") -> None:
        self._add_tab(FileTab(self, left, right))

    def close_tab(self, tab: Optional[Any] = None) -> None:
        if tab is None:
            tab = self.current_tab()
        if tab is None or tab is self.home:
            return
        if not tab.on_close():
            return
        self.nb.forget(tab)
        tab.destroy()

    def _tab_at(self, event: Any) -> Optional[Any]:
        try:
            idx = self.nb.index("@%d,%d" % (event.x, event.y))
        except tk.TclError:
            return None
        return self.tabs()[idx]

    def _on_tab_middle_click(self, event: Any) -> None:
        tab = self._tab_at(event)
        if tab is not None:
            self.close_tab(tab)

    def _on_tab_right_click(self, event: Any) -> None:
        tab = self._tab_at(event)
        if tab is not None and tab is not self.home:
            self.nb.select(tab)
            self.tab_menu.tk_popup(event.x_root, event.y_root)

    def _on_key(self, seq: str, event: Any) -> Optional[str]:
        # Keep normal text-editing behaviour inside entry fields
        if isinstance(event.widget, tk.Entry) and seq in ("<Control-Left>", "<Control-Right>", "<Control-z>"):
            return None
        # The compare panes are editable, so Ctrl+arrows move the text cursor by word.
        # Copying a block is Alt+arrows there.
        if isinstance(event.widget, tk.Text) and seq in ("<Control-Left>", "<Control-Right>"):
            return None
        tab = self.current_tab()
        fn = getattr(tab, "keymap", {}).get(seq) if tab is not None else None
        if fn:
            fn()
            return "break"
        return None

    # ---------- history / recent ----------
    def history(self, kind: str) -> List[str]:
        return list(self.cfg.get("history", {}).get(kind, []))

    def remember_path(self, kind: str, path: str) -> None:
        if not path:
            return
        hist = self.cfg.setdefault("history", {}).setdefault(kind, [])
        hist[:] = ([path] + [p for p in hist if p != path])[:MAX_HISTORY]

    def add_recent(self, kind: str, left: str, right: str) -> None:
        if not (left or right):
            return
        rec = self.cfg.setdefault("recent", [])
        entry = {"kind": kind, "left": left, "right": right}
        rec[:] = ([entry] + [r for r in rec if r != entry])[:MAX_RECENT]
        self.home.refresh()

    # ---------- FTP profiles ----------
    def set_profiles(self, profiles: List[FtpProfile]) -> None:
        self.profiles = list(profiles)
        self.cfg["ftp_profiles"] = [p.to_dict() for p in self.profiles]

    def manage_profiles(self) -> None:
        FtpProfilesDialog(self.root, self).show()

    def profile_for_ref(self, ref: RemoteRef) -> Optional[FtpProfile]:
        for p in self.profiles:
            if p.matches(ref):
                return p
        for p in self.profiles:                      # same server, different user
            if p.scheme == ref.scheme and p.host.lower() == ref.host.lower() and p.port == ref.port:
                return replace(p, user=ref.user, password="", save_password=False)
        return None

    def target(self, path: str, parent: tk.Misc) -> Optional[FsTarget]:
        """Resolve a path the user typed into something a worker thread can open.

        Returns None only when the user cancels the password prompt."""
        path = (path or "").strip()
        if not path:
            return EMPTY_TARGET
        if not is_remote_path(path):
            return FsTarget(path, path)
        ref = parse_remote(path)
        assert ref is not None
        profile = self.profile_for_ref(ref) or FtpProfile(
            name=ref.host, scheme=ref.scheme, host=ref.host, port=ref.port, user=ref.user)
        profile = replace(profile, scheme=ref.scheme, host=ref.host, port=ref.port, user=ref.user)
        if not profile.password:
            cached = self._session_passwords.get(ref.key)
            if cached is not None:
                profile = replace(profile, password=cached)
            elif profile.user != "anonymous":
                answer = PasswordDialog(parent, ref).show()
                if answer is None:
                    return None
                password, remember = answer
                profile = replace(profile, password=password)
                self._session_passwords[ref.key] = password
                if remember:
                    saved = replace(profile, save_password=True)
                    existing = [i for i, p in enumerate(self.profiles) if p.matches(ref)]
                    if existing:
                        self.profiles[existing[0]] = saved
                    else:
                        saved.name = saved.name or "%s@%s" % (ref.user, ref.host)
                        self.profiles.append(saved)
                    self.set_profiles(self.profiles)
        return FsTarget(path, ref.path, profile)

    def popup_remote_menu(self, parent: tk.Misc, menu: tk.Menu, var: tk.StringVar,
                          pick_file: bool, after: Optional[Callable[[], Any]] = None) -> None:
        """Drop-down of saved FTP connections, used by the Remote ▾ buttons."""
        menu.delete(0, "end")
        style_menu(menu)
        if self.profiles:
            for profile in self.profiles:
                menu.add_command(label="%s  (%s)" % (profile.name or profile.host, profile.host),
                                 command=lambda p=profile: self._browse_remote(parent, p, var,
                                                                               pick_file, after))
            menu.add_separator()
        else:
            menu.add_command(label="(no saved connections)", state="disabled")
            menu.add_separator()
        menu.add_command(label="Manage connections…", command=self.manage_profiles)
        try:
            widget = parent.winfo_toplevel()
            x = widget.winfo_pointerx()
            y = widget.winfo_pointery()
        except tk.TclError:
            x = y = 100
        menu.tk_popup(x, y)

    def _browse_remote(self, parent: tk.Misc, profile: FtpProfile, var: tk.StringVar,
                       pick_file: bool, after: Optional[Callable[[], Any]]) -> None:
        if not profile.password and profile.user != "anonymous":
            cached = self._session_passwords.get(profile.ref().key)
            if cached is not None:
                profile = replace(profile, password=cached)
            else:
                answer = PasswordDialog(parent, profile.ref()).show()
                if answer is None:
                    return
                password, remember = answer
                profile = replace(profile, password=password)
                self._session_passwords[profile.ref().key] = password
                if remember:
                    for i, p in enumerate(self.profiles):
                        if p.matches(profile.ref()):
                            self.profiles[i] = replace(profile, save_password=True)
                            self.set_profiles(self.profiles)
                            break
        url = RemoteBrowserDialog(parent, self, profile, pick_file).show()
        if url:
            var.set(url)
            if after:
                after()

    # ---------- theme / font ----------
    def set_theme(self, name: str) -> None:
        if name not in THEMES:
            return
        T.clear()
        T.update(THEMES[name])
        self.theme_name = name
        self.theme_var.set(name)
        self.cfg["theme"] = name
        apply_ttk_theme(self.root)
        self.root.configure(bg=T["bg"])
        self._style_chrome()
        for tab in self.tabs():
            tab.apply_theme()
        set_titlebar_dark(self.root, bool(T["dark"]))
        self.root.update_idletasks()

    def toggle_theme(self) -> None:
        names = list(THEMES)
        self.set_theme(names[(names.index(self.theme_name) + 1) % len(names)])

    def change_font(self, delta: int) -> None:
        size = 10 if delta == 0 else max(7, min(32, int(FONTS.mono.cget("size")) + delta))
        FONTS.mono.configure(size=size)
        self.cfg["font_size"] = size

    # ---------- dialogs ----------
    def show_shortcuts(self) -> None:
        messagebox.showinfo("Keyboard shortcuts", (
            "Global\n"
            "  Ctrl+D   New folder compare\n"
            "  Ctrl+N   New file compare\n"
            "  Ctrl+W   Close tab (middle-click also works)\n"
            "  Ctrl +/-/0   Font size (Ctrl+wheel in a pane)\n\n"
            "Folder compare\n"
            "  F5   Re-compare        Enter / double-click   Compare file\n"
            "  Ctrl+→ / Ctrl+←   Copy selection to right / left\n\n"
            "File compare (both panes are editable - just type)\n"
            "  Alt+→ / Alt+←   Copy the current block to right / left\n"
            "  F7 / F8   Previous / next difference\n"
            "  Ctrl+Z   Undo        Ctrl+S   Save        F5   Reload from disk\n"
            "  Ctrl+F   Find in the active pane (the one you last clicked)\n"
            "  F3 / Shift+F3   Next / previous match from the cursor (wraps around)\n"
            "            Enter / Shift+Enter in the find box do the same\n"
            "  Ctrl+→ / Ctrl+←   Move the text cursor one word"))

    def show_about(self) -> None:
        messagebox.showinfo("About", "%s %s\nFolder and file comparison with FTP/FTPS support.\n\n"
                                     "Python %s, Tk %s" % (APP_NAME, APP_VERSION,
                                                           sys.version.split()[0], tk.TkVersion))

    def on_exit(self) -> None:
        for tab in self.tabs():
            if tab is self.home:
                continue
            self.nb.select(tab)
            if not tab.on_close():
                return
        self.cfg["geometry"] = self.root.winfo_geometry()
        self.cfg["ftp_profiles"] = [p.to_dict() for p in self.profiles]
        save_config(self.cfg)
        self.root.destroy()

    # ---------- command line ----------
    def open_paths(self, a: str, b: str) -> None:
        remote = is_remote_path(a) or is_remote_path(b)
        if remote:
            # a remote URL with a file extension is treated as a file, otherwise as a folder
            looks_file = bool(posixpath.splitext(a)[1] or posixpath.splitext(b)[1])
            if looks_file:
                self.new_file_compare(a, b)
            else:
                self.new_folder_compare(a, b)
        elif os.path.isdir(a) and os.path.isdir(b):
            self.new_folder_compare(a, b)
        else:
            self.new_file_compare(a, b)


def main(argv: List[str]) -> None:
    if os.name == "nt":
        try:
            import ctypes
            getattr(ctypes, "windll").shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    cfg = load_config()
    root = tk.Tk()
    app = App(root, cfg)
    args = [a if is_remote_path(a) else os.path.abspath(a) for a in argv[1:] if not a.startswith("-")]
    if len(args) >= 2:
        root.after(100, lambda: app.open_paths(args[0], args[1]))
    root.mainloop()


if __name__ == "__main__":
    main(sys.argv)