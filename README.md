# py_maya
`py_maya` is a Python package for working with Autodesk Maya scene files (`.mb` and `.ma`) without Maya.

Sibling project of [py-aep](https://github.com/forticheprod/py-aep) and [py-premiere](https://github.com/Pouf/py-premiere).

## About

Maya Binary files (`.mb`) are IFF chunk trees; Maya ASCII files (`.ma`) are MEL scripts. This package reads and writes both, keeping every byte it does not change, so that tools can inspect and edit scenes on machines and farms without a Maya licence.

## Status

Pre-alpha.

### Supported
Everything below works the same on `.mb` and `.ma` (Maya 2018 to 2025), through one scene model.

* Byte-identical round-trip: parse then save without edits gives the same bytes (all 771 production `.mb` and 3300 of 3305 production `.ma`; files that cannot be reproduced are refused at parse time); edits rewrite only the values they change, keeping Maya's own layout around them
* Scene model: version, file info, required plugins, references (at every depth, with file type), nodes (name, type, UUID, DAG paths including instances), string attribute values, connections
* Listing and replacing external dependencies: references, every attribute Maya flags as a file name (textures, caches, audio, Arnold, Alembic...) plus Yeti and Golaem ones seen in production, and paths set on referenced nodes (reference edits). A reference moved to a `.ma` or `.mb` file gets the matching file type
* Reference edits: decoded and re-encoded byte for byte (parent, addAttr, setAttr, deleteAttr, connect/disconnectAttr, lock/unlock)
* Renaming nodes (`node.name = "new"`, `node.rename(...)`): parents, connections, relationships, instances, reference edits and the `externalContentTable` that name the node or its descendants are rewritten the way Maya writes them, and a transform's shapes follow like with `maya.cmds.rename`; checked against renames done by Maya 2018, 2022 and 2024. Optionally (`in_strings=True`) also node names in string attributes and in expression / script node MEL, which Maya leaves alone
* The `externalContentTable` follows remaps, renames and edited path values (CRC included)
* Saving atomically: `save(path)` refuses to overwrite, `save_in_place()` overwrites the parsed file
* `maya-inspect`: chunk tree and chunk statistics of a `.mb` file

### Not supported (yet)
* Renaming reference nodes and default nodes
* Values other than strings (numbers, matrices, meshes) are kept as stored but not decoded

### Planned
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
