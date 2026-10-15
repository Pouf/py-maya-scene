"""Nodes and their stored attribute values."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .rename import rename_node

if TYPE_CHECKING:
    from ..storage.base import AttributeRecord, AttributeValue, NodeRecord
    from .scene import Scene

_INDEX = re.compile(r"\[[^\]]*\]")


def leaf_name(attribute: str) -> str:
    """The attribute itself, without indices or parents (`uvst[0].uvsn` > `uvsn`)."""
    return _INDEX.sub("", attribute).split(".")[-1]


class Attribute:
    """An attribute value stored on a node.

    `value` decodes the storage types py_maya_scene understands so far: strings
    (`STR`), string arrays (`STR#`) and reference edits (`REFE`).
    """

    def __init__(self, _record: AttributeRecord, node: Node) -> None:
        self._record = _record
        self.node = node

    @property
    def name(self) -> str:
        """Name as stored: short name, with indices and parents (`uvst[0].uvsn`)."""
        return self._record.name

    @property
    def leaf_name(self) -> str:
        """Short name of the attribute itself, without indices or parents."""
        return leaf_name(self.name)

    @property
    def type(self) -> str:
        """Storage type: `STR`, `STR#`, `REFE`, else the format's own name for it."""
        return self._record.type

    @property
    def value(self) -> AttributeValue:
        return self._record.get_value()

    @value.setter
    def value(self, value: AttributeValue) -> None:
        if isinstance(value, list) and not all(isinstance(v, str) for v in value):
            raise TypeError("a string array value must be a list of str")
        self.node.scene._before_edit()
        self._record.set_value(value)

    def __repr__(self) -> str:
        return f"Attribute({self.node.name}.{self.name}, {self.type})"


class Node:
    """A node the file creates, or a default node whose attributes it edits."""

    def __init__(self, _record: NodeRecord, scene: Scene) -> None:
        self._record = _record
        self.scene = scene

    @property
    def is_default(self) -> bool:
        """True for a node the file does not create (a `select -ne` block)."""
        return self._record.is_default

    @property
    def name(self) -> str:
        """Name, with its namespace (`ns:target`).

        Setting it is `rename(value)` with its defaults.
        """
        return self._record.name

    @name.setter
    def name(self, value: str) -> None:
        rename_node(self.scene, self, value)

    def rename(
        self, new_name: str, *, shapes: bool = True, in_strings: bool = False
    ) -> None:
        """Rename the node and rewrite every stored reference to it.

        Rewritten: parents, connections, relationships, reference edits and the
        externalContentTable (on save). Like `maya.cmds.rename`, renaming a
        transform also renames its shapes whose name starts with the old name
        (`boxShape` > `crateShape`), unless `shapes` is False.

        `in_strings` also rewrites node names inside string attribute values
        that name a node (`"box"`, `"|grp|box.tx"`) and inside the MEL of
        expressions and script nodes (`getAttr box.ty`). Maya does not, and
        a string that only looks like a node name (`box.exr`) is rewritten too.
        """
        rename_node(self.scene, self, new_name, shapes=shapes, in_strings=in_strings)

    @property
    def type_id(self) -> int | None:
        """Type id, or None when unknown (default nodes, plugin types in `.ma`)."""
        return self._record.type_id

    @property
    def type(self) -> str:
        """Type name, `0x...` for an unknown `.mb` type id, `""` for an unknown default node."""
        return self._record.type_name

    @property
    def uuid(self) -> str | None:
        return self._record.uuid

    @property
    def parent_name(self) -> str | None:
        """Parent as stored: a short name, or a DAG path when the name is ambiguous."""
        return self._record.parent_name

    @property
    def parent(self) -> Node | None:
        """The parent node, or None for a root node or a parent outside this file."""
        parent_name = self.parent_name
        if parent_name is None:
            return None
        return self.scene._find(parent_name)

    @property
    def is_dag(self) -> bool:
        return self._record.is_dag

    @property
    def is_transform(self) -> bool:
        return self._record.is_transform

    @property
    def path(self) -> str:
        """Long name: the full DAG path for DAG nodes, the name otherwise."""
        cache = self.scene._path_cache
        cached = cache.get(id(self))
        if cached is not None:
            return cached
        # Walk up, not recurse: rigs nest joints hundreds of levels deep
        chain: list[Node] = []
        node = self
        prefix: str | None = None
        while True:
            chain.append(node)
            parent_name = node.parent_name
            if parent_name is None:
                break
            if parent_name.startswith("|"):
                # An absolute path is the parent's path already
                prefix = parent_name
                break
            parent = self.scene._find(parent_name, primary=True)
            if parent is None:
                # Parent outside this file (a referenced node): the best known path
                prefix = f"|{parent_name}"
                break
            if "|" in parent_name or id(parent) in cache:
                prefix = parent.path
                break
            if len(chain) > len(self.scene._nodes):
                raise ValueError(f"the parents of {self.name} form a loop")
            node = parent
        for node in reversed(chain):
            if prefix is None:
                path = f"|{node.name}" if node.is_dag else node.name
            else:
                path = f"{prefix}|{node.name}"
            cache[id(node)] = path
            prefix = path
        return cache[id(self)]

    @property
    def paths(self) -> list[str]:
        """Every DAG path of the node, `path` first: one per parent (instancing
        adds parents), times the paths of each parent."""
        if not self.scene._instance_parents():
            return [self.path]
        cache = self.scene._paths_cache
        # Parents first, without recursing (see `path`); a node met again
        # while its parents are pending is its own ancestor
        stack = [self]
        expanded: set[int] = set()
        while stack:
            node = stack[-1]
            if id(node) in cache:
                stack.pop()
                continue
            parents = node._parents()
            pending = [p for _, p in parents if p is not None and id(p) not in cache]
            if pending:
                if id(node) in expanded:
                    raise ValueError(f"the parents of {node.name} form a loop")
                expanded.add(id(node))
                stack += pending
                continue
            stack.pop()
            paths = []
            for parent_name, parent in parents:
                if parent is None:
                    prefix = (
                        parent_name
                        if parent_name.startswith("|")
                        else f"|{parent_name}"
                    )
                    paths.append(f"{prefix}|{node.name}")
                else:
                    paths += [f"{p}|{node.name}" for p in cache[id(parent)]]
            cache[id(node)] = paths or [node.path]
        return cache[id(self)]

    def _parents(self) -> list[tuple[str, Node | None]]:
        """Parent names, the primary one first, with the nodes they name."""
        names = [] if self.parent_name is None else [self.parent_name]
        names += self.scene._instance_parents().get(id(self), [])
        return [(name, self.scene._find(name)) for name in names]

    @property
    def attributes(self) -> list[Attribute]:
        return [Attribute(r, self) for r in self._record.attributes()]

    def _string_attributes(self) -> list[Attribute]:
        return [Attribute(r, self) for r in self._record.string_attributes()]

    def attr(self, name: str) -> Attribute:
        """The stored attribute named `name` (as stored, e.g. `ftn` or `uvst[0].uvsn`)."""
        for attribute in self.attributes:
            if attribute.name == name:
                return attribute
        raise KeyError(f"{self.name} stores no value for {name!r}")

    def __repr__(self) -> str:
        return f"Node({self.path!r}, {self.type!r})"
