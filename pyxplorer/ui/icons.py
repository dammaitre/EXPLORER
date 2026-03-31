"""
Phase 9 — Icon generation via Pillow.

Draws minimal 16×16 icons at runtime so no external assets are needed.
If Pillow is not installed every value is None and callers fall back to
the plain-text treeview style (fully functional, just no icons).
"""
import tkinter as tk

try:
    from PIL import Image, ImageDraw, ImageTk
    _PIL = True
except ImportError:
    _PIL = False


def _folder(size: int) -> "Image.Image":
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    s   = size
    # Tab (top-left bump that makes it look like a folder)
    d.rectangle([1, s // 4, s // 2, s // 2], fill="#FCC84A")
    # Body
    d.rectangle([1, s // 2 - 1, s - 2, s - 3], fill="#FCC84A")
    # Subtle dark outline on body bottom/right
    d.rectangle([1, s // 2 - 1, s - 2, s - 3], outline="#C9A227", width=1)
    return img


def _file(size: int) -> "Image.Image":
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d    = ImageDraw.Draw(img)
    fold = max(3, size // 4)
    r    = size - 2          # right edge
    b    = size - 2          # bottom edge
    # Body (polygon with top-right corner cut)
    d.polygon([(1, 1), (r - fold, 1), (r, 1 + fold), (r, b), (1, b)],
              fill="#DDE3EA", outline="#8A97A6")
    # Dog-ear triangle
    d.polygon([(r - fold, 1), (r - fold, 1 + fold), (r, 1 + fold)],
              fill="#A8B5C2", outline="#8A97A6")
    return img


def _drive(size: int) -> "Image.Image":
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    s   = size
    mid = s // 2
    # Cylinder body
    d.rectangle([1, mid - 1, s - 2, s - 3], fill="#74B9FF", outline="#0984E3")
    # Top ellipse (lighter)
    d.ellipse([1, mid - 4, s - 2, mid + 2], fill="#A8D4FF", outline="#0984E3")
    # Bottom seam ellipse
    d.ellipse([1, s - 6, s - 2, s - 2], fill="#5BA4EF", outline="#0984E3")
    return img


def load(root: tk.Tk, size: int = 16) -> dict:
    """
    Return {'folder', 'file', 'drive'} → PhotoImage | None.
    All None when Pillow is unavailable.
    """
    if not _PIL:
        return {"folder": None, "file": None, "drive": None}

    return {
        "folder": ImageTk.PhotoImage(_folder(size), master=root),
        "file":   ImageTk.PhotoImage(_file(size),   master=root),
        "drive":  ImageTk.PhotoImage(_drive(size),  master=root),
    }
