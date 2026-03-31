# Pyxplorer — Claude Code Agent Plan
> Win11-style File Explorer for the Building Industry · Python + tkinter

---

## Stack

| Layer | Choice | Rationale |
|---|---|---|
| UI framework | `tkinter` + `ttk` | Zero external deps, ships with Python, sufficient for Win11 styling |
| Async / threading | `concurrent.futures.ThreadPoolExecutor` + `queue.Queue` | Non-blocking size scans fed back via `root.after()` |
| File ops | `os`, `shutil`, `pathlib`, `re` | Standard lib, no extra install |
| Long-path support | `pathlib` + `\\?\` prefix layer | Bypasses Win11 MAX_PATH=260 limit transparently |
| Packaging | `pyinstaller` | Single `.exe` for field deployment |

---

## Project Structure

```
pyxplorer/
├── main.py              # Entry point
├── app.py               # App class, root window, global keybindings
├── ui/
│   ├── top_bar.py       # Path entry, history dropdown, breadcrumbs
│   ├── left_panel.py    # Expandable tree (TreeSize style)
│   ├── main_frame.py    # Directory listing with size %
│   └── status_bar.py    # Weight display, scanning indicator
├── core/
│   ├── fs.py            # Filesystem ops: list, copy, cut, paste, mkdir
│   ├── scanner.py       # Async recursive size scanner
│   ├── search.py        # Regex search across file/dir names
│   └── longpath.py      # Long-path normalization utilities
├── state.py             # Clipboard state, selection state, nav history
└── keybindings.py       # All keyboard shortcuts wired to actions
```

---

## Phase 0 — Long Path Support (Windows MAX_PATH Fix)

> **Do this before writing any other file I/O code. Every path operation in the app must go through `longpath.py`.**

### The problem

Windows historically enforces `MAX_PATH = 260` characters. In building industry projects, deeply nested document hierarchies (project > lot > building > floor > apartment > trade > revision > …) routinely exceed this. Python's `os` module on Windows will silently fail or raise `FileNotFoundError` on paths longer than 260 characters unless the extended-path prefix is applied.

### Registry fix (one-time, requires admin)

Enable long paths at the OS level via Group Policy or registry. The app should attempt this on first launch and prompt the user if elevation is needed:

```python
# core/longpath.py

import sys
import os
import pathlib

WIN_MAX_PATH = 260
UNC_PREFIX = "\\\\?\\"  # Extended-length path prefix

def normalize(path: str | pathlib.Path) -> str:
    """
    Return a path string safe for all Win32 API calls.
    - On Windows: prepend \\?\ for absolute paths longer than 240 chars (safety margin).
    - On other OS: return as-is.
    Idempotent: safe to call on already-prefixed paths.
    """
    p = str(path)
    if sys.platform != "win32":
        return p
    if p.startswith(UNC_PREFIX):
        return p
    abs_p = os.path.abspath(p)
    if len(abs_p) >= WIN_MAX_PATH - 20:   # 20-char safety margin
        return UNC_PREFIX + abs_p
    return abs_p

def to_display(path: str) -> str:
    """Strip \\?\ prefix for display in UI — users should never see it."""
    if path.startswith(UNC_PREFIX):
        return path[len(UNC_PREFIX):]
    return path

def enable_longpath_registry():
    """
    Attempt to set HKLM LongPathsEnabled = 1.
    Requires admin. Returns True on success, False if insufficient privileges.
    """
    if sys.platform != "win32":
        return True
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
            0, winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, "LongPathsEnabled", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(key)
        return True
    except PermissionError:
        return False
```

### Python-level fix

Python 3.6+ on Windows respects the `\\?\` prefix. Wrap **every** `open()`, `os.stat()`, `os.scandir()`, `shutil.copy2()`, `os.makedirs()`, `os.rename()` call with `normalize()`:

```python
# BAD — will fail on paths > 260 chars
with open(path, "r") as f: ...

