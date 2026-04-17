"""
Archive virtual filesystem — lets zip/7z/rar archives behave like folders.

Virtual path convention:
  <os_archive_path> + ARCHIVE_SEP + <inner_path>
  e.g.  C:\\data\\project.zip\x00reports/q1.pdf

  ARCHIVE_SEP  = "\\x00"  (null byte — can never appear in a real Windows path)
  inner_path   = always forward-slash-separated, no leading slash

Public API
----------
  is_archive_file(path)             True for .zip/.7z/.rar files (not virtual paths)
  is_archive_virtual_path(path)     True when ARCHIVE_SEP is present
  split_archive_path(path)          → (archive_os_path, inner_path)
  make_archive_path(archive, inner) → virtual path string
  find_archive_in_path(path)        walk a display path to find an embedded archive
  list_archive_dir(archive, inner)  → [VirtualEntry, ...]
  extract_to_temp(archive, inner)   → real OS path to extracted file, or None

Backends (in priority order, graceful degradation)
---------------------------------------------------
  ZIP  : built-in zipfile  (always available)
  7Z   : py7zr             (pip install py7zr)   → 7z.exe fallback
  RAR  : rarfile           (pip install rarfile) → 7z.exe fallback
"""
from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

ARCHIVE_SEP  = "\x00"
ARCHIVE_EXTS = {".zip", ".7z", ".rar"}

_DEFAULT_TEMP_WIN  = r"C:\temp"
_DEFAULT_TEMP_UNIX = "/tmp/pyxplorer"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def is_archive_file(path: str) -> bool:
    """True when path's extension is a supported archive format (not a virtual path)."""
    if ARCHIVE_SEP in path:
        return False
    return os.path.splitext(path)[1].lower() in ARCHIVE_EXTS


def is_archive_virtual_path(path: str) -> bool:
    """True when path is a virtual location inside an archive."""
    return ARCHIVE_SEP in path


def split_archive_path(path: str) -> tuple[str, str]:
    """Return (archive_os_path, inner_path).  inner_path has no leading slash."""
    if ARCHIVE_SEP not in path:
        return path, ""
    archive, inner = path.split(ARCHIVE_SEP, 1)
    return archive, inner.strip("/")


def make_archive_path(archive_path: str, inner_path: str) -> str:
    """Construct a virtual archive path from an OS archive path + inner path."""
    return archive_path + ARCHIVE_SEP + inner_path.strip("/")


def find_archive_in_path(path: str) -> str | None:
    """
    Given an OS (or display) path that may pass through an archive file,
    return the corresponding virtual archive path, or None.

    Used so breadcrumb clicks on ``archive.zip/inner/folder`` still work
    after to_display() has rendered the virtual path as a plain string.
    """
    from .longpath import normalize, to_display

    display = to_display(path)
    try:
        parts = Path(display).parts
    except Exception:
        return None

    for i in range(len(parts), 0, -1):
        try:
            prefix = str(Path(*parts[:i]))
        except Exception:
            continue
        if is_archive_file(prefix) and os.path.isfile(normalize(prefix)):
            remaining = parts[i:]
            inner = "/".join(remaining) if remaining else ""
            return make_archive_path(prefix, inner)
    return None


# ---------------------------------------------------------------------------
# Virtual directory entry
# ---------------------------------------------------------------------------

class VirtualEntry:
    """Mimics os.DirEntry for archive contents."""
    __slots__ = ("name", "path", "is_dir", "size")

    def __init__(self, name: str, path: str, is_dir: bool, size: int = -1):
        self.name   = name
        self.path   = path
        self.is_dir = is_dir
        self.size   = size   # uncompressed bytes; -1 when unknown / directory


# ---------------------------------------------------------------------------
# Directory listing
# ---------------------------------------------------------------------------

def list_archive_dir(archive_path: str, inner_path: str) -> list[VirtualEntry]:
    """Return immediate children of inner_path inside the archive."""
    ext      = os.path.splitext(archive_path)[1].lower()
    inner    = inner_path.strip("/")

    if ext == ".zip":
        return _list_zip(archive_path, inner)
    if ext == ".7z":
        return _list_7z(archive_path, inner)
    if ext == ".rar":
        return _list_rar(archive_path, inner)
    return []


# ── ZIP ──────────────────────────────────────────────────────────────────────

def _list_zip(archive_path: str, inner_dir: str) -> list[VirtualEntry]:
    from .longpath import normalize

    entries:    list[VirtualEntry] = []
    seen_dirs:  set[str] = set()
    seen_files: set[str] = set()
    prefix = (inner_dir + "/") if inner_dir else ""

    try:
        with zipfile.ZipFile(normalize(archive_path), "r") as zf:
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if not name.startswith(prefix):
                    continue
                rest = name[len(prefix):]
                if not rest:
                    continue

                parts      = rest.split("/")
                child_name = parts[0]
                if not child_name:
                    continue

                inner_child = prefix + child_name

                # child is a deeper file or an implied sub-directory
                if len(parts) > 1:
                    if child_name not in seen_dirs:
                        seen_dirs.add(child_name)
                        entries.append(VirtualEntry(
                            child_name,
                            make_archive_path(archive_path, inner_child),
                            True,
                        ))
                else:
                    # Direct child: file or explicit directory entry (trailing /)
                    if name.endswith("/"):
                        if child_name not in seen_dirs:
                            seen_dirs.add(child_name)
                            entries.append(VirtualEntry(
                                child_name,
                                make_archive_path(archive_path, inner_child),
                                True,
                            ))
                    else:
                        if child_name not in seen_files:
                            seen_files.add(child_name)
                            entries.append(VirtualEntry(
                                child_name,
                                make_archive_path(archive_path, inner_child),
                                False,
                                info.file_size,
                            ))
    except Exception:
        pass

    return entries


