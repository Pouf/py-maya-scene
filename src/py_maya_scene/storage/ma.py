"""Scene records over a Maya ASCII (`.ma`) statement list.

Only the statements py_maya_scene interprets are tokenized, lazily; edits replace
single tokens, so the rest of a statement keeps Maya's own layout.
"""

from __future__ import annotations

from typing import Sequence

from ..ascii import Document, Token
from ..binary.scene_chunks import (
    AddedAttribute,
    DataValue,
    ReferenceEdit,
    ReferenceEdits,
    group_numbers,
    xform_from_numbers,
)
from ..data.node_types import DEFAULT_NODES, PARENT_TYPES, TYPE_NAMES
from ..mel import number, number_literal, quote, unquote
from ..resolvers.file_attributes import type_chain
from ..units import Units, short_unit_name
from .base import (
    AttributeRecord,
    AttributeValue,
    ConnectionRecord,
    InstanceRecord,
    NodeRecord,
    Numbers,
    Plugin,
    RecordFlags,
    ReferenceRecord,
    RelationshipRecord,
    SceneStorage,
    file_type_for,
)

# Statements that belong to the node created or selected just before them
_NODE_STATEMENTS = ("setAttr", "addAttr", "rename", "lockNode")
_TYPE_IDS = {name: type_id for type_id, name in TYPE_NAMES.items()}
_VALUE_TYPES = {"string": "STR", "stringArray": "STR#", "dataReferenceEdits": "REFE"}
# `setAttr -type` of values made of numbers alone, None for no type
_NUMBER_TYPES = (
    None,
    "double2",
    "double3",
    "double4",
    "float2",
    "float3",
    "long2",
    "long3",
    "short2",
    "short3",
)
# Numbers per element of the array types
_ARRAY_SIZES = {"doubleArray": 1, "Int32Array": 1, "pointArray": 4, "vectorArray": 3}
# Reference edit kind > number of strings (kind 5: by its side), as in `.mb`
_EDIT_STRINGS = {0: 3, 1: 4, 2: 3, 3: 3, 4: 3, 8: 2, 9: 2}


# Long spellings of the flags read here, for the commands that read them:
# Maya writes short flags, but scripts that write `.ma` files may not
# (`createNode transform -name "ALL"`). A short flag of two commands has the
# long spelling of each (`-l`, `currentUnit -linear` and `setAttr -lock`)
_LONG_FLAGS = {
    "-n": ("-name",),
    "-p": ("-parent",),
    "-uid": ("-uuid",),
    "-r": ("-reference",),
    "-rdi": ("-referenceDepthInfo",),
    "-ns": ("-namespace",),
    "-dr": ("-deferReference",),
    "-rfn": ("-referenceNode",),
    "-typ": ("-type",),
    "-na": ("-nextAvailable",),
    "-ne": ("-noExpand",),
    "-add": ("-addObject",),
    "-nt": ("-nodeType",),
    "-v": ("-value",),
    "-ln": ("-longName",),
    "-sn": ("-shortName",),
    "-uaf": ("-usedAsFilename",),
    "-l": ("-linear", "-lock"),
    "-a": ("-angle",),
    "-t": ("-time",),
    "-at": ("-attributeType",),
    "-dt": ("-dataType",),
    "-dv": ("-defaultValue",),
    "-k": ("-keyable",),
    "-m": ("-multi",),
    "-s": ("-storable", "-size"),
    "-cb": ("-channelBox",),
}
# Value kind of `addAttr -at` and `-dt` types, named as in attribute specs
_ADDED_KINDS = {
    name: name
    for name in (
        "bool byte char short long float double time enum message matrix "
        "fltMatrix float2 float3 double2 double3 double4 long2 long3 short2 "
        "short3 compound string stringArray doubleArray Int32Array pointArray "
        "vectorArray mesh nurbsCurve nurbsSurface componentList attributeAlias"
    ).split()
}
_ADDED_KINDS.update(
    {
        "doubleLinear": "linear",
        "floatLinear": "flinear",
        "doubleAngle": "angle",
        "floatAngle": "fangle",
        "reflectance": "float3",
        "spectrum": "float3",
        "reflectanceRGB": "float3",
        "spectrumRGB": "float3",
    }
)


