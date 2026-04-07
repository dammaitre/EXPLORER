import argparse
from .app import App
from .logging import set_verbose


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
    App(start_path=args.path).run()


if __name__ == "__main__":
    main()
