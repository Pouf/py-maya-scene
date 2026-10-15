"""Binary I/O layer for Maya Binary (`.mb`) files."""

from .iff import (
    GROUP_TAGS,
    AnyChunk,
    Chunk,
    GroupChunk,
    extent,
    read_mb,
    walk,
    write_mb,
)

__all__ = [
    "GROUP_TAGS",
    "AnyChunk",
    "Chunk",
    "GroupChunk",
    "extent",
    "read_mb",
    "walk",
    "write_mb",
]
