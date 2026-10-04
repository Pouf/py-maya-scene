from __future__ import annotations

import pytest

from py_maya import mel


@pytest.mark.parametrize(
    ("text", "literal"),
    [
        ("P:/plain.exr", '"P:/plain.exr"'),
        ("\\\\server\\share\\a.grm", '"\\\\\\\\server\\\\share\\\\a.grm"'),
        ('say "hi"\n\tnow', '"say \\"hi\\"\\n\\tnow"'),
        ("d\u00e9mo", '"d\u00e9mo"'),
    ],
)
def test_quote_round_trip(text: str, literal: str) -> None:
    assert mel.quote(text) == literal
    assert mel.unquote(literal) == text


def test_string_setattr_arguments() -> None:
    arguments = ' -type "string" "P:/a\\\\b.exr"'
    assert mel.string_setattr_value(arguments) == "P:/a\\b.exr"
    assert mel.string_setattr_arguments("P:/a\\b.exr") == arguments
    assert mel.string_setattr_value(' -type "double3" 5 0 0') is None
