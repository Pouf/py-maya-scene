"""Scene records over a Maya ASCII (`.ma`) statement list.

Only the statements py_maya interprets are tokenized, lazily; edits replace
single tokens, so the rest of a statement keeps Maya's own layout.
"""

from __future__ import annotations

from ..ascii import Document, Token
from ..binary.scene_chunks import ReferenceEdit, ReferenceEdits
from ..data.node_types import DEFAULT_NODES, PARENT_TYPES, TYPE_NAMES
from ..mel import quote, unquote
from ..resolvers.file_attributes import type_chain
from .base import (
    AttributeRecord,
    AttributeValue,
    ConnectionRecord,
    NodeRecord,
    Plugin,
    ReferenceRecord,
    RelationshipRecord,
    SceneStorage,
    file_type_for,
)

# Statements that belong to the node created or selected just before them
_NODE_STATEMENTS = ("setAttr", "addAttr", "rename", "lockNode")
_TYPE_IDS = {name: type_id for type_id, name in TYPE_NAMES.items()}
_VALUE_TYPES = {"string": "STR", "stringArray": "STR#", "dataReferenceEdits": "REFE"}
# Reference edit kind > number of strings (kind 5: by its side), as in `.mb`
_EDIT_STRINGS = {0: 3, 1: 4, 2: 3, 3: 3, 4: 3, 8: 2, 9: 2}


def _flag_value(tokens: list[Token], flag: str) -> int | None:
    """Position of the token after `flag`, or None."""
    for position, token in enumerate(tokens[:-1]):
        if token.text == flag:
            return position + 1
    return None


class MaAttribute(AttributeRecord):
    """A `setAttr` statement."""

    def __init__(self, document: Document, index: int) -> None:
        self._document = document
        self._index = index

    def _tokens(self) -> list[Token]:
        return self._document.tokens(self._index)

    def _name_position(self, tokens: list[Token]) -> int:
        # Flags carry unquoted values; the first string is the attribute
        return next(i for i, t in enumerate(tokens) if t.text.startswith('"'))

    @property
    def name(self) -> str:
        tokens = self._tokens()
        return unquote(tokens[self._name_position(tokens)].text)[1:]

    @property
    def type(self) -> str:
        tokens = self._tokens()
        position = _flag_value(tokens, "-type")
        if position is None:
            return ""
        value_type = unquote(tokens[position].text)
        return _VALUE_TYPES.get(value_type, value_type)

    def get_value(self) -> AttributeValue:
        tokens = self._tokens()
        position = _flag_value(tokens, "-type")
        value_type = self.type
        if position is None:
            raise NotImplementedError("numeric values are not decoded yet")
        if value_type == "STR":
            return unquote(tokens[position + 1].text)
        if value_type == "STR#":
            count = int(tokens[position + 1].text)
            return [
                unquote(t.text) for t in tokens[position + 2 : position + 2 + count]
            ]
        if value_type == "REFE":
            return _decode_edits(tokens[position + 1 :])[0]
        raise NotImplementedError(f"{value_type} values are not decoded yet")

    def set_value(self, value: AttributeValue) -> None:
        tokens = self._tokens()
        position = _flag_value(tokens, "-type")
        value_type = self.type
        if value_type == "STR" and isinstance(value, str) and position is not None:
            self._document.replace_tokens(self._index, {position + 1: quote(value)})
        elif value_type == "STR#" and isinstance(value, list) and position is not None:
            count = int(tokens[position + 1].text)
            replacements = {position + 1: str(len(value))}
            string_slots = list(range(position + 2, position + 2 + count))
            for slot, text in zip(string_slots, value):
                replacements[slot] = quote(text)
            for slot in string_slots[len(value) :]:
                replacements[slot] = ""
            if len(value) > count:
                last = string_slots[-1] if string_slots else position + 1
                extra = " ".join(quote(t) for t in value[count:])
                replacements[last] = f"{replacements[last]} {extra}"
            self._document.replace_tokens(self._index, replacements)
        elif (
            value_type == "REFE"
            and isinstance(value, ReferenceEdits)
            and position is not None
        ):
            current, slots = _decode_edits(tokens[position + 1 :])
            before, after = _strings(current), _strings(value)
            if len(before) != len(after):
                raise NotImplementedError("adding or removing reference edits in .ma")
            self._document.replace_tokens(
                self._index,
                {
                    position + 1 + slot: quote(new) if quoted else new
                    for (slot, quoted), old, new in zip(slots, before, after)
                    if old != new
                },
            )
        else:
            raise TypeError(
                f"cannot store {type(value).__name__} in a {value_type or 'numeric'} value"
            )

    def mentions(self, text: str) -> bool:
        return self._document.contains(self._index, text)


