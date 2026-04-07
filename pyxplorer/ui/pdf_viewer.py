import io
import importlib
import os
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable, Any

from ..core.longpath import normalize, to_display
from ..logging import vprint
from ..settings import THEME as _T, SCROLL_SPEED
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
_DEFAULT_ZOOM = 1.25
_SCROLL_SPEED = SCROLL_SPEED


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
        self._doc_path: str | None = None
        self._page_count = 0
        self._loaded_count = 0
        self._failed_count = 0
        self._load_token = 0
        self._render_queue: queue.Queue = queue.Queue()
        self._pump_after: str | None = None
        self._worker: threading.Thread | None = None
        self._page_views: list[dict] = []
        self._photos: list = []
        self._page_payloads: dict[int, dict] = {}
        self._selection_items: list[int] = []
        self._selection_text = ""
        self._drag_anchor: tuple[float, float] | None = None
        self._drag_current: tuple[float, float] | None = None
        self._zoom = _DEFAULT_ZOOM

        self._build()
        self.show_message("Select a single PDF file and press Ctrl+Alt+P.")

    def _build(self) -> None:
        self._message_var = tk.StringVar(value="")
        ttk.Label(
            self,
            textvariable=self._message_var,
            anchor="w",
            font=(_FONT, _SZ_S),
            foreground=_TEXT_MUTE,
            padding=(12, 8),
        ).pack(side=tk.TOP, fill=tk.X)

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
        self._canvas.bind("<Configure>", self._on_canvas_configure)

    def focus_viewer(self) -> None:
        self._canvas.focus_set()

    def show_message(self, message: str) -> None:
        self._message_var.set(message)

    @property
    def is_loading(self) -> bool:
        """True while a render worker is active and pages are still pending."""
        return (
            self._doc is not None
            and self._loaded_count + self._failed_count < self._page_count
        )

    def cancel_load(self) -> None:
        """Cancel an in-progress load and reset the viewer."""
        if self.is_loading:
            self.unload()
            self.show_message("Load cancelled.")
            self._status_cb("PDF load cancelled")

    def unload(self) -> None:
        self._load_token += 1
        if self._pump_after is not None:
            try:
                self.after_cancel(self._pump_after)
            except Exception:
                pass
            self._pump_after = None
        self._clear_selection()
        self._page_views.clear()
        self._photos.clear()
        self._page_payloads.clear()
        self._page_count = 0
        self._loaded_count = 0
        self._failed_count = 0
        self._doc_path = None
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
            doc = fitz.open(norm)
        except Exception as exc:
            self.show_message(f"Unable to open PDF: {exc}")
            self._status_cb(f"PDF open error: {exc}")
            return

        self.unload()
        self._doc = doc
        self._doc_path = norm
        self._page_count = doc.page_count
        self._loaded_count = 0
        self._failed_count = 0
        self._load_token += 1
        token = self._load_token
        self._canvas.delete("all")
        self._canvas.configure(scrollregion=(0, 0, 0, 0))
        self._render_loading_state()
        self._emit_progress("loading")

        self._worker = threading.Thread(
            target=self._render_all_pages,
            args=(norm, token, self._page_count, self._zoom),
            daemon=True,
        )
        self._worker.start()
        self._ensure_queue_pump()

    def copy_selection(self) -> str | None:
        if not self._selection_text:
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
                self._emit_progress("loading")
            elif kind == "page_skip":
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

        if self._doc is not None and (self._loaded_count + self._failed_count) < self._page_count:
            self._ensure_queue_pump()

    def _render_all_pages(self, path: str, token: int, page_count: int, zoom: float) -> None:
        if fitz is None:
            return
        worker_doc = None
        try:
            worker_doc = fitz.open(path)
            for index in range(page_count):
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
        return {
            "index": index,
            "png": pix.tobytes("png"),
            "render_width": pix.width,
            "render_height": pix.height,
            "page_width": rect.width,
            "page_height": rect.height,
        }

    def _append_page(self, payload: dict, token: int) -> None:
        if token != self._load_token or Image is None or ImageTk is None:
            return

        self._page_payloads[payload["index"]] = payload

        image = Image.open(io.BytesIO(payload["png"]))
        photo = ImageTk.PhotoImage(image, master=self.root)
        self._photos.append(photo)

        y = _MARGIN_Y
        if self._page_views:
            previous = self._page_views[-1]
            y = previous["y"] + previous["render_height"] + _PAGE_GAP

        x = self._center_x(payload["render_width"])
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

        self._page_views.append({
            "index": payload["index"],
            "x": x,
            "y": y,
            "render_width": payload["render_width"],
            "render_height": payload["render_height"],
            "page_width": payload["page_width"],
            "page_height": payload["page_height"],
            "items": (shadow, image_id, border),
        })
        self._loaded_count = len(self._page_views)
        self._recenter_pages()

    def _on_mousewheel(self, event: tk.Event) -> str:
        units = self._wheel_units(event.delta)
        self._canvas.yview_scroll(units, "units")
        return "break"

    def _on_shift_mousewheel(self, event: tk.Event) -> str:
        units = self._wheel_units(event.delta)
        self._canvas.xview_scroll(units, "units")
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
        if self._doc_path:
            self.load_pdf(self._doc_path)
        else:
            self.show_message(f"PDF zoom set to {round(self._zoom * 100)}%")
        return "break"

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self._recenter_pages()

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
                    f"{name} — {self._loaded_count} / {self._page_count} page(s) ready"
                    f"{suffix} at {round(self._zoom * 100)}%"
                )
        else:
            message = (
                f"Loading {name} — {self._loaded_count} / {self._page_count} "
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
        if not self._page_views:
            return
        max_right = 0
        max_width = max((page["render_width"] for page in self._page_views), default=0)
        for page in self._page_views:
            new_x = self._center_x(page["render_width"])
            dx = new_x - page["x"]
            if abs(dx) > 0.01:
                for item in page["items"]:
                    self._canvas.move(item, dx, 0)
                page["x"] = new_x
            max_right = max(max_right, page["x"] + page["render_width"])
        height = self._page_views[-1]["y"] + self._page_views[-1]["render_height"] + _MARGIN_Y
        scroll_width = max(max_right + _MARGIN_X, max_width + (_MARGIN_X * 2))
        self._canvas.configure(scrollregion=(0, 0, scroll_width, height))
