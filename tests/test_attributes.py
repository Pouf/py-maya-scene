from __future__ import annotations

import json
from pathlib import Path

import pytest

import py_maya_scene
from py_maya_scene.binary.scene_chunks import Xform
from py_maya_scene.models import Scene

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "models"
UNITS = [
    SAMPLES / version / f"units.{ext}"
    for version in ("maya2022", "maya2025")
    for ext in ("mb", "ma")
]


def _id(path: Path) -> str:
    return path.relative_to(SAMPLES).as_posix()


def _value(scene: Scene, path: str) -> object:
    node, _, name = path.partition(".")
    return scene.node(node).attr(name).value


def _flat(value: object) -> list[object]:
    """`getAttr` gives `[(x, y, z)]` for a compound, attributes give `(x, y, z)`."""
    if isinstance(value, (list, tuple)):
        flat: list[object] = []
        for item in value:
            flat.extend(_flat(item))
        return flat
    return [value]


def _same(mine: object, maya: object) -> bool:
    ours, theirs = _flat(mine), _flat(maya)
    if len(ours) != len(theirs):
        return False
    for a, b in zip(ours, theirs):
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if a != pytest.approx(b, rel=1e-6, abs=1e-9):
                return False
        elif a != b:
            return False
    return True


@pytest.mark.parametrize("path", UNITS, ids=_id)
def test_matches_maya(path: Path) -> None:
    """Every value and flag an attribute gives is what Maya's `getAttr` gave
    for the same file (`dump_scene.py`), but for driven attributes and what
    attributes refuse to answer."""
    scene = py_maya_scene.parse(path)
    truth = json.loads(path.with_name(path.name + ".json").read_text(encoding="ascii"))
    nodes = {n.path if not n.is_default else n.name: n for n in scene.nodes}
    compared = 0
    wrong = []
    for node_path, attributes in truth["values"].items():
        node = nodes[node_path]
        for name, maya_value, maya_flags in attributes:
            attribute = node.attr(name)
            if attribute._is_connected():
                continue
            flags = [attribute.locked, attribute.keyable, attribute.channel_box]
            if flags != maya_flags:
                wrong.append((node_path, attribute, flags, maya_flags))
            try:
                value = attribute.value
            except NotImplementedError:
                continue
            if isinstance(value, Xform):  # `getAttr` composes the matrix
                continue
            compared += 1
            if not _same(value, maya_value):
                wrong.append((node_path, attribute, value, maya_value))
    assert compared > 4500
    assert wrong == []


@pytest.mark.parametrize("path", UNITS, ids=_id)
def test_values_in_scene_units(path: Path) -> None:
    # The scene is in metres, degrees and ntsc, .mb stores centimetres,
    # radians and ticks
    scene = py_maya_scene.parse(path)
    assert _value(scene, "values.t") == pytest.approx((1, 2, 3))
    assert _value(scene, "values.translateY") == pytest.approx(2)
    assert _value(scene, "values.r") == pytest.approx((10, 20, 30))
    assert _value(scene, "values.ro") == 2
    assert _value(scene, "values.it") is False
    assert _value(scene, "added.dst") == pytest.approx(0.5)
    assert _value(scene, "added.ang") == pytest.approx(45)
    assert _value(scene, "added.pos") == pytest.approx((1, 2, 3))
    assert _value(scene, "added.cls") == (4, -5)
    assert _value(scene, "time1.o") == pytest.approx(7.5)
    assert _value(scene, "defaultResolution.w") == 1280
    assert _value(scene, "sequence.fe") == 12
    # Keys in frames and metres
    keys = _value(scene, "anim_translateX.ktv")
    assert keys == [pytest.approx((1, 1)), pytest.approx((30, 2))]
    assert _value(scene, "anim_translateX.ktv[1].kt") == pytest.approx(30)


