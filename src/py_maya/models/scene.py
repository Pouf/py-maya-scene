"""A Maya scene, whatever its format."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Optional, Tuple, Union

from ..binary.scene_chunks import ReferenceEdit, ReferenceEdits
from ..mel import number, string_setattr_arguments, string_setattr_value
from ..metadata import ContentRow, ContentTable
from ..resolvers.file_attributes import (
    file_attribute_long_names,
    file_attribute_names,
    file_attribute_short_name,
    type_chain,
)
from .node import Attribute, Node, leaf_name
from .reference import Reference

if TYPE_CHECKING:
    from typing import Callable, Iterable, Mapping

    from ..storage.base import Plugin, SceneStorage

    ExtraAttributes = Mapping[str, Iterable[str]]


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
    `sequence` is True when `path` is one frame of an image sequence: the
    node has `useFrameExtension` on, in this file or by a reference edit.
    """

    path: str
    node: str
    attribute: str | None
    kind: str
    sequence: bool = False


class Scene:
    """A Maya scene file (`.mb` or `.ma`)."""

    def __init__(self, _storage: SceneStorage, path: Path | None) -> None:
        self._storage = _storage
        self._path = path
        self._all_references = [Reference(r, self) for r in _storage.references()]
        self._nodes = [Node(r, self) for r in _storage.nodes()]
        self._by_name: dict[str, list[Node]] | None = None
        self._path_cache: dict[int, str] = {}
        self._paths_cache: dict[int, list[str]] = {}
        self._extra_parents: dict[int, list[str]] | None = None
        # externalContentTable rows bound to what they describe, at the first edit
        self._content: tuple[ContentTable, list[_ContentBinding]] | None = None

    def _instance_parents(self) -> dict[int, list[str]]:
        """Extra parent names of instanced nodes (`parent -add`), by node."""
        if self._extra_parents is None:
            self._extra_parents = {}
            for record in self._storage.instances():
                child = self._find(record.child, primary=True)
                if child is not None:
                    self._extra_parents.setdefault(id(child), []).append(record.parent)
        return self._extra_parents

    def _before_edit(self) -> None:
        """Bind the externalContentTable rows while names and paths are unchanged."""
        if self._content is not None:
            return
        text = self._storage.metadata()
        table = ContentTable(text or "")
        bindings: list[_ContentBinding] = []
        references = {r.node: r for r in self.references}
        for row in table.rows:
            if not row.attribute:
                if row.node in references:
                    bindings.append((row, references[row.node], None))
                continue
            node = self._find(row.node)
            short = (
                file_attribute_short_name(node.type, row.attribute) if node else None
            )
            if node is None or short is None:
                continue
            for attribute in node._string_attributes():
                if attribute.name == short:
                    bindings.append((row, node, attribute))
        self._content = (table, bindings)

    def _sync_content_table(self) -> None:
        if self._content is None:
            return
        table, bindings = self._content
        changed = False
        for row, owner, attribute in bindings:
            if attribute is None:
                assert isinstance(owner, Reference)  # a reference's own row
                node_name, path = owner.node, owner.path
            else:
                value = attribute.value
                if not isinstance(value, str):
                    continue
                node_name, path = owner.path, value
            changed |= row.update(
                node_name, path, _resolved(path), self._storage.codepage
            )
        if changed:
            self._storage.set_metadata(table.text)

    def _names(self) -> dict[str, list[Node]]:
        """Nodes by name, built once and kept up to date by renames."""
        if self._by_name is None:
            self._by_name = {}
            for node in self._nodes:
                self._by_name.setdefault(node.name, []).append(node)
        return self._by_name

    def _renamed(self, node: Node, old_name: str, moved: list[Node]) -> None:
        names = self._names()
        names[old_name].remove(node)
        if not names[old_name]:
            del names[old_name]
        names.setdefault(node.name, []).append(node)
        for stale in moved:
            self._path_cache.pop(id(stale), None)
            self._paths_cache.pop(id(stale), None)
        # Instance records name parents: resolve them again
        self._extra_parents = None

    @property
    def path(self) -> Path | None:
        """File the scene was parsed from or last saved to."""
        return self._path

    @property
    def version(self) -> str:
        """Maya version that saved the file (`2022`, `2018ff09` for Maya 2019)."""
        return self._storage.version

    @property
    def file_info(self) -> dict[str, str]:
        """`fileInfo` entries."""
        return self._storage.file_info

    @property
    def plugins(self) -> list[Plugin]:
        """Required plugins (`requires`), with the node types used from them."""
        return self._storage.plugins

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

    def _find(self, name: str, primary: bool = False) -> Node | None:
        """The node named `name` (short name or DAG path, any of an instanced
        node's paths unless `primary`)."""
        short = name.rsplit("|", 1)[-1].lstrip(":")
        candidates = self._names().get(short, [])
        if name.startswith("|"):
            # A full path names one node: primary paths first, they are cheap
            exact = [n for n in candidates if n.path == name]
            if not exact and not primary:
                exact = [n for n in candidates if name in n.paths]
            candidates = exact
        elif "|" in name:
            suffix = f"|{name}"
            candidates = [
                n
                for n in candidates
                if any(p.endswith(suffix) for p in ([n.path] if primary else n.paths))
            ]
        if len(candidates) > 1:
            raise ValueError(f"{name!r} matches several nodes")
        return candidates[0] if candidates else None

    @property
    def connections(self) -> list[Connection]:
        """`connectAttr` statements, in file order."""
        return [
            Connection(r.source, r.destination, r.next_available)
            for r in self._storage.connections()
        ]

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
        frames: dict[int, bool] = {}
        for attribute in self.file_attributes(extra):
            node = attribute.node
            if id(node) not in frames:
                frames[id(node)] = _uses_frames(node)
            value = attribute.value
            for path in value if isinstance(value, list) else [value]:
                if isinstance(path, str) and path:
                    dependencies.append(
                        Dependency(
                            path,
                            node.path,
                            attribute.name,
                            "attribute",
                            frames[id(node)],
                        )
                    )
        edited_frames = self._frame_edits()
        for _, _, edit, path in self._file_edits(extra):
            node_name, attribute_name = edit.strings[0], edit.strings[1]
            dependencies.append(
                Dependency(
                    path,
                    node_name,
                    attribute_name,
                    "reference edit",
                    node_name in edited_frames,
                )
            )
        return dependencies

    def _frame_edits(self) -> set[str]:
        """Referenced nodes whose `useFrameExtension` this file's edits turn on."""
        nodes = set()
        for _, edits in self.reference_edits():
            for _, edit in edits.all_edits():
                if edit.kind != 2 or leaf_name(edit.strings[1]) not in _FRAMES:
                    continue
                values = edit.strings[2].split()
                try:
                    if len(values) == 1 and number(values[0]):
                        nodes.add(edit.strings[0])
                except ValueError:
                    pass
        return nodes

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

    def replace_strings(self, replace: Callable[[str], str]) -> int:
        """Put the names and strings the file stores through `replace`, like a
        text replace over the whole file; return how many changed. `.mb` only.

        Covered: node names and parents, the nodes named by connections,
        relationships, instances and reference edits, the values set by
        reference edits, string attribute values (paths, notes, expression
        and script MEL), references (path, namespace, reference node, cached
        options), `fileInfo`, and the externalContentTable (CRCs recomputed).
        Attribute names, node types, plugins and default nodes are left
        alone, everywhere: a match in an attribute name changes nothing.

        Names stay consistent when `replace` changes a name the same way
        wherever it appears: on its own, in a DAG path (`|grp|name`), after
        a namespace (`ns:name`).
        """
        self._before_edit()
        changed = self._storage.replace_strings(replace)
        assert self._content is not None  # bound by _before_edit
        table, bindings = self._content
        # Bound rows follow their node, attribute or reference on save; the
        # others (plugin attributes the schema lacks) get the text replace
        bound = {id(row) for row, _, _ in bindings}
        rows = 0
        for row in table.rows:
            if id(row) in bound:
                continue
            path = replace(row.path)
            resolved = row.resolved_path if path == row.path else _resolved(path)
            rows += row.update(
                replace(row.node), path, resolved, self._storage.codepage
            )
        if rows:
            self._storage.set_metadata(table.text)
        # Names changed under the caches
        self._by_name = None
        self._path_cache.clear()
        self._paths_cache.clear()
        self._extra_parents = None
        return changed + rows

    def save(self, path: str | Path) -> None:
        """Save to a new file; refuses to overwrite one (`FileExistsError`).

        The write is atomic: bytes go to a temporary sibling file which then
        replaces the target. The format stays the one the scene was parsed from.
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
        self._sync_content_table()
        data = self._storage.to_bytes()
        temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as f:
                f.write(data)
            os.replace(temporary, target)
        finally:
            if temporary.exists():  # missing_ok needs Python 3.8
                temporary.unlink()
        self._path = target

    def __repr__(self) -> str:
        return f"Scene({self._path}, Maya {self.version}, {len(self._nodes)} nodes)"


def _remapped(remap: Callable[[str], str | None], path: str) -> str:
    new = remap(path)
    return path if new is None else new


# `useFrameExtension`: the file name is one frame of a sequence (file, imagePlane)
_FRAMES = ("useFrameExtension", "ufe")


def _uses_frames(node: Node) -> bool:
    for record in node._record.attributes():
        if record.name == "ufe":
            try:
                return record.get_number() != 0
            except (NotImplementedError, ValueError):
                return False
    return False


def _resolved(path: str) -> str:
    # Maya stores the resolved path of an existing file, "" otherwise; only
    # environment variables are resolved here (no workspace or dirmap rules)
    expanded = os.path.expandvars(path)
    return expanded.replace("\\", "/") if os.path.isfile(expanded) else ""


_ContentBinding = Tuple[ContentRow, Union[Node, Reference], Optional[Attribute]]
