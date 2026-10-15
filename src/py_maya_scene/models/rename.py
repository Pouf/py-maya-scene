"""Renaming a node and every stored reference to it."""

from __future__ import annotations

import re
from functools import partial
from typing import TYPE_CHECKING, List, Union

from ..binary.scene_chunks import ReferenceEdits
from ..mel import string_setattr_arguments, string_setattr_value

if TYPE_CHECKING:
    from typing import Callable

    from ..storage.base import ConnectionRecord, RelationshipRecord
    from .node import Attribute, Node
    from .scene import Scene

    Rewrite = Callable[[str], str]

NAME = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_]*:)*[A-Za-z_][A-Za-z0-9_]*")
_SEGMENT = r"(?:[A-Za-z_]\w*:)*[A-Za-z_]\w*"
# A whole string naming a node or a plug: `box`, `|grp|box`, `ns:box.tx[0]`
_REFERENCE = re.compile(rf":?\|?{_SEGMENT}(?:\|{_SEGMENT})*(?:\.[\w\[\].:]+)?")
# A node named before `.attribute` in MEL text (`getAttr box.ty`, `"box.rz"`);
# the look-behind skips variables (`$v.x`) and compiled references (`.O[0]`)
_MEL_PLUG_NODE = re.compile(
    rf"(?<![\w:|$.])(\|?{_SEGMENT}(?:\|{_SEGMENT})*)(?=\.[A-Za-z_])"
)
# Attributes holding MEL: expression text, script node before and after scripts
_MEL_ATTRIBUTES = {"expression": ("ixp",), "script": ("b", "a")}

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

StringValue = Union[str, List[str], ReferenceEdits]


def rename_node(
    scene: Scene,
    node: Node,
    new_name: str,
    *,
    shapes: bool = True,
    in_strings: bool = False,
) -> None:
    """Rename `node` and rewrite every stored reference to it (see `Node.rename`)."""
    if node.is_default:
        raise ValueError(f"{node.name} is a default node, the file does not create it")
    if node.type == "reference":
        raise NotImplementedError("renaming reference nodes is not supported yet")
    old_name = node.name
    plan = [(node, new_name)]
    if shapes and node.is_transform and new_name != old_name:
        # Like `maya.cmds.rename`: direct shape children named after the
        # transform get the new prefix (boxShape, boxShapeOrig, boxer)
        plan += [
            (child, new_name + child.name[len(old_name) :])
            for child in scene._nodes
            if child.is_dag
            and not child.is_transform
            and child.name.startswith(old_name)
            and child.path.rsplit("|", 1)[0] == node.path
        ]
    for target, name in plan:
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError(f"invalid node name: {name!r}")
        if name != target.name:
            _check_free(scene._names(), target, name)
    scene._before_edit()
    for target, name in plan:
        if name != target.name:
            _rename_one(scene, target, name, in_strings)


