from __future__ import annotations

from pathlib import Path

import pytest

import py_maya_scene
from py_maya_scene.resolvers.attribute_specs import attribute_specs

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "models"


def test_schema_specs() -> None:
    specs = attribute_specs("transform")
    translate, translate_x = specs["t"], specs["tx"]
    assert (translate.kind, translate.children) == ("double3", ("tx", "ty", "tz"))
    assert (translate_x.kind, translate_x.parent) == ("linear", "t")
    assert translate_x.keyable and not translate.keyable
    assert specs["rx"].kind == "angle"
    # Inherited from dagNode
    assert specs["ovrgb"].kind == "float3"
    assert specs["v"].kind == "bool" and specs["v"].default is True
    curve = attribute_specs("animCurveTL")
    assert curve["ktv"].multi and curve["ktv"].children == ("kt", "kv")
    assert (curve["kt"].kind, curve["kv"].kind) == ("time", "linear")
    assert attribute_specs("file")["ftn"].kind == "string"


def _added_specs(path: Path, node: str) -> dict[str, tuple[object, ...]]:
    scene = py_maya_scene.parse(path)
    found = scene.node(node)
    added = {a.short_name for a in found._record.added_attributes()}
    return {name: tuple(found._specs()[name]) for name in added}


@pytest.mark.parametrize(
    ("version", "scene", "node"),
    [("maya2025", "units", "added"), ("maya2025", "deps", "addedAttrs")],
)
def test_added_specs_agree_across_formats(version: str, scene: str, node: str) -> None:
    mb = _added_specs(SAMPLES / version / f"{scene}.mb", node)
    ma = _added_specs(SAMPLES / version / f"{scene}.ma", node)
    assert mb == ma


def test_added_specs() -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2025" / "units.mb")
    specs = scene.node("added")._specs()
    # Defaults are in internal units: 3 cm in a scene in metres
    assert (specs["dst"].kind, specs["dst"].default) == ("linear", 3.0)
    assert specs["pos"].children == ("posX", "posY", "posZ")
    assert (specs["posX"].kind, specs["posX"].parent) == ("linear", "pos")
    assert specs["spr"].multi
    assert specs["dmx"].kind == specs["amx"].kind == "matrix"
    assert specs["vcs"].kind == "vectorArray"
