# py_maya
`py_maya` is a Python package for working with Autodesk Maya scene files (`.mb` and `.ma`) without Maya.

Sibling project of [py-aep](https://github.com/forticheprod/py-aep) and [py-premiere](https://github.com/Pouf/py-premiere).

## About

Maya Binary files (`.mb`) are IFF chunk trees; Maya ASCII files (`.ma`) are MEL scripts. This package reads and writes both, keeping every byte it does not change, so that tools can inspect and edit scenes on machines and farms without a Maya licence.

## Status

Pre-alpha.

### Supported
* Reading and writing the `.mb` chunk tree (Maya 2018 to 2025), byte-identical on round-trip; files the writer could not reproduce are refused at parse time
* `.mb` scene model: version, file info, required plugins, references (at every depth), nodes (name, type, UUID, DAG path), string attribute values, connections
* Listing and replacing external dependencies in `.mb`: references, every attribute Maya flags as a file name (textures, caches, audio, Arnold, Alembic...) plus Yeti and Golaem ones seen in production, and paths set on referenced nodes (reference edits)
* Reference edits: decoded and re-encoded byte for byte (parent, addAttr, setAttr, deleteAttr, connect/disconnectAttr, lock/unlock)
* Saving atomically: `save(path)` refuses to overwrite, `save_in_place()` overwrites the parsed file
* `maya-inspect`: chunk tree and chunk statistics of a `.mb` file

### Not supported (yet)
* `.ma` files
* The `externalContentTable` metadata is left as is after a remap (Maya accepts it stale and rebuilds it on save)

### Planned
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
import py_maya

scene = py_maya.parse("shot.mb")
for dependency in scene.dependencies():
    print(dependency.node, dependency.attribute, dependency.path)

# Paths are passed exactly as stored; return None to keep one
scene.remap_paths(
    lambda path: path.replace("//server/old_project/", "//server/new_project/", 1)
    if path.startswith("//server/old_project/")
    else None
)
scene.save("shot_remapped.mb")
```

## Documentation

See [docs/file_format.md](docs/file_format.md) for what is known of the `.mb` and `.ma` formats.