def _rename_one(scene: Scene, node: Node, new_name: str, in_strings: bool) -> None:
    old_name = node.name
    names = scene._names()
    # Every path of the node (instancing gives several) and what it becomes
    old_paths = node.paths
    new_paths = [p[: len(p) - len(old_name)] + new_name for p in old_paths]

    def renamed_path(path: str) -> str:
        for old, new in zip(old_paths, new_paths):
            if path == old or _is_below(path, old):
                return new + path[len(old) :]
        return path

    # The node and its descendants change path; nodes sharing the old or new
    # name may change between short and full names
    moved = [node] + [
        n
        for n in scene._nodes
        if any(_is_below(p, old) for p in n.paths for old in old_paths)
    ]
    affected = {id(n): n for n in moved}
    for name in (old_name, new_name):
        affected.update((id(n), n) for n in names.get(name, ()))
    affected_names = {n.name for n in affected.values()}

    def count_after(name: str) -> int:
        return len(names.get(name, ())) + (name == new_name) - (name == old_name)

    def full_name(target: Node, path: str | None) -> str:
        if not target.is_dag:
            return new_name if target is node else target.name
        return renamed_path(target.path if path is None else path)

    def shortest_name(target: Node, path: str | None) -> str:
        # A short name cannot tell the instances of an instanced node apart
        name = new_name if target is node else target.name
        unique = count_after(name) == 1 and len(target.paths) == 1
        return name if unique else full_name(target, path)

    def rewriters(render: Callable[[Node, str | None], str]) -> tuple[Rewrite, Rewrite]:
        def rewrite_node(text: str) -> str:
            prefix = ":" if text.startswith(":") else ""
            body = text[len(prefix) :]
            if body.rsplit("|", 1)[-1] in affected_names:
                target = scene._find(body)
                if target is not None and id(target) in affected:
                    # The path the text names: an instanced node has several
                    suffix = body if body.startswith("|") else f"|{body}"
                    named = next((p for p in target.paths if p.endswith(suffix)), None)
                    return prefix + render(target, named if "|" in body else None)
            if any(_is_below(body, old) for old in old_paths):
                # A node outside this file (referenced) below the renamed one
                return prefix + renamed_path(body)
            return text

        def rewrite_plug(text: str) -> str:
            node_part, dot, attribute = text.partition(".")
            return rewrite_node(node_part) + dot + attribute if text else text

        return rewrite_node, rewrite_plug

    # Parents, connections and relationships name nodes by their shortest
    # unique name; reference edits by full DAG path (seen in Maya's own saves)
    rewrite_node, rewrite_plug = rewriters(shortest_name)
    edit_node, edit_plug = rewriters(full_name)

    def rewrite_keeping_form(text: str) -> str:
        # Strings written by people: keep a path a path, a short name short
        return (edit_plug if text.lstrip(":").startswith("|") else rewrite_plug)(text)

    # Any string naming an affected node contains the old or the new name
    # (a path to a descendant contains the old one): skip the others undecoded
    needles = (old_name, new_name)

    # Compute every new value against the current names, then write them all
    writes: list[Callable[[], None]] = []
    for other in scene._nodes:
        parent_name = other.parent_name
        if parent_name is not None and any(n in parent_name for n in needles):
            new_parent = rewrite_node(parent_name)
            if new_parent != parent_name:
                writes.append(partial(other._record.set_parent_name, new_parent))
    storage = scene._storage
    for connection in storage.connections():
        if any(connection.mentions(n) for n in needles):
            writes += _rewrite_connection(connection, rewrite_plug)
    for relationship in storage.relationships():
        if any(relationship.mentions(n) for n in needles):
            writes += _rewrite_relationship(relationship, rewrite_node, rewrite_plug)
    for instance in storage.instances():
        # The instanced node by full path, its extra parent like a CREA parent
        child, parent = instance.child, instance.parent
        if any(instance.mentions(n) for n in needles):
            new_child, new_parent = edit_node(child), rewrite_node(parent)
            if (new_child, new_parent) != (child, parent):
                writes.append(partial(instance.set_names, new_child, new_parent))
    for attribute, edits in scene.reference_edits():
        changed = _rewrite_edits(edits, edit_node, edit_plug)
        if in_strings:
            changed |= _rewrite_edit_strings(edits, rewrite_keeping_form)
        if changed:
            writes.append(partial(_store_value, attribute, edits))
    if in_strings:
        for other in scene._nodes:
            mel = _MEL_ATTRIBUTES.get(other.type, ())
            for attribute in other._string_attributes():
                if not any(attribute._record.mentions(n) for n in needles):
                    continue
                value = attribute.value
                new_value: StringValue
                if isinstance(value, str) and attribute.leaf_name in mel:
                    new_value = _MEL_PLUG_NODE.sub(
                        lambda m: rewrite_keeping_form(m.group(1)), value
                    )
                elif isinstance(value, str):
                    new_value = _rewrite_reference(value, rewrite_keeping_form)
                elif isinstance(value, list):
                    new_value = [
                        _rewrite_reference(v, rewrite_keeping_form) for v in value
                    ]
                else:
                    continue
                if new_value != value:
                    writes.append(partial(_store_value, attribute, new_value))
    writes.append(partial(node._record.set_name, new_name))
    for write in writes:
        write()
    scene._renamed(node, old_name, moved)


def _is_below(path: str, ancestor: str) -> bool:
    return path.startswith(ancestor + "|")


def _rewrite_reference(text: str, rewrite: Rewrite) -> str:
    return rewrite(text) if _REFERENCE.fullmatch(text) else text


def _check_free(names: dict[str, list[Node]], node: Node, new_name: str) -> None:
    parent_path = node.path.rsplit("|", 1)[0]
    for other in names.get(new_name, ()):
        if other is node:
            continue
        if not (node.is_dag and other.is_dag):
            raise ValueError(f"a node named {new_name!r} already exists")
        if other.path.rsplit("|", 1)[0] == parent_path:
            raise ValueError(f"{new_name!r} already names a sibling of {node.path}")


def _rewrite_connection(
    connection: ConnectionRecord, rewrite_plug: Rewrite
) -> list[Callable[[], None]]:
    source, destination = connection.source, connection.destination
    new_source, new_destination = rewrite_plug(source), rewrite_plug(destination)
    if (new_source, new_destination) == (source, destination):
        return []
    return [partial(connection.set_plugs, new_source, new_destination)]


def _rewrite_relationship(
    relationship: RelationshipRecord, rewrite_node: Rewrite, rewrite_plug: Rewrite
) -> list[Callable[[], None]]:
    owner, plugs = relationship.node, relationship.plugs
    new_owner, new_plugs = rewrite_node(owner), [rewrite_plug(p) for p in plugs]
    if (new_owner, new_plugs) == (owner, plugs):
        return []
    return [partial(relationship.set_names, new_owner, new_plugs)]


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


def _rewrite_edit_strings(edits: ReferenceEdits, rewrite: Rewrite) -> bool:
    # String values set on referenced nodes (kind 2, ` -type "string" "..."`)
    changed = False
    for _, edit in edits.all_edits():
        value = string_setattr_value(edit.strings[2]) if edit.kind == 2 else None
        if value is not None:
            new = _rewrite_reference(value, rewrite)
            if new != value:
                edit.strings[2] = string_setattr_arguments(new)
                changed = True
    return changed


def _store_value(attribute: Attribute, value: StringValue) -> None:
    attribute.value = value
