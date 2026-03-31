# CLAUDE.md — Pyxplorer

Win11-style file explorer for the building industry.  
Stack: Python 3.12 + tkinter/ttk, dark theme, Pillow icons, no external UI deps.

---

## How to run

```bash
# From the repo root (editable install already in place via pyproject.toml)
python -m pyxplorer

# Or via the installed script
pyxplorer
```

Requires Python 3.12+. Pillow is an optional dependency (icons degrade gracefully without it):
```bash
pip install Pillow
```

---

## Project structure

```
pyxplorer/
├── main.py              # Entry point: calls App().run()
├── app.py               # Root Tk window, ttk styling, layout, navigation controller
├── settings.json        # All theme colours + start_dirs (edit this, not settings.py)
├── settings.py          # Loads settings.json, exports THEME: dict and START_DIRS: list
├── state.py             # AppState: current_dir, nav_history, clipboard, selection
├── keybindings.py       # All global keyboard shortcuts (bound on root Tk window)
├── core/
│   ├── longpath.py      # normalize() / to_display() — every OS call goes through here
│   ├── fs.py            # copy_items / move_items (robocopy on Win) / delete / mkdir
│   ├── scanner.py       # Async recursive size scanner with CancelToken
│   └── search.py        # Regex name search used by Ctrl+F dialog
└── ui/
    ├── top_bar.py       # Path entry, 10-item history dropdown, breadcrumbs, Ctrl+R
    ├── left_panel.py    # Lazy-loading directory tree (Nav.Treeview style)
    ├── main_frame.py    # Directory listing: Name / Size / % columns
    ├── status_bar.py    # Braille spinner, item count, selection size
    ├── search_dialog.py # Ctrl+F — debounced regex search with streaming results
    └── icons.py         # Pillow-generated 16×16 folder / file / drive PhotoImages
```

---

## Critical rules — read before touching any file

### 1. Every OS call must go through `normalize()`

```python
# WRONG — silently breaks on paths > 260 chars (common in building projects)
os.scandir(path)

# RIGHT
from core.longpath import normalize
os.scandir(normalize(path))
```

`normalize()` prepends `\\?\` on Windows for paths ≥ 240 chars. It is idempotent.

### 2. Never show `\\?\` in the UI

```python
from core.longpath import to_display
label.config(text=to_display(path))   # strips \\?\ prefix
```

### 3. Never hardcode colours or font sizes

All visual properties live in `settings.json` under `"theme"`.  
Import them via `from .settings import THEME as _T`, e.g. `_T["accent"]`.  
Do not duplicate values across files.

### 4. Copy / move always uses robocopy on Windows

`fs.copy_items()` and `fs.move_items()` delegate to `robocopy` for reliability on network drives and long paths. `shutil` is the non-Windows fallback only. Exit codes 0–7 = success, 8+ = error.

### 5. UI must never block

Size scanning runs in a daemon thread (`core/scanner.py`). Results are pushed onto a `queue.Queue` and consumed by `App._process_queue()` via `root.after(100, ...)`.  
Never call a long-running filesystem operation on the main thread (exception: robocopy paste is currently synchronous — acceptable for MVP).

---

## Key patterns

### Navigation flow (single point of truth)

All navigation goes through `App._navigate(path)`:

```
App._navigate(path)
  ├── cancels the previous CancelToken
  ├── state.navigate_to(path)       → updates current_dir + history
  ├── top_bar.update_path(path)     → entry + breadcrumbs
  ├── main_frame.load_dir(path)     → populates treeview
  ├── left_panel.load_dir(path)     → highlights + expands tree
  └── scanner.scan_items(...)       → starts async size scan
```

Never call `main_frame.load_dir()` or `left_panel.load_dir()` directly — always go through `App._navigate()`.

### Async size scanner

```python
# Start a scan
token = CancelToken()
scanner.scan_items(parent_path, subdir_dirs, token)

# Cancel when navigating away
token.cancel()

