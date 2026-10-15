"""py_maya - Autodesk Maya scene file (.mb, .ma) reader and writer."""

from __future__ import annotations

from pathlib import Path

from .binary import GROUP_TAGS
from .models import Scene
from .storage.ma import MaScene
from .storage.mb import MbScene

try:
    import importlib.metadata as importlib_metadata
except ImportError:
    import importlib_metadata  # type: ignore[import,no-redef]  # Python 3.7

try:
    __version__ = importlib_metadata.version("py-maya-scene")
except importlib_metadata.PackageNotFoundError:
    # Running from a source tree without an installed distribution.
    __version__ = "0.0.0"

__all__ = ["Scene", "parse"]


def parse(path: str | Path) -> Scene:
    """Parse a Maya scene file, `.mb` or `.ma` (told apart by content, not extension)."""
    path = Path(path)
    data = path.read_bytes()
    storage = MbScene(data) if data[:4] in GROUP_TAGS else MaScene(data)
    return Scene(storage, path)
