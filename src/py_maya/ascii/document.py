"""Lossless statement list of a Maya ASCII (`.ma`) file.

A `.ma` file is a sequence of MEL statements ending with `;`. The document
keeps the decoded text and each statement's end offset; a statement's text
runs from the previous end to its own, so it carries the whitespace and
comments before it. Edited statements are kept aside and spliced in on write,
so everything else is written back byte for byte.
"""

from __future__ import annotations

import re
from array import array
from typing import Iterator, NamedTuple

# Leading whitespace and comment lines, the command word, then the arguments:
# runs of anything but quotes and `;`, and complete string literals
_STATEMENT = re.compile(
    r'(?:\s|//[^\n]*\n)*([A-Za-z]\w*)[^";]*(?:"(?:[^"\\]|\\.)*"[^";]*)*;', re.S
)
_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[^\s;"]+')
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
        self.text = data.decode(self.encoding, "surrogateescape")
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
        self.tail = self.text[position:]
        if self.tail.strip() and not all(
            line.startswith("//") for line in self.tail.split("\n") if line.strip()
        ):
            raise ValueError(
                f"unexpected text after the last statement: {self.tail[:60]!r}"
            )
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

    def iter_text(self) -> Iterator[str]:
        start = 0
        for index, end in enumerate(self._ends):
            yield self._edited[index] if index in self._edited else self.text[start:end]
            start = end
        yield self.tail

    def to_bytes(self) -> bytes:
        return "".join(self.iter_text()).encode(self.encoding, "surrogateescape")
