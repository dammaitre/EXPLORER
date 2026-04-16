import tkinter as tk
from tkinter import ttk
from pathlib import Path
from typing import Callable

from ..core.heuristics import list_heuristic_scripts, scripts_dir
from ..settings import THEME as _T

_FONT      = _T["font_family"]
_SZ        = _T["font_size_base"]
_SZ_S      = _T["font_size_small"]
_BG        = _T["bg"]
_TEXT_MUTE = _T["text_mute"]


class HeuristicsWindow:
    def __init__(
        self,
        root: tk.Tk,
        on_run_cb: Callable[[str, str], None],
        on_close_cb: Callable[[], None],
    ):
        self.root = root
        self._on_run_cb = on_run_cb
        self._on_close_cb = on_close_cb
        self._scripts: list[Path] = []
        self._buttons: list[ttk.Button] = []
        self._selected_idx: int = 0
        self.alive: bool = True

        self.win = tk.Toplevel(root)
        self.win.title("Pyxplorer Heuristics")
        self.win.geometry("420x520")
        self.win.minsize(340, 260)
        self.win.configure(bg=_BG)
        self.win.transient(root)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        self._build()
        self.win.bind("<Escape>", lambda e: self.close())
        self.win.bind("<Up>", self._on_up)
        self.win.bind("<Down>", self._on_down)
        self.win.bind("<Return>", self._on_enter)
        self.win.bind("<Control-h>", lambda e: self.close())
        self.win.bind("<Control-H>", lambda e: self.close())
        self.win.focus_set()

    def _build(self) -> None:
        shell = ttk.Frame(self.win)
        shell.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            shell,
            text="Heuristics (python script.py PATH)",
            font=(_FONT, _SZ_S),
            foreground=_TEXT_MUTE,
            anchor="w",
            padding=(12, 10),
        ).pack(fill=tk.X)

        self._list_frame = ttk.Frame(shell)
        self._list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self._scripts = list_heuristic_scripts()
        if not self._scripts:
            ttk.Label(
                self._list_frame,
                text=f"No .py scripts found in {scripts_dir()}",
                justify=tk.LEFT,
                anchor="w",
                font=(_FONT, _SZ_S),
                foreground=_TEXT_MUTE,
                padding=(6, 8),
            ).pack(fill=tk.X)
            return

        for i, script in enumerate(self._scripts):
            button = ttk.Button(
                self._list_frame,
                text=script.name,
                command=lambda idx=i: self.run_index(idx),
                width=48,
            )
            button.pack(fill=tk.X, pady=3)
            self._buttons.append(button)

        self._refresh_selection()

    def close(self) -> None:
        if not self.alive:
            return
        self.alive = False
        try:
            self.win.destroy()
        finally:
            self._on_close_cb()

    def run_index(self, index: int) -> None:
        if not self._scripts:
            return
        self._selected_idx = max(0, min(index, len(self._scripts) - 1))
        self._refresh_selection()
        script = self._scripts[self._selected_idx]
        self._on_run_cb(str(script), script.stem)

    def _refresh_selection(self) -> None:
        for i, button in enumerate(self._buttons):
            script_name = self._scripts[i].name
            if i == self._selected_idx:
                button.configure(style="LowerTabActive.TButton", text=f"▶ {script_name}")
                button.focus_set()
            else:
                button.configure(style="LowerTab.TButton", text=f"  {script_name}")

    def _on_up(self, event=None) -> str:
        if not self._scripts:
            return "break"
        self._selected_idx = (self._selected_idx - 1) % len(self._scripts)
        self._refresh_selection()
        return "break"

    def _on_down(self, event=None) -> str:
        if not self._scripts:
            return "break"
        self._selected_idx = (self._selected_idx + 1) % len(self._scripts)
        self._refresh_selection()
        return "break"

    def _on_enter(self, event=None) -> str:
        if self._scripts:
            self.run_index(self._selected_idx)
        return "break"
