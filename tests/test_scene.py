from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

import py_maya
from py_maya.binary.scene_chunks import decode_metadata
from py_maya.metadata import ContentTable, path_crc
from py_maya.models import Dependency, Scene

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "models"
MB_FILES = sorted(SAMPLES.glob("*/*.mb"))


def _truth(path: Path) -> Any:
    return json.loads(path.with_name(path.name + ".json").read_text(encoding="ascii"))


def _id(path: Path) -> str:
    return path.relative_to(SAMPLES).as_posix()


def _long_name(scene: Scene, name: str) -> str:
    node = scene._find(name)
    return node.path if node is not None else name.lstrip(":")


@pytest.mark.parametrize("path", MB_FILES, ids=_id)
def test_nodes_match_maya(path: Path) -> None:
    scene = py_maya.parse(path)
    truth = {n["name"]: n for n in _truth(path)["nodes"]}
    created = [n for n in scene.nodes if not n.is_default]
    assert created
    for node in created:
        assert node.path in truth, node
        assert (node.type, node.uuid) == (
            truth[node.path]["type"],
            truth[node.path]["uuid"],
        )


@pytest.mark.parametrize("path", MB_FILES, ids=_id)
def test_connections_match_maya(path: Path) -> None:
    scene = py_maya.parse(path)
    truth = {
        (_long_name(scene, s.split(".")[0]), _long_name(scene, d.split(".")[0]))
        for s, d in _truth(path)["connections"]
    }
    for connection in scene.connections:
        pair = (
            _long_name(scene, connection.source.split(".")[0]),
            _long_name(scene, connection.destination.split(".")[0]),
        )
        assert pair in truth, connection


@pytest.mark.parametrize("path", MB_FILES, ids=_id)
def test_references_match_maya(path: Path) -> None:
    scene = py_maya.parse(path)
    # Maya reports duplicate references with a `{n}` copy number the file does not store
    truth = sorted(
        (r["node"], r["namespace"], re.sub(r"\{\d+\}$", "", r["path"]))
        for r in _truth(path)["references"]
    )
    assert sorted((r.node, r.namespace, r.path) for r in scene.references) == truth


@pytest.mark.parametrize("path", MB_FILES, ids=_id)
def test_file_paths_match_maya(path: Path) -> None:
    scene = py_maya.parse(path)
    truth = set()
    for _, value in _truth(path)["paths"]:
        for v in value if isinstance(value, list) else [value]:
            if isinstance(v, str) and v:
                truth.add(v)
    # The JSON is made with references unloaded, so it has no edited values
    found = {d.path for d in scene.dependencies() if d.kind == "attribute"}
    assert found == truth


def test_deps_fixture() -> None:
    scene = py_maya.parse(SAMPLES / "maya2024" / "deps.mb")
    assert scene.version == "2024"
    assert scene.file_info["product"] == "Maya 2024"
    assert {p.name for p in scene.plugins} == {"AbcImport", "mtoa", "gpuCache"}
    assert (
        scene.node("unicodeTex").attr("ftn").value
        == "P:/projets/d\u00e9mo/textures/\u00e9toile.png"
    )
    assert scene.node("transform1").path == "|transform1"
    gpu = scene.node("gpuCache1")
    assert (gpu.type, gpu.path, gpu.is_dag) == (
        "gpuCache",
        "|transform1|gpuCache1",
        True,
    )


def test_refs_fixture() -> None:
    scene = py_maya.parse(SAMPLES / "maya2024" / "refs.mb")
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


@pytest.mark.parametrize("path", MB_FILES, ids=_id)
def test_reference_edits_round_trip(path: Path) -> None:
    for attribute, edits in py_maya.parse(path).reference_edits():
        raw = attribute._chunk.data
        attribute.value = edits
        assert attribute._chunk.data == raw


def test_reference_edit_kinds() -> None:
    scene = py_maya.parse(SAMPLES / "maya2024" / "refedits.mb")
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


def test_extra_attributes_reach_reference_edits() -> None:
    scene = py_maya.parse(SAMPLES / "maya2024" / "refedits.mb")
    assert not [d for d in scene.dependencies() if d.kind == "reference edit"]
    found = scene.dependencies(extra={"transform": ["note"]})
    assert [d for d in found if d.kind == "reference edit"] == [
        Dependency("P:/note.txt", "|localGrp|e:refCube", "note", "reference edit")
    ]


