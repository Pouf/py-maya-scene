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
from collections import OrderedDict
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
# Comment lines are skipped, the first statement carries the header comments
_TOKEN = re.compile(rf'{_STRING}|//[^\n]*|[^\s;"]+', re.S)
_CODESET = re.compile(r"//Codeset: (\S+)")
_TOKENS_CACHED = 4096


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
        # Tokens of the statements read last, since plugs read a statement's
        # name, flags and values separately
        self._tokens_cache: OrderedDict[int, list[Token]] = OrderedDict()
        # Statements added after a parsed one, numbered after the parsed ones
        self._parsed = len(self._ends)
        self._inserted: dict[int, list[int]] = {}
        self._anchors: dict[int, int] = {}
        self.newline = "\r\n" if "\r\n" in self.text[:4096] else "\n"
        """Line end the file uses, for statements added to it."""

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
        """Words and string literals of statement `index`, the command first.

        Maya splits long string literals into MEL concatenations, across
        lines and in reference edits within parentheses (`("part" + "part")`):
        such a run is one token, spanning all its parts, whose text is the
        joined literal.
        """
        cached = self._tokens_cache.get(index)
        if cached is not None:
            self._tokens_cache.move_to_end(index)
            return cached
        tokens = self._tokenize(index)
        self._tokens_cache[index] = tokens
        if len(self._tokens_cache) > _TOKENS_CACHED:
            self._tokens_cache.popitem(last=False)
        return tokens

    def _tokenize(self, index: int) -> list[Token]:
        text = self.statement(index)
        found = []
        for match in _TOKEN.finditer(text):
            if not match.group().startswith("//"):
                found.append((match.group(), match.start(), match.end()))

        def is_string(position: int) -> bool:
            return position < len(found) and found[position][0].startswith('"')

        tokens = []
        position = 0
        while position < len(found):
            token, start, end = found[position]
            opened = token == "(" and is_string(position + 1)
            if opened or token.startswith('"'):
                position += opened
                parts = [found[position][0][1:-1]]
                end = found[position][2]
                while (
                    position + 2 < len(found)
                    and found[position + 1][0] == "+"
                    and is_string(position + 2)
                ):
                    parts.append(found[position + 2][0][1:-1])
                    end = found[position + 2][2]
                    position += 2
                if (
                    opened
                    and position + 1 < len(found)
                    and found[position + 1][0] == ")"
                ):
                    position += 1
                    end = found[position][2]
                token = '"' + "".join(parts) + '"'
            tokens.append(Token(token, start, end))
            position += 1
        return tokens

    def replace_tokens(self, index: int, replacements: dict[int, str]) -> None:
        """Replace tokens of statement `index` (by token position) with new text."""
        text = self.statement(index)
        tokens = self.tokens(index)
        for position in sorted(replacements, reverse=True):
            token = tokens[position]
            text = text[: token.start] + replacements[position] + text[token.end :]
        self._edited[index] = text
        self._tokens_cache.pop(index, None)

    def insert_after(self, index: int, text: str) -> int:
        """Add the statement `text`, with the whitespace before it, right after
        statement `index`, and return the new statement's index. Indices of
        other statements do not change."""
        match = _STATEMENT.fullmatch(text)
        if match is None:
            raise ValueError(f"not one statement: {text!r}")
        command = match.group(1)
        if command not in self._command_names:
            self._command_names.append(command)
        added = len(self._ends)
        self._ends.append(-1)  # in no slice of the parsed text
        self._commands.append(self._command_names.index(command))
        self._edited[added] = text
        anchor = self._anchors.get(index, index)
        followers = self._inserted.setdefault(anchor, [])
        position = followers.index(index) + 1 if index in followers else len(followers)
        followers.insert(position, added)
        self._anchors[added] = anchor
        return added

    def decode(self, raw: str) -> str:
        """A string value as read from the document, in the file's code page."""
        return raw.encode("latin-1").decode(self.encoding, "surrogateescape")

    def encode(self, text: str) -> str:
        """Text to write into the document, through the file's code page."""
        return text.encode(self.encoding, "surrogateescape").decode("latin-1")

    def _pieces(self) -> Iterator[str]:
        # Unedited runs of statements as single slices
        start = 0
        edited = {i for i in self._edited if i < self._parsed}
        for index in sorted(edited | set(self._inserted)):
            yield self.text[start : self._ends[index - 1] if index else 0]
            yield self.statement(index)
            for added in self._inserted.get(index, []):
                yield self._edited[added]
            start = self._ends[index]
        yield self.text[start:]

    def to_bytes(self) -> bytes:
        return "".join(self._pieces()).encode("latin-1")
