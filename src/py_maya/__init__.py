"""py_maya - Autodesk Maya scene file (.mb, .ma) reader and writer."""

from __future__ import annotations

from pathlib import Path

from .binary import read_mb
from .models import Scene

try:
    import importlib.metadata as importlib_metadata
except ImportError:
    import importlib_metadata  # type: ignore[import,no-redef]  # Python 3.7

try:
    __version__ = importlib_metadata.version("py-maya")
except importlib_metadata.PackageNotFoundError:
    # Running from a source tree without an installed distribution.
    __version__ = "0.0.0"

__all__ = ["Scene", "parse"]


def parse(path: str | Path) -> Scene:
    """Parse a Maya Binary (`.mb`) file."""
    path = Path(path)
    return Scene(read_mb(path.read_bytes()), path)
