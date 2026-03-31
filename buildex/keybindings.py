"""
Phase 6 — Global keyboard shortcuts bound on the root Tk window.
All shortcuts fire regardless of which widget has focus, except where
a text entry is focused (clipboard ops are guarded to avoid conflicts).
"""
import os
import sys
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox

from .core.longpath import normalize, to_display
from .core.fs import copy_items, move_items, make_dir, delete_items
from .settings import THEME as _T

_FONT = _T["font_family"]
_SZ   = _T["font_size_base"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _in_entry(root: tk.Tk) -> bool:
    """True when a text entry currently holds keyboard focus."""
    try:
        return isinstance(root.focus_get(), (tk.Entry, ttk.Entry))
    except Exception:
        return False


# ── File clipboard ─────────────────────────────────────────────────────────────

def _do_copy(state) -> None:
    if state.selection:
        state.clipboard = {"mode": "copy", "paths": list(state.selection)}


def _do_cut(state) -> None:
    if state.selection:
        state.clipboard = {"mode": "cut", "paths": list(state.selection)}


def _do_paste(state, root: tk.Tk, refresh_cb) -> None:
    mode  = state.clipboard.get("mode")
    paths = state.clipboard.get("paths", [])
    if not mode or not paths:
        return
    dst = state.current_dir
    try:
        if mode == "copy":
            copy_items(paths, dst)
        else:
            move_items(paths, dst)
            state.clipboard = {"mode": None, "paths": []}
    except Exception as exc:
        messagebox.showerror("Paste error", str(exc), parent=root)
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


# ── Terminal opener (Phase 8 spec) ─────────────────────────────────────────────

def open_terminal(current_dir: str) -> None:
    """
    Open a terminal at current_dir.
    Priority on Windows: Windows Terminal → PowerShell 7 → PowerShell 5 → cmd.exe
    Never passes \\?\\ prefixed paths to the shell.
    """
    display_dir = to_display(current_dir)

    if sys.platform == "win32":
        for args in [
            ["wt.exe",          "-d",        display_dir],
            ["pwsh.exe",        "-NoExit", "-Command", f"Set-Location '{display_dir}'"],
            ["powershell.exe",  "-NoExit", "-Command", f"Set-Location '{display_dir}'"],
        ]:
            try:
                subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_CONSOLE)
                return
            except FileNotFoundError:
                continue
        # Last resort: cmd.exe
        subprocess.Popen(
            ["cmd.exe", "/K", f"cd /d \"{display_dir}\""],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Terminal", display_dir])
    else:
        for term in ["gnome-terminal", "konsole", "xterm"]:
            try:
                subprocess.Popen([term, "--working-directory", display_dir])
                return
            except FileNotFoundError:
                continue


# ── Search stub (replaced in Phase 7) ─────────────────────────────────────────

def _search_stub(root: tk.Tk) -> None:
    dlg = tk.Toplevel(root)
    dlg.title("Search")
    dlg.geometry("320x80")
    dlg.resizable(False, False)
    ttk.Label(dlg, text="Regex search — coming in Phase 7",
              font=(_FONT, _SZ), padding=22).pack()
    dlg.grab_set()


# ── Main entry point ───────────────────────────────────────────────────────────

def bind_keys(root: tk.Tk, state, top_bar, main_frame) -> None:
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
    root.bind("<Control-f>", _guard(lambda: _search_stub(root)))

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

    # ── Terminal (Ctrl+Alt+T) ──────────────────────────────────────────
    root.bind("<Control-Alt-t>", lambda e: open_terminal(state.current_dir))
    root.bind("<Control-Alt-T>", lambda e: open_terminal(state.current_dir))

    # ── Navigation ─────────────────────────────────────────────────────
    def _nav(fn):
        return lambda e: None if _in_entry(root) else fn()

    root.bind("<Left>",      _nav(main_frame._go_up))
    root.bind("<BackSpace>", _nav(main_frame._go_up))
    root.bind("<Right>",     _nav(main_frame._open_selected))