# ── 7Z ───────────────────────────────────────────────────────────────────────

def _list_7z(archive_path: str, inner_dir: str) -> list[VirtualEntry]:
    try:
        return _list_7z_py7zr(archive_path, inner_dir)
    except ImportError:
        pass
    return _list_via_cli(archive_path, inner_dir)


def _list_7z_py7zr(archive_path: str, inner_dir: str) -> list[VirtualEntry]:
    import py7zr  # noqa: F401 (ImportError propagates if absent)
    from .longpath import normalize

    entries:    list[VirtualEntry] = []
    seen_dirs:  set[str] = set()
    seen_files: set[str] = set()
    prefix = (inner_dir + "/") if inner_dir else ""

    with py7zr.SevenZipFile(normalize(archive_path), mode="r") as zf:
        for info in zf.list():
            name = info.filename.replace("\\", "/")
            if not name.startswith(prefix):
                continue
            rest = name[len(prefix):]
            if not rest:
                continue

            parts      = rest.split("/")
            child_name = parts[0]
            if not child_name:
                continue

            inner_child = prefix + child_name

            if info.is_directory or len(parts) > 1:
                if child_name not in seen_dirs:
                    seen_dirs.add(child_name)
                    entries.append(VirtualEntry(
                        child_name,
                        make_archive_path(archive_path, inner_child),
                        True,
                    ))
            else:
                if child_name not in seen_files:
                    seen_files.add(child_name)
                    size = getattr(info, "uncompressed", -1) or -1
                    entries.append(VirtualEntry(
                        child_name,
                        make_archive_path(archive_path, inner_child),
                        False,
                        size,
                    ))

    return entries


# ── RAR ──────────────────────────────────────────────────────────────────────

def _list_rar(archive_path: str, inner_dir: str) -> list[VirtualEntry]:
    try:
        return _list_rar_rarfile(archive_path, inner_dir)
    except ImportError:
        pass
    return _list_via_cli(archive_path, inner_dir)   # 7-zip can open .rar too


def _list_rar_rarfile(archive_path: str, inner_dir: str) -> list[VirtualEntry]:
    import rarfile  # noqa: F401 (ImportError propagates if absent)
    from .longpath import normalize

    entries:    list[VirtualEntry] = []
    seen_dirs:  set[str] = set()
    seen_files: set[str] = set()
    prefix = (inner_dir + "/") if inner_dir else ""

    with rarfile.RarFile(normalize(archive_path)) as rf:
        for info in rf.infolist():
            name = info.filename.replace("\\", "/")
            if not name.startswith(prefix):
                continue
            rest = name[len(prefix):]
            if not rest:
                continue

            parts      = rest.split("/")
            child_name = parts[0]
            if not child_name:
                continue

            inner_child = prefix + child_name

            if info.is_dir() or len(parts) > 1:
                if child_name not in seen_dirs:
                    seen_dirs.add(child_name)
                    entries.append(VirtualEntry(
                        child_name,
                        make_archive_path(archive_path, inner_child),
                        True,
                    ))
            else:
                if child_name not in seen_files:
                    seen_files.add(child_name)
                    size = getattr(info, "file_size", -1) or -1
                    entries.append(VirtualEntry(
                        child_name,
                        make_archive_path(archive_path, inner_child),
                        False,
                        size,
                    ))

    return entries


# ── 7-zip CLI (shared fallback for 7z and rar) ───────────────────────────────

def _list_via_cli(archive_path: str, inner_dir: str) -> list[VirtualEntry]:
    import subprocess
    from .longpath import to_display

    seven_zip = _find_7zip()
    if not seven_zip:
        return []

    entries:    list[VirtualEntry] = []
    seen_dirs:  set[str] = set()
    seen_files: set[str] = set()
    prefix = (inner_dir + "/") if inner_dir else ""

    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        result = subprocess.run(
            [seven_zip, "l", "-slt", to_display(archive_path)],
            capture_output=True, text=True, check=False,
            **kwargs,
        )
        current: dict = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("----------"):
                if current:
                    _add_cli_entry(current, prefix, archive_path,
                                   seen_dirs, seen_files, entries)
                    current = {}
            elif "=" in line:
                k, _, v = line.partition("=")
                current[k.strip()] = v.strip()
        if current:
            _add_cli_entry(current, prefix, archive_path,
                           seen_dirs, seen_files, entries)
    except Exception:
        pass

    return entries


