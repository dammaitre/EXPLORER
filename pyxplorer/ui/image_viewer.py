"""
Image viewer tab for the lower panel.

Features:
- Loads images via Pillow in a background thread (non-blocking).
- Downsamples to max 1024 px on the longest side (preserving aspect ratio)
  before displaying, to keep load times fast on large photos.
- Mouse-wheel scrolls vertically; Shift+wheel scrolls horizontally.
- Ctrl+wheel zooms in/out (re-scales the already-loaded thumbnail).
- Single-file only; unsupported formats show a friendly message.
"""

from __future__ import annotations

import ctypes
import io
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Callable

from ..core.longpath import normalize, to_display
from ..settings import THEME as _T, SCROLL_SPEED
from .scroll_utils import make_autohide_pack_setter

if TYPE_CHECKING:
    from PIL import Image as _PilImage
    from PIL import ImageTk as _PilImageTk

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None      # type: ignore[assignment]
    ImageTk = None    # type: ignore[assignment]

_BG       = _T["bg"]
_BG_DARK  = _T["bg_dark"]
_TEXT_MUTE= _T["text_mute"]
_FONT     = _T["font_family"]
_SZ_S     = _T["font_size_small"]

_MAX_PX       = 1024          # longest side cap for the loaded thumbnail
_ZOOM_MIN     = 0.25
_ZOOM_MAX     = 8.0
_ZOOM_STEP    = 1.15
_DEFAULT_ZOOM = 1.0
_SCROLL_SPEED = SCROLL_SPEED

_CF_DIB = 8
_GMEM_MOVEABLE = 0x0002
_GMEM_ZEROINIT = 0x0040
_GHND = _GMEM_MOVEABLE | _GMEM_ZEROINIT

# Extensions this viewer will accept
_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".bmp", ".gif",
    ".tiff", ".tif", ".webp", ".ico",
}