# GOOD
from core.longpath import normalize
with open(normalize(path), "r") as f: ...
```

Use `pathlib.Path` for path arithmetic (joining, parent, stem) and convert to string via `normalize()` only at the point of an OS call.

### Top bar display

The top bar must always call `to_display()` before showing a path to the user. The `\\?\` prefix is an implementation detail and must never appear in the UI.

### Test cases to verify

- Navigate to a directory at depth 30+ (e.g. `C:\a\b\c\…\z` totalling > 300 chars)
- Copy/paste a file inside such a path
- Regex search within such a path
- Create a new folder inside such a path

---

## Phase 1 — Project Scaffold

**Agent instruction:** Create the full file structure above with empty classes and a runnable `main.py` that opens a blank 3-panel window (top bar, left panel, main frame, status bar). Apply the long-path normalization from Phase 0 to `fs.py` from the start.

```python
# main.py
from app import App

if __name__ == "__main__":
    app = App()
    app.run()
```

The window must be resizable, start at 1200×700, and have the correct Win11 proportions: left panel ~220px wide, status bar ~28px tall, top bar ~56px tall.

---

## Phase 2 — Top Bar

- A `ttk.Entry` that is always copy-pastable. Shows the current absolute path via `to_display()`.
- On `<Return>`: validate the typed path (with `normalize()`), navigate if valid, shake the widget red if not.
- **Last 10 paths history**: stored in `state.py` as a `collections.deque(maxlen=10)`. Shown in a `tk.Listbox` dropdown below the entry on focus or `Alt+Down`. Clicking a history entry navigates to it.
- Breadcrumb row: clickable `ttk.Button` segments for each path component. Clicking any segment navigates there.
- **Ctrl+R**: opens a `Toplevel` dialog (Win+R style) with a single entry. On submit: try `os.startfile(normalize(value))` then fall back to `subprocess.run([value], shell=True)`.
- All path values stored internally with `normalize()`, displayed with `to_display()`.

---

## Phase 3 — Left Panel (Expandable Tree)

- `ttk.Treeview` in a `Frame` with a vertical scrollbar.
- On expand (`<<TreeviewOpen>>`): lazy-load direct children using `os.scandir(normalize(path))`, insert only directories.
- Insert a dummy child `"…"` when first inserting a node (so the expand arrow appears); replace it with real children on expand.
- Size annotation: after expanding a node, kick off a background size scan (Phase 5) and update the node's text with the human-readable size once computed.
- **No selection**: clicking a node navigates the main frame but does not add it to the clipboard selection. The `selectmode` of the Treeview should be set to `"none"`.
- Empty directories: detected async, shown with lightened foreground color (`foreground="#aaa"`).

---

## Phase 4 — Main Frame

- `ttk.Treeview` with columns: `Name | Size | % of parent dir`.
- Column headers are clickable for sorting (by name, size, %).

### Navigation

| Action | Behavior |
|---|---|
| Single click on dir | `navigate_to(normalize(path))` |
| Single click on file | Select only (no open) |
| Left Arrow `←` | Go up one level (`Path(current).parent`) |
| Right Arrow `→` | Open selected directory |
| Up/Down `↑↓` | Move selection without navigating |
| `Backspace` | Go up one level |
| `Shift+Click` | Extend selection (multi-select) |
| `Ctrl+Click` | Toggle item in selection |

### Visual cues

- **Empty directories**: `foreground` tag set to `#b0b0b0` (detected async — default to normal color, update when scan confirms empty).
- **Size % column**: computed once the async scan of the current directory finishes. Shows `—` while scanning.
- **Selected weight**: shown live in the status bar as items are selected/deselected.

---

## Phase 5 — Async Weight Scanner

This is the most critical non-freezing component. The UI must never block.