def _add_cli_entry(info: dict, prefix: str, archive_path: str,
                   seen_dirs: set, seen_files: set,
                   entries: list[VirtualEntry]) -> None:
    name = info.get("Path", "").replace("\\", "/")
    if not name.startswith(prefix):
        return
    rest = name[len(prefix):]
    if not rest:
        return

    parts      = rest.split("/")
    child_name = parts[0]
    if not child_name:
        return

    inner_child = prefix + child_name
    is_dir = info.get("Attributes", "").startswith("D") or len(parts) > 1

    if is_dir:
        if child_name not in seen_dirs:
            seen_dirs.add(child_name)
            entries.append(VirtualEntry(
                child_name,
                make_archive_path(archive_path, inner_child),
                True,
            ))
    else:
        if child_name not in seen_files:
            seen_files.add(child_name)
            try:
                size = int(info.get("Size", "-1"))
            except (ValueError, TypeError):
                size = -1
            entries.append(VirtualEntry(
                child_name,
                make_archive_path(archive_path, inner_child),
                False,
                size,
            ))


# ---------------------------------------------------------------------------
# Single-file extraction
# ---------------------------------------------------------------------------

def extract_to_temp(archive_path: str, inner_path: str,
                    temp_dir: str | None = None) -> str | None:
    """
    Extract a single file from the archive to temp_dir.
    Returns the path to the extracted file, or None on failure.
    """
    if temp_dir is None:
        temp_dir = _DEFAULT_TEMP_WIN if os.name == "nt" else _DEFAULT_TEMP_UNIX

    try:
        os.makedirs(temp_dir, exist_ok=True)
    except OSError:
        pass

    filename = inner_path.strip("/").split("/")[-1]
    if not filename:
        return None

    dest = os.path.join(temp_dir, filename)
    ext  = os.path.splitext(archive_path)[1].lower()

    if ext == ".zip":
        return _extract_zip(archive_path, inner_path, dest)
    if ext == ".7z":
        return _extract_7z(archive_path, inner_path, dest)
    if ext == ".rar":
        return _extract_rar(archive_path, inner_path, dest)
    return None


def _extract_zip(archive_path: str, inner_path: str, dest: str) -> str | None:
    from .longpath import normalize

    member = inner_path.strip("/")
    try:
        with zipfile.ZipFile(normalize(archive_path), "r") as zf:
            members = zf.namelist()
            if member not in members:
                lower = member.lower()
                member = next((m for m in members if m.lower() == lower), None)
                if not member:
                    return None
            with zf.open(member) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
        return dest
    except Exception:
        return None


def _extract_7z(archive_path: str, inner_path: str, dest: str) -> str | None:
    try:
        return _extract_7z_py7zr(archive_path, inner_path, dest)
    except ImportError:
        pass
    return _extract_via_cli(archive_path, inner_path, dest)


def _extract_7z_py7zr(archive_path: str, inner_path: str, dest: str) -> str | None:
    import py7zr  # noqa: F401
    from .longpath import normalize

    target    = inner_path.strip("/")
    temp_base = os.path.dirname(dest)

    try:
        with py7zr.SevenZipFile(normalize(archive_path), mode="r") as zf:
            zf.extract(path=temp_base, targets=[target])

        extracted = os.path.join(temp_base, target.replace("/", os.sep))
        if os.path.isfile(extracted):
            if os.path.normcase(extracted) != os.path.normcase(dest):
                shutil.move(extracted, dest)
            return dest
    except Exception:
        pass
    return None


def _extract_rar(archive_path: str, inner_path: str, dest: str) -> str | None:
    try:
        return _extract_rar_rarfile(archive_path, inner_path, dest)
    except ImportError:
        pass
    return _extract_via_cli(archive_path, inner_path, dest)


def _extract_rar_rarfile(archive_path: str, inner_path: str, dest: str) -> str | None:
    import rarfile  # noqa: F401
    from .longpath import normalize

    member = inner_path.strip("/")
    try:
        with rarfile.RarFile(normalize(archive_path)) as rf:
            with rf.open(member) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
        return dest
    except Exception:
        return None


def _extract_via_cli(archive_path: str, inner_path: str, dest: str) -> str | None:
    import subprocess
    from .longpath import to_display

    seven_zip = _find_7zip()
    if not seven_zip:
        return None

    temp_dir = os.path.dirname(dest)
    target   = inner_path.strip("/").replace("/", os.sep)

    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        subprocess.run(
            [seven_zip, "e", to_display(archive_path),
             f"-o{temp_dir}", target, "-y"],
            capture_output=True, check=False,
            **kwargs,
        )
        extracted = os.path.join(temp_dir, os.path.basename(target))
        if os.path.isfile(extracted):
            return extracted
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 7-Zip discovery
# ---------------------------------------------------------------------------

def _find_7zip() -> str | None:
    found = shutil.which("7z")
    if found:
        return found
    if os.name == "nt":
        for candidate in (
            r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe",
        ):
            if os.path.isfile(candidate):
                return candidate
    return None
