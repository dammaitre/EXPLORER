"""
App — root window, Win11 ttk styling, 3-panel layout, navigation controller.
"""
import os
import sys
import queue
import threading
import subprocess
import importlib.util
import tkinter as tk
from tkinter import ttk

from .state import AppState
from .core.longpath import normalize, to_display, enable_longpath_registry
from .core.scanner import SizeScanner, CancelToken, _is_scan_skipped
from .ui import icons as _icons_mod
from .ui.top_bar import TopBar
from .ui.left_panel import LeftPanel
from .ui.main_frame import MainFrame
from .ui.lower_panel import LowerPanel
from .ui.heuristics_window import HeuristicsWindow
from .ui.status_bar import StatusBar
from .keybindings import bind_keys
from .settings import THEME as _T
from .core.heuristics import run_heuristic

# ── Palette & font vars (sourced from settings.json) ──────────────────────────
BG        = _T["bg"]
BG_DARK   = _T["bg_dark"]
BG_ENTRY  = _T["bg_entry"]
ACCENT    = _T["accent"]
TEXT      = _T["text"]
TEXT_MUTE = _T["text_mute"]
BORDER    = _T["border"]
ROW_H     = _T["row_hover"]
ROW_SEL   = _T["row_selected"]
STATUS_BG = _T["status_bg"]

_FONT  = _T["font_family"]
_SZ    = _T["font_size_base"]    # default: 13
_SZ_S  = _T["font_size_small"]   # default: 12
_RH    = _T["row_height"]        # default: 36
_LOWER_MIN_H = 150
_LOWER_DEFAULT_H = 260


def _create_root() -> tuple[tk.Tk, bool]:
    try:
        module = __import__("tkinterdnd2", fromlist=["TkinterDnD"])
        tkdnd = getattr(module, "TkinterDnD", None)
        if tkdnd is None:
            return tk.Tk(), False
        return tkdnd.Tk(), True
    except Exception:
        return tk.Tk(), False


def _copy_engine_name() -> str:
    return "robocopy" if sys.platform == "win32" else "shutil"


def _terminal_backend_name() -> str:
    if importlib.util.find_spec("winpty") is not None:
        return "winpty"
    if importlib.util.find_spec("ptyprocess") is not None:
        return "ptyprocess"
    return "none"


def _terminal_shell_name() -> str:
    if sys.platform == "win32":
        return "powershell"
    shell = os.environ.get("SHELL")
    if shell:
        return os.path.basename(shell)
    return "bash"


def _runtime_capabilities_message(dnd_enabled: bool) -> str:
    return (
        "Runtime: "
        f"copy={_copy_engine_name()} · "
        f"pty={_terminal_backend_name()} · "
        f"shell={_terminal_shell_name()} · "
        f"dnd={'on' if dnd_enabled else 'off'}"
    )


