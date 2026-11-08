#!/usr/bin/env python3
"""PyCompare - a Beyond Compare style folder / file comparison tool (tkinter, no dependencies, single file).

    python pycompare.py                 # home screen
    python pycompare.py <left> <right>  # two folders -> folder compare, two files -> file compare

Optional window icon: put CompareFiles.ico (Windows) and/or CompareFiles.png next to this script.
With PyInstaller:  pyinstaller --noconsole --onefile --icon CompareFiles.ico ^
                       --add-data "CompareFiles.ico;." --add-data "CompareFiles.png;." pycompare.py

Layout of this file:  1) comparison engine (pure Python)   2) tkinter user interface
"""
from __future__ import annotations

import codecs
import difflib
import fnmatch
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

# ============================================================
#               PART 1 - COMPARISON ENGINE (no UI code)
# ============================================================

IS_WINDOWS = os.name == "nt"

# ============================================================
#               TEXT FILE I/O
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


def _looks_binary(data: bytes) -> bool:
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE, codecs.BOM_UTF8)):
        return False
    return b"\x00" in data[:BINARY_SNIFF_BYTES]


def _decode(data: bytes) -> Tuple[str, str]:
    """Return (text, encoding_name)."""
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16"), "utf-16"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "cp1252"


def _detect_eol(text: str) -> str:
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


def read_text_file(path: Optional[str]) -> TextFile:
    """Load a file. A missing/empty path yields an empty, non-existent TextFile."""
    if not path or not os.path.isfile(path):
        return TextFile(exists=False)
    with open(path, "rb") as fh:
        data = fh.read()
    if _looks_binary(data):
        return TextFile(binary=True, size=len(data), raw=data)
    text, encoding = _decode(data)
    lines, trailing = split_lines(text)
    return TextFile(
        lines=lines,
        encoding=encoding,
        eol=_detect_eol(text),
        trailing_newline=trailing,
        size=len(data),
    )


def write_text_file(path: str, lines: Sequence[str], encoding: str = "utf-8",
                    eol: str = "", trailing_newline: bool = True) -> None:
    """Write lines atomically, keeping the encoding / line ending of the original."""
    sep = {"CRLF": "\r\n", "LF": "\n", "CR": "\r"}.get(eol, "\r\n" if IS_WINDOWS else "\n")
    text = sep.join(lines)
    if lines and trailing_newline:
        text += sep
    data = text.encode(encoding)
    if encoding == "utf-16":
        # str.encode('utf-16') already prepends a BOM
        pass
    tmp = path + ".pycompare.tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


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


# ---------- editing support (map edited pane text back to file lines) ----------

def extract_edited_lines(raw_lines: Sequence[str], pad_flags: Sequence[bool], width_pad: int) -> List[str]:
    """Turn the text of an edited pane back into real file lines.

    raw_lines: every line of the pane; the LAST one is the invisible width-padding line.
    pad_flags[i]: True when line i is an alignment filler (it is empty and still tagged as padding).
    """
    if not raw_lines:
        return []
    body, last = list(raw_lines[:-1]), raw_lines[-1]
    out: List[str] = []
    for i, line in enumerate(body):
        if i < len(pad_flags) and pad_flags[i] and line == "":
            continue
        out.append(line)
    pad = " " * width_pad
    if width_pad and last.startswith(pad):
        last = last[width_pad:]
    elif width_pad and last.endswith(pad):
        last = last[:-width_pad]
    if last != "":
        out.append(last)
    return out


def nearest_line_number(rows: Sequence[Row], display: Sequence[int], d: int, side: str) -> Optional[int]:
    """File line number at display position d (or the closest numbered row if d is a filler row)."""
    if not display:
        return None
    d = max(0, min(d, len(display) - 1))
    for k in list(range(d, len(display))) + list(range(d - 1, -1, -1)):
        r = rows[display[k]]
        n = r.lno if side == "left" else r.rno
        if n is not None:
            return n
    return None


def display_index_for_number(rows: Sequence[Row], display: Sequence[int], side: str, number: int) -> Optional[int]:
    for k, i in enumerate(display):
        r = rows[i]
        if (r.lno if side == "left" else r.rno) == number:
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
#               FOLDER COMPARE
# ============================================================

DEFAULT_IGNORES = (".git", ".svn", ".vs", "__pycache__", "node_modules", "*.pyc", "Thumbs.db", ".DS_Store")

S_SAME, S_DIFF, S_LEFT_ONLY, S_RIGHT_ONLY, S_MISMATCH = (
    "same", "diff", "left_only", "right_only", "mismatch")

CMP_QUICK, CMP_SIZE, CMP_CONTENT = "quick", "size", "content"


@dataclass(frozen=True)
class FolderOptions:
    compare: str = CMP_QUICK                       # quick (size+date) | size | content
    ignore_patterns: Tuple[str, ...] = DEFAULT_IGNORES
    text_opts: DiffOptions = DiffOptions()         # applied to content compare of text files
    mtime_tolerance: float = 2.0                   # seconds (FAT / copy rounding)


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
    counts: dict = field(default_factory=dict)


class Cancelled(Exception):
    pass


def _key(name: str) -> str:
    return name.lower() if IS_WINDOWS else name


def _ignored(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(name.lower() if IS_WINDOWS else name,
                               p.lower() if IS_WINDOWS else p) for p in patterns if p)


@dataclass
class _Node:
    name: str
    path: str
    is_dir: bool
    size: int
    mtime: float