```python
# core/scanner.py

import os
import queue
import threading
from pathlib import Path
from core.longpath import normalize

class CancelToken:
    def __init__(self):
        self.cancelled = False
    def cancel(self):
        self.cancelled = True

class SizeScanner:
    def __init__(self, result_queue: queue.Queue):
        self.q = result_queue
        self._current_token: CancelToken | None = None
        self._lock = threading.Lock()

    def scan(self, path: str, token: CancelToken):
        """Submit a scan job. Any previous job for same path is superseded by the token."""
        with self._lock:
            self._current_token = token
        t = threading.Thread(target=self._worker, args=(path, token), daemon=True)
        t.start()

    def _worker(self, path: str, token: CancelToken):
        total = 0
        try:
            for entry in os.scandir(normalize(path)):
                if token.cancelled:
                    return
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat().st_size
                    elif entry.is_dir(follow_symlinks=False):
                        total += self._dir_size(entry.path, token)
                except (PermissionError, OSError):
                    pass
        except (PermissionError, OSError):
            pass
        if not token.cancelled:
            self.q.put(("size_result", path, total))

    def _dir_size(self, path: str, token: CancelToken) -> int:
        total = 0
        try:
            for entry in os.scandir(normalize(path)):
                if token.cancelled:
                    return 0
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat().st_size
                    elif entry.is_dir(follow_symlinks=False):
                        total += self._dir_size(entry.path, token)
                except (PermissionError, OSError):
                    pass
        except (PermissionError, OSError):
            pass
        return total
```

The UI processes the result queue via `root.after(100, self._process_queue)`:

```python
def _process_queue(self):
    while not self.q.empty():
        msg = self.q.get_nowait()
        if msg[0] == "size_result":
            _, path, size = msg
            self._update_size_display(path, size)
    self.root.after(100, self._process_queue)
```

**Cancel on navigate**: when the user navigates to a new directory, call `token.cancel()` on the previous scan before starting a new one.

**Status bar states:**
- `Scanning…` with an animated spinner while any worker is running.
- `3 items selected — 1.24 GB` when selection is non-empty and scan is done.
- `N items — X GB total` when no selection.

---

## Phase 6 — Keyboard Shortcuts

All shortcuts are bound in `keybindings.py` on the root `Tk` window so they work regardless of which widget has focus.

| Shortcut | Action |
|---|---|
| `Ctrl+C` | Copy selected paths → `state.clipboard` (mode=copy) |
| `Ctrl+X` | Cut selected paths → `state.clipboard` (mode=cut) |
| `Ctrl+V` | Paste into current dir: cut=`shutil.move`, copy=`shutil.copy2` (both via `normalize()`) |
| `Ctrl+Shift+C` | Copy **display path string** to system clipboard (`root.clipboard_clear()` + `root.clipboard_append(to_display(path))`) |
| `Ctrl+Shift+N` | Open a small `Toplevel` prompt → create new folder in current dir via `os.makedirs(normalize(new_path), exist_ok=True)` |
| `Ctrl+R` | Open run dialog (see Phase 2) |
| `Ctrl+F` | Open regex search dialog (see Phase 7) |
| `Ctrl+Alt+T` | Open terminal in current directory (see Phase 8) |
| `←` | Go up one level |
| `→` | Open selected directory |
| `↑ / ↓` | Move selection |
| `Backspace` | Go up one level |

---

## Phase 7 — Ctrl+F Regex Search

- Opens a `Toplevel` with a single `Entry` (regex pattern input) + a `ttk.Treeview` for results (columns: name, relative path, type).
- **Debounced search**: 300ms after the last keystroke, runs `search.py` in a background thread (using the same `CancelToken` pattern as Phase 5).
- **Invalid regex**: shows a red border on the entry and an error label; does not launch a worker.

```python
# core/search.py

import os
import re
import queue
from core.longpath import normalize

def search_names(root_dir: str, pattern: str, result_queue: queue.Queue, token):
    """Walk root_dir, match file/dir names against pattern, push results to queue."""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        result_queue.put(("search_error", str(e)))
        return

    for dirpath, dirnames, filenames in os.walk(normalize(root_dir)):
        if token.cancelled:
            return
        for name in dirnames + filenames:
            if token.cancelled:
                return
            if rx.search(name):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root_dir)
                result_queue.put(("search_result", name, rel, "dir" if name in dirnames else "file"))

    result_queue.put(("search_done",))
```

- Results appear incrementally as they stream in (process queue via `root.after()`).
- Double-clicking a result navigates the main frame to that directory (or to the file's parent if it's a file).
- The search scope is always the **current directory** displayed in the main frame, recursively.

---

## Phase 8 — Ctrl+Alt+T : Open Terminal in Current Directory

Open the system terminal (Windows Terminal, PowerShell, or CMD fallback) rooted at the currently displayed directory.