def _apply_win11_style(root: tk.Tk) -> None:
    style = ttk.Style(root)

    # clam is consistent across platforms and accepts full overrides
    style.theme_use("clam")

    # ── Base ──────────────────────────────────────────────────────────
    style.configure(".",
        background=BG, foreground=TEXT,
        font=(_FONT, _SZ),
        bordercolor=BORDER,
        troughcolor=BORDER,
    )

    # ── Frames ────────────────────────────────────────────────────────
    style.configure("TopBar.TFrame", background=BG)
    style.configure("LeftPanel.TFrame", background=BG_DARK)
    style.configure("TFrame", background=BG)
    style.configure("StatusBar.TFrame", background=STATUS_BG, relief="flat")
    style.configure("LowerPanel.TFrame", background=BG_DARK)
    style.configure("LowerTabs.TFrame", background=BG_DARK)
    style.configure("LowerContent.TFrame", background=BG)

    # ── Labels ────────────────────────────────────────────────────────
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("LeftPanel.TLabel", background=BG_DARK, foreground=TEXT_MUTE)
    style.configure("StatusBar.TLabel",
        background=STATUS_BG, foreground=TEXT_MUTE,
        font=(_FONT, _SZ_S),
    )

    # ── Entries ───────────────────────────────────────────────────────
    style.configure("TEntry",
        fieldbackground=BG_ENTRY, foreground=TEXT,
        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
        selectbackground=ROW_SEL, selectforeground=TEXT,
        insertcolor=TEXT,
    )
    style.map("TEntry",
        bordercolor=[("focus", ACCENT)],
        lightcolor=[("focus", ACCENT)],
    )
    style.configure("Path.TEntry",
        fieldbackground=BG_ENTRY, foreground=TEXT,
        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
        insertcolor=TEXT,
    )
    style.map("Path.TEntry",
        bordercolor=[("focus", ACCENT)],
        lightcolor=[("focus", ACCENT)],
    )
    style.configure("Error.TEntry",
        fieldbackground="#FFE0E0", foreground="#C62828",
        bordercolor="#D32F2F", lightcolor="#D32F2F",
    )

    # ── Buttons ───────────────────────────────────────────────────────
    style.configure("TButton",
        background=BG, foreground=TEXT,
        bordercolor=BORDER, focuscolor=BG,
        padding=(6, 4),
        relief="flat",
    )
    style.map("TButton",
        background=[("active", ROW_H), ("pressed", ROW_SEL)],
        bordercolor=[("active", BORDER)],
    )
    style.configure("Flat.TButton",
        background=BG, foreground=TEXT_MUTE,
        bordercolor=BORDER, focuscolor=BG,
        padding=(4, 4),
        relief="flat",
        font=(_FONT, _SZ_S),
    )
    style.map("Flat.TButton",
        background=[("active", ROW_H)],
    )
    style.configure("LowerTab.TButton",
        background=BG_DARK, foreground=TEXT_MUTE,
        bordercolor=BG_DARK, focuscolor=BG_DARK,
        padding=(10, 6),
        relief="flat",
        font=(_FONT, _SZ_S),
    )
    style.map("LowerTab.TButton",
        background=[("active", ROW_H), ("pressed", ROW_SEL)],
        foreground=[("active", TEXT)],
    )
    style.configure("LowerTabActive.TButton",
        background=ROW_SEL, foreground=TEXT,
        bordercolor=ROW_SEL, focuscolor=ROW_SEL,
        padding=(10, 6),
        relief="flat",
        font=(_FONT, _SZ_S),
    )
    style.configure("Breadcrumb.TButton",
        background=BG, foreground=TEXT,
        bordercolor=BG, focuscolor=BG,
        padding=(4, 2),
        relief="flat",
        font=(_FONT, _SZ),
    )
    style.map("Breadcrumb.TButton",
        background=[("active", ROW_H)],
        foreground=[("active", ACCENT)],
    )

    # ── Treeview ──────────────────────────────────────────────────────
    style.configure("Treeview",
        background=BG, foreground=TEXT,
        fieldbackground=BG,
        borderwidth=0,
        rowheight=_RH,
        font=(_FONT, _SZ),
    )
    style.map("Treeview",
        background=[("selected", ROW_SEL)],
        foreground=[("selected", TEXT)],
    )
    style.configure("Treeview.Heading",
        background=BG_DARK, foreground=TEXT_MUTE,
        font=(_FONT, _SZ_S),
        relief="flat",
        borderwidth=0,
    )
    style.map("Treeview.Heading",
        background=[("active", ROW_H)],
    )

    # ── Scrollbar ─────────────────────────────────────────────────────
    style.configure("Vertical.TScrollbar",
        background=BG, troughcolor=BG,
        bordercolor=BG, arrowcolor=TEXT_MUTE,
        width=10,
    )
    style.map("Vertical.TScrollbar",
        background=[("active", BORDER)],
    )

    # ── Separator ─────────────────────────────────────────────────────
    style.configure("TSeparator", background=BORDER)


