from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Any

import pytest

import py_maya_scene
from py_maya_scene.binary import Chunk
from py_maya_scene.binary.scene_chunks import ReferenceEdit
from py_maya_scene.data.node_types import DEFAULT_NODES
from py_maya_scene.metadata import ContentTable, path_crc
from py_maya_scene.models import Dependency, Scene
from py_maya_scene.storage.base import file_type_for

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "models"
SCENE_FILES = sorted(SAMPLES.glob("*/*.m[ab]"))
FORMATS = ["mb", "ma"]


def _truth(path: Path) -> Any:
    return json.loads(path.with_name(path.name + ".json").read_text(encoding="ascii"))


def _id(path: Path) -> str:
    return path.relative_to(SAMPLES).as_posix()


def _long_name(scene: Scene, name: str) -> str:
    node = scene._find(name)
    return node.path if node is not None else name.lstrip(":")


def _save(scene: Scene, tmp_path: Path) -> bytes:
    assert scene.path is not None
    target = tmp_path / f"out{scene.path.suffix}"
    scene.save(target)
    return target.read_bytes()


def _content_rows(scene: Scene) -> list[Any]:
    return ContentTable(scene._storage.metadata() or "").rows


@pytest.mark.parametrize("path", SCENE_FILES, ids=_id)
def test_nodes_match_maya(path: Path) -> None:
    scene = py_maya_scene.parse(path)
    truth = {n["name"]: n for n in _truth(path)["nodes"]}
    created = [n for n in scene.nodes if not n.is_default]
    assert created
    for node in created:
        assert node.path in truth, node
        assert node.type == truth[node.path]["type"]
        # Opening a `.ma`, Maya keeps its own UUID for the nodes every scene
        # starts with (lightLinker1, layerManager...); the file still stores one
        if not (path.suffix == ".ma" and node.name in DEFAULT_NODES):
            assert node.uuid == truth[node.path]["uuid"], node


@pytest.mark.parametrize("path", SCENE_FILES, ids=_id)
def test_connections_match_maya(path: Path) -> None:
    scene = py_maya_scene.parse(path)
    truth = {
        (_long_name(scene, s.split(".")[0]), _long_name(scene, d.split(".")[0]))
        for s, d in _truth(path)["connections"]
    }
    assert scene.connections
    for connection in scene.connections:
        pair = (
            _long_name(scene, connection.source.split(".")[0]),
            _long_name(scene, connection.destination.split(".")[0]),
        )
        assert pair in truth, connection


@pytest.mark.parametrize("path", SCENE_FILES, ids=_id)
def test_references_match_maya(path: Path) -> None:
    scene = py_maya_scene.parse(path)
    # Maya reports duplicate references with a `{n}` copy number the file does not store
    truth = sorted(
        (r["node"], r["namespace"], re.sub(r"\{\d+\}$", "", r["path"]))
        for r in _truth(path)["references"]
    )
    assert sorted((r.node, r.namespace, r.path) for r in scene.references) == truth


@pytest.mark.parametrize("path", SCENE_FILES, ids=_id)
def test_file_paths_match_maya(path: Path) -> None:
    scene = py_maya_scene.parse(path)
    truth = set()
    for _, value in _truth(path)["paths"]:
        for v in value if isinstance(value, list) else [value]:
            if isinstance(v, str) and v:
                truth.add(v)
    # The JSON is made with references unloaded, so it has no edited values
    found = {d.path for d in scene.dependencies() if d.kind == "attribute"}
    assert found == truth


@pytest.mark.parametrize("ext", FORMATS)
def test_deps_fixture(ext: str) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / f"deps.{ext}")
    assert scene.version == "2024"
    assert scene.file_info["product"] == "Maya 2024"
    assert {p.name for p in scene.plugins} == {"AbcImport", "mtoa", "gpuCache"}
    texture = scene.node("unicodeTex").attr("ftn")
    assert texture.value == "P:/projets/d\u00e9mo/textures/\u00e9toile.png"
    assert scene.node("transform1").path == "|transform1"
    gpu = scene.node("gpuCache1")
    assert (gpu.type, gpu.path, gpu.is_dag) == (
        "gpuCache",
        "|transform1|gpuCache1",
        True,
    )


