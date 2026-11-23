from __future__ import annotations

from pathlib import Path

import pytest

import py_maya_scene
from py_maya_scene.units import Units, frames_per_second, short_unit_name

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "models"


@pytest.mark.parametrize("version", ["maya2022", "maya2025"])
@pytest.mark.parametrize("ext", ["mb", "ma"])
def test_scene_units(version: str, ext: str) -> None:
    scene = py_maya_scene.parse(SAMPLES / version / f"units.{ext}")
    # `requires maya` is the first statement, after the header comments.
    # Maya 2025 writes `2025ff03`
    assert scene.version[:4] == version[-4:]
    assert scene.units == Units("m", "deg", "ntsc")
    assert scene.units.centimetres == 100.0


@pytest.mark.parametrize("ext", ["mb", "ma"])
def test_default_units(ext: str) -> None:
    scene = py_maya_scene.parse(SAMPLES / "maya2024" / f"deps.{ext}")
    assert scene.units == Units("cm", "deg", "film")


def test_unit_names() -> None:
    assert short_unit_name("meter") == "m"
    assert short_unit_name("radian") == "rad"
    assert short_unit_name("cm") == "cm"


def test_frames_per_second() -> None:
    # Ticks per frame as Maya 2025 gives them
    assert Units(time="film").ticks_per_frame == 5880000
    assert Units(time="23.976fps").ticks_per_frame == pytest.approx(5885880)
    assert Units(time="29.97df").ticks_per_frame == pytest.approx(4708704)
    assert Units(time="119.88fps").ticks_per_frame == pytest.approx(1177176)
    assert frames_per_second("12fps") == 12
    assert frames_per_second("48000fps") == 48000
    with pytest.raises(ValueError):
        frames_per_second("weekly")
