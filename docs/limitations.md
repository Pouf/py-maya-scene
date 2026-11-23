# Known Limitations

This page documents limitations of py_maya_scene that arise from reading a
scene file rather than querying a running Maya.

## The file, not the loaded scene

py_maya_scene sees what the file stores. Referenced files are not loaded, so
the nodes they hold are not in `Scene.nodes`; only the reference itself and
the edits the file makes to referenced nodes are.

## Plug values

`Node.plug()` gives the value and flags the file gives a plug, which is what
`getAttr` gives for most plugs. Not always:

- A plug driven by a connection or an animation curve gives its stored
  static value, not the evaluated one.
- These raise `NotImplementedError`:
  - outputs Maya computes and never stores (`worldMatrix`);
  - elements of the multis a node keeps itself, which the file leaves out
    unless it sets them (mesh vertices, animation key tangents);
  - mesh geometry (`vt`, `ed`, `fc`, `n`);
  - NURBS curves and surfaces, component lists, character mappings and
    generic compounds.
- An `xform` matrix reads as its parts (`Xform`), where `getAttr` gives the
  composed 16-value matrix.
- What py_maya_scene knows of an attribute (kind of value, unit, default,
  keyable state) comes from the schemas of Maya 2022 and 2025. A file of
  Maya 2024 or older reads with Maya 2022's, which covers 2018 and 2019 files
  only as far as their attributes did not change since. Plugin attributes the
  schemas do not know read as stored, with no unit conversion, and their
  keyable state comes from `.mb` records only.
- A multi lists the elements the file stores. Elements at their default can
  be left out, even when Maya counts them.

Setting a plug writes into the records that hold it, or adds a record of its
own. Maya would sometimes write a parent attribute instead (`.t` for `tx`),
which reads the same. Numbers are written in their shortest exact form, not
always with Maya's digits. A locked or connected plug is refused, as in Maya.

## Dependencies

- Paths are reported as stored: unresolved, with environment variables kept.
- File attributes come from Maya's own schema (attributes flagged as file
  names) and from the attributes a file adds with `addAttr -usedAsFilename`,
  plus Yeti and Golaem ones. Other plugin attributes holding paths need
  `extra` (see `Scene.dependencies`).
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
- A `maya.cmds`-like API on a scene file, beyond plug values and flags
- Adding or removing attributes and connections
