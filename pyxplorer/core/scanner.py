r"""
Async recursive size scanner with cancellation support.

Symlink / junction-point policy (Windows):
- is_dir(follow_symlinks=False) == True  →  regular dirs AND NTFS junction points.
  Both are recursed; junction points do not create cycles in typical share layouts.
- is_dir(follow_symlinks=False) == False, is_symlink() == True  →  NTFS symlink-to-dir.
  These ARE skipped to prevent infinite recursion.
- is_file(follow_symlinks=False) == True  →  regular files (not symlinks).
- is_symlink() == True, is_file(follow_symlinks=True) == True  →  symlink to file.
  The target's size is counted.

Network policy:
- Only raw UNC paths (\\server\share\...) are skipped.
- Mapped drive letters (R:\, S:\, …) are scanned normally — the user has
  explicitly added them to start_dirs and expects sizes to be computed.
"""
import os
import queue
import threading
from .longpath import normalize, to_display
from ..settings import EXT_SKIPPED, SCAN_SKIP_DIRS


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
    Return True only for raw UNC paths (\\\\server\\share\\...).
    Mapped drive letters are NOT flagged — they are user-configured and
    the scanner is expected to work on them.
    """
    return to_display(path).startswith("\\\\")


def _norm_for_match(path: str) -> str:
    return os.path.normcase(os.path.normpath(to_display(path)))


_SCAN_SKIP_DIRS_NORM = [_norm_for_match(p) for p in SCAN_SKIP_DIRS]


def _is_scan_skipped(path: str) -> bool:
    """
    Check if a path should be skipped during scanning.
    
    Inverse logic (changed from original):
    - If path == scan_skip_dir entry → skip
    - If path is a PARENT of scan_skip_dir entry → skip (prevent scans near root)
    - If path is a CHILD of scan_skip_dir entry → OK to scan (allow scans within subtrees)
    """
    candidate = _norm_for_match(path)
    for root in _SCAN_SKIP_DIRS_NORM:
        # Exact match: skip
        if candidate == root:
            return True
        # candidate is a parent of root: skip
        # (e.g., if root is "A:\B\C" and candidate is "A:\B", skip candidate)
        try:
            if os.path.commonpath([candidate, root]) == candidate:
                return True
        except ValueError:
            # Different drives or otherwise incomparable paths: not a match.
            continue
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
        True UNC paths are skipped (emits size=-1 so the row stays "—").
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
            if _is_network(path) or _is_scan_skipped(path):
                # True UNC path — emit -1 so the row keeps showing "—"
                self.q.put(("size_result", path, -1))
                continue
            size = self._dir_size(path, token)
            if not token.cancelled:
                self.q.put(("size_result", path, size))
        if not token.cancelled:
            self.q.put(("scan_complete", parent_path))

    def _single_worker(self, path: str, token: CancelToken) -> None:
        if _is_network(path) or _is_scan_skipped(path):
            return
        size = self._dir_size(path, token)
        if not token.cancelled:
            self.q.put(("size_result", path, size))

    # ------------------------------------------------------------------
    # Recursive helper
    # ------------------------------------------------------------------

    def _dir_size(self, path: str, token: CancelToken) -> int:
        if _is_scan_skipped(path):
            return 0
        total = 0
        try:
            for entry in os.scandir(normalize(path)):
                if token.cancelled:
                    return 0
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if _is_scan_skipped(entry.path):
                            continue
                        # Regular directories AND NTFS junction points.
                        # follow_symlinks=False means NTFS symlinks-to-dirs evaluate
                        # to False here, so they are NOT recursed (cycle prevention).
                        total += self._dir_size(entry.path, token)
                    elif entry.is_file(follow_symlinks=False):
                        # Regular file (not a symlink) — skip filtered extensions.
                        if not EXT_SKIPPED or \
                                os.path.splitext(entry.name)[1].lower() not in EXT_SKIPPED:
                            total += entry.stat().st_size
                    elif entry.is_symlink() and entry.is_file(follow_symlinks=True):
                        # Symlink pointing to a file — same extension filter applies.
                        if not EXT_SKIPPED or \
                                os.path.splitext(entry.name)[1].lower() not in EXT_SKIPPED:
                            try:
                                total += entry.stat(follow_symlinks=True).st_size
                            except OSError:
                                pass
                    # Anything else (symlink-to-dir, unknown type) is skipped.
                except (PermissionError, OSError):
                    pass
        except (PermissionError, OSError):
            pass
        return total
