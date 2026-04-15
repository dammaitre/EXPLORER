"""
Phase 3 — Main frame: directory listing with Name / Size / % columns.
Phase 5 — update_item_size / finalize_pct wired to async scanner.
"""
import os
import sys
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from ..core.longpath import normalize, to_display
from ..core.fs import fmt_size, copy_items, move_items
from ..core import starred as _starred
from ..core import tags as _tags
from ..settings import THEME as _T, EXPR_SKIPPED, SCROLL_SPEED
from .scroll_utils import make_autohide_pack_setter

try:
    _tkdnd2 = __import__("tkinterdnd2", fromlist=["DND_FILES"])
    DND_FILES = getattr(_tkdnd2, "DND_FILES", None)
    COPY = getattr(_tkdnd2, "COPY", "copy")
    MOVE = getattr(_tkdnd2, "MOVE", "move")
except Exception:
    DND_FILES = None
    COPY = "copy"
    MOVE = "move"

_BG       = _T["bg"]
_TEXT     = _T["text"]
_TEXT_DIM = _T["text_mute"]
_TEXT_DIR = _T["text"]
_ACCENT   = _T["accent"]
_ROW_H    = _T["row_hover"]
_ROW_SEL  = _T["row_selected"]
_DENIED   = "#7A4040"

_MAX_ROWS = 500  # Phase 10 cap: show first 500, then a "load more" sentinel
_SCROLL_SPEED = SCROLL_SPEED


