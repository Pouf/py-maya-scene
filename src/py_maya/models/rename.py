"""Renaming a node and every stored reference to it."""

from __future__ import annotations

import re
from functools import partial
from typing import TYPE_CHECKING

from ..binary import Chunk, GroupChunk
from ..binary.scene_chunks import (
    ReferenceEdits,
    decode_connection,
    decode_relationship,
    encode_connection,
    encode_relationship,
    encode_text,
)

if TYPE_CHECKING:
    from typing import Callable

    from .node import Attribute, Node
    from .scene import Scene

    Rewrite = Callable[[str], str]

NAME = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_]*:)*[A-Za-z_][A-Za-z0-9_]*")

# Per reference edit kind, the strings that name a node and those that name a
# plug (kind 5 by side); the others are attribute names, flags or arguments
_EDIT_NODES: dict[int, tuple[int, ...]] = {
    0: (0, 1),
    1: (0,),
    2: (0,),
    4: (0,),
    8: (0,),
    9: (0,),
}
_EDIT_PLUGS: dict[int, tuple[int, ...]] = {3: (0, 1)}
_CONNECTION_PLUGS: dict[int, tuple[int, ...]] = {
    0: (1, 2, 3, 4, 5),
    3: (1, 2, 3),
    4: (1, 2, 3),
}


def rename_node(scene: Scene, node: Node, new_name: str) -> None:
    """Rename `node`, rewriting the parents, connections, relationships and
    reference edits that name it.

    Like Maya when it saves, a rewritten reference uses the node's short name
    when no other node of the file has it, else the absolute path. This also
    applies to references to the nodes that share the old or the new name,
    whose short names may become unique or ambiguous.
    """
    if not isinstance(new_name, str) or not NAME.fullmatch(new_name):
        raise ValueError(f"invalid node name: {new_name!r}")
    if node.is_default:
        raise ValueError(f"{node.name} is a default node, the file does not create it")
    if node.type == "reference":
        raise NotImplementedError("renaming reference nodes is not supported yet")
    old_name = node.name
    if new_name == old_name:
        return
    names = scene._names()
    _check_free(names, node, new_name)

    old_path = node.path
    new_path = old_path[: len(old_path) - len(old_name)] + new_name
    # The node and its descendants change path; nodes sharing the old or new
    # name may change between short and full names
    moved = [node] + [n for n in scene._nodes if _is_below(n.path, old_path)]
    affected = {id(n): n for n in moved}
    for name in (old_name, new_name):
        affected.update((id(n), n) for n in names.get(name, ()))
    affected_names = {n.name for n in affected.values()}

    def count_after(name: str) -> int:
        return len(names.get(name, ())) + (name == new_name) - (name == old_name)

    def full_name(target: Node) -> str:
        name = new_name if target is node else target.name
        if not target.is_dag:
            return name
        path = target.path
        if path == old_path or _is_below(path, old_path):
            return new_path + path[len(old_path) :]
        return path

    def shortest_name(target: Node) -> str:
        name = new_name if target is node else target.name
        return name if count_after(name) == 1 else full_name(target)

    def rewriters(render: Callable[[Node], str]) -> tuple[Rewrite, Rewrite]:
        def rewrite_node(text: str) -> str:
            prefix = ":" if text.startswith(":") else ""
            body = text[len(prefix) :]
            if body.rsplit("|", 1)[-1] in affected_names:
                target = scene._find(body)
                if target is not None and id(target) in affected:
                    return prefix + render(target)
            if _is_below(body, old_path):
                # A node outside this file (referenced) below the renamed one
                return prefix + new_path + body[len(old_path) :]
            return text

        def rewrite_plug(text: str) -> str:
            node_part, dot, attribute = text.partition(".")
            return rewrite_node(node_part) + dot + attribute if text else text

        return rewrite_node, rewrite_plug

    # Parents, connections and relationships name nodes by their shortest
    # unique name; reference edits by full DAG path (seen in Maya's own saves)
    rewrite_node, rewrite_plug = rewriters(shortest_name)
    edit_node, edit_plug = rewriters(full_name)

    # Any string naming an affected node contains the old or the new name
    # (a path to a descendant contains the old one): skip the others undecoded
    needles = (old_name, new_name)
    byte_needles = tuple(encode_text(n) for n in needles)

    # Compute every new value against the current names, then write them all
    writes: list[Callable[[], None]] = []
    for other in scene._nodes:
        parent_name = other.parent_name
        if parent_name is not None and any(n in parent_name for n in needles):
            new_parent = rewrite_node(parent_name)
            if new_parent != parent_name:
                writes.append(partial(other._store_parent_name, new_parent))
    for chunk in _connection_chunks(scene):
        if any(n in chunk.data for n in byte_needles):
            data = _rewrite_connection(
                chunk.tag, chunk.data, rewrite_node, rewrite_plug
            )
            if data != chunk.data:
                writes.append(partial(_store_data, chunk, data))
    for attribute, edits in scene.reference_edits():
        if _rewrite_edits(edits, edit_node, edit_plug):
            writes.append(partial(_store_value, attribute, edits))
    writes.append(partial(node._store_name, new_name))
    for write in writes:
        write()
    scene._renamed(node, old_name, moved)