def _is_flag(token: Token, flag: str) -> bool:
    """Whether `token` is `flag`, in its short or long spelling."""
    return token.text == flag or token.text in _LONG_FLAGS[flag]


def _flag_value(tokens: list[Token], flag: str) -> int | None:
    """Position of the token after `flag`, or None."""
    for position, token in enumerate(tokens[:-1]):
        if _is_flag(token, flag):
            return position + 1
    return None


def _flag_text(document: Document, tokens: list[Token], flag: str) -> str | None:
    """The text of the value after `flag`, or None."""
    position = _flag_value(tokens, flag)
    return None if position is None else _read(document, tokens[position].text)


def _has_flag(document: Document, index: int, flag: str) -> bool:
    return any(_is_flag(t, flag) for t in document.tokens(index))


def _read(document: Document, token: str) -> str:
    """The text of a string literal or bare word, in the file's code page."""
    return document.decode(unquote(token) if token.startswith('"') else token)


def _literal(document: Document, text: str, quoted: bool = True) -> str:
    """Token text for `text`: a string literal (or a bare word), in the file's code page."""
    return document.encode(quote(text) if quoted else text)


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
        return _read(self._document, tokens[self._name_position(tokens)].text)[1:]

    @property
    def type(self) -> str:
        tokens = self._tokens()
        position = _flag_value(tokens, "-typ")
        if position is None:
            return ""
        value_type = _read(self._document, tokens[position].text)
        return _VALUE_TYPES.get(value_type, value_type)

    def get_value(self) -> AttributeValue:
        tokens = self._tokens()
        position = _flag_value(tokens, "-typ")
        value_type = self.type
        if position is None:
            raise NotImplementedError("numeric values are not decoded yet")
        if value_type == "STR":
            return _read(self._document, tokens[position + 1].text)
        if value_type == "STR#":
            count = int(tokens[position + 1].text)
            return [
                _read(self._document, t.text)
                for t in tokens[position + 2 : position + 2 + count]
            ]
        if value_type == "REFE":
            return _decode_edits(self._document, tokens[position + 1 :])[0]
        raise NotImplementedError(f"{value_type} values are not decoded yet")

    def set_value(self, value: AttributeValue) -> None:
        tokens = self._tokens()
        position = _flag_value(tokens, "-typ")
        value_type = self.type
        if value_type == "STR" and isinstance(value, str) and position is not None:
            self._document.replace_tokens(
                self._index, {position + 1: _literal(self._document, value)}
            )
        elif value_type == "STR#" and isinstance(value, list) and position is not None:
            count = int(tokens[position + 1].text)
            replacements = {position + 1: str(len(value))}
            string_slots = list(range(position + 2, position + 2 + count))
            for slot, text in zip(string_slots, value):
                replacements[slot] = _literal(self._document, text)
            for slot in string_slots[len(value) :]:
                replacements[slot] = ""
            if len(value) > count:
                last = string_slots[-1] if string_slots else position + 1
                extra = " ".join(_literal(self._document, t) for t in value[count:])
                replacements[last] = f"{replacements[last]} {extra}"
            self._document.replace_tokens(self._index, replacements)
        elif (
            value_type == "REFE"
            and isinstance(value, ReferenceEdits)
            and position is not None
        ):
            current, slots = _decode_edits(self._document, tokens[position + 1 :])
            before, after = _strings(current), _strings(value)
            if len(before) != len(after):
                raise NotImplementedError("adding or removing reference edits in .ma")
            self._document.replace_tokens(
                self._index,
                {
                    position + 1 + slot: _literal(self._document, new, quoted)
                    for (slot, quoted), old, new in zip(slots, before, after)
                    if old != new
                },
            )
        else:
            raise TypeError(
                f"cannot store {type(value).__name__} in a {value_type or 'numeric'} value"
            )

    def get_number(self) -> float:
        tokens = self._tokens()
        values = tokens[self._name_position(tokens) + 1 :]
        if len(values) != 1:
            raise NotImplementedError("only single values are decoded")
        return number(values[0].text)

    @property
    def flags(self) -> RecordFlags:
        tokens = self._tokens()
        before_name = tokens[: self._name_position(tokens)]
        stated: list[bool | None] = []
        for flag in ("-l", "-k", "-cb"):
            position = _flag_value(before_name, flag)
            if position is None:
                stated.append(None)
            else:
                stated.append(bool(number(before_name[position].text)))
        return RecordFlags(*stated)

    def set_flags(self, flags: RecordFlags) -> None:
        tokens = self._tokens()
        before_name = tokens[: self._name_position(tokens)]
        replacements = {}
        command = tokens[0].text
        for flag, state in (
            ("-l", flags.locked),
            ("-k", flags.keyable),
            ("-cb", flags.channel_box),
        ):
            if state is None:
                continue
            text = "on" if state else "off"
            position = _flag_value(before_name, flag)
            if position is None:
                command += f" {flag} {text}"
            else:
                replacements[position] = text
        if command != tokens[0].text:
            replacements[0] = command
        if replacements:
            self._document.replace_tokens(self._index, replacements)

    def _value_tokens(self) -> tuple[str | None, list[int]]:
        """The `-type` of the value and the positions of its tokens."""
        tokens = self._tokens()
        value_type = None
        positions = []
        position = self._name_position(tokens) + 1
        while position < len(tokens):
            if _is_flag(tokens[position], "-typ") and position + 1 < len(tokens):
                value_type = _read(self._document, tokens[position + 1].text)
                position += 2
                continue
            positions.append(position)
            position += 1
        return value_type, positions

    def numbers(self, kinds: Sequence[str | None]) -> Numbers:
        value_type, positions = self._value_tokens()
        if not positions or value_type not in _NUMBER_TYPES:
            return None
        tokens = self._tokens()
        values = []
        found = []
        for i, position in enumerate(positions):
            try:
                values.append(number(tokens[position].text))
            except ValueError:
                return None
            found.append(kinds[i % len(kinds)] if kinds else None)
        return values, found

    def set_number(self, position: int, kind: str | None, value: float) -> None:
        _, positions = self._value_tokens()
        text = number_literal(value, boolean=kind == "bool")
        self._document.replace_tokens(self._index, {positions[position]: text})

    def data(self) -> DataValue | None:
        value_type, positions = self._value_tokens()
        tokens = self._tokens()
        texts = [tokens[p].text for p in positions]
        if value_type == "matrix":
            if texts and texts[0] == '"xform"':
                return xform_from_numbers([number(t) for t in texts[1:]])
            return [number(t) for t in texts]
        if value_type not in _ARRAY_SIZES:
            return None
        # A count, then the values
        values = [number(t) for t in texts[1:]]
        size = _ARRAY_SIZES[value_type]
        return values if size == 1 else group_numbers(values, size)

    def mentions(self, text: str) -> bool:
        return self._document.contains(self._index, self._document.encode(text))


