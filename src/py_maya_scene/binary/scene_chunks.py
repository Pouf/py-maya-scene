"""Payload layouts of the `.mb` chunks that describe a scene.

Decoders return the fields py_maya_scene uses; encoders rebuild the payload from
them, byte for byte. Text is decoded as UTF-8 with `surrogateescape`, so any
byte sequence survives a decode/encode cycle. Layouts are documented in
`docs/file_format.md`.
"""

from __future__ import annotations

import struct
from typing import Callable, Iterator, List, NamedTuple, Tuple

UUID_SIZE = 16
_U32 = struct.Struct(">I")
_DOUBLE = struct.Struct(">d")
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


def decode_double(value: bytes) -> float:
    """`DBLE` value holding one double (also booleans, integers and enums;
    times are integer ticks under the same tag)."""
    if len(value) != 8:
        raise ValueError(f"not a single double: {value!r}")
    return float(_DOUBLE.unpack(value)[0])


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


def reference_file_type(data: bytes) -> str:
    """File type of a `FREF`/`FRDI` payload (`mayaBinary`, `mayaAscii`): the
    last string, followed by fixed NUL bytes (3 in `FRDI`, 12 in `FREF`)."""
    stripped = data.rstrip(b"\x00")
    return decode_text(stripped[stripped.rfind(b"\x00") + 1 :])


def replace_reference_file_type(data: bytes, file_type: str) -> bytes:
    stripped = data.rstrip(b"\x00")
    start = stripped.rfind(b"\x00") + 1
    return data[:start] + encode_text(file_type) + data[len(stripped) :]


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