def _sequences(scene: Scene) -> dict[str, bool]:
    return {d.path: d.sequence for d in scene.dependencies() if d.kind != "reference"}


def test_sequences_mb(tmp_path: Path) -> None:
    # As Maya stores `setAttr ".ufe" yes` (seen in production): flag 0x20, 1.0
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / "refs.mb")
    attribute, edits = next(
        (a, e) for a, e in scene.reference_edits() if e.owner == "depsRN"
    )
    group = next(g for section in edits.sections for n, g in section if g)
    texture = next(e.strings[0] for e in group if e.strings[1] == "fileTextureName")
    group.append(ReferenceEdit(2, None, [texture, "useFrameExtension", " 1"]))
    attribute.value = edits
    _save(scene, tmp_path)
    found = _sequences(py_maya_scene.parse(tmp_path / "out.mb"))
    assert found.pop("P:/edited/in/parent.exr")
    assert not any(found.values())

    scene = py_maya_scene.parse(SAMPLES / "maya2024" / "deps.mb")
    scene.node("unicodeTex")._record._group.chunks.append(
        Chunk(b"DBLE", b"ufe\x00\x20" + struct.pack(">d", 1.0))
    )
    found = _sequences(scene)
    assert found.pop("P:/projets/d\u00e9mo/textures/\u00e9toile.png")
    assert not any(found.values())


def test_sequences_ma(tmp_path: Path) -> None:
    path = tmp_path / "sequences.ma"
    path.write_bytes(
        b'createNode file -n "plate";\n'
        b'\tsetAttr ".ftn" -type "string" "P:/plates/plate.1001.exr";\n'
        b'\tsetAttr ".ufe" yes;\n'
        b'createNode file -n "still";\n'
        b'\tsetAttr ".ftn" -type "string" "P:/plates/still.1001.exr";\n'
        b'\tsetAttr ".ufe" no;\n'
    )
    assert _sequences(py_maya_scene.parse(path)) == {
        "P:/plates/plate.1001.exr": True,
        "P:/plates/still.1001.exr": False,
    }


@pytest.mark.parametrize("ext", FORMATS)
def test_instance_paths(ext: str) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / f"instances.{ext}")
    shape = scene.node("heroShape")
    assert shape.paths == ["|hero|heroShape", "|copies|heroInstance|heroShape"]
    assert scene.node("|copies|heroInstance|heroShape") is shape


@pytest.mark.parametrize("ext", FORMATS)
def test_refs_fixture(ext: str) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / f"refs.{ext}")
    assert [(r.node, r.depth, r.deferred) for r in scene.references] == [
        ("depsRN", 1, False),
        ("aRN", 1, False),
        ("bRN", 1, False),
        ("lateRN", 1, True),
    ]
    assert [(r.node, r.depth) for r in scene.nested_references] == [("deps:childRN", 2)]
    edited = [d for d in scene.dependencies() if d.kind == "reference edit"]
    assert edited == [
        Dependency(
            "P:/edited/in/parent.exr",
            "deps:diffuseTex",
            "fileTextureName",
            "reference edit",
        )
    ]


@pytest.mark.parametrize("path", SCENE_FILES, ids=_id)
def test_reference_edits_round_trip(path: Path, tmp_path: Path) -> None:
    scene = py_maya_scene.parse(path)
    for attribute, edits in scene.reference_edits():
        attribute.value = edits
    assert _save(scene, tmp_path) == path.read_bytes()


