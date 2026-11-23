"""What the scene model needs from a file format.

`.mb` (chunks) and `.ma` (MEL statements) store the same scene; each format
implements these records over its own storage, and the models in
`py_maya_scene.models` (renaming, remapping, dependencies) work on records only.
Setters write through to the storage; untouched records are never re-encoded.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, List, NamedTuple, Optional, Sequence, Tuple, Union

from ..binary.scene_chunks import AddedAttribute, DataValue, ReferenceEdits
from ..units import Units

AttributeValue = Union[str, List[str], ReferenceEdits]
Numbers = Optional[Tuple[List[float], List[Optional[str]]]]


class RecordFlags(NamedTuple):
    """Flags a record states for its plug, None for those it leaves unsaid."""

    locked: bool | None
    keyable: bool | None
    channel_box: bool | None


_MAYA_FILE_TYPES = {".ma": "mayaAscii", ".mb": "mayaBinary"}


def file_type_for(path: str, current: str) -> str:
    """The file type a reference to `path` needs: Maya's own types follow the
    extension; another translator's type is kept."""
    if current not in _MAYA_FILE_TYPES.values():
        return current
    extension = path[path.rfind(".") :].lower() if "." in path else ""
    return _MAYA_FILE_TYPES.get(extension, current)


class Plugin(NamedTuple):
    """A required plugin (`requires`) and the node types the file uses from it."""

    name: str
    version: str
    node_types: list[str]


class AttributeRecord(ABC):
    """A stored attribute value."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name as stored: short name with indices and parents (`uvst[0].uvsn`)."""

    @property
    @abstractmethod
    def type(self) -> str:
        """`STR`, `STR#`, `REFE` for the decoded types, else a format-specific name."""

    @abstractmethod
    def get_value(self) -> AttributeValue:
        """The value; raises `NotImplementedError` for types not decoded yet."""

    @abstractmethod
    def set_value(self, value: AttributeValue) -> None: ...

    @abstractmethod
    def get_number(self) -> float:
        """The value of an attribute the caller knows holds one double,
        boolean, integer or enum (`.mb` stores times as integer ticks under
        the same tag); `NotImplementedError` for any other storage."""

    @property
    @abstractmethod
    def flags(self) -> RecordFlags: ...

    @abstractmethod
    def set_flags(self, flags: RecordFlags) -> None:
        """State the flags of `flags` that are not None, keep the others."""

    @abstractmethod
    def numbers(self, kinds: Sequence[str | None]) -> Numbers:
        """The numbers the record holds in the file's units and the kind of
        each, or None when it holds no numbers. `kinds` gives the kind of each
        number of an element, in turn."""

    @abstractmethod
    def set_number(self, position: int, kind: str | None, value: float) -> None:
        """Store number `position` of `numbers()`, in the file's units."""

    @abstractmethod
    def data(self) -> DataValue | None:
        """A matrix or array value (`xform` matrices as `Xform`), else None."""

    @abstractmethod
    def mentions(self, text: str) -> bool:
        """Quick test: could the stored value contain `text`."""


class NodeRecord(ABC):
    """A node the file creates, or a default node whose attributes it edits."""

    @property
    @abstractmethod
    def is_default(self) -> bool: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def set_name(self, value: str) -> None: ...

    @property
    @abstractmethod
    def parent_name(self) -> str | None: ...

    @abstractmethod
    def set_parent_name(self, value: str) -> None: ...

    @property
    @abstractmethod
    def type_name(self) -> str: ...

    @property
    @abstractmethod
    def type_id(self) -> int | None: ...

    @property
    @abstractmethod
    def uuid(self) -> str | None: ...

    @property
    @abstractmethod
    def is_dag(self) -> bool: ...

    @property
    @abstractmethod
    def is_transform(self) -> bool: ...

    @abstractmethod
    def attributes(self) -> list[AttributeRecord]: ...

    @abstractmethod
    def string_attributes(self) -> list[AttributeRecord]:
        """The `STR`/`STR#` attributes only, found without decoding the others."""

    @abstractmethod
    def added_attributes(self) -> list[AddedAttribute]:
        """The attributes the file adds to the node with `addAttr`, in file order."""

    @abstractmethod
    def add_record(
        self,
        name: str,
        kind: str,
        kinds: Sequence[str | None],
        value: Sequence[float] | str | None,
        flags: RecordFlags,
        defaults: RecordFlags,
    ) -> AttributeRecord:
        """Add a record for attribute `name` after the node's others.

        `value` is numbers in the file's units (`kinds` gives the kind of each
        number of an element), a string, or None for flags alone. `kind` is
        the attribute's kind of value. `flags` is the plug's whole flag state,
        `defaults` the attribute's.
        """


