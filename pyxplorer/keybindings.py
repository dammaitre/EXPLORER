"""
Phase 6 — Global keyboard shortcuts bound on the root Tk window.
All shortcuts fire regardless of which widget has focus, except where
a text entry is focused (clipboard ops are guarded to avoid conflicts).
"""
import os
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from .core.longpath import normalize, to_display
from .core.fs import copy_items, move_items, make_dir, delete_items, rename_item
from .ui.search_dialog import SearchDialog
from .settings import THEME as _T

_FONT = _T["font_family"]
_SZ   = _T["font_size_base"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _in_entry(root: tk.Tk) -> bool:
    """True when a text-input widget currently holds keyboard focus."""
    try:
        return isinstance(root.focus_get(), (tk.Entry, ttk.Entry, tk.Text))
    except Exception:
        return False


# ── File clipboard ─────────────────────────────────────────────────────────────

def _do_copy(state) -> None:
    if state.selection:
        state.clipboard = {"mode": "copy", "paths": list(state.selection)}


def _do_cut(state) -> None:
    if state.selection:
        state.clipboard = {"mode": "cut", "paths": list(state.selection)}


_paste_busy = False   # simple re-entrancy guard (robocopy is synchronous)


def _do_paste(state, root: tk.Tk, refresh_cb) -> None:
    global _paste_busy
    if _paste_busy:
        return
    mode  = state.clipboard.get("mode")
    paths = state.clipboard.get("paths", [])
    if not mode or not paths:
        return
    dst = state.current_dir
    _paste_busy = True
    try:
        if mode == "copy":
            copy_items(paths, dst)
        else:
            move_items(paths, dst)
            state.clipboard = {"mode": None, "paths": []}
    except Exception as exc:
        messagebox.showerror("Paste error", str(exc), parent=root)
    finally:
        _paste_busy = False
    refresh_cb()


# ── Path copy to system clipboard ──────────────────────────────────────────────

def _copy_path(root: tk.Tk, state) -> None:
    """Ctrl+Shift+C — copy display path(s) to the OS clipboard."""
    paths = state.selection if state.selection else [state.current_dir]
    text  = "\n".join(to_display(p) for p in paths)
    root.clipboard_clear()
    root.clipboard_append(text)


# ── New folder dialog ──────────────────────────────────────────────────────────

def _new_folder_dialog(root: tk.Tk, state, refresh_cb) -> None:
    dlg = tk.Toplevel(root)
    dlg.title("New Folder")
    dlg.geometry("420x105")
    dlg.resizable(False, False)
    dlg.grab_set()

    ttk.Label(dlg, text="Folder name:", font=(_FONT, _SZ)).pack(
        anchor="w", padx=14, pady=(14, 2)
    )
    var   = tk.StringVar()
    entry = ttk.Entry(dlg, textvariable=var, font=(_FONT, _SZ))
    entry.pack(fill=tk.X, padx=14)
    entry.focus_set()

    def _create(event=None):
        name = var.get().strip()
        dlg.destroy()
        if not name:
            return
        new_path = os.path.join(state.current_dir, name)
        try:
            make_dir(new_path)
        except OSError as exc:
            messagebox.showerror("Create folder", str(exc), parent=root)
            return
        refresh_cb()

    entry.bind("<Return>", _create)
    btn_row = ttk.Frame(dlg)
    btn_row.pack(anchor="e", padx=14, pady=8)
    ttk.Button(btn_row, text="Create", command=_create,     width=8).pack(side=tk.LEFT, padx=2)
    ttk.Button(btn_row, text="Cancel", command=dlg.destroy, width=8).pack(side=tk.LEFT, padx=2)


def _rename_selected_dialog(root: tk.Tk, state, refresh_cb, focus_main_cb) -> None:
    """F2 — rename the currently selected item when selection is singular."""
    try:
        paths = list(state.selection)
        if len(paths) != 1:
            return

        src = paths[0]
        current_name = os.path.basename(to_display(src).rstrip("\\/"))
        if not current_name:
            return

        new_name = simpledialog.askstring(
            "Rename",
            "New name:",
            parent=root,
            initialvalue=current_name,
        )
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name or new_name == current_name:
            return

        try:
            new_path = rename_item(src, new_name)
        except Exception as exc:
            messagebox.showerror("Rename error", str(exc), parent=root)
            return

        state.selection = [new_path]
        refresh_cb()
    finally:
        root.after(0, focus_main_cb)


# ── Main entry point ───────────────────────────────────────────────────────────

def bind_keys(
    root: tk.Tk,
    state,
    top_bar,
    main_frame,
    open_pdf_cb=None,
    open_terminal_cb=None,
    open_notes_cb=None,
    hide_lower_cb=None,
    close_cb=None,
) -> None:
    """Attach all application-wide shortcuts to the root window."""

    def _refresh():
        main_frame.navigate_cb(state.current_dir)

    def _guard(fn):
        """Wrap fn so it silently does nothing when a text entry is focused."""
        return lambda e=None: None if _in_entry(root) else fn()

    # ── File clipboard ─────────────────────────────────────────────────
    root.bind("<Control-c>", _guard(lambda: _do_copy(state)))
    root.bind("<Control-x>", _guard(lambda: _do_cut(state)))
    root.bind("<Control-v>", _guard(lambda: _do_paste(state, root, _refresh)))

    # ── Path string to system clipboard (Ctrl+Shift+C) ─────────────────
    # In tkinter, capital letter in binding implies Shift is held
    root.bind("<Control-C>", lambda e: _copy_path(root, state))

    # ── New folder (Ctrl+Shift+N) ──────────────────────────────────────
    root.bind("<Control-N>", _guard(lambda: _new_folder_dialog(root, state, _refresh)))

    # ── Run dialog ─────────────────────────────────────────────────────
    root.bind("<Control-r>", lambda e: top_bar.open_run_dialog())

    # ── Regex search (Ctrl+F) ──────────────────────────────────────────
    # Keep a single dialog instance; re-raise if already open.
    _search_holder: list[SearchDialog] = []

    def _open_search():
        if _search_holder and _search_holder[0].alive:
            _search_holder[0].lift()
        else:
            dlg = SearchDialog(root, state, navigate_cb=main_frame.navigate_cb)
            if _search_holder:
                _search_holder[0] = dlg
            else:
                _search_holder.append(dlg)

    root.bind("<Control-f>", _guard(_open_search))

    # ── Delete (Suppr) ─────────────────────────────────────────────────
    def _do_delete():
        paths = list(state.selection)
        if not paths:
            return
        names = "\n".join(f"  • {to_display(p)}" for p in paths[:10])
        if len(paths) > 10:
            names += f"\n  … and {len(paths) - 10} more"
        if not messagebox.askyesno(
            "Delete permanently",
            f"Permanently delete {len(paths)} item(s)?\n\n{names}",
            icon="warning",
            parent=root,
        ):
            return
        try:
            delete_items(paths)
        except Exception as exc:
            messagebox.showerror("Delete error", str(exc), parent=root)
        state.selection = []
        _refresh()

    root.bind("<Delete>", _guard(lambda: _do_delete()))

    # ── Rename (F2) ────────────────────────────────────────────────────
    root.bind("<F2>", _guard(lambda: _rename_selected_dialog(
        root, state, _refresh, main_frame._tree.focus_set
    )))

    # ── Lower PDF viewer (Ctrl+Alt+P) ─────────────────────────────────
    if open_pdf_cb is not None:
        root.bind("<Control-Alt-p>", lambda e: open_pdf_cb())
        root.bind("<Control-Alt-P>", lambda e: open_pdf_cb())

    # ── Lower terminal (Ctrl+Alt+T) ──────────────────────────────────
    if open_terminal_cb is not None:
        root.bind("<Control-Alt-t>", lambda e: open_terminal_cb())
        root.bind("<Control-Alt-T>", lambda e: open_terminal_cb())

    # ── Lower temp notes (Ctrl+Alt+N) ────────────────────────────────
    if open_notes_cb is not None:
        root.bind("<Control-Alt-n>", lambda e: open_notes_cb())
        root.bind("<Control-Alt-N>", lambda e: open_notes_cb())

    # ── Hide lower pane (Escape) ───────────────────────────────────────
    if hide_lower_cb is not None:
        root.bind("<Escape>", lambda e: hide_lower_cb())

    # ── Close window (Ctrl+W) ──────────────────────────────────────────
    if close_cb is None:
        close_cb = root.destroy
    root.bind("<Control-w>", lambda e: close_cb())
    root.bind("<Control-W>", lambda e: close_cb())

    # ── Navigation ─────────────────────────────────────────────────────
    def _focus_main_and_run(fn):
        def _wrapped(e=None):
            if _in_entry(root):
                return None
            main_frame._tree.focus_set()
            return fn()
        return _wrapped

    root.bind("<Left>",      _focus_main_and_run(main_frame._go_up))
    root.bind("<Right>",     _focus_main_and_run(main_frame._open_selected))
    root.bind("<Up>",        _focus_main_and_run(main_frame._on_up))
    root.bind("<Down>",      _focus_main_and_run(main_frame._on_down))
    root.bind("<BackSpace>", _focus_main_and_run(main_frame._go_up))
