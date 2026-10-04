from __future__ import annotations

from pathlib import Path

import pytest

import py_maya
from py_maya.models import Scene

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "models"
VERSIONS = sorted(p.name for p in SAMPLES.iterdir())

# (scene, renames, the same renames done and saved by Maya, tokens that pick
# the connections, parents and edits a rename can touch). Maya's rename also
# renames a transform's shape; py_maya's does not, so the shape is listed.
CASES = [
    ("names", [("|grpA", "grpRenamed")], "names_renamed", ["grp", "cube"]),
    (
        "names",
        [("|grpA|cube", "grpB"), ("|grpA|grpB|cubeShape", "grpBShape")],
        "names_clash",
        ["grp", "cube"],
    ),
    (
        "refedits",
        [
            ("localGrp", "renamedGrp"),
            ("localLoc", "renamedLoc"),
            ("renamedLoc|localLocShape", "renamedLocShape"),
        ],
        "refedits_renamed",
        ["local", "renamed"],
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