@pytest.mark.parametrize("path", UNITS, ids=_id)
def test_compound_children_and_ranges(path: Path) -> None:
    scene = py_maya_scene.parse(path)
    # .mb stores the colour's children apart, .ma the parent
    assert _value(scene, "color.ovrgb") == (1.0, 0.5, 0.0)
    assert _value(scene, "color.ovcg") == 0.5
    # .ma splits this range every 250 elements
    shape = scene.node("rangeShape")
    assert shape.attr("pt[299]").value == pytest.approx((0, 2.99, 0))
    # pt[0] keeps its default, so neither format stores it
    assert len(shape.attr("pt").value) == 299
    # A sparse multi lists its stored elements
    assert _value(scene, "added.spr") == [1.0, 2.0]
    assert _value(scene, "added.spr[3]") == 0.0


@pytest.mark.parametrize("path", UNITS, ids=_id)
def test_listing(path: Path) -> None:
    scene = py_maya_scene.parse(path)

    def names(node: str) -> list[str]:
        return [a.name for a in scene.node(node).attributes]

    # .mb stores the colour's children apart, .ma the parent
    assert names("color") == ["ove", "ovrgbf", "ovrgb"]
    assert names("locks") == ["v", "t", "r", "ro"]
    # Elements and ranges come as their multi
    assert names("added")[-1] == "spr"
    assert names("rangeShape").count("pt") == 1
    assert "uvst[0].uvsp" in names("rangeShape")
    assert "ktv" in names("anim_translateX")


def test_strings_without_spec(tmp_path: Path) -> None:
    path = tmp_path / "plugin.ma"
    path.write_bytes(
        b'createNode pluginCache -n "cache";\n'
        b'\tsetAttr ".cfp" -type "string" "P:/cache/a.abc";\n'
    )
    scene = py_maya_scene.parse(path)
    attribute = scene.node("cache").attr("cfp")
    assert attribute.spec is None
    assert attribute.value == "P:/cache/a.abc"
    attribute.value = "P:/cache/b.abc"
    with pytest.raises(TypeError):
        attribute.value = 2
    with pytest.raises(NotImplementedError):
        scene.node("cache").attr("other").value = "P:/cache/c.abc"
    scene.save(tmp_path / "saved.ma")
    saved = py_maya_scene.parse(tmp_path / "saved.ma")
    assert saved.node("cache").attr("cfp").value == "P:/cache/b.abc"


@pytest.mark.parametrize("path", UNITS, ids=_id)
def test_data_values(path: Path) -> None:
    scene = py_maya_scene.parse(path)
    # Matrices are never converted to the scene's units
    matrix = [1.0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1, 2, 3, 1]
    assert _value(scene, "added.dmx") == matrix
    assert _value(scene, "added.amx") == matrix
    assert _value(scene, "added.dbs") == [1.5, 2.5]
    assert _value(scene, "added.ins") == [1, -2, 3]
    assert _value(scene, "added.pts") == [(1, 2, 3, 1), (4, 5, 6, 1)]
    assert _value(scene, "added.vcs") == [(1, 2, 3), (4, 5, 6)]
    xform = _value(scene, "posedPose.xm[0]")
    assert isinstance(xform, Xform)
    assert xform.translate == pytest.approx((100, 200, 300))
    assert xform.rotate_pivot == pytest.approx((100, 100, 100))
    # An unset matrix reads as its default
    assert _value(scene, "values.opm") == [
        1.0,
        0,
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        0,
        1,
    ]


@pytest.mark.parametrize("path", UNITS, ids=_id)
def test_flags(path: Path) -> None:
    scene = py_maya_scene.parse(path)
    locks = scene.node("locks")
    assert locks.attr("t").locked
    # Locked through its parent
    assert locks.attr("tx").locked
    assert locks.attr("rx").locked and not locks.attr("ry").locked
    assert not locks.attr("rz").keyable and locks.attr("rz").channel_box
    assert locks.attr("ro").keyable
    assert not locks.attr("v").keyable
    assert scene.node("values").attr("tx").keyable