def _scan(path: Optional[str], patterns: Sequence[str]) -> Tuple[dict, str]:
    """Return ({key: _Node}, error_message)."""
    if not path:
        return {}, ""
    out: dict = {}
    try:
        with os.scandir(path) as it:
            for e in it:
                if _ignored(e.name, patterns):
                    continue
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                    st = e.stat(follow_symlinks=False)
                    size, mtime = (0 if is_dir else st.st_size), st.st_mtime
                except OSError:
                    is_dir, size, mtime = False, 0, 0.0
                out[_key(e.name)] = _Node(e.name, e.path, is_dir, size, mtime)
    except OSError as ex:
        return {}, str(ex)
    return out, ""


def _bytes_equal(a: str, b: str) -> bool:
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            ca, cb = fa.read(65536), fb.read(65536)
            if ca != cb:
                return False
            if not ca:
                return True


def _content_equal(a: str, b: str, opts: FolderOptions) -> bool:
    if opts.text_opts.is_default:
        return _bytes_equal(a, b)
    ta, tb = read_text_file(a), read_text_file(b)
    if ta.binary or tb.binary:
        return ta.binary and tb.binary and ta.raw == tb.raw
    return ([normalize_line(x, opts.text_opts) for x in ta.lines]
            == [normalize_line(x, opts.text_opts) for x in tb.lines])


def files_equal(ln: _Node, rn: _Node, opts: FolderOptions) -> bool:
    try:
        if opts.compare == CMP_CONTENT:
            if opts.text_opts.is_default and ln.size != rn.size:
                return False
            return _content_equal(ln.path, rn.path, opts)
        if ln.size != rn.size:
            return False
        if opts.compare == CMP_SIZE:
            return True
        return abs(ln.mtime - rn.mtime) <= opts.mtime_tolerance
    except OSError:
        return False


def compare_folders(left_root: str, right_root: str, opts: FolderOptions = FolderOptions(),
                    progress: Optional[Callable[[int], None]] = None,
                    is_cancelled: Optional[Callable[[], bool]] = None) -> FolderResult:
    """Recursively compare two folders. Either root may be '' to browse one side only."""
    counter = [0]
    patterns = opts.ignore_patterns

    def tick() -> None:
        counter[0] += 1
        if is_cancelled and is_cancelled():
            raise Cancelled()
        if progress and counter[0] % 50 == 0:
            progress(counter[0])

    def build(parts: Tuple[str, ...], lpath: Optional[str], rpath: Optional[str]) -> List[FolderEntry]:
        lnodes, lerr = _scan(lpath, patterns)
        rnodes, rerr = _scan(rpath, patterns)
        entries: List[FolderEntry] = []
        keys = sorted(set(lnodes) | set(rnodes),
                      key=lambda k: (not (lnodes.get(k) or rnodes[k]).is_dir, k.lower()))
        for k in keys:
            tick()
            ln, rn = lnodes.get(k), rnodes.get(k)
            name = (ln or rn).name  # type: ignore[union-attr]
            e = FolderEntry(
                name=name, parts=parts + (name,), is_dir=bool((ln or rn).is_dir),  # type: ignore[union-attr]
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
                elif files_equal(ln, rn, opts):
                    e.status = S_SAME
                else:
                    e.status = S_DIFF
                    e.newer = "left" if ln.mtime > rn.mtime else ("right" if rn.mtime > ln.mtime else None)
            else:
                e.status = S_LEFT_ONLY if ln else S_RIGHT_ONLY
                only = ln or rn
                if only is not None and only.is_dir:
                    e.children = build(e.parts, ln.path if ln else None, rn.path if rn else None)
                    for c in _walk(e.children):
                        c.status = e.status
            entries.append(e)
        if lerr or rerr:
            # surface the error on the parent via a synthetic entry
            entries.append(FolderEntry(name="(cannot read folder)", parts=parts + ("(cannot read folder)",),
                                       is_dir=False, status=S_MISMATCH, error=lerr or rerr))
        return entries

    root = FolderEntry(name="", parts=(), is_dir=True, lpath=left_root or None, rpath=right_root or None)
    root.children = build((), left_root or None, right_root or None)
    root.status = S_SAME if all(c.status == S_SAME for c in root.children) else S_DIFF
    result = FolderResult(left_root, right_root, root)
    result.counts = summarize(root)
    if progress:
        progress(counter[0])
    return result


def _walk(entries: Sequence[FolderEntry]) -> Iterator[FolderEntry]:
    for e in entries:
        yield e
        yield from _walk(e.children)


def summarize(root: FolderEntry) -> dict:
    """File counts per status (folders are not counted)."""
    counts = {S_SAME: 0, S_DIFF: 0, S_LEFT_ONLY: 0, S_RIGHT_ONLY: 0, S_MISMATCH: 0}
    for e in _walk(root.children):
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

def copy_entry(entry: FolderEntry, from_side: str, left_root: str, right_root: str) -> str:
    """Copy an entry from one side to the other. Returns the destination path."""
    if from_side == "left":
        src, dst_root = entry.lpath, right_root
    else:
        src, dst_root = entry.rpath, left_root
    if not src:
        raise FileNotFoundError("Nothing to copy on the %s side." % from_side)
    if not dst_root:
        raise ValueError("The destination folder is not set.")
    dst = os.path.join(dst_root, *entry.parts)
    if os.path.isdir(src):
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    return dst


def delete_path(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


# ============================================================
#               PART 2 - USER INTERFACE
# ============================================================

APP_NAME = "PyCompare"
APP_VERSION = "1.0"
MAX_HISTORY = 15
MAX_RECENT = 12
LARGE_FILE_BYTES = 25 * 1024 * 1024
MAX_INTRALINE_ROWS = 2500
MAX_FIND_MATCHES = 5000

# ============================================================
#               THEMES
# ============================================================

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
        self.ui_family = pick_font(root, ["Segoe UI", "Helvetica Neue", "Noto Sans", "DejaVu Sans"], "TkDefaultFont")
        mono_family = pick_font(
            root, ["Cascadia Mono", "Consolas", "JetBrains Mono", "Menlo", "DejaVu Sans Mono", "Courier New"], "Courier")
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
                    bordercolor=T["panel"], arrowcolor=T["fg"], lightcolor=T["panel2"], darkcolor=T["panel2"],
                    gripcount=0)
        s.map(orient + ".TScrollbar", background=[("active", T["hover"])])
    s.configure("TSeparator", background=T["border"])

    # Drop-down lists of comboboxes created from now on
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
            if windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


# ============================================================
#               CONFIG
# ============================================================

def resource_path(relative: str) -> str:
    """Bundled files (icon) live in PyInstaller's temp folder when frozen."""
    return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), relative)


