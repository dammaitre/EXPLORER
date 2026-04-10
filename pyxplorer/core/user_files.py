from __future__ import annotations

import json
from pathlib import Path

from .appdirs import pyxplorer_data_dir


_SETTINGS_TEMPLATE: dict = {
    "scroll_speed": 1.0,
    "default-pdf-zoom": 150,
    "scan_skip_dirs": [],
    "theme": {
        "bg": "#202020",
        "bg_dark": "#161616",
        "bg_entry": "#2D2D2D",
        "accent": "#60CDFF",
        "text": "#F3F3F3",
        "terminal_text": "#00FF41",
        "text_mute": "#9D9D9D",
        "border": "#3A3A3A",
        "row_hover": "#2A2A2A",
        "row_selected": "#3D3D3D",
        "status_bg": "#1C1C1C",
        "font_family": "Segoe UI",
        "font_size_base": 13,
        "font_size_entry": 14,
        "font_size_small": 12,
        "row_height": 36,
        "row_height_nav": 34,
        "md_heading_color":     "#87CEEB",
        "md_h1_size":           22,
        "md_h2_size":           20,
        "md_h3_size":           18,
        "md_h4_size":           16,
        "md_h5_size":           15,
        "md_h6_size":           14,
        "md_bold_color":        "#F0F0F0",
        "md_italic_color":      "#D4D4D4",
        "md_code_fg":           "#CE9178",
        "md_code_bg":           "#2A2A2A",
        "md_blockquote_color":  "#9D9D9D",
        "md_link_color":        "#60CDFF",
        "md_hr_color":          "#4A4A4A",
        "md_list_marker_color": "#60CDFF",
    },
    "start_dirs": [],
    "expression_skipped": [],
}


def settings_json_path() -> Path:
    return pyxplorer_data_dir() / "settings.json"


def clipboard_json_path() -> Path:
    return pyxplorer_data_dir() / "clipboard.json"


def starred_json_path() -> Path:
    return pyxplorer_data_dir() / "starred.json"


def tags_json_path() -> Path:
    return pyxplorer_data_dir() / "tags.json"


def _write_json_if_missing(path: Path, payload: dict | list) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_user_json_files() -> None:
    """Ensure per-user json files exist at startup."""
    _write_json_if_missing(settings_json_path(), _SETTINGS_TEMPLATE)
    _write_json_if_missing(clipboard_json_path(), {})
    _write_json_if_missing(starred_json_path(), {})
    _write_json_if_missing(tags_json_path(), {})