class ReferenceRecord(ABC):
    """A reference at any depth."""

    @property
    @abstractmethod
    def path(self) -> str: ...

    @abstractmethod
    def set_path(self, value: str) -> None:
        """Store a new path everywhere the format keeps it for this reference,
        and switch the file type between `mayaBinary` and `mayaAscii` when the
        extension says so."""

    @property
    @abstractmethod
    def file_type(self) -> str:
        """`mayaBinary`, `mayaAscii`, or another translator's name."""

    @property
    @abstractmethod
    def namespace(self) -> str: ...

    @property
    @abstractmethod
    def node(self) -> str: ...

    @property
    @abstractmethod
    def depth(self) -> int: ...

    @property
    @abstractmethod
    def deferred(self) -> bool: ...


class ConnectionRecord(ABC):
    """A `connectAttr`."""

    @property
    @abstractmethod
    def source(self) -> str: ...

    @property
    @abstractmethod
    def destination(self) -> str: ...

    @property
    @abstractmethod
    def next_available(self) -> bool: ...

    @abstractmethod
    def set_plugs(self, source: str, destination: str) -> None: ...

    @abstractmethod
    def mentions(self, text: str) -> bool: ...


class InstanceRecord(ABC):
    """`parent -add`: an extra parent for an existing DAG node (instancing)."""

    @property
    @abstractmethod
    def child(self) -> str:
        """The instanced node, as stored (a DAG path)."""

    @property
    @abstractmethod
    def parent(self) -> str:
        """The extra parent, as stored."""

    @abstractmethod
    def set_names(self, child: str, parent: str) -> None: ...

    @abstractmethod
    def mentions(self, text: str) -> bool: ...


class RelationshipRecord(ABC):
    """A `relationship` (light linking and the like)."""

    @property
    @abstractmethod
    def kind(self) -> str: ...

    @property
    @abstractmethod
    def node(self) -> str: ...

    @property
    @abstractmethod
    def plugs(self) -> list[str]: ...

    @abstractmethod
    def set_names(self, node: str, plugs: list[str]) -> None: ...

    @abstractmethod
    def mentions(self, text: str) -> bool: ...


class SceneStorage(ABC):
    """A whole file."""

    codepage: str
    """Python encoding of the system code page the file was saved under."""

    internal_units: bool
    """True when values are stored in centimetres, radians and ticks (`.mb`),
    False when in the scene's units (`.ma`)."""

    @property
    @abstractmethod
    def version(self) -> str: ...

    @property
    @abstractmethod
    def units(self) -> Units:
        """Linear, angle and time units, Maya's defaults when the file names none."""

    @property
    @abstractmethod
    def file_info(self) -> dict[str, str]: ...

    @property
    @abstractmethod
    def plugins(self) -> list[Plugin]: ...

    @abstractmethod
    def nodes(self) -> list[NodeRecord]: ...

    @abstractmethod
    def references(self) -> list[ReferenceRecord]:
        """References at every depth, in file order."""

    @abstractmethod
    def connections(self) -> list[ConnectionRecord]: ...

    @abstractmethod
    def relationships(self) -> list[RelationshipRecord]: ...

    @abstractmethod
    def instances(self) -> list[InstanceRecord]: ...

    @abstractmethod
    def metadata(self) -> str | None:
        """Metadata text in Maya's raw format (externalContentTable), if any."""

    @abstractmethod
    def set_metadata(self, text: str) -> None: ...

    def replace_strings(self, replace: Callable[[str], str]) -> int:
        """Put names, paths and string values through `replace` (see
        `Scene.replace_strings`), the metadata excepted; return how many changed."""
        raise NotImplementedError("replacing strings is only supported in .mb files")

    @abstractmethod
    def to_bytes(self) -> bytes: ...
