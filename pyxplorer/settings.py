"""
settings.py — loads per-user settings.json and exposes THEME and START_DIRS.
All other modules import from here; never hardcode palette values elsewhere.
"""
import json
import os
import re
from .core.user_files import ensure_user_json_files, settings_json_path
from .logging import vprint

ensure_user_json_files()
_SETTINGS_FILE = settings_json_path()

_DEFAULTS: dict = {
    "ext_skipped": [],
    "scroll_speed": 1.0,
    "default_pdf_zoom": 1.5,
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


def _strip_jsonc(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"(^|\s)//.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _load_raw_settings() -> dict:
    try:
        with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return {}

    try:
        raw = json.loads(content)
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        try:
            raw = json.loads(_strip_jsonc(content))
            return raw if isinstance(raw, dict) else {}
        except json.JSONDecodeError as exc:
            vprint(f"[settings] Invalid JSON in settings.json: {exc}. Using defaults.")
            return {}
    except Exception as exc:
        vprint(f"[settings] Failed reading settings.json: {exc}. Using defaults.")
        return {}


def _load() -> dict:
    raw = _load_raw_settings()

    theme = {**_DEFAULTS["theme"], **raw.get("theme", {})}
    try:
        scroll_speed = float(raw.get("scroll_speed", _DEFAULTS["scroll_speed"]))
    except (TypeError, ValueError):
        scroll_speed = float(_DEFAULTS["scroll_speed"])
    scroll_speed = max(0.1, min(10.0, scroll_speed))

    raw_default_pdf_zoom = raw.get(
        "default-pdf-zoom",
        raw.get("default_pdf_zoom", _DEFAULTS["default_pdf_zoom"]),
    )
    try:
        default_pdf_zoom = float(raw_default_pdf_zoom)
    except (TypeError, ValueError):
        default_pdf_zoom = float(_DEFAULTS["default_pdf_zoom"])
    if default_pdf_zoom > 10.0:
        default_pdf_zoom = default_pdf_zoom / 100.0
    default_pdf_zoom = max(0.5, min(3.0, default_pdf_zoom))

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
        "default_pdf_zoom": default_pdf_zoom,
    }


_cfg = _load()

THEME: dict       = _cfg["theme"]
START_DIRS: list  = _cfg["start_dirs"]
SCAN_SKIP_DIRS: list = _cfg["scan_skip_dirs"]
EXT_SKIPPED: set  = _cfg["ext_skipped"]   # lowercase extensions with leading dot
SCROLL_SPEED: float = _cfg["scroll_speed"]
DEFAULT_PDF_ZOOM: float = _cfg["default_pdf_zoom"]
