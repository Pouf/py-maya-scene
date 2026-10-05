"""Scene records over a Maya Binary (`.mb`) chunk tree."""

from __future__ import annotations

import io

from ..binary import Chunk, GroupChunk, extent, read_mb, write_mb
from ..binary.scene_chunks import (
    Crea,
    ReferenceEdits,
    decode_connection,
    decode_crea,
    decode_file_info,
    decode_includes,
    decode_instance,
    decode_metadata,
    decode_plugin,
    decode_reference,
    decode_reference_edits,
    decode_relationship,
    decode_string,
    decode_string_array,
    decode_text,
    encode_connection,
    encode_crea,
    encode_includes,
    encode_instance,
    encode_metadata,
    encode_reference_edits,
    encode_relationship,
    encode_string,
    encode_string_array,
    encode_text,
    encode_u32,
    format_uuid,
    join_attribute,
    reference_file_type,
    replace_reference_file_type,
    split_attribute,
    split_reference,
    type_id_of,
)
from ..data.node_types import DEFAULT_NODES, TYPE_NAMES
from ..metadata import CODEPAGE
from .base import (
    AttributeRecord,
    AttributeValue,
    ConnectionRecord,
    InstanceRecord,
    NodeRecord,
    Plugin,
    ReferenceRecord,
    RelationshipRecord,
    SceneStorage,
    file_type_for,
)

# Root groups that are not nodes; PING holds the extra parents of instancing
_NOT_NODES = (b"HEAD", b"FRDI", b"FREF", b"PING")
# Chunks in a node group that are not attribute values: addAttr and aliasAttr
# Chunks in a node group that are not attribute values: addAttr and aliasAttr
_NOT_VALUES = (b"ATTR", b"ATAL")
_STRING_TAGS = (b"STR ", b"STR#")
# CREA flags set on DAG nodes and on transforms only (48k nodes checked
# against the type hierarchy, fixtures and production files)
_DAG_FLAG = 0x04
_TRANSFORM_FLAG = 0x08


class MbAttribute(AttributeRecord):
    def __init__(self, chunk: Chunk) -> None:
        self._chunk = chunk

    @property
    def name(self) -> str:
        return split_attribute(self._chunk.data)[0]

    @property
    def type(self) -> str:
        return self._chunk.tag.decode("ascii").rstrip()

    def get_value(self) -> AttributeValue:
        _, _, raw = split_attribute(self._chunk.data)
        if self._chunk.tag == b"STR ":
            return decode_string(raw)
        if self._chunk.tag == b"STR#":
            return decode_string_array(raw)
        if self._chunk.tag == b"REFE":
            return decode_reference_edits(raw)
        raise NotImplementedError(f"{self.type} values are not decoded yet")

    def set_value(self, value: AttributeValue) -> None:
        name, flags, _ = split_attribute(self._chunk.data)
        if self._chunk.tag == b"STR " and isinstance(value, str):
            raw = encode_string(value)
        elif self._chunk.tag == b"STR#" and isinstance(value, list):
            raw = encode_string_array(value)
        elif self._chunk.tag == b"REFE" and isinstance(value, ReferenceEdits):
            raw = encode_reference_edits(value)
        else:
            raise TypeError(
                f"cannot store {type(value).__name__} in a {self.type} value"
            )
        self._chunk.data = join_attribute(name, flags, raw)

    def mentions(self, text: str) -> bool:
        return encode_text(text) in self._chunk.data


