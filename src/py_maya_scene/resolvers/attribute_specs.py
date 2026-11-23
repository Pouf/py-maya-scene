"""What is known of each attribute of a node: kind of value, parent,
children, default and flags."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import TYPE_CHECKING, Any, NamedTuple

from ..data.attribute_specs import ATTRIBUTE_SPECS
from .file_attributes import type_chain

if TYPE_CHECKING:
    from ..binary.scene_chunks import AddedAttribute

# Kinds holding one number
NUMBER_KINDS = frozenset(
    "bool byte char short long int64 float double linear flinear angle fangle "
    "time enum".split()
)


class AttributeSpec(NamedTuple):
    """An attribute of a node, by short name.

    `kind` is the kind of value: a number kind (`bool`, `long`, `double`,
    `linear`, `angle`, `time`...), a numeric compound (`double3`, `float2`...),
    `compound`, `matrix`, or a data kind (`string`, `doubleArray`...).
    `default` is in internal units, centimetres, radians and seconds.
    """

    short_name: str
    kind: str
    parent: str | None
    children: tuple[str, ...]
    default: Any
    multi: bool
    keyable: bool
    channel_box: bool
    storable: bool


@lru_cache(maxsize=None)
def _declared(node_type: str) -> dict[str, AttributeSpec]:
    specs = {}
    text = ATTRIBUTE_SPECS.get(node_type)
    if text:
        for short_name, kind, parent, children, default, flags in json.loads(text):
            specs[short_name] = AttributeSpec(
                short_name,
                kind,
                parent,
                tuple(children or ()),
                default,
                "a" in flags,
                "k" in flags,
                "c" in flags,
                "s" in flags,
            )
    return specs


@lru_cache(maxsize=None)
def attribute_specs(node_type: str) -> dict[str, AttributeSpec]:
    """Specs of the attributes of `node_type`, inherited ones included."""
    specs: dict[str, AttributeSpec] = {}
    for current in reversed(type_chain(node_type)):
        specs.update(_declared(current))
    return specs


def added_attribute_specs(added: list[AddedAttribute]) -> dict[str, AttributeSpec]:
    """Specs of the attributes a file adds to a node."""
    short_names = {a.long_name: a.short_name for a in added}
    children: dict[str, list[str]] = {}
    for attribute in added:
        if attribute.parent is not None:
            children.setdefault(attribute.parent, []).append(attribute.short_name)
    specs = {}
    for attribute in added:
        default: Any = attribute.default
        # `.ma` leaves a default of 0 out
        if default is None and attribute.kind in NUMBER_KINDS:
            default = 0.0
        parent = attribute.parent
        specs[attribute.short_name] = AttributeSpec(
            attribute.short_name,
            attribute.kind,
            None if parent is None else short_names.get(parent, parent),
            tuple(children.get(attribute.long_name, ())),
            default,
            attribute.multi,
            attribute.keyable,
            False,
            attribute.storable,
        )
    return specs
