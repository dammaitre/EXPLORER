"""
Phase 7 — Ctrl+F regex search dialog.

Opens a Toplevel with a pattern Entry and an incremental result Treeview.
Search runs in a daemon thread using the same CancelToken pattern as the
size scanner. Results stream in via a queue polled every 100 ms.
"""
import os
import re
import queue
import threading
import tkinter as tk
from tkinter import ttk

from ..core.longpath import normalize, to_display
from ..core.scanner import CancelToken
from ..core.search import search_names
from ..settings import THEME as _T

_FONT   = _T["font_family"]
_SZ     = _T["font_size_base"]
_SZ_S   = _T["font_size_small"]
BG      = _T["bg"]
BG_DARK = _T["bg_dark"]
BG_ENTRY= _T["bg_entry"]
ACCENT  = _T["accent"]
TEXT    = _T["text"]
TEXT_M  = _T["text_mute"]
BORDER  = _T["border"]
ROW_SEL = _T["row_selected"]
ROW_H   = _T["row_hover"]


class SearchDialog:
    """
    Modal-ish (non-blocking) search dialog.
    Only one instance is kept alive — calling open() a second time just
    raises the existing window.
    """

    def __init__(self, root: tk.Tk, state, navigate_cb):
        self._root        = root
        self._state       = state
        self._navigate_cb = navigate_cb

        self._token:      CancelToken | None = None
        self._queue:      queue.Queue        = queue.Queue()
        self._poll_id:    str | None         = None
        self._debounce_id:str | None         = None

        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        dlg = tk.Toplevel(self._root)
        self._dlg = dlg
        dlg.title("Search")
        dlg.geometry("760x520")
        dlg.minsize(540, 320)
        dlg.configure(bg=BG)
        dlg.protocol("WM_DELETE_WINDOW", self._on_close)

        # Make it appear above the main window but not force-modal so the
        # user can still browse while results come in.
        dlg.transient(self._root)

        # ── Scope label ──────────────────────────────────────────────────
        scope = to_display(self._state.current_dir)
        ttk.Label(
            dlg, text=f"Search in:  {scope}",
            font=(_FONT, _SZ_S), foreground=TEXT_M,
        ).pack(anchor="w", padx=14, pady=(12, 2))

        # ── Pattern entry row ────────────────────────────────────────────
        entry_row = ttk.Frame(dlg, style="TFrame")
        entry_row.pack(fill=tk.X, padx=14, pady=(0, 2))

        self._pattern_var = tk.StringVar()
        self._entry = ttk.Entry(
            entry_row,
            textvariable=self._pattern_var,
            font=(_FONT, _SZ),
        )
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._error_lbl = ttk.Label(
            entry_row, text="", foreground="#FF6060",
            font=(_FONT, _SZ_S),
        )
        self._error_lbl.pack(side=tk.LEFT, padx=(10, 0))

        self._pattern_var.trace_add("write", self._on_pattern_changed)

        # ── Status bar ───────────────────────────────────────────────────
        self._status_var = tk.StringVar(value="Type a regex pattern…")
        ttk.Label(
            dlg, textvariable=self._status_var,
            font=(_FONT, _SZ_S), foreground=TEXT_M,
        ).pack(anchor="w", padx=14, pady=(0, 4))

        # ── Results treeview ─────────────────────────────────────────────
        tree_outer = ttk.Frame(dlg, style="TFrame")
        tree_outer.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))

        self._tree = ttk.Treeview(
            tree_outer,
            columns=("name", "path", "type"),
            show="headings",
            selectmode="browse",
        )
        self._tree.heading("name", text="Name")
        self._tree.heading("path", text="Relative path")
        self._tree.heading("type", text="Type")
        self._tree.column("name", width=220, stretch=False, minwidth=120)
        self._tree.column("path", width=460, stretch=True,  minwidth=200)
        self._tree.column("type", width=52,  stretch=False, minwidth=40)

        vsb = ttk.Scrollbar(tree_outer, orient="vertical",
                            command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self._tree.bind("<Double-1>",  self._on_double_click)
        self._tree.bind("<Return>",    self._on_double_click)

        self._entry.focus_set()

        # Start polling
        self._schedule_poll()

    # ------------------------------------------------------------------
    # Debounced search trigger
    # ------------------------------------------------------------------

    def _on_pattern_changed(self, *_) -> None:
        if self._debounce_id:
            self._dlg.after_cancel(self._debounce_id)
        self._debounce_id = self._dlg.after(300, self._start_search)

    def _start_search(self) -> None:
        pattern = self._pattern_var.get().strip()

        # Empty pattern → clear everything
        if not pattern:
            self._error_lbl.config(text="")
            self._entry.configure(style="TEntry")
            self._status_var.set("Type a regex pattern…")
            self._tree.delete(*self._tree.get_children())
            if self._token:
                self._token.cancel()
                self._token = None
            return

        # Validate regex before launching thread
        try:
            re.compile(pattern, re.IGNORECASE)
            self._error_lbl.config(text="")
            self._entry.configure(style="TEntry")
        except re.error as exc:
            self._error_lbl.config(text=f"  ✕ {exc}")
            self._entry.configure(style="Error.TEntry")
            return

        # Cancel any running search
        if self._token:
            self._token.cancel()
        # Flush leftover results from previous run
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        self._token = CancelToken()
        self._tree.delete(*self._tree.get_children())
        self._status_var.set("Searching…")

        threading.Thread(
            target=search_names,
            args=(self._state.current_dir, pattern, self._queue, self._token),
            daemon=True,
        ).start()

    # ------------------------------------------------------------------
    # Queue polling
    # ------------------------------------------------------------------

    def _schedule_poll(self) -> None:
        if self._dlg.winfo_exists():
            self._poll_id = self._dlg.after(100, self._poll)

    def _poll(self) -> None:
        try:
            while True:
                msg = self._queue.get_nowait()
                kind = msg[0]

                if kind == "search_result":
                    _, name, rel, ftype = msg
                    self._tree.insert("", "end", values=(name, rel, ftype))
                    n = len(self._tree.get_children())
                    self._status_var.set(f"{n} result(s) so far…")

                elif kind == "search_done":
                    n = len(self._tree.get_children())
                    self._status_var.set(
                        f"{n} result(s) found." if n else "No results."
                    )
                    self._token = None

                elif kind == "search_error":
                    self._status_var.set(f"Search error: {msg[1]}")
                    self._token = None

        except queue.Empty:
            pass

        self._schedule_poll()

    # ------------------------------------------------------------------
    # Double-click / Enter: navigate to result
    # ------------------------------------------------------------------

    def _on_double_click(self, event=None) -> None:
        iid = self._tree.focus()
        if not iid:
            return
        values = self._tree.item(iid, "values")
        if not values:
            return
        name, rel, ftype = values

        # Reconstruct absolute path: current_dir at time of search + rel path
        root_dir   = to_display(self._state.current_dir)
        full_path  = os.path.normpath(os.path.join(root_dir, rel))

        if ftype == "dir":
            target = full_path
        else:
            target = os.path.dirname(full_path)

        self._navigate_cb(target)
        self._dlg.lift()   # keep search window on top after navigation

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        if self._token:
            self._token.cancel()
        if self._poll_id:
            try:
                self._dlg.after_cancel(self._poll_id)
            except Exception:
                pass
        if self._debounce_id:
            try:
                self._dlg.after_cancel(self._debounce_id)
            except Exception:
                pass
        self._dlg.destroy()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def lift(self) -> None:
        """Raise an already-open dialog to the front."""
        if self._dlg.winfo_exists():
            self._dlg.lift()
            self._dlg.focus_force()

    @property
    def alive(self) -> bool:
        return self._dlg.winfo_exists()