class MbNode(NodeRecord):
    def __init__(self, group: GroupChunk, storage: MbScene) -> None:
        self._group = group
        self._storage = storage
        # Decoded CREA, kept because names are read millions of times on big files
        self._crea_cache: Crea | None = None

    def _first(self) -> Chunk:
        chunk = self._group.chunks[0]
        assert isinstance(chunk, Chunk)  # a node group starts with CREA or SLCT
        return chunk

    def _crea(self) -> Crea:
        if self._crea_cache is None:
            self._crea_cache = decode_crea(self._first().data)
        return self._crea_cache

    def _store_crea(self, crea: Crea) -> None:
        self._first().data = encode_crea(crea)
        self._crea_cache = crea

    @property
    def is_default(self) -> bool:
        return self._group.form_type == b"SLCT"

    @property
    def name(self) -> str:
        if self.is_default:
            # Stored with the root namespace prefix (`:time1`)
            return decode_text(self._first().data)[1:]
        return self._crea().name

    def set_name(self, value: str) -> None:
        self._store_crea(self._crea()._replace(name=value))

    @property
    def parent_name(self) -> str | None:
        return None if self.is_default else self._crea().parent

    def set_parent_name(self, value: str) -> None:
        self._store_crea(self._crea()._replace(parent=value))

    @property
    def type_id(self) -> int | None:
        return None if self.is_default else type_id_of(self._group.form_type)

    @property
    def type_name(self) -> str:
        type_id = self.type_id
        if type_id is None:
            return DEFAULT_NODES.get(self.name, "")
        name = self._storage._plugin_types.get(type_id) or TYPE_NAMES.get(type_id)
        return name if name is not None else f"0x{type_id:08X}"

    @property
    def uuid(self) -> str | None:
        return None if self.is_default else format_uuid(self._crea().uuid)

    @property
    def is_dag(self) -> bool:
        return not self.is_default and bool(self._crea().flags & _DAG_FLAG)

    @property
    def is_transform(self) -> bool:
        return not self.is_default and bool(self._crea().flags & _TRANSFORM_FLAG)

    def attributes(self) -> list[AttributeRecord]:
        return [
            MbAttribute(chunk)
            for chunk in self._group.chunks[1:]
            if isinstance(chunk, Chunk) and chunk.tag not in _NOT_VALUES
        ]

    def string_attributes(self) -> list[AttributeRecord]:
        # Tag test first: decoding every attribute name is the cost on big files
        return [
            MbAttribute(chunk)
            for chunk in self._group.chunks[1:]
            if isinstance(chunk, Chunk) and chunk.tag in _STRING_TAGS
        ]


class MbReference(ReferenceRecord):
    """Backed by `FRDI` (every depth); depth 1 also has a `FREF` and an `INCL` entry."""

    def __init__(self, chunk: Chunk, storage: MbScene) -> None:
        self._chunk = chunk
        self._storage = storage

    @property
    def path(self) -> str:
        return decode_reference(self._chunk.data, True).path

    def set_path(self, value: str) -> None:
        old = self.path
        _store_reference_path(self._chunk, True, value)
        if self.depth != 1:
            return
        frefs = self._storage._frefs
        index = [decode_reference(c.data, False).node for c in frefs].index(self.node)
        _store_reference_path(frefs[index], False, value)
        include = self._storage._trailer_chunk(b"INCL")
        paths = decode_includes(include.data)
        # The trailer lists the top-level references in FREF order; leave it
        # alone if a file ever breaks that rule rather than guess.
        if index < len(paths) and paths[index] == old:
            paths[index] = value
            include.data = encode_includes(paths)

    @property
    def namespace(self) -> str:
        return decode_reference(self._chunk.data, True).namespace

    @property
    def node(self) -> str:
        return decode_reference(self._chunk.data, True).node

    @property
    def depth(self) -> int:
        return decode_reference(self._chunk.data, True).depth

    @property
    def deferred(self) -> bool:
        return decode_reference(self._chunk.data, True).deferred

    @property
    def file_type(self) -> str:
        return reference_file_type(self._chunk.data)


class MbConnection(ConnectionRecord):
    def __init__(self, chunk: Chunk) -> None:
        self._chunk = chunk

    @property
    def source(self) -> str:
        return decode_connection(self._chunk.data)[1]

    @property
    def destination(self) -> str:
        return decode_connection(self._chunk.data)[2]

    @property
    def next_available(self) -> bool:
        return bool(self._chunk.data[0] & 1)

    def set_plugs(self, source: str, destination: str) -> None:
        self._chunk.data = encode_connection(self._chunk.data[0], source, destination)

    def mentions(self, text: str) -> bool:
        return encode_text(text) in self._chunk.data


class MbInstance(InstanceRecord):
    """`PRNT`, in a root `PING` group."""

    def __init__(self, chunk: Chunk) -> None:
        self._chunk = chunk

    @property
    def child(self) -> str:
        return decode_instance(self._chunk.data)[1]

    @property
    def parent(self) -> str:
        return decode_instance(self._chunk.data)[2]

    def set_names(self, child: str, parent: str) -> None:
        self._chunk.data = encode_instance(self._chunk.data[0], child, parent)

    def mentions(self, text: str) -> bool:
        return encode_text(text) in self._chunk.data


class MbRelationship(RelationshipRecord):
    def __init__(self, chunk: Chunk) -> None:
        self._chunk = chunk

    @property
    def kind(self) -> str:
        return decode_relationship(self._chunk.data)[0]

    @property
    def node(self) -> str:
        return decode_relationship(self._chunk.data)[1]

    @property
    def plugs(self) -> list[str]:
        return decode_relationship(self._chunk.data)[2]

    def set_names(self, node: str, plugs: list[str]) -> None:
        self._chunk.data = encode_relationship(self.kind, node, plugs)

    def mentions(self, text: str) -> bool:
        return encode_text(text) in self._chunk.data