def _decode_edits(
    document: Document, tokens: list[Token]
) -> tuple[ReferenceEdits, list[tuple[int, bool]]]:
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
        return _read(document, text)

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
        # setAttr statements added to the node, after its last one
        self._added: list[int] = []

    def _tokens(self) -> list[Token]:
        if self._tokens_cache is None:
            self._tokens_cache = self._document.tokens(self._index)
        return self._tokens_cache

    def _statements(self) -> list[int]:
        """Indices of the node's own statements, added ones included."""
        return list(range(self._index + 1, self._end)) + self._added

    def _flag(self, flag: str) -> str | None:
        tokens = self._tokens()
        position = _flag_value(tokens, flag)
        return (
            None if position is None else _read(self._document, tokens[position].text)
        )

    def _replace_flag(self, flag: str, value: str) -> None:
        position = _flag_value(self._tokens(), flag)
        if position is None:
            raise ValueError(f"{self.name} has no {flag} to change")
        self._document.replace_tokens(
            self._index, {position: _literal(self._document, value)}
        )
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
                    return _read(self._document, tokens[position].text)
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
            for index in self._statements()
            if self._document.command(index) == "setAttr"
        ]

    def string_attributes(self) -> list[AttributeRecord]:
        document = self._document
        return [
            MaAttribute(document, index)
            for index in self._statements()
            if document.command(index) == "setAttr"
            and (
                document.contains(index, '-type "string"')
                or document.contains(index, '-type "stringArray"')
            )
        ]

    def add_record(
        self,
        name: str,
        kind: str,
        kinds: Sequence[str | None],
        value: Sequence[float] | str | None,
        flags: RecordFlags,
        defaults: RecordFlags,
    ) -> AttributeRecord:
        document = self._document
        words = ["setAttr"]
        if flags.locked:
            words.append("-l on")
        if flags.keyable != defaults.keyable:
            words.append("-k on" if flags.keyable else "-k off")
        if flags.channel_box != defaults.channel_box:
            words.append("-cb on" if flags.channel_box else "-cb off")
        words.append(_literal(document, "." + name))
        if isinstance(value, str):
            words += ['-type "string"', _literal(document, value)]
        elif value is not None:
            if kind in _NUMBER_TYPES:
                words.append(f'-type "{kind}"')
            for position, number in enumerate(value):
                number_kind = kinds[position % len(kinds)] if kinds else None
                words.append(number_literal(number, boolean=number_kind == "bool"))
        text = document.newline + "\t" + " ".join(words) + ";"
        after = self._added[-1] if self._added else self._end - 1
        index = document.insert_after(after, text)
        self._added.append(index)
        return MaAttribute(document, index)

    def added_attributes(self) -> list[AddedAttribute]:
        document = self._document
        found = []
        for index in range(self._index + 1, self._end):
            if document.command(index) != "addAttr":
                continue
            tokens = document.tokens(index)
            names = []
            for flag in ("-ln", "-sn"):
                text = _flag_text(document, tokens, flag)
                if text is not None:
                    names.append(text)
            data_type = _flag_text(document, tokens, "-dt")
            if data_type is not None:
                kind = _ADDED_KINDS.get(data_type, "data")
            else:
                attribute_type = _flag_text(document, tokens, "-at") or "double"
                kind = _ADDED_KINDS.get(attribute_type, "other")
            default = _flag_text(document, tokens, "-dv")
            keyable = _flag_text(document, tokens, "-k")
            storable = _flag_text(document, tokens, "-s")
            # Maya writes both names, a script may write one for both
            found.append(
                AddedAttribute(
                    names[0],
                    names[-1],
                    any(_is_flag(t, "-uaf") for t in tokens),
                    kind,
                    _flag_text(document, tokens, "-p"),
                    any(_is_flag(t, "-m") for t in tokens),
                    keyable is not None and bool(number(keyable)),
                    storable is None or bool(number(storable)),
                    None if default is None else number(default),
                )
            )
        return found


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
        return _read(self._document, self._document.tokens(self._index)[-1].text)

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
            replacements = {len(tokens) - 1: _literal(self._document, value)}
            position = _flag_value(tokens, "-typ")
            if position is not None:
                replacements[position] = _literal(self._document, file_type)
            self._document.replace_tokens(index, replacements)

    @property
    def namespace(self) -> str:
        value = self._value("-ns")
        return "" if value is None else _read(self._document, value)

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
        return "" if value is None else _read(self._document, value)


