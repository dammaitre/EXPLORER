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


def _load() -> set[str]:
    p = _store_path()
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {os.path.normcase(os.path.normpath(s)) for s in data if isinstance(s, str)}
    except Exception:
        pass
    return set()


def _save(starred: set[str]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted(starred), indent=2, ensure_ascii=False), encoding="utf-8")


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def is_starred(path: str) -> bool:
    return _key(path) in _load()


def toggle(path: str) -> bool:
    """Toggle star. Returns True if the path is now starred, False if unstarred."""
    starred = _load()
    k = _key(path)
    if k in starred:
        starred.discard(k)
        result = False
    else:
        starred.add(k)
        result = True
    _save(starred)
    return result


def all_starred() -> list[str]:
    """Return all starred paths in stable sorted order (normalised)."""
    return sorted(_load())


def clear_all() -> None:
    _save(set())
