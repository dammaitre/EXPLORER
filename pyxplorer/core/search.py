"""
Regex search across file/dir names. Stub — Phase 7 will wire this to the UI.
"""
import os
import re
import queue
from .longpath import normalize


def search_names(
    root_dir: str,
    pattern: str,
    result_queue: queue.Queue,
    token,
    max_results: int | None = None,
) -> None:
    """
    Walk root_dir, match file/dir names against pattern, push results to queue.
    Results: ("search_result", name, rel_path, "dir"|"file")
    Done:    ("search_done", truncated: bool)
    Error:   ("search_error", message)
    """
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        result_queue.put(("search_error", str(e)))
        return

    match_count = 0
    truncated = False

    for dirpath, dirnames, filenames in os.walk(normalize(root_dir)):
        if token.cancelled:
            return
        for name in dirnames + filenames:
            if token.cancelled:
                return
            if rx.search(name):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root_dir)
                kind = "dir" if name in dirnames else "file"
                result_queue.put(("search_result", name, rel, kind))
                match_count += 1
                if isinstance(max_results, int) and max_results > 0 and match_count >= max_results:
                    truncated = True
                    result_queue.put(("search_done", truncated))
                    return

    result_queue.put(("search_done", truncated))
