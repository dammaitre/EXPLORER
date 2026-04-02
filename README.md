# EXPLORER

Win11's file explorer copy but damien-friendly

`EXPLORER` is a desktop file explorer prototype built with `tkinter` and `ttk`. It keeps familiar Explorer flows while adding keyboard-first navigation, background folder-size scanning, and a VS Code-style lower panel for PDF, terminal, and temp notes workflows.

## Current state

- **Repository name:** `EXPLORER`
- **Python package:** `pyxplorer`
- **Published project name:** `pyxplorer`
- **GUI entry point:** `pyxplorer`
- **Direct module entry point:** `python -m pyxplorer`

The app is currently organized as a desktop GUI package with a main application shell, a small core utility layer, and dedicated UI components.

## Features implemented

- **Top bar navigation:** editable path entry, breadcrumb navigation, and recent path history popup.
- **Left navigation panel:** lazy-loaded directory tree with current-path highlighting.
- **Main file view:** directory and file listing with sortable `Name`, `Size`, and `%` columns, plus dynamic heuristic column support.
- **Async folder size scanning:** subdirectory sizes are scanned in the background and reflected in the UI when ready.
- **Status bar feedback:** spinner during scans plus operation messages (scan, paste, heuristics, lower-panel actions).
- **Lower panel (`P/T/N`):** resizable bottom panel with:
	- `P`: lightweight PDF viewer (async page load, zoom, text copy)
	- `T`: embedded terminal (`PowerShell` on Windows, `$SHELL`/`bash` fallback on Linux/macOS)
	- `N`: temp UTF-8 notepad backed by a per-user data file (`Pyxplorer/temp.txt`)
- **Shared file clipboard across instances:** `Ctrl+C/X/V` uses a per-user data file (`Pyxplorer/clipboard.json`) for cross-window copy/cut/paste.
- **Cross-instance drag & drop (Phase 2):** dropping files/folders onto the main list is supported, and dragging selected items from one `pyxplorer` window to another is enabled when `tkinterdnd2` is available.
- **Async paste:** copy/move operations are non-blocking and report progress in the status bar (`robocopy` on Windows, `shutil` fallback elsewhere).
- **Heuristics window (`Ctrl+H`):** runs scripts from the per-user scripts directory (`Pyxplorer/scripts`) over current directory children and displays results in a dynamic column.
- **New-window workflows:** middle-click directories in left/main panels to open new windows, plus `Ctrl+N` for opening current directory in another window.
- **Windows long-path support:** internal path normalization plus an attempt to enable the Windows long-path registry flag (Windows only).
- **Theme configuration:** colors, fonts, row heights, and optional start directories are loaded from `pyxplorer/settings.json`.

## Project layout

```text
EXPLORER/
├── pyproject.toml
├── README.md
├── pyxplorer/
│   ├── __init__.py
│   ├── __main__.py
│   ├── app.py
│   ├── keybindings.py
│   ├── main.py
│   ├── settings.json
│   ├── settings.py
│   ├── state.py
│   ├── core/
│   │   ├── heuristics.py
│   │   ├── fs.py
│   │   ├── longpath.py
│   │   ├── scanner.py
│   │   ├── search.py
│   │   └── shared_clipboard.py
│   └── ui/
│       ├── embedded_terminal.py
│       ├── heuristics_window.py
│       ├── lower_panel.py
│       ├── left_panel.py
│       ├── main_frame.py
│       ├── pdf_viewer.py
│       ├── status_bar.py
│       ├── temp_notepad.py
│       └── top_bar.py
└── pyxplorer_agent_plan.md
```

## Module overview

### `pyxplorer/app.py`

Creates the root `Tk` window, applies the Win11-style theme, wires the top bar, left panel, main frame, and status bar together, and coordinates background size scanning.

### `pyxplorer/main.py` and `pyxplorer/__main__.py`

Expose the app startup path used by both the console script and `python -m pyxplorer`.

### `pyxplorer/state.py`

Stores navigation history, current directory, clipboard state, and current selection.

### `pyxplorer/keybindings.py`

Defines app-wide shortcuts and file operations: async paste with status updates, shared clipboard, lower-panel toggles, heuristics window toggle, and navigation/new-window flows.

### `pyxplorer/settings.py` and `pyxplorer/settings.json`