@pytest.mark.parametrize("ext", FORMATS)
def test_reference_edit_kinds(ext: str) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / f"refedits.{ext}")
    ((_, edits),) = scene.reference_edits()
    (first,), ((node, node_edits),) = edits.sections
    assert first == ("eRN", []) and node == "eRN"
    kinds = [e.kind for e in node_edits if "uvSet" not in e.strings[1]]
    # parent, addAttr, setAttr x2, disconnectAttr x2, deleteAttr,
    # connectAttr x4, lock, unlock
    assert kinds == [0, 1, 2, 2, 3, 3, 4, 5, 5, 5, 5, 8, 9]
    assert node_edits[0].strings == ["|e:refCube", "|localGrp", "-s -r "]
    assert [e.side for e in node_edits if e.kind == 5] == [3, 4, 0, 3]
    both_referenced = next(e for e in node_edits if e.side == 0)
    assert len(both_referenced.strings) == 6


@pytest.mark.parametrize("ext", FORMATS)
def test_reference_edits_agree_across_formats(ext: str) -> None:
    def edits(name: str) -> list[Any]:
        scene = py_maya_scene.parse(SAMPLES / "maya2024" / name)
        return [value for _, value in scene.reference_edits()]

    for name in ("refs", "refedits"):
        assert edits(f"{name}.ma") == edits(f"{name}.mb")


@pytest.mark.parametrize("ext", FORMATS)
def test_extra_attributes_reach_reference_edits(ext: str) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / f"refedits.{ext}")
    assert not [d for d in scene.dependencies() if d.kind == "reference edit"]
    found = scene.dependencies(extra={"transform": ["note"]})
    assert [d for d in found if d.kind == "reference edit"] == [
        Dependency("P:/note.txt", "|localGrp|e:refCube", "note", "reference edit")
    ]


@pytest.mark.parametrize("ext", FORMATS)
def test_remap_reference_edit(ext: str, tmp_path: Path) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / f"refs.{ext}")
    changed = scene.remap_paths(
        lambda p: p.replace("P:/edited", "Q:/a\\b") if "edited" in p else None
    )
    assert changed == 1
    data = _save(scene, tmp_path)
    saved = py_maya_scene.parse(tmp_path / f"out.{ext}")
    assert [d.path for d in saved.dependencies() if d.kind == "reference edit"] == [
        "Q:/a\\b/in/parent.exr"
    ]
    # MEL escaping: once in the edit arguments, once more as a `.ma` string
    escaped = b"Q:/a\\\\b" if ext == "mb" else b"Q:/a\\\\\\\\b"
    assert escaped + b"/in/parent.exr" in data


@pytest.mark.parametrize("path", SCENE_FILES, ids=_id)
def test_noop_remap_is_byte_identical(path: Path, tmp_path: Path) -> None:
    scene = py_maya_scene.parse(path)
    assert scene.remap_paths(lambda p: None) == 0
    assert _save(scene, tmp_path) == path.read_bytes()


@pytest.mark.parametrize("ext", FORMATS)
def test_remap_paths(ext: str, tmp_path: Path) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / f"deps.{ext}")
    before = scene.dependencies()
    changed = scene.remap_paths(
        lambda p: "Q:/new/" + p[3:] if p.startswith("P:/") else None
    )
    assert changed == sum(d.path.startswith("P:/") for d in before)
    _save(scene, tmp_path)
    saved = py_maya_scene.parse(tmp_path / f"out.{ext}")
    expected = [
        d._replace(path="Q:/new/" + d.path[3:]) if d.path.startswith("P:/") else d
        for d in before
    ]
    assert saved.dependencies() == expected
    # The externalContentTable follows, CRC included
    texture_rows = [r for r in _content_rows(saved) if r.attribute]
    assert texture_rows and all(r.path.startswith("Q:/new/") for r in texture_rows)
    assert all(int(r._tokens[3]) == path_crc(r.path) for r in texture_rows)


