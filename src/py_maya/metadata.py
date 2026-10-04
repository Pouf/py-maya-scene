"""Scene metadata in Maya's raw text format, and its externalContentTable.

The same text is stored in the `.mb` trailer (`SMTD`) and in the `.ma`
`applyMetadata -fmt "raw" -v "..."` statement:

    channel
    name externalContentTable
    stream
    ...
    structure externalContentTable
    0
    "node" "attribute" "path" <crc> "resolved path" "role"
    1
    ...
    endStream
"""

from __future__ import annotations

import re
import zlib

from .mel import quote, unquote

# Maya computes `upathcrc` on the path in the system code page, also in `.mb`
# files whose text is UTF-8 (measured on Maya 2018 to 2024, code page 1252)
CODEPAGE = "cp1252"
_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\S+')
_TABLE = "externalContentTable"


def path_crc(path: str, codepage: str = CODEPAGE) -> int:
    try:
        raw = path.encode(codepage)
    except UnicodeEncodeError as error:
        raise ValueError(
            f"{path!r} has characters code page {codepage} cannot hold"
        ) from error
    return zlib.crc32(raw) & 0xFFFFFFFF


class ContentRow:
    """One externalContentTable row: a file path stored on a node attribute,
    or a reference's file (empty `attribute`, role `FileRef`)."""

    def __init__(self, table: ContentTable, line: int, tokens: list[str]) -> None:
        self._table = table
        self._line = line
        self._tokens = tokens

    @property
    def node(self) -> str:
        return unquote(self._tokens[0])

    @property
    def attribute(self) -> str:
        """Long name of the attribute, empty for a reference."""
        return unquote(self._tokens[1])

    @property
    def path(self) -> str:
        return unquote(self._tokens[2])

    @property
    def resolved_path(self) -> str:
        return unquote(self._tokens[4])

    @property
    def role(self) -> str:
        return unquote(self._tokens[5])

    def update(self, node: str, path: str, resolved_path: str, codepage: str) -> bool:
        """Store new values; return whether the row changed."""
        if node == self.node and path == self.path:
            return False
        self._tokens[0] = quote(node)
        if path != self.path:
            self._tokens[2] = quote(path)
            self._tokens[3] = str(path_crc(path, codepage))
            self._tokens[4] = quote(resolved_path)
        self._table._lines[self._line] = " ".join(self._tokens)
        return True


class ContentTable:
    """The rows of the externalContentTable stream; every other line is kept as is."""

    def __init__(self, text: str) -> None:
        self._lines = text.split("\n")
        self.rows: list[ContentRow] = []
        in_table = False
        for index, line in enumerate(self._lines):
            if line == f"structure {_TABLE}":
                in_table = True
            elif line == "endStream":
                in_table = False
            elif in_table and line.startswith('"'):
                tokens = _TOKEN.findall(line)
                if len(tokens) == 6:
                    self.rows.append(ContentRow(self, index, tokens))

    @property
    def text(self) -> str:
        return "\n".join(self._lines)
