from __future__ import annotations

import json
import os
from pathlib import Path

from .longpath import normalize
from .user_files import tags_json_path


_CACHE: dict[str, str] | None = None


def _store_path() -> Path:
    return tags_json_path()


def _key(path: str) -> str:
    return os.path.normcase(normalize(path))


def _ensure_loaded() -> None:
    global _CACHE
    if _CACHE is not None:
        return

    p = _store_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _CACHE = {}
        return
    except Exception:
        _CACHE = {}
        return

    if not isinstance(raw, dict):
        _CACHE = {}
        return

    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        tag = value.strip()
        if not tag:
            continue
        cleaned[key] = tag
    _CACHE = cleaned


def _save() -> None:
    _ensure_loaded()
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = _CACHE or {}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def get_tag(path: str) -> str | None:
    _ensure_loaded()
    return (_CACHE or {}).get(_key(path))


def set_tag(path: str, tag: str | None) -> None:
    _ensure_loaded()
    if _CACHE is None:
        return
    k = _key(path)
    cleaned = (tag or "").strip()
    if cleaned:
        _CACHE[k] = cleaned
    else:
        _CACHE.pop(k, None)
    _save()


def set_tag_bulk(paths: list[str], tag: str | None) -> int:
    _ensure_loaded()
    if _CACHE is None:
        return 0
    cleaned = (tag or "").strip()
    count = 0
    for path in paths:
        if not isinstance(path, str) or not path:
            continue
        key = _key(path)
        if cleaned:
            _CACHE[key] = cleaned
        else:
            _CACHE.pop(key, None)
        count += 1
    _save()
    return count
