"""Command line: python -m app.cli --store FILE add TITLE | list"""

from __future__ import annotations

import argparse
import sys

from . import store


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="todo")
    parser.add_argument("--store", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("add").add_argument("title")
    sub.add_parser("list")
    args = parser.parse_args(argv)
    if args.command == "add":
        item = store.add(args.store, args.title)
        print(f"{item['id']} {item['title']}")
    else:
        for item in store.load(args.store):
            print(f"{item['id']} {item['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
