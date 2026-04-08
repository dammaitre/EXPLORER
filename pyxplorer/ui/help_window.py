"""
Help window displaying all keyboard shortcuts.
Triggered by ? (Shift+/) key.
"""
import tkinter as tk
from tkinter import ttk

from ..settings import THEME as _T

_FONT   = _T["font_family"]
_SZ     = _T["font_size_base"]
_SZ_S   = _T["font_size_small"]
BG      = _T["bg"]
BG_DARK = _T["bg_dark"]
TEXT    = _T["text"]
TEXT_M  = _T["text_mute"]
ACCENT  = _T["accent"]


def show_help_window(root: tk.Tk) -> None:
    """Open a modeless help window displaying keyboard shortcuts."""
    
    # Check if a help window already exists and focus it
    if hasattr(root, '_help_window') and root._help_window and root._help_window.winfo_exists():
        root._help_window.lift()
        root._help_window.focus_force()
        return
    
    win = tk.Toplevel(root)
    root._help_window = win
    win.title("Keyboard Shortcuts")
    win.geometry("700x600")
    win.minsize(500, 400)
    win.configure(bg=BG)
    
    # Close handler
    def _on_close():
        root._help_window = None
        win.destroy()
    
    win.protocol("WM_DELETE_WINDOW", _on_close)
    win.transient(root)
    
    # Title
    title_frame = ttk.Frame(win)
    title_frame.pack(fill=tk.X, padx=14, pady=(12, 8))
    ttk.Label(
        title_frame,
        text="Keyboard Shortcuts",
        font=(_FONT, _SZ + 2, "bold"),
        foreground=ACCENT,
    ).pack(anchor="w")
    
    # Scrollable text area
    text_frame = ttk.Frame(win)
    text_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))
    
    text_widget = tk.Text(
        text_frame,
        font=(_FONT, _SZ_S),
        bg=BG_DARK,
        fg=TEXT,
        insertbackground=ACCENT,
        relief=tk.FLAT,
        borderwidth=0,
        wrap=tk.WORD,
    )
    
    scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=text_widget.yview)
    text_widget.configure(yscrollcommand=scrollbar.set)
    
    text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    
    # Configure text tags for formatting
    text_widget.tag_configure("heading", foreground=ACCENT, font=(_FONT, _SZ_S, "bold"))
    text_widget.tag_configure("key", foreground=ACCENT, font=(_FONT, _SZ_S, "bold"))
    text_widget.tag_configure("desc", foreground=TEXT)
    text_widget.tag_configure("muted", foreground=TEXT_M)
    
    # Populate shortcuts
    shortcuts_data = [
        ("FILE OPERATIONS", [
            ("Ctrl+C", "Copy selected items to clipboard"),
            ("Ctrl+X", "Cut selected items to clipboard"),
            ("Ctrl+V", "Paste items from clipboard (async)"),
            ("Ctrl+Shift+C", "Copy current/selected paths to OS clipboard"),
            ("Ctrl+Shift+N", "Copy selected item name(s) to OS clipboard"),
            ("Ctrl+Shift+X", "Create a new folder"),
            ("Delete", "Permanently delete selected items"),
            ("F2", "Rename selected item (single selection only)"),
        ]),
        ("NAVIGATION & SELECTION", [
            ("Tab", "Toggle focus: main frame ↔ search window"),
            ("Left", "Go up to parent directory"),
            ("Right", "Open selected directory or file"),
            ("Up / Down", "Move selection up/down"),
            ("BackSpace", "Go up to parent directory (with focus)"),
            ("Ctrl+N", "Open current directory in new window"),
            ("Ctrl+S", "Toggle star on selected item"),
            ("Alt+Up / Alt+Down", "Jump to previous/next starred item"),
        ]),
        ("SEARCH & FILTERS", [
            ("Ctrl+F", "Open regex search dialog"),
            ("Ctrl+H", "Toggle heuristics window (if scripts available)"),
            ("Ctrl+Shift+R", "Reload user settings from disk"),
        ]),
        ("TAGGING", [
            ("Ctrl+T", "Set/clear tag on selected item(s)"),
            ("", "(tags shown as 'aka <tag>' in file list)"),
        ]),
        ("LOWER PANEL (P/T/N/I)", [
            ("Ctrl+Alt+P", "Show PDF panel and load selected PDF"),
            ("  Ctrl+C", "Copy selected text to OS clipboard (in PDF)"),
            ("  Ctrl+I", "Copy selected region as image to clipboard (in PDF)"),
            ("  Ctrl+O", "OCR selected region and copy text (in PDF)"),
            ("Ctrl+Alt+T", "Show terminal panel and restart in current dir"),
            ("Ctrl+Alt+N", "Show temp notes panel"),
            ("Ctrl+Alt+I", "Show image panel and load selected image"),
            ("Escape", "Hide lower panel (or collapse selection)"),
        ]),
        ("UTILITIES", [
            ("Ctrl+R", "Open run dialog (execute command)"),
            ("?", "Show this help window"),
            ("Ctrl+W", "Close window"),
        ]),
    ]
    
    text_widget.config(state=tk.NORMAL)
    
    for section_title, shortcuts in shortcuts_data:
        text_widget.insert(tk.END, "\n" + section_title + "\n", "heading")
        text_widget.insert(tk.END, "─" * 70 + "\n", "muted")
        
        for key, description in shortcuts:
            if key:
                text_widget.insert(tk.END, f"  {key:<20} ", "key")
                text_widget.insert(tk.END, description + "\n", "desc")
            else:
                text_widget.insert(tk.END, f"  {description}\n", "muted")
        
        text_widget.insert(tk.END, "\n")
    
    text_widget.config(state=tk.DISABLED)
    
    # Close on Escape or ?
    def _on_key(event):
        if event.keysym in ("Escape", "question"):
            _on_close()
            return "break"
    
    win.bind("<Escape>", _on_key)
    win.bind("<question>", _on_key)
    
    win.lift()
    win.focus_force()
