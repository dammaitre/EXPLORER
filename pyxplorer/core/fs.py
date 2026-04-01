"""
Filesystem operations. Every OS call goes through normalize() from longpath.py.
Copy and move on Windows use robocopy for reliability on network drives and long paths.
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path
from .longpath import normalize, to_display


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


def _unique_copy_name(dst_dir: str, name: str) -> str:
    """Return a non-conflicting path for a same-directory copy (Windows Explorer style)."""
    base, ext = os.path.splitext(name)
    candidate = os.path.join(dst_dir, f"{base} — Copy{ext}")
    n = 2
    while os.path.exists(normalize(candidate)):
        candidate = os.path.join(dst_dir, f"{base} — Copy ({n}){ext}")
        n += 1
    return candidate


def _unique_copy_suffix_name(dst_dir: str, name: str) -> str:
    """Return a non-conflicting path using a '_copy' suffix.

    Examples:
    - file.txt      -> file_copy.txt
    - file_copy.txt -> file_copy_2.txt
    """
    base, ext = os.path.splitext(name)
    candidate = os.path.join(dst_dir, f"{base}_copy{ext}")
    n = 2
    while os.path.exists(normalize(candidate)):
        candidate = os.path.join(dst_dir, f"{base}_copy_{n}{ext}")
        n += 1
    return candidate


def _is_file_exists_error(err_text: str) -> bool:
    text = (err_text or "").lower()
    return "error 80" in text or "already exists" in text or "file exists" in text


_ROBOCOPY_FLAGS = ["/R:3", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS", "/NP"]


def _run_robocopy(args: list[str]) -> None:
    """Run robocopy with the given args. Exit codes 0-7 are success; 8+ are errors."""
    result = subprocess.run(
        ["robocopy"] + args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode >= 8:
        raise OSError(
            f"robocopy failed (exit {result.returncode}): "
            + result.stderr.decode(errors="replace").strip()
        )


def copy_items(src_paths: list[str], dst_dir: str) -> None:
    """Copy files/dirs into dst_dir. Uses robocopy on Windows."""
    dst = normalize(dst_dir)
    dst_display = to_display(dst_dir)

    for src in src_paths:
        s = normalize(src)
        s_display = to_display(src)
        name = os.path.basename(s_display)

        if sys.platform == "win32":
            dest_display = os.path.join(dst_display, name)
            # Same-directory copy: robocopy cannot copy a file onto itself
            if os.path.normcase(s) == os.path.normcase(normalize(dest_display)):
                unique = _unique_copy_name(dst_display, name)
                unique_name = os.path.basename(unique)
                if os.path.isdir(s):
                    _run_robocopy([s_display, unique, "/E", "/COPY:DAT"] + _ROBOCOPY_FLAGS)
                else:
                    src_dir = str(Path(s_display).parent)
                    _run_robocopy([src_dir, dst_display, name, f"/COPYALL", "/A-:SH"] + _ROBOCOPY_FLAGS)
                    # rename to unique name
                    os.rename(
                        normalize(os.path.join(dst_display, name)),
                        normalize(unique),
                    )
            elif os.path.isdir(s):
                _run_robocopy([s_display, dest_display, "/E", "/COPY:DAT"] + _ROBOCOPY_FLAGS)
            else:
                src_dir = str(Path(s_display).parent)
                try:
                    _run_robocopy([src_dir, dst_display, name, "/COPY:DAT"] + _ROBOCOPY_FLAGS)
                except OSError as exc:
                    if not _is_file_exists_error(str(exc)):
                        raise
                    unique = _unique_copy_suffix_name(dst_display, name)
                    shutil.copy2(s, normalize(unique))
        else:
            dest = normalize(os.path.join(dst, os.path.basename(s)))
            if os.path.normcase(s) == os.path.normcase(dest):
                dest = normalize(_unique_copy_name(dst_display, os.path.basename(s)))
            if os.path.isdir(s):
                shutil.copytree(s, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(s, dest)


def move_items(src_paths: list[str], dst_dir: str) -> None:
    """Move (cut+paste) files/dirs into dst_dir. Uses robocopy /MOVE on Windows."""
    dst = normalize(dst_dir)
    dst_display = to_display(dst_dir)

    for src in src_paths:
        s = normalize(src)
        s_display = to_display(src)
        name = os.path.basename(s_display)

        if sys.platform == "win32":
            dest_display = os.path.join(dst_display, name)
            if os.path.normcase(s) == os.path.normcase(normalize(dest_display)):
                continue  # moving a file to the same location is a no-op
            if os.path.isdir(s):
                _run_robocopy([s_display, dest_display, "/E", "/MOVE", "/COPY:DAT"] + _ROBOCOPY_FLAGS)
                # robocopy /MOVE leaves empty source dir; clean it up
                try:
                    if os.path.exists(s):
                        shutil.rmtree(s)
                except OSError:
                    pass
            else:
                src_dir = str(Path(s_display).parent)
                _run_robocopy([src_dir, dst_display, name, "/MOV", "/COPY:DAT"] + _ROBOCOPY_FLAGS)
        else:
            dest = normalize(os.path.join(dst, os.path.basename(s)))
            shutil.move(s, dest)


def delete_items(paths: list[str]) -> None:
    """Permanently delete files and directories (no recycle bin)."""
    for p in paths:
        s = normalize(p)
        if os.path.isdir(s) and not os.path.islink(s):
            shutil.rmtree(s)
        else:
            os.remove(s)


def make_dir(path: str) -> None:
    """Create a directory (and parents)."""
    os.makedirs(normalize(path), exist_ok=True)


def rename_item(src_path: str, new_name: str) -> str:
    """Rename a file or directory in place. Returns the new absolute path."""
    cleaned = (new_name or "").strip()
    if not cleaned:
        raise ValueError("New name cannot be empty.")
    if os.sep in cleaned or (os.altsep and os.altsep in cleaned):
        raise ValueError("New name must not contain path separators.")

    src_display = to_display(src_path)
    parent_display = str(Path(src_display).parent)
    dst_display = os.path.join(parent_display, cleaned)

    src = normalize(src_display)
    dst = normalize(dst_display)
    if os.path.normcase(src) == os.path.normcase(dst):
        return dst_display
    if os.path.exists(dst):
        raise FileExistsError(f"An item named '{cleaned}' already exists.")

    os.rename(src, dst)
    return dst_display


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
