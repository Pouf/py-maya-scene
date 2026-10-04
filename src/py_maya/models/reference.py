"""File references."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..binary import Chunk
from ..binary.scene_chunks import decode_reference

if TYPE_CHECKING:
    from .scene import Scene


class Reference:
    """A reference to another scene file, at any depth.

    Backed by its `FRDI` chunk (`file -rdi`). A reference made by this file
    (depth 1) also has a `FREF` chunk (`file -r`) and an entry in the trailer
    `INCL`, which the scene keeps in step.
    """

    def __init__(self, _chunk: Chunk, scene: Scene) -> None:
        self._chunk = _chunk
        self.scene = scene

    @property
    def path(self) -> str:
        """Path as stored, unresolved (environment variables kept, no `{n}` copy number)."""
        return decode_reference(self._chunk.data, True).path

    @path.setter
    def path(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError(f"path must be a str, not {type(value).__name__}")
        self.scene._set_reference_path(self, value)

    @property
    def namespace(self) -> str:
        return decode_reference(self._chunk.data, True).namespace

    @property
    def node(self) -> str:
        """Name of the reference node (`childRN`, `deps:childRN` when nested)."""
        return decode_reference(self._chunk.data, True).node

    @property
    def depth(self) -> int:
        """1 for a reference made by this file, more for one made by a referenced file."""
        return decode_reference(self._chunk.data, True).depth

    @property
    def deferred(self) -> bool:
        """True when the reference is not loaded on open (`-dr 1`)."""
        return decode_reference(self._chunk.data, True).deferred

    def __repr__(self) -> str:
        return f"Reference({self.node!r}, {self.path!r})"
