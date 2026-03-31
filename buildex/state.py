import os
import collections
from core.longpath import normalize


class AppState:
    def __init__(self):
        start = os.path.expanduser("~")
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
