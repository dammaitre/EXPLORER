# CLAUDE.md — Pyxplorer

Win11-style file explorer for the building industry.  
Stack: Python 3.12 + tkinter/ttk, dark theme, Pillow icons, no external UI deps.

Cross-platform note: Windows is the most tuned target, but Linux/macOS fallbacks are implemented for storage paths, terminal backend, and file opening.

---

## How to run

```bash
# From the repo root (editable install already in place via pyproject.toml)
python -m pyxplorer               # opens at first start_dirs entry
python -m pyxplorer "R:\Projects" # opens at specified path (also pinned in left panel)

# Or via the installed script
pyxplorer
pyxplorer "R:\Projects"
```

Requires Python 3.12+. Pillow is an optional dependency (icons degrade gracefully without it):
```bash
pip install Pillow
```

---

## Project structure

```
pyxplorer/
├── main.py              # Entry point: argparse PATH argument, calls App(start_path).run()
├── app.py               # Root Tk window, ttk styling, layout, navigation controller
├── settings.json        # All theme colours, start_dirs, ext_skipped (edit this, not settings.py)
├── settings.py          # Loads settings.json, exports THEME / START_DIRS / SCAN_SKIP_DIRS / EXT_SKIPPED
├── state.py             # AppState: current_dir, nav_history, clipboard, selection
├── keybindings.py       # Global shortcuts (clipboard, lower panel, heuristics, navigation)
├── core/
│   ├── heuristics.py    # Heuristic script discovery + execution (python script.py PATH)
│   ├── longpath.py      # normalize() / to_display() — every OS call goes through here
│   ├── fs.py            # copy_items / move_items (robocopy on Win, shutil fallback) / delete / mkdir
│   ├── scanner.py       # Async recursive size scanner with CancelToken
│   ├── search.py        # Regex name search used by Ctrl+F dialog
│   └── shared_clipboard.py # Cross-instance clipboard file in per-user app data dir
└── ui/
    ├── top_bar.py       # Path entry, 10-item history dropdown, breadcrumbs, Ctrl+R
    ├── left_panel.py    # Lazy-loading directory tree (Nav.Treeview style)
  ├── main_frame.py    # Directory listing: Name / [Heuristic] / Size / % columns
  ├── lower_panel.py   # VSCode-style lower pane coordinator (P/T/N/I)
  ├── pdf_viewer.py    # PDF viewer tab (async page render, zoom, text selection/copy)
  ├── image_viewer.py  # Image viewer tab (async load, 1024px cap, zoom/pan, Ctrl+C image copy)
  ├── embedded_terminal.py # Embedded terminal tab (PowerShell on Win, shell fallback elsewhere)
  ├── temp_notepad.py  # Temp notes tab (per-user app data dir)
  ├── heuristics_window.py # Ctrl+H script picker window
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

### 4b. Per-user app data location is platform-dependent

Pyxplorer persistence files (clipboard, heuristics scripts, starred entries, temp notes) are under:
- Windows: `%LOCALAPPDATA%\Pyxplorer`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/Pyxplorer`
- macOS: `~/Library/Application Support/Pyxplorer`

### 5. UI must never block

Size scanning runs in a daemon thread (`core/scanner.py`). Results are pushed onto a `queue.Queue` and consumed by `App._process_queue()` via `root.after(100, ...)`.  
Long operations (paste, heuristics execution) must stay off the main thread and report progress back through status updates.

### 6. Extension filtering applies everywhere

`EXT_SKIPPED` (from `settings.py`) must be checked in both the scanner (`_dir_size`) and the main frame listing (`load_dir`). Files matching skipped extensions must be invisible in the UI and excluded from all size computations.

### 7. `scan_skip_dirs` controls where scans can start

`SCAN_SKIP_DIRS` (from `settings.py`) is interpreted with inverse/prefix semantics:
- If current path equals a configured entry → scan is skipped.
- If current path is a parent of a configured entry → scan is skipped.
- If current path is a child of a configured entry → scan is allowed.

Example: with `A\B` configured:
- `A\B` skipped
- `A\` skipped
- `A\B\C` scanned

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

### Startup directory

`AppState.__init__` resolves the starting directory in this order:
1. `start_path` argument (from `pyxplorer PATH` CLI)
2. First valid entry in `START_DIRS` (from `settings.json`)
3. `~` (fallback)

### Async size scanner

```python
# Start a scan
token = CancelToken()
scanner.scan_items(parent_path, subdir_dirs, token)

# Cancel when navigating away
token.cancel()

