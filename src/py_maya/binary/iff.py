"""IFF container of Maya Binary (`.mb`) files, 64-bit (`FOR8`) flavour.

Every `.mb` written by Maya 2018-2025 is a tree of big-endian chunks:

- A chunk header is 16 bytes: a 4-byte tag, 4 bytes Maya leaves
  uninitialised, and a u64 size. Maya ignores the 4 middle bytes when reading
  (zeroed copies open fine in Maya 2024); they are kept verbatim so that a
  round-trip is byte-identical.
- Group chunks (`FOR8`, `LIS8`, `CAT8`, `PRO8`) hold a 4-byte form type then
  child chunks. Their size covers the form type and the children, and they are
  never padded.
- Data chunks are NUL-padded to a multiple of 8 bytes counted from the start
  of their data, not from the start of the file. The size excludes the
  padding.

Measured on the 771 production `.mb` files saved by Maya 2018 to 2024.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING, Iterator, Union

if TYPE_CHECKING:
    from typing import BinaryIO

GROUP_TAGS = frozenset((b"FOR8", b"LIS8", b"CAT8", b"PRO8"))
ALIGNMENT = 8
_HEADER = struct.Struct(">4s4sQ")
_NO_RESERVED = b"\x00\x00\x00\x00"


class Chunk:
    """A data chunk: a tag and its raw bytes."""

    __slots__ = ("tag", "data", "reserved")

    def __init__(self, tag: bytes, data: bytes, reserved: bytes = _NO_RESERVED) -> None:
        self.tag = tag
        self.data = data
        self.reserved = reserved

    def __repr__(self) -> str:
        return f"Chunk({self.tag!r}, {len(self.data)} bytes)"


class GroupChunk:
    """A group chunk: a tag, a 4-byte form type and child chunks."""

    __slots__ = ("tag", "form_type", "chunks", "reserved")

    def __init__(
        self,
        tag: bytes,
        form_type: bytes,
        chunks: list[AnyChunk],
        reserved: bytes = _NO_RESERVED,
    ) -> None:
        self.tag = tag
        self.form_type = form_type
        self.chunks = chunks
        self.reserved = reserved

    def __iter__(self) -> Iterator[AnyChunk]:
        return iter(self.chunks)

    def __len__(self) -> int:
        return len(self.chunks)

    def __repr__(self) -> str:
        return (
            f"GroupChunk({self.tag!r}, {self.form_type!r}, {len(self.chunks)} chunks)"
        )


AnyChunk = Union[Chunk, GroupChunk]


def _padded(size: int) -> int:
    return -(-size // ALIGNMENT) * ALIGNMENT


def read_mb(data: bytes) -> GroupChunk:
    """Parse a whole `.mb` file into its root group (form type `Maya`).

    Raises `ValueError` for anything `write_mb` could not reproduce byte for
    byte: other IFF flavours, truncated or overrunning chunks, non-zero
    padding, or bytes after the root chunk.
    """
    if data[:4] not in GROUP_TAGS:
        raise ValueError(f"not a 64-bit Maya Binary file (starts with {data[:4]!r})")
    root, end = _read_chunk(data, 0, len(data))
    if end != len(data):
        raise ValueError(f"{len(data) - end} unexpected bytes after the root chunk")
    assert isinstance(root, GroupChunk)  # guaranteed by the GROUP_TAGS check above
    return root


def _read_chunk(data: bytes, pos: int, limit: int) -> tuple[AnyChunk, int]:
    if pos + _HEADER.size > limit:
        raise ValueError(f"truncated chunk header at {pos:#x}")
    tag, reserved, size = _HEADER.unpack_from(data, pos)
    body = pos + _HEADER.size
    end = body + size
    if end > limit:
        raise ValueError(f"chunk {tag!r} at {pos:#x} overruns its parent")
    if tag in GROUP_TAGS:
        if size < 4:
            raise ValueError(f"group {tag!r} at {pos:#x} has no form type")
        chunks = []
        child = body + 4
        while child < end:
            chunk, child = _read_chunk(data, child, end)
            chunks.append(chunk)
        return GroupChunk(tag, data[body : body + 4], chunks, reserved), end
    padded_end = body + _padded(size)
    if padded_end > limit:
        raise ValueError(f"chunk {tag!r} at {pos:#x}: padding overruns its parent")
    if data[end:padded_end].strip(b"\x00"):
        raise ValueError(f"chunk {tag!r} at {pos:#x}: non-zero padding")
    return Chunk(tag, data[body:end], reserved), padded_end


def write_mb(fp: BinaryIO, root: GroupChunk) -> int:
    """Write `root` to `fp`, recomputing every size; return the bytes written."""
    return _write_chunk(fp, root)


def _size(chunk: AnyChunk) -> int:
    if isinstance(chunk, GroupChunk):
        return 4 + sum(extent(child) for child in chunk.chunks)
    return len(chunk.data)


def extent(chunk: AnyChunk) -> int:
    """Bytes `chunk` occupies in a file: header, body and padding."""
    if isinstance(chunk, GroupChunk):
        return _HEADER.size + _size(chunk)
    return _HEADER.size + _padded(len(chunk.data))


def _write_chunk(fp: BinaryIO, chunk: AnyChunk) -> int:
    size = _size(chunk)
    fp.write(_HEADER.pack(chunk.tag, chunk.reserved, size))
    if isinstance(chunk, GroupChunk):
        fp.write(chunk.form_type)
        for child in chunk.chunks:
            _write_chunk(fp, child)
        return _HEADER.size + size
    fp.write(chunk.data)
    padding = _padded(size) - size
    fp.write(b"\x00" * padding)
    return _HEADER.size + size + padding


def walk(group: GroupChunk) -> Iterator[tuple[GroupChunk, AnyChunk]]:
    """Yield `(parent, chunk)` for every chunk below `group`, depth first."""
    for chunk in group.chunks:
        yield group, chunk
        if isinstance(chunk, GroupChunk):
            yield from walk(chunk)