class MainFrame(ttk.Frame):
    def __init__(self, parent, state, navigate_cb,
                 on_select_cb: Callable | None = None,
                 status_cb: Callable[[str], None] | None = None,
                 transfer_start_cb: Callable[[str], None] | None = None,
                 transfer_progress_cb: Callable[[int], None] | None = None,
                 transfer_stop_cb: Callable[[], None] | None = None,
                 icons: dict | None = None):
        super().__init__(parent, style="TFrame")
        self.state = state
        self.navigate_cb = navigate_cb
        self.on_select_cb = on_select_cb
        self.status_cb = status_cb or (lambda message: None)
        self.transfer_start_cb = transfer_start_cb
        self.transfer_progress_cb = transfer_progress_cb
        self.transfer_stop_cb = transfer_stop_cb
        self._icons = icons or {}

        self._current_path: str = ""
        self._item_data:  dict = {}   # iid  → row dict
        self._path_iids:  dict = {}   # normcase(path) → iid   (for O(1) size updates)
        self._all_rows:   list = []   # full row list for re-sorting
        self._sort_col:   str  = "name"
        self._sort_asc:   bool = True
        self._hidden_rows: list = []
        self._more_iid:   str | None = None
        self._pending_select: str | None = None   # path to pre-select after next render
        self._selection_anchor: str | None = None
        self._last_selected_iid: str | None = None
        self._drop_busy: bool = False
        self._dnd_enabled: bool = False
        self._drop_hover_iid: str | None = None
        self._alt_drag_intent: bool = False
        self._suppress_release_click: bool = False
        self._folder_shortcut_cache: dict[str, str | None] = {}

        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self._tree = ttk.Treeview(
            self,
            columns=("aka", "star", "heur", "size", "pct"),
            selectmode="extended",
        )

        # ── Headings ──────────────────────────────────────────────────
        self._tree.heading("#0",    text="Name",  anchor="w",
                           command=lambda: self._sort_by("name"))
        self._tree.heading("aka",   text="",      anchor="e")
        self._tree.heading("star",  text="",      anchor="center")
        self._tree.heading("size",  text="Size",  anchor="e",
                           command=lambda: self._sort_by("size"))
        self._tree.heading("pct",   text="%",     anchor="e",
                           command=lambda: self._sort_by("pct"))
        self._tree.heading("heur", text="", anchor="w")

        # ── Columns ───────────────────────────────────────────────────
        self._tree.column("#0",   stretch=True,  minwidth=180, width=420, anchor="w")
        self._tree.column("aka",  stretch=False, minwidth=120, width=170, anchor="e")
        self._tree.column("star", stretch=False, minwidth=28,  width=28,  anchor="center")
        self._tree.column("heur", stretch=False, minwidth=0,   width=0,   anchor="w")
        self._tree.column("size", stretch=False, minwidth=80,  width=110, anchor="e")
        self._tree.column("pct",  stretch=False, minwidth=50,  width=70,  anchor="e")

        # ── Tags ──────────────────────────────────────────────────────
        self._tree.tag_configure("dir",
            foreground=_TEXT_DIR,
            font=(_T["font_family"], _T["font_size_base"], "bold"))
        self._tree.tag_configure("file",    foreground=_TEXT)
        # empty_dir: bold (like a dir) but muted colour — set after scan confirms size=0
        self._tree.tag_configure("empty_dir",
            foreground=_TEXT_DIM,
            font=(_T["font_family"], _T["font_size_base"], "bold"))
        self._tree.tag_configure("empty",   foreground=_TEXT_DIM)
        self._tree.tag_configure("denied",  foreground=_DENIED)
        self._tree.tag_configure("more",    foreground=_ACCENT)
        self._tree.tag_configure("symlink", foreground="#A0C4E8")
        self._tree.tag_configure("starred",  foreground=_ACCENT)
        self._tree.tag_configure("aka_tagged",
            foreground=_TEXT_DIM,
            font=(_T["font_family"], _T["font_size_base"], "italic"))
        self._tree.tag_configure("drop_target", background=_ROW_H)

        # ── Scrollbar ─────────────────────────────────────────────────
        vsb = ttk.Scrollbar(self, orient="vertical", command=self._tree.yview)
        set_vsb = make_autohide_pack_setter(vsb, side=tk.RIGHT, fill=tk.Y)
        self._tree.configure(yscrollcommand=set_vsb)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Bindings ──────────────────────────────────────────────────
        self._tree.bind("<Button-1>",          self._on_button1_press)
        self._tree.bind("<ButtonRelease-1>",  self._on_click)
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self._tree.bind("<Left>",             self._go_up)
        self._tree.bind("<BackSpace>",        self._go_up)
        self._tree.bind("<Right>",            self._open_selected)   # dirs only
        self._tree.bind("<Return>",           self._on_return_key)   # dirs + files
        self._tree.bind("<Up>",               self._on_up)
        self._tree.bind("<Down>",             self._on_down)
        self._tree.bind("<Shift-Up>",         self._on_shift_up)
        self._tree.bind("<Shift-Down>",       self._on_shift_down)
        self._tree.bind("<Escape>",           self._on_escape)
        self._tree.bind("<Control-Up>",       self._on_ctrl_up)
        self._tree.bind("<Control-Down>",     self._on_ctrl_down)
        self._tree.bind("<Button-2>",         self._on_middle_click)
        self._tree.bind("<MouseWheel>",       self._on_mousewheel)
        self._register_drop_target()

    # ------------------------------------------------------------------
    # Public API — navigation
    # ------------------------------------------------------------------

    def load_dir(self, path: str) -> None:
        """Scan path and populate the treeview. Called on every navigation."""
        self._current_path = path
        self._item_data.clear()
        self._path_iids.clear()
        self._all_rows.clear()
        self._hidden_rows.clear()
        self._more_iid = None

        for iid in self._tree.get_children():
            self._tree.delete(iid)

        norm = normalize(path)
        try:
            raw_entries = list(os.scandir(norm))
        except PermissionError:
            self._tree.insert("", "end", text="  \U0001f512  Access denied",
                              values=("", "", "", "", ""), tags=("denied",))
            return
        except FileNotFoundError:
            # Directory was deleted or renamed while we were viewing it.
            # Navigate up one level automatically (scheduled to avoid re-entrancy).
            parent = str(Path(to_display(path)).parent)
            if os.path.normcase(parent) != os.path.normcase(to_display(path)):
                self.after(0, lambda p=parent: self.navigate_cb(p))
            return
        except OSError as exc:
            self._tree.insert("", "end", text=f"  Error: {exc}",
                              values=("", "", "", "", ""), tags=("denied",))
            return

        dirs, files = [], []
        for entry in raw_entries:
            try:
                (dirs if entry.is_dir(follow_symlinks=False) else files).append(entry)
            except OSError:
                files.append(entry)

        dirs.sort(key=lambda e: e.name.lower())
        files.sort(key=lambda e: e.name.lower())

        for entry in dirs:
            tag_value = _tags.get_tag(entry.path)
            self._all_rows.append({
                "name":       entry.name,
                "is_dir":     True,
                "size_bytes": -1,       # -1 = pending scan
                "heur_str":   "",
                "aka_str":    f"aka {tag_value}" if tag_value else "",
                "size_str":   "—",
                "pct_str":    "—",
                "path":       entry.path,
                "tag":        "symlink" if entry.is_symlink() else "dir",
            })

        for entry in files:
            if EXPR_SKIPPED and \
                    any(p.search(entry.name) for p in EXPR_SKIPPED):
                continue
            try:
                size = entry.stat(follow_symlinks=False).st_size
                size_str = fmt_size(size)
            except OSError:
                size, size_str = 0, "—"
            tag_value = _tags.get_tag(entry.path)
            self._all_rows.append({
                "name":       entry.name,
                "is_dir":     False,
                "size_bytes": size,
                "heur_str":   "",
                "aka_str":    f"aka {tag_value}" if tag_value else "",
                "size_str":   size_str,
                "pct_str":    "—",
                "path":       entry.path,
                "tag":        "symlink" if entry.is_symlink() else "file",
            })

        self._render_rows()

    # ------------------------------------------------------------------
    # Public API — async scanner callbacks (Phase 5)
    # ------------------------------------------------------------------

    def update_item_size(self, path: str, size: int) -> None:
        """Called by App._process_queue when a dir scan result arrives.
        size=-1 means the scanner skipped this path (e.g. network drive);
        the row keeps showing '—'.
        """
        key = os.path.normcase(path)
        iid = self._path_iids.get(key)
        if not iid:
            return
        row = self._item_data.get(iid)
        if not row:
            return
        if size < 0:
            return   # network / skipped — leave '—' as-is
        row["size_bytes"] = size
        row["size_str"]   = fmt_size(size)
        self._tree.set(iid, "size", row["size_str"])

    def finalize_pct(self) -> None:
        """Compute % column and dim empty dirs once the full scan is done."""
        total = self.get_total_size()
        for iid, row in self._item_data.items():
            # % column
            if total > 0 and row["size_bytes"] >= 0:
                pct = row["size_bytes"] / total * 100
                row["pct_str"] = f"{pct:.1f}%"
                self._tree.set(iid, "pct", row["pct_str"])
            # Dim empty dirs (size confirmed as 0 after scan; -1 = skipped/network)
            if row["is_dir"] and row["size_bytes"] == 0 and row["tag"] == "dir":
                self._tree.item(iid, tags=("empty_dir",))

    # ------------------------------------------------------------------
    # Public API — query helpers
    # ------------------------------------------------------------------

    def get_subdir_paths(self) -> list[str]:
        """All subdirectory paths in the current listing (including hidden rows)."""
        return [r["path"] for r in self._all_rows if r["is_dir"]]

    def get_item_count(self) -> int:
        return len(self._all_rows)

    def get_total_size(self) -> int:
        """Sum of all known sizes (files always known; dirs once scanned)."""
        return sum(r["size_bytes"] for r in self._all_rows if r["size_bytes"] >= 0)

    def get_selection_size(self) -> int:
        total = 0
        for iid in self._tree.selection():
            row = self._item_data.get(iid)
            if row and row["size_bytes"] >= 0:
                total += row["size_bytes"]
        return total

    def get_current_rows(self) -> list[dict]:
        return [dict(r) for r in self._all_rows]

    @staticmethod
    def _starred_key(path: str) -> str:
        return os.path.normcase(normalize(path))

    def _starred_keys_snapshot(self) -> set[str]:
        return {self._starred_key(p) for p in _starred.all_starred()}

    def refresh_stars(self) -> None:
        """Refresh the ★ column for all visible rows without reloading the directory."""
        starred_keys = self._starred_keys_snapshot()
        for iid, row in self._item_data.items():
            is_star = self._starred_key(row["path"]) in starred_keys
            star_val = "★" if is_star else ""
            self._tree.set(iid, "star", star_val)
            current_tags = list(self._tree.item(iid, "tags"))
            base_tags = [t for t in current_tags if t != "starred"]
            new_tags = base_tags + (["starred"] if is_star else [])
            self._tree.item(iid, tags=new_tags)

    def toggle_star_selected(self) -> str | None:
        """Toggle star on the currently focused/selected item. Returns the path."""
        sel = self._tree.selection()
        if not sel:
            return None
        row = self._item_data.get(sel[0])
        if not row:
            return None
        _starred.toggle(row["path"])
        self.refresh_stars()
        return row["path"]

    def get_starred_iids_in_order(self) -> list[str]:
        """Return tree iids of starred rows in their display order."""
        starred_keys = self._starred_keys_snapshot()
        return [
            iid for iid in self._item_data
            if self._starred_key(self._item_data[iid]["path"]) in starred_keys
        ]

    def begin_heuristic_results(self, title: str) -> None:
        """Show heuristic column and reset current values before a new run."""
        self._tree.heading("heur", text=title or "Heuristic", anchor="w")
        self._tree.column("heur", stretch=True, minwidth=120, width=180, anchor="w")
        for row in self._all_rows:
            row["heur_str"] = ""
        for iid in self._item_data:
            self._tree.set(iid, "heur", "")

    def update_heuristic_value(self, path: str, value: str) -> None:
        """Set heuristic value for one path and update row if visible."""
        key = os.path.normcase(path)
        iid = self._path_iids.get(key)
        if iid and iid in self._item_data:
            row = self._item_data[iid]
            row["heur_str"] = value
            self._tree.set(iid, "heur", value)

        for row in self._all_rows:
            if os.path.normcase(row["path"]) == key:
                row["heur_str"] = value
                break

    def apply_heuristic_results(self, title: str, by_path: dict[str, str]) -> None:
        self.begin_heuristic_results(title)
        for path, value in by_path.items():
            self.update_heuristic_value(path, value)

    def clear_heuristic_column(self) -> None:
        self._tree.heading("heur", text="", anchor="w")
        self._tree.column("heur", stretch=False, minwidth=0, width=0, anchor="w")
        for row in self._all_rows:
            row["heur_str"] = ""
        for iid in self._item_data:
            self._tree.set(iid, "heur", "")

    # ------------------------------------------------------------------
    # Rendering & sorting
    # ------------------------------------------------------------------

    def _render_rows(self) -> None:
        children = self._tree.get_children()
        if children:
            self._tree.delete(*children)
        self._item_data.clear()
        self._path_iids.clear()
        self._more_iid = None
        starred_keys = self._starred_keys_snapshot()

        sorted_rows = self._sorted_rows()
        visible = sorted_rows[:_MAX_ROWS]
        self._hidden_rows = sorted_rows[_MAX_ROWS:]

        for row in visible:
            img = self._icons.get("folder" if row["is_dir"] else "file")
            is_star = self._starred_key(row["path"]) in starred_keys
            star_val = "★" if is_star else ""
            tags = [row["tag"]]
            if is_star:
                tags.append("starred")
            if row.get("aka_str"):
                tags.append("aka_tagged")
            iid = self._tree.insert(
                "", "end",
                text=f"  {row['name']}",
                image=img or "",
                values=(row.get("aka_str", ""), star_val, row.get("heur_str", ""), row["size_str"], row["pct_str"]),
                tags=tuple(tags),
            )
            self._item_data[iid] = row
            self._path_iids[os.path.normcase(row["path"])] = iid

        if self._hidden_rows:
            self._more_iid = self._tree.insert(
                "", "end",
                text=f"  … {len(self._hidden_rows)} more items — click to load",
                image="",
                values=("", "", "", "", ""),
                tags=("more",),
            )

        # Auto-select: prefer _pending_select (set by _go_up), fall back to first item
        target_iid = None
        if self._pending_select:
            key = os.path.normcase(normalize(self._pending_select))
            target_iid = self._path_iids.get(key)
            self._pending_select = None
        if not target_iid:
            target_iid = next(iter(self._item_data), None)
        if target_iid:
            self._tree.selection_set(target_iid)
            self._tree.focus(target_iid)
            self._tree.see(target_iid)
            self._selection_anchor = target_iid
            self._last_selected_iid = target_iid
        self._tree.focus_set()   # always steal keyboard focus back to main frame

    def _load_more(self) -> None:
        if self._more_iid:
            self._tree.delete(self._more_iid)
            self._more_iid = None
        starred_keys = self._starred_keys_snapshot()
        for row in self._hidden_rows:
            img = self._icons.get("folder" if row["is_dir"] else "file")
            is_star = self._starred_key(row["path"]) in starred_keys
            star_val = "★" if is_star else ""
            tags = [row["tag"]]
            if is_star:
                tags.append("starred")
            if row.get("aka_str"):
                tags.append("aka_tagged")
            iid = self._tree.insert(
                "", "end",
                text=f"  {row['name']}",
                image=img or "",
                values=(row.get("aka_str", ""), star_val, row.get("heur_str", ""), row["size_str"], row["pct_str"]),
                tags=tuple(tags),
            )
            self._item_data[iid] = row
            self._path_iids[os.path.normcase(row["path"])] = iid
        self._hidden_rows.clear()

    def _sorted_rows(self) -> list:
        col, asc = self._sort_col, self._sort_asc
        if col == "size":
            key = lambda r: (not r["is_dir"], r["size_bytes"])
        else:
            key = lambda r: (not r["is_dir"], r["name"].lower())
        return sorted(self._all_rows, key=key, reverse=not asc)

    def _sort_by(self, col: str) -> None:
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        self._update_heading_arrows()
        self._render_rows()

    def _update_heading_arrows(self) -> None:
        arrow = " ▲" if self._sort_asc else " ▼"
        for col_id, col_key, label in (
            ("#0",   "name", "Name"),
            ("size", "size", "Size"),
            ("pct",  "pct",  "%"),
        ):
            suffix = arrow if col_key == self._sort_col else ""
            self._tree.heading(col_id, text=label + suffix)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_button1_press(self, event: tk.Event) -> str | None:
        self._suppress_release_click = False
        item = self._tree.identify_row(event.y)
        if not item or item == self._more_iid:
            self._alt_drag_intent = False
            return None

        self._alt_drag_intent = self._is_alt_pressed(event) and self._dnd_enabled
        if self._alt_drag_intent:
            if len(self._tree.selection()) > 1:
                self._suppress_release_click = True
                return "break"

        is_shift = bool(event.state & 0x1)
        if not is_shift:
            return None

        selected = set(self._tree.selection())
        if item not in selected:
            self._tree.selection_add(item)

        self._tree.focus(item)
        self._tree.see(item)
        self._last_selected_iid = item
        if self._selection_anchor is None or self._selection_anchor not in self._item_data:
            self._selection_anchor = next(iter(self._tree.selection()), item)
        self._on_select()
        return "break"

    def _on_click(self, event: tk.Event) -> None:
        if self._suppress_release_click:
            self._suppress_release_click = False
            self._alt_drag_intent = False
            return

        region = self._tree.identify_region(event.x, event.y)
        if region not in ("cell", "tree"):
            return
        item = self._tree.identify_row(event.y)
        if not item:
            return
        if item == self._more_iid:
            self._load_more()
            return
        if event.state & 0x4 or event.state & 0x1:
            return
        row = self._item_data.get(item)
        if row:
            target = self._resolve_directory_target(row)
            if target:
                self.navigate_cb(target)
            elif not row["is_dir"]:
                self._open_file(row["path"])
        self._alt_drag_intent = False

    def _on_select(self, event=None) -> None:
        paths = []
        selection = list(self._tree.selection())
        for iid in selection:
            row = self._item_data.get(iid)
            if row:
                paths.append(row["path"])

        if selection:
            focus_iid = self._tree.focus()
            if focus_iid in selection:
                self._last_selected_iid = focus_iid
            else:
                self._last_selected_iid = selection[-1]
            if len(selection) == 1:
                self._selection_anchor = selection[0]

        self.state.selection = paths
        if self.on_select_cb:
            sel_size = self.get_selection_size()
            self.on_select_cb(len(paths), sel_size)

    def _on_up(self, event=None) -> str:
        items = list(self._item_data)
        if not items:
            return "break"
        sel = self._tree.selection()
        current = sel[0] if sel else None
        if not current or current == items[0]:
            self._select_item(items[-1])          # wrap to last
        else:
            prev = self._tree.prev(current)
            self._select_item(prev if prev in self._item_data else items[-1])
        return "break"

    def _on_down(self, event=None) -> str:
        items = list(self._item_data)
        if not items:
            return "break"
        sel = self._tree.selection()
        current = sel[0] if sel else None
        if not current or current == items[-1]:
            self._select_item(items[0])           # wrap to first
        else:
            nxt = self._tree.next(current)
            self._select_item(nxt if nxt in self._item_data else items[0])
        return "break"

    def _on_shift_up(self, event=None) -> str:
        return self._move_with_shift(-1)

    def _on_shift_down(self, event=None) -> str:
        return self._move_with_shift(+1)

    def _on_ctrl_up(self, event=None) -> str:
        items = list(self._item_data)
        if items:
            self._select_item(items[0])
        return "break"

    def _on_ctrl_down(self, event=None) -> str:
        items = list(self._item_data)
        if items:
            self._select_item(items[-1])
        return "break"

    def _move_with_shift(self, step: int) -> str:
        items = list(self._item_data)
        if not items:
            return "break"

        current = self._tree.focus()
        if current not in self._item_data:
            sel = list(self._tree.selection())
            current = sel[-1] if sel else items[0]

        idx = items.index(current)
        target = items[(idx + step) % len(items)]
        self._extend_selection_to(target)
        return "break"

    def _extend_selection_to(self, target_iid: str) -> None:
        items = list(self._item_data)
        if not items or target_iid not in self._item_data:
            return

        if self._selection_anchor not in self._item_data:
            sel = list(self._tree.selection())
            self._selection_anchor = sel[0] if sel else target_iid

        anchor_idx = items.index(self._selection_anchor)
        target_idx = items.index(target_iid)
        lo, hi = sorted((anchor_idx, target_idx))
        self._tree.selection_set(items[lo:hi + 1])
        self._tree.focus(target_iid)
        self._tree.see(target_iid)
        self._last_selected_iid = target_iid
        self._on_select()

    def _select_item(self, iid: str) -> None:
        self._tree.selection_set(iid)
        self._tree.focus(iid)
        self._tree.see(iid)
        self._selection_anchor = iid
        self._last_selected_iid = iid

    def collapse_selection_to_last(self) -> bool:
        target = self._last_selected_iid
        if target not in self._item_data:
            selection = list(self._tree.selection())
            target = selection[-1] if selection else None
        if target not in self._item_data:
            target = next(iter(self._item_data), None)
        if not target:
            return False
        self._select_item(target)
        self._on_select()
        return True

    def _on_escape(self, event=None) -> str:
        self.collapse_selection_to_last()
        return "break"

    def _go_up(self, event=None) -> str:
        if not self._current_path:
            return "break"
        display = to_display(self._current_path)
        parent = str(Path(display).parent)
        if os.path.normcase(parent) != os.path.normcase(display):
            self._pending_select = self._current_path   # re-select where we came from
            self.navigate_cb(parent)
        return "break"

    def _open_selected(self, event=None) -> str:
        """Right arrow — navigate into selected directory only."""
        sel = self._tree.selection()
        if sel:
            row = self._item_data.get(sel[0])
            if row:
                target = self._resolve_directory_target(row)
                if target:
                    self.navigate_cb(target)
        return "break"

    def _on_return_key(self, event=None) -> str:
        """Enter — navigate into directory OR open file with the OS default app."""
        sel = self._tree.selection()
        if sel:
            row = self._item_data.get(sel[0])
            if row:
                target = self._resolve_directory_target(row)
                if target:
                    self.navigate_cb(target)
                else:
                    self._open_file(row["path"])
        return "break"

    def _resolve_directory_target(self, row: dict) -> str | None:
        if row.get("is_dir"):
            return row.get("path")
        path = row.get("path")
        if not isinstance(path, str):
            return None
        return self._resolve_folder_shortcut(path)

    def _resolve_folder_shortcut(self, path: str) -> str | None:
        if sys.platform != "win32":
            return None
        if os.path.splitext(path)[1].lower() != ".lnk":
            return None

        key = os.path.normcase(normalize(path))
        if key in self._folder_shortcut_cache:
            return self._folder_shortcut_cache[key]

        escaped = to_display(path).replace("'", "''")
        command = (
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(" 
            f"'{escaped}');"
            "$p=$s.TargetPath;"
            "if ($p) { [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Write-Output $p }"
        )
        kwargs: dict = {
            "capture_output": True,
            "text": True,
            "check": False,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                **kwargs,
            )
            target = proc.stdout.strip()
            if target and os.path.isdir(normalize(target)):
                self._folder_shortcut_cache[key] = target
                return target
        except Exception:
            pass
        self._folder_shortcut_cache[key] = None
        return None

    def _open_file(self, path: str) -> None:
        """Open a file with the OS default application (like double-clicking in Explorer)."""
        try:
            # to_display strips the \\?\ prefix: ShellExecuteW (used by os.startfile)
            # does not support extended-length paths and can crash shell extension DLLs.
            os.startfile(to_display(path))
        except AttributeError:
            # os.startfile is Windows-only; fall back to xdg-open / open on other platforms
            try:
                cmd = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([cmd, to_display(path)])
            except Exception:
                pass
        except Exception:
            pass

    def _on_middle_click(self, event: tk.Event) -> None:
        """Middle mouse button on a directory — open a new Pyxplorer window there."""
        item = self._tree.identify_row(event.y)
        if not item:
            return
        row = self._item_data.get(item)
        if not row:
            return
        target = self._resolve_directory_target(row)
        if not target:
            return
        target = to_display(target)
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(
            [sys.executable, "-m", "pyxplorer", target],
            **kwargs,
        )

    def _on_mousewheel(self, event: tk.Event) -> str:
        units = int((-event.delta / 120) * _SCROLL_SPEED)
        if units == 0 and event.delta != 0:
            units = -1 if event.delta > 0 else 1
        self._tree.yview_scroll(units, "units")
        return "break"

    def _register_drop_target(self) -> None:
        if not DND_FILES:
            self._dnd_enabled = False
            return
        try:
            self._tree.drop_target_register(DND_FILES)
            self._tree.drag_source_register(1, DND_FILES)
            self._tree.dnd_bind("<<DragInitCmd>>", self._on_drag_init)
            self._tree.dnd_bind("<<DragEndCmd>>", self._on_drag_end)
            self._tree.dnd_bind("<<DropEnter>>", self._on_drop_enter)
            self._tree.dnd_bind("<<DropPosition>>", self._on_drop_position)
            self._tree.dnd_bind("<<DropLeave>>", self._on_drop_leave)
            self._tree.dnd_bind("<<Drop>>", self._on_drop)
            self._dnd_enabled = True
        except Exception:
            self._dnd_enabled = False

    def _on_drag_init(self, event):
        x = self._event_tree_x(event)
        y = self._event_tree_y(event)
        region = self._tree.identify_region(x, y)
        if region not in ("tree", "cell"):
            return (COPY, DND_FILES, "")

        if not (self._alt_drag_intent or self._is_alt_pressed(event)):
            return (COPY, DND_FILES, "")

        self._suppress_release_click = True

        item = self._tree.identify_row(y)
        if not item or item not in self._item_data:
            return (COPY, DND_FILES, "")
        if item and item in self._item_data and item not in self._tree.selection():
            self._select_item(item)
            self._on_select()

        paths = self._drag_payload_paths()
        if not paths:
            return (COPY, DND_FILES, "")
        data = tuple(paths)
        return ((COPY, MOVE), DND_FILES, data)

    @staticmethod
    def _is_alt_pressed(event) -> bool:
        try:
            state = int(getattr(event, "state", 0) or 0)
            if state & 0x0008:
                return True
        except Exception:
            pass

        modifiers = str(getattr(event, "modifiers", "") or "").lower()
        return "alt" in modifiers or "mod1" in modifiers

    def _on_drag_end(self, event):
        self._alt_drag_intent = False
        self._set_drop_hover(None)

    def _on_drop_enter(self, event):
        self._set_drop_hover(self._hover_drop_target_iid(event))
        return getattr(event, "action", COPY)

    def _on_drop_position(self, event):
        self._set_drop_hover(self._hover_drop_target_iid(event))
        return getattr(event, "action", COPY)

    def _on_drop_leave(self, event):
        self._set_drop_hover(None)
        return getattr(event, "action", COPY)

    def _on_drop(self, event) -> str:
        self._set_drop_hover(None)
        if self._drop_busy:
            self.status_cb("Drop already running…")
            return "break"

        paths = self._parse_drop_paths(getattr(event, "data", ""))
        if not paths:
            self.status_cb("Drop ignored: no valid local path")
            return "break"

        dst = self._drop_destination_from_event(event)
        if not dst or not os.path.isdir(normalize(dst)):
            self.status_cb("Drop ignored: invalid destination")
            return "break"

        forbidden = [p for p in paths if self._is_forbidden_drop(p, dst)]
        if forbidden:
            self.status_cb("Drop blocked: cannot drop into itself")
            return "break"

        mode = self._drop_mode(getattr(event, "action", ""), paths, dst)
        self._run_drop(mode, paths, dst)
        return "break"

    def _event_tree_y(self, event) -> int:
        y = getattr(event, "y", None)
        if isinstance(y, int):
            return y
        y_root = getattr(event, "y_root", None)
        if isinstance(y_root, int):
            return y_root - self._tree.winfo_rooty()
        return 0

    def _event_tree_x(self, event) -> int:
        x = getattr(event, "x", None)
        if isinstance(x, int):
            return x
        x_root = getattr(event, "x_root", None)
        if isinstance(x_root, int):
            return x_root - self._tree.winfo_rootx()
        return 0

    def _hover_drop_target_iid(self, event) -> str | None:
        y = self._event_tree_y(event)
        item = self._tree.identify_row(y)
        if not item or item == self._more_iid:
            return None
        row = self._item_data.get(item)
        if not row or not row["is_dir"]:
            return None
        return item

    def _set_drop_hover(self, iid: str | None) -> None:
        if iid == self._drop_hover_iid:
            return
        if self._drop_hover_iid and self._drop_hover_iid in self._item_data:
            tags = [t for t in self._tree.item(self._drop_hover_iid, "tags") if t != "drop_target"]
            self._tree.item(self._drop_hover_iid, tags=tuple(tags))
        self._drop_hover_iid = None
        if iid and iid in self._item_data:
            tags = list(self._tree.item(iid, "tags"))
            if "drop_target" not in tags:
                tags.append("drop_target")
                self._tree.item(iid, tags=tuple(tags))
            self._drop_hover_iid = iid

    def _drag_payload_paths(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for iid in self._tree.selection():
            row = self._item_data.get(iid)
            if not row:
                continue
            path = row.get("path")
            if not path:
                continue
            norm = os.path.normcase(normalize(path))
            if norm in seen:
                continue
            if not os.path.exists(normalize(path)):
                continue
            seen.add(norm)
            out.append(to_display(path))
        return out

    def _drop_destination_from_event(self, event) -> str:
        item = self._tree.identify_row(getattr(event, "y", 0))
        if item and item != self._more_iid:
            row = self._item_data.get(item)
            if row and row["is_dir"]:
                return row["path"]
        return self.state.current_dir

    def _parse_drop_paths(self, raw: str) -> list[str]:
        if not raw:
            return []
        try:
            parts = list(self._tree.tk.splitlist(raw))
        except Exception:
            parts = [raw]

        out: list[str] = []
        seen: set[str] = set()
        for item in parts:
            path = self._coerce_drop_item_to_path(item)
            if not path:
                continue
            norm = normalize(path)
            key = os.path.normcase(norm)
            if key in seen:
                continue
            if not os.path.exists(norm):
                continue
            seen.add(key)
            out.append(path)
        return out

    @staticmethod
    def _coerce_drop_item_to_path(item: str) -> str | None:
        if not item:
            return None
        text = item.strip()
        if not text:
            return None
        if text.startswith("file://"):
            parsed = urlparse(text)
            if parsed.scheme != "file":
                return None
            if sys.platform == "win32":
                path = unquote(parsed.path or "")
                if path.startswith("/") and len(path) > 2 and path[2] == ":":
                    path = path[1:]
                path = path.replace("/", "\\")
                if parsed.netloc and parsed.netloc.lower() != "localhost":
                    path = f"\\\\{parsed.netloc}{path}"
                return path
            return unquote(parsed.path or "")
        return text

    @staticmethod
    def _drop_mode(action: str, src_paths: list[str], dst_dir: str) -> str:
        a = (action or "").lower()
        if a == "move":
            return "move"
        if a == "copy":
            return "copy"

        dst_norm = normalize(dst_dir)
        if sys.platform == "win32":
            dst_drive = os.path.splitdrive(to_display(dst_norm))[0].lower()
            same_drive = True
            for src in src_paths:
                src_drive = os.path.splitdrive(to_display(src))[0].lower()
                if src_drive != dst_drive:
                    same_drive = False
                    break
            return "move" if same_drive else "copy"

        try:
            dst_dev = os.stat(dst_norm).st_dev
            same_dev = all(os.stat(normalize(src)).st_dev == dst_dev for src in src_paths)
            return "move" if same_dev else "copy"
        except OSError:
            return "copy"

    @staticmethod
    def _is_forbidden_drop(src_path: str, dst_dir: str) -> bool:
        src_norm = normalize(src_path)
        dst_norm = normalize(dst_dir)
        if os.path.normcase(src_norm) == os.path.normcase(dst_norm):
            return True
        if not os.path.isdir(src_norm):
            return False
        try:
            src_real = os.path.normcase(os.path.abspath(src_norm))
            dst_real = os.path.normcase(os.path.abspath(dst_norm))
            common = os.path.commonpath([src_real, dst_real])
            return common == src_real
        except Exception:
            return False

    def _run_drop(self, mode: str, paths: list[str], dst: str) -> None:
        self._drop_busy = True
        verb = "Copying" if mode == "copy" else "Moving"
        self.status_cb(f"{verb} {len(paths)} dropped item(s)…")
        if self.transfer_start_cb is not None:
            self.after(0, lambda: self.transfer_start_cb(f"{verb} {len(paths)} item(s)…"))

        def _emit_progress(pct: int) -> None:
            if self.transfer_progress_cb is None:
                return
            self.after(0, lambda p=pct: self.transfer_progress_cb(p))

        def _worker() -> None:
            err: Exception | None = None
            try:
                if mode == "move":
                    move_items(paths, dst, progress_cb=_emit_progress)
                else:
                    copy_items(paths, dst, progress_cb=_emit_progress)
            except Exception as exc:
                err = exc

            def _finish() -> None:
                self._drop_busy = False
                if self.transfer_stop_cb is not None:
                    self.transfer_stop_cb()
                if err is not None:
                    self.status_cb(f"Drop failed: {err}")
                    messagebox.showerror("Drop error", str(err), parent=self.winfo_toplevel())
                    return
                self.status_cb(f"Drop complete — {len(paths)} item(s)")
                self.navigate_cb(self.state.current_dir)

            self.after(0, _finish)

        threading.Thread(target=_worker, daemon=True).start()