class ImageViewer(ttk.Frame):
    def __init__(
        self,
        parent,
        root: tk.Tk,
        status_cb: Callable[[str], None] | None = None,
    ):
        super().__init__(parent, style="LowerContent.TFrame")
        self.root = root
        self._status_cb = status_cb or (lambda msg: None)

        # --- state ---
        self._path:         str | None                    = None
        self._thumbnail:    _PilImage.Image | None        = None  # type: ignore[name-defined]
        self._photo:        _PilImageTk.PhotoImage | None = None  # type: ignore[name-defined]
        self._zoom:         float       = _DEFAULT_ZOOM
        self._loading:      bool        = False
        self._load_token:   int         = 0
        self._canvas_image: int | None  = None   # canvas item id

        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self._message_var = tk.StringVar(value="")
        self._follow_selection = tk.BooleanVar(value=False)

        top_bar = ttk.Frame(self, style="LowerContent.TFrame")
        top_bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(
            top_bar,
            textvariable=self._message_var,
            anchor="w",
            font=(_FONT, _SZ_S),
            foreground=_TEXT_MUTE,
            padding=(12, 8),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Checkbutton(
            top_bar,
            text="Follow selection",
            variable=self._follow_selection,
            style="TCheckbutton",
        ).pack(side=tk.RIGHT, padx=(0, 12))

        viewport = ttk.Frame(self, style="LowerContent.TFrame")
        viewport.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self._canvas = tk.Canvas(
            viewport,
            background=_BG_DARK,
            highlightthickness=0,
            borderwidth=0,
            xscrollincrement=16,
            yscrollincrement=16,
        )
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._vsb = ttk.Scrollbar(viewport, orient="vertical",
                                   command=self._canvas.yview)
        self._hsb = ttk.Scrollbar(self, orient="horizontal",
                                   command=self._canvas.xview)
        set_vsb = make_autohide_pack_setter(self._vsb, side=tk.RIGHT, fill=tk.Y)
        set_hsb = make_autohide_pack_setter(self._hsb, side=tk.BOTTOM, fill=tk.X)
        self._canvas.configure(xscrollcommand=set_hsb, yscrollcommand=set_vsb)

        self._canvas.bind("<MouseWheel>",         self._on_mousewheel)
        self._canvas.bind("<Shift-MouseWheel>",   self._on_shift_wheel)
        self._canvas.bind("<Control-MouseWheel>", self._on_ctrl_wheel)
        self._canvas.bind("<Button-1>",
                          lambda e: self._canvas.focus_set())
        self._canvas.bind("<Control-c>", self._on_copy_image)
        self._canvas.bind("<Control-C>", self._on_copy_image)
        self._canvas.bind("<Configure>",          self._on_canvas_configure)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def follow_selection(self) -> bool:
        return self._follow_selection.get()

    def focus_viewer(self) -> None:
        self._canvas.focus_set()

    def show_message(self, message: str) -> None:
        self._message_var.set(message)

    def copy_image_to_clipboard(self) -> bool:
        """Copy the currently loaded thumbnail image to Windows clipboard."""
        if self._thumbnail is None:
            self._status_cb("Copy skipped: no image loaded")
            return False
        if sys.platform != "win32":
            self._status_cb("Copy image to clipboard is only available on Windows")
            return False

        try:
            from ctypes import wintypes

            img = self._thumbnail
            if img.mode != "RGB":
                img = img.convert("RGB")

            bmp_io = io.BytesIO()
            img.save(bmp_io, format="BMP")
            bmp_data = bmp_io.getvalue()
            if len(bmp_data) <= 14:
                raise ValueError("Invalid BMP data generated")

            dib_data = bmp_data[14:]   # strip 14-byte file header → CF_DIB format
            data_len = len(dib_data)

            kernel32 = ctypes.windll.kernel32
            user32   = ctypes.windll.user32

            # ctypes.windll defaults restype to c_int (32-bit).  HGLOBAL / LPVOID
            # are pointer-sized (64-bit on 64-bit Windows), so the handle returned
            # by GlobalAlloc would be silently truncated and GlobalLock would fail.
            # Declare the correct types explicitly.
            kernel32.GlobalAlloc.restype   = ctypes.c_void_p
            kernel32.GlobalAlloc.argtypes  = [wintypes.UINT, ctypes.c_size_t]
            kernel32.GlobalLock.restype    = ctypes.c_void_p
            kernel32.GlobalLock.argtypes   = [ctypes.c_void_p]
            kernel32.GlobalUnlock.restype  = wintypes.BOOL
            kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
            kernel32.GlobalFree.restype    = ctypes.c_void_p
            kernel32.GlobalFree.argtypes   = [ctypes.c_void_p]
            user32.SetClipboardData.restype  = ctypes.c_void_p
            user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]

            # Allocate and fill memory before opening the clipboard so we can
            # free it cleanly if anything goes wrong before SetClipboardData.
            h_mem = kernel32.GlobalAlloc(_GHND, data_len)
            if not h_mem:
                raise MemoryError("GlobalAlloc failed")

            ptr = kernel32.GlobalLock(h_mem)
            if not ptr:
                kernel32.GlobalFree(h_mem)
                raise MemoryError("GlobalLock failed")
            try:
                ctypes.memmove(ptr, dib_data, data_len)
            finally:
                kernel32.GlobalUnlock(h_mem)

            if not user32.OpenClipboard(None):
                kernel32.GlobalFree(h_mem)
                raise OSError("OpenClipboard failed")
            try:
                user32.EmptyClipboard()
                if not user32.SetClipboardData(_CF_DIB, h_mem):
                    kernel32.GlobalFree(h_mem)
                    raise OSError("SetClipboardData failed")
                # Clipboard now owns h_mem — must not free it
            finally:
                user32.CloseClipboard()

            self._status_cb("Image copied to clipboard")
            return True
        except Exception as exc:
            self._status_cb(f"Image copy failed: {exc}")
            return False

    @property
    def is_loading(self) -> bool:
        return self._loading

    def cancel_load(self) -> None:
        if self._loading:
            self._load_token += 1
            self._loading = False
            self._thumbnail = None
            self._photo = None
            self._canvas.delete("all")
            self.show_message("Load cancelled.")
            self._status_cb("Image load cancelled")

    def unload(self) -> None:
        """Reset viewer to initial state."""
        self._load_token += 1
        self._loading = False
        self._path = None
        self._thumbnail = None
        self._photo = None
        self._zoom = _DEFAULT_ZOOM
        self._canvas.delete("all")
        self._canvas.configure(scrollregion=(0, 0, 0, 0))
        self.show_message("Select an image file and press Ctrl+Alt+I.")
        self._status_cb("Image viewer ready")

    def load_image(self, path: str) -> None:
        """Load an image file asynchronously."""
        if Image is None or ImageTk is None:
            self.show_message("Image support requires Pillow (pip install Pillow).")
            self._status_cb("Image viewer unavailable: Pillow not installed")
            return

        norm = normalize(path)
        if not os.path.isfile(norm):
            self.show_message("The selected file no longer exists.")
            self._status_cb("Image load skipped: file not found")
            return

        ext = os.path.splitext(norm)[1].lower()
        if ext not in _IMAGE_EXTS:
            self.show_message(
                f"Unsupported format '{ext}'. "
                f"Supported: {', '.join(sorted(_IMAGE_EXTS))}"
            )
            self._status_cb(f"Image load skipped: unsupported format '{ext}'")
            return

        # Increment token so any previous in-flight thread becomes stale
        self._load_token += 1
        token = self._load_token
        self._loading = True
        self._path = norm
        self._zoom = _DEFAULT_ZOOM
        self._canvas.delete("all")
        self.show_message(f"Loading {os.path.basename(to_display(norm))}…")
        self._status_cb(f"Loading image: {os.path.basename(to_display(norm))}")

        threading.Thread(
            target=self._load_worker,
            args=(norm, token),
            daemon=True,
        ).start()

    # ------------------------------------------------------------------
    # Background loader
    # ------------------------------------------------------------------

    def _load_worker(self, path: str, token: int) -> None:
        try:
            img = Image.open(path)  # type: ignore[union-attr]
            img.load()   # force decode before thumb
            # Downsample to _MAX_PX on the longest side, keep aspect ratio
            w, h = img.size
            scale = min(_MAX_PX / max(w, h, 1), 1.0)
            if scale < 1.0:
                new_w = max(1, int(w * scale))
                new_h = max(1, int(h * scale))
                resample = getattr(Image, "LANCZOS", getattr(Image, "ANTIALIAS", 1))  # type: ignore[union-attr]
                img = img.resize((new_w, new_h), resample)
            # Convert to RGBA for consistent compositing
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")
            self.root.after(0, self._on_loaded, token, img, None)
        except Exception as exc:
            self.root.after(0, self._on_loaded, token, None, str(exc))

    # ------------------------------------------------------------------
    # Load completion callback (main thread)
    # ------------------------------------------------------------------

    def _on_loaded(self, token: int, img, error: str | None) -> None:
        if token != self._load_token:
            return   # stale result from cancelled load
        self._loading = False
        if error:
            self.show_message(f"Failed to load image: {error}")
            self._status_cb(f"Image load error: {error}")
            return
        self._thumbnail = img
        fname = os.path.basename(to_display(self._path or ""))
        w, h = img.size
        self.show_message(f"{fname}  —  {w}×{h} px  (scroll to pan, Ctrl+scroll to zoom)")
        self._status_cb(f"Image loaded: {fname} ({w}×{h} px)")
        self._render()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render(self) -> None:
        """Scale thumbnail by current zoom and draw on canvas."""
        if self._thumbnail is None:
            return

        tw, th = self._thumbnail.size  # type: ignore[union-attr]
        dw = max(1, int(tw * self._zoom))
        dh = max(1, int(th * self._zoom))

        resample = getattr(Image, "LANCZOS", getattr(Image, "ANTIALIAS", 1))  # type: ignore[union-attr]
        display_img = self._thumbnail.resize((dw, dh), resample)  # type: ignore[union-attr]
        self._photo = ImageTk.PhotoImage(display_img)  # type: ignore[union-attr]

        self._canvas.delete("all")
        # Centre the image in the canvas if it's smaller
        cw = self._canvas.winfo_width()  or dw
        ch = self._canvas.winfo_height() or dh
        x = max(dw // 2, cw // 2)
        y = max(dh // 2, ch // 2)
        self._canvas_image = self._canvas.create_image(
            x, y, anchor="center", image=self._photo
        )
        scroll_w = max(dw, cw)
        scroll_h = max(dh, ch)
        self._canvas.configure(scrollregion=(0, 0, scroll_w, scroll_h))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        """Re-centre the image when the canvas is resized."""
        if self._thumbnail is not None:
            self._render()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_mousewheel(self, event: tk.Event) -> str:
        delta = -1 if event.delta < 0 else 1
        self._canvas.yview_scroll(int(-delta * _SCROLL_SPEED), "units")
        return "break"

    def _on_shift_wheel(self, event: tk.Event) -> str:
        delta = -1 if event.delta < 0 else 1
        self._canvas.xview_scroll(int(-delta * _SCROLL_SPEED), "units")
        return "break"

    def _on_ctrl_wheel(self, event: tk.Event) -> str:
        if self._thumbnail is None:
            return "break"
        if event.delta > 0:
            self._zoom = min(_ZOOM_MAX, self._zoom * _ZOOM_STEP)
        else:
            self._zoom = max(_ZOOM_MIN, self._zoom / _ZOOM_STEP)
        self._render()
        pct = int(self._zoom * 100)
        self.show_message(
            f"{os.path.basename(to_display(self._path or ''))}  —  "
            f"{pct}% zoom  (Ctrl+scroll to zoom)"
        )
        return "break"

    def _on_copy_image(self, event: tk.Event | None = None) -> str:
        self.copy_image_to_clipboard()
        return "break"
