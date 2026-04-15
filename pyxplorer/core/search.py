"""
Regex search across file/dir names. Stub — Phase 7 will wire this to the UI.
"""
import html as _html
import importlib
import os
import re
import queue
from .longpath import normalize


def parse_pdf_content(path: str, pattern: str, snippet_chars: int) -> tuple[list[str], int]:
    """Parse a single PDF and return (snippets, total_matches).

    Designed to run in a subprocess (via ProcessPoolExecutor) so that fitz's
    GIL-holding C code does not stall the tkinter main thread.
    All imports are done locally so this module is safe for workers to import.
    """
    try:
        fitz_mod = importlib.import_module("fitz")
    except ImportError:
        return [], 0
    try:
        rx = re.compile(pattern, re.IGNORECASE)
        doc = fitz_mod.open(path)
        snippets: list[str] = []
        total = 0
        ctx_before = max(10, snippet_chars // 3)
        ctx_after  = max(10, snippet_chars // 2)
        for page_num, page in enumerate(doc, 1):
            # xhtml mode correctly maps accented characters (é, â, î…)
            xhtml = page.get_text("xhtml")
            s = re.sub(r'</p>|</div>|<br[^>]*/?>', '\n', xhtml, flags=re.IGNORECASE)
            s = re.sub(r'<[^>]+>', '', s)
            text = _html.unescape(s)
            for m in rx.finditer(text):
                total += 1
                if len(snippets) < 5:
                    start = max(0, m.start() - ctx_before)
                    end   = min(len(text), m.end() + ctx_after)
                    raw   = text[start:end].replace("\n", " ").strip()
                    if len(raw) > snippet_chars:
                        raw = raw[:snippet_chars] + "…"
                    snippets.append(f"p.{page_num}: …{raw}…")
        doc.close()
        return snippets, total
    except Exception:
        return [], 0


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