@pytest.mark.parametrize("path", UNITS, ids=_id)
def test_values_not_known(path: Path) -> None:
    scene = py_maya_scene.parse(path)
    with pytest.raises(NotImplementedError):
        _value(scene, "values.wm")  # computed by Maya
    with pytest.raises(NotImplementedError):
        _value(scene, "rangeShape.vt[0]")  # mesh data
    with pytest.raises(NotImplementedError):
        _value(scene, "anim_translateX.kit[0]")  # kept by the node


@pytest.mark.parametrize("path", UNITS, ids=_id)
def test_set_values(path: Path, tmp_path: Path) -> None:
    scene = py_maya_scene.parse(path)
    scene.node("values").attr("tx").value = 4
    scene.node("values").attr("r").value = (15, 25, 35)
    scene.node("added").attr("dst").value = 2
    scene.node("color").attr("ovcg").value = 0.25
    scene.node("rangeShape").attr("pt[5]").value = (0, 1, 0)
    scene.node("time1").attr("o").value = 10
    scene.node("values").attr("it").value = True
    target = tmp_path / path.name
    scene.save(target)
    saved = py_maya_scene.parse(target)
    assert _value(saved, "values.t") == pytest.approx((4, 2, 3))
    assert _value(saved, "values.r") == pytest.approx((15, 25, 35))
    assert _value(saved, "added.dst") == pytest.approx(2)
    assert _value(saved, "color.ovrgb") == (1.0, 0.25, 0.0)
    assert _value(saved, "rangeShape.pt[5]") == pytest.approx((0, 1, 0))
    assert _value(saved, "rangeShape.pt[6]") == pytest.approx((0, 0.06, 0))
    assert _value(saved, "time1.o") == pytest.approx(10)
    assert _value(saved, "values.it") is True


@pytest.mark.parametrize("path", UNITS, ids=_id)
def test_set_values_refused(path: Path) -> None:
    scene = py_maya_scene.parse(path)
    with pytest.raises(ValueError, match="locked"):
        scene.node("locks").attr("tx").value = 1
    with pytest.raises(ValueError, match="connected"):
        scene.node("anim").attr("tx").value = 1
    with pytest.raises(NotImplementedError):
        scene.node("anim_translateX").attr("kit[0]").value = 1  # kept by the node


@pytest.mark.parametrize("path", UNITS, ids=_id)
def test_add_records(path: Path, tmp_path: Path) -> None:
    """Plugs the file does not store get a record of their own."""
    scene = py_maya_scene.parse(path)
    values = scene.node("values")
    values.attr("shxy").value = 0.5
    scene.node("added").attr("spr[3]").value = 4
    values.attr("tz").locked = True  # `.t` has a record, `tz` none
    scene.node("rangeShape").attr("pt[5]").locked = True  # in a range record
    scene.node("sequence").attr("fileTextureName").value = "P:/new.exr"
    target = tmp_path / path.name
    scene.save(target)
    saved = py_maya_scene.parse(target)
    assert _value(saved, "values.sh") == pytest.approx((0.5, 0, 0))
    assert _value(saved, "added.spr") == [1.0, 4.0, 2.0]
    assert saved.node("values").attr("tz").locked
    assert not saved.node("values").attr("tx").locked
    assert saved.node("rangeShape").attr("pt[5]").locked
    assert not saved.node("rangeShape").attr("pt[6]").locked
    assert "P:/new.exr" in {d.path for d in saved.dependencies()}


@pytest.mark.parametrize("path", UNITS, ids=_id)
def test_set_flags(path: Path, tmp_path: Path) -> None:
    scene = py_maya_scene.parse(path)
    values = scene.node("values")
    values.attr("t").locked = True
    values.attr("ro").keyable = True
    scene.node("locks").attr("rx").locked = False
    target = tmp_path / path.name
    scene.save(target)
    saved = py_maya_scene.parse(target)
    assert saved.node("values").attr("ty").locked
    assert saved.node("values").attr("ro").keyable
    assert not saved.node("locks").attr("rx").locked
