"""
Phase 6 — Global keyboard shortcuts bound on the root Tk window.
All shortcuts fire regardless of which widget has focus, except where
a text entry is focused (clipboard ops are guarded to avoid conflicts).
"""
import os
import sys
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from .core.longpath import normalize, to_display
from .core.fs import copy_items, move_items, make_dir, delete_items, rename_item
from .core.shared_clipboard import (
    load_shared_clipboard,
    save_shared_clipboard,
    clear_shared_clipboard,
)
from .core import starred as _starred
from .core import tags as _tags
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
        paths = [p for p in state.selection if isinstance(p, str) and p]
        payload = {"mode": "copy", "paths": list(paths)}
        state.clipboard = payload
        save_shared_clipboard(payload["mode"], payload["paths"])


def _do_cut(state) -> None:
    if state.selection:
        paths = [p for p in state.selection if isinstance(p, str) and p]
        payload = {"mode": "cut", "paths": list(paths)}
        state.clipboard = payload
        save_shared_clipboard(payload["mode"], payload["paths"])


_paste_busy = False   # re-entrancy guard for async paste worker


def _do_paste(
    state,
    root: tk.Tk,
    refresh_cb,
    status_cb,
    transfer_start_cb=None,
    transfer_progress_cb=None,
    transfer_stop_cb=None,
) -> None:
    global _paste_busy
    if _paste_busy:
        status_cb("Paste already running…")
        return

    shared = load_shared_clipboard()
    mode = shared.get("mode")
    paths = [p for p in shared.get("paths", []) if isinstance(p, str) and p]
    if mode and paths:
        state.clipboard = {"mode": mode, "paths": list(paths)}
    else:
        mode = state.clipboard.get("mode")
        paths = [p for p in state.clipboard.get("paths", []) if isinstance(p, str) and p]

    if not mode or not paths:
        status_cb("Clipboard is empty")
        return

    dst = state.current_dir
    _paste_busy = True
    verb = "Copying" if mode == "copy" else "Moving"
    status_cb(f"{verb} {len(paths)} item(s)…")
    if transfer_start_cb is not None:
        root.after(0, lambda: transfer_start_cb(f"{verb} {len(paths)} item(s)…"))

    def _emit_progress(pct: int) -> None:
        if transfer_progress_cb is None:
            return
        root.after(0, lambda p=pct: transfer_progress_cb(p))

    def _worker() -> None:
        nonlocal mode, paths, dst
        err: Exception | None = None
        try:
            if mode == "copy":
                copy_items(paths, dst, progress_cb=_emit_progress)
            else:
                move_items(paths, dst, progress_cb=_emit_progress)
        except Exception as exc:
            err = exc

        def _finish() -> None:
            global _paste_busy
            _paste_busy = False
            if transfer_stop_cb is not None:
                transfer_stop_cb()
            if err is not None:
                status_cb(f"Paste failed: {err}")
                messagebox.showerror("Paste error", str(err), parent=root)
                return

            if mode == "cut":
                state.clipboard = {"mode": None, "paths": []}
                clear_shared_clipboard()
            else:
                save_shared_clipboard(mode, paths)

            status_cb(f"{verb} complete — {len(paths)} item(s)")
            refresh_cb()

        root.after(0, _finish)

    threading.Thread(target=_worker, daemon=True).start()


# ── Path copy to system clipboard ──────────────────────────────────────────────

def _copy_path(root: tk.Tk, state) -> None:
    """Ctrl+Shift+C — copy display path(s) to the OS clipboard."""
    paths = state.selection if state.selection else [state.current_dir]
    text  = "\n".join(f'"{to_display(p)}"' for p in paths)
    root.clipboard_clear()
    root.clipboard_append(text)


def _display_name(path: str) -> str:
    disp = to_display(path).rstrip("\\/")
    if not disp:
        return path
    name = os.path.basename(disp)
    return name or disp


def _copy_name(root: tk.Tk, state, main_frame, left_panel=None) -> None:
    """Ctrl+Shift+N — copy selected item name(s) to the OS clipboard."""
    try:
        focused = root.focus_get()
    except Exception:
        focused = None

    names: list[str] = []

    if left_panel is not None and focused is left_panel._tree:
        path = left_panel.get_current_path()
        if path:
            names = [_display_name(path)]
    elif state.selection:
        names = [_display_name(p) for p in state.selection if isinstance(p, str) and p]
    else:
        names = [_display_name(state.current_dir)]

    if not names:
        return

    text = "\n".join(names)
    root.clipboard_clear()
    root.clipboard_append(text)