```python
# keybindings.py — terminal handler

import subprocess
import sys
import os
from core.longpath import normalize, to_display

def open_terminal(current_dir: str):
    """
    Open a terminal emulator at current_dir.
    Priority: Windows Terminal (wt.exe) → PowerShell → cmd.exe
    Falls back gracefully if the preferred shell is not installed.
    """
    display_dir = to_display(current_dir)  # never pass \\?\ to shell

    if sys.platform == "win32":
        # Try Windows Terminal first
        try:
            subprocess.Popen(
                ["wt.exe", "-d", display_dir],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            return
        except FileNotFoundError:
            pass
        # Fall back to PowerShell 7+
        try:
            subprocess.Popen(
                ["pwsh.exe", "-NoExit", "-Command", f"Set-Location '{display_dir}'"],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            return
        except FileNotFoundError:
            pass
        # Fall back to PowerShell 5 (built-in)
        try:
            subprocess.Popen(
                ["powershell.exe", "-NoExit", "-Command", f"Set-Location '{display_dir}'"],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            return
        except FileNotFoundError:
            pass
        # Last resort: cmd.exe
        subprocess.Popen(
            ["cmd.exe", "/K", f"cd /d \"{display_dir}\""],
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Terminal", display_dir])
    else:
        # Linux: try common terminals in order
        for term in ["gnome-terminal", "konsole", "xterm"]:
            try:
                subprocess.Popen([term, "--working-directory", display_dir])
                return
            except FileNotFoundError:
                continue
```

**Key points:**
- **Never pass `\\?\` prefixed paths to the shell** — use `to_display()` before handing the path to `subprocess`. Shells do not understand the extended-path prefix.
- The function is bound to `Ctrl+Alt+T` in `keybindings.py` and receives `state.current_dir` at the moment of the key press.
- The new terminal process is detached (`CREATE_NEW_CONSOLE`) so closing it does not affect the explorer.

---

## Phase 9 — Win11 Visual Styling

- Set `ttk.Style` base theme to `"clam"` (cross-platform) or `"vista"` (Windows only).
- Override colors to approximate Win11:

```python
style = ttk.Style()
style.theme_use("clam")

BG        = "#F3F3F3"   # panel background
BG_DARK   = "#EBEBEB"   # sidebar background
ACCENT    = "#005FB8"   # Win11 blue
TEXT      = "#1A1A1A"
TEXT_MUTE = "#6B6B6B"
BORDER    = "#E5E5E5"
ROW_H     = "#E5F1FB"   # hover row
ROW_SEL   = "#CCE4F7"   # selected row

style.configure("Treeview",
    background=BG, foreground=TEXT,
    fieldbackground=BG, borderwidth=0,
    rowheight=28, font=("Segoe UI", 9))
style.map("Treeview",
    background=[("selected", ROW_SEL)],
    foreground=[("selected", TEXT)])
style.configure("Treeview.Heading",
    background=BG, foreground=TEXT_MUTE,
    font=("Segoe UI", 8), relief="flat")
