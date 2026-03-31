import argparse
from .app import App


def main():
    parser = argparse.ArgumentParser(
        prog="pyxplorer",
        description="Win11-style file explorer for the building industry",
    )
    parser.add_argument(
        "path", nargs="?", default=None, metavar="PATH",
        help="Directory to open on startup (also pinned as top entry in the left panel)",
    )
    args = parser.parse_args()
    App(start_path=args.path).run()


if __name__ == "__main__":
    main()
