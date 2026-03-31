import os
import collections
from .core.longpath import normalize
from .settings import START_DIRS


class AppState:
    def __init__(self, start_path: str | None = None):
        if start_path and os.path.isdir(normalize(start_path)):
            start = start_path
        else:
            # Default to first valid start_dirs entry, then fall back to ~
            start = next(
                (p for p in START_DIRS if os.path.isdir(normalize(p))),
                os.path.expanduser("~"),
            )
        self.current_dir: str = normalize(start)
        self.nav_history: collections.deque = collections.deque(maxlen=10)
        # clipboard: {"mode": "copy"|"cut"|None, "paths": [...]}
        self.clipboard: dict = {"mode": None, "paths": []}
        self.selection: list = []

    def navigate_to(self, path: str) -> None:
        """Update current_dir and push to history. Deduplicated."""
        norm = normalize(path)
        # Push destination into history (skip if same as most recent)
        if not self.nav_history or self.nav_history[0] != norm:
            self.nav_history.appendleft(norm)
        self.current_dir = norm
        self.selection = []
