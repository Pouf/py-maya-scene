"""Scene records over a Maya Binary (`.mb`) chunk tree."""

from __future__ import annotations

import io
from typing import Callable, List, cast

from ..binary import Chunk, GroupChunk, extent, read_mb, write_mb
from ..binary.scene_chunks import (
    Crea,
    ReferenceEdit,
    ReferenceEdits,
    decode_connection,
    decode_crea,
    decode_double,
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
    encode_file_info,
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
    replace_reference_strings,
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
_NOT_VALUES = (b"ATTR", b"ATAL")
_STRING_TAGS = (b"STR ", b"STR#")
_REPLACED_TAGS = _STRING_TAGS + (b"REFE",)
# What each reference edit string holds, by edit kind: a node name ("n"), a
# plug ("p"), MEL values ("v"), or an attribute name or flags ("-", kept).
# Kind 5 depends on its side: both ends referenced (0), or one (3, 4)
_EDIT_ROLES = {0: "nn-", 1: "n---", 2: "n-v", 3: "pp-", 4: "n--", 8: "n-", 9: "n-"}
_CONNECT_ROLES_BOTH, _CONNECT_ROLES_ONE = "npppp-", "npp-"
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

    def get_number(self) -> float:
        _, _, raw = split_attribute(self._chunk.data)
        if self._chunk.tag != b"DBLE" or len(raw) != 8:
            raise NotImplementedError(f"{self.type} values are not decoded yet")
        return decode_double(raw)

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

    def replace_strings(self, replace: Callable[[str], str]) -> int:
        changed = 0

        def swap(text: str) -> str:
            nonlocal changed
            new = replace(text)
            changed += new != text
            return new

        defaults = set(DEFAULT_NODES) | {n.name for n in self._nodes if n.is_default}

        def swap_node(name: str) -> str:
            # Name by name along a DAG path, keeping Maya's default nodes
            # (`:time1`, `|persp|perspShape`) wherever they are named
            if "|" in name:
                return "|".join(
                    swap_node(part) if part else "" for part in name.split("|")
                )
            return name if name.lstrip(":") in defaults else swap(name)

        def swap_plug(plug: str) -> str:
            # Node names hold no ".": the rest is the attribute, kept
            node, dot, attribute = plug.partition(".")
            return swap_node(node) + dot + attribute

        swaps: dict[str, Callable[[str], str]] = {
            "n": swap_node,
            "p": swap_plug,
            "v": swap,
            "-": lambda text: text,
        }

        def swap_edit(edit: ReferenceEdit) -> ReferenceEdit:
            if edit.kind == 5:
                roles = _CONNECT_ROLES_BOTH if edit.side == 0 else _CONNECT_ROLES_ONE
            else:
                roles = _EDIT_ROLES[edit.kind]
            return edit._replace(
                strings=[swaps[r](s) for r, s in zip(roles, edit.strings)]
            )

        for chunk in self._header_chunks(b"FINF"):
            key, value = decode_file_info(chunk.data)
            chunk.data = encode_file_info(swap(key), swap(value))
        for chunk in self._header_chunks(b"INCL") + [
            c for c in self._trailer.chunks if isinstance(c, Chunk) and c.tag == b"INCL"
        ]:
            chunk.data = encode_includes([swap(p) for p in decode_includes(chunk.data)])
        for group in self._root.chunks:
            if isinstance(group, GroupChunk) and group.form_type in (b"FRDI", b"FREF"):
                chunk = _only_chunk(group)
                with_depth = group.form_type == b"FRDI"
                data = replace_reference_strings(chunk.data, with_depth, swap)
                path = decode_reference(data, with_depth).path
                file_type = file_type_for(path, reference_file_type(data))
                chunk.data = replace_reference_file_type(data, file_type)
        for node in cast(List[MbNode], self._nodes):
            if not node.is_default:
                crea = node._crea()
                name = swap_node(crea.name)
                parent = None if crea.parent is None else swap_node(crea.parent)
                if (name, parent) != (crea.name, crea.parent):
                    node._store_crea(crea._replace(name=name, parent=parent))
            for child in node._group.chunks[1:]:
                if not isinstance(child, Chunk) or child.tag not in _REPLACED_TAGS:
                    continue
                attribute, flags, raw = split_attribute(child.data)
                if child.tag == b"STR ":
                    new_raw = encode_string(swap(decode_string(raw)))
                elif child.tag == b"STR#":
                    new_raw = encode_string_array(
                        [swap(s) for s in decode_string_array(raw)]
                    )
                else:
                    edits = decode_reference_edits(raw)
                    new_raw = encode_reference_edits(
                        ReferenceEdits(
                            swap_node(edits.owner),
                            [
                                [
                                    (swap_node(n), [swap_edit(e) for e in g])
                                    for n, g in section
                                ]
                                for section in edits.sections
                            ],
                        )
                    )
                if new_raw != raw:
                    child.data = join_attribute(attribute, flags, new_raw)
        for chunk in self._instances:
            flags, instanced, parent_name = decode_instance(chunk.data)
            chunk.data = encode_instance(
                flags, swap_node(instanced), swap_node(parent_name)
            )
        for chunk in self._links:
            if chunk.tag == b"CWFL":
                flags, source, destination = decode_connection(chunk.data)
                chunk.data = encode_connection(
                    flags, swap_plug(source), swap_plug(destination)
                )
            elif chunk.tag == b"RELA":
                kind, node_name, plugs = decode_relationship(chunk.data)
                chunk.data = encode_relationship(
                    kind, swap_node(node_name), [swap_plug(p) for p in plugs]
                )
        return changed

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
