"""Nodes and their stored attribute values."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..binary import Chunk, GroupChunk
from ..binary.scene_chunks import (
    decode_crea,
    decode_string,
    decode_string_array,
    decode_text,
    encode_string,
    encode_string_array,
    format_uuid,
    join_attribute,
    split_attribute,
    type_id_of,
)
from ..data.node_types import DEFAULT_NODES

if TYPE_CHECKING:
    from .scene import Scene

# Chunks in a node group that are not attribute values: addAttr and aliasAttr
_NOT_VALUES = (b"ATTR", b"ATAL")
# CREA flag set on DAG nodes only (48k nodes checked against the type hierarchy,
# fixtures and production files)
_DAG_FLAG = 0x04
_INDEX = re.compile(r"\[[^\]]*\]")


class Attribute:
    """An attribute value stored on a node.

    `value` decodes the storage types py_maya understands so far: strings
    (`STR`) and string arrays (`STR#`).
    """

    def __init__(self, _chunk: Chunk, node: Node) -> None:
        self._chunk = _chunk
        self.node = node

    @property
    def name(self) -> str:
        """Name as stored: short name, with indices and parents (`uvst[0].uvsn`)."""
        return split_attribute(self._chunk.data)[0]

    @property
    def leaf_name(self) -> str:
        """Short name of the attribute itself, without indices or parents."""
        return _INDEX.sub("", self.name).split(".")[-1]

    @property
    def type(self) -> str:
        """Storage type: the chunk tag (`STR`, `STR#`, `DBLE`, `DBL3`, ...)."""
        return self._chunk.tag.decode("ascii").rstrip()

    @property
    def value(self) -> str | list[str]:
        _, _, raw = split_attribute(self._chunk.data)
        if self._chunk.tag == b"STR ":
            return decode_string(raw)
        if self._chunk.tag == b"STR#":
            return decode_string_array(raw)
        raise NotImplementedError(f"{self.type} values are not decoded yet")

    @value.setter
    def value(self, value: str | list[str]) -> None:
        name, flags, _ = split_attribute(self._chunk.data)
        if self._chunk.tag == b"STR " and isinstance(value, str):
            raw = encode_string(value)
        elif self._chunk.tag == b"STR#" and isinstance(value, list):
            if not all(isinstance(v, str) for v in value):
                raise TypeError("a string array value must be a list of str")
            raw = encode_string_array(value)
        else:
            raise TypeError(
                f"cannot store {type(value).__name__} in a {self.type} value"
            )
        self._chunk.data = join_attribute(name, flags, raw)

    def __repr__(self) -> str:
        return f"Attribute({self.node.name}.{self.name}, {self.type})"


class Node:
    """A node the file creates, or a default node whose attributes it edits."""

    def __init__(self, _group: GroupChunk, scene: Scene) -> None:
        self._group = _group
        self.scene = scene

    @property
    def is_default(self) -> bool:
        """True for a node the file does not create (a `select -ne` block)."""
        return self._group.form_type == b"SLCT"

    @property
    def name(self) -> str:
        """Name, with its namespace (`ns:target`)."""
        first = self._group.chunks[0]
        assert isinstance(first, Chunk)  # a node group starts with CREA or SLCT
        if self.is_default:
            # Stored with the root namespace prefix (`:time1`)
            return decode_text(first.data)[1:]
        return decode_crea(first.data).name

    @property
    def type_id(self) -> int | None:
        """Type id, or None for a default node (select blocks do not store it)."""
        return None if self.is_default else type_id_of(self._group.form_type)

    @property
    def type(self) -> str:
        """Type name, `0x...` for an unknown type id, `""` for an unknown default node."""
        if self.type_id is None:
            return DEFAULT_NODES.get(self.name, "")
        return self.scene._type_name(self.type_id)

    @property
    def uuid(self) -> str | None:
        if self.is_default:
            return None
        return format_uuid(self._crea_chunk_data()[-16:])

    @property
    def parent_name(self) -> str | None:
        """Parent as stored: a short name, or a DAG path when the name is ambiguous."""
        if self.is_default:
            return None
        return decode_crea(self._crea_chunk_data()).parent

    @property
    def parent(self) -> Node | None:
        """The parent node, or None for a root node or a parent outside this file."""
        parent_name = self.parent_name
        if parent_name is None:
            return None
        return self.scene._find(parent_name)

    @property
    def is_dag(self) -> bool:
        if self.is_default:
            return False
        return bool(self._crea_chunk_data()[0] & _DAG_FLAG)

    @property
    def path(self) -> str:
        """Long name: the full DAG path for DAG nodes, the name otherwise."""
        parent_name = self.parent_name
        if parent_name is None:
            return f"|{self.name}" if self.is_dag else self.name
        parent = self.scene._find(parent_name)
        if parent is not None:
            return f"{parent.path}|{self.name}"
        # Parent outside this file (a referenced node): the best known path
        prefix = parent_name if parent_name.startswith("|") else f"|{parent_name}"
        return f"{prefix}|{self.name}"

    @property
    def attributes(self) -> list[Attribute]:
        return [
            Attribute(chunk, self)
            for chunk in self._group.chunks[1:]
            if isinstance(chunk, Chunk) and chunk.tag not in _NOT_VALUES
        ]

    def attr(self, name: str) -> Attribute:
        """The stored attribute named `name` (as stored, e.g. `ftn` or `uvst[0].uvsn`)."""
        for attribute in self.attributes:
            if attribute.name == name:
                return attribute
        raise KeyError(f"{self.name} stores no value for {name!r}")

    def _crea_chunk_data(self) -> bytes:
        first = self._group.chunks[0]
        assert isinstance(first, Chunk)  # a node group starts with CREA
        return first.data

    def __repr__(self) -> str:
        return f"Node({self.path!r}, {self.type!r})"
