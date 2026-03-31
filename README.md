# EXPLORER

Win11's file explorer copy but damien-friendly

`EXPLORER` is a Windows-focused file explorer prototype built with `tkinter` and `ttk`. It aims to keep the familiar Explorer workflow while stripping the UI down to the essentials: fast navigation, a clean three-pane layout, keyboard-first interaction, and asynchronous folder size scanning.

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
- **Main file view:** directory and file listing with sortable `Name`, `Size`, and `%` columns.
- **Async folder size scanning:** subdirectory sizes are scanned in the background and reflected in the UI when ready.
- **Status bar feedback:** spinner during scans, item count, total size, and selection size summary.
- **Keyboard shortcuts:** copy, cut, paste, delete, new folder, run dialog, navigation shortcuts, and terminal launcher.
- **Windows long-path support:** internal path normalization plus an attempt to enable the Windows long-path registry flag.
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
│   │   ├── fs.py
│   │   ├── longpath.py
│   │   ├── scanner.py
│   │   └── search.py
│   └── ui/
│       ├── left_panel.py
│       ├── main_frame.py
│       ├── status_bar.py
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

Defines app-wide shortcuts and file operations such as copy, cut, paste, delete, new folder creation, path copy, run dialog, and terminal launch.

### `pyxplorer/settings.py` and `pyxplorer/settings.json`

Load user-adjustable theme and startup directory settings.

### `pyxplorer/core/`

- `fs.py`: filesystem operations and display helpers.
- `longpath.py`: Windows long-path normalization and registry toggle helper.
- `scanner.py`: cancellable background directory-size scanning.
- `search.py`: regex name search backend stub for future UI integration.

### `pyxplorer/ui/`

- `top_bar.py`: path entry, breadcrumbs, history dropdown, and run dialog.
- `left_panel.py`: lazy-loading tree navigation.
- `main_frame.py`: main directory listing, sorting, selection tracking, and incremental loading.
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
- `Ctrl+V`: paste into the current directory.
- `Ctrl+Shift+C`: copy current path or selected paths to the system clipboard.
- `Ctrl+Shift+N`: create a new folder.
- `Delete`: permanently delete the selected items.
- `Ctrl+R`: open the run dialog.
- `Ctrl+F`: open the current search placeholder dialog.
- `Ctrl+Alt+T`: open a terminal in the current directory.
- `Left` or `Backspace`: navigate up.
- `Right` or `Enter`: open the selected directory.

## Notes and limitations

- The search backend exists in `pyxplorer/core/search.py`, but the UI is still a placeholder dialog.
- The directory scanner computes folder sizes asynchronously, so directory sizes briefly show `—` until scan results arrive.
- File deletion is permanent; there is currently no recycle-bin integration.
- The project is Windows-oriented, though parts of the code include basic cross-platform fallbacks.
- The package name, console script, and project metadata now consistently use `pyxplorer`.

## Configuration

`pyxplorer/settings.json` can override:

- theme colors
- fonts and font sizes
- row heights
- optional `start_dirs` for the left panel root nodes

If `settings.json` is missing or invalid, defaults from `pyxplorer/settings.py` are used.