class MbScene(SceneStorage):
    """A `.mb` file: header, references, node groups, connections, trailer."""

    codepage = CODEPAGE

    def __init__(self, data: bytes) -> None:
        self._root = read_mb(data)
        groups = [c for c in self._root.chunks if isinstance(c, GroupChunk)]
        heads = [g for g in groups if g.form_type == b"HEAD"]
        self._header, self._trailer = heads[0], heads[-1]
        self._frefs = [_only_chunk(g) for g in groups if g.form_type == b"FREF"]
        self._references: list[ReferenceRecord] = [
            MbReference(_only_chunk(g), self) for g in groups if g.form_type == b"FRDI"
        ]
        node_groups = [
            g for g in groups if g.tag == b"FOR8" and g.form_type not in _NOT_NODES
        ]
        self._nodes: list[NodeRecord] = [MbNode(g, self) for g in node_groups]
        self._instances = [
            c
            for g in groups
            if g.form_type == b"PING"
            for c in g.chunks
            if isinstance(c, Chunk) and c.tag == b"PRNT"
        ]
        self._links = [
            chunk
            for g in groups
            if g.form_type == b"CONS"
            for conn in g.chunks
            if isinstance(conn, GroupChunk)
            for chunk in conn.chunks
            if isinstance(chunk, Chunk)
        ]
        self._plugin_types: dict[int, str] = {}
        for chunk in self._header_chunks(b"PLUG"):
            self._plugin_types.update(decode_plugin(chunk.data).node_types)

    def _header_chunks(self, tag: bytes) -> list[Chunk]:
        return [c for c in self._header.chunks if isinstance(c, Chunk) and c.tag == tag]

    def _trailer_chunk(self, tag: bytes) -> Chunk:
        for chunk in self._trailer.chunks:
            if isinstance(chunk, Chunk) and chunk.tag == tag:
                return chunk
        raise KeyError(f"no {tag!r} in the trailer")

    @property
    def version(self) -> str:
        return decode_text(self._header_chunks(b"VERS")[0].data)

    @property
    def file_info(self) -> dict[str, str]:
        return dict(decode_file_info(c.data) for c in self._header_chunks(b"FINF"))

    @property
    def plugins(self) -> list[Plugin]:
        plugins = []
        for chunk in self._header_chunks(b"PLUG"):
            plugin = decode_plugin(chunk.data)
            plugins.append(
                Plugin(plugin.name, plugin.version, list(plugin.node_types.values()))
            )
        return plugins

    def nodes(self) -> list[NodeRecord]:
        return self._nodes

    def references(self) -> list[ReferenceRecord]:
        return self._references

    def connections(self) -> list[ConnectionRecord]:
        return [MbConnection(c) for c in self._links if c.tag == b"CWFL"]

    def relationships(self) -> list[RelationshipRecord]:
        return [MbRelationship(c) for c in self._links if c.tag == b"RELA"]

    def instances(self) -> list[InstanceRecord]:
        return [MbInstance(chunk) for chunk in self._instances]

    def metadata(self) -> str | None:
        try:
            return decode_metadata(self._trailer_chunk(b"SMTD").data)
        except KeyError:
            return None

    def set_metadata(self, text: str) -> None:
        self._trailer_chunk(b"SMTD").data = encode_metadata(text)

    def to_bytes(self) -> bytes:
        # BCSZ stores the distance from the start of the trailer group to itself
        offset = 16 + 4
        for chunk in self._trailer.chunks:
            if isinstance(chunk, Chunk) and chunk.tag == b"BCSZ":
                chunk.data = encode_u32(offset)
                break
            offset += extent(chunk)
        buffer = io.BytesIO()
        write_mb(buffer, self._root)
        return buffer.getvalue()


def _only_chunk(group: GroupChunk) -> Chunk:
    chunk = group.chunks[0]
    assert isinstance(chunk, Chunk)  # FREF and FRDI groups hold one data chunk
    return chunk


def _store_reference_path(chunk: Chunk, with_depth: bool, value: str) -> None:
    prefix, _, rest = split_reference(chunk.data, with_depth)
    data = prefix + encode_text(value) + rest
    file_type = reference_file_type(data)
    chunk.data = replace_reference_file_type(data, file_type_for(value, file_type))