def _is_below(path: str, ancestor: str) -> bool:
    return path.startswith(ancestor + "|")


def _check_free(names: dict[str, list[Node]], node: Node, new_name: str) -> None:
    parent_path = node.path.rsplit("|", 1)[0]
    for other in names.get(new_name, ()):
        if other is node:
            continue
        if not (node.is_dag and other.is_dag):
            raise ValueError(f"a node named {new_name!r} already exists")
        if other.path.rsplit("|", 1)[0] == parent_path:
            raise ValueError(f"{new_name!r} already names a sibling of {node.path}")


def _connection_chunks(scene: Scene) -> list[Chunk]:
    chunks = []
    for group in scene._connection_groups:
        assert isinstance(group, GroupChunk)  # CONS holds CONN groups
        chunks += [
            c
            for c in group.chunks
            if isinstance(c, Chunk) and c.tag in (b"CWFL", b"RELA")
        ]
    return chunks


def _rewrite_connection(
    tag: bytes, data: bytes, rewrite_node: Rewrite, rewrite_plug: Rewrite
) -> bytes:
    if tag == b"CWFL":
        flags, source, destination = decode_connection(data)
        return encode_connection(flags, rewrite_plug(source), rewrite_plug(destination))
    kind, owner, plugs = decode_relationship(data)
    return encode_relationship(
        kind, rewrite_node(owner), [rewrite_plug(p) for p in plugs]
    )


def _rewrite_edits(
    edits: ReferenceEdits, rewrite_node: Rewrite, rewrite_plug: Rewrite
) -> bool:
    changed = False
    for section in edits.sections:
        for index, (group_node, group_edits) in enumerate(section):
            section[index] = (rewrite_node(group_node), group_edits)
            changed |= section[index][0] != group_node
            for edit in group_edits:
                if edit.side is None:
                    node_indices = _EDIT_NODES.get(edit.kind, ())
                    plug_indices = _EDIT_PLUGS.get(edit.kind, ())
                else:
                    node_indices = (0,)
                    plug_indices = _CONNECTION_PLUGS.get(edit.side, ())
                for rewrite, indices in (
                    (rewrite_node, node_indices),
                    (rewrite_plug, plug_indices),
                ):
                    for i in indices:
                        new = rewrite(edit.strings[i])
                        changed |= new != edit.strings[i]
                        edit.strings[i] = new
    return changed


def _store_data(chunk: Chunk, data: bytes) -> None:
    chunk.data = data


def _store_value(attribute: Attribute, value: ReferenceEdits) -> None:
    attribute.value = value
