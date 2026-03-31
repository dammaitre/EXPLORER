"""
Global keyboard shortcuts bound on the root Tk window.
Phase 6 will wire Ctrl+C/X/V, Ctrl+Shift+N, Ctrl+F, Ctrl+Alt+T.
"""


def bind_keys(root, state, top_bar, main_frame) -> None:
    """Attach all application-wide shortcuts to root."""
    root.bind("<Control-r>", lambda e: (top_bar.open_run_dialog(), "break")[1])

    # Navigation — fire only when the path entry does NOT have focus,
    # so typing in the entry is never hijacked.
    def _nav(fn):
        def handler(e):
            if str(root.focus_get()).endswith("entry"):
                return
            return fn(e)
        return handler

    root.bind("<Left>",      _nav(lambda e: main_frame._go_up()))
    root.bind("<BackSpace>", _nav(lambda e: main_frame._go_up()))
    root.bind("<Right>",     _nav(lambda e: main_frame._open_selected()))
