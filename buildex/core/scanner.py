"""
Async recursive size scanner with cancellation support.
"""
import os
import queue
import threading
from .longpath import normalize


class CancelToken:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class SizeScanner:
    """
    Scans directory sizes in background threads.
    All results are pushed onto result_queue as:
        ("size_result",   item_path,   total_bytes)
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
            size = self._dir_size(path, token)
            if not token.cancelled:
                self.q.put(("size_result", path, size))
        if not token.cancelled:
            self.q.put(("scan_complete", parent_path))

    def _single_worker(self, path: str, token: CancelToken) -> None:
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
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat().st_size
                    elif entry.is_dir(follow_symlinks=False):
                        total += self._dir_size(entry.path, token)
                except (PermissionError, OSError):
                    pass
        except (PermissionError, OSError):
            pass
        return total
