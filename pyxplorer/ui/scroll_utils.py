from __future__ import annotations

from tkinter import ttk


def make_autohide_pack_setter(scrollbar: ttk.Scrollbar, **pack_kwargs):
    """Return a y/xscrollcommand callback that auto-hides a pack-managed scrollbar."""
    visible = {"value": True}
    scrollbar.pack(**pack_kwargs)

    def _set(lo, hi):
        lo_f = float(lo)
        hi_f = float(hi)
        needed = lo_f > 0.0 or hi_f < 1.0
        if needed and not visible["value"]:
            scrollbar.pack(**pack_kwargs)
            visible["value"] = True
        elif not needed and visible["value"]:
            scrollbar.pack_forget()
            visible["value"] = False
        scrollbar.set(lo, hi)

    _set(0.0, 1.0)
    return _set


def make_autohide_grid_setter(scrollbar: ttk.Scrollbar, **grid_kwargs):
    """Return a y/xscrollcommand callback that auto-hides a grid-managed scrollbar."""
    visible = {"value": True}
    scrollbar.grid(**grid_kwargs)

    def _set(lo, hi):
        lo_f = float(lo)
        hi_f = float(hi)
        needed = lo_f > 0.0 or hi_f < 1.0
        if needed and not visible["value"]:
            scrollbar.grid()
            visible["value"] = True
        elif not needed and visible["value"]:
            scrollbar.grid_remove()
            visible["value"] = False
        scrollbar.set(lo, hi)

    _set(0.0, 1.0)
    return _set
