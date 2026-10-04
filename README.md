# py_maya
`py_maya` is a Python package for working with Autodesk Maya scene files (`.mb` and `.ma`) without Maya.

Sibling project of [py-aep](https://github.com/forticheprod/py-aep) and [py-premiere](https://github.com/Pouf/py-premiere).

## About

Maya Binary files (`.mb`) are IFF chunk trees; Maya ASCII files (`.ma`) are MEL scripts. This package reads and writes both, keeping every byte it does not change, so that tools can inspect and edit scenes on machines and farms without a Maya licence.

## Status

Pre-alpha.

### Supported
* Reading and writing the `.mb` chunk tree (Maya 2018 to 2025), byte-identical on round-trip; files the writer could not reproduce are refused at parse time
* `maya-inspect`: chunk tree and chunk statistics of a `.mb` file

### Planned
* A scene model shared by both formats: header, plugins, references, nodes, attributes, connections
* Listing and replacing external dependencies (references, textures, caches, audio, plugin files)
* Renaming nodes, with every connection, selection and reference edit that names them
* Reading and writing `.ma` with the same guarantees
* Converting between `.ma` and `.mb`
* A `maya.cmds`-like API on a scene file

## Installation

```sh
uv sync --extra dev
```

## Getting started

```python
from py_maya.binary import read_mb, walk, write_mb

with open("scene.mb", "rb") as f:
    root = read_mb(f.read())
for parent, chunk in walk(root):
    ...
with open("copy.mb", "wb") as f:
    write_mb(f, root)
```

## Documentation

See [docs/file_format.md](docs/file_format.md) for what is known of the `.mb` and `.ma` formats.
