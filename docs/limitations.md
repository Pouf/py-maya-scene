# Known Limitations

This page documents limitations of py_maya_scene that arise from reading a
scene file rather than querying a running Maya.

## The file, not the loaded scene

py_maya_scene sees what the file stores. Referenced files are not loaded, so
the nodes they hold are not in `Scene.nodes`; only the reference itself and
the edits the file makes to referenced nodes are.

## Values other than strings

Strings (`STR`), string arrays (`STR#`) and reference edits (`REFE`) are
decoded. Numbers, matrices, meshes and other values are kept as stored and
written back unchanged, but `Attribute.value` does not decode them.

## Dependencies

- Paths are reported as stored: unresolved, with environment variables kept.
- File attributes come from Maya's own schema (attributes flagged as file
  names), plus Yeti and Golaem ones. Other plugin attributes holding paths
  need `extra` (see `Scene.dependencies`).
- For reference edits, the edited node's type is not stored in the file, so
  attributes are matched by name only.

## Renaming

- Reference nodes and default nodes cannot be renamed.
- Node names inside the MEL of expressions and script nodes are only
  rewritten on request (`in_strings=True`), and a string that only looks like
  a node name is rewritten too.

## replace_strings

`Scene.replace_strings` works on `.mb` files only. A `.ma` file is text, where
a plain text replace does the same.

## Files that are refused

A file that py_maya_scene cannot write back byte for byte is refused at parse
time (`ValueError`) rather than silently rewritten. So is a reference edit of
a kind not known yet.

## Maya versions

Maya 2018 to 2025 (`FOR8` files).

## Not available yet

- Converting between `.ma` and `.mb`
- A `maya.cmds`-like API on a scene file
