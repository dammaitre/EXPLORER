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
import subprocess
import string
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from pathlib import Path
from typing import Callable

from ..core.longpath import normalize, to_display
from ..core import starred as _starred
from ..settings import THEME as _T, START_DIRS, SCROLL_SPEED
from .scroll_utils import make_autohide_pack_setter

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
_SCROLL_SPEED = SCROLL_SPEED
_STARRED_MIN_PATH_PIXELS = 80


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
        self.focus_back_cb: Callable[[], None] | None = None  # set by App after layout is built

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

        # ── Starred section ────────────────────────────────────────────
        self._starred_frame = tk.Frame(self, bg=_BG_PANEL)
        self._starred_frame.pack(side=tk.TOP, fill=tk.X)

        self._starred_label = tk.Label(
            self._starred_frame,
            text="  ★ Starred",
            bg=_BG_PANEL,
            fg=_ACCENT,
            font=(_FONT, _SZ, "bold"),
            anchor="w",
        )
        self._starred_label.pack(side=tk.TOP, fill=tk.X, pady=(6, 0))

        self._starred_list = tk.Listbox(
            self._starred_frame,
            bg=_BG_PANEL,
            fg=_TEXT,
            selectbackground=_BG_PANEL,
            selectforeground=_ACCENT,
            font=(_FONT, _SZ),
            borderwidth=0,
            highlightthickness=0,
            activestyle="none",
            height=0,   # hidden until there are entries
        )
        self._starred_list.pack(side=tk.TOP, fill=tk.X, padx=4)
        self._starred_list.bind("<Button-1>",        self._on_starred_click)
        self._starred_list.bind("<Double-Button-1>", self._on_starred_click)
        self._starred_list.bind("<Button-2>",        self._on_starred_middle_click)
        self._starred_list.bind("<MouseWheel>",      self._on_starred_wheel)
        self._starred_list.bind("<Configure>",       self._on_starred_resize)
        self._starred_frame.bind("<Configure>",      self._on_starred_resize)

        self._starred_sep = ttk.Separator(self, orient="horizontal")
        self._starred_sep.pack(side=tk.TOP, fill=tk.X, pady=2)

        # Internal map: listbox index -> full path
        self._starred_paths: list[str] = []
        self._starred_render_after: str | None = None
        self._starred_font = tkfont.Font(font=self._starred_list.cget("font"))

        self._build_nav_tree()

    def _build_nav_tree(self) -> None:
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
        set_vsb = make_autohide_pack_setter(vsb, side=tk.RIGHT, fill=tk.Y)
        self._tree.configure(yscrollcommand=set_vsb)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._tree.bind("<<TreeviewOpen>>",   self._on_expand)
        self._tree.bind("<Button-1>",        self._on_click)
        self._tree.bind("<Double-Button-1>", self._on_double_click)
        self._tree.bind("<Button-2>",        self._on_middle_click)
        self._tree.bind("<MouseWheel>",      self._on_tree_wheel)

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
            # Always add a dummy so the expand arrow appears without an extra
            # os.scandir per child (the old _has_subdirs N+1 pattern).
            # If the directory turns out to be empty, the dummy is removed when
            # the user expands it and _load_children finds no children.
            self._insert_dummy(child_iid)

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

    def _on_middle_click(self, event: tk.Event) -> str:
        """Middle click: open the clicked directory in a new Pyxplorer window."""
        item = self._tree.identify_row(event.y)
        if not item:
            return "break"
        path = self._node_paths.get(item)
        if not path:
            return "break"

        target = to_display(path)
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen([sys.executable, "-m", "pyxplorer", target], **kwargs)
        return "break"

    # ------------------------------------------------------------------
    # Public API — starred list
    # ------------------------------------------------------------------

    def _truncate_left_to_width(self, text: str, max_width_px: int) -> str:
        if max_width_px <= 0:
            return "..."
        if self._starred_font.measure(text) <= max_width_px:
            return text
        ell = "..."
        ell_w = self._starred_font.measure(ell)
        if ell_w >= max_width_px:
            return ell

        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            candidate = ell + text[-mid:]
            if self._starred_font.measure(candidate) <= max_width_px:
                lo = mid
            else:
                hi = mid - 1
        return ell + text[-lo:] if lo > 0 else ell

    def _format_starred_label(self, path: str) -> str:
        display = to_display(path).rstrip("\\/")
        if not display:
            display = path
        prefix = "  \u2605  "
        listbox_width = self._starred_list.winfo_width()
        if listbox_width <= 1:
            listbox_width = max(self._starred_frame.winfo_width() - 8, 1)
        path_width = max(
            _STARRED_MIN_PATH_PIXELS,
            listbox_width - self._starred_font.measure(prefix) - 12,
        )
        display = self._truncate_left_to_width(display, path_width)
        return prefix + display

    def _render_starred_labels(self) -> None:
        self._starred_list.delete(0, tk.END)
        for path in self._starred_paths:
            self._starred_list.insert(tk.END, self._format_starred_label(path))

    def _schedule_starred_render(self) -> None:
        if self._starred_render_after is not None:
            try:
                self.after_cancel(self._starred_render_after)
            except Exception:
                pass
        self._starred_render_after = self.after_idle(self._flush_starred_render)

    def _flush_starred_render(self) -> None:
        self._starred_render_after = None
        self._render_starred_labels()

    def refresh_starred(self) -> None:
        """Rebuild the starred listbox from the persisted store."""
        self._starred_paths = list(_starred.all_starred())
        # Show/hide the listbox and separator dynamically
        if self._starred_paths:
            self._starred_list.configure(height=min(len(self._starred_paths), 8))
            self._starred_list.pack(side=tk.TOP, fill=tk.X, padx=4)
            self._starred_sep.pack(side=tk.TOP, fill=tk.X, pady=2)
            self._schedule_starred_render()
        else:
            self._starred_list.delete(0, tk.END)
            self._starred_list.pack_forget()
            self._starred_sep.pack_forget()

    def _on_starred_resize(self, event=None) -> None:
        if self._starred_paths:
            self._schedule_starred_render()

    def _on_starred_click(self, event: tk.Event) -> None:
        idx = self._starred_list.nearest(event.y)
        if 0 <= idx < len(self._starred_paths):
            path = self._starred_paths[idx]
            if os.path.isdir(normalize(path)):
                self.navigate_cb(path)
            if self.focus_back_cb:
                self.focus_back_cb()

    def _on_starred_middle_click(self, event: tk.Event) -> str:
        idx = self._starred_list.nearest(event.y)
        if not (0 <= idx < len(self._starred_paths)):
            return "break"

        path = self._starred_paths[idx]
        if not os.path.isdir(normalize(path)):
            return "break"

        target = to_display(path)
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen([sys.executable, "-m", "pyxplorer", target], **kwargs)
        return "break"

    def _wheel_units(self, delta: int) -> int:
        units = int((-delta / 120) * _SCROLL_SPEED)
        if units == 0 and delta != 0:
            units = -1 if delta > 0 else 1
        return units

    def _on_tree_wheel(self, event: tk.Event) -> str:
        self._tree.yview_scroll(self._wheel_units(event.delta), "units")
        return "break"

    def _on_starred_wheel(self, event: tk.Event) -> str:
        self._starred_list.yview_scroll(self._wheel_units(event.delta), "units")
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