ICON_NAMES = ("CompareFiles", "pycompare")       # looked up as <name>.ico / <name>.png, first match wins


def apply_window_icon(root: tk.Tk) -> None:
    """Use CompareFiles.ico (Windows) or CompareFiles.png next to the script; silently skip if absent."""
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
                setattr(root, "_icon_ref", img)      # keep a reference so it is not garbage collected
                return
    except Exception:
        pass


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


# ============================================================
#               SMALL HELPERS
# ============================================================

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
    if ts is None:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except (OSError, ValueError, OverflowError):
        return ""


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
        ttk.Button(top, text="⇄ Swap", command=self.swap).grid(row=0, column=3, padx=(8, 0), pady=3, sticky="ew")

        ttk.Label(top, text="Right").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=3)
        self.rcombo = ttk.Combobox(top, textvariable=self.rvar, values=hist)
        self.rcombo.grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Button(top, text="Browse…", command=lambda: self._browse(self.rvar)).grid(
            row=1, column=2, padx=(8, 0), pady=3)
        self.compare_btn = ttk.Button(top, text="Compare  (F5)", style="Accent.TButton", command=self.compare)
        self.compare_btn.grid(row=1, column=3, padx=(8, 0), pady=3, sticky="ew")
        for cb in (self.lcombo, self.rcombo):
            cb.bind("<Return>", lambda _e: self.compare())

        opts = ttk.Frame(self)
        opts.pack(fill="x", padx=12, pady=(2, 4))
        ttk.Label(opts, text="Compare by").pack(side="left", padx=(0, 6))
        self.cmp_combo = ttk.Combobox(opts, textvariable=self.cmp_var, values=list(COMPARE_LABELS),
                                      state="readonly", width=20)
        self.cmp_combo.pack(side="left", padx=(0, 14))
        ttk.Label(opts, text="Ignore").pack(side="left", padx=(0, 6))
        ign = ttk.Entry(opts, textvariable=self.ignore_var, width=34)
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

        status = ttk.Label(self, textvariable=self.status_var, style="Status.TLabel", anchor="w")
        status.pack(side="bottom", fill="x")

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

    def apply_theme(self) -> None:
        for status, key in ((S_SAME, "s_same"), (S_DIFF, "s_diff"), (S_LEFT_ONLY, "s_left"),
                            (S_RIGHT_ONLY, "s_right"), (S_MISMATCH, "s_mismatch")):
            self.tree.tag_configure(status, foreground=T[key])
        style_menu(self.menu)
        for cb in (self.lcombo, self.rcombo, self.cmp_combo, self.ws_combo):
            retheme_combobox(cb)

    # ---------- actions ----------
    def _browse(self, var: tk.StringVar) -> None:
        start = var.get() if os.path.isdir(var.get()) else None
        folder = filedialog.askdirectory(title="Select folder", initialdir=start)
        if folder:
            var.set(os.path.normpath(folder))

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

    def compare(self) -> None:
        left, right = self.lvar.get().strip(), self.rvar.get().strip()
        if not left and not right:
            messagebox.showwarning("Folders Required", "Choose a left and a right folder first.")
            return
        for label, path in (("Left", left), ("Right", right)):
            if path and not os.path.isdir(path):
                messagebox.showerror("Folder Not Found", "%s folder does not exist:\n\n%s" % (label, path))
                return

        opts = self.folder_options()
        self.app.cfg["folder_opts"] = {
            "compare": opts.compare, "ignore": self.ignore_var.get(),
            "ws": opts.text_opts.ignore_whitespace, "case": opts.text_opts.ignore_case,
        }
        if left:
            self.app.remember_path("folder", left)
        if right:
            self.app.remember_path("folder", right)
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
        self.status_var.set("Comparing…")
        t0 = time.time()

        def worker() -> None:
            try:
                res = compare_folders(left, right, opts, progress=lambda n: q.put(("progress", n)),
                                          is_cancelled=cancel.is_set)
                q.put(("done", res))
            except Cancelled:
                q.put(("cancelled", None))
            except Exception as ex:             # pragma: no cover - surfaced to the user
                q.put(("error", str(ex)))

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
                    self.status_var.set("Compare failed.")
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
        left_root, right_root = self.lvar.get().strip(), self.rvar.get().strip()
        lp = e.lpath or (os.path.join(left_root, *e.parts) if left_root else "")
        rp = e.rpath or (os.path.join(right_root, *e.parts) if right_root else "")
        self.app.new_file_compare(lp, rp)

    def copy_selected(self, from_side: str) -> None:
        ents = [e for e in self._selected_top() if (e.lpath if from_side == "left" else e.rpath)]
        if not ents:
            return
        to_side = "right" if from_side == "left" else "left"
        left_root, right_root = self.lvar.get().strip(), self.rvar.get().strip()
        if not (right_root if from_side == "left" else left_root):
            messagebox.showwarning("Copy", "The %s folder is not set." % to_side)
            return
        overwrite = sum(1 for e in ents if (e.rpath if from_side == "left" else e.lpath))
        msg = "Copy %d item(s) from %s to %s?" % (len(ents), from_side, to_side)
        if overwrite:
            msg += "\n\n%d existing item(s) will be overwritten." % overwrite
        if not messagebox.askyesno("Copy", msg):
            return
        errors = []
        for e in ents:
            try:
                copy_entry(e, from_side, left_root, right_root)
            except Exception as ex:
                errors.append("%s: %s" % (e.rel, ex))
        if errors:
            messagebox.showerror("Copy Failed", "\n".join(errors[:10]))
        self.compare()

    def delete_selected(self, side: str) -> None:
        ents = [e for e in self._selected_top() if (e.lpath if side == "left" else e.rpath)]
        if not ents:
            return
        names = "\n".join("  " + e.rel for e in ents[:8]) + ("\n  …" if len(ents) > 8 else "")
        if not messagebox.askyesno(
                "Delete", "PERMANENTLY delete %d item(s) from the %s side?\n\n%s\n\nThis cannot be undone."
                % (len(ents), side, names), icon="warning"):
            return
        errors = []
        for e in ents:
            try:
                delete_path((e.lpath if side == "left" else e.rpath) or "")
            except OSError as ex:
                errors.append("%s: %s" % (e.rel, ex))
        if errors:
            messagebox.showerror("Delete Failed", "\n".join(errors[:10]))
        self.compare()

    def reveal(self, side: str) -> None:
        for e in self._selected()[:1]:
            path = e.lpath if side == "left" else e.rpath
            if path:
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
        names = [os.path.basename(p.rstrip("/\\")) or p for p in (self.lvar.get().strip(), self.rvar.get().strip()) if p]
        return "📁 " + (" ↔ ".join(names) if names else "Folder compare")


