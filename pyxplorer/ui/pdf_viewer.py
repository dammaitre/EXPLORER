import importlib
import os
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable, Any

from ..core.ocr import extract_text_from_image, ocr_backend_status
from ..core.longpath import normalize, to_display
from ..logging import vprint
from ..settings import THEME as _T, SCROLL_SPEED, DEFAULT_PDF_ZOOM
from .scroll_utils import make_autohide_pack_setter

try:
    fitz = importlib.import_module("fitz")
except ImportError:
    fitz = None

if fitz is not None:
    try:
        fitz.TOOLS.mupdf_display_errors(False)
    except Exception:
        pass
    try:
        fitz.TOOLS.mupdf_display_warnings(False)
    except Exception:
        pass

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

_BG = _T["bg"]
_BG_DARK = _T["bg_dark"]
_TEXT = _T["text"]
_TEXT_MUTE = _T["text_mute"]
_ACCENT = _T["accent"]
_BORDER = _T["border"]
_FONT = _T["font_family"]
_SZ_S = _T["font_size_small"]

_MARGIN_X = 18
_MARGIN_Y = 16
_PAGE_GAP = 18
_QUEUE_TICK_MS = 35
_ZOOM_MIN = 0.5
_ZOOM_MAX = 3.0
_ZOOM_STEP = 1.1
_ZOOM_DEBOUNCE_MS = 200      # ms of scroll inactivity before re-render fires
_DEFAULT_ZOOM = min(_ZOOM_MAX, max(_ZOOM_MIN, DEFAULT_PDF_ZOOM))
_SCROLL_SPEED = SCROLL_SPEED
_VISIBLE_PAGE_BUFFER = 2


