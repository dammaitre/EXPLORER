"""
Phase 2 — Top bar: editable path entry, 10-item history dropdown, breadcrumbs, Ctrl+R dialog.
"""
import os
import sys
import subprocess
import tkinter as tk
from tkinter import ttk
from pathlib import Path

from ..core.longpath import normalize, to_display
from ..settings import THEME as _T

_BG        = _T["bg"]
_BG_ENTRY  = _T["bg_entry"]
_ACCENT    = _T["accent"]
_TEXT      = _T["text"]
_TEXT_MUTE = _T["text_mute"]
_BORDER    = _T["border"]
_ROW_SEL   = _T["row_selected"]
_ROW_H     = _T["row_hover"]
_FONT      = _T["font_family"]
_SZ        = _T["font_size_base"]
_SZ_E      = _T["font_size_entry"]


class TopBar(ttk.Frame):
    def __init__(self, parent, state, navigate_cb):
        super().__init__(parent, style="TopBar.TFrame")
        self.state = state
        self.navigate_cb = navigate_cb
        self._history_popup: tk.Toplevel | None = None
        self._history_listbox: tk.Listbox | None = None
        self._editing_path: bool = False
        self._committed_path: str = ""
        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        # Row 1: path entry + dropdown arrow
        row1 = ttk.Frame(self, style="TopBar.TFrame", padding=(8, 8, 8, 2))
        row1.pack(fill=tk.X)

        self._path_var = tk.StringVar()
        self._entry = ttk.Entry(
            row1,
            textvariable=self._path_var,
            font=(_FONT, _SZ_E),
            style="Path.TEntry",
        )
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
        self._entry.configure(state="readonly")

        self._hist_btn = ttk.Button(
            row1, text="▾", width=2, style="Flat.TButton",
            command=self._toggle_history,
        )
        self._hist_btn.pack(side=tk.LEFT, padx=(3, 0))

        self._entry.bind("<Return>", self._on_enter)
        self._entry.bind("<Escape>", self._on_escape)
        self._entry.bind("<Button-1>", self._on_entry_single_click)
        self._entry.bind("<Double-Button-1>", self._on_entry_double_click)
        self._entry.bind("<FocusIn>", self._on_focus_in)
        self._entry.bind("<FocusOut>", self._on_focus_out)
        self._entry.bind("<Alt-Down>", lambda e: self._show_history())
        # Typing a new path closes the history popup
        self._entry.bind("<KeyPress>", self._on_keypress)

        # Row 2: breadcrumbs
        self._crumb_frame = ttk.Frame(self, style="TopBar.TFrame", padding=(8, 0, 8, 6))
        self._crumb_frame.pack(fill=tk.X)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_path(self, path: str) -> None:
        """Called after every successful navigation."""
        display = to_display(path)
        self._committed_path = display
        self._editing_path = False
        self._entry.configure(state="normal")
        self._path_var.set(display)
        self._entry.icursor(tk.END)
        self._entry.configure(state="readonly")
        self._build_breadcrumbs(display)

    def open_run_dialog(self) -> None:
        """Ctrl+R: Win+R style run dialog."""
        dlg = tk.Toplevel(self)
        dlg.title("Run")
        dlg.geometry("420x100")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.focus_set()

        ttk.Label(dlg, text="Open:", font=(_FONT, _SZ)).pack(
            anchor="w", padx=14, pady=(14, 2)
        )
        var = tk.StringVar()
        entry = ttk.Entry(dlg, textvariable=var, font=(_FONT, _SZ))
        entry.pack(fill=tk.X, padx=14)
        entry.focus_set()

        def _run(event=None):
            val = var.get().strip()
            dlg.destroy()
            if not val:
                return
            try:
                if sys.platform == "win32":
                    os.startfile(normalize(val))
                else:
                    cmd = "open" if sys.platform == "darwin" else "xdg-open"
                    subprocess.Popen([cmd, val])
            except Exception:
                try:
                    subprocess.run(val, shell=True)
                except Exception:
                    pass

        entry.bind("<Return>", _run)
        btn_row = ttk.Frame(dlg)
        btn_row.pack(anchor="e", padx=14, pady=8)
        ttk.Button(btn_row, text="OK", command=_run, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="Cancel", command=dlg.destroy, width=8).pack(
            side=tk.LEFT, padx=2
        )

    # ------------------------------------------------------------------
    # Breadcrumbs
    # ------------------------------------------------------------------

    def _build_breadcrumbs(self, display_path: str) -> None:
        for w in self._crumb_frame.winfo_children():
            w.destroy()

        try:
            parts = Path(display_path).parts  # ('C:\\', 'Users', ...) on Windows
        except Exception:
            return

        accumulated = ""
        for i, part in enumerate(parts):
            if i == 0:
                accumulated = part  # e.g. 'C:\\'
            else:
                accumulated = str(Path(accumulated) / part)

            if i > 0:
                ttk.Label(
                    self._crumb_frame,
                    text="›",
                    foreground=_TEXT_MUTE,
                    background=_BG,
                    font=(_FONT, _SZ),
                ).pack(side=tk.LEFT)

            label = part.rstrip("\\") or part  # 'C:\\' → 'C:'
            crumb_path = accumulated
            ttk.Button(
                self._crumb_frame,
                text=label,
                style="Breadcrumb.TButton",
                command=lambda p=crumb_path: self.navigate_cb(p),
            ).pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Entry handlers
    # ------------------------------------------------------------------

    def _on_entry_single_click(self, event=None) -> str | None:
        if self._editing_path:
            return None
        current = self._committed_path or to_display(self.state.current_dir)
        if current:
            self.clipboard_clear()
            self.clipboard_append(current)
        return "break"

    def _on_entry_double_click(self, event=None) -> str:
        self._editing_path = True
        self._entry.configure(state="normal")
        self._entry.focus_set()
        self._entry.selection_range(0, tk.END)
        self._entry.icursor(tk.END)
        if self.state.nav_history:
            self._show_history()
        return "break"

    def _on_enter(self, event=None) -> None:
        if not self._editing_path:
            return
        raw = self._path_var.get().strip()
        norm = normalize(raw)
        self._hide_history()
        if os.path.isdir(norm):
            self._editing_path = False
            self._entry.configure(state="readonly")
            self.navigate_cb(norm)
        else:
            self._flash_error()

    def _on_escape(self, event=None) -> str:
        self._hide_history()
        if self._editing_path:
            self._editing_path = False
            self._entry.configure(state="normal")
            self._path_var.set(self._committed_path or to_display(self.state.current_dir))
            self._entry.icursor(tk.END)
            self._entry.configure(state="readonly")
        return "break"

    def _on_focus_in(self, event=None) -> None:
        if self._editing_path and self.state.nav_history:
            self._show_history()

    def _on_focus_out(self, event=None) -> None:
        # Delay so a click on the popup can register before we destroy it
        self.after(180, self._hide_if_not_popup_focused)

    def _on_keypress(self, event: tk.Event) -> None:
        if not self._editing_path:
            return
        # Hide history as soon as the user starts typing a new path
        if event.keysym not in ("Alt_L", "Alt_R", "Down", "Up",
                                 "Control_L", "Control_R", "Shift_L", "Shift_R"):
            self._hide_history()

    def _flash_error(self) -> None:
        """Briefly tint the entry red to signal invalid path."""
        self._entry.configure(style="Error.TEntry")
        self.after(700, lambda: self._entry.configure(style="Path.TEntry"))

    # ------------------------------------------------------------------
    # History dropdown
    # ------------------------------------------------------------------

    def _toggle_history(self) -> None:
        if self._history_popup and self._history_popup.winfo_exists():
            self._hide_history()
        else:
            self._show_history()

    def _show_history(self) -> None:
        if not self.state.nav_history:
            return
        self._hide_history()  # close any stale popup

        popup = tk.Toplevel(self)
        popup.wm_overrideredirect(True)
        popup.configure(bg=_BORDER)

        listbox = tk.Listbox(
            popup,
            font=(_FONT, _SZ),
            background=_BG_ENTRY,
            foreground=_TEXT,
            selectbackground=_ROW_SEL,
            selectforeground=_TEXT,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=_BORDER,
            activestyle="none",
            relief="flat",
        )
        listbox.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        for path in self.state.nav_history:
            listbox.insert(tk.END, to_display(path))

        # Geometry: flush below the entry
        self._entry.update_idletasks()
        x = self._entry.winfo_rootx()
        y = self._entry.winfo_rooty() + self._entry.winfo_height() + 1
        w = self._entry.winfo_width() + self._hist_btn.winfo_width() + 3
        item_h = 22
        h = min(len(self.state.nav_history), 10) * item_h + 4
        popup.geometry(f"{w}x{h}+{x}+{y}")
        popup.lift()

        listbox.bind("<ButtonRelease-1>", lambda e: self._on_history_select(listbox))
        listbox.bind("<Return>", lambda e: self._on_history_select(listbox))
        listbox.bind("<Escape>", lambda e: self._hide_history())
        listbox.bind("<FocusOut>", lambda e: self.after(180, self._hide_if_not_popup_focused))

        self._history_popup = popup
        self._history_listbox = listbox

    def _on_history_select(self, listbox: tk.Listbox) -> None:
        sel = listbox.curselection()
        if sel:
            path = listbox.get(sel[0])
            self._hide_history()
            self.navigate_cb(normalize(path))

    def _hide_history(self) -> None:
        if self._history_popup and self._history_popup.winfo_exists():
            self._history_popup.destroy()
        self._history_popup = None
        self._history_listbox = None

    def _hide_if_not_popup_focused(self) -> None:
        """Only hide the popup if focus has moved somewhere outside it."""
        if not (self._history_popup and self._history_popup.winfo_exists()):
            return
        try:
            focused = self.focus_get()
        except Exception:
            focused = None
        in_popup = focused in (self._history_popup, self._history_listbox)
        in_entry = focused is self._entry
        if not in_popup and not in_entry:
            self._hide_history()