# ============================================================
#               FILE COMPARE TAB
# ============================================================

class FileTab(ttk.Frame):
    kind = "file"

    def __init__(self, app: "App", left: str = "", right: str = "") -> None:
        super().__init__(app.nb)
        self.app = app
        fo = app.cfg.get("file_opts", {})

        self.lpath = tk.StringVar(value=left)
        self.rpath = tk.StringVar(value=right)
        self.linfo = tk.StringVar()
        self.rinfo = tk.StringVar()
        self.status_var = tk.StringVar()
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
        self.edit_var = tk.BooleanVar(value=False)
        self.leol = tk.StringVar(value="Auto")
        self.reol = tk.StringVar(value="Auto")
        self._edit_job: Optional[str] = None
        self._edit_sides: set = set()
        self._burst_snapshot = False
        self._painting = False
        self._wpad = 1
        self._pre_edit_view: Optional[tuple] = None

        self.lf = TextFile(exists=False)
        self.rf = TextFile(exists=False)
        self.left_lines: List[str] = []
        self.right_lines: List[str] = []
        self.dirty = {"left": False, "right": False}
        self.undo_stack: List[tuple] = []
        self.rows: List[Row] = []
        self.hunks: List[Hunk] = []
        self.display: List[int] = []
        self.nav_index = -1
        self.binary = False
        self._ysync = False
        self._xsync = False
        self._flash_job: Optional[str] = None
        self._matches: List[tuple] = []
        self._match_pos = -1
        self._find_query = ""

        self.keymap: Dict[str, Callable[[], Any]] = {
            "<F5>": self.reload, "<F7>": self.prev_change, "<F8>": self.next_change,
            "<Control-s>": self.save, "<Control-z>": self.undo, "<Control-f>": self.focus_find,
            "<Control-Right>": lambda: self.copy_block("ltr"),
            "<Control-Left>": lambda: self.copy_block("rtl"),
            "<Alt-Right>": lambda: self.copy_block("ltr"),
            "<Alt-Left>": lambda: self.copy_block("rtl"),
        }

        self._build()
        self.apply_theme()
        self.load()

    # ---------- UI ----------
    def _build(self) -> None:
        # Row 1: navigation / merge
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=(10, 0))
        for text, value in (("All", "all"), ("Diffs", "diffs")):
            ttk.Radiobutton(bar, text=text, value=value, variable=self.mode_var, style="Toolbutton",
                            command=self.repaint).pack(side="left", padx=(0, 2))
        ttk.Checkbutton(bar, text="+ Context", variable=self.ctx_var, style="Toolbutton",
                        command=self._on_ctx_toggle).pack(side="left", padx=(0, 4))
        ttk.Checkbutton(bar, text="✎ Edit", variable=self.edit_var, style="Toolbutton",
                        command=self._on_edit_toggle).pack(side="left", padx=(0, 10))
        ttk.Button(bar, text="◀ Prev  (F7)", command=self.prev_change).pack(side="left", padx=(0, 3))
        ttk.Button(bar, text="Next ▶  (F8)", command=self.next_change).pack(side="left", padx=(0, 12))
        self.btn_rtl = ttk.Button(bar, text="← Copy block", command=lambda: self.copy_block("rtl"))
        self.btn_rtl.pack(side="left", padx=(0, 3))
        self.btn_ltr = ttk.Button(bar, text="Copy block →", command=lambda: self.copy_block("ltr"))
        self.btn_ltr.pack(side="left", padx=(0, 3))
        self.btn_undo = ttk.Button(bar, text="Undo", command=self.undo)
        self.btn_undo.pack(side="left", padx=(0, 12))
        self.btn_save = ttk.Button(bar, text="Save  (Ctrl+S)", command=self.save)
        self.btn_save.pack(side="left", padx=(0, 3))
        ttk.Button(bar, text="⇄ Swap", command=self.swap).pack(side="left", padx=(0, 3))
        ttk.Button(bar, text="Reload  (F5)", command=self.reload).pack(side="left")

        find = ttk.Frame(bar)
        find.pack(side="right")
        ttk.Label(find, text="Find").pack(side="left", padx=(0, 4))
        self.find_entry = ttk.Entry(find, textvariable=self.find_var, width=22)
        self.find_entry.pack(side="left")
        self.find_entry.bind("<Return>", lambda _e: self.find_next(1))
        self.find_entry.bind("<Shift-Return>", lambda _e: self.find_next(-1))
        ttk.Button(find, text="▲", width=3, command=lambda: self.find_next(-1)).pack(side="left", padx=(3, 0))
        ttk.Button(find, text="▼", width=3, command=lambda: self.find_next(1)).pack(side="left", padx=(2, 0))

        # Row 2: options
        opt = ttk.Frame(self)
        opt.pack(fill="x", padx=10, pady=(6, 0))
        self.ws_combo = ttk.Combobox(opt, textvariable=self.ws_var, values=list(WS_LABELS), state="readonly",
                                     width=24)
        self.ws_combo.pack(side="left", padx=(0, 8))
        self.ws_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_opts_change())
        ttk.Checkbutton(opt, text="Ignore case", variable=self.case_var, command=self._on_opts_change).pack(
            side="left", padx=(0, 14))
        ttk.Label(opt, text="Context lines:").pack(side="left", padx=(0, 6))
        ttk.Label(opt, text="Before").pack(side="left", padx=(0, 3))
        make_spin(opt, self.before, self.repaint).pack(side="left", padx=(0, 8))
        ttk.Label(opt, text="After").pack(side="left", padx=(0, 3))
        make_spin(opt, self.after_, self.repaint).pack(side="left", padx=(0, 14))
        ttk.Label(opt, text="Comment-only changes:").pack(side="left", padx=(0, 6))
        ttk.Label(opt, text="Before").pack(side="left", padx=(0, 3))
        make_spin(opt, self.cbefore, self.repaint).pack(side="left", padx=(0, 8))
        ttk.Label(opt, text="After").pack(side="left", padx=(0, 3))
        make_spin(opt, self.cafter, self.repaint).pack(side="left", padx=(0, 10))
        ttk.Label(opt, text="Comment prefixes").pack(side="left", padx=(0, 3))
        pe = ttk.Entry(opt, textvariable=self.prefix_var, width=9)
        pe.pack(side="left")
        pe.bind("<Return>", lambda _e: self.repaint())
        pe.bind("<FocusOut>", lambda _e: self.repaint())

        # status bar first so it never gets pushed out
        ttk.Label(self, textvariable=self.status_var, style="Status.TLabel", anchor="w").pack(side="bottom", fill="x")

        # Panes
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
            w.bind("<Control-End>", self._ctrl_end)
            w.bind("<Button-1>", lambda e: e.widget.focus_set(), add="+")      # focus on click (copy / edit)
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
        ttk.Button(box, text="Browse…", command=lambda: self._browse(side)).grid(row=0, column=1, padx=(6, 0))
        eol = ttk.Combobox(box, textvariable=eol_var, values=("Auto", "CRLF", "LF", "CR"), state="readonly", width=6)
        eol.grid(row=0, column=2, padx=(6, 0))
        eol.bind("<<ComboboxSelected>>", lambda _e: self._on_eol_change(side))
        ttk.Label(box, textvariable=info, style="Muted.TLabel").grid(row=1, column=0, columnspan=3, sticky="w",
                                                                    pady=(2, 0))
        setattr(self, side + "_combo", combo)
        setattr(self, side + "_eol_combo", eol)

    def _make_text(self, parent: tk.Misc) -> tk.Text:
        w = tk.Text(parent, font=FONTS.mono, wrap="none", relief="flat", bd=0, highlightthickness=1,
                    padx=8, pady=6, undo=False, cursor="arrow", takefocus=0)
        w.configure(state="disabled")
        return w

    def _make_gutter(self, parent: tk.Misc) -> tk.Text:
        w = tk.Text(parent, font=FONTS.mono, wrap="none", relief="flat", bd=0, highlightthickness=0,
                    padx=6, pady=6, width=5, cursor="arrow", takefocus=0)
        w.configure(state="disabled")
        return w

    def apply_theme(self) -> None:
        for w in (self.left, self.right):
            w.configure(bg=T["text_bg"], fg=T["text_fg"], selectbackground=T["sel"], inactiveselectbackground=T["sel"],
                        highlightbackground=T["border"], highlightcolor=T["accent"], insertbackground=T["fg"])
            w.tag_configure("equal", background=T["text_bg"], foreground=T["text_fg"])
            w.tag_configure("pad", background=T["pad_bg"], foreground=T["pad_fg"])
            w.tag_configure("widthpad", background=T["text_bg"])
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
        start = os.path.dirname(var.get()) if var.get() else None
        path = filedialog.askopenfilename(title="Select %s file" % side, initialdir=start or None)
        if path:
            self._flush_edit()
            if not self._confirm_discard():
                return
            var.set(os.path.normpath(path))
            self.load()

    def _confirm_discard(self) -> bool:
        if not (self.dirty["left"] or self.dirty["right"]):
            return True
        return messagebox.askyesno("Unsaved Changes", "Discard your unsaved merge changes?")

    def reload(self) -> None:
        self._flush_edit()
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
            if p and os.path.isfile(p) and os.path.getsize(p) > LARGE_FILE_BYTES:
                if not messagebox.askyesno("Large File", "%s is larger than %d MB.\nComparing it may be slow. Continue?"
                                           % (os.path.basename(p), LARGE_FILE_BYTES // (1024 * 1024))):
                    return
        try:
            self.lf = read_text_file(lp)
            self.rf = read_text_file(rp)
        except OSError as ex:
            messagebox.showerror("Read Failed", str(ex))
            return
        for p in (lp, rp):
            if p:
                self.app.remember_path("file", p)
        self.app.add_recent("file", lp, rp)
        self.left_lines, self.right_lines = list(self.lf.lines), list(self.rf.lines)
        self.dirty = {"left": False, "right": False}
        self.undo_stack.clear()
        self.nav_index = -1
        self.binary = self.lf.binary or self.rf.binary
        self.recompute()
        self.after_idle(self.next_change)

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
            self.rows, self.hunks, self.display = [], [], []
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

    def _on_ctx_toggle(self) -> None:
        if self.ctx_var.get() and self.mode_var.get() == "all":
            self.mode_var.set("diffs")
        self.repaint()

    def repaint(self) -> None:
        if self.binary:
            return
        self._flush_edit()
        self._save_options()
        # While editing, always show every row so pane lines map 1:1 back to the file
        self.display = list(range(len(self.rows))) if self.editing else visible_rows(self.rows, self.hunks, self._view())
        width = 0
        for i in self.display:
            r = self.rows[i]
            width = max(width, len((r.ltext or "").expandtabs()), len((r.rtext or "").expandtabs()))
        digits = len(str(max(len(self.left_lines), len(self.right_lines), 1)))
        self._wpad = max(width, 1)
        self._fill(self.left, self.gl, "left", width, digits)
        self._fill(self.right, self.gr, "right", width, digits)
        self._apply_intraline()
        self._clear_find()
        self._draw_overview()
        self._update_info()

    def _fill(self, text: tk.Text, gutter: tk.Text, side: str, width: int, digits: int) -> None:
        editing = self.editing and not self.binary
        self._painting = True
        try:
            text.configure(state="normal")
            gutter.configure(state="normal", width=digits + 1)
            text.delete("1.0", "end")
            gutter.delete("1.0", "end")
            buf: List[str] = []
            cur: Optional[str] = None
            nums: List[str] = []
            left = side == "left"
            for i in self.display:
                r = self.rows[i]
                t = r.ltext if left else r.rtext
                tag = r.ltag if left else r.rtag
                no = r.lno if left else r.rno
                nums.append("" if no is None else str(no))
                if tag != cur and buf:
                    text.insert("end", "\n".join(buf) + "\n", cur or "equal")
                    buf = []
                cur = tag
                buf.append(t or "")
            if buf:
                text.insert("end", "\n".join(buf) + "\n", cur or "equal")
            # Last line: spaces that equalise the scroll width of both panes (never part of the file)
            text.insert("end", " " * max(width, 1), ("equal", "widthpad"))
            if nums:
                gutter.insert("end", "\n".join(nums) + "\n", "gnum")
            text.configure(state="normal" if editing else "disabled", cursor="xterm" if editing else "arrow")
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
        for w, f in ((self.left, self.lf), (self.right, self.rf)):
            w.configure(state="normal")
            w.delete("1.0", "end")
            w.insert("end", "\n  Binary comparison\n  %s\n\n  %s" % (
                f.describe(), "Files are identical." if same else "Files differ."), "equal")
            w.configure(state="disabled")
        for g in (self.gl, self.gr):
            g.configure(state="normal")
            g.delete("1.0", "end")
            g.configure(state="disabled")
        self.overview.delete("all")

    def _update_info(self) -> None:
        default_eol = "CRLF" if IS_WINDOWS else "LF"

        def side_text(meta: TextFile, path_var: tk.StringVar, side: str, eol_var: tk.StringVar) -> str:
            if not meta.exists:
                base = "(new file)" if path_var.get().strip() else "(no file)"
            else:
                base = meta.describe()
                if meta.eol == "Mixed" and eol_var.get() == "Auto":
                    base += "  → saves as " + default_eol
            if eol_var.get() != "Auto":
                base += "  → saves as " + eol_var.get()
            return base + ("   ● modified" if self.dirty[side] else "")

        self.linfo.set(side_text(self.lf, self.lpath, "left", self.leol))
        self.rinfo.set(side_text(self.rf, self.rpath, "right", self.reol))
        if self.binary:
            self.status_var.set("Binary files — " + ("identical" if self.lf.raw == self.rf.raw else "different"))
        elif self.hunks:
            self.status_var.set("%d difference block(s)" % len(self.hunks))
        elif self.left_lines == self.right_lines:
            self.status_var.set("Files are identical")
        else:
            self.status_var.set("No differences under the current options (whitespace / case ignored)")
        can_merge = not self.binary
        for b in (self.btn_ltr, self.btn_rtl):
            b.state(["!disabled"] if can_merge else ["disabled"])
        self.btn_undo.state(["!disabled"] if self.undo_stack else ["disabled"])
        self.btn_save.state(["!disabled"] if (self.dirty["left"] or self.dirty["right"]) else ["disabled"])
        if self.editing and not self.binary:
            self.status_var.set(self.status_var.get() + "   |   edit mode: type in either pane (re-compares when you pause)")
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
        top = max(0, min(top, len(self.display) - 1))
        full_top = self.display[top] if self.display else 0
        for n, (_h0, h1) in enumerate(self.hunks):
            if h1 >= full_top:
                return n
        return len(self.hunks) - 1

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
        self.leol.set(re_)
        self.reol.set(le)
        self.lpath.set(rp)
        self.rpath.set(lp)
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
        path = var.get().strip()
        if not path:
            path = filedialog.asksaveasfilename(title="Save %s file as" % side)
            if not path:
                return False
            var.set(os.path.normpath(path))
            path = var.get()
        meta = self.lf if side == "left" else self.rf
        other = self.rf if side == "left" else self.lf
        src = meta if meta.exists else (other if other.exists and not other.binary else meta)
        lines = self.left_lines if side == "left" else self.right_lines
        eol_var = self.leol if side == "left" else self.reol
        eol = src.eol if src.exists else ""
        if eol_var.get() in ("CRLF", "LF", "CR"):
            eol = eol_var.get()
        try:
            write_text_file(path, lines, src.encoding if src.exists else "utf-8",
                            eol, src.trailing_newline if src.exists else True)
            fresh = read_text_file(path)
        except OSError as ex:
            messagebox.showerror("Save Failed", "%s\n\n%s" % (path, ex))
            return False
        if side == "left":
            self.lf = fresh
        else:
            self.rf = fresh
        self.dirty[side] = False
        eol_var.set("Auto")
        self.app.remember_path("file", path)
        return True

    # ---------- editing ----------
    @property
    def editing(self) -> bool:
        return bool(self.edit_var.get())

    def _on_opts_change(self) -> None:
        self._flush_edit()
        self.recompute()

    def _on_eol_change(self, side: str) -> None:
        self.dirty[side] = True
        self._update_info()

    def _on_edit_toggle(self) -> None:
        if self.editing and self.binary:
            self.edit_var.set(False)
            return
        if self.editing:
            self._pre_edit_view = (self.mode_var.get(), bool(self.ctx_var.get()))
            self.mode_var.set("all")
            self.ctx_var.set(False)
        else:
            self._flush_edit()
            if self._pre_edit_view:
                mode, ctx = self._pre_edit_view
                self.mode_var.set(mode)
                self.ctx_var.set(ctx)
                self._pre_edit_view = None
        self.repaint()
        if self.editing:
            self.left.focus_set()

    def _on_modified(self, side: str) -> None:
        w = self.left if side == "left" else self.right
        if not w.edit_modified():
            return                                  # the reset we do ourselves also raises this event
        w.edit_modified(False)
        if self._painting or not self.editing or self.binary:
            return
        self._schedule_edit(side)

    def _schedule_edit(self, side: str) -> None:
        self._edit_sides.add(side)
        if self._edit_job is None:
            # first keystroke of a burst: remember the state so Ctrl+Z can take the whole burst back
            self.undo_stack.append((list(self.left_lines), list(self.right_lines), dict(self.dirty)))
            del self.undo_stack[:-50]
            self._burst_snapshot = True
        else:
            self.after_cancel(self._edit_job)
        self._edit_job = self.after(700, self._commit_edit)

    def _flush_edit(self) -> None:
        if self._edit_job is not None:
            self.after_cancel(self._edit_job)
            self._commit_edit()

    def _extract_lines(self, side: str) -> List[str]:
        w = self.left if side == "left" else self.right
        raw = str(w.get("1.0", "end-1c")).split("\n")
        flags = [False] * max(0, len(raw) - 1)
        for i in range(len(flags)):
            if raw[i] == "" and "pad" in w.tag_names("%d.0" % (i + 1)):
                flags[i] = True
        return extract_edited_lines(raw, flags, self._wpad)

    def _cursor_anchor(self) -> Optional[tuple]:
        try:
            w = self.focus_get()
        except (KeyError, tk.TclError):
            return None
        if w is not self.left and w is not self.right:
            return None
        side = "left" if w is self.left else "right"
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
        w.mark_set("insert", "%d.%d" % (d + 1, col))
        w.see("insert")
        w.focus_set()

    def _commit_edit(self) -> None:
        """Read the edited pane text back into the line lists, then re-diff and re-align."""
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
                self.recompute()
                self._restore_anchor(anchor, y)
        if not changed and took_snapshot and self.undo_stack:
            self.undo_stack.pop()

    def _ctrl_end(self, event: Any) -> Optional[str]:
        if not self.editing:
            return None
        event.widget.mark_set("insert", "%d.end" % len(self.display) if self.display else "1.0")
        event.widget.see("insert")
        return "break"

    # ---------- find ----------
    def focus_find(self) -> None:
        self.find_entry.focus_set()
        self.find_entry.selection_range(0, "end")

    def _clear_find(self) -> None:
        self._matches, self._match_pos, self._find_query = [], -1, ""

    def _collect_matches(self, query: str) -> List[tuple]:
        out: List[tuple] = []
        count = tk.IntVar(self)
        for w in (self.left, self.right):
            w.tag_remove("find", "1.0", "end")
            w.tag_remove("find_cur", "1.0", "end")
            pos = "1.0"
            while len(out) < MAX_FIND_MATCHES:
                idx = w.search(query, pos, stopindex="end", nocase=True, count=count)
                if not idx:
                    break
                end = "%s+%dc" % (idx, max(1, count.get()))
                w.tag_add("find", idx, end)
                line, col = str(idx).split(".")
                out.append((int(line), 0 if w is self.left else 1, int(col), w, str(idx), end))
                pos = end
        out.sort(key=lambda m: m[:3])
        return out

    def find_next(self, direction: int) -> None:
        query = self.find_var.get()
        if not query:
            return
        if query != self._find_query:
            self._matches = self._collect_matches(query)
            self._find_query = query
            self._match_pos = -1
        if not self._matches:
            self.status_var.set("Not found: " + query)
            return
        for m in self._matches:
            m[3].tag_remove("find_cur", "1.0", "end")
        self._match_pos = (self._match_pos + direction) % len(self._matches)
        _line, _pane, _col, w, idx, end = self._matches[self._match_pos]
        w.tag_add("find_cur", idx, end)
        w.see(idx)
        self.status_var.set("Match %d of %d" % (self._match_pos + 1, len(self._matches)))

    # ---------- tab protocol ----------
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
        a = os.path.basename(self.lpath.get().strip())
        b = os.path.basename(self.rpath.get().strip())
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
        ttk.Label(center, text="Compare folders and files, merge differences, work in dark or light.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 18))

        btns = ttk.Frame(center)
        btns.pack(anchor="w")
        ttk.Button(btns, text="📁  Folder Compare   (Ctrl+D)", style="Accent.TButton",
                   command=lambda: app.new_folder_compare()).pack(side="left", padx=(0, 10))
        ttk.Button(btns, text="📄  File Compare   (Ctrl+N)", style="Accent.TButton",
                   command=lambda: app.new_file_compare()).pack(side="left")

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
        root.geometry(cfg.get("geometry", "1450x880"))
        root.minsize(1000, 620)

        name = cfg.get("theme", DEFAULT_THEME)
        T.clear()
        T.update(THEMES.get(name, THEMES[DEFAULT_THEME]))
        self.theme_name = name if name in THEMES else DEFAULT_THEME
        FONTS = Fonts(root, int(cfg.get("font_size", 10)))
        apply_ttk_theme(root)
        root.configure(bg=T["bg"])

        self._build_chrome()
        self.home = HomeTab(self)
        self.nb.add(self.home, text=self.home.title())

        for seq in ("<F5>", "<F7>", "<F8>", "<Control-s>", "<Control-z>", "<Control-f>",
                    "<Control-Right>", "<Control-Left>", "<Alt-Right>", "<Alt-Left>"):
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
        tab = self.current_tab()
        # In edit mode Ctrl+arrows move the text cursor word by word (Alt+arrows copy blocks instead)
        if isinstance(event.widget, tk.Text) and getattr(tab, "editing", False) \
                and seq in ("<Control-Left>", "<Control-Right>"):
            return None
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
            "File compare\n"
            "  F7 / F8   Previous / next difference\n"
            "  Alt+→ / Alt+←   Copy block to right / left (Ctrl+→/← too, when not editing)\n"
            "  ✎ Edit button   Type directly in either pane; re-compares when you pause\n"
            "  Ctrl+Z   Undo merge / edit        Ctrl+S   Save\n"
            "  Ctrl+F   Find (Enter = next, Shift+Enter = previous)\n"
            "  F5   Reload from disk"))

    def show_about(self) -> None:
        messagebox.showinfo("About", "%s %s\nFolder and file comparison utility." % (APP_NAME, APP_VERSION))

    def on_exit(self) -> None:
        for tab in self.tabs():
            if tab is self.home:
                continue
            self.nb.select(tab)
            if not tab.on_close():
                return
        self.cfg["geometry"] = self.root.winfo_geometry()
        save_config(self.cfg)
        self.root.destroy()

    # ---------- command line ----------
    def open_paths(self, a: str, b: str) -> None:
        if os.path.isdir(a) and os.path.isdir(b):
            self.new_folder_compare(a, b)
        elif os.path.isfile(a) or os.path.isfile(b) or not (os.path.isdir(a) or os.path.isdir(b)):
            self.new_file_compare(a, b)
        else:
            messagebox.showerror("Cannot Compare", "Pass two folders or two files.")


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
    args = [os.path.abspath(a) for a in argv[1:] if not a.startswith("-")]
    if len(args) >= 2:
        root.after(100, lambda: app.open_paths(args[0], args[1]))
    root.mainloop()


if __name__ == "__main__":
    main(sys.argv)
