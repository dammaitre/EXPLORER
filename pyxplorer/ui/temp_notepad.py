import os
import re
import sys
import time
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from typing import Callable


# ── Lock-file helpers ─────────────────────────────────────────────────────────

def _lock_path(notes_path: Path) -> Path:
    return notes_path.parent / (notes_path.name + ".lock")


def _pid_alive(pid: int) -> bool:
    """Return True if a process with this PID is currently running."""
    try:
        if sys.platform == "win32":
            import ctypes
            SYNCHRONIZE = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if handle == 0:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        else:
            os.kill(pid, 0)
            return True
    except (OSError, PermissionError):
        return False


def _try_acquire_lock(notes_path: Path) -> bool:
    """Write our PID to the lock file and return True, unless another live process holds it."""
    lp = _lock_path(notes_path)
    if lp.exists():
        try:
            pid = int(lp.read_text(encoding="utf-8").strip())
            if pid != os.getpid() and _pid_alive(pid):
                return False          # genuinely locked by another instance
        except (ValueError, OSError):
            pass                      # stale / corrupt lock — overwrite it
    try:
        lp.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    return True


def _release_lock(notes_path: Path) -> None:
    lp = _lock_path(notes_path)
    try:
        if lp.exists():
            pid_text = lp.read_text(encoding="utf-8").strip()
            if int(pid_text) == os.getpid():
                lp.unlink(missing_ok=True)
    except (ValueError, OSError):
        pass

from ..settings import THEME as _T
from .scroll_utils import make_autohide_pack_setter
from ..core.appdirs import pyxplorer_data_dir

_BG_DARK   = _T["bg_dark"]
_TEXT      = _T["text"]
_TEXT_MUTE = _T["text_mute"]
_FONT      = _T["font_family"]
_SZ        = _T["font_size_base"]
_SZ_S      = _T["font_size_small"]
_FONT_MONO = "Consolas"

# ── Markdown theme values ─────────────────────────────────────────────────────
_H_COLOR    = _T.get("md_heading_color",     "#87CEEB")
_H_SIZES    = [
    _T.get("md_h1_size", 22),
    _T.get("md_h2_size", 20),
    _T.get("md_h3_size", 18),
    _T.get("md_h4_size", 16),
    _T.get("md_h5_size", 15),
    _T.get("md_h6_size", 14),
]
_BOLD_COLOR = _T.get("md_bold_color",        "#F0F0F0")
_ITAL_COLOR = _T.get("md_italic_color",      "#D4D4D4")
_CODE_FG    = _T.get("md_code_fg",           "#CE9178")
_CODE_BG    = _T.get("md_code_bg",           "#2A2A2A")
_BQ_COLOR   = _T.get("md_blockquote_color",  "#9D9D9D")
_LINK_COLOR = _T.get("md_link_color",        "#60CDFF")
_HR_COLOR   = _T.get("md_hr_color",          "#4A4A4A")
_LIST_COLOR = _T.get("md_list_marker_color", "#60CDFF")

_SAVE_DELAY_MS         = 250
_AUTO_SAVE_INTERVAL_MS = 10_000
_HIGHLIGHT_DELAY_MS    = 60   # debounce for typing


# ── Inline rules ──────────────────────────────────────────────────────────────
# Each entry: (tag, pattern, marker_ranges_fn)
# marker_ranges_fn(match) → list of (abs_start, abs_end) ranges to elide when clean.
# Return [] to never elide (e.g. code blocks — multiline markers are kept visible).

def _bold_markers(m: re.Match) -> list[tuple[int, int]]:
    return [(m.start(), m.start() + 2), (m.end() - 2, m.end())]

def _italic_markers(m: re.Match) -> list[tuple[int, int]]:
    return [(m.start(), m.start() + 1), (m.end() - 1, m.end())]

def _code_inline_markers(m: re.Match) -> list[tuple[int, int]]:
    return [(m.start(), m.start() + 1), (m.end() - 1, m.end())]

def _link_markers(m: re.Match) -> list[tuple[int, int]]:
    # [label](url) → elide "[" and "](url)"
    label_end = m.start() + 1 + len(m.group(1))
    return [(m.start(), m.start() + 1), (label_end, m.end())]

_INLINE_RULES: list[tuple[str, str, Callable]] = [
    # Code blocks first so bold/italic don't fire inside them
    ("md_code_block",  r"```[\s\S]*?```",                                    lambda _: []),
    ("md_code_inline", r"`[^`\n]+`",                                         _code_inline_markers),
    ("md_bold",        r"\*\*[^\*\n]+\*\*|__[^_\n]+__",                     _bold_markers),
    ("md_italic",      r"(?<!\*)\*(?!\*)(?!\s)[^\*\n]+(?<!\s)\*(?!\*)"
                       r"|(?<!_)_(?!_)(?!\s)[^_\n]+(?<!\s)_(?!_)",          _italic_markers),
    ("md_link",        r"\[([^\]\n]+)\]\([^)\n]+\)",                         _link_markers),
]