class App:
    def __init__(self, start_path: str | None = None):
        self.root, self._dnd_enabled = _create_root()
        # Resolve start_path early; store None if invalid so layout can ignore it
        if start_path:
            _norm = normalize(start_path)
            self._start_path: str | None = _norm if os.path.isdir(_norm) else None
        else:
            self._start_path = None
        self.root.title("Pyxplorer")
        self.root.geometry("1200x700")
        self.root.minsize(800, 500)

        # Windows DPI awareness so the window isn't blurry on HiDPI displays
        if sys.platform == "win32":
            try:
                from ctypes import windll
                windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass

        _apply_win11_style(self.root)
        self.root.configure(bg=BG)

        # Long-path registry fix (silent — no admin prompt at this stage)
        enable_longpath_registry()

        self.state = AppState(start_path=self._start_path)

        # Icons (Pillow-generated; values are None when Pillow is absent)
        self._icons = _icons_mod.load(self.root)

        # Async scanner
        self._scan_queue: queue.Queue = queue.Queue()
        self._scanner:    SizeScanner = SizeScanner(self._scan_queue)
        self._scan_token: CancelToken | None = None
        self._lower_visible: bool = False
        self._lower_height: int = _LOWER_DEFAULT_H
        self._heuristics_win: HeuristicsWindow | None = None
        self._heuristic_run_token: int = 0

        self._build_layout()
        bind_keys(
            self.root,
            self.state,
            self.top_bar,
            self.main_frame,
            open_pdf_cb=self.open_pdf_panel,
            open_terminal_cb=self.open_terminal_panel,
            open_notes_cb=self.open_notes_panel,
            toggle_heuristics_cb=self.toggle_heuristics_window,
            hide_lower_cb=self.hide_lower_panel,
            close_cb=self.close,
            status_cb=self.status_bar.set_status,
            refresh_starred_cb=self.left_panel.refresh_starred,
            transfer_start_cb=self.status_bar.start_transfer,
            transfer_progress_cb=self.status_bar.update_transfer_progress,
            transfer_stop_cb=self.status_bar.stop_transfer,
        )
        self.status_bar.set_status(_runtime_capabilities_message(self._dnd_enabled))
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _set_title(self, path: str) -> None:
        self.root.title(f"Pyxplorer - {to_display(path)}")

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        # Top bar (~56 px tall — enforced by its own padding/content)
        self.top_bar = TopBar(self.root, self.state, navigate_cb=self._navigate)
        self.top_bar.pack(side=tk.TOP, fill=tk.X)

        # Thin separator line between top bar and body
        ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X)

        # Status bar (~28 px tall)
        self.status_bar = StatusBar(self.root, self.state)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # Middle area: vertical shell → top work area + lower panel
        self.body_paned = tk.PanedWindow(
            self.root,
            orient=tk.VERTICAL,
            sashwidth=4,
            sashrelief="flat",
            background=ROW_H,
            bd=0,
        )
        self.body_paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.workspace = ttk.Frame(self.body_paned, style="TFrame")
        self.body_paned.add(self.workspace, minsize=320)

        # Main work area: left panel + main frame
        self.paned = tk.PanedWindow(
            self.workspace,
            orient=tk.HORIZONTAL,
            sashwidth=4,
            sashrelief="flat",
            background=ROW_H,
            bd=0,
        )
        self.paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        extra = [self._start_path] if self._start_path else []
        self.left_panel = LeftPanel(
            self.paned, self.state, navigate_cb=self._navigate,
            icons=self._icons, extra_start_dirs=extra,
        )
        self.paned.add(self.left_panel, width=220, minsize=100)

        self.main_frame = MainFrame(
            self.paned, self.state,
            navigate_cb=self._navigate,
            on_select_cb=self._on_selection_change,
            status_cb=self.status_bar.set_status,
            transfer_start_cb=self.status_bar.start_transfer,
            transfer_progress_cb=self.status_bar.update_transfer_progress,
            transfer_stop_cb=self.status_bar.stop_transfer,
            icons=self._icons,
        )
        self.paned.add(self.main_frame, minsize=400)

        # Single-click on left panel returns keyboard focus to main frame
        self.left_panel.focus_back_cb = self.main_frame._tree.focus_set

        # Initialise starred list from persisted store
        self.left_panel.refresh_starred()

        self.lower_panel = LowerPanel(
            self.body_paned,
            self.root,
            self.state,
            hide_cb=self.hide_lower_panel,
            status_cb=self.status_bar.set_status,
        )

    def _ensure_lower_panel_visible(self) -> None:
        if self._lower_visible:
            self.lower_panel.focus_active_tab()
            return
        self.body_paned.add(
            self.lower_panel,
            minsize=_LOWER_MIN_H,
            height=max(_LOWER_MIN_H, self._lower_height),
        )
        self._lower_visible = True
        self.root.after_idle(self._set_lower_sash)
        self.root.after_idle(self.lower_panel.focus_active_tab)

    def _set_lower_sash(self) -> None:
        if not self._lower_visible:
            return
        try:
            total = max(self.body_paned.winfo_height(), 520)
            lower_h = max(_LOWER_MIN_H, min(self._lower_height, total - 100))
            self.body_paned.sash_place(0, 1, total - lower_h)
        except Exception:
            pass

    def _remember_lower_height(self) -> None:
        if not self._lower_visible:
            return
        try:
            total = self.body_paned.winfo_height()
            _, sash_y = self.body_paned.sash_coord(0)
            remembered = total - sash_y
            if remembered >= _LOWER_MIN_H:
                self._lower_height = remembered
        except Exception:
            pass

    def hide_lower_panel(self) -> None:
        if not self._lower_visible:
            return
        self._remember_lower_height()
        try:
            self.body_paned.forget(self.lower_panel)
        except Exception:
            return
        self._lower_visible = False
        self.status_bar.set_status("Lower panel hidden")
        self.main_frame._tree.focus_set()

    def open_pdf_panel(self) -> None:
        self._ensure_lower_panel_visible()
        self.lower_panel.request_pdf()

    def open_terminal_panel(self) -> None:
        self._ensure_lower_panel_visible()
        self.lower_panel.request_terminal()

    def open_notes_panel(self) -> None:
        self._ensure_lower_panel_visible()
        self.lower_panel.request_notes()

    def close(self) -> None:
        if self._heuristics_win and self._heuristics_win.alive:
            self._heuristics_win.close()
        if self._scan_token:
            self._scan_token.cancel()
            self._scan_token = None
        self.lower_panel.shutdown()
        self.root.destroy()

    def toggle_heuristics_window(self) -> None:
        if self._heuristics_win and self._heuristics_win.alive:
            self._heuristics_win.close()
            return

        self._heuristics_win = HeuristicsWindow(
            self.root,
            on_run_cb=self._run_heuristic_script,
            on_close_cb=self._on_heuristics_close,
        )
        self.status_bar.set_status("Heuristics window opened")

    def _on_heuristics_close(self) -> None:
        self._heuristic_run_token += 1
        self._heuristics_win = None
        self.main_frame.clear_heuristic_column()
        self.status_bar.set_status("Heuristics window closed")

    def _run_heuristic_script(self, script_path: str, script_name: str) -> None:
        rows = self.main_frame.get_current_rows()
        if not rows:
            self.status_bar.set_status("No items to evaluate")
            return

        self._heuristic_run_token += 1
        token = self._heuristic_run_token
        total = len(rows)
        self.main_frame.begin_heuristic_results(script_name)

        self.status_bar.set_status(f"Running heuristic '{script_name}' on {total} item(s)…")

        def _worker() -> None:
            done = 0
            for row in rows:
                path = row.get("path")
                if not path:
                    continue
                try:
                    value = run_heuristic(script_path, to_display(path))
                except subprocess.TimeoutExpired:
                    value = "ERR: timeout"
                except Exception as exc:
                    value = f"ERR: {str(exc)[:80]}"

                done += 1

                def _update_one(result_path=path, result_value=value, n=done) -> None:
                    if token != self._heuristic_run_token:
                        return
                    self.main_frame.update_heuristic_value(result_path, result_value)
                    self.status_bar.set_status(
                        f"Running heuristic '{script_name}'… {n}/{total}"
                    )

                self.root.after(0, _update_one)

            def _finish() -> None:
                if token != self._heuristic_run_token:
                    return
                self.status_bar.set_status(
                    f"Heuristic '{script_name}' complete — {done} item(s)"
                )

            self.root.after(0, _finish)

        threading.Thread(target=_worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Navigation controller (single point of truth)
    # ------------------------------------------------------------------

    def _navigate(self, path: str) -> None:
        norm = normalize(path)
        if not os.path.isdir(norm):
            return

        # Cancel any running scan before changing directory
        if self._scan_token:
            self._scan_token.cancel()
            self._scan_token = None

        self.state.navigate_to(norm)
        self._set_title(norm)
        self.top_bar.update_path(norm)
        self.main_frame.load_dir(norm)
        self.left_panel.load_dir(norm)

        # Check if current directory is in scan_skip_dirs
        if _is_scan_skipped(norm):
            self.status_bar.set_skip_message()

        dirs = self.main_frame.get_subdir_paths()
        if dirs:
            self._scan_token = CancelToken()
            self.status_bar.start_scanning()
            self._scanner.scan_items(norm, dirs, self._scan_token)
        else:
            # No subdirs — file sizes already known; compute % immediately
            n     = self.main_frame.get_item_count()
            total = self.main_frame.get_total_size()
            self.main_frame.finalize_pct()
            self.status_bar.stop_scanning(n, total)

    def _on_selection_change(self, n_selected: int, sel_size: int) -> None:
        self.status_bar.update_selection(n_selected, sel_size)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def _process_queue(self) -> None:
        """Drain the scanner result queue. Rescheduled every 100 ms."""
        try:
            while True:
                msg = self._scan_queue.get_nowait()
                kind = msg[0]

                if kind == "size_result":
                    _, item_path, size = msg
                    self.main_frame.update_item_size(item_path, size)

                elif kind == "scan_complete":
                    _, parent_path = msg
                    # Guard: ignore stale results from a previous directory
                    if os.path.normcase(normalize(parent_path)) == \
                       os.path.normcase(self.state.current_dir):
                        n     = self.main_frame.get_item_count()
                        total = self.main_frame.get_total_size()
                        self.main_frame.finalize_pct()
                        self.status_bar.stop_scanning(n, total)
                        self._scan_token = None

        except queue.Empty:
            pass
        self.root.after(100, self._process_queue)

    def run(self) -> None:
        self._navigate(self.state.current_dir)
        self.root.after(100, self._process_queue)
        self.root.mainloop()
