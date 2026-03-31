"""
Phase 3 — Main frame: directory listing with Name / Size / % columns.
Phase 5 — update_item_size / finalize_pct wired to async scanner.
"""
import os
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from typing import Callable

from ..core.longpath import normalize, to_display
from ..core.fs import fmt_size
from ..settings import THEME as _T

_BG       = _T["bg"]
_TEXT     = _T["text"]
_TEXT_DIM = _T["text_mute"]
_TEXT_DIR = _T["text"]
_ACCENT   = _T["accent"]
_ROW_SEL  = _T["row_selected"]
_DENIED   = "#7A4040"

_MAX_ROWS = 500  # Phase 10 cap: show first 500, then a "load more" sentinel


class MainFrame(ttk.Frame):
    def __init__(self, parent, state, navigate_cb,
                 on_select_cb: Callable | None = None):
        super().__init__(parent, style="TFrame")
        self.state = state
        self.navigate_cb = navigate_cb
        self.on_select_cb = on_select_cb

        self._current_path: str = ""
        self._item_data:  dict = {}   # iid  → row dict
        self._path_iids:  dict = {}   # normcase(path) → iid   (for O(1) size updates)
        self._all_rows:   list = []   # full row list for re-sorting
        self._sort_col:   str  = "name"
        self._sort_asc:   bool = True
        self._hidden_rows: list = []
        self._more_iid:   str | None = None

        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self._tree = ttk.Treeview(
            self,
            columns=("size", "pct"),
            selectmode="extended",
        )

        # ── Headings ──────────────────────────────────────────────────
        self._tree.heading("#0",    text="Name",  anchor="w",
                           command=lambda: self._sort_by("name"))
        self._tree.heading("size",  text="Size",  anchor="e",
                           command=lambda: self._sort_by("size"))
        self._tree.heading("pct",   text="%",     anchor="e",
                           command=lambda: self._sort_by("pct"))

        # ── Columns ───────────────────────────────────────────────────
        self._tree.column("#0",   stretch=True,  minwidth=180, width=420, anchor="w")
        self._tree.column("size", stretch=False, minwidth=80,  width=110, anchor="e")
        self._tree.column("pct",  stretch=False, minwidth=50,  width=70,  anchor="e")

        # ── Tags ──────────────────────────────────────────────────────
        self._tree.tag_configure("dir",
            foreground=_TEXT_DIR,
            font=(_T["font_family"], _T["font_size_base"], "bold"))
        self._tree.tag_configure("file",    foreground=_TEXT)
        self._tree.tag_configure("empty",   foreground=_TEXT_DIM)
        self._tree.tag_configure("denied",  foreground=_DENIED)
        self._tree.tag_configure("more",    foreground=_ACCENT)
        self._tree.tag_configure("symlink", foreground="#A0C4E8")

        # ── Scrollbar ─────────────────────────────────────────────────
        vsb = ttk.Scrollbar(self, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Bindings ──────────────────────────────────────────────────
        self._tree.bind("<ButtonRelease-1>",  self._on_click)
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self._tree.bind("<Left>",             self._go_up)
        self._tree.bind("<BackSpace>",        self._go_up)
        self._tree.bind("<Right>",            self._open_selected)
        self._tree.bind("<Return>",           self._open_selected)
        self._tree.bind("<Up>",               self._on_up)
        self._tree.bind("<Down>",             self._on_down)
        self._tree.bind("<Control-Up>",       self._on_ctrl_up)
        self._tree.bind("<Control-Down>",     self._on_ctrl_down)

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
                              values=("", ""), tags=("denied",))
            return
        except OSError as exc:
            self._tree.insert("", "end", text=f"  Error: {exc}",
                              values=("", ""), tags=("denied",))
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
            self._all_rows.append({
                "name":       entry.name,
                "is_dir":     True,
                "size_bytes": -1,       # -1 = pending scan
                "size_str":   "—",
                "pct_str":    "—",
                "path":       entry.path,
                "tag":        "symlink" if entry.is_symlink() else "dir",
            })

        for entry in files:
            try:
                size = entry.stat(follow_symlinks=False).st_size
                size_str = fmt_size(size)
            except OSError:
                size, size_str = 0, "—"
            self._all_rows.append({
                "name":       entry.name,
                "is_dir":     False,
                "size_bytes": size,
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
        """Called by App._process_queue when a dir scan result arrives."""
        key = os.path.normcase(path)
        iid = self._path_iids.get(key)
        if not iid:
            return
        row = self._item_data.get(iid)
        if not row:
            return
        row["size_bytes"] = size
        row["size_str"]   = fmt_size(size)
        self._tree.set(iid, "size", row["size_str"])

    def finalize_pct(self) -> None:
        """Compute and display % column once the full scan is done."""
        total = self.get_total_size()
        if total <= 0:
            return
        for iid, row in self._item_data.items():
            if row["size_bytes"] >= 0:
                pct = row["size_bytes"] / total * 100
                row["pct_str"] = f"{pct:.1f}%"
                self._tree.set(iid, "pct", row["pct_str"])

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

    # ------------------------------------------------------------------
    # Rendering & sorting
    # ------------------------------------------------------------------

    def _render_rows(self) -> None:
        for iid in self._tree.get_children():
            self._tree.delete(iid)
        self._item_data.clear()
        self._path_iids.clear()
        self._more_iid = None

        sorted_rows = self._sorted_rows()
        visible = sorted_rows[:_MAX_ROWS]
        self._hidden_rows = sorted_rows[_MAX_ROWS:]

        for row in visible:
            iid = self._tree.insert(
                "", "end",
                text=f"  {row['name']}",
                values=(row["size_str"], row["pct_str"]),
                tags=(row["tag"],),
            )
            self._item_data[iid] = row
            self._path_iids[os.path.normcase(row["path"])] = iid

        if self._hidden_rows:
            self._more_iid = self._tree.insert(
                "", "end",
                text=f"  … {len(self._hidden_rows)} more items — click to load",
                values=("", ""),
                tags=("more",),
            )

        # Auto-select, focus and claim keyboard focus on the first entry
        first = next(iter(self._item_data), None)
        if first:
            self._tree.selection_set(first)
            self._tree.focus(first)
            self._tree.see(first)
        self._tree.focus_set()   # always steal keyboard focus back to main frame

    def _load_more(self) -> None:
        if self._more_iid:
            self._tree.delete(self._more_iid)
            self._more_iid = None
        for row in self._hidden_rows:
            iid = self._tree.insert(
                "", "end",
                text=f"  {row['name']}",
                values=(row["size_str"], row["pct_str"]),
                tags=(row["tag"],),
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

    def _on_click(self, event: tk.Event) -> None:
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
        if row and row["is_dir"]:
            self.navigate_cb(row["path"])

    def _on_select(self, event=None) -> None:
        paths = []
        for iid in self._tree.selection():
            row = self._item_data.get(iid)
            if row:
                paths.append(row["path"])
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

    def _select_item(self, iid: str) -> None:
        self._tree.selection_set(iid)
        self._tree.focus(iid)
        self._tree.see(iid)

    def _go_up(self, event=None) -> str:
        if not self._current_path:
            return "break"
        parent = str(Path(to_display(self._current_path)).parent)
        if os.path.normcase(parent) != os.path.normcase(to_display(self._current_path)):
            self.navigate_cb(parent)
        return "break"

    def _open_selected(self, event=None) -> str:
        sel = self._tree.selection()
        if sel:
            row = self._item_data.get(sel[0])
            if row and row["is_dir"]:
                self.navigate_cb(row["path"])
        return "break"
