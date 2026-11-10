# API Reference

Welcome to the py_maya_scene API reference. This section provides detailed documentation for the classes and functions of the library.

## Main Entry Point

The primary function you'll use is [`parse()`](parsers.md).

## Core Modules

### [Scene](scene.md)

The `Scene` parsed from a `.mb` or `.ma` file: version, plugins, references, nodes, connections, dependencies, remapping and saving.


### Nodes

What the file creates or edits:

- [Node](node.md) - A node, with its name, type, UUID and DAG paths
- [Attribute](attribute.md) - A value stored on a node

### References

Links to other scene files:

- [Reference](reference.md) - A reference, at any depth
- [Reference Edits](other/reference_edits.md) - The edits a file makes to referenced nodes

### Other

Values returned by the scene:

- [Dependency](other/dependency.md) - A file path the scene stores
- [Connection](other/connection.md) - A `connectAttr` stored in the file
- [Plugin](other/plugin.md) - A required plugin

## Quick Start

See the [Quick Start guide](../quickstart.md) for usage examples (parsing, dependencies, remapping, renaming, saving).