def _decode_edits(tokens: list[Token]) -> tuple[ReferenceEdits, list[tuple[int, bool]]]:
    """Decode `dataReferenceEdits` tokens; also return each string's token
    position and whether it is quoted.

    `.ma` writes the groups of both sections in one list, without counts; the
    first section ends with the owner's own group (seen in every fixture and
    in production files). Maya 2018 to 2022 write the node of an addAttr edit
    as a bare word (`1 |grp|ns:node "note" ...`).
    """
    texts = [t.text for t in tokens]
    slots: list[tuple[int, bool]] = []

    def string(position: int) -> str:
        text = texts[position]
        quoted = text.startswith('"')
        slots.append((position, quoted))
        return unquote(text) if quoted else text

    owner = string(0)
    groups: list[tuple[str, list[ReferenceEdit]]] = []
    position = 1
    while position < len(texts):
        node = string(position)
        count = int(texts[position + 1])
        position += 2
        edits = []
        for _ in range(count):
            kind = int(texts[position])
            side = None
            position += 1
            if kind == 5:
                side = int(texts[position])
                position += 1
                strings_count = 6 if side == 0 else 4
            elif kind in _EDIT_STRINGS:
                strings_count = _EDIT_STRINGS[kind]
            else:
                raise ValueError(f"unknown reference edit kind {kind} in {owner}")
            strings = [string(position + i) for i in range(strings_count)]
            position += strings_count
            edits.append(ReferenceEdit(kind, side, strings))
        groups.append((node, edits))
    split = next((i + 1 for i, (node, _) in enumerate(groups) if node == owner), 0)
    return ReferenceEdits(owner, [groups[:split], groups[split:]]), slots


def _strings(edits: ReferenceEdits) -> list[str]:
    # Every string in file order: owner, then per group its node and edit strings
    strings = [edits.owner]
    for section in edits.sections:
        for node, group_edits in section:
            strings.append(node)
            for edit in group_edits:
                strings.extend(edit.strings)
    return strings


class MaNode(NodeRecord):
    """A `createNode` (or `select -ne`) statement and the statements after it."""

    def __init__(
        self, document: Document, index: int, end: int, storage: MaScene
    ) -> None:
        self._document = document
        self._index = index
        self._end = end
        self._storage = storage
        self._tokens_cache: list[Token] | None = None

    def _tokens(self) -> list[Token]:
        if self._tokens_cache is None:
            self._tokens_cache = self._document.tokens(self._index)
        return self._tokens_cache

    def _flag(self, flag: str) -> str | None:
        tokens = self._tokens()
        position = _flag_value(tokens, flag)
        return None if position is None else unquote(tokens[position].text)

    def _replace_flag(self, flag: str, value: str) -> None:
        position = _flag_value(self._tokens(), flag)
        if position is None:
            raise ValueError(f"{self.name} has no {flag} to change")
        self._document.replace_tokens(self._index, {position: quote(value)})
        self._tokens_cache = None

    @property
    def is_default(self) -> bool:
        return self._document.command(self._index) == "select"

    @property
    def name(self) -> str:
        if self.is_default:
            # `select -ne :time1;`
            return self._tokens()[-1].text.lstrip(":")
        name = self._flag("-n")
        return name if name is not None else ""

    def set_name(self, value: str) -> None:
        self._replace_flag("-n", value)

    @property
    def parent_name(self) -> str | None:
        return None if self.is_default else self._flag("-p")

    def set_parent_name(self, value: str) -> None:
        self._replace_flag("-p", value)

    @property
    def type_name(self) -> str:
        if self.is_default:
            return DEFAULT_NODES.get(self.name, "")
        return self._tokens()[1].text

    @property
    def type_id(self) -> int | None:
        return None if self.is_default else _TYPE_IDS.get(self.type_name)

    @property
    def uuid(self) -> str | None:
        for index in range(self._index + 1, self._end):
            if self._document.command(index) == "rename":
                tokens = self._document.tokens(index)
                position = _flag_value(tokens, "-uid")
                if position is not None:
                    return unquote(tokens[position].text)
        return None

    @property
    def is_dag(self) -> bool:
        if self.is_default:
            return False
        if self.parent_name is not None:
            return True
        node_type = self.type_name
        if node_type in PARENT_TYPES or node_type in (
            "containerBase",
            "entity",
            "dagNode",
        ):
            return "dagNode" in type_chain(node_type)
        # A plugin type unknown to the schema tables: DAG if something is under it
        return self.name in self._storage._parent_names()

    @property
    def is_transform(self) -> bool:
        return not self.is_default and "transform" in type_chain(self.type_name)

    def attributes(self) -> list[AttributeRecord]:
        return [
            MaAttribute(self._document, index)
            for index in range(self._index + 1, self._end)
            if self._document.command(index) == "setAttr"
        ]

    def string_attributes(self) -> list[AttributeRecord]:
        document = self._document
        return [
            MaAttribute(document, index)
            for index in range(self._index + 1, self._end)
            if document.command(index) == "setAttr"
            and (
                document.contains(index, '-type "string"')
                or document.contains(index, '-type "stringArray"')
            )
        ]