def _set_tag_dialog(root: tk.Tk, state, refresh_cb, status_cb) -> None:
    """Ctrl+T — set or clear a tag on selected items."""
    paths = [p for p in state.selection if isinstance(p, str) and p]
    if not paths:
        status_cb("Tagging skipped: no selected item")
        return

    initial = _tags.get_tag(paths[0]) if len(paths) == 1 else ""
    value = simpledialog.askstring(
        "Set tag",
        "Tag for selected item(s) (leave empty to clear):",
        parent=root,
        initialvalue=initial or "",
    )
    if value is None:
        status_cb("Tagging cancelled")
        return

    cleaned = value.strip()
    count = _tags.set_tag_bulk(paths, cleaned if cleaned else None)
    refresh_cb()

    if cleaned:
        status_cb(f"Tag '{cleaned}' set on {count} item(s)")
    else:
        status_cb(f"Tag cleared on {count} item(s)")


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


def _open_new_window(path: str) -> None:
    target = to_display(path)
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen([sys.executable, "-m", "pyxplorer", target], **kwargs)


# ── Main entry point ───────────────────────────────────────────────────────────

def bind_keys(
    root: tk.Tk,
    state,
    top_bar,
    main_frame,
    left_panel=None,
    open_pdf_cb=None,
    open_terminal_cb=None,
    open_notes_cb=None,
    toggle_heuristics_cb=None,
    hide_lower_cb=None,
    close_cb=None,
    status_cb=None,
    refresh_starred_cb=None,
    transfer_start_cb=None,
    transfer_progress_cb=None,
    transfer_stop_cb=None,
    cancel_pdf_load_cb=None,
    open_image_cb=None,
    cancel_image_load_cb=None,
    lower_panel_focus_cb=None,
    pdf_copy_image_cb=None,
    pdf_ocr_cb=None,
) -> None:
    """Attach all application-wide shortcuts to the root window."""

    def _refresh():
        main_frame.navigate_cb(state.current_dir)

    def _guard(fn):
        """Wrap fn so it silently does nothing when a text entry is focused."""
        return lambda e=None: None if _in_entry(root) else fn()

    if status_cb is None:
        status_cb = lambda message: None

    # ── File clipboard ─────────────────────────────────────────────────
    root.bind("<Control-c>", _guard(lambda: _do_copy(state)))
    root.bind("<Control-x>", _guard(lambda: _do_cut(state)))
    root.bind(
        "<Control-v>",
        _guard(
            lambda: _do_paste(
                state,
                root,
                _refresh,
                status_cb,
                transfer_start_cb=transfer_start_cb,
                transfer_progress_cb=transfer_progress_cb,
                transfer_stop_cb=transfer_stop_cb,
            )
        ),
    )

    # ── Path string to system clipboard (Ctrl+Shift+C) ─────────────────
    # In tkinter, capital letter in binding implies Shift is held
    root.bind("<Control-C>", lambda e: _copy_path(root, state))

    # ── Name string to system clipboard (Ctrl+Shift+N) ─────────────────
    root.bind("<Control-N>", _guard(lambda: _copy_name(root, state, main_frame, left_panel)))

    # ── New folder dialog (Ctrl+Shift+X) ───────────────────────────────
    root.bind("<Control-X>", _guard(lambda: _new_folder_dialog(root, state, _refresh)))

    # ── Set tag on selected item(s) (Ctrl+T) ───────────────────────────
    root.bind("<Control-t>", _guard(lambda: _set_tag_dialog(root, state, _refresh, status_cb)))
    root.bind("<Control-T>", _guard(lambda: _set_tag_dialog(root, state, _refresh, status_cb)))

    # ── New window at current dir (Ctrl+N) ─────────────────────────────
    root.bind("<Control-n>", _guard(lambda: _open_new_window(state.current_dir)))

    # ── Run dialog ─────────────────────────────────────────────────────
    root.bind("<Control-r>", lambda e: top_bar.open_run_dialog())

    # ── Regex search (Ctrl+F) ──────────────────────────────────────────
    # Keep a single dialog instance; re-raise if already open.
    _search_holder: list[SearchDialog] = []

    def _open_search():
        if _search_holder and _search_holder[0].alive:
            _search_holder[0].lift()
        else:
            dlg = SearchDialog(
                root,
                state,
                navigate_cb=main_frame.navigate_cb,
                open_pdf_cb=open_pdf_cb,
                open_image_cb=open_image_cb,
            )
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

    # ── PDF viewer: copy selection as image (Ctrl+I) ──────────────────
    if pdf_copy_image_cb is not None:
        root.bind("<Control-i>", lambda e: pdf_copy_image_cb() or "break")
        root.bind("<Control-I>", lambda e: pdf_copy_image_cb() or "break")

    # ── PDF viewer: OCR selection to text (Ctrl+O) ───────────────────
    if pdf_ocr_cb is not None:
        root.bind("<Control-o>", lambda e: pdf_ocr_cb() or "break")
        root.bind("<Control-O>", lambda e: pdf_ocr_cb() or "break")


    # ── Lower terminal (Ctrl+Alt+T) ──────────────────────────────────
    if open_terminal_cb is not None:
        root.bind("<Control-Alt-t>", lambda e: open_terminal_cb())
        root.bind("<Control-Alt-T>", lambda e: open_terminal_cb())

    # ── Lower temp notes (Ctrl+Alt+N) ────────────────────────────────────
    if open_notes_cb is not None:
        root.bind("<Control-Alt-n>", lambda e: open_notes_cb())
        root.bind("<Control-Alt-N>", lambda e: open_notes_cb())

    # ── Lower image viewer (Ctrl+Alt+I) ──────────────────────────────
    if open_image_cb is not None:
        root.bind("<Control-Alt-i>", lambda e: open_image_cb())
        root.bind("<Control-Alt-I>", lambda e: open_image_cb())

    # ── Heuristics window toggle (Ctrl+H) ────────────────────────────
    if toggle_heuristics_cb is not None:
        root.bind("<Control-h>", lambda e: toggle_heuristics_cb())
        root.bind("<Control-H>", lambda e: toggle_heuristics_cb())

    # ── Hide lower pane (Escape) ───────────────────────────────────────
    if hide_lower_cb is not None:
        def _on_escape(e=None):
            try:
                focused = root.focus_get()
            except Exception:
                focused = None
            if focused is main_frame._tree:
                main_frame.collapse_selection_to_last()
                return "break"
            # Cancel an in-progress PDF load before hiding the panel
            if cancel_pdf_load_cb is not None and cancel_pdf_load_cb():
                return "break"
            # Cancel an in-progress image load before hiding the panel
            if cancel_image_load_cb is not None and cancel_image_load_cb():
                return "break"
            hide_lower_cb()
            return "break"

        root.bind("<Escape>", _on_escape)

    # ── Close window (Ctrl+W) ───────────────────────────────────────────────
    if close_cb is None:
        close_cb = root.destroy
    root.bind("<Control-w>", lambda e: close_cb())
    root.bind("<Control-W>", lambda e: close_cb())

    # ── Star toggle (Ctrl+S) ─────────────────────────────────────────────
    def _do_toggle_star():
        path = main_frame.toggle_star_selected()
        if path is None:
            return
        verb = "★ Starred" if _starred.is_starred(path) else "☆ Unstarred"
        name = os.path.basename(to_display(path).rstrip("\\/")) or path
        status_cb(f"{verb}: {name}")
        if refresh_starred_cb is not None:
            refresh_starred_cb()

    root.bind("<Control-s>", _guard(lambda: _do_toggle_star()))
    root.bind("<Control-S>", _guard(lambda: _do_toggle_star()))

    # ── Jump to starred (Alt+Up / Alt+Down) ──────────────────────────────
    def _jump_starred(direction: int, event=None) -> str:
        """Move selection to previous (-1) or next (+1) starred item in list order."""
        if _in_entry(root):
            return "break"
        starred_iids = main_frame.get_starred_iids_in_order()
        if not starred_iids:
            status_cb("No starred items in this directory")
            return "break"
        sel = main_frame._tree.selection()
        current = sel[0] if sel else None
        if current in starred_iids:
            idx = starred_iids.index(current)
            target = starred_iids[(idx + direction) % len(starred_iids)]
        else:
            # jump to first (down) or last (up) starred item
            target = starred_iids[0] if direction > 0 else starred_iids[-1]
        main_frame._select_item(target)
        main_frame._tree.focus_set()
        return "break"

    def _bind_star_jump(widget, sequence: str, direction: int) -> None:
        widget.bind(sequence, lambda e, d=direction: _jump_starred(d, e))

    # Tk modifier names vary by platform/keyboard layout; bind common aliases.
    jump_sequences = [
        ("<Alt-Up>", -1),
        ("<Alt-Down>", +1),
        ("<Alt-KeyPress-Up>", -1),
        ("<Alt-KeyPress-Down>", +1),
        ("<Meta-Up>", -1),
        ("<Meta-Down>", +1),
        ("<Option-Up>", -1),
        ("<Option-Down>", +1),
    ]
    for seq, direction in jump_sequences:
        _bind_star_jump(root, seq, direction)
        _bind_star_jump(main_frame._tree, seq, direction)

    # ── Navigation ─────────────────────────────────────────────────────────
    def _focus_main_and_run(fn):
        def _wrapped(e=None):
            if _in_entry(root):
                return None
            if lower_panel_focus_cb is not None and lower_panel_focus_cb():
                return "break"
            main_frame._tree.focus_set()
            return fn()
        return _wrapped

    def _run_main_navigation_without_focus(fn):
        def _wrapped(e=None):
            if _in_entry(root):
                return None
            return fn()
        return _wrapped

    root.bind("<Left>",      _run_main_navigation_without_focus(main_frame._go_up))
    root.bind("<Right>",     _run_main_navigation_without_focus(main_frame._open_selected))
    root.bind("<Up>",        _run_main_navigation_without_focus(main_frame._on_up))
    root.bind("<Down>",      _run_main_navigation_without_focus(main_frame._on_down))
    root.bind("<BackSpace>", _focus_main_and_run(main_frame._go_up))