@pytest.mark.parametrize("ext", FORMATS)
def test_reference_path_keeps_both_statements_in_step(ext: str, tmp_path: Path) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / f"refs.{ext}")
    a_ref = next(r for r in scene.references if r.node == "aRN")
    a_ref.path = "Q:/elsewhere/ref_child.mb"
    data = _save(scene, tmp_path)
    saved = py_maya_scene.parse(tmp_path / f"out.{ext}")
    paths = {r.node: r.path for r in saved.references + saved.nested_references}
    assert paths["aRN"] == "Q:/elsewhere/ref_child.mb"
    assert paths["bRN"] == paths["deps:childRN"] == "$PY_MAYA_SAMPLES/ref_child.mb"
    # `.mb`: FRDI, FREF, trailer INCL; `.ma`: `file -rdi`, `file -r`; plus the table row
    assert data.count(b"Q:/elsewhere/ref_child.mb") == (4 if ext == "mb" else 3)


@pytest.mark.parametrize("ext", FORMATS)
def test_reference_file_type_follows_extension(ext: str, tmp_path: Path) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / f"refs.{ext}")
    a_ref = next(r for r in scene.references if r.node == "aRN")
    assert a_ref.file_type == "mayaBinary"
    a_ref.path = "$PY_MAYA_SAMPLES/ref_child.ma"
    assert a_ref.file_type == "mayaAscii"
    data = _save(scene, tmp_path)
    saved = py_maya_scene.parse(tmp_path / f"out.{ext}")
    types = {r.node: r.file_type for r in saved.references}
    assert types == {
        "depsRN": "mayaBinary",
        "aRN": "mayaAscii",
        "bRN": "mayaBinary",
        "lateRN": "mayaAscii",
    }
    # Both statements of the reference (`FRDI`/`FREF`, `file -rdi`/`file -r`)
    assert data.count(b"mayaAscii") == 4


def test_file_type_for() -> None:
    assert file_type_for("P:/a.MA", "mayaBinary") == "mayaAscii"
    assert file_type_for("P:/a.mb", "mayaAscii") == "mayaBinary"
    assert file_type_for("P:/a.abc", "mayaBinary") == "mayaBinary"
    assert file_type_for("P:/a.ma", "Alembic") == "Alembic"


def test_mb_trailer_follows_reference_paths(tmp_path: Path) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / "refs.mb")
    next(
        r for r in scene.references if r.node == "aRN"
    ).path = "Q:/elsewhere/ref_child.mb"
    data = _save(scene, tmp_path)
    # Trailer INCL lists top-level references in FREF order: deps, a, b, late
    assert b"/deps.mb(Q:/elsewhere/ref_child.mb($PY_MAYA_SAMPLES/ref_child.mb(" in data


def test_mb_trailer_offset_follows_resized_trailer(tmp_path: Path) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / "refs.mb")
    scene.references[0].path = "Q:/a/much/longer/path/than/before/deps.mb"
    data = _save(scene, tmp_path)
    trailer = (
        data.rfind(b"HEAD") - 16
    )  # the trailer group's form type follows its header
    bcsz = data.rfind(b"BCSZ")
    assert int.from_bytes(data[bcsz + 16 : bcsz + 20], "big") == bcsz - trailer


# Maya writes short flags; some production files, written by scripts, use
# long ones, with Windows line ends
LONG_FLAGS = (
    b"//Maya ASCII 2022 scene\r\n"
    b"//Codeset: 1252\r\n"
    b'file -referenceDepthInfo 1 -namespace "ref" -deferReference 1 -referenceNode "refRN"'
    b' -type "mayaAscii" "C:/a/ref.ma";\r\n'
    b'file -reference -namespace "ref" -deferReference 1 -referenceNode "refRN"'
    b' -type "mayaAscii"\r\n\t\t "C:/a/ref.ma";\r\n'
    b'requires maya "2022";\r\n'
    b'requires -nodeType "pgYetiMaya" "pgYetiMaya" "4.0";\r\n'
    b'createNode transform -name "ALL";\r\n'
    b'\trename -uuid "11111111-2222-3333-4444-555555555555";\r\n'
    b'createNode transform -name "geo" -parent "ALL";\r\n'
    b'createNode mesh -name "geoShape" -parent "geo";\r\n'
    b'createNode transform -name "other";\r\n'
    b"select -noExpand :time1;\r\n"
    b'parent -shape -noConnections -relative -addObject "geoShape" "other";\r\n'
    b'connectAttr "ALL.tx" "other.tx" -nextAvailable;\r\n'
    b"// End of long_flags.ma\r\n"
)


