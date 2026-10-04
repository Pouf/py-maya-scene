"""File path attributes that the generated schema tables miss.

The schema dumps only cover plugins installed where they ran, and only
attributes flagged `usedAsFilename`. These attributes held file or directory
paths in production scenes (Yeti, Golaem); names are the short names stored
in files.
"""

from __future__ import annotations

EXTRA_FILE_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "CharacterMakerLocator": ("cuf",),
    "CrowdEntityTypeNode": ("gch",),
    "CrowdManagerNode": ("escod", "gch"),
    "MotionClip": ("gmo",),
    "SimulationCacheProxy": ("lf", "lfp"),
    "pgYetiMaya": ("cfn", "ofn"),
}
