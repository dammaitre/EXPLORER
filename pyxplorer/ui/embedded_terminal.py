import os
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..core.longpath import normalize, to_display
from ..settings import THEME as _T
from .scroll_utils import make_autohide_grid_setter

try:
    from winpty import PtyProcess
except ImportError:
    PtyProcess = None

_BG_DARK = _T["bg_dark"]
_TEXT = _T.get("terminal_text", _T["text"])
_TEXT_MUTE = _T["text_mute"]
_FONT = _T["font_family"]
_SZ = _T["font_size_base"]
_SZ_S = _T["font_size_small"]

_READ_SIZE = 2048
_PUMP_MS = 30
_INPUT_BAR_H = 44

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\].*?(\x07|\x1b\\)", flags=re.DOTALL)


class EmbeddedTerminal(ttk.Frame):
    def __init__(self, parent, root: tk.Tk, status_cb: Callable[[str], None] | None = None):
        super().__init__(parent, style="LowerContent.TFrame")
        self.root = root
        self._status_cb = status_cb or (lambda message: None)
        self._proc = None
        self._reader_thread: threading.Thread | None = None
        self._queue: queue.Queue = queue.Queue()
        self._pump_after: str | None = None
        self._running = False
        self._cwd_display = ""

        self._build()

    def _build(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ttk.Frame(self, style="LowerContent.TFrame")
        header.grid(row=0, column=0, sticky="ew")

        self._title_var = tk.StringVar(value="Terminal")
        ttk.Label(
            header,
            textvariable=self._title_var,
            anchor="w",
            font=(_FONT, _SZ_S),
            foreground=_TEXT_MUTE,
            padding=(12, 8),
        ).pack(side=tk.LEFT)

        body = ttk.Frame(self, style="LowerContent.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)

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
            undo=False,
        )
        self._text.grid(row=0, column=0, sticky="nsew")
        self._text.configure(state="disabled")

        vsb = ttk.Scrollbar(body, orient="vertical", command=self._text.yview)
        set_vsb = make_autohide_grid_setter(vsb, row=0, column=1, sticky="ns")
        self._text.configure(yscrollcommand=set_vsb)

        ttk.Separator(self, orient="horizontal").grid(row=2, column=0, sticky="ew")

        entry_host = tk.Frame(
            self,
            background=_BG_DARK,
            height=_INPUT_BAR_H,
            highlightthickness=0,
            borderwidth=0,
        )
        entry_host.grid(row=3, column=0, sticky="ew")
        entry_host.pack_propagate(False)

        entry_row = tk.Frame(
            entry_host,
            background=_BG_DARK,
            highlightthickness=0,
            borderwidth=0,
        )
        entry_row.pack(fill=tk.BOTH, expand=True)

        prompt = tk.Label(
            entry_row,
            text=">",
            fg=_TEXT_MUTE,
            bg=_BG_DARK,
            font=(_FONT, _SZ),
            padx=10,
            pady=6,
        )
        prompt.pack(side=tk.LEFT)

        self._cmd_var = tk.StringVar(value="")
        self._entry = tk.Entry(
            entry_row,
            textvariable=self._cmd_var,
            bg=_BG_DARK,
            fg=_TEXT,
            insertbackground=_TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground="#3A3A3A",
            highlightcolor="#3A3A3A",
            font=("Consolas", _SZ),
            borderwidth=0,
        )
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10), pady=8)
        self._entry.bind("<Return>", self._on_entry_return)

        self._text.bind("<Control-c>", self._copy_from_text)
        self._text.bind("<Control-C>", self._copy_from_text)
        self._text.bind("<Control-v>", self._paste_into_entry)
        self._text.bind("<Control-V>", self._paste_into_entry)

    def focus_terminal(self) -> None:
        self._entry.focus_set()

    def load(self, cwd: str) -> None:
        self.kill()

        if PtyProcess is None:
            self._append_text("pywinpty is not installed in this environment.\n")
            self._status_cb("Terminal unavailable: missing pywinpty")
            return

        norm = normalize(cwd)
        if not os.path.isdir(norm):
            norm = normalize(os.path.expanduser("~"))

        self._cwd_display = to_display(norm)
        self._title_var.set(f"Terminal — {self._cwd_display}")
        self._set_text_state("normal")
        self._text.delete("1.0", tk.END)
        self._set_text_state("disabled")
        self._append_text(f"Starting PowerShell in {self._cwd_display}\n")

        try:
            self._proc = PtyProcess.spawn(["powershell.exe", "-NoLogo"], cwd=norm)
        except Exception as exc:
            self._proc = None
            self._append_text(f"Unable to start PowerShell: {exc}\n")
            self._status_cb(f"Terminal start error: {exc}")
            return

        self._running = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        self._ensure_pump()
        self._status_cb(f"Terminal started in {self._cwd_display}")
        self.focus_terminal()

    def kill(self) -> None:
        self._running = False

        if self._pump_after is not None:
            try:
                self.after_cancel(self._pump_after)
            except Exception:
                pass
            self._pump_after = None

        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass

        self._reader_thread = None

    def shutdown(self) -> None:
        self.kill()

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        while self._running:
            try:
                chunk = proc.read(_READ_SIZE)
            except Exception:
                break
            if not chunk:
                continue
            self._queue.put(self._clean_ansi(chunk))

    def _ensure_pump(self) -> None:
        if self._pump_after is None:
            self._pump_after = self.after(_PUMP_MS, self._drain_output)

    def _drain_output(self) -> None:
        self._pump_after = None
        appended = False
        while True:
            try:
                text = self._queue.get_nowait()
            except queue.Empty:
                break
            self._append_text(text)
            appended = True

        if appended:
            self._text.see(tk.END)

        if self._running and self._proc is not None:
            self._ensure_pump()

    def _append_text(self, text: str) -> None:
        if not text:
            return
        self._set_text_state("normal")
        self._text.insert(tk.END, text)
        self._text.see(tk.END)
        self._set_text_state("disabled")

    def _on_entry_return(self, event=None) -> str:
        command = self._cmd_var.get()
        self._cmd_var.set("")
        if not command:
            return "break"

        self._append_text(f"> {command}\n")
        proc = self._proc
        if proc is None:
            self._append_text("Terminal is not running.\n")
            return "break"
        try:
            proc.write(command + "\r\n")
        except Exception as exc:
            self._append_text(f"Write error: {exc}\n")
            self._status_cb(f"Terminal write error: {exc}")
        return "break"

    def _paste_into_entry(self, event=None) -> str:
        try:
            text = self.root.clipboard_get()
        except Exception:
            text = ""
        if text:
            current = self._cmd_var.get()
            self._cmd_var.set(current + text)
            self._entry.icursor(tk.END)
            self._entry.focus_set()
        return "break"

    def _copy_from_text(self, event=None) -> str:
        try:
            selected = self._text.selection_get()
        except Exception:
            selected = ""
        if selected:
            self.root.clipboard_clear()
            self.root.clipboard_append(selected)
        return "break"

    def _set_text_state(self, state: str) -> None:
        try:
            self._text.configure(state=state)
        except Exception:
            pass

    @staticmethod
    def _clean_ansi(text: str) -> str:
        no_osc = _OSC_RE.sub("", text)
        return _ANSI_RE.sub("", no_osc)