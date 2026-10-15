from __future__ import annotations

from pathlib import Path

import pytest

import py_maya_scene
from py_maya_scene.metadata import ContentTable
from py_maya_scene.models import Scene

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "models"
VERSIONS = sorted(p.name for p in SAMPLES.iterdir())
FORMATS = ["mb", "ma"]

# (scene, renames, the same renames done and saved by Maya, tokens that pick
# the connections, parents and edits a rename can touch)
CASES = [
    ("names", [("|grpA", "grpRenamed")], "names_renamed", ["grp", "cube"]),
    ("names", [("|grpA|cube", "grpB")], "names_clash", ["grp", "cube"]),
    (
        "instances",
        [("hero", "villain"), ("copies", "doubles")],
        "instances_renamed",
        ["hero", "villain", "copies", "doubles"],
    ),
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
    # Instance records (`parent -add`) count as parents
    parents |= {(i.child, i.parent) for i in scene._storage.instances()}
    return connections, parents, edits


@pytest.mark.parametrize("ext", FORMATS)
@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize(
    ("base", "renames", "expected", "tokens"), CASES, ids=[c[2] for c in CASES]
)
def test_rename_matches_maya(
    ext: str,
    version: str,
    base: str,
    renames: list[tuple[str, str]],
    expected: str,
    tokens: list[str],
) -> None:
    scene = py_maya_scene.parse(SAMPLES / version / f"{base}.{ext}")
    for old, new in renames:
        scene.node(old).name = new
    ours = _touched(scene, tokens)
    maya = _touched(
        py_maya_scene.parse(SAMPLES / version / f"{expected}.{ext}"), tokens
    )
    assert ours[0] == maya[0]
    assert ours[1] == maya[1]
    # Maya re-generates edits when it saves (merging the UV edits, dropping a
    # lock/unlock pair): every edit it keeps must be one py_maya_scene wrote
    assert {e for e in maya[2] if "uvst" not in e[2][1]} <= ours[2]


def _content_rows(path: Path) -> list[tuple[str, ...]]:
    scene = py_maya_scene.parse(path)
    return [
        tuple(row._tokens) for row in ContentTable(scene._storage.metadata() or "").rows
    ]


@pytest.mark.parametrize("ext", FORMATS)
@pytest.mark.parametrize("version", VERSIONS)
def test_content_table_matches_maya(ext: str, version: str, tmp_path: Path) -> None:
    scene = py_maya_scene.parse(SAMPLES / version / f"rename_probe.{ext}")
    for old, new in next(c for c in CASES if c[0] == "rename_probe")[1]:
        scene.node(old).name = new
    scene.node("texRenamed").attr("ftn").value = "Q:/probe/new_tex.exr"
    scene.save(tmp_path / f"out.{ext}")
    expected = _content_rows(SAMPLES / version / f"rename_probe_renamed.{ext}")
    assert _content_rows(tmp_path / f"out.{ext}") == expected


@pytest.mark.parametrize("ext", FORMATS)
def test_shapes_follow_unless_asked(ext: str) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / f"rename_probe.{ext}")
    scene.node("box").rename("crate")
    assert {"crateShape", "crateShapeOrig"} <= {n.name for n in scene.nodes}
    scene.node("ball").rename("sphere", shapes=False)
    assert scene.node("customName").parent_name == "sphere"
    scene.node("crate").rename("box", shapes=False)
    assert scene.node("crateShape").parent_name == "box"


@pytest.mark.parametrize("ext", FORMATS)
def test_in_strings(ext: str) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / f"rename_probe.{ext}")
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


@pytest.mark.parametrize("ext", FORMATS)
def test_rename_survives_save(ext: str, tmp_path: Path) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / f"refedits.{ext}")
    scene.node("localGrp").name = "renamedGrp"
    scene.save(tmp_path / f"out.{ext}")
    saved = py_maya_scene.parse(tmp_path / f"out.{ext}")
    assert saved.node("renamedGrp").path == "|renamedGrp"
    strings = [
        s
        for _, v in saved.reference_edits()
        for _, e in v.all_edits()
        for s in e.strings
    ]
    assert not [s for s in strings if "localGrp" in s]
    assert "|renamedGrp|e:refCube" in strings


@pytest.mark.parametrize("ext", FORMATS)
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
def test_rename_refusals(ext: str, old: str, new: str, error: str) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / f"names.{ext}")
    with pytest.raises(ValueError, match=error):
        scene.node(old).name = new


@pytest.mark.parametrize("ext", FORMATS)
def test_rename_reference_node_is_not_supported(ext: str) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / f"refs.{ext}")
    with pytest.raises(NotImplementedError):
        scene.node("aRN").name = "otherRN"


def test_deep_hierarchy(tmp_path: Path) -> None:
    # Deeper than the recursion limit. The first names repeat under `m`, so
    # their children name them by full path; the deepest node is instanced
    depth, repeated = 1500, 400
    lines = []
    prefix = ""
    for i in range(depth):
        parent = prefix if i <= repeated else f"j{i - 1}"
        lines.append(
            f'createNode transform -n "j{i}"' + (f' -p "{parent}";' if i else ";")
        )
        prefix += f"|j{i}"
    deepest = prefix
    prefix = "|m"
    lines.append('createNode transform -n "m";')
    for i in range(repeated):
        lines.append(f'createNode transform -n "j{i}" -p "{prefix}";')
        prefix += f"|j{i}"
    lines += [
        'createNode transform -n "other";',
        f'parent -s -nc -r -add "{deepest}" "other";',
    ]
    path = tmp_path / "deep.ma"
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    scene = py_maya_scene.parse(path)
    last = f"j{depth - 1}"
    assert scene.node(last).paths == [deepest, f"|other|{last}"]
    assert scene.nodes[-2].path == prefix

    scene.node("|j0").name = "root"
    scene.save(tmp_path / "out.ma")
    saved = py_maya_scene.parse(tmp_path / "out.ma")
    assert saved.node(last).paths == ["|root" + deepest[3:], f"|other|{last}"]


@pytest.mark.parametrize("ext", FORMATS)
def test_rename_to_same_name_writes_nothing(ext: str, tmp_path: Path) -> None:
    path = SAMPLES / "maya2024" / f"names.{ext}"
    scene = py_maya_scene.parse(path)
    scene.node("|grpA").name = "grpA"
    scene.save(tmp_path / f"out.{ext}")
    assert (tmp_path / f"out.{ext}").read_bytes() == path.read_bytes()
