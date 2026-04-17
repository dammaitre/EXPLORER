import os
import tkinter as tk
from tkinter import ttk
from typing import Any

from ..core.longpath import normalize, to_display
from ..settings import THEME as _T
from .embedded_terminal import EmbeddedTerminal
from .image_viewer import ImageViewer, _IMAGE_EXTS
from .pdf_viewer import PDFViewer
from .temp_notepad import TempNotepad

_FONT = _T["font_family"]
_SZ_S = _T["font_size_small"]
_TEXT_MUTE = _T["text_mute"]


class LowerPanel(ttk.Frame):
    def __init__(self, parent, root: tk.Tk, state, hide_cb, status_cb=None):
        super().__init__(parent, style="LowerPanel.TFrame")
        self.root = root
        self.state: Any = state
        self._hide_cb = hide_cb
        self._status_cb = status_cb or (lambda message: None)
        self.active_tab: str | None = None
        self._tab_buttons: dict[str, ttk.Button] = {}
        self._tab_frames: dict[str, ttk.Frame] = {}
        self._tab_titles = {
            "pdf": "PDF viewer",
            "terminal": "Terminal",
            "notes": "Temp notes",
            "image": "Image viewer",
        }

        self._build()

    def _build(self) -> None:
        tabs = ttk.Frame(self, style="LowerTabs.TFrame")
        tabs.pack(side=tk.TOP, fill=tk.X)

        for key, label in (("pdf", "P"), ("terminal", "T"), ("notes", "N"), ("image", "I")):
            button = ttk.Button(
                tabs,
                text=label,
                style="LowerTab.TButton",
                command=lambda name=key: self.show_tab(name),
                width=4,
            )
            button.pack(side=tk.LEFT, padx=(8 if key == "pdf" else 0, 6), pady=8)
            self._tab_buttons[key] = button

        ttk.Button(
            tabs,
            text="✕",
            style="Flat.TButton",
            command=self._hide_cb,
            width=3,
        ).pack(side=tk.RIGHT, padx=8, pady=8)

        self._title_var = tk.StringVar(value="Lower panel")
        ttk.Label(
            tabs,
            textvariable=self._title_var,
            anchor="w",
            font=(_FONT, _SZ_S),
            foreground=_TEXT_MUTE,
        ).pack(side=tk.LEFT, padx=(8, 0))

        content = ttk.Frame(self, style="LowerContent.TFrame")
        content.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self._pdf_viewer   = PDFViewer(content, self.root, status_cb=self._status_cb)
        self._terminal     = EmbeddedTerminal(content, self.root, status_cb=self._status_cb)
        self._notes        = TempNotepad(content, self.root, status_cb=self._status_cb)
        self._image_viewer = ImageViewer(content, self.root, status_cb=self._status_cb)

        self._tab_frames = {
            "pdf":     self._pdf_viewer,
            "terminal": self._terminal,
            "notes":   self._notes,
            "image":   self._image_viewer,
        }

        self.show_tab("pdf")
        self._pdf_viewer.show_message("Select a single PDF file and press Ctrl+Alt+P.")
        self._status_cb("PDF viewer ready")

    def show_tab(self, name: str) -> None:
        frame = self._tab_frames.get(name)
        if frame is None:
            return

        for child in self._tab_frames.values():
            child.pack_forget()

        frame.pack(fill=tk.BOTH, expand=True)
        self.active_tab = name

        for key, button in self._tab_buttons.items():
            button.configure(style="LowerTabActive.TButton" if key == name else "LowerTab.TButton")

        self._title_var.set(self._tab_titles.get(name, "Lower panel"))
        self._status_cb(f"{self._title_var.get()} active")

        self.focus_active_tab()

    def focus_active_tab(self) -> None:
        if self.active_tab == "pdf":
            self._pdf_viewer.focus_viewer()
        elif self.active_tab == "terminal":
            self._terminal.focus_terminal()
        elif self.active_tab == "notes":
            self._notes.focus_editor()
        elif self.active_tab == "image":
            self._image_viewer.focus_viewer()

    def contains_focus(self) -> bool:
        """True when keyboard focus is currently inside the lower panel."""
        try:
            focused = self.root.focus_get()
        except Exception:
            focused = None
        if focused is None:
            return False
        try:
            return str(focused).startswith(str(self))
        except Exception:
            return False

    @property
    def follow_pdf_selection(self) -> bool:
        return self._pdf_viewer.follow_selection

    def on_file_selection_changed(self, paths: list[str]) -> None:
        """Called whenever the main-frame selection changes.

        Auto-loads the selected file into the active viewer when 'Follow
        selection' is enabled for that viewer.
        """
        if len(paths) != 1:
            return
        path = paths[0]

        from ..core.archive import is_archive_virtual_path, split_archive_path
        if is_archive_virtual_path(path):
            _, inner = split_archive_path(path)
            if not inner:
                return
            ext = os.path.splitext(inner)[1].lower()
            if self.active_tab == "pdf" and self._pdf_viewer.follow_selection:
                if ext == ".pdf":
                    self._load_archive_for_viewer("pdf", path, inner)
            elif self.active_tab == "image" and self._image_viewer.follow_selection:
                if ext in _IMAGE_EXTS:
                    self._load_archive_for_viewer("image", path, inner)
            return

        norm = normalize(path)
        if os.path.isdir(norm):
            return

        if self.active_tab == "pdf" and self._pdf_viewer.follow_selection:
            if not norm.lower().endswith(".pdf"):
                return
            if self._pdf_viewer._doc_path and os.path.normcase(self._pdf_viewer._doc_path) == os.path.normcase(norm):
                return
            self._title_var.set(f"PDF viewer — {os.path.basename(to_display(norm))}")
            self._pdf_viewer.load_pdf(norm)

        elif self.active_tab == "image" and self._image_viewer.follow_selection:
            if os.path.splitext(norm)[1].lower() not in _IMAGE_EXTS:
                return
            if self._image_viewer._path and os.path.normcase(self._image_viewer._path) == os.path.normcase(norm):
                return
            self._title_var.set(f"Image viewer — {os.path.basename(to_display(norm))}")
            self._image_viewer.load_image(norm)

    def _load_archive_for_viewer(self, viewer: str, virtual_path: str,
                                  inner_path: str) -> None:
        """Extract a file from an archive in a background thread, then load it."""
        import threading
        from ..core.archive import split_archive_path, extract_to_temp

        archive_path, _ = split_archive_path(virtual_path)
        name = inner_path.split("/")[-1]

        if viewer == "pdf":
            self._pdf_viewer.show_message(f"Extracting {name}…")
        else:
            self._image_viewer.show_message(f"Extracting {name}…")
        self._status_cb(f"Extracting {name}…")

        def _worker():
            out = extract_to_temp(archive_path, inner_path)

            def _done():
                if out:
                    self._title_var.set(f"{'PDF' if viewer == 'pdf' else 'Image'} viewer — {name}")
                    if viewer == "pdf":
                        self._pdf_viewer.load_pdf(out)
                    else:
                        self._image_viewer.load_image(out)
                else:
                    msg = f"Failed to extract {name} from archive"
                    if viewer == "pdf":
                        self._pdf_viewer.show_message(msg)
                    else:
                        self._image_viewer.show_message(msg)
                    self._status_cb(msg)

            self.root.after(0, _done)

        threading.Thread(target=_worker, daemon=True).start()

    def request_pdf(self) -> None:
        self.show_tab("pdf")
        paths = list(self.state.selection)
        if len(paths) != 1:
            self._pdf_viewer.show_message("Select a single PDF file and press Ctrl+Alt+P.")
            self._status_cb("PDF load skipped: select a single PDF file")
            return

        from ..core.archive import is_archive_virtual_path, split_archive_path
        raw_path = paths[0]
        if is_archive_virtual_path(raw_path):
            _, inner = split_archive_path(raw_path)
            if not inner or not inner.lower().endswith(".pdf"):
                self._pdf_viewer.show_message("The selected item is not a PDF file.")
                self._status_cb("PDF load skipped: selected item is not a PDF")
                return
            self._load_archive_for_viewer("pdf", raw_path, inner)
            return

        path = normalize(raw_path)
        if os.path.isdir(path) or not path.lower().endswith(".pdf"):
            self._pdf_viewer.show_message("The selected item is not a PDF file.")
            self._status_cb("PDF load skipped: selected item is not a PDF")
            return

        self._title_var.set(f"PDF viewer — {os.path.basename(to_display(path))}")
        self._pdf_viewer.load_pdf(path)

    def request_terminal(self) -> None:
        self.show_tab("terminal")
        target = normalize(self.state.current_dir)
        self._title_var.set(f"Terminal — {to_display(target)}")
        self._terminal.load(target)

    def request_notes(self) -> None:
        self.show_tab("notes")
        self._title_var.set(f"Temp notes — {self._notes.temp_path_display}")
        self._notes.load()

    def cancel_pdf_if_loading(self) -> bool:
        """Cancel an in-progress PDF load. Returns True if a load was cancelled."""
        if self.active_tab == "pdf" and self._pdf_viewer.is_loading:
            self._pdf_viewer.cancel_load()
            return True
        return False

    def request_image(self) -> None:
        self.show_tab("image")
        paths = list(self.state.selection)
        if len(paths) != 1:
            self._image_viewer.show_message(
                "Select a single image file and press Ctrl+Alt+I."
            )
            self._status_cb("Image load skipped: select a single image file")
            return

        from ..core.archive import is_archive_virtual_path, split_archive_path
        raw_path = paths[0]
        if is_archive_virtual_path(raw_path):
            _, inner = split_archive_path(raw_path)
            if not inner or os.path.splitext(inner)[1].lower() not in _IMAGE_EXTS:
                self._image_viewer.show_message("The selected item is not a supported image.")
                self._status_cb("Image load skipped: selected item is not a supported image")
                return
            self._load_archive_for_viewer("image", raw_path, inner)
            return

        path = normalize(raw_path)
        if os.path.isdir(path):
            self._image_viewer.show_message("Select an image file, not a directory.")
            self._status_cb("Image load skipped: selected item is a directory")
            return
        self._title_var.set(f"Image viewer — {os.path.basename(to_display(path))}")
        self._image_viewer.load_image(path)

    def cancel_image_if_loading(self) -> bool:
        """Cancel an in-progress image load. Returns True if a load was cancelled."""
        if self.active_tab == "image" and self._image_viewer.is_loading:
            self._image_viewer.cancel_load()
            return True
        return False

    def copy_pdf_selection_image(self) -> None:
        """Copy the PDF selection as an image to clipboard (global Ctrl+I handler)."""
        if self.active_tab == "pdf":
            self._pdf_viewer.copy_selection_image()

    def copy_pdf_selection_ocr_text(self) -> None:
        """Run OCR on PDF selection and copy recognized text (global Ctrl+O handler)."""
        if self.active_tab == "pdf":
            self._pdf_viewer.copy_selection_ocr_text()


    def shutdown(self) -> None:
        self._pdf_viewer.unload()
        self._image_viewer.unload()
        self._terminal.shutdown()
        self._notes.shutdown()
