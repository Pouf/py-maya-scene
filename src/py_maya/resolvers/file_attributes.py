"""Which attributes of a node type hold file paths."""

from __future__ import annotations

from functools import lru_cache

from ..data.file_attributes import EXTRA_FILE_ATTRIBUTES
from ..data.node_types import FILE_ATTRIBUTES, PARENT_TYPES


def type_chain(node_type: str) -> list[str]:
    """`node_type` followed by its ancestors, nearest first."""
    chain = []
    current: str | None = node_type
    while current:
        chain.append(current)
        current = PARENT_TYPES.get(current)
    return chain


@lru_cache(maxsize=None)
def file_attribute_names(node_type: str) -> frozenset[str]:
    """Short names of the file path attributes of `node_type`, inherited ones included."""
    names: set[str] = set()
    for current in type_chain(node_type):
        names.update(short for short, _ in FILE_ATTRIBUTES.get(current, ()))
        names.update(EXTRA_FILE_ATTRIBUTES.get(current, ()))
    return frozenset(names)