def _rfn(document: Document, index: int) -> str:
    tokens = document.tokens(index)
    position = _flag_value(tokens, "-rfn")
    return "" if position is None else _read(document, tokens[position].text)


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
        return _read(
            self._document, self._document.tokens(self._index)[self._strings()[0]].text
        )

    @property
    def destination(self) -> str:
        return _read(
            self._document, self._document.tokens(self._index)[self._strings()[1]].text
        )

    @property
    def next_available(self) -> bool:
        return _has_flag(self._document, self._index, "-na")

    def set_plugs(self, source: str, destination: str) -> None:
        first, second = self._strings()
        self._document.replace_tokens(
            self._index,
            {
                first: _literal(self._document, source),
                second: _literal(self._document, destination),
            },
        )

    def mentions(self, text: str) -> bool:
        return self._document.contains(self._index, self._document.encode(text))


class MaInstance(InstanceRecord):
    """`parent -s -nc -r -add "child" "parent"`."""

    def __init__(self, document: Document, index: int) -> None:
        self._document = document
        self._index = index

    def _strings(self) -> list[int]:
        tokens = self._document.tokens(self._index)
        return [i for i, t in enumerate(tokens) if t.text.startswith('"')][-2:]

    @property
    def child(self) -> str:
        tokens = self._document.tokens(self._index)
        return _read(self._document, tokens[self._strings()[0]].text)

    @property
    def parent(self) -> str:
        tokens = self._document.tokens(self._index)
        return _read(self._document, tokens[self._strings()[1]].text)

    def set_names(self, child: str, parent: str) -> None:
        first, second = self._strings()
        self._document.replace_tokens(
            self._index,
            {
                first: _literal(self._document, child),
                second: _literal(self._document, parent),
            },
        )

    def mentions(self, text: str) -> bool:
        return self._document.contains(self._index, self._document.encode(text))


