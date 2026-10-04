"""A Maya scene, backed by its `.mb` chunk tree."""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from ..binary import Chunk, GroupChunk, extent, write_mb
from ..binary.scene_chunks import (
    Plugin,
    ReferenceEdit,
    ReferenceEdits,
    decode_connection,
    decode_file_info,
    decode_includes,
    decode_plugin,
    decode_reference,
    decode_text,
    encode_includes,
    encode_text,
    encode_u32,
    split_reference,
)
from ..data.node_types import TYPE_NAMES
from ..mel import string_setattr_arguments, string_setattr_value
from ..resolvers.file_attributes import (
    file_attribute_long_names,
    file_attribute_names,
    type_chain,
)
from .node import Attribute, Node, leaf_name
from .reference import Reference

if TYPE_CHECKING:
    from typing import Callable, Iterable, Mapping

    ExtraAttributes = Mapping[str, Iterable[str]]

# Root groups that are not nodes
_NOT_NODES = (b"HEAD", b"FRDI", b"FREF")


class Connection(NamedTuple):
    """A `connectAttr` stored in the file."""

    source: str
    destination: str
    next_available: bool


class Dependency(NamedTuple):
    """A file path stored in the scene.

    `kind` is `"reference"` (the file of a reference, `attribute` None),
    `"attribute"` (a value stored on a node of this file; `attribute` as
    stored, e.g. `ftn`) or `"reference edit"` (a value set on a referenced
    node; `node` and `attribute` as the edit names them, long attribute name).
    `node` is a long name; for a reference, the reference node.
    """

    path: str
    node: str
    attribute: str | None
    kind: str


