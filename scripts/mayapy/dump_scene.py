# -*- coding: utf-8 -*-
"""Export Maya's own view of scene files as JSON ground truth (`<file>.json`).

    "C:/Program Files/Autodesk/Maya2024/bin/mayapy.exe" scripts/mayapy/dump_scene.py samples/models/maya2024/*.mb

Files are opened without loading references, so the JSON describes the file
itself, which is what py_maya parses. Runs under Python 2.7 (Maya 2018).
"""

from __future__ import print_function, unicode_literals

import glob
import io
import json
import os
import sys

import maya.standalone

maya.standalone.initialize(name="python")
import maya.cmds as cmds  # noqa: E402

os.environ.setdefault("PY_MAYA_SAMPLES", "")


def path_values(node):
    values = []
    for attr in cmds.listAttr(node, usedAsFilename=True) or []:
        plug = node + "." + attr
        try:
            value = cmds.getAttr(plug)
        except (RuntimeError, ValueError):  # multi/compound parents have no value
            continue
        if value:
            values.append([plug, value])
    return values


def dump(path):
    os.environ["PY_MAYA_SAMPLES"] = os.path.dirname(os.path.abspath(path)).replace(
        "\\", "/"
    )
    cmds.file(path, open=True, force=True, loadReferenceDepth="none")
    nodes = cmds.ls(long=True) or []
    connections = (
        cmds.listConnections(
            nodes, connections=True, plugs=True, source=False, destination=True
        )
        or []
    )
    data = {
        "maya": cmds.about(version=True),
        "nodes": [
            {
                "name": n,
                "type": cmds.nodeType(n),
                "uuid": (cmds.ls(n, uuid=True) or [""])[0],
            }
            for n in nodes
        ],
        "connections": sorted(
            [connections[i], connections[i + 1]] for i in range(0, len(connections), 2)
        ),
        # referenceQuery -namespace fails on unloaded references; file -q -ns does not
        "references": [
            {
                "node": r,
                "namespace": cmds.file(
                    cmds.referenceQuery(r, filename=True), query=True, namespace=True
                ),
                "path": cmds.referenceQuery(r, filename=True, unresolvedName=True),
            }
            for r in cmds.ls(type="reference")
            if r != "sharedReferenceNode" and not r.endswith("_UNKNOWN_REF_NODE_")
        ],
        "paths": sorted(v for n in nodes for v in path_values(n)),
    }
    # ASCII JSON in binary mode: the same bytes from Python 2.7 (Maya 2018) and 3
    with io.open(path + ".json", "wb") as f:
        f.write(json.dumps(data, indent=1, sort_keys=True).encode("ascii"))
    print("wrote", path + ".json")


for pattern in sys.argv[1:]:
    for path in sorted(glob.glob(pattern)):
        dump(path)
maya.standalone.uninitialize()