class MaReference(ReferenceRecord):
    """A `file -rdi` statement; depth 1 also has a `file -r` statement."""

    def __init__(self, document: Document, index: int, storage: MaScene) -> None:
        self._document = document
        self._index = index
        self._storage = storage

    def _value(self, flag: str) -> str | None:
        tokens = self._document.tokens(self._index)
        position = _flag_value(tokens, flag)
        return None if position is None else tokens[position].text

    @property
    def path(self) -> str:
        return unquote(self._document.tokens(self._index)[-1].text)

    def set_path(self, value: str) -> None:
        indices = [self._index]
        if self.depth == 1:
            indices += [
                i
                for i in self._storage._top_references
                if _rfn(self._document, i) == self.node
            ]
        file_type = file_type_for(value, self.file_type)
        for index in indices:
            tokens = self._document.tokens(index)
            replacements = {len(tokens) - 1: quote(value)}
            position = _flag_value(tokens, "-typ")
            if position is not None:
                replacements[position] = quote(file_type)
            self._document.replace_tokens(index, replacements)

    @property
    def namespace(self) -> str:
        value = self._value("-ns")
        return "" if value is None else unquote(value)

    @property
    def node(self) -> str:
        return _rfn(self._document, self._index)

    @property
    def depth(self) -> int:
        value = self._value("-rdi")
        return 1 if value is None else int(value)

    @property
    def deferred(self) -> bool:
        return self._value("-dr") == "1"

    @property
    def file_type(self) -> str:
        value = self._value("-typ")
        return "" if value is None else unquote(value)


def _rfn(document: Document, index: int) -> str:
    tokens = document.tokens(index)
    position = _flag_value(tokens, "-rfn")
    return "" if position is None else unquote(tokens[position].text)


class MaConnection(ConnectionRecord):
    """`connectAttr "source" "destination" [-na] ...`."""

    def __init__(self, document: Document, index: int) -> None:
        self._document = document
        self._index = index

    def _strings(self) -> list[int]:
        tokens = self._document.tokens(self._index)
        return [i for i, t in enumerate(tokens) if t.text.startswith('"')][:2]

    @property
    def source(self) -> str:
        return unquote(self._document.tokens(self._index)[self._strings()[0]].text)

    @property
    def destination(self) -> str:
        return unquote(self._document.tokens(self._index)[self._strings()[1]].text)

    @property
    def next_available(self) -> bool:
        return any(t.text == "-na" for t in self._document.tokens(self._index))

    def set_plugs(self, source: str, destination: str) -> None:
        first, second = self._strings()
        self._document.replace_tokens(
            self._index, {first: quote(source), second: quote(destination)}
        )

    def mentions(self, text: str) -> bool:
        return self._document.contains(self._index, text)