# Results arrive as queue messages:
# ("size_result",   item_path, bytes)   — bytes == -1 for network/skipped
# ("scan_complete", parent_path)
```

Network and UNC paths are detected and skipped (size stays `—`).  
Symlink directories are never recursed (avoids infinite loops).

### Left panel — lazy loading

Every inserted node gets a `\x00dummy` child so the expand arrow appears.  
On `<<TreeviewOpen>>`, the dummy is replaced with real subdirectories.  
`selectmode="none"` — clicking navigates but never touches `state.selection`.

### Keyboard focus after left panel click

Single-click on the left panel calls `self.focus_back_cb()` (wired in `app.py` as `main_frame._tree.focus_set`) so arrow keys keep working in the main frame immediately.

### `_guard(fn)` in keybindings

Clipboard shortcuts (`Ctrl+C/X/V`) are wrapped in `_guard()` which silently no-ops when a text entry has focus, preventing conflicts while the user types in the path bar or search dialog.

---

## Settings reference (`settings.json`)

```json
{
  "theme": {
    "bg":              "#202020",   // main panel background
    "bg_dark":         "#161616",   // left panel background
    "bg_entry":        "#2D2D2D",   // text entry background / current highlight
    "accent":          "#60CDFF",   // Win11 blue
    "text":            "#F3F3F3",
    "text_mute":       "#9D9D9D",   // headings, status, dim items
    "border":          "#3A3A3A",
    "row_hover":       "#2A2A2A",
    "row_selected":    "#3D3D3D",
    "status_bg":       "#1C1C1C",
    "font_family":     "Segoe UI",
    "font_size_base":  15,          // main treeview + labels
    "font_size_entry": 16,          // path entry
    "font_size_small": 12,          // headings, status bar
    "row_height":      36,          // main frame row height (px)
    "row_height_nav":  34           // left panel row height (px)
  },
  "start_dirs": [                   // roots shown in left panel (instead of all drives)
    "~",
    "D:\\",
    "R:\\P013926_OTI_CDGX\\"
  ]
}
```

---

## Treeview tags reference

### Main frame (`main_frame.py`)

| Tag | Meaning | Style |
|---|---|---|
| `dir` | Directory | bold |
| `file` | Regular file | normal |
| `empty_dir` | Directory confirmed empty after scan | bold + muted colour |
| `symlink` | Symbolic link | blue tint |
| `denied` | Permission error | dark red |
| `more` | "Load more" sentinel (500+ items) | accent colour |

### Left panel (`left_panel.py`)

| Tag | Meaning |
|---|---|
| `dir` | Normal directory node |
| `drive` | Root / start-dir node (bold) |
| `current` | Currently navigated path (accent + highlight bg) |
| `empty` | Empty directory (muted) |
| `denied` | Permission error |

---

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+C` | Copy selected items to file clipboard |
| `Ctrl+X` | Cut selected items |
| `Ctrl+V` | Paste into current directory |
| `Ctrl+Shift+C` | Copy display path string to system clipboard |
| `Ctrl+Shift+N` | Create new folder (prompt dialog) |
| `Ctrl+R` | Open run dialog (Win+R style) |
| `Ctrl+F` | Open regex search dialog |
| `Ctrl+Alt+T` | Open terminal in current directory |
| `Delete` | Permanently delete selected items (confirm dialog) |
| `←` / `Backspace` | Go up one level |
| `→` / `Enter` | Open selected directory |
| `↑` / `↓` | Move selection (wraps around) |
| `Ctrl+↑` | Jump to first item |
| `Ctrl+↓` | Jump to last item |

---

## Phase completion status

| Phase | Description | Status |
|---|---|---|
| 0 | Long-path support (`longpath.py`) | ✓ |
| 1 | Project scaffold | ✓ |
| 2 | Top bar: path entry, history, breadcrumbs | ✓ |
| 3 | Main frame: directory listing | ✓ |
| 4 | Left panel: lazy-loading tree | ✓ |
| 5 | Async size scanner + status bar | ✓ |
| 6 | Keyboard shortcuts + file ops | ✓ |
| 7 | Ctrl+F regex search dialog | ✓ |
| 8 | Ctrl+Alt+T terminal opener | ✓ |
| 9 | Win11 styling: icons, empty-dir dimming | ✓ |
| 10 | Edge cases: symlinks, network drives, deleted dirs | ✓ |
| 11 | MVP delivery checklist | ✓ |
