# Quick Start

## Parse a scene

```python
import py_maya_scene

scene = py_maya_scene.parse("shot.mb")  # or a .ma file

print(f"Maya version: {scene.version}")
print(f"File info: {scene.file_info}")
for plugin in scene.plugins:
    print(f"Requires {plugin.name} {plugin.version}")
```

The format is told apart by content, not by extension.

## Iterate over nodes and attributes

```python
for node in scene.nodes:
    print(f"{node.path} ({node.type})")

node = scene.node("diffuseTex")  # a unique short name, or a DAG path
for attribute in node.attributes:
    print(f"  {attribute.name} [{attribute.type}] = {attribute.value!r}")
```

Strings (`STR`), string arrays (`STR#`) and reference edits (`REFE`) are
decoded; other values are kept as stored.

## Inspect references

```python
for reference in scene.references:
    print(f"{reference.node}: {reference.path} ({reference.file_type})")
    print(f"  namespace: {reference.namespace}, deferred: {reference.deferred}")

# References made by referenced files, as recorded in this file
for reference in scene.nested_references:
    print(f"depth {reference.depth}: {reference.path}")
```

## List file dependencies

`dependencies()` lists every file path the scene stores: references at every
depth, the attributes Maya flags as file names (textures, caches, audio,
Alembic...) and paths set on referenced nodes by reference edits.

```python
for dependency in scene.dependencies():
    print(
        f"{dependency.kind}: {dependency.node}.{dependency.attribute} = {dependency.path}"
    )
```

Attributes Maya's schema does not flag can be added per node type:

```python
scene.dependencies(extra={"transform": ["icon_path"]})
```

## Remap paths

`remap_paths()` covers everything `dependencies()` lists. The function sees
each path exactly as stored and returns the new path, or `None` to keep it.

```python
def remap(path):
    if path.startswith("//server/old_project/"):
        return path.replace("//server/old_project/", "//server/new_project/", 1)
    return None


changed = scene.remap_paths(remap)
print(f"{changed} paths changed")
```

A reference moved to a `.ma` or `.mb` file gets the matching file type.

## Rename a node

Parents, connections, relationships, instances, reference edits and the
`externalContentTable` that name the node are rewritten the way Maya writes
them. Like `maya.cmds.rename`, a transform's shapes follow.

```python
scene.node("box").name = "crate"

# Keep the shapes' names, and also rewrite node names in string attributes
# and in expression / script node MEL (which Maya leaves alone)
scene.node("ball").rename("sphere", shapes=False, in_strings=True)
```

## Replace strings everywhere

`.mb` only. `replace_strings()` puts every name and string the file stores
through one function, like a text replace over a `.ma` file: node names,
namespaces, reference nodes, string values, reference paths and `fileInfo`.

```python
scene = py_maya_scene.parse("asset.mb")
scene.replace_strings(lambda text: text.replace("old_asset", "new_asset"))
```

## Save

```python
scene.save("shot_edited.mb")  # refuses to overwrite an existing file
scene.save_in_place()  # overwrites the parsed file, atomically
```

The format stays the one the scene was parsed from.