```

- **Icons**: use `Pillow` (`pip install Pillow`) to load small 16×16 PNG icons from `assets/` (folder, file, drive types).
- **Font**: `Segoe UI, 9` on Windows; auto-detect with `tkfont.nametofont("TkDefaultFont")` on other platforms.
- **Empty dir rows**: tag `"empty"` with `foreground="#C0C0C0"`. Applied after async scan confirms a directory is empty.

---

## Phase 10 — Edge Cases & Robustness

| Scenario | Handling |
|---|---|
| Permission denied | Catch `PermissionError`, show dimmed `🔒 Access denied` entry |
| Symlinks | Follow for display; skip recursive size scan to avoid loops (`is_symlink()` check) |
| Very large directories (500+ items) | Show first 500 entries + `Load more…` button |
| Cancel on navigate | Call `token.cancel()` before launching new scan |
| Network / UNC drives | Detect with `os.path.ismount()`, skip recursive scan, show `—` for size |
| Paths > 260 chars | Handled transparently by Phase 0 (`longpath.py`) |
| Paths exceeding NTFS limit (32,767 chars) | Clamp and warn in status bar; do not crash |
| Drag & drop (future) | Stub in `fs.py` as `move_to(src, dst)` — same as cut+paste |
| Renamed/deleted dir while open | `FileNotFoundError` on refresh → navigate up one level automatically |
| Concurrent paste operations | Queue paste ops in `state.py`; show progress in status bar |

---

## Phase 11 — MVP Delivery Checklist

The agent must verify every item before considering the MVP complete.

**Layout & navigation**
- [ ] Window opens, resizable, Win11-proportioned (3-panel: tree 220px, main frame fills rest)
- [ ] Can navigate by single-clicking directories in the main frame
- [ ] Left tree expands lazily, shows subdirectories only
- [ ] `←` goes up one level; `→` opens selected dir; `↑↓` move selection
- [ ] `Backspace` goes up one level
- [ ] Shift+click and Ctrl+click multi-select work

**Top bar**
- [ ] Shows current display path (no `\\?\` prefix)
- [ ] Path is editable and navigates on Enter
- [ ] History dropdown shows last 10 navigated paths
- [ ] Breadcrumb segments are clickable

**Shortcuts**
- [ ] `Ctrl+C` / `Ctrl+X` / `Ctrl+V` copy/move files without freezing UI
- [ ] `Ctrl+Shift+C` copies display path string to system clipboard
- [ ] `Ctrl+Shift+N` creates a new folder with a name prompt
- [ ] `Ctrl+R` opens a run dialog that executes commands
- [ ] `Ctrl+F` opens regex search dialog with live incremental results
- [ ] `Ctrl+Alt+T` opens a terminal in the current directory

**Async weight & display**
- [ ] Size scan runs in background — UI never freezes during scan
- [ ] Status bar shows `Scanning…` then transitions to size summary
- [ ] Empty directories are visually dimmed
- [ ] Each item shows its `%` of the current directory total
- [ ] Navigating away cancels the in-progress scan

**Long path support**
- [ ] Can navigate to a path longer than 260 characters
- [ ] Copy/paste works on long paths
- [ ] Regex search works inside long-path trees
- [ ] New folder creation works in long-path trees
- [ ] Top bar always displays the path without `\\?\`

---

## Suggested Agent Prompt Sequence

Start with a single session per phase to keep context manageable.

**Session 1 — Scaffold + Long Path**
> "Create the `pyxplorer/` project structure as specified. Implement `core/longpath.py` with `normalize()`, `to_display()`, and `enable_longpath_registry()`. Then create `main.py` and `app.py` that open a blank resizable window with placeholder panels. Do not implement any real functionality yet."

**Session 2 — Top bar + navigation state**
> "Implement `ui/top_bar.py` and `state.py`. The top bar shows the current path (via `to_display()`), has an editable entry that navigates on Enter, and a dropdown of the last 10 paths. Wire `state.current_dir` so navigating updates it."

**Session 3 — Main frame**
> "Implement `ui/main_frame.py`. List the current directory using `os.scandir(normalize(path))`. Support single-click navigation, ←/→/↑/↓ keyboard nav, Shift+click multi-select. Show Name, Size, % columns. Leave Size and % as `—` for now."

**Session 4 — Left panel**
> "Implement `ui/left_panel.py` as a lazy-loading Treeview. On expand, load only subdirectories. No selection. Uses `normalize()` for all OS calls."

**Session 5 — Async scanner + status bar**
> "Implement `core/scanner.py` (with `CancelToken`) and `ui/status_bar.py`. Wire the scanner so navigating to a new directory triggers a scan. Update Size and % in the main frame and the tree nodes in the left panel. Show `Scanning…` and dismiss when done. Dim empty dirs."

**Session 6 — Keyboard shortcuts + file ops**
> "Implement `keybindings.py` and `core/fs.py`. Wire Ctrl+C/X/V (copy/move via `shutil`), Ctrl+Shift+C, Ctrl+Shift+N, Ctrl+R dialog, Ctrl+F search dialog (Phase 7), Ctrl+Alt+T terminal opener (Phase 8). All file ops must use `normalize()`. Terminal opener must use `to_display()` before passing to subprocess."

**Session 7 — Styling + polish**
> "Apply Win11 styling via `ttk.Style` as specified in Phase 9. Add folder/file icons from `assets/`. Set row height to 28px. Verify all edge cases from Phase 10 are handled."
