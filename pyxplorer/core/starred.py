"""
Starred-paths store: persists a set of absolute paths to
%LOCALAPPDATA%/Pyxplorer/starred.json.

Public API
----------
is_starred(path)  -> bool
toggle(path)      -> bool   (True = now starred, False = now unstarred)
all_starred()     -> list[str]
clear_all()       -> None
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _store_path() -> Path:
    local_app = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local_app) / "Pyxplorer" / "starred.json"


def _restore_leaf_case(path: str) -> str:
    """Best-effort restore of final path component casing from filesystem."""
    norm = os.path.normpath(path)
    parent, leaf = os.path.split(norm)
    if not parent or not leaf:
        return norm
    try:
        for candidate in os.listdir(parent):
            if candidate.casefold() == leaf.casefold():
                return os.path.join(parent, candidate)
    except Exception:
        pass
    return norm


def _load() -> dict[str, str]:
    p = _store_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            result: dict[str, str] = {}
            for value in data:
                if not isinstance(value, str):
                    continue
                display = _restore_leaf_case(value)
                result[_key(display)] = os.path.normpath(display)
            return result
        if isinstance(data, dict):
            # Forward-compatible shape: {normalized_key: display_path}
            result: dict[str, str] = {}
            for value in data.values():
                if not isinstance(value, str):
                    continue
                display = os.path.normpath(value)
                result[_key(display)] = display
            return result
    except Exception:
        pass
    return {}


def _save(starred: dict[str, str]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    values = sorted(starred.values(), key=lambda s: s.casefold())
    p.write_text(json.dumps(values, indent=2, ensure_ascii=False), encoding="utf-8")


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def is_starred(path: str) -> bool:
    return _key(path) in _load()


def toggle(path: str) -> bool:
    """Toggle star. Returns True if the path is now starred, False if unstarred."""
    starred = _load()
    k = _key(path)
    if k in starred:
        starred.pop(k, None)
        result = False
    else:
        starred[k] = os.path.normpath(path)
        result = True
    _save(starred)
    return result


def all_starred() -> list[str]:
    """Return all starred paths in stable sorted order, preserving display casing."""
    return sorted(_load().values(), key=lambda s: s.casefold())


def clear_all() -> None:
    _save({})