def replace_reference_strings(
    data: bytes, with_depth: bool, replace: Callable[[str], str]
) -> bytes:
    """A `FREF`/`FRDI` payload with its path, namespace, reference node and
    `-op` options string (the string before the file type, empty for a
    deferred reference) put through `replace`; every other byte is kept."""
    start = 4 if with_depth else 0
    path_end = data.index(b"\x00", start)
    namespace_end = data.index(b"\x00", path_end + 1)
    node_start = namespace_end + 3
    node_end = data.index(b"\x00", node_start)
    stripped = data.rstrip(b"\x00")
    options_end = stripped.rfind(b"\x00")
    options_start = data.rfind(b"\x00", node_end, options_end) + 1

    def swap(begin: int, end: int) -> bytes:
        return encode_text(replace(decode_text(data[begin:end])))

    return b"".join(
        (
            data[:start],
            swap(start, path_end),
            data[path_end : path_end + 1],
            swap(path_end + 1, namespace_end),
            data[namespace_end:node_start],
            swap(node_start, node_end),
            data[node_end:options_start],
            swap(options_start, options_end),
            data[options_end:],
        )
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


def encode_file_info(key: str, value: str) -> bytes:
    return encode_string(key) + encode_string(value)


def decode_connection(data: bytes) -> tuple[int, str, str]:
    """`CWFL`: flag byte, source plug, destination plug."""
    source, destination, _ = data[1:].split(b"\x00")
    return data[0], decode_text(source), decode_text(destination)


def encode_connection(flags: int, source: str, destination: str) -> bytes:
    return bytes((flags,)) + encode_string(source) + encode_string(destination)


def decode_instance(data: bytes) -> tuple[int, str, str]:
    """`PRNT` (`parent -add`): flag byte, instanced node path, extra parent."""
    child, parent, _ = data[1:].split(b"\x00")
    return data[0], decode_text(child), decode_text(parent)


def encode_instance(flags: int, child: str, parent: str) -> bytes:
    return bytes((flags,)) + encode_string(child) + encode_string(parent)


def decode_relationship(data: bytes) -> tuple[str, str, list[str]]:
    """`RELA`: kind, node, u32 count, then that many plugs."""
    cursor = _Cursor(data)
    kind, node = cursor.string(), cursor.string()
    plugs = [cursor.string() for _ in range(cursor.u32())]
    if cursor.pos != len(data):
        raise ValueError(f"unexpected bytes after the relationship plugs: {data!r}")
    return kind, node, plugs


def encode_relationship(kind: str, node: str, plugs: list[str]) -> bytes:
    return (
        encode_string(kind)
        + encode_string(node)
        + _U32.pack(len(plugs))
        + b"".join(encode_string(p) for p in plugs)
    )


def encode_u32(value: int) -> bytes:
    return _U32.pack(value)


_METADATA_HEAD = b"\x00raw\x00"


def decode_metadata(data: bytes) -> str:
    """`SMTD`: `\\0raw\\0`, metadata text in Maya's raw format (UTF-8), `\\0`."""
    if not (data.startswith(_METADATA_HEAD) and data.endswith(b"\x00")):
        raise ValueError(f"unexpected metadata layout: {data[:16]!r}")
    return decode_text(data[len(_METADATA_HEAD) : -1])


def encode_metadata(text: str) -> bytes:
    return _METADATA_HEAD + encode_text(text) + b"\x00"


# Edit kind > number of strings after it
REFERENCE_EDIT_STRINGS = {
    0: 3,  # parent: node, new parent, flags
    1: 4,  # addAttr: node, long name, short name, arguments
    2: 3,  # setAttr: node, attribute, arguments
    3: 3,  # disconnectAttr: source plug, destination plug, flags
    4: 3,  # deleteAttr: node, attribute, flags
    8: 2,  # lock attribute: node, attribute
    9: 2,  # unlock attribute: node, attribute
}
# Kind 5, connectAttr through placeholders, starts with a u32 side: 3 or 4 when
# one end is referenced (reference node, plug, placeholder, extra), 0 when both
# are (reference node, source, destination, two placeholders, extra)
_CONNECTION_EDIT = 5


class ReferenceEdit(NamedTuple):
    """One reference edit. `side` (kind 5 only): 3 when the referenced plug is
    the source of the connection, 4 when it is the destination, 0 when both
    ends are referenced."""

    kind: int
    side: int | None
    strings: list[str]


EditGroup = Tuple[str, List[ReferenceEdit]]


class ReferenceEdits(NamedTuple):
    """`REFE` value: the owning reference node, then two sections of edits
    grouped by the reference node they apply to. Both sections share one
    layout; what sends an edit to one or the other is not known yet."""

    owner: str
    sections: list[list[EditGroup]]

    def all_edits(self) -> Iterator[tuple[str, ReferenceEdit]]:
        for section in self.sections:
            for node, edits in section:
                for edit in edits:
                    yield node, edit


class _Cursor:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def string(self) -> str:
        end = self.data.index(b"\x00", self.pos)
        text = decode_text(self.data[self.pos : end])
        self.pos = end + 1
        return text

    def u32(self) -> int:
        value = int.from_bytes(self.data[self.pos : self.pos + 4], "big")
        self.pos += 4
        return value


def _decode_edit(cursor: _Cursor, owner: str) -> ReferenceEdit:
    kind = cursor.u32()
    side = None
    if kind == _CONNECTION_EDIT:
        side = cursor.u32()
        count = 6 if side == 0 else 4
    elif kind in REFERENCE_EDIT_STRINGS:
        count = REFERENCE_EDIT_STRINGS[kind]
    else:
        raise ValueError(f"unknown reference edit kind {kind} in {owner}")
    return ReferenceEdit(kind, side, [cursor.string() for _ in range(count)])


def _decode_section(cursor: _Cursor, owner: str) -> list[EditGroup]:
    groups = []
    for _ in range(cursor.u32()):
        node = cursor.string()
        groups.append(
            (node, [_decode_edit(cursor, owner) for _ in range(cursor.u32())])
        )
    return groups


def decode_reference_edits(value: bytes) -> ReferenceEdits:
    """Decode a `REFE` value; raises `ValueError` on an edit kind not yet known."""
    cursor = _Cursor(value)
    owner = cursor.string()
    sections = [_decode_section(cursor, owner) for _ in range(2)]
    if cursor.pos != len(value):
        raise ValueError(
            f"{len(value) - cursor.pos} unexpected bytes after the edits of {owner}"
        )
    return ReferenceEdits(owner, sections)


def encode_reference_edits(edits: ReferenceEdits) -> bytes:
    parts = [encode_string(edits.owner)]
    for section in edits.sections:
        parts.append(_U32.pack(len(section)))
        for node, node_edits in section:
            parts += [encode_string(node), _U32.pack(len(node_edits))]
            for edit in node_edits:
                parts.append(_U32.pack(edit.kind))
                if edit.side is not None:
                    parts.append(_U32.pack(edit.side))
                parts += [encode_string(s) for s in edit.strings]
    return b"".join(parts)


def type_id_of(form_type: bytes) -> int:
    """Node type id stored as a group's form type (big-endian u32)."""
    return int.from_bytes(form_type, "big")
