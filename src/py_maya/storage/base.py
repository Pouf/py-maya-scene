"""What the scene model needs from a file format.

`.mb` (chunks) and `.ma` (MEL statements) store the same scene; each format
implements these records over its own storage, and the models in
`py_maya.models` (renaming, remapping, dependencies) work on records only.
Setters write through to the storage; untouched records are never re-encoded.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, NamedTuple, Union

from ..binary.scene_chunks import ReferenceEdits

AttributeValue = Union[str, List[str], ReferenceEdits]

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

    @property
    @abstractmethod
    def version(self) -> str: ...

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

    @abstractmethod
    def to_bytes(self) -> bytes: ...