Load user-adjustable theme and startup directory settings.

### `pyxplorer/core/`

- `fs.py`: filesystem operations and display helpers.
- `heuristics.py`: script discovery and `python script.py PATH` execution helpers.
- `longpath.py`: Windows long-path normalization and registry toggle helper.
- `scanner.py`: cancellable background directory-size scanning.
- `search.py`: regex name search backend stub for future UI integration.
- `shared_clipboard.py`: cross-instance clipboard persistence in a per-user data directory.

### `pyxplorer/ui/`

- `top_bar.py`: path entry, breadcrumbs, history dropdown, and run dialog.
- `left_panel.py`: lazy-loading tree navigation.
- `main_frame.py`: main directory listing, sorting, selection tracking, incremental loading, and dynamic heuristic column.
- `lower_panel.py`: bottom panel coordinator (`P/T/N`).
- `pdf_viewer.py`: async PDF rendering with zoom and text selection/copy.
- `embedded_terminal.py`: embedded terminal view (PowerShell/PTY backend on Windows, ptyprocess shell fallback on Linux/macOS).
- `temp_notepad.py`: temporary text editor tied to a per-user data file.
- `heuristics_window.py`: script selector window for `Ctrl+H` workflow.
- `status_bar.py`: scan progress and selection summary.

## Installation

Editable install from the repository root:

```powershell
python -m pip install -e .
```

If the `pyxplorer` command is not recognized after installation, add your user scripts directory to `PATH`. On the machine used during this repo review, the install location was:

```text
C:\Users\RK6721\AppData\Roaming\Python\Python312\Scripts
```

## Running the app

From the repository root, you can use either the package entry point or the module form:

```powershell
pyxplorer
```

```powershell
python -m pyxplorer
```

## Keyboard shortcuts

- `Ctrl+C`: copy selected items into the app clipboard.
- `Ctrl+X`: cut selected items into the app clipboard.
- `Ctrl+V`: async paste into current directory (cross-instance shared clipboard).
- `Ctrl+Shift+C`: copy current path or selected paths to the system clipboard.
- `Ctrl+N`: open current directory in a new window.
- `Ctrl+Shift+N`: create a new folder.
- `Delete`: permanently delete the selected items.
- `Ctrl+R`: open the run dialog.
- `Ctrl+F`: open regex search dialog.
- `Ctrl+Alt+P`: show PDF panel and load selected PDF.
- `Ctrl+Alt+T`: show terminal panel and restart terminal in current directory.
- `Ctrl+Alt+N`: show temp notes panel and reset temp file.
- `Ctrl+H`: toggle heuristics window.
- `Escape`: hide lower panel.
- `Left` or `Backspace`: navigate up.
- `Right` or `Enter`: open the selected directory.
- `Middle click` on directory (left/main panel): open it in a new window.

Drag & drop (when backend is available):

- Drag selected rows from one `pyxplorer` instance and drop into another.
- Drop onto a directory row to target that directory.
- Drop onto empty space/file rows to target the current directory.
- Default action follows platform conventions (same drive/device: move; otherwise: copy).
- Operations run asynchronously and refresh the current view when complete.

## Notes and limitations

- The directory scanner computes folder sizes asynchronously, so directory sizes briefly show `—` until scan results arrive.
- File deletion is permanent; there is currently no recycle-bin integration.
- The project is now cross-platform aware (Windows/Linux/macOS), but Windows remains the most tuned environment.
- The package name, console script, and project metadata now consistently use `pyxplorer`.
- Drag & drop requires a working Tk DnD backend (`tkinterdnd2`/`tkdnd`). If unavailable, cross-instance clipboard (`Ctrl+C/X/V`) remains supported.

## Per-user data directory

Pyxplorer stores clipboard, heuristics scripts, starred entries, and temp notes under:

- **Windows:** `%LOCALAPPDATA%\Pyxplorer`
- **Linux:** `${XDG_DATA_HOME:-~/.local/share}/Pyxplorer`
- **macOS:** `~/Library/Application Support/Pyxplorer`

## Configuration

`pyxplorer/settings.json` can override:

- theme colors
- fonts and font sizes
- row heights
- optional `start_dirs` for the left panel root nodes

If `settings.json` is missing or invalid, defaults from `pyxplorer/settings.py` are used.
