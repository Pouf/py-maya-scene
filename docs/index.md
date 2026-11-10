# py_maya_scene Documentation

Welcome to the py_maya_scene documentation! This library reads and writes Autodesk Maya scene files (`.mb` and `.ma`) without Maya.

## About

Maya Binary files (`.mb`) are IFF chunk trees; Maya ASCII files (`.ma`) are MEL scripts. py_maya_scene parses both into one scene model and writes them back keeping every byte it does not change, so that tools can inspect and edit scenes on machines and farms without a Maya licence.

## Installation

=== "uv"

    ```bash
    uv add py-maya-scene
    ```

=== "pip"

    ```bash
    pip install py-maya-scene
    ```

## Quick Start

```python
import py_maya_scene

scene = py_maya_scene.parse("shot.mb")

# List the files the scene depends on
for dependency in scene.dependencies():
    print(dependency.node, dependency.attribute, dependency.path)

# Rename a node; everything that names it follows
scene.node("box").name = "crate"

# Save to a new file
scene.save("shot_edited.mb")
```

See the [Quick Start guide](quickstart.md) for examples.

## Key Concepts

### Scene Structure

A parsed scene exposes what the file itself stores:

```
Scene
├── Plugin
├── Reference
│   └── Reference (nested)
├── Node
│   └── Attribute
│       └── ReferenceEdits (on reference nodes)
│           └── ReferenceEdit
├── Connection
└── Dependency
```

### Data Model

- `Scene`: a `.mb` or `.ma` file: version, file info, plugins, references, nodes, connections
- `Node`: a node the file creates, or a default node it edits (name, type, UUID, DAG paths)
- `Attribute`: a value stored on a node; strings, string arrays and reference edits are decoded
- `Reference`: a reference to another scene file, at any depth
- `ReferenceEdits`, `ReferenceEdit`: the edits a file makes to referenced nodes
- `Connection`: a `connectAttr` stored in the file
- `Dependency`: a file path the scene stores (reference, file attribute, reference edit)
- `Plugin`: a required plugin and the node types used from it

### Guarantees

- **Byte-identical round-trip**: parsing then saving without edits gives the same bytes. Files that cannot be reproduced are refused at parse time.
- **Minimal edits**: an edit rewrites only the values it changes, keeping Maya's own layout around them.
- **One model for both formats**: everything works the same on `.mb` and `.ma`, unless noted in [Known Limitations](limitations.md).

## API Reference

Browse the [API Reference](api/index.md) for detailed documentation of all classes and methods.

## Contributing

Contributions are welcome! See the [Contributing Guide](contributing.md) to get started, or visit the [GitHub repository](https://github.com/Pouf/py-maya-scene) for more information.

## License

This project is licensed under the MIT License.