class MaRelationship(RelationshipRecord):
    """`relationship "kind" "node" "plug" ...`."""

    def __init__(self, document: Document, index: int) -> None:
        self._document = document
        self._index = index

    def _values(self) -> list[str]:
        return [unquote(t.text) for t in self._document.tokens(self._index)[1:]]

    @property
    def kind(self) -> str:
        return self._values()[0]

    @property
    def node(self) -> str:
        return self._values()[1]

    @property
    def plugs(self) -> list[str]:
        return self._values()[2:]

    def set_names(self, node: str, plugs: list[str]) -> None:
        replacements = {2: quote(node)}
        replacements.update({3 + i: quote(p) for i, p in enumerate(plugs)})
        self._document.replace_tokens(self._index, replacements)

    def mentions(self, text: str) -> bool:
        return self._document.contains(self._index, text)


class MaScene(SceneStorage):
    """A `.ma` file."""

    def __init__(self, data: bytes) -> None:
        document = self._document = Document(data)
        self.codepage = document.encoding
        self._nodes: list[NodeRecord] = []
        self._references: list[ReferenceRecord] = []
        self._top_references: list[int] = []
        self._connections: list[int] = []
        self._relationships: list[int] = []
        self._file_info: list[int] = []
        self._requires: list[int] = []
        self._metadata: int | None = None
        self._parents: set[str] | None = None
        node_start = None
        for index in range(len(document)):
            command = document.command(index)
            if node_start is not None and command in _NODE_STATEMENTS:
                continue
            if node_start is not None:
                self._nodes.append(MaNode(document, node_start, index, self))
                node_start = None
            if command == "createNode" or (
                command == "select" and document.contains(index, "-ne")
            ):
                node_start = index
            elif command == "file":
                if document.contains(index, "-rdi"):
                    self._references.append(MaReference(document, index, self))
                elif document.contains(index, "-r "):
                    self._top_references.append(index)
            elif command == "connectAttr":
                self._connections.append(index)
            elif command == "relationship":
                self._relationships.append(index)
            elif command == "fileInfo":
                self._file_info.append(index)
            elif command == "requires":
                self._requires.append(index)
            elif command == "applyMetadata":
                self._metadata = index
        if node_start is not None:
            self._nodes.append(MaNode(document, node_start, len(document), self))

    def _parent_names(self) -> set[str]:
        if self._parents is None:
            self._parents = set()
            for node in self._nodes:
                parent = node.parent_name
                if parent is not None:
                    self._parents.add(parent.rsplit("|", 1)[-1])
        return self._parents

    @property
    def version(self) -> str:
        for index in self._requires:
            tokens = self._document.tokens(index)
            if len(tokens) > 2 and tokens[1].text == "maya":
                return unquote(tokens[2].text)
        return ""

    @property
    def file_info(self) -> dict[str, str]:
        info = {}
        for index in self._file_info:
            tokens = self._document.tokens(index)
            info[unquote(tokens[1].text)] = unquote(tokens[2].text)
        return info

    @property
    def plugins(self) -> list[Plugin]:
        plugins = []
        for index in self._requires:
            tokens = self._document.tokens(index)
            if tokens[1].text == "maya":
                continue
            node_types = [
                unquote(tokens[i + 1].text)
                for i, t in enumerate(tokens[:-1])
                if t.text == "-nodeType"
            ]
            plugins.append(
                Plugin(unquote(tokens[-2].text), unquote(tokens[-1].text), node_types)
            )
        return plugins

    def nodes(self) -> list[NodeRecord]:
        return self._nodes

    def references(self) -> list[ReferenceRecord]:
        return self._references

    def connections(self) -> list[ConnectionRecord]:
        return [MaConnection(self._document, i) for i in self._connections]

    def relationships(self) -> list[RelationshipRecord]:
        return [MaRelationship(self._document, i) for i in self._relationships]

    def metadata(self) -> str | None:
        if self._metadata is None:
            return None
        tokens = self._document.tokens(self._metadata)
        position = _flag_value(tokens, "-v")
        return None if position is None else unquote(tokens[position].text)

    def set_metadata(self, text: str) -> None:
        assert self._metadata is not None  # only called after metadata() found a table
        position = _flag_value(self._document.tokens(self._metadata), "-v")
        assert position is not None
        self._document.replace_tokens(self._metadata, {position: quote(text)})

    def to_bytes(self) -> bytes:
        return self._document.to_bytes()
