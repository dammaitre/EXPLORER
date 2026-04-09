import os
import re
import time
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from typing import Callable

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

# ── Markdown theme values (with fallbacks) ────────────────────────────────────
_H_COLOR   = _T.get("md_heading_color",     "#87CEEB")
_H_SIZES   = [
    _T.get("md_h1_size", 22),
    _T.get("md_h2_size", 20),
    _T.get("md_h3_size", 18),
    _T.get("md_h4_size", 16),
    _T.get("md_h5_size", 15),
    _T.get("md_h6_size", 14),
]
_BOLD_COLOR = _T.get("md_bold_color",       "#F0F0F0")
_ITAL_COLOR = _T.get("md_italic_color",     "#D4D4D4")
_CODE_FG    = _T.get("md_code_fg",          "#CE9178")
_CODE_BG    = _T.get("md_code_bg",          "#2A2A2A")
_BQ_COLOR   = _T.get("md_blockquote_color", "#9D9D9D")
_LINK_COLOR = _T.get("md_link_color",       "#60CDFF")
_HR_COLOR   = _T.get("md_hr_color",         "#4A4A4A")
_LIST_COLOR = _T.get("md_list_marker_color","#60CDFF")

_SAVE_DELAY_MS         = 250
_AUTO_SAVE_INTERVAL_MS = 10_000
_HIGHLIGHT_DELAY_MS    = 80

# ── Inline regex rules (applied in order; earlier rules win visually if
#    tags are configured with descending priority) ────────────────────────────
_INLINE_RULES: list[tuple[str, str]] = [
    # Code blocks before everything so bold/italic don't fire inside them
    ("md_code_block",  r"```[\s\S]*?```"),
    ("md_code_inline", r"`[^`\n]+`"),
    # Bold: **…** or __…__
    ("md_bold",        r"\*\*[^\*\n]+\*\*|__[^_\n]+__"),
    # Italic: *…* or _…_ — must not start/end at another * or _
    ("md_italic",      r"(?<!\*)\*(?!\*)(?!\s)[^\*\n]+(?<!\s)\*(?!\*)"
                       r"|(?<!_)_(?!_)(?!\s)[^_\n]+(?<!\s)_(?!_)"),
    # Links: [label](url)
    ("md_link",        r"\[[^\]\n]+\]\([^)\n]+\)"),
]

# All tag names that _rehighlight manages (cleared on each pass)
_ALL_MD_TAGS = (
    ["md_hr", "md_blockquote", "md_list_marker"]
    + [f"md_h{i}" for i in range(1, 7)]
    + [name for name, _ in _INLINE_RULES]
)


