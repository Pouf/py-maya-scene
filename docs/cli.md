# Command Line Tools

py_maya_scene provides one command-line utility. It is installed automatically when you install the package.

## Installation

=== "uv"

    ```bash
    uv add py-maya-scene
    ```

=== "pip"

    ```bash
    pip install py-maya-scene
    ```

---

## maya-inspect

Prints the chunk tree of a Maya Binary file, with a preview of each chunk's data, or chunk counts by tag.

### Usage

```bash
maya-inspect scene.mb
maya-inspect scene.mb --depth 1
maya-inspect scene.mb --summary
```

### Arguments

| Argument | Description |
|----------|-------------|
| `path` | Path to the `.mb` file |
| `--depth` | Maximum tree depth (`1` shows the top-level forms only) |
| `--preview` | Data bytes shown per chunk (default 48) |
| `--summary` | Print chunk counts by tag instead of the tree |