# Results arrive as queue messages:
# ("size_result",   item_path, bytes)   — bytes == -1 for UNC/skipped
# ("scan_complete", parent_path)
```

Only raw UNC paths (`\\server\share\...`) are skipped. Mapped network drives (`R:\`, `S:\` etc.) are scanned normally.  
`is_dir(follow_symlinks=False)` is used as the recursion gate — this correctly includes NTFS junction points and excludes NTFS symlinks-to-dirs.

### Left panel — lazy loading

Every inserted node gets a `\x00dummy` child so the expand arrow appears.  
On `<<TreeviewOpen>>`, the dummy is replaced with real subdirectories.  
`selectmode="none"` — left-panel clicks never touch `state.selection`.

Middle mouse click on a directory opens a new Pyxplorer window at that path.

### Keyboard focus after left panel click

Single-click on the left panel calls `self.focus_back_cb()` (wired in `app.py` as `main_frame._tree.focus_set`) so arrow keys keep working in the main frame immediately.

### `_guard(fn)` in keybindings

Clipboard/file-operation shortcuts are wrapped in `_guard()` to avoid conflicts when text widgets are focused (`Entry`/`Text` contexts such as path bar, terminal, notes).

### Shared clipboard

`Ctrl+C` / `Ctrl+X` write clipboard state to `Pyxplorer/clipboard.json` in the per-user app data directory so copy/cut/paste works across multiple Pyxplorer windows.

`Ctrl+V` resolves clipboard from this shared file first, then local in-memory fallback.

### Lower panel semantics (`Ctrl+Alt+P/T/N/I`)

- `P` (PDF): load selected PDF into viewer
- `I` (Image): load selected image into viewer (thumbnail capped to 1024 px on longest side)
- `T` (Terminal): restart embedded terminal at current directory (`powershell.exe` on Windows, `$SHELL`/`bash` elsewhere)
- `N` (Notes): reset `Pyxplorer/temp.txt` in app data directory
- `Escape`: hide lower panel only (no kill)
- Kill actions run on app shutdown (`Ctrl+W` / window close)

### Heuristics (`Ctrl+H`)

- Opens/toggles a dedicated window listing scripts in `Pyxplorer/scripts` under the per-user app data directory
- Runs selected script for each current-directory child as `python script.py PATH`
- Writes output to dynamic `Heuristic` column in main frame
- Closing heuristics window removes the dynamic column

### Go-up selection memory

`main_frame._go_up()` stores `_pending_select = current_path` before navigating up. `_render_rows()` checks this field first and pre-selects the child that was previously the current directory, so the user always lands back where they came from.

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
    "terminal_text":   "#00FF41",   // embedded terminal foreground
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
  "start_dirs": [                   // roots shown in left panel; first entry = startup dir
    "R:\\P013926_OTI_CDGX\\",
    "~",
    "D:\\"
  ],
  "scan_skip_dirs": [              // scan starts are blocked on these paths and their parents
    "C:\\Windows\\"
  ],
  "ext_skipped": [".db"]           // extensions hidden from listing AND excluded from sizes
}
```

`ext_skipped` normalisation rules (handled in `settings.py`):
- Case-insensitive: `".TMP"` → `".tmp"`
- Leading dot optional: `"tmp"` → `".tmp"`
- Duplicates removed automatically

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

### Main frame

| Shortcut | Action |
|---|---|
| `Ctrl+C` | Copy selected items to file clipboard |
| `Ctrl+X` | Cut selected items |
| `Ctrl+V` | Async paste into current directory (shared clipboard aware) |
| `Ctrl+Shift+C` | Copy display path string to system clipboard |
| `Ctrl+Shift+N` | Copy selected item name(s) to system clipboard |
| `Ctrl+Shift+X` | Create new folder (prompt dialog) |
| `Ctrl+N` | Open current directory in a new Pyxplorer window |
| `Ctrl+R` | Open run dialog (Win+R style) |
| `Ctrl+F` | Open regex search dialog |
| `Ctrl+Alt+P` | Show PDF tab and load selected PDF |
| `Ctrl+Alt+I` | Show Image tab and load selected image |
| `Ctrl+Alt+T` | Show Terminal tab and reload PowerShell |
| `Ctrl+Alt+N` | Show Notes tab and reset temp file |
| `Ctrl+H` | Toggle heuristics window |
| `Escape` | Hide lower panel |
| `Delete` | Permanently delete selected items (confirm dialog) |
| `←` / `Backspace` | Go up one level (re-selects the dir you came from) |
| `→` | Open selected directory |
| `Enter` | Open selected directory or file (OS default app) |
| `↑` / `↓` | Move selection (wraps around) |
| `Ctrl+↑` | Jump to first item |
| `Ctrl+↓` | Jump to last item |
| Left click on file | Open with OS default app |
| Left click on dir | Navigate into directory |
| Middle click on dir | Open new Pyxplorer window at that directory |

Windows folder shortcuts (`.lnk`) that target directories are treated like directories for `→`, `Enter`, click-open, and middle-click new-window.

### Left panel

| Interaction | Action |
|---|---|
| Double click on dir | Navigate main frame to that directory |
| Middle click on dir | Open new Pyxplorer window at that directory |

### Search dialog (`Ctrl+F`)

Results are displayed in a table with columns:
- **Name**: The file or folder name (not the full path)
- **Relative path**: The parent directory path (relative to search root)
- **Type**: "file" or "dir"

| Interaction | Action |
|---|---|
| Left click on file result | Open file with OS default app |
| Left click on dir result | Navigate main frame into that directory |
| Double-click / Enter | Same as left click |
| Middle click on any result | Open the result's parent directory in a new Pyxplorer window |
| `Ctrl+Shift+C` | Copy absolute path of focused result to system clipboard |
| `Ctrl+Shift+N` | Copy focused result name to system clipboard |

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
| 8 | Lower panel (`P/T/N/I`) + lifecycle semantics | ✓ |
| 9 | Embedded terminal + temp notes + PDF + image viewer | ✓ |
| 10 | Shared clipboard + async paste + robocopy conflict fallback | ✓ |
| 11 | Heuristics window + dynamic result column | ✓ |
