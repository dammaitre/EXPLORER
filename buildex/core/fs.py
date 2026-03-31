"""
Filesystem operations. Every OS call goes through normalize() from longpath.py.
Phases 3-6 will flesh these out; stubs are intentionally minimal.
"""
import os
import shutil
from pathlib import Path
from core.longpath import normalize


def list_dir(path: str) -> list[os.DirEntry]:
    """Return os.DirEntry objects for direct children of path."""
    entries = []
    try:
        with os.scandir(normalize(path)) as it:
            for entry in it:
                entries.append(entry)
    except (PermissionError, OSError):
        pass
    return entries


def copy_items(src_paths: list[str], dst_dir: str) -> None:
    """Copy files/dirs into dst_dir. Stub — Phase 6."""
    dst = normalize(dst_dir)
    for src in src_paths:
        s = normalize(src)
        name = os.path.basename(s)
        dest = normalize(os.path.join(dst, name))
        if os.path.isdir(s):
            shutil.copytree(s, dest)
        else:
            shutil.copy2(s, dest)


def move_items(src_paths: list[str], dst_dir: str) -> None:
    """Move (cut+paste) files/dirs into dst_dir. Stub — Phase 6."""
    dst = normalize(dst_dir)
    for src in src_paths:
        s = normalize(src)
        name = os.path.basename(s)
        dest = normalize(os.path.join(dst, name))
        shutil.move(s, dest)


def make_dir(path: str) -> None:
    """Create a directory (and parents). Stub — Phase 6."""
    os.makedirs(normalize(path), exist_ok=True)


def move_to(src: str, dst: str) -> None:
    """Drag-and-drop stub — same as cut+paste. Phase 10."""
    shutil.move(normalize(src), normalize(dst))


def fmt_size(n: int) -> str:
    """Human-readable file size. Shared by main_frame and status_bar."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
