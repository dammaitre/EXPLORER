import json
from pathlib import Path
from .user_files import clipboard_json_path


def _clipboard_path() -> Path:
    return clipboard_json_path()


def load_shared_clipboard() -> dict:
    path = _clipboard_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"mode": None, "paths": []}
    except Exception:
        return {"mode": None, "paths": []}

    mode = raw.get("mode")
    paths = raw.get("paths")
    if mode not in ("copy", "cut"):
        return {"mode": None, "paths": []}
    if not isinstance(paths, list):
        return {"mode": None, "paths": []}

    clean_paths = [p for p in paths if isinstance(p, str) and p]
    if not clean_paths:
        return {"mode": None, "paths": []}
    return {"mode": mode, "paths": clean_paths}


def save_shared_clipboard(mode: str | None, paths: list[str]) -> None:
    payload = {"mode": mode if mode in ("copy", "cut") else None, "paths": list(paths or [])}
    path = _clipboard_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_shared_clipboard() -> None:
    save_shared_clipboard(None, [])
