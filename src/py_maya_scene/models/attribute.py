"""The value and flags of one attribute of a node, read across the records a
file stores for it, the way `getAttr` gives them."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Optional, Tuple

from ..binary.scene_chunks import ReferenceEdits, float32
from ..resolvers.attribute_specs import NUMBER_KINDS
from ..resolvers.file_attributes import type_chain
from ..storage.base import RecordFlags
from ..units import TICKS_PER_SECOND

if TYPE_CHECKING:
    from ..resolvers.attribute_specs import AttributeSpec
    from ..storage.base import AttributeRecord, AttributeValue
    from .node import Node

# An attribute name with an optional index or index range, like `pt[0:9]`
_COMPONENT = re.compile(r"([^.\[\]]+)(?:\[(\d+)(?::(\d+))?\])?")
_INDEX = re.compile(r"\[[^\]]*\]")
_FLOAT_KINDS = frozenset(("float", "flinear", "fangle"))
_INT_KINDS = frozenset(("byte", "char", "short", "long", "int64", "enum"))
_LINEAR_KINDS = frozenset(("linear", "flinear"))
_ANGLE_KINDS = frozenset(("angle", "fangle"))
# Mesh vertices, edges, faces and normals, which `.mb` keeps in the mesh's
# `MESH` chunk where `.ma` writes `setAttr` statements
_MESH_DATA = frozenset(("vt", "ed", "fc", "n"))
# Data kinds whose values py_maya_scene decodes
_DATA_KINDS = frozenset(
    "string stringArray matrix doubleArray Int32Array pointArray vectorArray".split()
)

# Records whose value they decode themselves, strings, string arrays and
# reference edits
_RECORD_TYPES = ("STR", "STR#", "REFE")

# Attribute name and index range of each level of an attribute path
Component = Tuple[str, Optional[Tuple[int, int]]]
# Where a number of a value sits, by element index for a multi attribute
# and by child for a compound
Leaf = Tuple[int, ...]
# A record holding a number of the value, with the number's position in it,
# the number in the file's units and its kind
Source = Tuple["AttributeRecord", int, float, Optional[str]]


def leaf_name(attribute: str) -> str:
    """The attribute itself, without indices or parents (`uvst[0].uvsn` > `uvsn`)."""
    return _INDEX.sub("", attribute).split(".")[-1]


def parse_path(name: str) -> list[Component]:
    """`uvst[0].uvsp[0:9]` > `[("uvst", (0, 0)), ("uvsp", (0, 9))]`."""
    components: list[Component] = []
    for part in name.split("."):
        match = _COMPONENT.fullmatch(part)
        if match is None:
            raise ValueError(f"not an attribute path: {name!r}")
        attribute, first, last = match.groups()
        if first is None:
            components.append((attribute, None))
        else:
            components.append((attribute, (int(first), int(last or first))))
    return components


def full_path(
    components: list[Component], specs: dict[str, AttributeSpec]
) -> list[Component]:
    """`components` with the compound parents a stored name leaves out
    (`ovcr` > `do.ovrgb.ovcr`), so attributes and records compare level by level."""
    path: list[Component] = []
    for name, index in components:
        previous = path[-1][0] if path else None
        missing = []
        spec = specs.get(name)
        parent = spec.parent if spec is not None else None
        while parent is not None and parent != previous:
            missing.append((parent, None))
            parent_spec = specs.get(parent)
            parent = parent_spec.parent if parent_spec is not None else None
        path.extend(reversed(missing))
        path.append((name, index))
    return path


def _contains(outer: tuple[int, int] | None, inner: tuple[int, int] | None) -> bool:
    if outer is None or inner is None:
        return outer is None and inner is None
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def _overlap(path: list[Component], other: list[Component]) -> bool:
    """Whether one path is the other, a parent of it, or shares elements with it."""
    for level in range(min(len(path), len(other))):
        name, index = path[level]
        other_name, other_index = other[level]
        if name != other_name:
            return False
        if index is not None and other_index is not None:
            if index[1] < other_index[0] or other_index[1] < index[0]:
                return False
    return True


def _prefix_matches(path: list[Component], record: list[Component], count: int) -> bool:
    """Whether the first `count` levels of `record` hold those of `path`."""
    for level in range(count):
        name, index = path[level]
        record_name, record_index = record[level]
        if name != record_name or not _contains(record_index, index):
            return False
    return True


def path_name(path: list[Component]) -> str:
    """The name files write for `path`, the multi elements that lead to the
    attribute then the attribute, compound parents left out (`uvst[0].uvsp[5]`)."""
    parts = []
    last = len(path) - 1
    for level, (name, index) in enumerate(path):
        if index is None:
            if level == last:
                parts.append(name)
        elif index[0] == index[1]:
            parts.append(f"{name}[{index[0]}]")
        else:
            parts.append(f"{name}[{index[0]}:{index[1]}]")
    return ".".join(parts)


def attribute_path(
    record: list[Component], specs: dict[str, AttributeSpec]
) -> list[Component]:
    """The attribute a record holds a part of, its numeric compound parent
    for a child stored apart and its multi for elements."""
    path = record
    while len(path) > 1 and path[-1][1] is None:
        parent = specs.get(path[-2][0])
        if parent is None or not _number_compound(parent, specs):
            break
        path = path[:-1]
    if path[-1][1] is not None:
        path = path[:-1] + [(path[-1][0], None)]
    return path


def _number_compound(spec: AttributeSpec, specs: dict[str, AttributeSpec]) -> bool:
    if not spec.children:
        return False
    for child in spec.children:
        child_spec = specs.get(child)
        if child_spec is None or child_spec.kind not in NUMBER_KINDS:
            return False
    return True


def _record_type(value: Any) -> str:
    """The type of the records that store `value`."""
    if isinstance(value, str):
        return "STR"
    if isinstance(value, ReferenceEdits):
        return "REFE"
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return "STR#"
    raise TypeError(f"not a string, a list of str or reference edits: {value!r}")


class Attribute:
    """One attribute of a node, or one element of a multi attribute, with the
    value and flags the file gives it, like `getAttr`.

    Values are in the scene's units (`Scene.units`). One number is a bool,
    int or float, a numeric compound a tuple, the elements of a multi
    attribute and an array a list, and an `xform` matrix an `Xform`. String
    arrays are lists of str and reference edits `ReferenceEdits`. The file
    stores static values, so an attribute driven by a connection or an
    animation curve gives its stored value, not the evaluated one.
    """

    def __init__(self, node: Node, name: str) -> None:
        self.node = node
        self.name = name
        """Name with short attribute names (`uvst[0].uvsp[3]`)."""
        specs = node._specs()
        self._path = full_path(parse_path(name), specs)
        self._spec = specs.get(self._path[-1][0])

    def __repr__(self) -> str:
        return f"Attribute({self.node.name}.{self.name})"

    @property
    def spec(self) -> AttributeSpec | None:
        """What the schema or the node's added attributes say of the attribute."""
        return self._spec

    def _is_array(self) -> bool:
        return self._spec is not None and self._spec.multi and self._path[-1][1] is None

    def _records(self) -> list[tuple[AttributeRecord, list[Component]]]:
        return self.node._record_paths(self._path[0][0])

    # Values

    def _kinds(self, attribute: str) -> list[str | None]:
        """Kinds of the numbers of one element of `attribute`."""
        spec = self.node._specs().get(attribute)
        if spec is None:
            return []
        if spec.kind in NUMBER_KINDS:
            return [spec.kind]
        kinds: list[str | None] = []
        for child in spec.children:
            child_spec = self.node._specs().get(child)
            kinds.append(child_spec.kind if child_spec is not None else None)
        return kinds

    def _number_leaves(self) -> dict[Leaf, list[Source]]:
        """Each number of the value, in the file's units, with every record
        that holds it in file order. The last one gives the value."""
        path = self._path
        depth = len(path)
        array = self._is_array()
        assert self._spec is not None  # value() handles attributes with no spec
        children = self._spec.children
        leaves: dict[Leaf, list[Source]] = {}
        for record, record_path in self._records():
            levels = len(record_path)
            kinds = self._kinds(record_path[-1][0])
            if levels == depth and record_path[-1][0] == path[-1][0]:
                # The attribute itself, a range holding it, or elements of a multi
                if not _prefix_matches(path, record_path, depth - 1):
                    continue
                index = path[-1][1]
                record_index = record_path[-1][1]
                if array:
                    if record_index is None:
                        continue
                    elements = list(range(record_index[0], record_index[1] + 1))
                elif _contains(record_index, index):
                    elements = [index[0] if index is not None else 0]
                else:
                    continue
                numbers = record.numbers(kinds)
                if numbers is None:
                    continue
                size = max(len(kinds), 1)
                first = record_index[0] if record_index is not None else 0
                for element in elements:
                    start = (element - first) * size
                    for child in range(size):
                        if start + child >= len(numbers[0]):
                            break
                        key: Leaf = (element,) if array else ()
                        if len(kinds) > 1:
                            key = key + (child,)
                        at = start + child
                        source = (record, at, numbers[0][at], numbers[1][at])
                        leaves.setdefault(key, []).append(source)
            elif levels == depth - 1 and not array:
                # A compound element holding the attribute as one of its children
                parent_name = record_path[-1][0]
                parent_spec = self.node._specs().get(parent_name)
                if (
                    parent_spec is None
                    or path[-1][0] not in parent_spec.children
                    or not _prefix_matches(path, record_path, levels)
                ):
                    continue
                numbers = record.numbers(kinds)
                if numbers is None:
                    continue
                index = path[-2][1]
                record_index = record_path[-1][1]
                first = record_index[0] if record_index is not None else 0
                element = index[0] if index is not None else 0
                at = (element - first) * len(kinds) + parent_spec.children.index(
                    path[-1][0]
                )
                if at < len(numbers[0]):
                    source = (record, at, numbers[0][at], numbers[1][at])
                    leaves.setdefault((), []).append(source)
            elif levels == depth + 1 and record_path[-1][0] in children:
                # A child of the attribute, or of one element of a multi
                if record_path[-1][1] is not None:
                    continue
                child = children.index(record_path[-1][0])
                if array:
                    if not _prefix_matches(path, record_path, depth - 1):
                        continue
                    element_index = record_path[-2][1]
                    if (
                        record_path[-2][0] != path[-1][0]
                        or element_index is None
                        or element_index[0] != element_index[1]
                    ):
                        continue
                    key = (element_index[0], child)
                elif _prefix_matches(path, record_path, depth):
                    key = (child,)
                else:
                    continue
                numbers = record.numbers(self._kinds(record_path[-1][0]))
                if numbers is not None and numbers[0]:
                    source = (record, 0, numbers[0][0], numbers[1][0])
                    leaves.setdefault(key, []).append(source)
        return leaves

    def _convert(self, number: float, kind: str | None, internal: bool) -> Any:
        """A number in the scene's units, typed like `getAttr` gives it."""
        if kind is None:
            return number
        if internal:
            units = self.node.scene.units
            if kind in _LINEAR_KINDS:
                number /= units.centimetres
            elif kind in _ANGLE_KINDS:
                number /= units.radians
            elif kind == "time":
                number /= units.ticks_per_frame
        if kind == "bool":
            return number != 0
        if kind in _INT_KINDS:
            return int(round(number))
        if kind in _FLOAT_KINDS:
            return float32(number)
        return float(number)

    def _default(self, attribute: str) -> Any:
        """The default of one element of `attribute`, typed and in scene units."""
        specs = self.node._specs()
        spec = specs[attribute]
        if spec.kind in NUMBER_KINDS:
            default = spec.default if spec.default is not None else 0.0
            if spec.kind == "time":
                default = default * TICKS_PER_SECOND
            return self._convert(float(default), spec.kind, True)
        values = []
        for position, child in enumerate(spec.children):
            child_spec = specs.get(child)
            if child_spec is not None and child_spec.kind in NUMBER_KINDS:
                values.append(self._default(child))
            elif isinstance(spec.default, list) and position < len(spec.default):
                values.append(float(spec.default[position]))
            else:
                values.append(0.0)
        return tuple(values)

    def _data_value(self) -> Any:
        """A string, array or matrix value, or the elements of a multi of them."""
        array = self._is_array()
        depth = len(self._path)
        found: dict[int, Any] = {}
        for record, record_path in self._records():
            if len(record_path) != depth or record_path[-1][0] != self._path[-1][0]:
                continue
            if not _prefix_matches(self._path, record_path, depth - 1):
                continue
            index = record_path[-1][1]
            if array:
                if index is None or index[0] != index[1]:
                    continue
                key = index[0]
            elif _contains(index, self._path[-1][1]):
                key = 0
            else:
                continue
            if record.type in ("STR", "STR#"):
                found[key] = record.get_value()
            else:
                value = record.data()
                if value is not None:
                    found[key] = value
        if array:
            return [self._typed_data(found[i]) for i in sorted(found)]
        if not found:
            # None for an attribute with no default data, as `getAttr` gives
            default = self._spec.default if self._spec is not None else None
            return list(default) if isinstance(default, list) else default
        return self._typed_data(found[0])

    def _typed_data(self, value: Any) -> Any:
        if self._spec is not None and self._spec.kind == "Int32Array":
            return [int(v) for v in value]
        return value

    @property
    def value(self) -> Any:
        """The value, in the scene's units. `NotImplementedError` for kinds
        of value not decoded yet (meshes, curves, component lists...), and
        for attributes that are not stored (outputs)."""
        spec = self._spec
        if spec is not None and not spec.storable:
            raise NotImplementedError(f"{self} is not stored, Maya computes it")
        if self._path[0][0] in _MESH_DATA and "mesh" in type_chain(self.node.type):
            raise NotImplementedError(f"{self} is mesh data, not decoded yet")
        if spec is not None and spec.kind in _DATA_KINDS:
            return self._data_value()
        if not self._is_numeric():
            # Strings of attributes no spec describes, reference edits
            records = [r for r in self._exact_records() if r.type in _RECORD_TYPES]
            if records:
                return records[-1].get_value()
            if spec is not None:
                raise NotImplementedError(f"{spec.kind} values are not decoded yet")
        internal = self.node.scene._storage.internal_units
        if spec is None:
            return self._stored_numbers(internal)
        leaves = self._number_leaves()
        if not leaves and self._in_internal_multi():
            raise NotImplementedError(f"{self} is kept by the node, not stored")
        if self._is_array():
            return [
                self._element_value(spec, leaves, (index,), internal)
                for index in sorted({key[0] for key in leaves})
            ]
        return self._element_value(spec, leaves, (), internal)

    @value.setter
    def value(self, value: Any) -> None:
        """Store a value given in the scene's units, in every record that
        holds the attribute. Refused, as Maya refuses it, for a locked or
        connected attribute. An attribute the file does not store gets a
        record of its own."""
        self._set_value(value)

    def _is_numeric(self) -> bool:
        spec = self._spec
        if spec is None:
            return False
        return spec.kind in NUMBER_KINDS or _number_compound(spec, self.node._specs())

    def _in_internal_multi(self) -> bool:
        """Whether the attribute is, or is in, a multi whose elements the node keeps:
        the file leaves those out (mesh vertices, animation key tangents)."""
        specs = self.node._specs()
        for name, _ in self._path:
            spec = specs.get(name)
            if spec is not None and spec.multi and spec.internal:
                return True
        return False

    def _stored_numbers(self, internal: bool) -> Any:
        """The numbers of the last record named like the attribute, as stored, for
        an attribute no spec describes. `.mb` times are found by their bits."""
        found = None
        for record, record_path in self._records():
            if record_path == self._path:
                numbers = record.numbers(())
                if numbers is not None and numbers[0]:
                    found = numbers
        if found is None:
            raise KeyError(f"{self.node.name} stores no value for {self.name!r}")
        values = []
        for number, kind in zip(*found):
            values.append(self._convert(number, kind, internal))
        return values[0] if len(values) == 1 else values

    def _element_value(
        self,
        spec: AttributeSpec,
        leaves: dict[Leaf, list[Source]],
        key: Leaf,
        internal: bool,
    ) -> Any:
        if spec.kind in NUMBER_KINDS:
            if key not in leaves:
                return self._default(spec.short_name)
            return self._convert(leaves[key][-1][2], spec.kind, internal)
        values = []
        defaults = self._default(spec.short_name)
        for child in range(len(spec.children)):
            sources = leaves.get(key + (child,))
            if sources is None:
                values.append(defaults[child])
            else:
                child_spec = self.node._specs()[spec.children[child]]
                values.append(self._convert(sources[-1][2], child_spec.kind, internal))
        return tuple(values)

    def _set_value(self, value: Any) -> None:
        spec = self._spec
        if spec is not None and not spec.storable:
            raise ValueError(f"{self} is not stored, Maya computes it")
        if self.locked:
            raise ValueError(f"{self} is locked")
        if self._is_connected():
            raise ValueError(f"{self} is connected")
        if spec is None or not self._is_numeric():
            self._set_data(value)
            return
        numbers: dict[Leaf, Any]
        if spec.kind in NUMBER_KINDS:
            numbers = {(): value}
        elif not self._is_array():
            if len(value) != len(spec.children):
                raise ValueError(f"{self} takes {len(spec.children)} values")
            numbers = {(child,): v for child, v in enumerate(value)}
        else:
            raise NotImplementedError(f"setting {spec.kind} values")
        if self._in_internal_multi() and not self._number_leaves():
            raise NotImplementedError(f"{self} is kept by the node, not stored")
        leaves = self._number_leaves()
        self.node.scene._before_edit()
        internal = self.node.scene._storage.internal_units
        if any(key not in leaves for key in numbers):
            # A record of its own for the attribute, which later records override
            kinds = self._kinds(spec.short_name)
            stored = []
            for position, key in enumerate(sorted(numbers)):
                stored.append(self._to_file(numbers[key], kinds[position], internal))
            self._add_record(stored)
            return
        for key, number in numbers.items():
            for record, position, _, kind in leaves[key]:
                record.set_number(position, kind, self._to_file(number, kind, internal))

    def _set_data(self, value: Any) -> None:
        kind = self._spec.kind if self._spec is not None else None
        if kind in _DATA_KINDS and kind not in ("string", "stringArray"):
            raise NotImplementedError(f"setting {kind} values")
        if kind == "string" and not isinstance(value, str):
            raise TypeError(f"{self} takes a str")
        if kind == "stringArray" and not isinstance(value, list):
            raise TypeError(f"{self} takes a list of str")
        if self._store(value):
            return
        if kind == "string":
            self._add_record(value)
            return
        raise NotImplementedError(f"{self} has no record to store the value in")

    def _store(self, value: AttributeValue) -> bool:
        """Write `value` into the attribute's own records of its type, with no
        lock or connection check. False when there are none."""
        record_type = _record_type(value)
        self.node.scene._before_edit()
        stored = False
        for record in self._exact_records():
            if record.type == record_type:
                record.set_value(value)
                stored = True
        return stored

    def _add_record(self, value: list[float] | str | None) -> None:
        """Add a record for the attribute, with its present flags."""
        assert self._spec is not None  # set only with a spec
        own_lock = self._stated_flags()[0].get(len(self._path), False)
        flags = RecordFlags(own_lock, self.keyable, self.channel_box)
        self._add_record_with(value, flags)

    def _add_record_with(
        self, value: list[float] | str | None, flags: RecordFlags
    ) -> None:
        spec = self._spec
        assert spec is not None  # set only with a spec
        defaults = RecordFlags(False, spec.keyable, spec.channel_box)
        kinds = self._kinds(spec.short_name)
        self.node._record.add_record(
            path_name(self._path), spec.kind, kinds, value, flags, defaults
        )
        self.node._forget_records()

    def _to_file(self, value: Any, kind: str | None, internal: bool) -> float:
        """A value in the scene's units as the file stores it."""
        number = float(value)
        if internal and kind is not None:
            units = self.node.scene.units
            if kind in _LINEAR_KINDS:
                number *= units.centimetres
            elif kind in _ANGLE_KINDS:
                number *= units.radians
            elif kind == "time":
                number *= units.ticks_per_frame
        if kind in _INT_KINDS or kind == "bool":
            return float(round(number))
        return number

    def _is_connected(self) -> bool:
        """Whether a connection drives the attribute, one of its parents or children."""
        for connection in self.node.scene.connections:
            node_name, _, attribute = connection.destination.partition(".")
            if self.node.scene._find(node_name.lstrip(":")) is not self.node:
                continue
            if _overlap(self._path, Attribute(self.node, attribute)._path):
                return True
        return False

    def _exact_records(self) -> list[AttributeRecord]:
        """Records of the attribute itself, not of a parent, child or wider range."""
        found = []
        for record, record_path in self._records():
            if record_path == self._path:
                found.append(record)
        return found

    # Flags

    def _stated_flags(self) -> tuple[dict[int, bool], bool | None, bool | None]:
        """Lock states by level of the attribute and its ancestors, then the
        keyable and channel box states the attribute's own records give last."""
        path = self._path
        depth = len(path)
        locks: dict[int, bool] = {}
        keyable = None
        channel_box = None
        for record, record_path in self._records():
            levels = len(record_path)
            if levels > depth or not _prefix_matches(path, record_path, levels - 1):
                continue
            name, index = path[levels - 1]
            record_name, record_index = record_path[-1]
            if name != record_name:
                continue
            if self._is_array() and levels == depth:
                if record_index is not None:
                    continue
            elif not _contains(record_index, index):
                continue
            flags = record.flags
            if flags.locked is not None:
                locks[levels] = flags.locked
            if levels == depth:
                if flags.keyable is not None:
                    keyable = flags.keyable
                if flags.channel_box is not None:
                    channel_box = flags.channel_box
        return locks, keyable, channel_box

    @property
    def locked(self) -> bool:
        """True when the attribute or one of its compound or multi parents is locked."""
        return any(self._stated_flags()[0].values())

    @locked.setter
    def locked(self, value: bool) -> None:
        # A locked parent keeps the attribute locked, as in Maya
        self._set_flags(RecordFlags(value, None, None))

    @property
    def keyable(self) -> bool:
        """Keyable. For strings, matrices and arrays, the attribute's flag:
        Maya leaves the keyable bit of their `.mb` records unset."""
        spec = self._spec
        if spec is not None and spec.kind in _DATA_KINDS:
            return spec.keyable
        stated = self._stated_flags()[1]
        if stated is not None:
            return stated
        return spec is not None and spec.keyable

    @keyable.setter
    def keyable(self, value: bool) -> None:
        self._set_flags(RecordFlags(None, value, None))

    @property
    def channel_box(self) -> bool:
        """Shown in the channel box while not keyable. Only numbers have the flag."""
        spec = self._spec
        if spec is not None and spec.kind not in NUMBER_KINDS:
            return False
        stated = self._stated_flags()[2]
        if stated is not None:
            return stated
        return spec is not None and spec.channel_box

    @channel_box.setter
    def channel_box(self, value: bool) -> None:
        self._set_flags(RecordFlags(None, None, value))

    def _set_flags(self, flags: RecordFlags) -> None:
        if self._spec is None:
            raise NotImplementedError(f"no spec tells how to store {self}")
        records = self._exact_records()
        self.node.scene._before_edit()
        for record in records:
            record.set_flags(flags)
        if not records:
            # A flags record of its own, the whole state with the change
            own_lock = self._stated_flags()[0].get(len(self._path), False)
            state = RecordFlags(
                own_lock if flags.locked is None else flags.locked,
                self.keyable if flags.keyable is None else flags.keyable,
                self.channel_box if flags.channel_box is None else flags.channel_box,
            )
            self._add_record_with(None, state)
