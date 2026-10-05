"""File references."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..storage.base import ReferenceRecord
    from .scene import Scene


class Reference:
    """A reference to another scene file, at any depth (`file -rdi`).

    A reference made by this file (depth 1) is also stored by `file -r`; the
    format keeps both in step when the path changes.
    """

    def __init__(self, _record: ReferenceRecord, scene: Scene) -> None:
        self._record = _record
        self.scene = scene

    @property
    def path(self) -> str:
        """Path as stored, unresolved (environment variables kept, no `{n}` copy number)."""
        return self._record.path

    @path.setter
    def path(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError(f"path must be a str, not {type(value).__name__}")
        self.scene._before_edit()
        self._record.set_path(value)

    @property
    def namespace(self) -> str:
        return self._record.namespace

    @property
    def node(self) -> str:
        """Name of the reference node (`childRN`, `deps:childRN` when nested)."""
        return self._record.node

    @property
    def depth(self) -> int:
        """1 for a reference made by this file, more for one made by a referenced file."""
        return self._record.depth

    @property
    def deferred(self) -> bool:
        """True when the reference is not loaded on open (`-dr 1`)."""
        return self._record.deferred

    @property
    def file_type(self) -> str:
        """`mayaBinary`, `mayaAscii`, or another translator's name.

        Setting `path` to a `.ma` or `.mb` file switches it accordingly, as
        Maya would otherwise read the file with the wrong translator.
        """
        return self._record.file_type

    def __repr__(self) -> str:
        return f"Reference({self.node!r}, {self.path!r})"