class TempNotepad(ttk.Frame):
    def __init__(self, parent, root: tk.Tk, status_cb: Callable[[str], None] | None = None):
        super().__init__(parent, style="LowerContent.TFrame")
        self.root = root
        self._status_cb       = status_cb or (lambda _: None)
        self._save_after:      str | None = None
        self._autosave_after:  str | None = None
        self._highlight_after: str | None = None
        self._temp_path = self._build_temp_path()
        self._loaded: bool = False

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

        self._text.bind("<KeyRelease>", self._on_change)
        self._text.bind("<Control-c>",  self._copy)
        self._text.bind("<Control-C>",  self._copy)
        self._text.bind("<Control-x>",  self._cut)
        self._text.bind("<Control-X>",  self._cut)
        self._text.bind("<Control-v>",  self._paste)
        self._text.bind("<Control-V>",  self._paste)
        self._text.bind("<Control-a>",  self._select_all)
        self._text.bind("<Control-A>",  self._select_all)

    def _configure_md_tags(self) -> None:
        """Configure all syntax-highlight tags. Later = higher priority."""
        # Inline tags (lower priority — headings will override foreground)
        self._text.tag_configure("md_link",
            foreground=_LINK_COLOR)
        self._text.tag_configure("md_code_block",
            foreground=_CODE_FG, background=_CODE_BG)
        self._text.tag_configure("md_code_inline",
            foreground=_CODE_FG, background=_CODE_BG)
        self._text.tag_configure("md_italic",
            foreground=_ITAL_COLOR,
            font=(_FONT_MONO, _SZ, "italic"))
        self._text.tag_configure("md_bold",
            foreground=_BOLD_COLOR,
            font=(_FONT_MONO, _SZ, "bold"))

        # Line-level tags
        self._text.tag_configure("md_hr",
            foreground=_HR_COLOR)
        self._text.tag_configure("md_blockquote",
            foreground=_BQ_COLOR,
            font=(_FONT_MONO, _SZ, "italic"))
        self._text.tag_configure("md_list_marker",
            foreground=_LIST_COLOR,
            font=(_FONT_MONO, _SZ, "bold"))

        # Heading tags — configured last so they win over inline tags
        for i, (size, ) in enumerate(zip(_H_SIZES), start=0):
            level = i + 1
            self._text.tag_configure(f"md_h{level}",
                foreground=_H_COLOR,
                font=(_FONT_MONO, size, "bold"))

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

        self._text.delete("1.0", tk.END)
        self._text.insert("1.0", text)
        self._rehighlight()
        self._title_var.set(f"Markdown notes — {self._temp_path}")
        self._status_cb(f"Notes loaded: {self._temp_path}")
        self._loaded = True
        self._text.focus_set()
        self._schedule_autosave_loop()

    def shutdown(self) -> None:
        self._cancel_autosave_loop()
        self._save_now()

    def focus_editor(self) -> None:
        self._text.focus_set()

    # ── Change / save / highlight scheduling ─────────────────────────────────

    def _on_change(self, event=None) -> None:
        self._schedule_highlight()
        self._schedule_save()

    def _schedule_highlight(self) -> None:
        if self._highlight_after is not None:
            try:
                self.after_cancel(self._highlight_after)
            except Exception:
                pass
        self._highlight_after = self.after(_HIGHLIGHT_DELAY_MS, self._rehighlight)

    def _schedule_save(self) -> None:
        if self._save_after is not None:
            try:
                self.after_cancel(self._save_after)
            except Exception:
                pass
        self._save_after = self.after(_SAVE_DELAY_MS, self._save_now)

    def _cancel_autosave_loop(self) -> None:
        if self._autosave_after is None:
            return
        try:
            self.after_cancel(self._autosave_after)
        except Exception:
            pass
        self._autosave_after = None

    def _schedule_autosave_loop(self) -> None:
        self._cancel_autosave_loop()
        self._autosave_after = self.after(_AUTO_SAVE_INTERVAL_MS, self._autosave_tick)

    def _autosave_tick(self) -> None:
        self._autosave_after = None
        self._save_now()
        self._schedule_autosave_loop()

    def _save_now(self) -> None:
        self._save_after = None
        text = self._text.get("1.0", "end-1c")
        self._temp_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._temp_path.write_text(text, encoding="utf-8")
            self._status_cb(f"Document saved: {time.strftime('%H:%M:%S')}")
        except Exception as exc:
            self._status_cb(f"Notes write error: {exc}")

    # ── Syntax highlighting ───────────────────────────────────────────────────

    def _rehighlight(self) -> None:
        self._highlight_after = None

        # Clear all managed tags in one pass
        for tag in _ALL_MD_TAGS:
            self._text.tag_remove(tag, "1.0", tk.END)

        content = self._text.get("1.0", "end-1c")
        if not content:
            return

        # Pre-compute byte offset → "line.col" index for fast lookups
        lines = content.split("\n")
        line_offsets: list[int] = []
        off = 0
        for line in lines:
            line_offsets.append(off)
            off += len(line) + 1  # +1 for the \n

        def to_idx(pos: int) -> str:
            # Binary search for the line that contains pos
            lo, hi = 0, len(line_offsets) - 1
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if line_offsets[mid] <= pos:
                    lo = mid
                else:
                    hi = mid - 1
            return f"{lo + 1}.{pos - line_offsets[lo]}"

        # ── Line-level passes ─────────────────────────────────────────────────
        heading_line_set: set[int] = set()  # 0-based line indices that are headings

        for lineno, line in enumerate(lines):
            ls = f"{lineno + 1}.0"
            le = f"{lineno + 1}.end"

            # Headings (1–6)
            m = re.match(r'^(#{1,6})(?=\s|$)', line)
            if m:
                level = len(m.group(1))
                self._text.tag_add(f"md_h{level}", ls, le)
                heading_line_set.add(lineno)
                continue   # headings take priority; skip other line rules

            # Horizontal rule: --- / *** / ___ (3+ chars, optional spaces)
            if re.match(r'^(\*{3,}|-{3,}|_{3,})\s*$', line):
                self._text.tag_add("md_hr", ls, le)
                continue

            # Blockquote
            if re.match(r'^>', line):
                self._text.tag_add("md_blockquote", ls, le)
                continue

            # List marker — highlight only the leading marker token
            m = re.match(r'^(\s*(?:[-*+]|\d+\.)\s)', line)
            if m:
                marker_end = f"{lineno + 1}.{m.end()}"
                self._text.tag_add("md_list_marker", ls, marker_end)

        # ── Inline passes (skip heading lines for bold/italic/link) ───────────
        for tag, pattern in _INLINE_RULES:
            flags = re.DOTALL if tag in ("md_code_block",) else 0
            for m in re.finditer(pattern, content, flags):
                start_line = content.count("\n", 0, m.start())
                # Skip bold/italic/link entirely if they start on a heading line
                if tag in ("md_bold", "md_italic", "md_link"):
                    if start_line in heading_line_set:
                        continue
                self._text.tag_add(tag, to_idx(m.start()), to_idx(m.end()))

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
            self._on_change()
        return "break"

    def _paste(self, event=None) -> str:
        try:
            clipboard = self.root.clipboard_get()
        except Exception:
            clipboard = ""
        if clipboard:
            self._text.insert("insert", clipboard)
            self._on_change()
        return "break"

    def _select_all(self, event=None) -> str:
        self._text.tag_add("sel", "1.0", "end-1c")
        self._text.mark_set("insert", "1.0")
        self._text.see("insert")
        return "break"

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _build_temp_path() -> Path:
        return pyxplorer_data_dir() / "notepad.md"
