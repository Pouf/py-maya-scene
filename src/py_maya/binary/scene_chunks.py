"""Payload layouts of the `.mb` chunks that describe a scene.

Decoders return the fields py_maya uses; encoders rebuild the payload from
them, byte for byte. Text is decoded as UTF-8 with `surrogateescape`, so any
byte sequence survives a decode/encode cycle. Layouts are documented in
`docs/file_format.md`.
"""

from __future__ import annotations

import struct
from typing import NamedTuple

UUID_SIZE = 16
_U32 = struct.Struct(">I")
_UUID_WORDS = struct.Struct("<4I")


def decode_text(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def encode_text(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


class Crea(NamedTuple):
    """`CREA`: the node a group creates."""

    flags: int
    name: str
    parent: str | None
    uuid: bytes


def decode_crea(data: bytes) -> Crea:
    strings = data[1:-UUID_SIZE].split(b"\x00")
    if len(strings) not in (2, 3) or strings[-1]:
        raise ValueError(f"unexpected CREA layout: {data!r}")
    parent = decode_text(strings[1]) if len(strings) == 3 else None
    return Crea(data[0], decode_text(strings[0]), parent, data[-UUID_SIZE:])


def encode_crea(crea: Crea) -> bytes:
    parent = b"" if crea.parent is None else encode_text(crea.parent) + b"\x00"
    return bytes((crea.flags,)) + encode_text(crea.name) + b"\x00" + parent + crea.uuid


def format_uuid(raw: bytes) -> str:
    """Maya's text form of a node UUID: four little-endian u32 words."""
    a, b, c, d = _UUID_WORDS.unpack(raw)
    return (
        f"{a:08X}-{b >> 16:04X}-{b & 0xFFFF:04X}-{c >> 16:04X}-{c & 0xFFFF:04X}{d:08X}"
    )


def split_attribute(data: bytes) -> tuple[str, int, bytes]:
    """Split an attribute chunk into its name, flag byte and value bytes."""
    nul = data.index(b"\x00")
    return decode_text(data[:nul]), data[nul + 1], data[nul + 2 :]


def join_attribute(name: str, flags: int, value: bytes) -> bytes:
    return encode_text(name) + b"\x00" + bytes((flags,)) + value


def decode_string(value: bytes) -> str:
    """`STR ` value: NUL-terminated text."""
    if not value.endswith(b"\x00"):
        raise ValueError(f"unterminated string value: {value!r}")
    return decode_text(value[:-1])


def encode_string(text: str) -> bytes:
    return encode_text(text) + b"\x00"


def decode_string_array(value: bytes) -> list[str]:
    """`STR#` value: a u32 count, then that many NUL-terminated strings."""
    (count,) = _U32.unpack_from(value)
    strings = value[4:].split(b"\x00")
    if len(strings) != count + 1 or strings[-1]:
        raise ValueError(f"unexpected string array layout: {value!r}")
    return [decode_text(s) for s in strings[:-1]]


def encode_string_array(texts: list[str]) -> bytes:
    return _U32.pack(len(texts)) + b"".join(encode_string(t) for t in texts)


def split_reference(data: bytes, with_depth: bool) -> tuple[bytes, str, bytes]:
    """Split a `FREF` (or `FRDI`, which starts with a u32 depth) payload.

    Returns the bytes before the path, the path, and the bytes after it
    (starting with the path's NUL).
    """
    start = 4 if with_depth else 0
    nul = data.index(b"\x00", start)
    return data[:start], decode_text(data[start:nul]), data[nul:]


class ReferenceFields(NamedTuple):
    """Fields of a `FREF`/`FRDI` payload."""

    depth: int
    path: str
    namespace: str
    deferred: bool
    node: str


def decode_reference(data: bytes, with_depth: bool) -> ReferenceFields:
    """`[u32 depth] path\\0 namespace\\0 flag deferred-flag refNode\\0 ...`.

    `FREF` has no depth (its references are all depth 1). The second flag byte
    is 1 for `-dr 1`.
    """
    start = 4 if with_depth else 0
    depth = _U32.unpack_from(data)[0] if with_depth else 1
    path_end = data.index(b"\x00", start)
    namespace_end = data.index(b"\x00", path_end + 1)
    node_start = namespace_end + 3
    node_end = data.index(b"\x00", node_start)
    return ReferenceFields(
        depth,
        decode_text(data[start:path_end]),
        decode_text(data[path_end + 1 : namespace_end]),
        data[namespace_end + 2] == 1,
        decode_text(data[node_start:node_end]),
    )


def decode_includes(data: bytes) -> list[str]:
    """`INCL`: paths, each followed by `(`."""
    return [decode_text(p) for p in data.split(b"(")[:-1]]


def encode_includes(paths: list[str]) -> bytes:
    return b"".join(encode_text(p) + b"(" for p in paths)


class Plugin(NamedTuple):
    """`PLUG`: a required plugin and the ids of the node types it provides."""

    name: str
    version: str
    requires: str
    node_types: dict[int, str]


def decode_plugin(data: bytes) -> Plugin:
    name, version, requires, rest = data.split(b"\x00", 3)
    node_types = {}
    while rest:
        nul = rest.index(b"\x00")
        (type_id,) = _U32.unpack_from(rest, nul + 1)
        node_types[type_id] = decode_text(rest[:nul])
        rest = rest[nul + 5 :]
    return Plugin(
        decode_text(name), decode_text(version), decode_text(requires), node_types
    )


def decode_file_info(data: bytes) -> tuple[str, str]:
    """`FINF`: `key\\0value\\0`."""
    key, value, _ = data.split(b"\x00")
    return decode_text(key), decode_text(value)


def decode_connection(data: bytes) -> tuple[int, str, str]:
    """`CWFL`: flag byte, source plug, destination plug."""
    source, destination, _ = data[1:].split(b"\x00")
    return data[0], decode_text(source), decode_text(destination)


def decode_relationship(data: bytes) -> tuple[str, str, list[str]]:
    """`RELA`: kind, node, u32 count, then that many plugs."""
    kind_end = data.index(b"\x00")
    node_end = data.index(b"\x00", kind_end + 1)
    (count,) = _U32.unpack_from(data, node_end + 1)
    plugs = data[node_end + 5 :].split(b"\x00")[:count]
    return (
        decode_text(data[:kind_end]),
        decode_text(data[kind_end + 1 : node_end]),
        [decode_text(p) for p in plugs],
    )


def encode_u32(value: int) -> bytes:
    return _U32.pack(value)


def type_id_of(form_type: bytes) -> int:
    """Node type id stored as a group's form type (big-endian u32)."""
    return int.from_bytes(form_type, "big")
