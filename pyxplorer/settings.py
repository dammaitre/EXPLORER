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
    start_dirs = [
        os.path.expanduser(p)
        for p in raw.get("start_dirs", _DEFAULTS["start_dirs"])
        if isinstance(p, str)
    ]
    # Normalise extensions: lowercase, ensure leading dot, deduplicate
    ext_skipped: set[str] = set()
    for e in raw.get("ext_skipped", _DEFAULTS["ext_skipped"]):
        if isinstance(e, str) and e:
            e = e.lower().strip()
            ext_skipped.add(e if e.startswith(".") else f".{e}")
    return {"theme": theme, "start_dirs": start_dirs, "ext_skipped": ext_skipped}


_cfg = _load()

THEME: dict       = _cfg["theme"]
START_DIRS: list  = _cfg["start_dirs"]
EXT_SKIPPED: set  = _cfg["ext_skipped"]   # lowercase extensions with leading dot