class Scene:
    """A Maya scene file."""

    def __init__(self, _root: GroupChunk, path: Path | None) -> None:
        self._root = _root
        self._path = path
        groups = [c for c in _root.chunks if isinstance(c, GroupChunk)]
        heads = [g for g in groups if g.form_type == b"HEAD"]
        self._header, self._trailer = heads[0], heads[-1]
        self._frefs = [_only_chunk(g) for g in groups if g.form_type == b"FREF"]
        self._all_references = [
            Reference(_only_chunk(g), self) for g in groups if g.form_type == b"FRDI"
        ]
        self._nodes = [
            Node(g, self)
            for g in groups
            if g.tag == b"FOR8" and g.form_type not in _NOT_NODES
        ]
        self._connection_groups = [
            conn for g in groups if g.form_type == b"CONS" for conn in g.chunks
        ]
        self._plugin_types: dict[int, str] = {}
        for plugin in self.plugins:
            self._plugin_types.update(plugin.node_types)
        self._by_name: dict[str, list[Node]] | None = None

    @property
    def path(self) -> Path | None:
        """File the scene was parsed from or last saved to."""
        return self._path

    def _header_chunks(self, tag: bytes) -> list[Chunk]:
        return [c for c in self._header.chunks if isinstance(c, Chunk) and c.tag == tag]

    @property
    def version(self) -> str:
        """Maya version that saved the file (`2022`, `2018ff09` for Maya 2019)."""
        return decode_text(self._header_chunks(b"VERS")[0].data)

    @property
    def file_info(self) -> dict[str, str]:
        """`fileInfo` entries."""
        return dict(decode_file_info(c.data) for c in self._header_chunks(b"FINF"))

    @property
    def plugins(self) -> list[Plugin]:
        """Required plugins (`requires`), with the node types they provide."""
        return [decode_plugin(c.data) for c in self._header_chunks(b"PLUG")]

    @property
    def references(self) -> list[Reference]:
        """References made by this file (depth 1)."""
        return [r for r in self._all_references if r.depth == 1]

    @property
    def nested_references(self) -> list[Reference]:
        """References made by referenced files, as recorded in this file."""
        return [r for r in self._all_references if r.depth > 1]

    @property
    def nodes(self) -> list[Node]:
        """Nodes the file creates or edits, in file order."""
        return list(self._nodes)

    def node(self, name: str) -> Node:
        """The node named `name`: a unique short name, or a DAG path."""
        node = self._find(name)
        if node is None:
            raise KeyError(f"no node named {name!r}")
        return node

    def _find(self, name: str) -> Node | None:
        if self._by_name is None:
            self._by_name = {}
            for node in self._nodes:
                self._by_name.setdefault(node.name, []).append(node)
        short = name.rsplit("|", 1)[-1].lstrip(":")
        candidates = self._by_name.get(short, [])
        if name.startswith("|"):
            candidates = [n for n in candidates if n.path == name]
        elif "|" in name:
            candidates = [n for n in candidates if n.path.endswith(f"|{name}")]
        if len(candidates) > 1:
            raise ValueError(f"{name!r} matches several nodes")
        return candidates[0] if candidates else None

    def _type_name(self, type_id: int) -> str:
        name = self._plugin_types.get(type_id) or TYPE_NAMES.get(type_id)
        return name if name is not None else f"0x{type_id:08X}"

    @property
    def connections(self) -> list[Connection]:
        """`connectAttr` statements, in file order."""
        connections = []
        for group in self._connection_groups:
            assert isinstance(group, GroupChunk)  # CONS holds CONN groups
            for chunk in group.chunks:
                if isinstance(chunk, Chunk) and chunk.tag == b"CWFL":
                    flags, source, destination = decode_connection(chunk.data)
                    connections.append(Connection(source, destination, bool(flags & 1)))
        return connections

    def file_attributes(self, extra: ExtraAttributes | None = None) -> list[Attribute]:
        """Stored string values of the attributes that hold file paths.

        `extra` adds attributes per node type, as stored short names (for
        example `{"transform": ["icon_path"]}`); subtypes inherit them.
        """
        found: list[Attribute] = []
        for node in self._nodes:
            names = set(file_attribute_names(node.type))
            if extra:
                for node_type in type_chain(node.type):
                    names.update(extra.get(node_type, ()))
            found.extend(a for a in node._string_attributes() if a.leaf_name in names)
        return found

    def reference_edits(self) -> list[tuple[Attribute, ReferenceEdits]]:
        """The edits each reference node records, with the attribute holding them."""
        found = []
        for node in self._nodes:
            if node.type == "reference":
                for attribute in node.attributes:
                    value = attribute.value if attribute.type == "REFE" else None
                    if isinstance(value, ReferenceEdits):
                        found.append((attribute, value))
        return found

    def _file_edits(
        self, extra: ExtraAttributes | None
    ) -> list[tuple[Attribute, ReferenceEdits, ReferenceEdit, str]]:
        # String setAttr edits on a file path attribute, matched by name only
        names = set(file_attribute_long_names())
        for attributes in (extra or {}).values():
            names.update(attributes)
        found = []
        for attribute, edits in self.reference_edits():
            for _, edit in edits.all_edits():
                if edit.kind != 2 or leaf_name(edit.strings[1]) not in names:
                    continue
                value = string_setattr_value(edit.strings[2])
                if value:
                    found.append((attribute, edits, edit, value))
        return found

    def dependencies(self, extra: ExtraAttributes | None = None) -> list[Dependency]:
        """Every file path the scene stores: references at every depth, file
        attributes, then file attributes set on referenced nodes.

        See `file_attributes` for `extra`; for reference edits its attribute
        names apply to every node type, since the edited node's type is not
        stored in this file.
        """
        dependencies = [
            Dependency(r.path, r.node, None, "reference") for r in self._all_references
        ]
        for attribute in self.file_attributes(extra):
            value = attribute.value
            for path in value if isinstance(value, list) else [value]:
                if isinstance(path, str) and path:
                    dependencies.append(
                        Dependency(
                            path, attribute.node.path, attribute.name, "attribute"
                        )
                    )
        for _, _, edit, path in self._file_edits(extra):
            dependencies.append(
                Dependency(path, edit.strings[0], edit.strings[1], "reference edit")
            )
        return dependencies

    def remap_paths(
        self,
        remap: Callable[[str], str | None],
        extra: ExtraAttributes | None = None,
    ) -> int:
        """Replace every stored file path `p` with `remap(p)`; return how many changed.

        Covers everything `dependencies` lists. `remap` returns the new path,
        or None (or `p`) to keep it; it sees paths exactly as stored, with no
        normalisation. See `dependencies` for `extra`.
        """
        changed = 0
        for reference in self._all_references:
            new = _remapped(remap, reference.path)
            if new != reference.path:
                reference.path = new
                changed += 1
        for attribute in self.file_attributes(extra):
            value = attribute.value
            if isinstance(value, list):
                new_values = [_remapped(remap, v) for v in value]
                if new_values != value:
                    attribute.value = new_values
                    changed += sum(a != b for a, b in zip(value, new_values))
            elif isinstance(value, str) and value:
                new_value = _remapped(remap, value)
                if new_value != value:
                    attribute.value = new_value
                    changed += 1
        edited: dict[Attribute, ReferenceEdits] = {}
        for attribute, edits, edit, path in self._file_edits(extra):
            new_path = _remapped(remap, path)
            if new_path != path:
                edit.strings[2] = string_setattr_arguments(new_path)
                edited[attribute] = edits
                changed += 1
        for attribute, edits in edited.items():
            attribute.value = edits
        return changed

    def _set_reference_path(self, reference: Reference, value: str) -> None:
        old = reference.path
        _store_reference_path(reference._chunk, True, value)
        if reference.depth != 1:
            return
        nodes = [decode_reference(c.data, False).node for c in self._frefs]
        index = nodes.index(reference.node)
        _store_reference_path(self._frefs[index], False, value)
        include = self._trailer_chunk(b"INCL")
        paths = decode_includes(include.data)
        # The trailer lists the top-level references in FREF order; leave it
        # alone if a file ever breaks that rule rather than guess.
        if index < len(paths) and paths[index] == old:
            paths[index] = value
            include.data = encode_includes(paths)

    def _trailer_chunk(self, tag: bytes) -> Chunk:
        for chunk in self._trailer.chunks:
            if isinstance(chunk, Chunk) and chunk.tag == tag:
                return chunk
        raise KeyError(f"no {tag!r} in the trailer")

    def _update_trailer_offset(self) -> None:
        # BCSZ stores the distance from the start of the trailer group to itself
        offset = 16 + 4
        for chunk in self._trailer.chunks:
            if isinstance(chunk, Chunk) and chunk.tag == b"BCSZ":
                chunk.data = encode_u32(offset)
                return
            offset += extent(chunk)

    def save(self, path: str | Path) -> None:
        """Save to a new file; refuses to overwrite one (`FileExistsError`).

        The write is atomic: bytes go to a temporary sibling file which then
        replaces the target.
        """
        target = Path(path)
        if target.exists():
            raise FileExistsError(f"refusing to overwrite {target}")
        self._write(target)

    def save_in_place(self) -> None:
        """Overwrite the file this scene was parsed from or last saved to."""
        if self._path is None:
            raise FileNotFoundError("the scene has no file to overwrite")
        self._write(self._path)

    def _write(self, target: Path) -> None:
        self._update_trailer_offset()
        buffer = io.BytesIO()
        write_mb(buffer, self._root)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_bytes(buffer.getvalue())
        os.replace(temporary, target)
        self._path = target

    def __repr__(self) -> str:
        return f"Scene({self._path}, Maya {self.version}, {len(self._nodes)} nodes)"


def _only_chunk(group: GroupChunk) -> Chunk:
    chunk = group.chunks[0]
    assert isinstance(chunk, Chunk)  # FREF and FRDI groups hold one data chunk
    return chunk


def _store_reference_path(chunk: Chunk, with_depth: bool, value: str) -> None:
    prefix, _, rest = split_reference(chunk.data, with_depth)
    chunk.data = prefix + encode_text(value) + rest


def _remapped(remap: Callable[[str], str | None], path: str) -> str:
    new = remap(path)
    return path if new is None else new
