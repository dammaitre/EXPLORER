from __future__ import annotations

import os
import sys
from pathlib import Path


def pyxplorer_data_dir() -> Path:
    """Return a writable per-user data directory for Pyxplorer across platforms."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME")
        if not base:
            base = str(Path.home() / ".local" / "share")

    return Path(base) / "Pyxplorer"
