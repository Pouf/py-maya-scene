# -*- coding: utf-8 -*-
"""Dump the node type schema of a Maya version as JSON.

    "C:/Program Files/Autodesk/Maya2024/bin/mayapy.exe" scripts/mayapy/dump_schema.py build/schema

For each node type: its 4-byte type id (the form type of its `.mb` group),
its parent types, and the attributes it declares (those its parent type does
not have) as [long name, short name, API type, usedAsFilename, array, parent,
details]. Details hold the numeric, unit or data type name, the default in
internal units (centimetres, radians, seconds), the keyable, channelBox,
storable, hidden and internal flags, and the children of compounds. The
values a new node starts with, where they are not the defaults, go to
`maya<version>_creation.jsonl`. Plugins that are installed are loaded first
so their node types are included.
"""

from __future__ import print_function, unicode_literals

import io
import json
import math
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


def enum_names(cls, names):
    """Value > name of the enum constants `names`, by name: function set
    classes also inherit the constants of their base classes."""
    return {getattr(cls, n): n for n in names if hasattr(cls, n)}


NUMERIC_TYPES = enum_names(
    om.MFnNumericData,
    "kBoolean kByte kChar kShort k2Short k3Short kLong k2Long k3Long kInt64 "
    "kFloat k2Float k3Float kDouble k2Double k3Double k4Double kAddr".split(),
)
UNIT_TYPES = enum_names(om.MFnUnitAttribute, ["kAngle", "kDistance", "kTime"])
DATA_TYPES = enum_names(om.MFnData, [n for n in vars(om.MFnData) if n.startswith("k")])


def plain(value):
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, om.MDistance):
        return value.asCentimeters()
    if isinstance(value, om.MAngle):
        return value.asRadians()
    if isinstance(value, om.MTime):
        return value.asUnits(om.MTime.kSeconds)
    return value


def instance_default(node_type, short_name):
    """Default read from a created node, for attributes whose default the
    attribute function set does not give (float distances and angles)."""
    node = cmds.createNode(node_type, skipSelect=True)
    try:
        selection = om.MSelectionList()
        selection.add(node + "." + short_name)
        return selection.getPlug(0).asDouble()
    finally:
        cmds.delete(node)


def data_default(data):
    """Default of a typed attribute. None when it has no default data, which
    `getAttr` gives for an unset value, else a string, a matrix or a list."""
    if data.isNull():
        return None
    if data.hasFn(om.MFn.kStringData):
        return om.MFnStringData(data).string()
    if data.hasFn(om.MFn.kMatrixData):
        return list(om.MFnMatrixData(data).matrix())
    return []


def details(node_type, obj, fn):
    out = {
        "keyable": fn.keyable,
        "channelBox": fn.channelBox,
        "storable": fn.storable,
        "hidden": fn.hidden,
        "internal": fn.internal,
    }
    try:
        if obj.hasFn(om.MFn.kNumericAttribute):
            numeric = om.MFnNumericAttribute(obj)
            out["numeric"] = NUMERIC_TYPES.get(numeric.numericType())
            out["default"] = plain(numeric.default)
        elif obj.hasFn(om.MFn.kUnitAttribute):
            unit = om.MFnUnitAttribute(obj)
            out["unit"] = UNIT_TYPES.get(unit.unitType())
            try:
                out["default"] = plain(unit.default)
            except RuntimeError:  # float units: read from a node instead
                out["default"] = instance_default(node_type, fn.shortName)
        elif obj.hasFn(om.MFn.kEnumAttribute):
            out["default"] = om.MFnEnumAttribute(obj).default
        elif obj.hasFn(om.MFn.kTypedAttribute):
            typed = om.MFnTypedAttribute(obj)
            out["data"] = DATA_TYPES.get(typed.attrType())
            out["default"] = data_default(typed.default)
        elif obj.hasFn(om.MFn.kMatrixAttribute):
            out["default"] = list(om.MFnMatrixAttribute(obj).default)
        if obj.hasFn(om.MFn.kCompoundAttribute):
            compound = om.MFnCompoundAttribute(obj)
            out["children"] = [
                om.MFnAttribute(compound.child(i)).shortName
                for i in range(compound.numChildren())
            ]
    except RuntimeError as e:  # abstract types cannot be created
        out["error"] = str(e)
    return out


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
            details(node_type, obj, fn),
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


def in_multi(fn):
    """Whether the attribute or one of its parents is a multi."""
    while True:
        if fn.array:
            return True
        if fn.parent.isNull():
            return False
        fn = om.MFnAttribute(fn.parent)


def creation_values(node_type):
    """Values a new node of `node_type` starts with that are not the
    attribute defaults, in internal units. A file leaves out what a new node
    already has. Read in centimetres, degrees and film, a new scene's units."""
    try:
        node = cmds.createNode(node_type, skipSelect=True)
    except RuntimeError as e:
        return {"": str(e)}
    found = {}
    selection = om.MSelectionList()
    selection.add(node)
    node_fn = om.MFnDependencyNode(selection.getDependNode(0))
    for i in range(node_fn.attributeCount()):
        fn = om.MFnAttribute(node_fn.attribute(i))
        if fn.name not in all_attrs[node_type] or fn.dynamic:
            continue
        defaults = all_attrs[node_type][fn.name][6]
        # Single numbers only, booleans included
        if (
            not defaults["storable"]
            or not isinstance(defaults.get("default"), (int, float))
            or in_multi(fn)
        ):
            continue
        try:
            value = cmds.getAttr(node + "." + fn.shortName)
        except (RuntimeError, ValueError, TypeError):  # no value to read
            continue
        if isinstance(value, (list, tuple)) or value is None:
            continue
        if defaults.get("unit") == "kAngle":
            value = math.radians(value)
        elif defaults.get("unit") == "kTime":
            value = value / 24.0
        if abs(float(value) - float(defaults["default"])) > 1e-9:
            found[fn.shortName] = value
    parents = cmds.listRelatives(node, parent=True) or []
    cmds.delete(parents or node)
    return found


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

# Creation values, one JSON line per type, written as they come. Creating
# some node types crashes Maya, so a run resumes after the last type started
# and skips it. Run the script again until it prints "creation values done".
creation_path = path[: -len(".json")] + "_creation.jsonl"
started = set()
if os.path.exists(creation_path):
    with io.open(creation_path, encoding="ascii") as f:
        for line in f:
            started.add(json.loads(line)["type"])
with io.open(creation_path, "ab") as f:
    # Plugin types last, most start with a capital. Creating some of them
    # leaves Maya unable to create other nodes, new scenes or not
    for name in sorted(types, key=lambda n: (n[:1].isupper(), n)):
        if types[name]["abstract"] or name in started:
            continue
        f.write(json.dumps({"type": name}).encode("ascii") + b"\n")
        f.flush()
        # A new scene per type, since some plugin nodes leave a scene where
        # later nodes fail to be created
        cmds.file(new=True, force=True)
        try:
            values = creation_values(name)
        except (RuntimeError, TypeError) as e:  # types that create no plain node
            values = {"": str(e)}
        if "" in values:
            entry = {"type": name, "error": values[""]}
        else:
            entry = {"type": name, "values": values}
        if values:
            f.write(json.dumps(entry, sort_keys=True).encode("ascii") + b"\n")
            f.flush()
print("creation values done")
maya.standalone.uninitialize()