class MaRelationship(RelationshipRecord):
    """`relationship "kind" "node" "plug" ...`."""

    def __init__(self, document: Document, index: int) -> None:
        self._document = document
        self._index = index

    def _values(self) -> list[str]:
        return [
            _read(self._document, t.text)
            for t in self._document.tokens(self._index)[1:]
        ]

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
        replacements = {2: _literal(self._document, node)}
        replacements.update(
            {3 + i: _literal(self._document, p) for i, p in enumerate(plugs)}
        )
        self._document.replace_tokens(self._index, replacements)

    def mentions(self, text: str) -> bool:
        return self._document.contains(self._index, self._document.encode(text))


class MaScene(SceneStorage):
    """A `.ma` file."""

    internal_units = False

    def __init__(self, data: bytes) -> None:
        document = self._document = Document(data)
        self.codepage = document.encoding
        self._nodes: list[NodeRecord] = []
        self._references: list[ReferenceRecord] = []
        self._top_references: list[int] = []
        self._connections: list[int] = []
        self._relationships: list[int] = []
        self._instances: list[int] = []
        self._file_info: list[int] = []
        self._requires: list[int] = []
        self._current_unit: int | None = None
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
                command == "select" and _has_flag(document, index, "-ne")
            ):
                node_start = index
            elif command == "file":
                if _has_flag(document, index, "-rdi"):
                    self._references.append(MaReference(document, index, self))
                elif _has_flag(document, index, "-r"):
                    self._top_references.append(index)
            elif command == "connectAttr":
                self._connections.append(index)
            elif command == "relationship":
                self._relationships.append(index)
            elif command == "parent" and _has_flag(document, index, "-add"):
                self._instances.append(index)
            elif command == "fileInfo":
                self._file_info.append(index)
            elif command == "requires":
                self._requires.append(index)
            elif command == "currentUnit":
                self._current_unit = index
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
                return _read(self._document, tokens[2].text)
        return ""

    @property
    def units(self) -> Units:
        if self._current_unit is None:
            return Units()
        tokens = self._document.tokens(self._current_unit)
        names = {}
        for flag, field in (("-l", "linear"), ("-a", "angle"), ("-t", "time")):
            position = _flag_value(tokens, flag)
            if position is not None:
                text = _read(self._document, tokens[position].text)
                names[field] = short_unit_name(text)
        return Units(**names)

    @property
    def file_info(self) -> dict[str, str]:
        info = {}
        for index in self._file_info:
            tokens = self._document.tokens(index)
            info[_read(self._document, tokens[1].text)] = _read(
                self._document, tokens[2].text
            )
        return info

    @property
    def plugins(self) -> list[Plugin]:
        plugins = []
        for index in self._requires:
            tokens = self._document.tokens(index)
            if tokens[1].text == "maya":
                continue
            node_types = [
                _read(self._document, tokens[i + 1].text)
                for i, t in enumerate(tokens[:-1])
                if _is_flag(t, "-nt")
            ]
            plugins.append(
                Plugin(
                    _read(self._document, tokens[-2].text),
                    _read(self._document, tokens[-1].text),
                    node_types,
                )
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

    def instances(self) -> list[InstanceRecord]:
        return [MaInstance(self._document, i) for i in self._instances]

    def metadata(self) -> str | None:
        if self._metadata is None:
            return None
        tokens = self._document.tokens(self._metadata)
        position = _flag_value(tokens, "-v")
        return (
            None if position is None else _read(self._document, tokens[position].text)
        )

    def set_metadata(self, text: str) -> None:
        assert self._metadata is not None  # only called after metadata() found a table
        position = _flag_value(self._document.tokens(self._metadata), "-v")
        assert position is not None
        self._document.replace_tokens(
            self._metadata, {position: _literal(self._document, text)}
        )

    def to_bytes(self) -> bytes:
        return self._document.to_bytes()
