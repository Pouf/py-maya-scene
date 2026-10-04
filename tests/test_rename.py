from __future__ import annotations

from pathlib import Path

import pytest

import py_maya
from py_maya.binary import Chunk, read_mb, walk
from py_maya.binary.scene_chunks import decode_metadata
from py_maya.metadata import ContentTable
from py_maya.models import Scene

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "models"
VERSIONS = sorted(p.name for p in SAMPLES.iterdir())

# (scene, renames, the same renames done and saved by Maya, tokens that pick
# the connections, parents and edits a rename can touch)
CASES = [
    ("names", [("|grpA", "grpRenamed")], "names_renamed", ["grp", "cube"]),
    ("names", [("|grpA|cube", "grpB")], "names_clash", ["grp", "cube"]),
    (
        "refedits",
        [("localGrp", "renamedGrp"), ("localLoc", "renamedLoc")],
        "refedits_renamed",
        ["local", "renamed"],
    ),
    (
        "rename_probe",
        [
            ("|root|a", "x"),
            ("box", "crate"),
            ("ball", "sphere"),
            ("tex1", "texRenamed"),
            ("cacheParent", "cacheGrp"),
        ],
        "rename_probe_renamed",
        ["root", "box", "crate", "ball", "sphere", "custom", "tex", "cache", "expr"],
    ),
]


def _touched(scene: Scene, tokens: list[str]) -> tuple[set, set, set]:
    def hit(text: str) -> bool:
        return any(t in text for t in tokens)

    connections = {c for c in scene.connections if hit(c.source + c.destination)}
    parents = {
        (n.name, n.parent_name)
        for n in scene.nodes
        if hit(n.name + (n.parent_name or ""))
    }
    edits = {
        (e.kind, e.side, tuple(e.strings))
        for _, value in scene.reference_edits()
        for _, e in value.all_edits()
        if any(hit(s) for s in e.strings)
    }
    return connections, parents, edits


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize(
    ("base", "renames", "expected", "tokens"), CASES, ids=[c[2] for c in CASES]
)
def test_rename_matches_maya(
    version: str,
    base: str,
    renames: list[tuple[str, str]],
    expected: str,
    tokens: list[str],
) -> None:
    scene = py_maya.parse(SAMPLES / version / f"{base}.mb")
    for old, new in renames:
        scene.node(old).name = new
    ours = _touched(scene, tokens)
    maya = _touched(py_maya.parse(SAMPLES / version / f"{expected}.mb"), tokens)
    assert ours[0] == maya[0]
    assert ours[1] == maya[1]
    # Maya re-generates edits when it saves (merging the UV edits, dropping a
    # lock/unlock pair): every edit it keeps must be one py_maya wrote
    assert {e for e in maya[2] if "uvst" not in e[2][1]} <= ours[2]


def _content_rows(path: Path) -> list[tuple[str, ...]]:
    root = read_mb(path.read_bytes())
    for _, chunk in walk(root):
        if isinstance(chunk, Chunk) and chunk.tag == b"SMTD":
            table = ContentTable(decode_metadata(chunk.data))
            return [tuple(row._tokens) for row in table.rows]
    return []


@pytest.mark.parametrize("version", VERSIONS)
def test_content_table_matches_maya(version: str, tmp_path: Path) -> None:
    scene = py_maya.parse(SAMPLES / version / "rename_probe.mb")
    for old, new in CASES[3][1]:
        scene.node(old).name = new
    scene.node("texRenamed").attr("ftn").value = "Q:/probe/new_tex.exr"
    scene.save(tmp_path / "out.mb")
    expected = _content_rows(SAMPLES / version / "rename_probe_renamed.mb")
    assert _content_rows(tmp_path / "out.mb") == expected


def test_shapes_follow_unless_asked(tmp_path: Path) -> None:
    scene = py_maya.parse(SAMPLES / "maya2024" / "rename_probe.mb")
    scene.node("box").rename("crate")
    assert {"crateShape", "crateShapeOrig"} <= {n.name for n in scene.nodes}
    scene.node("ball").rename("sphere", shapes=False)
    assert scene.node("customName").parent_name == "sphere"
    scene.node("crate").rename("box", shapes=False)
    assert scene.node("crateShape").parent_name == "box"


def test_in_strings(tmp_path: Path) -> None:
    scene = py_maya.parse(SAMPLES / "maya2024" / "rename_probe.mb")
    expression = scene.node("expr1").attr("ixp")
    expression.value = (
        'float $v = `getAttr box.ty`;\n.O[0] = $v;\nsetAttr "box.rz" 3;\nprint "boxes";'
    )
    scene.node("box").rename("crate")
    assert scene.node("ball").attr("targetName").value == "box"
    assert "getAttr box.ty" in expression.value
    scene.node("crate").rename("box2", in_strings=False)
    scene.node("box2").rename("crate", in_strings=True)
    assert scene.node("ball").attr("targetName").value == "box"  # names box, not box2
    scene.node("crate").rename("box", in_strings=True)
    scene.node("box").rename("lid", in_strings=True)
    assert scene.node("ball").attr("targetName").value == "lid"
    assert expression.value == (
        'float $v = `getAttr lid.ty`;\n.O[0] = $v;\nsetAttr "lid.rz" 3;\nprint "boxes";'
    )


def test_rename_survives_save(tmp_path: Path) -> None:
    scene = py_maya.parse(SAMPLES / "maya2024" / "refedits.mb")
    scene.node("localGrp").name = "renamedGrp"
    scene.save(tmp_path / "out.mb")
    saved = py_maya.parse(tmp_path / "out.mb")
    assert saved.node("renamedGrp").path == "|renamedGrp"
    strings = [
        s
        for _, v in saved.reference_edits()
        for _, e in v.all_edits()
        for s in e.strings
    ]
    assert not [s for s in strings if "localGrp" in s]
    assert "|renamedGrp|e:refCube" in strings


@pytest.mark.parametrize(
    ("old", "new", "error"),
    [
        ("|grpA", "grpB", "sibling"),
        ("cubeSet", "grpA", "already exists"),
        ("|grpA", "bad name", "invalid node name"),
        ("|grpA", "a|b", "invalid node name"),
        ("time1", "clock", "default node"),
    ],
)
def test_rename_refusals(old: str, new: str, error: str) -> None:
    scene = py_maya.parse(SAMPLES / "maya2024" / "names.mb")
    with pytest.raises(ValueError, match=error):
        scene.node(old).name = new


def test_rename_reference_node_is_not_supported() -> None:
    scene = py_maya.parse(SAMPLES / "maya2024" / "refs.mb")
    with pytest.raises(NotImplementedError):
        scene.node("aRN").name = "otherRN"


def test_rename_to_same_name_writes_nothing(tmp_path: Path) -> None:
    path = SAMPLES / "maya2024" / "names.mb"
    scene = py_maya.parse(path)
    scene.node("|grpA").name = "grpA"
    scene.save(tmp_path / "out.mb")
    assert (tmp_path / "out.mb").read_bytes() == path.read_bytes()
