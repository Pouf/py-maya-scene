from __future__ import annotations

from py_maya_scene.ascii import Document, codeset_encoding

SPLIT = (
    b'setAttr ".b" -type "string" "ab"\n\t\t+ "cd";\n'
    b'setAttr ".ed" -type "dataReferenceEdits" "RN" "RN" 1\n'
    b'\t\t2 "|n" "t" (" -s 2"\n\t\t+ " 0 1");\n'
    b"// End of x.ma\n"
)


def test_round_trip_keeps_every_byte() -> None:
    assert Document(SPLIT).to_bytes() == SPLIT


def test_split_strings_are_one_token() -> None:
    document = Document(SPLIT)
    assert document.tokens(0)[-1].text == '"abcd"'
    assert document.tokens(1)[-1].text == '" -s 2 0 1"'
    assert [t.text for t in document.tokens(1)][4:7] == ['"RN"', '"RN"', "1"]


def test_replacing_a_split_string_replaces_all_its_parts() -> None:
    document = Document(SPLIT)
    document.replace_tokens(0, {4: '"x"'})
    document.replace_tokens(1, {10: '"y"'})
    assert document.to_bytes() == (
        b'setAttr ".b" -type "string" "x";\n'
        b'setAttr ".ed" -type "dataReferenceEdits" "RN" "RN" 1\n'
        b'\t\t2 "|n" "t" "y";\n'
        b"// End of x.ma\n"
    )


def test_code_page() -> None:
    assert codeset_encoding(b"//Maya ASCII 2024 scene\n//Codeset: 1252\n") == "cp1252"
    assert codeset_encoding(b"//Codeset: 65001\n") == "utf-8"
    assert codeset_encoding(b'requires maya "2024";\n') == "cp1252"
    document = Document('setAttr ".ftn" -type "string" "démo";\n'.encode("cp1252"))
    raw = document.tokens(0)[-1].text
    assert document.decode(raw) == '"démo"'
    assert document.encode('"étoile"').encode("latin-1") == '"étoile"'.encode("cp1252")
