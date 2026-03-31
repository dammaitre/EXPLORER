"""
Async recursive size scanner with cancellation support.

Phase 10 additions:
- Symlink directories are NOT recursed to avoid infinite loops.
- Network / UNC paths are skipped (scan emits size=-1 so the row stays "—").
"""
import os
import sys
import queue
import threading
from .longpath import normalize, to_display


class CancelToken:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


# ---------------------------------------------------------------------------
# Network / UNC path detection
# ---------------------------------------------------------------------------

def _is_network(path: str) -> bool:
    """
    Return True when path lives on a network or UNC location.
    Recursive size scanning on these can block for a long time.
    """
    display = to_display(path)
    # UNC paths always start with \\
    if display.startswith("\\\\"):
        return True
    # On Windows, check the drive type via the kernel API
    if sys.platform == "win32" and len(display) >= 2 and display[1] == ":":
        try:
            import ctypes
            DRIVE_REMOTE = 4
            drive = display[:3]  # e.g. "R:\\"
            return ctypes.windll.kernel32.GetDriveTypeW(drive) == DRIVE_REMOTE
        except Exception:
            pass
    return False


class SizeScanner:
    """
    Scans directory sizes in background threads.
    All results are pushed onto result_queue as:
        ("size_result",   item_path,   total_bytes)   — total_bytes=-1 means skipped
        ("scan_complete", parent_path)
    """

    def __init__(self, result_queue: queue.Queue):
        self.q = result_queue

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan_items(self, parent_path: str, subdir_paths: list[str],
                   token: CancelToken) -> None:
        """
        Scan every path in subdir_paths sequentially in one daemon thread.
        Emits ("size_result", path, size) for each, then ("scan_complete", parent_path).
        Network paths are skipped (emits size=-1 so the row stays "—").
        Sequential scan avoids thread-explosion on wide directories.
        """
        t = threading.Thread(
            target=self._items_worker,
            args=(parent_path, subdir_paths, token),
            daemon=True,
        )
        t.start()

    def scan(self, path: str, token: CancelToken) -> None:
        """Scan a single directory recursively (used by left-panel node annotation)."""
        t = threading.Thread(
            target=self._single_worker,
            args=(path, token),
            daemon=True,
        )
        t.start()

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------

    def _items_worker(self, parent_path: str, items: list[str],
                      token: CancelToken) -> None:
        for path in items:
            if token.cancelled:
                return
            if _is_network(path):
                # Emit -1 so the row keeps showing "—" (network size unknown)
                self.q.put(("size_result", path, -1))
                continue
            size = self._dir_size(path, token)
            if not token.cancelled:
                self.q.put(("size_result", path, size))
        if not token.cancelled:
            self.q.put(("scan_complete", parent_path))

    def _single_worker(self, path: str, token: CancelToken) -> None:
        if _is_network(path):
            return   # left panel node stays without size annotation
        size = self._dir_size(path, token)
        if not token.cancelled:
            self.q.put(("size_result", path, size))

    # ------------------------------------------------------------------
    # Recursive helper
    # ------------------------------------------------------------------

    def _dir_size(self, path: str, token: CancelToken) -> int:
        total = 0
        try:
            for entry in os.scandir(normalize(path)):
                if token.cancelled:
                    return 0
                try:
                    if entry.is_symlink():
                        # Count symlink target size for files but NEVER recurse
                        # into symlink directories — avoids infinite loops.
                        if entry.is_file(follow_symlinks=True):
                            try:
                                total += entry.stat(follow_symlinks=True).st_size
                            except OSError:
                                pass
                        # symlink dirs: skip (size contribution unknown / risk of loop)
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat().st_size
                    elif entry.is_dir(follow_symlinks=False):
                        total += self._dir_size(entry.path, token)
                except (PermissionError, OSError):
                    pass
        except (PermissionError, OSError):
            pass
        return total
