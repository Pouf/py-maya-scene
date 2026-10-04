"""py_maya - Autodesk Maya scene file (.mb, .ma) reader and writer."""

from __future__ import annotations

try:
    import importlib.metadata as importlib_metadata
except ImportError:
    import importlib_metadata  # type: ignore[import,no-redef]  # Python 3.7

try:
    __version__ = importlib_metadata.version("py-maya")
except importlib_metadata.PackageNotFoundError:
    # Running from a source tree without an installed distribution.
    __version__ = "0.0.0"
