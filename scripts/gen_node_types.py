"""Generate `src/py_maya_scene/data/node_types.py` and `attribute_specs.py` from mayapy schema dumps.

    uv run python scripts/gen_node_types.py build/schema/maya2018.json build/schema/maya2022.json build/schema/maya2024.json build/schema/maya2025.json

Pass the dumps oldest first: a newer version wins when two disagree. Specs
come from the dumps that hold attribute details (Maya 2022 and later dumps).
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

OUT = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "py_maya_scene"
    / "data"
    / "node_types.py"
)
SPECS_OUT = OUT.with_name("attribute_specs.py")

# Kind of value of an attribute, by numeric type, data type, then API type
NUMERIC_KINDS = {
    "kBoolean": "bool",
    "kByte": "byte",
    "kChar": "char",
    "kShort": "short",
    "kLong": "long",
    "kInt": "long",
    "kInt64": "int64",
    "kFloat": "float",
    "kDouble": "double",
    "k2Short": "short2",
    "k3Short": "short3",
    "k2Long": "long2",
    "k2Int": "long2",
    "k3Long": "long3",
    "k3Int": "long3",
    "k2Float": "float2",
    "k3Float": "float3",
    "k2Double": "double2",
    "k3Double": "double3",
    "k4Double": "double4",
}
DATA_KINDS = {
    "kString": "string",
    "kStringArray": "stringArray",
    "kMatrix": "matrix",
    "kDoubleArray": "doubleArray",
    "kIntArray": "Int32Array",
    "kPointArray": "pointArray",
    "kVectorArray": "vectorArray",
    "kMesh": "mesh",
    "kNurbsCurve": "nurbsCurve",
    "kNurbsSurface": "nurbsSurface",
    "kComponentList": "componentList",
}
API_KINDS = {
    "kDoubleLinearAttribute": "linear",
    "kFloatLinearAttribute": "flinear",
    "kDoubleAngleAttribute": "angle",
    "kFloatAngleAttribute": "fangle",
    "kTimeAttribute": "time",
    "kEnumAttribute": "enum",
    "kMessageAttribute": "message",
    "kMatrixAttribute": "matrix",
    "kFloatMatrixAttribute": "fltMatrix",
    "kCompoundAttribute": "compound",
}
FLAGS = (("k", "keyable"), ("c", "channelBox"), ("s", "storable"))


def kind_of(api_type: str, details: dict[str, Any]) -> str:
    if "numeric" in details:
        return NUMERIC_KINDS.get(details["numeric"], "other")
    if "data" in details:
        return DATA_KINDS.get(details["data"], "data")
    return API_KINDS.get(api_type, "other")


def main(paths: list[str]) -> None:
    names: dict[int, str] = {}
    parents: dict[str, str] = {}
    file_attributes: dict[str, set[tuple[str, str]]] = {}
    short_names: dict[str, set[tuple[str, str]]] = {}
    default_nodes: dict[str, str] = {}
    specs: dict[str, dict[str, list[Any]]] = {}
    versions = []
    spec_versions = []
    for path in paths:
        dump = json.loads(Path(path).read_text(encoding="ascii"))
        versions.append(dump["maya"].split()[0])
        declared = {n: i["attributes"] for n, i in dump["types"].items()}
        has_details = False
        for name, info in dump["types"].items():
            names[int(info["id"], 16)] = name
            # Every link of the chain: some intermediate types (THsurfaceShape)
            # are not listed as types of their own
            chain = info["inherited"] + [name]
            for parent, child in zip(chain, chain[1:]):
                parents[child] = parent
            for attribute in info["attributes"]:
                long_name, short_name, _, used_as_filename = attribute[:4]
                if used_as_filename:
                    file_attributes.setdefault(name, set()).add((short_name, long_name))
                short_names.setdefault(name, set()).add((long_name, short_name))
            # Parents are dumped by long name, maybe declared by an ancestor type
            chain_short_names = {}
            for current in chain:
                for attribute in declared.get(current, []):
                    chain_short_names[attribute[0]] = attribute[1]
            for attribute in info["attributes"]:
                if len(attribute) < 7:
                    continue
                has_details = True
                _, short_name, api_type, _, array, parent, details = attribute
                flags = "a" if array else ""
                for letter, key in FLAGS:
                    if details.get(key):
                        flags += letter
                specs.setdefault(name, {})[short_name] = [
                    short_name,
                    kind_of(api_type, details),
                    chain_short_names.get(parent) if parent else None,
                    details.get("children"),
                    details.get("default"),
                    flags,
                ]
        if has_details:
            spec_versions.append(versions[-1])
        default_nodes.update(dump["default_nodes"])

    attribute_names = []
    for name, pairs in sorted(short_names.items()):
        # A name two versions give to different attributes would be a guess
        longs = Counter(long for long, _ in pairs)
        shorts = Counter(short for _, short in pairs)
        kept = []
        for long, short in sorted(pairs):
            if long != short and longs[long] == 1 and shorts[short] == 1:
                kept.append(f"{short} {long}")
        if kept:
            text = " ".join(kept)
            attribute_names.append(f"    {name!r}: {text!r},")

    lines = [
        '"""Maya node types: type ids, parent types, file path attributes, attribute names, default nodes.',
        "",
        f"Generated by `scripts/gen_node_types.py` from the node schemas of Maya {', '.join(versions)}",
        "(`scripts/mayapy/dump_schema.py`). Do not edit by hand.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "# Type id (the form type of a node's `.mb` group) > type name",
        "TYPE_NAMES: dict[int, str] = {",
        *(f"    0x{type_id:08X}: {name!r}," for type_id, name in sorted(names.items())),
        "}",
        "",
        "# Type name > parent type name",
        "PARENT_TYPES: dict[str, str] = {",
        *(f"    {name!r}: {parent!r}," for name, parent in sorted(parents.items())),
        "}",
        "",
        "# Type name > (short name, long name) of the attributes it declares with",
        "# `usedAsFilename`; subtypes inherit them",
        "FILE_ATTRIBUTES: dict[str, tuple[tuple[str, str], ...]] = {",
        *(
            f"    {name!r}: {tuple(sorted(attrs))!r},"
            for name, attrs in sorted(file_attributes.items())
        ),
        "}",
        "",
        "# Type name > `short long` name pairs of the attributes it declares whose two",
        "# names differ, one string per type because strings import faster than tuples.",
        "# Names that Maya versions give to different attributes are left out",
        "ATTRIBUTE_NAMES: dict[str, str] = {",
        *attribute_names,
        "}",
        "",
        "# Name > type of the nodes every new scene starts with",
        "DEFAULT_NODES: dict[str, str] = {",
        *(
            f"    {name!r}: {node_type!r},"
            for name, node_type in sorted(default_nodes.items())
        ),
        "}",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"wrote {OUT} ({len(names)} types, {len(file_attributes)} with file attributes)"
    )

    spec_lines = [
        '"""Maya attribute specs: kind of value, parent, children, default and flags.',
        "",
        f"Generated by `scripts/gen_node_types.py` from the node schemas of Maya {', '.join(spec_versions)}",
        "(`scripts/mayapy/dump_schema.py`). Do not edit by hand.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "# Type name > JSON list of the attributes it declares, as [short name, kind,",
        "# parent, children, default in internal units, flags], flags being `a`",
        "# array, `k` keyable, `c` channel box and `s` storable. One string per type,",
        "# parsed on first use",
        "ATTRIBUTE_SPECS: dict[str, str] = {",
    ]
    count = 0
    for name, attributes in sorted(specs.items()):
        entries = [attributes[short] for short in sorted(attributes)]
        count += len(entries)
        text = json.dumps(entries, separators=(",", ":"))
        spec_lines.append(f"    {name!r}: {text!r},")
    spec_lines += ["}", ""]
    SPECS_OUT.write_text("\n".join(spec_lines), encoding="utf-8")
    print(f"wrote {SPECS_OUT} ({len(specs)} types, {count} attributes)")


if __name__ == "__main__":
    main(sys.argv[1:])
