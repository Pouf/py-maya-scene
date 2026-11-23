"""Scene units: their names in each format and their size in Maya's internal
units, centimetres, radians and ticks."""

from __future__ import annotations

import math
from typing import NamedTuple

TICKS_PER_SECOND = 141120000

# Short name, as `.mb` and `currentUnit -q` give it > long name, as `.ma` writes it
LINEAR_NAMES = {
    "mm": "millimeter",
    "cm": "centimeter",
    "m": "meter",
    "km": "kilometer",
    "in": "inch",
    "ft": "foot",
    "yd": "yard",
    "mi": "mile",
}
ANGLE_NAMES = {"deg": "degree", "rad": "radian"}

_CENTIMETRES = {
    "mm": 0.1,
    "cm": 1.0,
    "m": 100.0,
    "km": 100000.0,
    "in": 2.54,
    "ft": 30.48,
    "yd": 91.44,
    "mi": 160934.4,
}
_RADIANS = {"deg": math.pi / 180, "rad": 1.0}
# Frames per second of the named time units, other ones are `<N>fps`
_FPS = {
    "game": 15.0,
    "film": 24.0,
    "pal": 25.0,
    "ntsc": 30.0,
    "show": 48.0,
    "palf": 50.0,
    "ntscf": 60.0,
    "sec": 1.0,
    "millisec": 1000.0,
    "min": 1 / 60,
    "hour": 1 / 3600,
    "29.97df": 30000 / 1001,
}


def short_unit_name(name: str) -> str:
    """The short name of a linear or angle unit given by either name (`meter` > `m`)."""
    for names in (LINEAR_NAMES, ANGLE_NAMES):
        for short, long in names.items():
            if name == long:
                return short
    return name


def frames_per_second(time_unit: str) -> float:
    """Frames per second of a time unit (`film`, `23.976fps`)."""
    if time_unit in _FPS:
        return _FPS[time_unit]
    if not time_unit.endswith("fps"):
        raise ValueError(f"unknown time unit {time_unit!r}")
    rate = float(time_unit[:-3])
    # Fractional rates are the NTSC ones, a whole rate times 1000/1001
    whole = round(rate * 1.001)
    if rate != whole and abs(whole * 1000 / 1001 - rate) < 0.001:
        return whole * 1000 / 1001
    return rate


class Units(NamedTuple):
    """The units of a scene, by short name (`cm`, `deg`, `film`)."""

    linear: str = "cm"
    angle: str = "deg"
    time: str = "film"

    @property
    def centimetres(self) -> float:
        """Centimetres per linear unit."""
        if self.linear not in _CENTIMETRES:
            raise ValueError(f"unknown linear unit {self.linear!r}")
        return _CENTIMETRES[self.linear]

    @property
    def radians(self) -> float:
        """Radians per angle unit."""
        if self.angle not in _RADIANS:
            raise ValueError(f"unknown angle unit {self.angle!r}")
        return _RADIANS[self.angle]

    @property
    def ticks_per_frame(self) -> float:
        return TICKS_PER_SECOND / frames_per_second(self.time)
