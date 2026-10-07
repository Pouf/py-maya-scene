"""MEL string literals, as Maya writes them in `.ma` files and reference edits."""

from __future__ import annotations

import re

_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}
_UNESCAPES = {v[1]: k for k, v in _ESCAPES.items()}
_LITERAL = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"', re.S)


def quote(text: str) -> str:
    """`text` as a MEL string literal."""
    return '"' + "".join(_ESCAPES.get(c, c) for c in text) + '"'


def unquote(literal: str) -> str:
    """The text of a MEL string literal (`"P:/a\\\\b"` > `P:/a\\b`)."""
    match = _LITERAL.fullmatch(literal)
    if match is None:
        raise ValueError(f"not a MEL string literal: {literal!r}")
    return re.sub(
        r"\\(.)",
        lambda m: _UNESCAPES.get(m.group(1), m.group(1)),
        match.group(1),
        flags=re.S,
    )


_STRING_SETATTR = re.compile(r' -type "string" ("[^"\\]*(?:\\.[^"\\]*)*")', re.S)


def string_setattr_value(arguments: str) -> str | None:
    """The value of `setAttr` arguments that set a string (` -type "string" "..."`), else None."""
    match = _STRING_SETATTR.fullmatch(arguments)
    return None if match is None else unquote(match.group(1))


def string_setattr_arguments(value: str) -> str:
    return f' -type "string" {quote(value)}'


# MEL's spellings of booleans, as `setAttr` writes them
_BOOLEANS = {"yes": 1.0, "on": 1.0, "true": 1.0, "no": 0.0, "off": 0.0, "false": 0.0}


def number(text: str) -> float:
    """A MEL number or boolean literal (`1`, `0.5`, `yes`, `off`); `ValueError` otherwise."""
    return _BOOLEANS[text] if text in _BOOLEANS else float(text)
