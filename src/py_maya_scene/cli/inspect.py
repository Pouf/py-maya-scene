"""
Maya Binary chunk inspection tool.

Modes:
    Default:  maya-inspect scene.mb               (chunk tree with data previews)
    Depth:    maya-inspect scene.mb --depth 1     (top-level forms only)
    Summary:  maya-inspect scene.mb --summary     (chunk counts by tag)
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from ..binary import Chunk, GroupChunk, read_mb, walk


def _label(tag: bytes) -> str:
    if all(32 <= byte < 127 for byte in bytearray(tag)):
        return tag.decode("ascii")
    return "0x" + tag.hex()


def _print_tree(
    group: GroupChunk, depth: int, max_depth: int | None, preview: int
) -> None:
    indent = "  " * depth
    for chunk in group.chunks:
        if isinstance(chunk, GroupChunk):
            print(
                f"{indent}{_label(chunk.tag)} {_label(chunk.form_type)} ({len(chunk)} chunks)"
            )
            if max_depth is None or depth + 1 < max_depth:
                _print_tree(chunk, depth + 1, max_depth, preview)
        else:
            shown = chunk.data[:preview]
            more = "..." if len(chunk.data) > preview else ""
            print(f"{indent}{_label(chunk.tag)} {len(chunk.data)} {shown!r}{more}")


def _print_summary(root: GroupChunk) -> None:
    counts: Counter[str] = Counter()
    for _, chunk in walk(root):
        if isinstance(chunk, Chunk):
            counts[_label(chunk.tag)] += 1
        else:
            counts[f"{_label(chunk.tag)} {_label(chunk.form_type)}"] += 1
    for label, count in counts.most_common():
        print(f"{count:>10}  {label}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="maya-inspect",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", type=Path, help=".mb file")
    parser.add_argument("--depth", type=int, default=None, help="maximum tree depth")
    parser.add_argument(
        "--preview", type=int, default=48, help="data bytes shown per chunk"
    )
    parser.add_argument("--summary", action="store_true", help="chunk counts by tag")
    args = parser.parse_args(argv)
    root = read_mb(args.path.read_bytes())
    print(f"{args.path.name}: {_label(root.tag)} {_label(root.form_type)}")
    if args.summary:
        _print_summary(root)
    else:
        _print_tree(root, 0, args.depth, args.preview)
    return 0


if __name__ == "__main__":
    sys.exit(main())
