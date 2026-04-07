import argparse
import os
from .app import App
from .logging import set_verbose


def _sanitize_cli_path(path: str | None) -> str | None:
    if path is None:
        return None
    cleaned = path.strip()
    if not cleaned:
        return None
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ('"', "'"):
        cleaned = cleaned[1:-1].strip()
    if not cleaned:
        return None
    cleaned = os.path.expanduser(cleaned)
    return os.path.normpath(cleaned)


def main():
    parser = argparse.ArgumentParser(
        prog="pyxplorer",
        description="Win11-style file explorer for the building industry",
    )
    parser.add_argument(
        "path", nargs="?", default=None, metavar="PATH",
        help="Directory to open on startup (also pinned as top entry in the left panel)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose terminal logs for debugging",
    )
    args = parser.parse_args()
    set_verbose(args.verbose)
    App(start_path=_sanitize_cli_path(args.path)).run()


if __name__ == "__main__":
    main()
