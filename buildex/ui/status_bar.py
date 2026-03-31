"""
Phase 5 — Status bar: animated scan spinner, item count, selection weight.
"""
import tkinter as tk
from tkinter import ttk

from settings import THEME as _T
from core.fs import fmt_size

_BG       = _T["status_bg"]
_TEXT     = _T["text"]
_TEXT_DIM = _T["text_mute"]
_ACCENT   = _T["accent"]
_FONT     = _T["font_family"]
_SZ_S     = _T["font_size_small"]

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_TICK_MS = 110   # spinner frame duration


class StatusBar(ttk.Frame):
    def __init__(self, parent, state):
        super().__init__(parent, style="StatusBar.TFrame", height=32)
        self.pack_propagate(False)
        self.state = state

        self._scanning:   bool = False
        self._spin_idx:   int  = 0
        self._n_items:    int  = 0
        self._total_size: int  = 0
        self._n_selected: int  = 0
        self._sel_size:   int  = 0

        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self._text_var = tk.StringVar(value="  Ready")
        ttk.Label(
            self,
            textvariable=self._text_var,
            style="StatusBar.TLabel",
            anchor="w",
            padding=(8, 0),
        ).pack(side=tk.LEFT, fill=tk.Y)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_scanning(self) -> None:
        """Show animated spinner. Call when a background scan begins."""
        self._scanning  = True
        self._spin_idx  = 0
        self._n_selected = 0
        self._tick()

    def stop_scanning(self, n_items: int, total_size: int) -> None:
        """Hide spinner and show summary. Call when scan_complete arrives."""
        self._scanning   = False
        self._n_items    = n_items
        self._total_size = total_size
        self._refresh()

    def update_selection(self, n_selected: int, sel_size: int) -> None:
        """Called whenever the selection changes in the main frame."""
        self._n_selected = n_selected
        self._sel_size   = sel_size
        if not self._scanning:
            self._refresh()

    def set_status(self, message: str) -> None:
        """Generic one-off message (used before any scan runs)."""
        if not self._scanning:
            self._text_var.set(f"  {message}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        if not self._scanning:
            return
        ch = _SPINNER[self._spin_idx % len(_SPINNER)]
        self._spin_idx += 1
        self._text_var.set(f"  {ch}  Scanning…")
        self.after(_TICK_MS, self._tick)

    def _refresh(self) -> None:
        if self._n_selected > 0:
            self._text_var.set(
                f"  {self._n_selected} selected"
                f"  —  {fmt_size(self._sel_size)}"
                f"    ({self._n_items} items  —  {fmt_size(self._total_size)} total)"
            )
        else:
            total_str = fmt_size(self._total_size) if self._total_size >= 0 else "—"
            self._text_var.set(
                f"  {self._n_items} items  —  {total_str}"
            )