class PDFViewer(ttk.Frame):
    def __init__(
        self,
        parent,
        root: tk.Tk,
        status_cb: Callable[[str], None] | None = None,
    ):
        super().__init__(parent, style="LowerContent.TFrame")
        self.root = root
        self._status_cb = status_cb or (lambda message: None)
        self._doc = None
        self._doc_bytes: bytes | None = None
        self._doc_path: str | None = None
        self._page_count = 0
        self._loaded_count = 0
        self._failed_count = 0
        self._load_token = 0
        self._render_queue: queue.Queue = queue.Queue()
        self._pump_after: str | None = None
        self._worker: threading.Thread | None = None
        self._visible_after: str | None = None
        self._page_views: list[dict] = []
        self._page_view_by_index: dict[int, dict] = {}
        self._page_layouts: list[dict] = []
        self._pending_pages: set[int] = set()
        self._photos: list = []
        self._page_payloads: dict[int, dict] = {}
        self._selection_items: list[int] = []
        self._selection_text = ""
        self._drag_anchor: tuple[float, float] | None = None
        self._drag_current: tuple[float, float] | None = None
        self._zoom = _DEFAULT_ZOOM
        self._pending_zoom_anchor: dict | None = None
        self._zoom_after: str | None = None      # debounce handle
        self._canvas_needs_clear: bool = False   # wipe canvas on first new page arrival
        self._stale_photos: list = []            # old photos kept alive until canvas wipe
        self._page_dim_cache: dict[int, tuple[float, float]] = {}  # index → (w_pt, h_pt)

        self._build()
        self.show_message("Select a single PDF file and press Ctrl+Alt+P.")

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
            xscrollincrement=24,
            yscrollincrement=24,
        )
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._vsb = ttk.Scrollbar(viewport, orient="vertical", command=self._canvas.yview)
        self._hsb = ttk.Scrollbar(self, orient="horizontal", command=self._canvas.xview)
        set_vsb = make_autohide_pack_setter(self._vsb, side=tk.RIGHT, fill=tk.Y)
        set_hsb = make_autohide_pack_setter(self._hsb, side=tk.BOTTOM, fill=tk.X)
        self._canvas.configure(xscrollcommand=set_hsb, yscrollcommand=set_vsb)

        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind("<Shift-MouseWheel>", self._on_shift_mousewheel)
        self._canvas.bind("<Control-MouseWheel>", self._on_ctrl_mousewheel)
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._canvas.bind("<Button-1>", lambda e: self._canvas.focus_set(), add=True)
        self._canvas.bind("<Control-c>", lambda e: self.copy_selection() or "break")
        self._canvas.bind("<Control-C>", lambda e: self.copy_selection() or "break")
        self._canvas.bind("<Control-i>", lambda e: self.copy_selection_image() or "break")
        self._canvas.bind("<Control-I>", lambda e: self.copy_selection_image() or "break")
        self._canvas.bind("<Control-o>", lambda e: self.copy_selection_ocr_text() or "break")
        self._canvas.bind("<Control-O>", lambda e: self.copy_selection_ocr_text() or "break")
        self._canvas.bind("<Next>",      self._on_page_down)
        self._canvas.bind("<Prior>",     self._on_page_up)
        self._canvas.bind("<Configure>", self._on_canvas_configure)

    @property
    def follow_selection(self) -> bool:
        return self._follow_selection.get()

    def focus_viewer(self) -> None:
        self._canvas.focus_set()

    def page_down(self) -> None:
        self._on_page_down(None)

    def page_up(self) -> None:
        self._on_page_up(None)

    def show_message(self, message: str) -> None:
        self._message_var.set(message)

    @property
    def is_loading(self) -> bool:
        """True while a render worker is active and pages are still pending."""
        return self._doc is not None and bool(self._pending_pages)

    def cancel_load(self) -> None:
        """Cancel an in-progress load and reset the viewer."""
        if self.is_loading:
            self.unload()
            self.show_message("Load cancelled.")
            self._status_cb("PDF load cancelled")

    def unload(self) -> None:
        self._load_token += 1
        if self._zoom_after is not None:
            try:
                self.after_cancel(self._zoom_after)
            except Exception:
                pass
            self._zoom_after = None
        if self._pump_after is not None:
            try:
                self.after_cancel(self._pump_after)
            except Exception:
                pass
            self._pump_after = None
        if self._visible_after is not None:
            try:
                self.after_cancel(self._visible_after)
            except Exception:
                pass
            self._visible_after = None
        self._clear_selection()
        self._canvas_needs_clear = False
        self._stale_photos.clear()
        self._page_dim_cache.clear()
        self._page_views.clear()
        self._page_view_by_index.clear()
        self._page_layouts.clear()
        self._pending_pages.clear()
        self._photos.clear()
        self._page_payloads.clear()
        self._page_count = 0
        self._loaded_count = 0
        self._failed_count = 0
        self._doc_path = None
        self._doc_bytes = None
        try:
            if self._doc is not None:
                self._doc.close()
        except Exception:
            pass
        self._doc = None
        self._canvas.delete("all")
        self._canvas.configure(scrollregion=(0, 0, 0, 0))
        self._message_var.set("PDF viewer ready")
        self._status_cb("PDF viewer ready")

    def load_pdf(self, path: str) -> None:
        if fitz is None or Image is None or ImageTk is None:
            self.show_message("PDF support requires PyMuPDF and Pillow.")
            self._status_cb("PDF viewer unavailable: missing PyMuPDF or Pillow")
            return

        norm = normalize(path)
        if not os.path.isfile(norm):
            self.show_message("The selected PDF no longer exists.")
            self._status_cb("Selected PDF no longer exists")
            return

        try:
            with open(norm, "rb") as handle:
                pdf_bytes = handle.read()
        except Exception as exc:
            self.show_message(f"Unable to read PDF: {exc}")
            self._status_cb(f"PDF read error: {exc}")
            return

        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            self.show_message(f"Unable to open PDF: {exc}")
            self._status_cb(f"PDF open error: {exc}")
            return

        self.unload()
        self._doc = doc
        self._doc_bytes = pdf_bytes
        self._doc_path = norm
        self._page_count = doc.page_count
        self._loaded_count = 0
        self._failed_count = 0
        self._load_token += 1
        self._canvas.delete("all")
        self._canvas.configure(scrollregion=(0, 0, 0, 0))
        self._rebuild_page_layouts()
        self._request_visible_pages(priority=True)

    def _rerender_current_pdf(self) -> None:
        if self._doc is None or self._page_count <= 0:
            return

        self._pending_zoom_anchor = self._capture_zoom_anchor()
        self._load_token += 1
        self._clear_selection()

        # Keep old photos alive and flag canvas for lazy wipe.
        # The canvas is cleared only when the first new page is ready to be placed,
        # so the old content stays visible until then instead of flashing blank.
        self._stale_photos = list(self._photos)
        self._canvas_needs_clear = True

        self._page_views.clear()
        self._page_view_by_index.clear()
        self._photos.clear()
        self._page_payloads.clear()
        self._pending_pages.clear()
        self._loaded_count = 0
        self._failed_count = 0
        self._rebuild_page_layouts()
        self._request_visible_pages(priority=True)

    def copy_selection(self) -> str | None:
        if not self._selection_text:
            if self._selection_bbox() is not None:
                self._status_cb("Ctrl+C: No text detected in selection — you might want to consider OCR (Ctrl+O)")
            return None
        self.root.clipboard_clear()
        self.root.clipboard_append(self._normalize_copied_text(self._selection_text))
        return "break"

    def copy_selection_image(self) -> str | None:
        """Capture selection as an image and push to Windows clipboard."""
        vprint("Ctrl+I: Attempting image capture...")
        if self._doc is None or fitz is None or Image is None:
            vprint("Ctrl+I: Cannot capture — PDF not loaded or dependencies missing")
            return None
        bbox = self._selection_bbox()
        if bbox is None:
            vprint("Ctrl+I: No selection rectangle detected")
            return None
        vprint(f"Ctrl+I: Selection detected — rendering image...")
        try:
            image = self._render_selection_image(bbox)
            if image is not None:
                vprint("Ctrl+I: Image rendered — pushing to clipboard...")
                copied = self._push_image_to_clipboard(image)
                if copied:
                    vprint("Ctrl+I: Image copied to clipboard!")
                    return "break"
                vprint("Ctrl+I: Clipboard copy failed")
            else:
                vprint("Ctrl+I: Image rendering returned None")
        except Exception as exc:
            vprint(f"Ctrl+I: Error during image capture: {exc}")
        return None

    def copy_selection_ocr_text(self) -> str | None:
        """Capture selection as image, OCR it, and copy text to clipboard."""
        vprint("Ctrl+O: Attempting OCR capture...")
        if self._doc is None or fitz is None or Image is None:
            self._status_cb("Ctrl+O: Cannot OCR — PDF not loaded or dependencies missing")
            return None

        bbox = self._selection_bbox()
        if bbox is None:
            self._status_cb("Ctrl+O: No selection rectangle detected")
            return None

        ok, backend_message = ocr_backend_status()
        if not ok:
            self._status_cb(f"Ctrl+O: OCR unavailable — {backend_message}")
            vprint(f"Ctrl+O: OCR unavailable — {backend_message}")
            return None

        try:
            image = self._render_selection_image(bbox)
            if image is None:
                self._status_cb("Ctrl+O: Image rendering failed")
                return None

            self._status_cb("Ctrl+O: Running OCR on selection...")
            text = extract_text_from_image(image)
            if not text.strip():
                self._status_cb("Ctrl+O: OCR returned no text")
                return None

            normalized = self._normalize_copied_text(text)
            self.root.clipboard_clear()
            self.root.clipboard_append(normalized)
            self.root.update_idletasks()
            self._status_cb(f"Ctrl+O: OCR text copied to clipboard ({len(normalized)} chars)")
            vprint(f"Ctrl+O: OCR text copied to clipboard ({len(normalized)} chars)")
            return "break"
        except Exception as exc:
            self._status_cb(f"Ctrl+O: OCR failed — {exc}")
            vprint(f"Ctrl+O: OCR failed — {exc}")
            return None

    def _render_selection_image(self, bbox: tuple[float, float, float, float]) -> Any:
        """Render the selected region from PDF pages into a PIL Image."""
        if self._doc is None or fitz is None or Image is None:
            vprint("_render_selection_image: Dependencies missing")
            return None
        x1, y1, x2, y2 = bbox
        width = int(round(x2 - x1))
        height = int(round(y2 - y1))
        vprint(f"_render_selection_image: Selection bbox {x1:.1f}, {y1:.1f}, {x2:.1f}, {y2:.1f}")
        vprint(f"_render_selection_image: Selection size {width}x{height} px")
        if width <= 0 or height <= 0:
            vprint(f"_render_selection_image: Invalid dimensions")
            return None

        combined = Image.new("RGB", (width, height), color=(255, 255, 255))
        vprint(f"_render_selection_image: Created blank canvas {width}x{height}")
        pages_rendered = 0
        for page_info in self._page_views:
            px1 = page_info["x"]
            py1 = page_info["y"]
            px2 = px1 + page_info["render_width"]
            py2 = py1 + page_info["render_height"]

            ix1 = max(x1, px1)
            iy1 = max(y1, py1)
            ix2 = min(x2, px2)
            iy2 = min(y2, py2)

            if ix1 >= ix2 or iy1 >= iy2:
                continue

            try:
                page = self._doc.load_page(page_info["index"])
                clip_rect = fitz.Rect(
                    (ix1 - px1) * page_info["page_width"] / page_info["render_width"],
                    (iy1 - py1) * page_info["page_height"] / page_info["render_height"],
                    (ix2 - px1) * page_info["page_width"] / page_info["render_width"],
                    (iy2 - py1) * page_info["page_height"] / page_info["render_height"],
                )
                vprint(f"_render_selection_image: Page {page_info['index']} clip_rect {clip_rect}")
                matrix = fitz.Matrix(self._zoom, self._zoom)
                pix = page.get_pixmap(matrix=matrix, alpha=False, clip=clip_rect, annots=True)
                vprint(f"_render_selection_image: Pixmap size {pix.width}x{pix.height}, stride {pix.stride}")
                partial = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                paste_x = int(round(ix1 - x1))
                paste_y = int(round(iy1 - y1))
                vprint(f"_render_selection_image: Pasting at ({paste_x}, {paste_y})")
                combined.paste(partial, (paste_x, paste_y))
                pages_rendered += 1
            except Exception as exc:
                vprint(f"_render_selection_image: Error on page {page_info['index']}: {exc}")
                continue
            vprint(f"_render_selection_image: Rendered {pages_rendered} page region(s)")
        return combined if pages_rendered > 0 else None

    def _push_image_to_clipboard(self, image: Any) -> bool:
        """Push a PIL Image to the Windows clipboard."""
        if sys.platform != "win32":
            vprint("_push_image_to_clipboard: Non-Windows platform, skipping clipboard push")
            return False
        from io import BytesIO
        import struct
        
        vprint(f"_push_image_to_clipboard: Image mode={image.mode} size={image.size}")
        vprint("_push_image_to_clipboard: Converting to BMP format...")
        output = BytesIO()
        image.convert("RGB").save(output, format="BMP")
        bmp_data = output.getvalue()
        vprint(f"_push_image_to_clipboard: BMP file size {len(bmp_data)} bytes")
        
        # Parse BMP header for validation
        if len(bmp_data) >= 26:
            width = struct.unpack('<I', bmp_data[18:22])[0]
            height = struct.unpack('<I', bmp_data[22:26])[0]
            vprint(f"_push_image_to_clipboard: BMP dimensions {width}x{height}")
        
        # Try win32clipboard first
        try:
            import win32clipboard
            vprint("_push_image_to_clipboard: Using win32clipboard...")
            vprint("_push_image_to_clipboard: Opening clipboard...")
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                # CF_DIB expects DIB format (bitmap header + pixel data, no file header)
                # Skip BMP file header (14 bytes) to get DIB format
                dib_data = bmp_data[14:]
                vprint(f"_push_image_to_clipboard: DIB data size {len(dib_data)} bytes")
                vprint(f"_push_image_to_clipboard: CF_DIB constant value = {win32clipboard.CF_DIB}")
                
                result = win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib_data)
                vprint(f"_push_image_to_clipboard: SetClipboardData returned: {result}")
                vprint("_push_image_to_clipboard: Successfully set clipboard data (CF_DIB format)")
            finally:
                win32clipboard.CloseClipboard()
                vprint("_push_image_to_clipboard: Clipboard closed")
            return True
        except ImportError as e:
            vprint(f"_push_image_to_clipboard: win32clipboard not available: {e}")
            vprint("_push_image_to_clipboard: Falling back to ctypes clipboard method...")
        except Exception as e:
            vprint(f"_push_image_to_clipboard: win32clipboard error: {type(e).__name__}: {e}")
            vprint("_push_image_to_clipboard: Falling back to ctypes clipboard method...")
        
        # Fallback: use ctypes to access Windows clipboard API directly
        try:
            import ctypes
            from ctypes import wintypes
            
            vprint("_push_image_to_clipboard: Using ctypes Windows API...")
            
            # Windows clipboard format constants
            CF_DIB = 8
            GMEM_MOVEABLE = 0x0002
            
            # Convert BMP to DIB format (skip 14-byte file header)
            dib_data = bmp_data[14:]
            
            # Allocate global memory
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            user32 = ctypes.WinDLL("user32", use_last_error=True)

            GlobalAlloc = kernel32.GlobalAlloc
            GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
            GlobalAlloc.restype = wintypes.HGLOBAL

            GlobalLock = kernel32.GlobalLock
            GlobalLock.argtypes = [wintypes.HGLOBAL]
            GlobalLock.restype = ctypes.c_void_p

            GlobalUnlock = kernel32.GlobalUnlock
            GlobalUnlock.argtypes = [wintypes.HGLOBAL]
            GlobalUnlock.restype = wintypes.BOOL

            GlobalFree = kernel32.GlobalFree
            GlobalFree.argtypes = [wintypes.HGLOBAL]
            GlobalFree.restype = wintypes.HGLOBAL

            OpenClipboard = user32.OpenClipboard
            OpenClipboard.argtypes = [wintypes.HWND]
            OpenClipboard.restype = wintypes.BOOL

            EmptyClipboard = user32.EmptyClipboard
            EmptyClipboard.argtypes = []
            EmptyClipboard.restype = wintypes.BOOL

            SetClipboardData = user32.SetClipboardData
            SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
            SetClipboardData.restype = wintypes.HANDLE

            CloseClipboard = user32.CloseClipboard
            CloseClipboard.argtypes = []
            CloseClipboard.restype = wintypes.BOOL
            
            vprint("_push_image_to_clipboard: Allocating global memory...")
            h_mem = GlobalAlloc(GMEM_MOVEABLE, len(dib_data))
            if not h_mem:
                err = ctypes.get_last_error()
                raise RuntimeError(f"Failed to allocate global memory (GetLastError={err})")
            
            vprint(f"_push_image_to_clipboard: Allocated {len(dib_data)} bytes")
            
            # Lock and copy data
            p_mem = GlobalLock(h_mem)
            if not p_mem:
                err = ctypes.get_last_error()
                GlobalFree(h_mem)
                raise RuntimeError(f"Failed to lock memory (GetLastError={err})")
            
            ctypes.memmove(p_mem, dib_data, len(dib_data))
            GlobalUnlock(h_mem)
            
            vprint("_push_image_to_clipboard: Opening clipboard...")
            if not OpenClipboard(None):
                err = ctypes.get_last_error()
                GlobalFree(h_mem)
                raise RuntimeError(f"Failed to open clipboard (GetLastError={err})")
            
            try:
                if not EmptyClipboard():
                    err = ctypes.get_last_error()
                    GlobalFree(h_mem)
                    raise RuntimeError(f"Failed to empty clipboard (GetLastError={err})")
                
                vprint(f"_push_image_to_clipboard: Setting clipboard data with CF_DIB={CF_DIB}...")
                result = SetClipboardData(CF_DIB, h_mem)
                if not result:
                    err = ctypes.get_last_error()
                    GlobalFree(h_mem)
                    raise RuntimeError(f"SetClipboardData returned NULL (GetLastError={err})")
                
                vprint("_push_image_to_clipboard: Successfully set clipboard data (ctypes/CF_DIB)")
                return True
            finally:
                if not CloseClipboard():
                    vprint("_push_image_to_clipboard: Warning - CloseClipboard failed")
                else:
                    vprint("_push_image_to_clipboard: Clipboard closed")
        except Exception as e:
            import traceback
            vprint(f"_push_image_to_clipboard: ctypes error: {type(e).__name__}: {e}")
            vprint(f"_push_image_to_clipboard: Traceback: {traceback.format_exc()}")
            return False

    @staticmethod
    def _normalize_copied_text(text: str) -> str:
        """Remove PDF line-wrap breaks while preserving paragraph breaks."""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        normalized = re.sub(r"-\n(?=\w)", "", normalized)

        paragraphs = re.split(r"\n\s*\n", normalized)
        cleaned: list[str] = []
        for paragraph in paragraphs:
            stripped = paragraph.strip()
            if not stripped:
                continue
            single_line = re.sub(r"\s*\n\s*", " ", stripped)
            single_line = re.sub(r"[ \t]+", " ", single_line).strip()
            cleaned.append(single_line)
        return "\n\n".join(cleaned)

    def _ensure_queue_pump(self) -> None:
        if self._pump_after is None:
            self._pump_after = self.after(_QUEUE_TICK_MS, self._drain_queue)

    def _drain_queue(self) -> None:
        self._pump_after = None
        while True:
            try:
                kind, token, payload = self._render_queue.get_nowait()
            except queue.Empty:
                break

            if token != self._load_token:
                continue

            if kind == "page":
                self._append_page(payload, token)
                self._pending_pages.discard(payload.get("index", -1))
                self._emit_progress("loading")
            elif kind == "page_skip":
                self._pending_pages.discard(payload.get("index", -1))
                self._failed_count += 1
                if self._failed_count <= 3:
                    page_no = payload.get("index", 0) + 1
                    err = payload.get("error", "rendering error")
                    self._status_cb(f"Skipped PDF page {page_no}: {err}")
                self._emit_progress("loading")
            elif kind == "done":
                self._emit_progress("ready")
            elif kind == "error":
                self.show_message(payload)
                self._status_cb(payload)

        if self._doc is not None and self._pending_pages:
            self._ensure_queue_pump()

    def _start_render_for_indices(self, indices: list[int]) -> None:
        if self._doc is None:
            return
        if not indices:
            return
        token = self._load_token
        self._pending_pages.update(indices)
        self._worker = threading.Thread(
            target=self._render_pages,
            args=(token, indices, self._zoom, self._doc_bytes, self._doc_path),
            daemon=True,
        )
        self._worker.start()
        self._ensure_queue_pump()

    def _render_pages(
        self,
        token: int,
        indices: list[int],
        zoom: float,
        doc_bytes: bytes | None,
        doc_path: str | None,
    ) -> None:
        if fitz is None:
            return
        worker_doc = None
        try:
            if doc_bytes:
                worker_doc = fitz.open(stream=doc_bytes, filetype="pdf")
            elif doc_path:
                worker_doc = fitz.open(doc_path)
            else:
                raise RuntimeError("No PDF source available for rendering.")
            for index in indices:
                if token != self._load_token:
                    return
                try:
                    payload = self._render_page(worker_doc, index, zoom)
                    self._render_queue.put(("page", token, payload))
                except Exception as exc:
                    self._render_queue.put((
                        "page_skip",
                        token,
                        {"index": index, "error": str(exc)},
                    ))
            self._render_queue.put(("done", token, None))
        except Exception as exc:
            self._render_queue.put(("error", token, f"PDF background rendering stopped: {exc}"))
            self._render_queue.put(("done", token, None))
        finally:
            try:
                if worker_doc is not None:
                    worker_doc.close()
            except Exception:
                pass

    def _rebuild_page_layouts(self) -> None:
        if self._doc is None:
            self._page_layouts = []
            self._canvas.configure(scrollregion=(0, 0, 0, 0))
            return

        layouts: list[dict] = []
        y = _MARGIN_Y
        max_width = 0
        for index in range(self._page_count):
            if index in self._page_dim_cache:
                page_w, page_h = self._page_dim_cache[index]
            else:
                rect = self._doc.load_page(index).rect
                page_w, page_h = rect.width, rect.height
                self._page_dim_cache[index] = (page_w, page_h)
            render_width = max(1, int(round(page_w * self._zoom)))
            render_height = max(1, int(round(page_h * self._zoom)))
            layouts.append({
                "index": index,
                "x": 0.0,
                "y": y,
                "render_width": render_width,
                "render_height": render_height,
                "page_width": page_w,
                "page_height": page_h,
            })
            y += render_height + _PAGE_GAP
            max_width = max(max_width, render_width)

        self._page_layouts = layouts
        self._loaded_count = 0
        scroll_height = (layouts[-1]["y"] + layouts[-1]["render_height"] + _MARGIN_Y) if layouts else 0
        scroll_width = max(max_width + (_MARGIN_X * 2), 1)
        self._canvas.configure(scrollregion=(0, 0, scroll_width, scroll_height))
        self._recenter_pages()
        self._emit_progress("loading")

    def _visible_page_indices(self) -> list[int]:
        if not self._page_layouts:
            return []

        viewport_top = self._canvas.canvasy(0)
        viewport_bottom = self._canvas.canvasy(max(self._canvas.winfo_height(), 1))
        first = 0
        last = -1
        found = False

        for layout in self._page_layouts:
            page_top = layout["y"]
            page_bottom = layout["y"] + layout["render_height"]
            if page_bottom < viewport_top:
                continue
            if page_top > viewport_bottom:
                break
            index = layout["index"]
            if not found:
                first = index
                found = True
            last = index

        if not found:
            first = 0
            last = min(self._page_count - 1, _VISIBLE_PAGE_BUFFER)

        start = max(0, first - _VISIBLE_PAGE_BUFFER)
        end = min(self._page_count - 1, last + _VISIBLE_PAGE_BUFFER)
        return list(range(start, end + 1))

    def _request_visible_pages(self, priority: bool = False) -> None:
        if self._doc is None or not self._page_layouts:
            return
        indices = self._visible_page_indices()
        missing = [
            index for index in indices
            if index not in self._page_view_by_index and index not in self._pending_pages
        ]
        if missing:
            self._start_render_for_indices(missing)
            return
        if priority:
            self._emit_progress("ready")

    def _schedule_visible_render(self) -> None:
        if self._visible_after is not None:
            try:
                self.after_cancel(self._visible_after)
            except Exception:
                pass
        self._visible_after = self.after(40, self._on_visible_render_tick)

    def _on_visible_render_tick(self) -> None:
        self._visible_after = None
        self._request_visible_pages()

    def _render_page(self, doc, index: int, zoom: float) -> dict:
        if fitz is None:
            raise RuntimeError("PyMuPDF is unavailable.")
        page = doc.load_page(index)
        rect = page.rect
        matrix = fitz.Matrix(zoom, zoom)
        try:
            pix = page.get_pixmap(matrix=matrix, alpha=False, annots=True)
        except Exception:
            pix = page.get_pixmap(matrix=matrix, alpha=False, annots=False)
        # Pass raw RGB samples — avoids a PNG encode here and a PNG decode in _append_page.
        return {
            "index": index,
            "samples": bytes(pix.samples),
            "render_width": pix.width,
            "render_height": pix.height,
            "page_width": rect.width,
            "page_height": rect.height,
        }

    def _append_page(self, payload: dict, token: int) -> None:
        if token != self._load_token or Image is None or ImageTk is None:
            return

        index = payload["index"]
        self._page_payloads[index] = payload
        if index in self._page_view_by_index:
            return

        if index < 0 or index >= len(self._page_layouts):
            return
        layout = self._page_layouts[index]

        # Lazy canvas wipe: clear old content just before placing the first new page.
        if self._canvas_needs_clear:
            self._canvas.delete("all")
            self._canvas.configure(scrollregion=(0, 0, 0, 0))
            self._stale_photos.clear()
            self._canvas_needs_clear = False
            self._recenter_pages()

        image = Image.frombytes(
            "RGB",
            (payload["render_width"], payload["render_height"]),
            payload["samples"],
        )
        photo = ImageTk.PhotoImage(image, master=self.root)
        self._photos.append(photo)
        x = layout["x"]
        y = layout["y"]
        shadow = self._canvas.create_rectangle(
            x + 2,
            y + 2,
            x + payload["render_width"] + 2,
            y + payload["render_height"] + 2,
            fill=_BORDER,
            outline="",
        )
        image_id = self._canvas.create_image(x, y, anchor="nw", image=photo)
        border = self._canvas.create_rectangle(
            x - 1,
            y - 1,
            x + payload["render_width"] + 1,
            y + payload["render_height"] + 1,
            outline=_BORDER,
            width=1,
        )
        self._canvas.tag_lower(shadow, image_id)
        self._canvas.tag_raise(border, image_id)

        view = {
            "index": index,
            "x": x,
            "y": y,
            "render_width": payload["render_width"],
            "render_height": payload["render_height"],
            "page_width": payload["page_width"],
            "page_height": payload["page_height"],
            "items": (shadow, image_id, border),
        }
        self._page_view_by_index[index] = view
        self._page_views = [self._page_view_by_index[k] for k in sorted(self._page_view_by_index)]
        self._loaded_count = len(self._page_view_by_index)
        self._restore_zoom_anchor_if_possible()

    def _on_mousewheel(self, event: tk.Event) -> str:
        units = self._wheel_units(event.delta)
        self._canvas.yview_scroll(units, "units")
        self._schedule_visible_render()
        return "break"

    def _on_shift_mousewheel(self, event: tk.Event) -> str:
        units = self._wheel_units(event.delta)
        self._canvas.xview_scroll(units, "units")
        self._schedule_visible_render()
        return "break"

    def _wheel_units(self, delta: int) -> int:
        units = int((-delta / 120) * _SCROLL_SPEED)
        if units == 0 and delta != 0:
            units = -1 if delta > 0 else 1
        return units

    def _on_ctrl_mousewheel(self, event: tk.Event) -> str:
        direction = 1 if event.delta > 0 else -1
        factor = _ZOOM_STEP if direction > 0 else (1 / _ZOOM_STEP)
        new_zoom = min(_ZOOM_MAX, max(_ZOOM_MIN, self._zoom * factor))
        if abs(new_zoom - self._zoom) < 1e-9:
            return "break"
        self._zoom = new_zoom
        self._status_cb(f"PDF zoom: {round(self._zoom * 100)}%")
        if self._doc is not None:
            self._schedule_zoom_rerender()
        else:
            self.show_message(f"PDF zoom set to {round(self._zoom * 100)}%")
        return "break"

    def _schedule_zoom_rerender(self) -> None:
        """Debounce zoom: re-render fires once, _ZOOM_DEBOUNCE_MS after the last scroll tick."""
        if self._zoom_after is not None:
            try:
                self.after_cancel(self._zoom_after)
            except Exception:
                pass
        self._zoom_after = self.after(_ZOOM_DEBOUNCE_MS, self._do_zoom_rerender)

    def _do_zoom_rerender(self) -> None:
        self._zoom_after = None
        self._rerender_current_pdf()

    def _current_top_page(self) -> int:
        """Index of the first page whose bottom edge is below the viewport top."""
        if not self._page_layouts:
            return 0
        viewport_top = self._canvas.canvasy(0)
        for layout in self._page_layouts:
            if layout["y"] + layout["render_height"] > viewport_top:
                return layout["index"]
        return self._page_count - 1

    def _scroll_to_page(self, index: int) -> None:
        if not self._page_layouts:
            return
        index = max(0, min(index, self._page_count - 1))
        layout = self._page_layouts[index]
        last = self._page_layouts[-1]
        total_height = last["y"] + last["render_height"] + _MARGIN_Y
        if total_height <= 0:
            return
        self._canvas.yview_moveto(max(0.0, min(1.0, layout["y"] / total_height)))
        self._schedule_visible_render()

    def _on_page_down(self, event: tk.Event | None = None) -> str:
        if self._doc is None:
            return "break"
        current = self._current_top_page()
        if current >= self._page_count - 1:
            # Already on the last page — scroll to the very bottom.
            self._canvas.yview_moveto(1.0)
            self._schedule_visible_render()
        else:
            self._scroll_to_page(current + 1)
        return "break"

    def _on_page_up(self, event: tk.Event | None = None) -> str:
        if self._doc is None:
            return "break"
        current = self._current_top_page()
        if current == 0:
            # Already on the first page — scroll to the very top.
            self._canvas.yview_moveto(0.0)
            self._schedule_visible_render()
            return "break"
        viewport_top = self._canvas.canvasy(0)
        layout = self._page_layouts[current]
        # If already near the top of the current page, jump to the previous one.
        if abs(layout["y"] - viewport_top) < 10:
            self._scroll_to_page(current - 1)
        else:
            self._scroll_to_page(current)
        return "break"

    def _capture_zoom_anchor(self) -> dict | None:
        if not self._page_views:
            return None

        viewport_w = max(self._canvas.winfo_width(), 1)
        viewport_h = max(self._canvas.winfo_height(), 1)
        anchor_x = self._canvas.canvasx(viewport_w / 2)
        anchor_y = self._canvas.canvasy(viewport_h / 2)
        x_frac = self._canvas.xview()[0] if self._canvas.xview() else 0.0
        y_frac = self._canvas.yview()[0] if self._canvas.yview() else 0.0

        for page in self._page_views:
            px1 = page["x"]
            py1 = page["y"]
            px2 = px1 + page["render_width"]
            py2 = py1 + page["render_height"]
            if not (px1 <= anchor_x <= px2 and py1 <= anchor_y <= py2):
                continue

            rel_x = (anchor_x - px1) / max(page["render_width"], 1)
            rel_y = (anchor_y - py1) / max(page["render_height"], 1)
            rel_x = min(1.0, max(0.0, rel_x))
            rel_y = min(1.0, max(0.0, rel_y))
            return {
                "page_index": page["index"],
                "rel_x": rel_x,
                "rel_y": rel_y,
                "x_frac": x_frac,
                "y_frac": y_frac,
                "fallback_applied": False,
            }

        return {
            "page_index": None,
            "rel_x": 0.5,
            "rel_y": 0.5,
            "x_frac": x_frac,
            "y_frac": y_frac,
            "fallback_applied": False,
        }

    def _restore_zoom_anchor_if_possible(self) -> None:
        anchor = self._pending_zoom_anchor
        if anchor is None:
            return

        if not anchor.get("fallback_applied", False):
            self._canvas.xview_moveto(min(1.0, max(0.0, anchor.get("x_frac", 0.0))))
            self._canvas.yview_moveto(min(1.0, max(0.0, anchor.get("y_frac", 0.0))))
            anchor["fallback_applied"] = True

        page_index = anchor.get("page_index")
        if page_index is None:
            self._pending_zoom_anchor = None
            return

        page = next((p for p in self._page_views if p["index"] == page_index), None)
        if page is None:
            return

        target_x = page["x"] + anchor["rel_x"] * page["render_width"]
        target_y = page["y"] + anchor["rel_y"] * page["render_height"]
        self._scroll_canvas_point_to_view_center(target_x, target_y)
        self._pending_zoom_anchor = None

    def _scroll_canvas_point_to_view_center(self, x: float, y: float) -> None:
        self._canvas.update_idletasks()
        viewport_w = max(self._canvas.winfo_width(), 1)
        viewport_h = max(self._canvas.winfo_height(), 1)

        region = self._canvas.cget("scrollregion")
        if not region:
            return
        try:
            left, top, right, bottom = [float(v) for v in str(region).split()]
        except Exception:
            return

        total_w = max(right - left, 1.0)
        total_h = max(bottom - top, 1.0)
        max_x = max(total_w - viewport_w, 0.0)
        max_y = max(total_h - viewport_h, 0.0)

        desired_left = min(max(x - (viewport_w / 2), 0.0), max_x)
        desired_top = min(max(y - (viewport_h / 2), 0.0), max_y)

        x_frac = 0.0 if max_x <= 0 else desired_left / max_x
        y_frac = 0.0 if max_y <= 0 else desired_top / max_y
        self._canvas.xview_moveto(x_frac)
        self._canvas.yview_moveto(y_frac)

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self._recenter_pages()
        self._schedule_visible_render()

    def _on_press(self, event: tk.Event) -> None:
        if self._doc is None:
            return
        self._clear_selection()
        self._drag_anchor = (self._canvas.canvasx(event.x), self._canvas.canvasy(event.y))
        self._drag_current = self._drag_anchor

    def _on_drag(self, event: tk.Event) -> None:
        if self._drag_anchor is None:
            return
        self._drag_current = (self._canvas.canvasx(event.x), self._canvas.canvasy(event.y))
        self._refresh_selection_visuals()

    def _on_release(self, event: tk.Event) -> None:
        if self._drag_anchor is None:
            return
        self._drag_current = (self._canvas.canvasx(event.x), self._canvas.canvasy(event.y))
        self._refresh_selection_visuals()
        self._selection_text = self._extract_selection_text()

    def _clear_selection(self) -> None:
        self._selection_text = ""
        self._drag_anchor = None
        self._drag_current = None
        while self._selection_items:
            self._canvas.delete(self._selection_items.pop())

    def _refresh_selection_visuals(self) -> None:
        while self._selection_items:
            self._canvas.delete(self._selection_items.pop())

        bbox = self._selection_bbox()
        if bbox is None:
            return
        x1, y1, x2, y2 = bbox

        for page in self._page_views:
            px1 = page["x"]
            py1 = page["y"]
            px2 = px1 + page["render_width"]
            py2 = py1 + page["render_height"]
            ix1 = max(x1, px1)
            iy1 = max(y1, py1)
            ix2 = min(x2, px2)
            iy2 = min(y2, py2)
            if ix1 >= ix2 or iy1 >= iy2:
                continue
            rect_id = self._canvas.create_rectangle(
                ix1,
                iy1,
                ix2,
                iy2,
                fill=_ACCENT,
                outline=_ACCENT,
                stipple="gray25",
                width=1,
            )
            self._selection_items.append(rect_id)

    def _selection_bbox(self) -> tuple[float, float, float, float] | None:
        if self._drag_anchor is None or self._drag_current is None:
            return None
        x1, y1 = self._drag_anchor
        x2, y2 = self._drag_current
        if abs(x2 - x1) < 3 and abs(y2 - y1) < 3:
            return None
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)

    def _extract_selection_text(self) -> str:
        if self._doc is None or fitz is None:
            return ""
        bbox = self._selection_bbox()
        if bbox is None:
            return ""
        x1, y1, x2, y2 = bbox
        parts: list[str] = []
        for page_info in self._page_views:
            px1 = page_info["x"]
            py1 = page_info["y"]
            px2 = px1 + page_info["render_width"]
            py2 = py1 + page_info["render_height"]
            ix1 = max(x1, px1)
            iy1 = max(y1, py1)
            ix2 = min(x2, px2)
            iy2 = min(y2, py2)
            if ix1 >= ix2 or iy1 >= iy2:
                continue

            rect = fitz.Rect(
                (ix1 - px1) * page_info["page_width"] / page_info["render_width"],
                (iy1 - py1) * page_info["page_height"] / page_info["render_height"],
                (ix2 - px1) * page_info["page_width"] / page_info["render_width"],
                (iy2 - py1) * page_info["page_height"] / page_info["render_height"],
            )
            try:
                page = self._doc.load_page(page_info["index"])
                text = page.get_text("text", clip=rect, sort=True).strip()
            except Exception:
                text = ""
            if text:
                parts.append(text)
        return "\n\n".join(parts)

    def _emit_progress(self, mode: str) -> None:
        name = os.path.basename(to_display(self._doc_path or ""))
        suffix = ""
        if self._failed_count > 0:
            suffix = f" ({self._failed_count} skipped)"
        if mode == "ready":
            if self._loaded_count == 0 and self._page_count > 0:
                message = f"{name} — no pages could be rendered"
            else:
                message = (
                    f"{name} — {self._loaded_count} / {self._page_count} page(s) rendered"
                    f"{suffix} at {round(self._zoom * 100)}%"
                )
        else:
            message = (
                f"Rendering {name} — {self._loaded_count} / {self._page_count} "
                f"page(s){suffix} at {round(self._zoom * 100)}%"
            )
        self.show_message(message)
        self._status_cb(message)

    def _render_loading_state(self) -> None:
        self._canvas.delete("all")
        width = max(self._canvas.winfo_width(), 320)
        height = max(self._canvas.winfo_height(), 180)
        self._canvas.create_text(
            width / 2,
            height / 2,
            text="Loading PDF…",
            fill=_TEXT_MUTE,
            font=(_FONT, _SZ_S),
        )
        self._canvas.configure(scrollregion=(0, 0, width, height))

    def _center_x(self, render_width: int) -> float:
        viewport_width = max(self._canvas.winfo_width(), 0)
        if viewport_width <= 0:
            return _MARGIN_X
        return max(_MARGIN_X, (viewport_width - render_width) / 2)

    def _recenter_pages(self) -> None:
        if not self._page_layouts:
            return
        max_right = 0
        max_width = max((page["render_width"] for page in self._page_layouts), default=0)
        for layout in self._page_layouts:
            index = layout["index"]
            new_x = self._center_x(layout["render_width"])
            dx = new_x - layout["x"]
            if abs(dx) > 0.01:
                view = self._page_view_by_index.get(index)
                if view is not None:
                    for item in view["items"]:
                        self._canvas.move(item, dx, 0)
                    view["x"] = new_x
                layout["x"] = new_x
            max_right = max(max_right, layout["x"] + layout["render_width"])
        height = self._page_layouts[-1]["y"] + self._page_layouts[-1]["render_height"] + _MARGIN_Y
        scroll_width = max(max_right + _MARGIN_X, max_width + (_MARGIN_X * 2))
        self._canvas.configure(scrollregion=(0, 0, scroll_width, height))
