# -*- coding: utf-8 -*-
"""Dump the node type schema of a Maya version as JSON.

    "C:/Program Files/Autodesk/Maya2024/bin/mayapy.exe" scripts/mayapy/dump_schema.py build/schema

For each node type: its 4-byte type id (the form type of its `.mb` group),
its parent types, and the attributes it declares (those its parent type does
not have) as [long name, short name, API type, usedAsFilename, array, parent].
Plugins that are installed are loaded first so their node types are included.
"""

from __future__ import print_function, unicode_literals

import io
import json
import os
import sys

import maya.standalone

maya.standalone.initialize(name="python")
import maya.api.OpenMaya as om  # noqa: E402
import maya.cmds as cmds  # noqa: E402

PLUGINS = [
    "AbcImport",
    "gpuCache",
    "mtoa",
    "matrixNodes",
    "quatNodes",
    "stereoCamera",
    "ikSpringSolver",
    "mayaUsdPlugin",
    "MASH",
    "renderSetup.py",
    "lookdevKit",
]
loaded = []
for plugin in PLUGINS:
    try:
        cmds.loadPlugin(plugin, quiet=True)
        loaded.append(plugin)
    except RuntimeError:  # not installed for this Maya version
        pass


def attributes(node_type):
    out = {}
    for obj in om.MNodeClass(node_type).getAttributes():
        fn = om.MFnAttribute(obj)
        parent = om.MFnAttribute(fn.parent).name if not fn.parent.isNull() else None
        out[fn.name] = [
            fn.name,
            fn.shortName,
            obj.apiTypeStr,
            fn.usedAsFilename,
            fn.array,
            parent,
        ]
    return out


types = {}
all_attrs = {}
for entry in cmds.allNodeTypes(includeAbstract=True):
    name = entry.split(" ")[0]
    try:
        attrs = attributes(name)
        type_id = om.MNodeClass(name).typeId.id()
        inherited = cmds.nodeType(name, inherited=True, isTypeName=True) or [name]
    except (RuntimeError, ValueError):  # a few abstract types have no node class
        continue
    all_attrs[name] = attrs
    types[name] = {
        "id": "%08x" % (type_id & 0xFFFFFFFF),
        "inherited": inherited[:-1],
        "abstract": entry.endswith("(abstract)"),
    }
for name, info in types.items():
    parent = info["inherited"][-1] if info["inherited"] else None
    inherited_attrs = all_attrs.get(parent, {})
    info["attributes"] = sorted(
        v for k, v in all_attrs[name].items() if k not in inherited_attrs
    )

# Nodes every scene starts with; `.mb` select blocks name them without a type
cmds.file(new=True, force=True)
default_nodes = {n: cmds.nodeType(n) for n in cmds.ls()}

out_dir = sys.argv[1] if len(sys.argv) > 1 else "build/schema"
if not os.path.isdir(out_dir):
    os.makedirs(out_dir)
path = os.path.join(out_dir, "maya%s.json" % cmds.about(version=True).split()[0])
# ASCII JSON in binary mode: the same bytes from Python 2.7 (Maya 2018) and 3
with io.open(path, "wb") as f:
    f.write(
        json.dumps(
            {
                "maya": cmds.about(version=True),
                "plugins": loaded,
                "types": types,
                "default_nodes": default_nodes,
            },
            indent=0,
            sort_keys=True,
        ).encode("ascii")
    )
print("wrote", path, len(types), "types")
maya.standalone.uninitialize()
