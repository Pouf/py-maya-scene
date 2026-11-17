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


def short_attribute_name(node_type: str, name: str) -> str:
    """`name` with each long name of an attribute of `node_type` made short
    (`uvSet[0].uvSetName` > `uvst[0].uvsn`)."""
    short_names = _short_names(node_type)
    return _NAME.sub(lambda match: short_names.get(match[0], match[0]), name)
