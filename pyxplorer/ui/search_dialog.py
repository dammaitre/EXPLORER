"""
Phase 7 — Ctrl+F regex search dialog.

Opens a Toplevel with a pattern Entry and an incremental result Treeview.
Search runs in a daemon thread using the same CancelToken pattern as the
size scanner. Results stream in via a queue polled every 100 ms.
"""
import os
import re
import sys
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import ttk

from ..core.longpath import normalize, to_display
from ..core.scanner import CancelToken
from ..core.search import search_names
from ..core.fs import fmt_size
from ..core.heuristics import list_heuristic_scripts, run_heuristic
from ..settings import EXT_SKIPPED
from ..settings import THEME as _T
from . import icons as _icons_mod
from .scroll_utils import make_autohide_pack_setter

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

    def __init__(self, root: tk.Tk, state, navigate_cb,
                 open_pdf_cb=None, open_image_cb=None, focus_main_cb=None):
        self._root        = root
        self._state       = state
        self._navigate_cb = navigate_cb
        self._open_pdf_cb = open_pdf_cb
        self._open_image_cb = open_image_cb
        self._focus_main_cb = focus_main_cb
        self._original_dir = normalize(state.current_dir)  # Capture original dir when dialog opens

        self._token:      CancelToken | None = None
        self._queue:      queue.Queue        = queue.Queue()
        self._size_tasks: queue.Queue        = queue.Queue()
        self._poll_id:    str | None         = None
        self._debounce_id:str | None         = None
        self._result_meta: dict[str, dict]   = {}
        self._size_token: int                = 0
        self._heuristic_token: int           = 0
        self._heuristic_picker: tk.Toplevel | None = None
        self._icons: dict                    = _icons_mod.load(self._root, size=16)
        self._size_worker_stop = threading.Event()
        self._size_worker = threading.Thread(target=self._size_worker_loop, daemon=True)
        self._size_worker.start()
        
        # Search scope: "current" (default) | "selected" | "original"
        self._search_scope = tk.StringVar(value="current")
        self._current_search_root = self._original_dir  # Default to original dir at start
        self._limit_results_var = tk.BooleanVar(value=True)

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

        # ── Search scope buttons (mutually exclusive) ─────────────────────
        scope_row = ttk.Frame(dlg, style="TFrame")
        scope_row.pack(fill=tk.X, padx=14, pady=(2, 4))

        ttk.Label(
            scope_row, text="Search in:",
            font=(_FONT, _SZ_S), foreground=TEXT_M,
        ).pack(side=tk.LEFT, padx=(0, 10))

        scope_buttons = [
            ("Current dir", "current"),
            ("Selected dirs", "selected"),
            ("Original dir", "original"),
        ]

        for label, value in scope_buttons:
            ttk.Radiobutton(
                scope_row,
                text=label,
                variable=self._search_scope,
                value=value,
                command=self._on_scope_changed,
            ).pack(side=tk.LEFT, padx=2)

        ttk.Checkbutton(
            scope_row,
            text="Only show first 50 results",
            variable=self._limit_results_var,
            command=self._on_scope_changed,
        ).pack(side=tk.LEFT, padx=(12, 0))

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
            columns=("path", "size", "heur"),
            show="tree headings",
            selectmode="browse",
        )
        self._tree.heading("#0", text="Name")
        self._tree.heading("path", text="Relative path")
        self._tree.heading("size", text="Size")
        self._tree.heading("heur", text="")
        self._tree.column("#0", width=250, stretch=False, minwidth=120, anchor="w")
        self._tree.column("path", width=380, stretch=True, minwidth=200, anchor="w")
        self._tree.column("size", width=110, stretch=False, minwidth=80, anchor="e")
        self._tree.column("heur", width=0, stretch=False, minwidth=0, anchor="w")

        self._tree.tag_configure(
            "dir",
            foreground=TEXT,
            font=(_FONT, _SZ, "bold"),
        )
        self._tree.tag_configure("file", foreground=TEXT)
        self._tree.tag_configure(
            "empty_dir",
            foreground=TEXT_M,
            font=(_FONT, _SZ, "bold"),
        )

        vsb = ttk.Scrollbar(tree_outer, orient="vertical",
                            command=self._tree.yview)
        set_vsb = make_autohide_pack_setter(vsb, side=tk.RIGHT, fill=tk.Y)
        self._tree.configure(yscrollcommand=set_vsb)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._tree.bind("<ButtonRelease-1>", self._on_left_click)
        self._tree.bind("<Button-2>",        self._on_middle_click)
        self._tree.bind("<ButtonRelease-2>", self._on_middle_click)
        self._tree.bind("<Double-1>",        self._on_double_click)
        self._tree.bind("<Return>",          self._on_double_click)
        self._tree.bind("<Up>",              self._on_up)
        self._tree.bind("<Down>",            self._on_down)
        self._tree.bind("<Control-h>",       self._on_run_heuristic_hotkey)
        self._tree.bind("<Control-H>",       self._on_run_heuristic_hotkey)
        # Ctrl+Shift+C: capital C in tkinter means Shift is held
        self._tree.bind("<Control-C>",       self._on_copy_path)
        self._dlg.bind("<Control-C>",        self._on_copy_path)
        # Ctrl+Shift+N: capital N in tkinter means Shift is held
        self._tree.bind("<Control-N>",       self._on_copy_name)
        self._dlg.bind("<Control-N>",        self._on_copy_name)
        self._dlg.bind("<Up>",               self._on_up)
        self._dlg.bind("<Down>",             self._on_down)
        self._dlg.bind("<Control-Alt-p>",    self._on_open_pdf)
        self._dlg.bind("<Control-Alt-P>",    self._on_open_pdf)
        self._dlg.bind("<Control-Alt-i>",    self._on_open_image)
        self._dlg.bind("<Control-Alt-I>",    self._on_open_image)
        self._dlg.bind("<Control-h>",        self._on_run_heuristic_hotkey)
        self._dlg.bind("<Control-H>",        self._on_run_heuristic_hotkey)
        self._dlg.bind("<Return>",           self._on_return_key)
        self._dlg.bind("<Tab>",              self._on_tab_to_main)

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

    def _on_scope_changed(self) -> None:
        """Triggered when search scope changes; restart search with new root."""
        self._start_search()

    def _start_search(self) -> None:
        pattern = self._pattern_var.get().strip()

        # Empty pattern → clear everything
        if not pattern:
            self._error_lbl.config(text="")
            self._entry.configure(style="TEntry")
            self._status_var.set("Type a regex pattern…")
            self._tree.delete(*self._tree.get_children())
            self._result_meta.clear()
            if self._token:
                self._token.cancel()
                self._token = None
            self._size_token += 1
            self._heuristic_token += 1
            self._tree.heading("heur", text="")
            self._tree.column("heur", width=0, stretch=False, minwidth=0, anchor="w")
            return

        # Determine search root based on selected scope
        scope = self._search_scope.get()
        if scope == "original":
            search_root = self._original_dir
        elif scope == "selected":
            # Use selected directories if any, else current dir
            selected = self._state.selection if self._state.selection else [self._state.current_dir]
            search_root = normalize(selected[0]) if selected else normalize(self._state.current_dir)
        else:  # "current"
            search_root = normalize(self._state.current_dir)

        max_results = 50 if bool(self._limit_results_var.get()) else None

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
        while not self._size_tasks.empty():
            try:
                self._size_tasks.get_nowait()
            except queue.Empty:
                break

        self._token = CancelToken()
        self._size_token += 1
        self._heuristic_token += 1
        self._tree.delete(*self._tree.get_children())
        self._result_meta.clear()
        self._tree.heading("heur", text="")
        self._tree.column("heur", width=0, stretch=False, minwidth=0, anchor="w")
        self._status_var.set("Searching…")
        self._current_search_root = search_root  # Store for use in polling

        threading.Thread(
            target=search_names,
            args=(search_root, pattern, self._queue, self._token, max_results),
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
                    root_dir = self._current_search_root  # Use stored search root
                    full_path = normalize(os.path.join(root_dir, rel))
                    parent_rel = os.path.dirname(rel) if rel else ""
                    if ftype == "file":
                        size_str, size_bytes = self._file_size_display(full_path)
                    else:
                        size_str, size_bytes = "—", -1

                    icon = self._icons.get("folder" if ftype == "dir" else "file")
                    iid = self._tree.insert(
                        "",
                        "end",
                        text=name,
                        image=icon,
                        values=(parent_rel, size_str, ""),
                        tags=("dir" if ftype == "dir" else "file",),
                    )
                    self._result_meta[iid] = {
                        "full_path": full_path,
                        "parent": os.path.dirname(full_path),
                        "ftype": ftype,
                        "size_bytes": size_bytes,
                    }
                    if ftype == "dir":
                        self._size_tasks.put((self._size_token, iid, full_path))

                    n = len(self._tree.get_children())
                    self._status_var.set(f"{n} result(s) so far…")

                elif kind == "search_size":
                    _, token, iid, size_bytes = msg
                    if token != self._size_token or not self._tree.exists(iid):
                        continue
                    meta = self._result_meta.get(iid)
                    if not meta:
                        continue
                    meta["size_bytes"] = size_bytes
                    self._tree.set(iid, "size", fmt_size(size_bytes))
                    if meta.get("ftype") == "dir" and size_bytes == 0:
                        self._tree.item(iid, tags=("empty_dir",))

                elif kind == "search_heuristic_result":
                    _, token, iid, value = msg
                    if token != self._heuristic_token or not self._tree.exists(iid):
                        continue
                    self._tree.set(iid, "heur", value)

                elif kind == "search_heuristic_done":
                    _, token, script_name, done, total = msg
                    if token != self._heuristic_token:
                        continue
                    self._status_var.set(
                        f"Heuristic '{script_name}' complete — {done}/{total} result(s)"
                    )

                elif kind == "search_done":
                    truncated = bool(msg[1]) if len(msg) > 1 else False
                    n = len(self._tree.get_children())
                    if n:
                        if truncated:
                            self._status_var.set(f"Showing first {n} result(s) (limit reached).")
                        else:
                            self._status_var.set(f"{n} result(s) found.")
                    else:
                        self._status_var.set("No results.")
                    self._token = None

                elif kind == "search_error":
                    self._status_var.set(f"Search error: {msg[1]}")
                    self._token = None

        except queue.Empty:
            pass

        self._schedule_poll()

    # ------------------------------------------------------------------
    # Result interaction helpers
    # ------------------------------------------------------------------

    def _result_paths(self, iid: str) -> tuple[str, str, str] | None:
        """
        Return (full_path, parent_dir, ftype) for a treeview row, or None.
        full_path  — absolute path to the file or directory
        parent_dir — directory that contains it
        ftype      — "dir" | "file"
        """
        meta = self._result_meta.get(iid)
        if not meta:
            return None
        full_path = meta.get("full_path")
        parent = meta.get("parent")
        ftype = meta.get("ftype")
        if not isinstance(full_path, str) or not isinstance(parent, str) or not isinstance(ftype, str):
            return None
        return full_path, parent, ftype

    @staticmethod
    def _file_size_display(path: str) -> tuple[str, int]:
        try:
            size = int(os.path.getsize(normalize(path)))
            return fmt_size(size), size
        except OSError:
            return "—", -1

    @staticmethod
    def _dir_size(path: str) -> int:
        total = 0
        stack = [normalize(path)]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                if EXT_SKIPPED and os.path.splitext(entry.name)[1].lower() in EXT_SKIPPED:
                                    continue
                                total += int(entry.stat(follow_symlinks=False).st_size)
                            elif entry.is_symlink() and entry.is_file(follow_symlinks=True):
                                if EXT_SKIPPED and os.path.splitext(entry.name)[1].lower() in EXT_SKIPPED:
                                    continue
                                try:
                                    total += int(entry.stat(follow_symlinks=True).st_size)
                                except OSError:
                                    pass
                        except OSError:
                            continue
            except OSError:
                continue
        return max(0, total)

    def _size_worker_loop(self) -> None:
        while not self._size_worker_stop.is_set():
            try:
                task = self._size_tasks.get(timeout=0.2)
            except queue.Empty:
                continue
            if task is None:
                break
            token, iid, path = task
            if token != self._size_token:
                continue
            size_bytes = self._dir_size(path)
            if token != self._size_token:
                continue
            self._queue.put(("search_size", token, iid, size_bytes))

    def _focused_iid(self) -> str | None:
        iid = self._tree.focus()
        return iid if iid else None

    def _move_selection(self, step: int) -> str:
        items = list(self._tree.get_children())
        if not items:
            return "break"
        current = self._focused_iid()
        if not current or current not in items:
            target = items[0] if step > 0 else items[-1]
        else:
            idx = items.index(current)
            target = items[(idx + step) % len(items)]
        self._tree.selection_set(target)
        self._tree.focus(target)
        self._tree.see(target)
        return "break"

    def _selected_result(self) -> tuple[str, str, str] | None:
        iid = self._focused_iid()
        if not iid:
            sel = self._tree.selection()
            if not sel:
                return None
            iid = sel[0]
        return self._result_paths(iid)

    def _on_up(self, event=None) -> str:
        return self._move_selection(-1)

    def _on_down(self, event=None) -> str:
        return self._move_selection(+1)

    def _on_return_key(self, event=None) -> str | None:
        result = self._selected_result()
        if not result:
            return  # nothing selected, let event pass through
        full_path, parent, ftype = result
        self._navigate_cb(full_path if ftype == "dir" else parent)
        self._dlg.lift()
        return "break"

    def _on_tab_to_main(self, event=None) -> str:
        if self._focus_main_cb:
            self._focus_main_cb()
        return "break"

    def _on_open_pdf(self, event=None) -> str:
        if self._open_pdf_cb is None:
            return "break"
        result = self._selected_result()
        if not result:
            return "break"
        full_path, _, ftype = result
        if ftype != "file" or not full_path.lower().endswith(".pdf"):
            self._status_var.set("Select a PDF result first.")
            return "break"
        self._state.selection = [full_path]
        self._open_pdf_cb()
        return "break"

    def _on_open_image(self, event=None) -> str:
        if self._open_image_cb is None:
            return "break"
        result = self._selected_result()
        if not result:
            return "break"
        full_path, _, ftype = result
        if ftype != "file":
            self._status_var.set("Select an image result first.")
            return "break"
        self._state.selection = [full_path]
        self._open_image_cb()
        return "break"

    def _on_run_heuristic_hotkey(self, event=None) -> str:
        scripts = list_heuristic_scripts()
        if not scripts:
            self._status_var.set("No heuristic scripts found.")
            return "break"
        if len(scripts) == 1:
            script = scripts[0]
            self._run_heuristic_for_results(str(script), script.stem)
            return "break"
        self._open_heuristic_picker(scripts)
        return "break"

    def _open_heuristic_picker(self, scripts) -> None:
        if self._heuristic_picker and self._heuristic_picker.winfo_exists():
            self._heuristic_picker.lift()
            self._heuristic_picker.focus_force()
            return

        win = tk.Toplevel(self._dlg)
        self._heuristic_picker = win
        win.title("Run heuristic on search results")
        win.geometry("420x360")
        win.transient(self._dlg)

        ttk.Label(
            win,
            text="Choose a heuristic script:",
            font=(_FONT, _SZ_S),
            foreground=TEXT_M,
        ).pack(anchor="w", padx=12, pady=(10, 6))

        lb = tk.Listbox(win, activestyle="dotbox", font=(_FONT, _SZ))
        lb.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        for script in scripts:
            lb.insert(tk.END, script.name)
        lb.selection_set(0)
        lb.focus_set()

        def _run_selected(event=None):
            sel = lb.curselection()
            if not sel:
                return "break"
            script = scripts[sel[0]]
            try:
                win.destroy()
            except Exception:
                pass
            self._run_heuristic_for_results(str(script), script.stem)
            return "break"

        def _close(event=None):
            try:
                win.destroy()
            except Exception:
                pass
            return "break"

        lb.bind("<Double-1>", _run_selected)
        lb.bind("<Return>", _run_selected)
        win.bind("<Return>", _run_selected)
        win.bind("<Escape>", _close)

    def _run_heuristic_for_results(self, script_path: str, script_name: str) -> None:
        iids = list(self._tree.get_children())
        if not iids:
            self._status_var.set("No search results to evaluate.")
            return

        self._heuristic_token += 1
        token = self._heuristic_token
        self._tree.heading("heur", text=script_name)
        self._tree.column("heur", width=220, stretch=True, minwidth=120, anchor="w")
        for iid in iids:
            if self._tree.exists(iid):
                self._tree.set(iid, "heur", "")

        self._status_var.set(f"Running heuristic '{script_name}' on {len(iids)} result(s)…")

        def _worker():
            done = 0
            total = len(iids)
            for iid in iids:
                if token != self._heuristic_token:
                    return
                meta = self._result_meta.get(iid)
                if not meta:
                    continue
                path = meta.get("full_path")
                if not isinstance(path, str):
                    continue
                try:
                    value = run_heuristic(script_path, to_display(path))
                except subprocess.TimeoutExpired:
                    value = "ERR: timeout"
                except Exception as exc:
                    value = f"ERR: {str(exc)[:80]}"
                done += 1
                self._queue.put(("search_heuristic_result", token, iid, value))
            self._queue.put(("search_heuristic_done", token, script_name, done, total))

        threading.Thread(target=_worker, daemon=True).start()

    # Left click — open file with OS default app / navigate into dir
    def _on_left_click(self, event: tk.Event) -> None:
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        result = self._result_paths(iid)
        if not result:
            return
        full_path, parent, ftype = result
        if ftype == "dir":
            self._navigate_cb(full_path)
            self._dlg.lift()
        else:
            self._open_file(full_path)

    # Middle click — open result directory in a new Pyxplorer window
    def _on_middle_click(self, event: tk.Event) -> None:
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        result = self._result_paths(iid)
        if not result:
            return
        full_path, parent, ftype = result
        target = full_path if ftype == "dir" else parent

        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen([sys.executable, "-m", "pyxplorer", target], **kwargs)

    # Double-click / Enter — same as before: navigate (to dir or file's parent)
    def _on_double_click(self, event=None) -> None:
        result = self._selected_result()
        if not result:
            return
        full_path, parent, ftype = result
        self._navigate_cb(full_path if ftype == "dir" else parent)
        self._dlg.lift()

    # Ctrl+Shift+C — copy absolute path(s) to OS clipboard
    def _on_copy_path(self, event=None) -> None:
        iid = self._focused_iid()
        if not iid:
            return
        result = self._result_paths(iid)
        if not result:
            return
        full_path, _, _ = result
        self._root.clipboard_clear()
        self._root.clipboard_append(full_path)

    # Ctrl+Shift+N — copy selected item name to OS clipboard
    def _on_copy_name(self, event=None) -> None:
        iid = self._focused_iid()
        if not iid:
            return
        result = self._result_paths(iid)
        if not result:
            return
        full_path, _, _ = result
        display = to_display(full_path).rstrip("\\/")
        name = os.path.basename(display) if display else full_path
        self._root.clipboard_clear()
        self._root.clipboard_append(name or full_path)

    # Open a file with the OS default application
    def _open_file(self, path: str) -> None:
        try:
            os.startfile(normalize(path))
        except AttributeError:
            try:
                cmd = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([cmd, path])
            except Exception:
                pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        if self._token:
            self._token.cancel()
        self._size_token += 1
        self._heuristic_token += 1
        self._size_worker_stop.set()
        self._size_tasks.put(None)
        if self._heuristic_picker and self._heuristic_picker.winfo_exists():
            try:
                self._heuristic_picker.destroy()
            except Exception:
                pass
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
