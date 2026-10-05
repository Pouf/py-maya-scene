"""Lossless statement list of a Maya ASCII (`.ma`) file.

A `.ma` file is a sequence of MEL statements ending with `;`. The document
keeps the text and each statement's end offset; a statement's text runs from
the previous end to its own, so it carries the whitespace and comments before
it. Edited statements are kept aside and spliced in on write, so everything
else is written back byte for byte.

The text is held as latin-1, one character per byte: MEL syntax is ASCII, the
string stays compact whatever the file's code page holds, and the bytes come
back exactly. `decode`/`encode` convert string values through the file's real
code page (its `//Codeset:`).
"""

from __future__ import annotations

import re
import sys
from array import array
from typing import Iterator, NamedTuple

# Possessive quantifiers (Python 3.11+) never backtrack; the patterns need no
# backtracking, so they match the same, 2.4 times faster on big groom files
_P = "+" if sys.version_info >= (3, 11) else ""
# A string literal, as an unrolled loop: runs of plain characters match at C
# speed and only escapes repeat (`(?:[^"\\]|\\.)*` repeats per character)
_STRING = rf'"[^"\\]*{_P}(?:\\.[^"\\]*{_P})*{_P}"'
# Leading whitespace and comment lines, the command word, then the arguments:
# runs of anything but quotes and `;`, and complete string literals
_STATEMENT = re.compile(
    rf'(?:\s|//[^\n]*\n)*{_P}([A-Za-z]\w*{_P})[^";]*{_P}(?:{_STRING}[^";]*{_P})*{_P};',
    re.S,
)
_TOKEN = re.compile(rf'{_STRING}|[^\s;"]+', re.S)
_CODESET = re.compile(r"//Codeset: (\S+)")


class Token(NamedTuple):
    """A word or string literal of a statement, with its offsets in the statement."""

    text: str
    start: int
    end: int


def codeset_encoding(head: bytes) -> str:
    """Python encoding of a `.ma` file from its `//Codeset:` comment (code page 1252 if none)."""
    match = _CODESET.search(head.decode("latin-1"))
    codeset = match.group(1) if match else "1252"
    if codeset in ("65001", "UTF-8"):
        return "utf-8"
    return f"cp{codeset}" if codeset.isdigit() else codeset


class Document:
    """The statements of a `.ma` file."""

    def __init__(self, data: bytes) -> None:
        self.encoding = codeset_encoding(data[:4096])
        self.text = data.decode("latin-1")
        # Offsets and command codes in arrays: big files hold ~10M statements
        self._ends = array("q")
        self._commands = array("H")
        self._command_names: list[str] = []
        codes: dict[str, int] = {}
        position = 0
        for match in _STATEMENT.finditer(self.text):
            if match.start() != position:
                raise ValueError(
                    f"unexpected text at offset {position}: {self.text[position : position + 60]!r}"
                )
            command = match.group(1)
            code = codes.get(command)
            if code is None:
                code = codes[command] = len(self._command_names)
                self._command_names.append(command)
            self._ends.append(match.end())
            self._commands.append(code)
            position = match.end()
        # After the last `;`: the `// End of` comment and final newline
        tail = self.text[position:]
        if tail.strip() and not all(
            line.startswith("//") for line in tail.split("\n") if line.strip()
        ):
            raise ValueError(f"unexpected text after the last statement: {tail[:60]!r}")
        self._edited: dict[int, str] = {}

    def __len__(self) -> int:
        return len(self._ends)

    def command(self, index: int) -> str:
        """Command word of statement `index` (`createNode`, `setAttr`, ...)."""
        return self._command_names[self._commands[index]]

    def statement(self, index: int) -> str:
        """Text of statement `index`, with the whitespace and comments before it."""
        if index in self._edited:
            return self._edited[index]
        start = self._ends[index - 1] if index else 0
        return self.text[start : self._ends[index]]

    def contains(self, index: int, text: str) -> bool:
        """Whether statement `index` contains `text`, without copying it out."""
        if index in self._edited:
            return text in self._edited[index]
        start = self._ends[index - 1] if index else 0
        return self.text.find(text, start, self._ends[index]) != -1

    def tokens(self, index: int) -> list[Token]:
        """Words and string literals of statement `index`, the command first."""
        text = self.statement(index)
        return [Token(m.group(), m.start(), m.end()) for m in _TOKEN.finditer(text)]

    def replace_tokens(self, index: int, replacements: dict[int, str]) -> None:
        """Replace tokens of statement `index` (by token position) with new text."""
        text = self.statement(index)
        tokens = self.tokens(index)
        for position in sorted(replacements, reverse=True):
            token = tokens[position]
            text = text[: token.start] + replacements[position] + text[token.end :]
        self._edited[index] = text

    def decode(self, raw: str) -> str:
        """A string value as read from the document, in the file's code page."""
        return raw.encode("latin-1").decode(self.encoding, "surrogateescape")

    def encode(self, text: str) -> str:
        """Text to write into the document, through the file's code page."""
        return text.encode(self.encoding, "surrogateescape").decode("latin-1")

    def _pieces(self) -> Iterator[str]:
        # Unedited runs of statements as single slices
        start = 0
        for index in sorted(self._edited):
            yield self.text[start : self._ends[index - 1] if index else 0]
            yield self._edited[index]
            start = self._ends[index]
        yield self.text[start:]

    def to_bytes(self) -> bytes:
        return "".join(self._pieces()).encode("latin-1")
