from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import pytest

import py_maya
from py_maya.data.node_types import DEFAULT_NODES
from py_maya.metadata import ContentTable, path_crc
from py_maya.models import Scene

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "models"
MB_FILES = sorted(SAMPLES.glob("*/*.mb"))


def _id(path: Path) -> str:
    return path.relative_to(SAMPLES).as_posix()


def _renamer(scene: Scene) -> Callable[[str], str]:
    """Prefix every name the file gives (nodes, namespaces), as a whole name."""
    names = {n.name.rsplit(":", 1)[-1] for n in scene.nodes if not n.is_default}
    names |= {r.namespace for r in scene.references + scene.nested_references}
    names -= set(DEFAULT_NODES)
    pattern = re.compile(
        r"(?<![A-Za-z0-9_])("
        + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        + r")(?![A-Za-z0-9_])"
    )
    return lambda text: pattern.sub(lambda m: "z" + m.group(1), text)


def _plug(rename: Callable[[str], str], plug: str) -> str:
    node, dot, attribute = plug.partition(".")
    return rename(node) + dot + attribute


def _view(scene: Scene) -> dict:
    """What a replace must map: names, plugs, references, edits, content rows."""
    return {
        "nodes": [(n.paths, [a.name for a in n.attributes]) for n in scene.nodes],
        "connections": [(c.source, c.destination) for c in scene.connections],
        "references": [
            (r.path, r.namespace, r.node)
            for r in scene.references + scene.nested_references
        ],
        "edits": [
            (edits.owner, [node for node, _ in edits.all_edits()])
            for _, edits in scene.reference_edits()
        ],
        "rows": [
            (row.node, row.path)
            for row in ContentTable(scene._storage.metadata() or "").rows
        ],
    }


@pytest.mark.parametrize("path", MB_FILES, ids=_id)
def test_identity_keeps_every_byte(path: Path) -> None:
    scene = py_maya.parse(path)
    assert scene.replace_strings(lambda text: text) == 0
    assert scene._storage.to_bytes() == path.read_bytes()


@pytest.mark.parametrize("path", MB_FILES, ids=_id)
def test_names_change_everywhere_alike(path: Path, tmp_path: Path) -> None:
    scene = py_maya.parse(path)
    rename = _renamer(scene)
    before = _view(scene)
    assert scene.replace_strings(rename) > 0
    scene.save(tmp_path / path.name)
    after = _view(py_maya.parse(tmp_path / path.name))

    assert after["nodes"] == [
        ([rename(p) for p in paths], attributes)
        for paths, attributes in before["nodes"]
    ]
    assert after["connections"] == [
        (_plug(rename, s), _plug(rename, d)) for s, d in before["connections"]
    ]
    assert after["references"] == [tuple(map(rename, r)) for r in before["references"]]
    assert after["edits"] == [
        (rename(owner), [rename(n) for n in nodes]) for owner, nodes in before["edits"]
    ]
    assert after["rows"] == [(rename(n), rename(p)) for n, p in before["rows"]]


def test_content_table_crcs_follow(tmp_path: Path) -> None:
    path = SAMPLES / "maya2024" / "deps.mb"
    scene = py_maya.parse(path)
    scene.replace_strings(lambda text: text.replace("textures", "maps"))
    scene.save(tmp_path / "out.mb")
    rows = ContentTable(
        py_maya.parse(tmp_path / "out.mb")._storage.metadata() or ""
    ).rows
    assert any("maps" in row.path for row in rows)
    for row in rows:
        assert row._tokens[3] == str(path_crc(row.path))


def test_ma_is_not_supported() -> None:
    scene = py_maya.parse(SAMPLES / "maya2024" / "names.ma")
    with pytest.raises(NotImplementedError):
        scene.replace_strings(lambda text: text)
