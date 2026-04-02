"""
settings.py — loads settings.json and exposes THEME and START_DIRS.
All other modules import from here; never hardcode palette values elsewhere.
"""
import json
import os
from pathlib import Path

_SETTINGS_FILE = Path(__file__).parent / "settings.json"

_DEFAULTS: dict = {
    "ext_skipped": [],
    "scroll_speed": 1.0,
    "scan_skip_dirs": [],
    "theme": {
        "bg":              "#202020",
        "bg_dark":         "#161616",
        "bg_entry":        "#2D2D2D",
        "accent":          "#60CDFF",
        "text":            "#F3F3F3",
        "terminal_text":   "#00FF41",
        "text_mute":       "#9D9D9D",
        "border":          "#3A3A3A",
        "row_hover":       "#2A2A2A",
        "row_selected":    "#3D3D3D",
        "status_bg":       "#1C1C1C",
        "font_family":     "Segoe UI",
        "font_size_base":  13,
        "font_size_entry": 14,
        "font_size_small": 12,
        "row_height":      36,
        "row_height_nav":  34,
    },
    "start_dirs": [],
}


def _load() -> dict:
    try:
        with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        raw = {}
    except json.JSONDecodeError as exc:
        print(f"[settings] Invalid JSON in settings.json: {exc}. Using defaults.")
        raw = {}

    theme = {**_DEFAULTS["theme"], **raw.get("theme", {})}
    try:
        scroll_speed = float(raw.get("scroll_speed", _DEFAULTS["scroll_speed"]))
    except (TypeError, ValueError):
        scroll_speed = float(_DEFAULTS["scroll_speed"])
    scroll_speed = max(0.1, min(10.0, scroll_speed))

    start_dirs = [
        os.path.expanduser(p)
        for p in raw.get("start_dirs", _DEFAULTS["start_dirs"])
        if isinstance(p, str)
    ]

    scan_skip_dirs: list[str] = []
    for value in raw.get("scan_skip_dirs", _DEFAULTS["scan_skip_dirs"]):
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if not cleaned:
            continue
        expanded = os.path.expanduser(cleaned)
        scan_skip_dirs.append(os.path.normpath(expanded))

    # Deduplicate while preserving order (case-insensitive on Windows)
    seen_skip_dirs: set[str] = set()
    unique_skip_dirs: list[str] = []
    for item in scan_skip_dirs:
        key = os.path.normcase(item)
        if key in seen_skip_dirs:
            continue
        seen_skip_dirs.add(key)
        unique_skip_dirs.append(item)

    # Normalise extensions: lowercase, ensure leading dot, deduplicate
    ext_skipped: set[str] = set()
    for e in raw.get("ext_skipped", _DEFAULTS["ext_skipped"]):
        if isinstance(e, str) and e:
            e = e.lower().strip()
            ext_skipped.add(e if e.startswith(".") else f".{e}")
    return {
        "theme": theme,
        "start_dirs": start_dirs,
        "scan_skip_dirs": unique_skip_dirs,
        "ext_skipped": ext_skipped,
        "scroll_speed": scroll_speed,
    }


_cfg = _load()

THEME: dict       = _cfg["theme"]
START_DIRS: list  = _cfg["start_dirs"]
SCAN_SKIP_DIRS: list = _cfg["scan_skip_dirs"]
EXT_SKIPPED: set  = _cfg["ext_skipped"]   # lowercase extensions with leading dot
SCROLL_SPEED: float = _cfg["scroll_speed"]