def test_remap_reference_edit(tmp_path: Path) -> None:
    scene = py_maya.parse(SAMPLES / "maya2024" / "refs.mb")
    assert (
        scene.remap_paths(
            lambda p: p.replace("P:/edited", "Q:/a\\b") if "edited" in p else None
        )
        == 1
    )
    _save(scene, tmp_path)
    saved = py_maya.parse(tmp_path / "out.mb")
    assert [d.path for d in saved.dependencies() if d.kind == "reference edit"] == [
        "Q:/a\\b/in/parent.exr"
    ]
    assert b'"Q:/a\\\\b/in/parent.exr"' in (tmp_path / "out.mb").read_bytes()


def _save(scene: Scene, tmp_path: Path) -> bytes:
    target = tmp_path / "out.mb"
    scene.save(target)
    return target.read_bytes()


@pytest.mark.parametrize("path", MB_FILES, ids=_id)
def test_noop_remap_is_byte_identical(path: Path, tmp_path: Path) -> None:
    scene = py_maya.parse(path)
    assert scene.remap_paths(lambda p: None) == 0
    assert _save(scene, tmp_path) == path.read_bytes()


def test_remap_paths(tmp_path: Path) -> None:
    path = SAMPLES / "maya2024" / "deps.mb"
    scene = py_maya.parse(path)
    before = scene.dependencies()
    changed = scene.remap_paths(
        lambda p: "Q:/new/" + p[3:] if p.startswith("P:/") else None
    )
    assert changed == sum(d.path.startswith("P:/") for d in before)
    _save(scene, tmp_path)
    after = py_maya.parse(tmp_path / "out.mb").dependencies()
    expected = [
        d._replace(path="Q:/new/" + d.path[3:]) if d.path.startswith("P:/") else d
        for d in before
    ]
    assert after == expected
    # The externalContentTable follows, CRC included
    data = (tmp_path / "out.mb").read_bytes()
    smtd = data[data.rfind(b"SMTD") + 16 :]
    rows = ContentTable(
        decode_metadata(smtd[: smtd.index(b"endAssociations\n") + 17])
    ).rows
    texture_rows = [r for r in rows if r.attribute]
    assert texture_rows and all(r.path.startswith("Q:/new/") for r in texture_rows)
    assert all(int(r._tokens[3]) == path_crc(r.path) for r in texture_rows)


def test_reference_path_keeps_fref_frdi_and_trailer_in_step(tmp_path: Path) -> None:
    scene = py_maya.parse(SAMPLES / "maya2024" / "refs.mb")
    a_ref = next(r for r in scene.references if r.node == "aRN")
    a_ref.path = "Q:/elsewhere/ref_child.mb"
    _save(scene, tmp_path)
    data = (tmp_path / "out.mb").read_bytes()
    saved = py_maya.parse(tmp_path / "out.mb")
    paths = {r.node: r.path for r in saved.references + saved.nested_references}
    assert paths["aRN"] == "Q:/elsewhere/ref_child.mb"
    assert paths["bRN"] == paths["deps:childRN"] == "$PY_MAYA_SAMPLES/ref_child.mb"
    assert data.count(b"Q:/elsewhere/ref_child.mb\x00") == 2  # FRDI and FREF
    # Trailer INCL lists top-level references in FREF order: deps, a, b, late
    assert b"/deps.mb(Q:/elsewhere/ref_child.mb($PY_MAYA_SAMPLES/ref_child.mb(" in data


def test_trailer_offset_follows_resized_trailer(tmp_path: Path) -> None:
    scene = py_maya.parse(SAMPLES / "maya2024" / "refs.mb")
    scene.references[0].path = "Q:/a/much/longer/path/than/before/deps.mb"
    data = _save(scene, tmp_path)
    trailer = (
        data.rfind(b"HEAD") - 16
    )  # the trailer group's form type follows its header
    bcsz = data.rfind(b"BCSZ")
    assert int.from_bytes(data[bcsz + 16 : bcsz + 20], "big") == bcsz - trailer


def test_save_refuses_to_overwrite(tmp_path: Path) -> None:
    scene = py_maya.parse(SAMPLES / "maya2024" / "ref_child.mb")
    _save(scene, tmp_path)
    with pytest.raises(FileExistsError):
        scene.save(tmp_path / "out.mb")
    scene.save_in_place()
    assert scene.path == tmp_path / "out.mb"
