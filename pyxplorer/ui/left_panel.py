"""
Phase 4 — Left panel: lazy-loading directory tree.
- selectmode="none": clicking navigates but never touches clipboard selection.
- Dummy "…" child on every unexpanded dir so the expand arrow appears.
- Expand arrow replaced with real subdirs on <<TreeviewOpen>>.
- Current path highlighted via "current" tag.
- Auto-expands to the navigated path so the user always sees where they are.
"""
import os
import sys
import string
import tkinter as tk
from tkinter import ttk
from pathlib import Path

from ..core.longpath import normalize, to_display
from ..settings import THEME as _T, START_DIRS

_BG_PANEL  = _T["bg_dark"]
_BG_CURR   = _T["bg_entry"]
_TEXT      = _T["text"]
_TEXT_DIM  = _T["text_mute"]
_ACCENT    = _T["accent"]
_FONT      = _T["font_family"]
_SZ        = _T["font_size_base"]
_RH_N      = _T["row_height_nav"]
_DENIED    = "#7A4040"

_DUMMY = "\x00dummy"   # sentinel text that identifies placeholder children


class LeftPanel(ttk.Frame):
    def __init__(self, parent, state, navigate_cb,
                 icons: dict | None = None,
                 extra_start_dirs: list[str] | None = None):
        super().__init__(parent, style="LeftPanel.TFrame")
        self.state = state
        self.navigate_cb = navigate_cb
        self._icons = icons or {}
        self._extra_start_dirs: list[str] = extra_start_dirs or []

        self._node_paths: dict[str, str | None] = {}  # iid  → absolute path (None = dummy)
        self._path_nodes: dict[str, str] = {}          # normcase(path) → iid
        self._loaded: set[str] = set()                 # iids whose children have been loaded
        self._current_iid: str | None = None
        self.focus_back_cb = None                      # set by App after layout is built

        self._build()
        self._populate_roots()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        # Dedicated style so the panel is darker than the main area
        style = ttk.Style()
        style.configure("Nav.Treeview",
            background=_BG_PANEL,
            fieldbackground=_BG_PANEL,
            foreground=_TEXT,
            rowheight=_RH_N,
            font=(_FONT, _SZ),
            borderwidth=0,
            relief="flat",
            indent=16,
        )
        style.map("Nav.Treeview",
            background=[("selected", _BG_PANEL)],
            foreground=[("selected", _TEXT)],
        )
        style.configure("Nav.Treeview.Heading",
            background=_BG_PANEL, foreground=_TEXT,
            relief="flat",
        )

        self._tree = ttk.Treeview(
            self,
            style="Nav.Treeview",
            selectmode="none",
            show="tree",
        )

        # Tags
        self._tree.tag_configure("dir",     foreground=_TEXT)
        self._tree.tag_configure("current", foreground=_ACCENT, background=_BG_CURR)
        self._tree.tag_configure("empty",   foreground=_TEXT_DIM)
        self._tree.tag_configure("denied",  foreground=_DENIED)
        self._tree.tag_configure("drive",   foreground=_TEXT,   font=(_FONT, _SZ, "bold"))

        vsb = ttk.Scrollbar(self, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._tree.bind("<<TreeviewOpen>>",   self._on_expand)
        self._tree.bind("<Button-1>",        self._on_click)
        self._tree.bind("<Double-Button-1>", self._on_double_click)

    # ------------------------------------------------------------------
    # Root population
    # ------------------------------------------------------------------

    def _populate_roots(self) -> None:
        # Extra dirs from CLI arg come first; deduplicate against settings START_DIRS
        extra_keys = {os.path.normcase(normalize(p)) for p in self._extra_start_dirs}
        settings_dirs = [p for p in START_DIRS
                         if os.path.normcase(normalize(p)) not in extra_keys]
        combined = self._extra_start_dirs + settings_dirs
        valid_start = [p for p in combined if os.path.isdir(normalize(p))]

        if valid_start:
            for path in valid_start:
                label = Path(to_display(path)).name or to_display(path)
                iid = self._insert_node("", path, f"  {label}", tags=("drive",))
                self._insert_dummy(iid)
        elif sys.platform == "win32":
            for letter in string.ascii_uppercase:
                path = f"{letter}:\\"
                try:
                    if os.path.exists(path):
                        iid = self._insert_node("", path, f"  {letter}:", tags=("drive",))
                        self._insert_dummy(iid)
                except OSError:
                    pass
        else:
            iid = self._insert_node("", "/", "  /", tags=("drive",))
            self._insert_dummy(iid)

    # ------------------------------------------------------------------
    # Node helpers
    # ------------------------------------------------------------------

    def _insert_node(self, parent: str, path: str, label: str,
                     tags: tuple = ("dir",)) -> str:
        img_key = "drive" if "drive" in tags else "folder"
        img = self._icons.get(img_key)
        iid = self._tree.insert(parent, "end", text=label,
                                image=img or "", tags=tags)
        self._node_paths[iid] = path
        self._path_nodes[self._key(path)] = iid
        return iid

    def _insert_dummy(self, parent_iid: str) -> None:
        dummy_iid = self._tree.insert(parent_iid, "end", text=_DUMMY)
        self._node_paths[dummy_iid] = None

    def _key(self, path: str) -> str:
        """Normalised lookup key: case-fold + normalise."""
        return os.path.normcase(normalize(path))

    # ------------------------------------------------------------------
    # Lazy loading
    # ------------------------------------------------------------------

    def _on_expand(self, event=None) -> None:
        iid = self._tree.focus()
        if iid:
            self._load_children(iid)

    def _load_children(self, iid: str) -> None:
        if iid in self._loaded:
            return
        self._loaded.add(iid)

        # Remove placeholder dummy(ies)
        for child in list(self._tree.get_children(iid)):
            if self._node_paths.get(child) is None:
                self._tree.delete(child)
                del self._node_paths[child]

        path = self._node_paths.get(iid)
        if not path:
            return

        try:
            entries = [
                e for e in os.scandir(normalize(path))
                if e.is_dir(follow_symlinks=False)
            ]
            entries.sort(key=lambda e: e.name.lower())
        except PermissionError:
            denied_iid = self._tree.insert(iid, "end",
                text="  \U0001f512 Access denied", tags=("denied",))
            self._node_paths[denied_iid] = None
            return
        except OSError:
            return

        for entry in entries:
            child_iid = self._insert_node(iid, entry.path, f"  {entry.name}")
            if self._has_subdirs(entry.path):
                self._insert_dummy(child_iid)

    def _has_subdirs(self, path: str) -> bool:
        """Quick check — stops at the first subdir found."""
        try:
            with os.scandir(normalize(path)) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            return True
                    except OSError:
                        pass
        except (PermissionError, OSError):
            pass
        return False

    # ------------------------------------------------------------------
    # Click handlers
    # ------------------------------------------------------------------

    def _on_click(self, event: tk.Event) -> None:
        """Single click: highlight + expand/collapse. No navigation."""
        if self._tree.identify_region(event.x, event.y) == "separator":
            return
        item = self._tree.identify_row(event.y)
        if not item:
            return
        path = self._node_paths.get(item)
        if not path:
            return

        self._set_current(item)

        # Toggle open/close on the row text area
        # (the triangle already handles it natively, but clicking the label should too)
        region = self._tree.identify_region(event.x, event.y)
        if region in ("cell", "text", "image"):
            if self._tree.item(item, "open"):
                self._tree.item(item, open=False)
            else:
                self._load_children(item)
                self._tree.item(item, open=True)

        # Return keyboard focus to the main frame so arrow keys keep working
        if self.focus_back_cb:
            self.focus_back_cb()

    def _on_double_click(self, event: tk.Event) -> str:
        """Double click: navigate to the directory."""
        if self._tree.identify_region(event.x, event.y) == "separator":
            return "break"
        item = self._tree.identify_row(event.y)
        if not item:
            return "break"
        path = self._node_paths.get(item)
        if path:
            self.navigate_cb(path)
        return "break"

    # ------------------------------------------------------------------
    # Public API — called by App._navigate on every navigation
    # ------------------------------------------------------------------

    def load_dir(self, path: str) -> None:
        """Highlight the current node and expand the tree to it if needed."""
        self._set_current(None)  # clear old highlight

        key = self._key(path)
        iid = self._path_nodes.get(key)

        if iid and self._tree.exists(iid):
            self._set_current(iid)
            self._tree.see(iid)
        else:
            # Expand path components until we reach (or get as close as possible to) path
            self._expand_to(path)

    # ------------------------------------------------------------------
    # Highlight helpers
    # ------------------------------------------------------------------

    def _set_current(self, iid: str | None) -> None:
        if self._current_iid and self._tree.exists(self._current_iid):
            tags = [t for t in self._tree.item(self._current_iid, "tags")
                    if t != "current"]
            self._tree.item(self._current_iid, tags=tags)
        self._current_iid = iid
        if iid and self._tree.exists(iid):
            tags = list(self._tree.item(iid, "tags"))
            if "current" not in tags:
                tags.append("current")
            self._tree.item(iid, tags=tags)

    # ------------------------------------------------------------------
    # Auto-expand to navigated path
    # ------------------------------------------------------------------

    def _expand_to(self, path: str) -> None:
        """Walk down the tree, loading children at each level, until we reach path."""
        display = to_display(path)
        try:
            parts = Path(display).parts   # ('C:\\', 'Users', 'Projects', ...)
        except Exception:
            return
        if not parts:
            return

        # Find the drive root node
        drive = parts[0]
        current_iid = self._path_nodes.get(self._key(drive))
        if not current_iid:
            return

        accumulated = drive
        for part in parts[1:]:
            # Expand and load children of the current level
            self._tree.item(current_iid, open=True)
            self._load_children(current_iid)

            accumulated = str(Path(accumulated) / part)
            child_iid = self._path_nodes.get(self._key(accumulated))
            if child_iid:
                current_iid = child_iid
            else:
                break   # path not found in tree (e.g. hidden or error)

        self._set_current(current_iid)
        self._tree.see(current_iid)