_INLINE_TAG_NAMES = [name for name, _, _ in _INLINE_RULES]

_ALL_MD_TAGS = (
    ["md_hide", "md_hr", "md_blockquote", "md_list_marker"]
    + [f"md_h{i}" for i in range(1, 7)]
    + _INLINE_TAG_NAMES
)


class TempNotepad(ttk.Frame):
    def __init__(self, parent, root: tk.Tk, status_cb: Callable[[str], None] | None = None):
        super().__init__(parent, style="LowerContent.TFrame")
        self.root = root
        self._status_cb        = status_cb or (lambda _: None)
        self._save_after:       str | None = None
        self._autosave_after:   str | None = None
        self._highlight_after:  str | None = None
        self._temp_path = self._build_temp_path()
        self._loaded:    bool = False
        self._readonly:  bool = False

        self._build()

    @property
    def temp_path_display(self) -> str:
        return str(self._temp_path)

    # ── Construction ──────────────────────────────────────────────────────────

    def _build(self) -> None:
        header = ttk.Frame(self, style="LowerContent.TFrame")
        header.pack(side=tk.TOP, fill=tk.X)

        self._title_var = tk.StringVar(value="Markdown notes")
        ttk.Label(
            header,
            textvariable=self._title_var,
            anchor="w",
            font=(_FONT, _SZ_S),
            foreground=_TEXT_MUTE,
            padding=(12, 8),
        ).pack(side=tk.LEFT)

        body = ttk.Frame(self, style="LowerContent.TFrame")
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self._text = tk.Text(
            body,
            wrap="word",
            bg=_BG_DARK,
            fg=_TEXT,
            insertbackground=_TEXT,
            selectbackground="#4A4A4A",
            font=(_FONT_MONO, _SZ),
            borderwidth=0,
            highlightthickness=0,
            padx=10,
            pady=8,
            undo=True,
            maxundo=-1,
            autoseparators=True,
        )
        self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        vsb = ttk.Scrollbar(body, orient="vertical", command=self._text.yview)
        set_vsb = make_autohide_pack_setter(vsb, side=tk.RIGHT, fill=tk.Y)
        self._text.configure(yscrollcommand=set_vsb)

        self._configure_md_tags()

        self._text.bind("<KeyRelease>",      self._on_key_release)
        self._text.bind("<ButtonRelease-1>", self._on_click)
        self._text.bind("<Control-c>",       self._copy)
        self._text.bind("<Control-C>",       self._copy)
        self._text.bind("<Control-x>",       self._cut)
        self._text.bind("<Control-X>",       self._cut)
        self._text.bind("<Control-v>",       self._paste)
        self._text.bind("<Control-V>",       self._paste)
        self._text.bind("<Control-a>",       self._select_all)
        self._text.bind("<Control-A>",       self._select_all)

    def _configure_md_tags(self) -> None:
        """Configure all syntax-highlight tags. Configured last = highest priority."""

        # md_hide: makes marked ranges invisible while keeping them in the buffer
        self._text.tag_configure("md_hide", elide=True)

        self._text.tag_configure("md_link",
            foreground=_LINK_COLOR)
        self._text.tag_configure("md_code_block",
            foreground=_CODE_FG, background=_CODE_BG)
        self._text.tag_configure("md_code_inline",
            foreground=_CODE_FG, background=_CODE_BG)
        self._text.tag_configure("md_italic",
            foreground=_ITAL_COLOR, font=(_FONT_MONO, _SZ, "italic"))
        self._text.tag_configure("md_bold",
            foreground=_BOLD_COLOR, font=(_FONT_MONO, _SZ, "bold"))
        self._text.tag_configure("md_hr",
            foreground=_HR_COLOR)
        self._text.tag_configure("md_blockquote",
            foreground=_BQ_COLOR, font=(_FONT_MONO, _SZ, "italic"))
        self._text.tag_configure("md_list_marker",
            foreground=_LIST_COLOR, font=(_FONT_MONO, _SZ, "bold"))

        for level in range(1, 7):
            self._text.tag_configure(
                f"md_h{level}",
                foreground=_H_COLOR,
                font=(_FONT_MONO, _H_SIZES[level - 1], "bold"),
            )

    # ── Public interface ──────────────────────────────────────────────────────

    def load(self) -> None:
        if self._loaded:
            self._text.focus_set()
            return

        self._temp_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._temp_path.exists():
            self._temp_path.write_text("", encoding="utf-8")

        try:
            text = self._temp_path.read_text(encoding="utf-8")
        except Exception as exc:
            self._status_cb(f"Notes read error: {exc}")
            text = ""

        # Try to acquire the lock; fall back to read-only if another instance holds it
        if _try_acquire_lock(self._temp_path):
            self._readonly = False
            self._text.configure(state="normal")
            title_suffix = ""
        else:
            self._readonly = True
            title_suffix = "  [read-only — locked by another instance]"
            self._status_cb("Notepad is read-only: locked by another running instance")

        self._text.delete("1.0", tk.END)
        self._text.insert("1.0", text)
        if self._readonly:
            self._text.configure(state="disabled")
        self._rehighlight()
        self._title_var.set(f"Markdown notes — {self._temp_path}{title_suffix}")
        if not self._readonly:
            self._status_cb(f"Notes loaded: {self._temp_path}")
        self._loaded = True
        self._text.focus_set()
        self._schedule_autosave_loop()

    def shutdown(self) -> None:
        self._cancel_autosave_loop()
        if self._loaded:
            self._save_now()
        _release_lock(self._temp_path)

    def save_and_unlock(self) -> None:
        """Save content and release the lock (panel hide). Autosave keeps running.
        Next load() call will re-acquire the lock fresh."""
        if not self._loaded:
            return  # notepad was never opened — text widget is empty, don't overwrite
        self._save_now()
        _release_lock(self._temp_path)
        self._loaded = False   # force re-acquire on next open

    def focus_editor(self) -> None:
        self._text.focus_set()

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_key_release(self, event=None) -> None:  # noqa: ARG002
        # All key releases: schedule highlight (debounced) + save (debounced).
        # Arrow / Home / End etc. don't modify content, so save just rewrites same bytes —
        # acceptable; we avoid a keysym whitelist that would need constant maintenance.
        self._schedule_highlight(delay=_HIGHLIGHT_DELAY_MS)
        self._schedule_save()

    def _on_click(self, event=None) -> None:  # noqa: ARG002
        # Mouse click moves cursor without modifying text → update immediately so
        # the newly active line uncleans without a visible delay.
        self._schedule_highlight(delay=0)

    # ── Save / autosave ───────────────────────────────────────────────────────

    def _schedule_save(self) -> None:
        if self._save_after is not None:
            try:
                self.after_cancel(self._save_after)
            except Exception:
                pass
        self._save_after = self.after(_SAVE_DELAY_MS, self._save_now)

    def _schedule_autosave_loop(self) -> None:
        self._cancel_autosave_loop()
        self._autosave_after = self.after(_AUTO_SAVE_INTERVAL_MS, self._autosave_tick)

    def _cancel_autosave_loop(self) -> None:
        if self._autosave_after is None:
            return
        try:
            self.after_cancel(self._autosave_after)
        except Exception:
            pass
        self._autosave_after = None

    def _autosave_tick(self) -> None:
        self._autosave_after = None
        if self._readonly:
            # Check if the lock has been freed by the other instance
            if _try_acquire_lock(self._temp_path):
                self._readonly = False
                self._text.configure(state="normal")
                # Reload fresh content now that we own it
                try:
                    text = self._temp_path.read_text(encoding="utf-8")
                    self._text.delete("1.0", tk.END)
                    self._text.insert("1.0", text)
                    self._text.configure(state="normal")
                    self._rehighlight()
                except Exception:
                    pass
                self._title_var.set(f"Markdown notes — {self._temp_path}")
                self._status_cb("Notepad lock acquired — now editable")
        else:
            self._save_now()
        self._schedule_autosave_loop()

    def _save_now(self) -> None:
        self._save_after = None
        if self._readonly:
            return
        text = self._text.get("1.0", "end-1c")
        self._temp_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._temp_path.write_text(text, encoding="utf-8")
            self._status_cb(f"Document saved: {time.strftime('%H:%M:%S')}")
        except Exception as exc:
            self._status_cb(f"Notes write error: {exc}")

    # ── Highlight scheduling ──────────────────────────────────────────────────

    def _schedule_highlight(self, delay: int = _HIGHLIGHT_DELAY_MS) -> None:
        if self._highlight_after is not None:
            try:
                self.after_cancel(self._highlight_after)
            except Exception:
                pass
        self._highlight_after = self.after(delay, self._rehighlight)

    # ── Syntax highlighting ───────────────────────────────────────────────────

    def _rehighlight(self) -> None:
        self._highlight_after = None

        for tag in _ALL_MD_TAGS:
            self._text.tag_remove(tag, "1.0", tk.END)

        content = self._text.get("1.0", "end-1c")
        if not content:
            return

        # Cursor line (0-based logical line index, i.e. \n-separated)
        cursor_line: int = int(self._text.index("insert").split(".")[0]) - 1

        lines = content.split("\n")

        # Precompute absolute char offset of each line start
        line_offsets: list[int] = []
        off = 0
        for line in lines:
            line_offsets.append(off)
            off += len(line) + 1  # +1 for \n

        def to_idx(pos: int) -> str:
            """Convert absolute char offset to tkinter "line.col" index."""
            lo, hi = 0, len(line_offsets) - 1
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if line_offsets[mid] <= pos:
                    lo = mid
                else:
                    hi = mid - 1
            return f"{lo + 1}.{pos - line_offsets[lo]}"

        def hide(abs_start: int, abs_end: int) -> None:
            if abs_start < abs_end:
                self._text.tag_add("md_hide", to_idx(abs_start), to_idx(abs_end))

        # ── Line-level pass ───────────────────────────────────────────────────
        heading_lines: set[int] = set()

        for lineno, line in enumerate(lines):
            ls = f"{lineno + 1}.0"
            le = f"{lineno + 1}.end"
            clean = lineno != cursor_line

            # Headings
            m = re.match(r'^(#{1,6})(?=[ \t]|$)', line)
            if m:
                level = len(m.group(1))
                self._text.tag_add(f"md_h{level}", ls, le)
                heading_lines.add(lineno)
                if clean:
                    # Elide "## " prefix (hashes + optional space/tab)
                    prefix = re.match(r'^#{1,6}[ \t]?', line)
                    plen = prefix.end() if prefix else len(m.group(1))
                    hide(line_offsets[lineno], line_offsets[lineno] + plen)
                continue

            # Horizontal rule  ---  /  ***  /  ___  (markers kept visible — hiding
            # would produce a confusing blank line with no visual cue)
            if re.match(r'^(\*{3,}|-{3,}|_{3,})\s*$', line):
                self._text.tag_add("md_hr", ls, le)
                continue

            # Blockquote
            bq = re.match(r'^(>[ \t]?)', line)
            if bq:
                self._text.tag_add("md_blockquote", ls, le)
                if clean:
                    hide(line_offsets[lineno], line_offsets[lineno] + bq.end())
                continue

            # List marker — style the marker token only; don't elide it
            lm = re.match(r'^(\s*(?:[-*+]|\d+\.)[ \t])', line)
            if lm:
                self._text.tag_add("md_list_marker", ls, f"{lineno + 1}.{lm.end()}")

        # ── Inline pass ───────────────────────────────────────────────────────
        for tag, pattern, get_marker_ranges in _INLINE_RULES:
            flags = re.DOTALL if tag == "md_code_block" else 0
            for m in re.finditer(pattern, content, flags):
                match_start_line = content.count("\n", 0, m.start())

                # Don't overlay inline styles on heading lines
                if tag not in ("md_code_block", "md_code_inline"):
                    if match_start_line in heading_lines:
                        continue

                self._text.tag_add(tag, to_idx(m.start()), to_idx(m.end()))

                # Elide markers only when cursor is not on the match's start line
                if match_start_line != cursor_line:
                    for hs, he in get_marker_ranges(m):
                        hide(hs, he)

    # ── Clipboard operations ──────────────────────────────────────────────────

    def _copy(self, event=None) -> str:
        try:
            selected = self._text.selection_get()
        except Exception:
            selected = ""
        if selected:
            self.root.clipboard_clear()
            self.root.clipboard_append(selected)
        return "break"

    def _cut(self, event=None) -> str:
        try:
            selected = self._text.selection_get()
        except Exception:
            selected = ""
        if selected:
            self.root.clipboard_clear()
            self.root.clipboard_append(selected)
            self._text.delete("sel.first", "sel.last")
            self._on_key_release()
        return "break"

    def _paste(self, event=None) -> str:
        try:
            clipboard = self.root.clipboard_get()
        except Exception:
            clipboard = ""
        if clipboard:
            self._text.insert("insert", clipboard)
            self._on_key_release()
        return "break"

    def _select_all(self, event=None) -> str:
        self._text.tag_add("sel", "1.0", "end-1c")
        self._text.mark_set("insert", "1.0")
        self._text.see("insert")
        return "break"

    # ── Path ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _build_temp_path() -> Path:
        return pyxplorer_data_dir() / "notepad.md"
