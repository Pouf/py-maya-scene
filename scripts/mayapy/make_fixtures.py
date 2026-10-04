# -*- coding: utf-8 -*-
"""Build the Maya-made fixtures in samples/models/maya<version>/.

Runs under any mayapy (Python 2.7 for Maya 2018, so no f-strings):

    "C:/Program Files/Autodesk/Maya2024/bin/mayapy.exe" scripts/mayapy/make_fixtures.py

Each scene is saved as both .mb and .ma so every binary chunk has a text twin.
References use `$PY_MAYA_SAMPLES` so the files do not embed a machine path.
"""

from __future__ import print_function, unicode_literals

import io
import os
import sys

import maya.standalone

maya.standalone.initialize(name="python")
import maya.cmds as cmds  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VERSION = cmds.about(version=True).split()[0]
OUT = os.path.join(ROOT, "samples", "models", "maya" + VERSION)
if not os.path.isdir(OUT):
    os.makedirs(OUT)
os.environ["PY_MAYA_SAMPLES"] = OUT.replace("\\", "/")
LOG_DIR = os.path.join(ROOT, "build")
if not os.path.isdir(LOG_DIR):
    os.makedirs(LOG_DIR)
log = io.open(
    os.path.join(LOG_DIR, "make_fixtures_%s.log" % VERSION), "w", encoding="utf-8"
)


def save(name):
    for ext, kind in ((".mb", "mayaBinary"), (".ma", "mayaAscii")):
        path = os.path.join(OUT, name + ext)
        cmds.file(rename=path)
        cmds.file(save=True, type=kind, force=True)
        log.write("saved %s\n" % path)


def set_path(node, attr, value):
    cmds.setAttr(node + "." + attr, value, type="string")


# ref_child: the file the other scenes reference
cmds.file(new=True, force=True)
cmds.polyCube(name="refCube")
cmds.addAttr("refCube", longName="extra", attributeType="double")
save("ref_child")

# deps: one node per kind of external dependency
cmds.file(new=True, force=True)
cmds.polySphere(name="mySphere")
cmds.setKeyframe("mySphere", attribute="translateX", time=1, value=0)
cmds.setKeyframe("mySphere", attribute="translateX", time=24, value=10)
set_path(
    cmds.shadingNode("file", asTexture=True, name="diffuseTex"),
    "fileTextureName",
    "P:/projects/demo/textures/diffuse.<UDIM>.exr",
)
set_path(
    cmds.shadingNode("file", asTexture=True, name="unicodeTex"),
    "fileTextureName",
    "P:/projets/d\u00e9mo/textures/\u00e9toile.png",
)
set_path(
    cmds.shadingNode("file", asTexture=True, name="longTex"),
    "fileTextureName",
    "P:/" + "x" * 300 + ".tif",
)
cmds.file("$PY_MAYA_SAMPLES/ref_child.mb", reference=True, namespace="child")
set_path(cmds.createNode("audio", name="myAudio"), "filename", "P:/sound/dialog.wav")
for plugin, node_type, attr, value in [
    ("gpuCache", "gpuCache", "cacheFileName", "P:/cache/anim_gpu.abc"),
    ("AbcImport", "AlembicNode", "abc_File", "P:/cache/anim.abc"),
    ("mtoa", "aiStandIn", "dso", "P:/cache/standin.ass"),
    ("mtoa", "aiImage", "filename", "P:/textures/ai_image.tx"),
]:
    try:
        cmds.loadPlugin(plugin, quiet=True)
    except RuntimeError as e:  # plugin not installed for this Maya version
        log.write("skip %s: %s\n" % (node_type, e))
        continue
    set_path(cmds.createNode(node_type), attr, value)
save("deps")

# names: duplicate short names, namespaces and connections, for renaming
cmds.file(new=True, force=True)
for group in ("grpA", "grpB"):
    cmds.group(empty=True, name=group)
    cube = cmds.polyCube(name="cube")[0]
    cmds.parent(cube, group)
cmds.namespace(add="ns")
loc = cmds.spaceLocator(name="ns:target")[0]
cmds.connectAttr("|grpA|cube.translate", loc + ".translate")
cmds.connectAttr("|grpB|cube.rotate", loc + ".rotate")
cmds.parentConstraint(loc, "grpA")
cmds.sets("|grpA|cube", "|grpB|cube", name="cubeSet")
save("names")

# refs: nested, duplicated and deferred references, plus edits on referenced nodes
cmds.file(new=True, force=True)
cmds.file("$PY_MAYA_SAMPLES/deps.mb", reference=True, namespace="deps")
cmds.file("$PY_MAYA_SAMPLES/ref_child.mb", reference=True, namespace="a")
cmds.file("$PY_MAYA_SAMPLES/ref_child.mb", reference=True, namespace="b")
cmds.file(
    "$PY_MAYA_SAMPLES/ref_child.ma",
    reference=True,
    namespace="late",
    deferReference=True,
)
cmds.setAttr("a:refCube.translateX", 5)
set_path("deps:diffuseTex", "fileTextureName", "P:/edited/in/parent.exr")
# Links between local and referenced nodes, which a rename must follow
local = cmds.spaceLocator(name="localLoc")[0]
cmds.connectAttr("a:refCube.translate", local + ".translate")
cmds.connectAttr(local + ".rotate", "b:refCube.rotate")
cmds.parent(cmds.group(empty=True, name="localGrp"), "a:refCube")
save("refs")

# refedits: one reference edit of each kind on a referenced cube
cmds.file(new=True, force=True)
cmds.file("$PY_MAYA_SAMPLES/ref_child.mb", reference=True, namespace="e")
local_grp = cmds.group(empty=True, name="localGrp")
local = cmds.spaceLocator(name="localLoc")[0]
cmds.setAttr("e:refCube.translateX", 2)
cmds.addAttr("e:refCube", longName="note", dataType="string")
set_path("e:refCube", "note", "P:/note.txt")
cmds.connectAttr("e:refCube.translate", local + ".translate")
cmds.connectAttr(local + ".rotate", "e:refCube.rotate")
cmds.disconnectAttr("e:polyCube1.output", "e:refCubeShape.inMesh")
cmds.parent("e:refCube", local_grp)
cmds.lockNode("e:refCubeShape", lock=True)
shader = cmds.shadingNode("lambert", asShader=True, name="localLambert")
group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="localSG")
cmds.connectAttr(shader + ".outColor", group + ".surfaceShader")
cmds.sets("e:refCubeShape", edit=True, forceElement=group)
cmds.connectAttr("e:refCube.scaleX", "e:refCube.scaleY")
cmds.setAttr("e:refCube.translateZ", lock=True)
cmds.setAttr("e:refCube.translateY", lock=True)
cmds.setAttr("e:refCube.translateY", lock=False)
cmds.deleteAttr("e:refCube.extra")
save("refedits")

log.write("maya %s\nDONE\n" % cmds.about(version=True))
log.close()
maya.standalone.uninitialize()
sys.exit(0)
