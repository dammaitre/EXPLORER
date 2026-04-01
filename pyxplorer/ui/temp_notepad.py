import os
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from typing import Callable

from ..settings import THEME as _T
from .scroll_utils import make_autohide_pack_setter

_BG_DARK = _T["bg_dark"]
_TEXT = _T["text"]
_TEXT_MUTE = _T["text_mute"]
_FONT = _T["font_family"]
_SZ = _T["font_size_base"]
_SZ_S = _T["font_size_small"]

_SAVE_DELAY_MS = 250


class TempNotepad(ttk.Frame):
    def __init__(self, parent, root: tk.Tk, status_cb: Callable[[str], None] | None = None):
        super().__init__(parent, style="LowerContent.TFrame")
        self.root = root
        self._status_cb = status_cb or (lambda message: None)
        self._save_after: str | None = None
        self._temp_path = self._build_temp_path()

        self._build()

    @property
    def temp_path_display(self) -> str:
        return str(self._temp_path)

    def _build(self) -> None:
        header = ttk.Frame(self, style="LowerContent.TFrame")
        header.pack(side=tk.TOP, fill=tk.X)

        self._title_var = tk.StringVar(value="Temp notes")
        ttk.Label(
            header,
            textvariable=self._title_var,
            anchor="w",
            font=(_FONT, _SZ_S),
            foreground=_TEXT_MUTE,
            padding=(12, 8),
        ).pack(side=tk.LEFT)

        body = ttk.Frame(self, style="LowerContent.TFrame")
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self._text = tk.Text(
            body,
            wrap="word",
            bg=_BG_DARK,
            fg=_TEXT,
            insertbackground=_TEXT,
            selectbackground="#4A4A4A",
            font=("Consolas", _SZ),
            borderwidth=0,
            highlightthickness=0,
            padx=10,
            pady=8,
            undo=True,
            maxundo=-1,
            autoseparators=True,
        )
        self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        vsb = ttk.Scrollbar(body, orient="vertical", command=self._text.yview)
        set_vsb = make_autohide_pack_setter(vsb, side=tk.RIGHT, fill=tk.Y)
        self._text.configure(yscrollcommand=set_vsb)

        self._text.bind("<KeyRelease>", self._on_change)
        self._text.bind("<Control-c>", self._copy)
        self._text.bind("<Control-C>", self._copy)
        self._text.bind("<Control-x>", self._cut)
        self._text.bind("<Control-X>", self._cut)
        self._text.bind("<Control-v>", self._paste)
        self._text.bind("<Control-V>", self._paste)
        self._text.bind("<Control-a>", self._select_all)
        self._text.bind("<Control-A>", self._select_all)

    def load(self) -> None:
        self._cancel_pending_save()
        self._temp_path.parent.mkdir(parents=True, exist_ok=True)
        self._temp_path.write_text("", encoding="utf-8")

        self._text.delete("1.0", tk.END)
        self._title_var.set(f"Temp notes — {self._temp_path}")
        self._status_cb(f"Temp file reset: {self._temp_path}")
        self._text.focus_set()

    def shutdown(self) -> None:
        self._cancel_pending_save()
        try:
            self._temp_path.unlink(missing_ok=True)
        except Exception as exc:
            self._status_cb(f"Temp file delete error: {exc}")
            return
        self._status_cb("Temp file deleted")

    def focus_editor(self) -> None:
        self._text.focus_set()

    def _on_change(self, event=None) -> None:
        self._schedule_save()

    def _schedule_save(self) -> None:
        self._cancel_pending_save()
        self._save_after = self.after(_SAVE_DELAY_MS, self._save_now)

    def _cancel_pending_save(self) -> None:
        if self._save_after is None:
            return
        try:
            self.after_cancel(self._save_after)
        except Exception:
            pass
        self._save_after = None

    def _save_now(self) -> None:
        self._save_after = None
        text = self._text.get("1.0", "end-1c")
        self._temp_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._temp_path.write_text(text, encoding="utf-8")
        except Exception as exc:
            self._status_cb(f"Temp file write error: {exc}")

    def _copy(self, event=None) -> str:
        try:
            selected = self._text.selection_get()
        except Exception:
            selected = ""
        if selected:
            self.root.clipboard_clear()
            self.root.clipboard_append(selected)
        return "break"

    def _cut(self, event=None) -> str:
        try:
            selected = self._text.selection_get()
        except Exception:
            selected = ""
        if selected:
            self.root.clipboard_clear()
            self.root.clipboard_append(selected)
            self._text.delete("sel.first", "sel.last")
            self._schedule_save()
        return "break"

    def _paste(self, event=None) -> str:
        try:
            clipboard = self.root.clipboard_get()
        except Exception:
            clipboard = ""
        if clipboard:
            self._text.insert("insert", clipboard)
            self._schedule_save()
        return "break"

    def _select_all(self, event=None) -> str:
        self._text.tag_add("sel", "1.0", "end-1c")
        self._text.mark_set("insert", "1.0")
        self._text.see("insert")
        return "break"

    @staticmethod
    def _build_temp_path() -> Path:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            local_app_data = str(Path.home() / "AppData" / "Local")
        return Path(local_app_data) / "Pyxplorer" / "temp.txt"
