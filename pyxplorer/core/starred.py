"""
Starred-paths store: persists a set of absolute paths to
the per-user Pyxplorer data directory.

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
from .appdirs import pyxplorer_data_dir


_CACHE: dict[str, str] | None = None
_CACHE_MTIME_NS: int | None = None


def _store_path() -> Path:
    return pyxplorer_data_dir() / "starred.json"


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
    global _CACHE, _CACHE_MTIME_NS

    try:
        stat = p.stat()
        mtime_ns: int | None = stat.st_mtime_ns
    except FileNotFoundError:
        _CACHE = {}
        _CACHE_MTIME_NS = None
        return {}
    except Exception:
        mtime_ns = None

    if _CACHE is not None and mtime_ns is not None and _CACHE_MTIME_NS == mtime_ns:
        return dict(_CACHE)

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            result: dict[str, str] = {}
            for value in data:
                if not isinstance(value, str):
                    continue
                display = _restore_leaf_case(value)
                result[_key(display)] = os.path.normpath(display)
            _CACHE = result
            _CACHE_MTIME_NS = mtime_ns
            return dict(result)
        if isinstance(data, dict):
            # Forward-compatible shape: {normalized_key: display_path}
            result: dict[str, str] = {}
            for value in data.values():
                if not isinstance(value, str):
                    continue
                display = os.path.normpath(value)
                result[_key(display)] = display
            _CACHE = result
            _CACHE_MTIME_NS = mtime_ns
            return dict(result)
    except Exception:
        pass
    _CACHE = {}
    _CACHE_MTIME_NS = mtime_ns
    return {}


def _save(starred: dict[str, str]) -> None:
    global _CACHE, _CACHE_MTIME_NS
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    values = sorted(starred.values(), key=lambda s: s.casefold())
    p.write_text(json.dumps(values, indent=2, ensure_ascii=False), encoding="utf-8")
    _CACHE = dict(starred)
    try:
        _CACHE_MTIME_NS = p.stat().st_mtime_ns
    except Exception:
        _CACHE_MTIME_NS = None


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
