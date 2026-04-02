"""
Filesystem operations. Every OS call goes through normalize() from longpath.py.
Copy and move on Windows use robocopy for reliability on network drives and long paths.
"""
import os
import sys
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable
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


_ROBOCOPY_FLAGS = ["/R:3", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS"]

_PERCENT_RE = re.compile(r"(\d{1,3})%")


def _estimate_path_bytes(path: str) -> int:
    norm = normalize(path)
    try:
        if os.path.isfile(norm):
            return max(1, int(os.path.getsize(norm)))
        if not os.path.isdir(norm):
            return 1
    except OSError:
        return 1

    total = 0
    stack = [norm]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += max(1, int(entry.stat(follow_symlinks=False).st_size))
                    except OSError:
                        continue
        except OSError:
            continue
    return max(1, total)


def _build_progress_spans(src_paths: list[str]) -> list[tuple[int, int]]:
    if not src_paths:
        return []
    weights = [_estimate_path_bytes(p) for p in src_paths]
    total_weight = sum(weights)
    if total_weight <= 0:
        step = 100 / max(1, len(src_paths))
        spans: list[tuple[int, int]] = []
        for index in range(len(src_paths)):
            start = int(index * step)
            end = int((index + 1) * step)
            spans.append((start, end))
        return spans

    spans = []
    acc = 0
    for weight in weights:
        start = int(acc * 100 / total_weight)
        acc += weight
        end = int(acc * 100 / total_weight)
        spans.append((start, end))
    if spans:
        spans[-1] = (spans[-1][0], 100)
    return spans


def _sub_progress(
    callback: Callable[[int], None] | None,
    span: tuple[int, int],
) -> Callable[[int], None] | None:
    if callback is None:
        return None

    start, end = span

    def _emit(local_pct: int) -> None:
        clamped = max(0, min(100, int(local_pct)))
        overall = start + int((end - start) * clamped / 100)
        callback(overall)

    return _emit


def _run_robocopy(args: list[str], progress_cb: Callable[[int], None] | None = None) -> None:
    """Run robocopy with the given args. Exit codes 0-7 are success; 8+ are errors."""
    proc = subprocess.Popen(
        ["robocopy"] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    last_pct = 0
    lines: list[str] = []
    if proc.stdout is not None:
        for line in proc.stdout:
            lines.append(line)
            m = _PERCENT_RE.search(line)
            if m is None:
                continue
            try:
                pct = max(0, min(100, int(m.group(1))))
            except Exception:
                continue
            if pct >= last_pct:
                last_pct = pct
                if progress_cb is not None:
                    progress_cb(last_pct)

    result_code = proc.wait()
    if result_code >= 8:
        raise OSError(
            f"robocopy failed (exit {result_code}): "
            + "".join(lines[-20:]).strip()
        )
    if progress_cb is not None:
        progress_cb(100)


def copy_items(
    src_paths: list[str],
    dst_dir: str,
    progress_cb: Callable[[int], None] | None = None,
) -> None:
    """Copy files/dirs into dst_dir. Uses robocopy on Windows."""
    dst = normalize(dst_dir)
    dst_display = to_display(dst_dir)
    spans = _build_progress_spans(src_paths)

    for index, src in enumerate(src_paths):
        item_cb = _sub_progress(progress_cb, spans[index]) if index < len(spans) else progress_cb
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
                    _run_robocopy([s_display, unique, "/E", "/COPY:DAT"] + _ROBOCOPY_FLAGS, progress_cb=item_cb)
                else:
                    src_dir = str(Path(s_display).parent)
                    _run_robocopy([src_dir, dst_display, name, f"/COPYALL", "/A-:SH"] + _ROBOCOPY_FLAGS, progress_cb=item_cb)
                    # rename to unique name
                    os.rename(
                        normalize(os.path.join(dst_display, name)),
                        normalize(unique),
                    )
            elif os.path.isdir(s):
                _run_robocopy([s_display, dest_display, "/E", "/COPY:DAT"] + _ROBOCOPY_FLAGS, progress_cb=item_cb)
            else:
                src_dir = str(Path(s_display).parent)
                try:
                    _run_robocopy([src_dir, dst_display, name, "/COPY:DAT"] + _ROBOCOPY_FLAGS, progress_cb=item_cb)
                except OSError as exc:
                    if not _is_file_exists_error(str(exc)):
                        raise
                    unique = _unique_copy_suffix_name(dst_display, name)
                    shutil.copy2(s, normalize(unique))
                    if item_cb is not None:
                        item_cb(100)
        else:
            dest = normalize(os.path.join(dst, os.path.basename(s)))
            if os.path.normcase(s) == os.path.normcase(dest):
                dest = normalize(_unique_copy_name(dst_display, os.path.basename(s)))
            if os.path.isdir(s):
                shutil.copytree(s, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(s, dest)
            if item_cb is not None:
                item_cb(100)


def move_items(
    src_paths: list[str],
    dst_dir: str,
    progress_cb: Callable[[int], None] | None = None,
) -> None:
    """Move (cut+paste) files/dirs into dst_dir. Uses robocopy /MOVE on Windows."""
    dst = normalize(dst_dir)
    dst_display = to_display(dst_dir)
    spans = _build_progress_spans(src_paths)

    for index, src in enumerate(src_paths):
        item_cb = _sub_progress(progress_cb, spans[index]) if index < len(spans) else progress_cb
        s = normalize(src)
        s_display = to_display(src)
        name = os.path.basename(s_display)

        if sys.platform == "win32":
            dest_display = os.path.join(dst_display, name)
            if os.path.normcase(s) == os.path.normcase(normalize(dest_display)):
                if item_cb is not None:
                    item_cb(100)
                continue  # moving a file to the same location is a no-op
            if os.path.isdir(s):
                _run_robocopy([s_display, dest_display, "/E", "/MOVE", "/COPY:DAT"] + _ROBOCOPY_FLAGS, progress_cb=item_cb)
                # robocopy /MOVE leaves empty source dir; clean it up
                try:
                    if os.path.exists(s):
                        shutil.rmtree(s)
                except OSError:
                    pass
            else:
                src_dir = str(Path(s_display).parent)
                _run_robocopy([src_dir, dst_display, name, "/MOV", "/COPY:DAT"] + _ROBOCOPY_FLAGS, progress_cb=item_cb)
        else:
            dest = normalize(os.path.join(dst, os.path.basename(s)))
            shutil.move(s, dest)
            if item_cb is not None:
                item_cb(100)


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