def test_long_flags(tmp_path: Path) -> None:
    path = tmp_path / "long_flags.ma"
    path.write_bytes(LONG_FLAGS)
    scene = py_maya_scene.parse(path)
    assert scene._storage.to_bytes() == LONG_FLAGS
    assert [n.path for n in scene.nodes] == [
        "|ALL",
        "|ALL|geo",
        "|ALL|geo|geoShape",
        "|other",
        "time1",
    ]
    assert scene.node("ALL").uuid == "11111111-2222-3333-4444-555555555555"
    assert scene.node("time1").is_default
    assert scene.node("geoShape").paths == ["|ALL|geo|geoShape", "|other|geoShape"]
    assert scene.plugins[0].node_types == ["pgYetiMaya"]
    assert scene.connections[0].next_available
    (reference,) = scene.references
    assert (reference.namespace, reference.node, reference.deferred) == (
        "ref",
        "refRN",
        True,
    )
    assert reference.file_type == "mayaAscii"

    scene.node("ALL").name = "ROOT"
    reference.path = "C:/b/ref.mb"
    data = _save(scene, tmp_path)
    assert b'createNode transform -name "geo" -parent "ROOT";\r\n' in data
    assert data.count(b'-type "mayaBinary"') == 2
    assert data.count(b'"C:/b/ref.mb"') == 2


@pytest.mark.parametrize("ext", FORMATS)
def test_save_refuses_to_overwrite(ext: str, tmp_path: Path) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / f"ref_child.{ext}")
    _save(scene, tmp_path)
    with pytest.raises(FileExistsError):
        scene.save(tmp_path / f"out.{ext}")
    scene.save_in_place()
    assert scene.path == tmp_path / f"out.{ext}"


@pytest.mark.parametrize("ext", FORMATS)
def test_save_keeps_a_file_named_like_a_temporary(ext: str, tmp_path: Path) -> None:
    keep = tmp_path / f"out.{ext}.tmp"
    keep.write_bytes(b"user data")
    py_maya_scene.parse(SAMPLES / "maya2024" / f"ref_child.{ext}").save(
        tmp_path / f"out.{ext}"
    )
    assert keep.read_bytes() == b"user data"
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"out.{ext}", keep.name]


@pytest.mark.parametrize("ext", FORMATS)
def test_failed_write_leaves_the_file(
    ext: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The temporary file is created, then writing it fails part-way (a full
    # disk, a dropped network share): it is removed, the scene file untouched.
    source = (SAMPLES / "maya2024" / f"ref_child.{ext}").read_bytes()
    target = tmp_path / f"out.{ext}"
    target.write_bytes(source)
    scene = py_maya_scene.parse(target)
    real_open = Path.open

    def open_then_fail(path: Path, *args: Any, **kwargs: Any) -> None:
        with real_open(path, *args, **kwargs) as f:
            f.write(b"partial")
        raise OSError("simulated write failure")

    monkeypatch.setattr(Path, "open", open_then_fail)
    with pytest.raises(OSError):
        scene.save_in_place()
    with pytest.raises(OSError):
        scene.save(tmp_path / f"new.{ext}")
    monkeypatch.undo()
    assert target.read_bytes() == source
    assert [p.name for p in tmp_path.iterdir()] == [target.name]
