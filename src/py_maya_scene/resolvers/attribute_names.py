"""Short names of attributes named by their long name."""

from __future__ import annotations

import re
from functools import lru_cache

from ..data.node_types import ATTRIBUTE_NAMES
from .file_attributes import type_chain

_NAME = re.compile(r"[^.\[\]]+")


@lru_cache(maxsize=None)
def _short_names(node_type: str) -> dict[str, str]:
    """Long name > short name of the attributes of `node_type` whose names differ."""
    names: dict[str, str] = {}
    for current in type_chain(node_type):
        words = ATTRIBUTE_NAMES.get(current, "").split()
        names.update(zip(words[1::2], words[::2]))
    return names


def short_attribute_name(
    node_type: str, name: str, added: dict[str, str] | None = None
) -> str:
    """`name` with each long name of an attribute of `node_type` made short
    (`uvSet[0].uvSetName` > `uvst[0].uvsn`). `added` maps the long names of
    the node's added attributes to their short names."""
    short_names = _short_names(node_type)
    added = added or {}

    def short(match: re.Match[str]) -> str:
        word = match[0]
        return added[word] if word in added else short_names.get(word, word)

    return _NAME.sub(short, name)
