#!/usr/bin/env python3
"""Integration test for the camera framing helpers (builtin_libs/framing.py).

Talks to a running Blender instance via Smoothie's HTTP API on port 8889.
Builds a walking-robot scene, then exercises fit_camera_to_objects and
aim_and_fit_camera at several frames and padding values, verifying that
all bounding-box corners land inside the camera frame with the expected
safety margin.

Run via test_watcher.py, or directly:
    python3 tests/scripts/test_script.py

Requires Blender to be running with the Smoothie add-on enabled.
"""

import json
import os
import sys
import urllib.error
import urllib.request

BLENDER_HOST = "127.0.0.1"
BLENDER_PORTS = [8889, 8890, 8891]

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))
_FRAMING_SOURCE = os.path.join(
    _PROJECT_ROOT, "smoothie", "executor", "builtin_libs", "framing.py"
)


def post(path, data, timeout=120):
    body = json.dumps(data).encode("utf-8")
    last_err = None
    for port in BLENDER_PORTS:
        url = f"http://{BLENDER_HOST}:{port}{path}"
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            # Server responded with 4xx/5xx — this is a real response, not a
            # connection failure. Blender returns 400 with a JSON error body
            # when generated code raises an exception; read and return it so
            # the caller can surface the traceback.
            try:
                return json.loads(e.read())
            except (json.JSONDecodeError, ValueError):
                return {"success": False, "error": f"HTTP {e.code} (non-JSON body)"}
        except (urllib.error.URLError, ConnectionRefusedError) as e:
            last_err = e
            continue
    raise RuntimeError(
        f"Could not reach Blender API on any of {BLENDER_PORTS}: {last_err}"
    )


def install_framing_library():
    """Install framing.py as a user library file (smoothie_lib/framing.py).

    Workaround for the fact that Blender caches runner.py at add-on load
    time, so the new executor/builtin_libs/ mechanism added in this branch
    isn't active in any Blender session that was started before the change.
    We bypass that by writing framing.py directly into bpy.data.texts via
    the /api/library/write endpoint — the existing _load_library_files
    (which IS in the cached runner) picks it up on the next /api/execute
    call and exposes the framing functions in the persistent namespace.

    Once the user reloads the Smoothie add-on (or restarts Blender) the
    builtin_libs loader will kick in automatically and this step becomes
    a no-op that tests the fallback path.
    """
    with open(_FRAMING_SOURCE, "r", encoding="utf-8") as f:
        content = f.read()
    result = post("/api/library/write", {"name": "framing.py", "content": content})
    if not result.get("success"):
        raise RuntimeError(f"/api/library/write failed: {result}")
    print(
        f"Installed framing.py ({len(content)} bytes) as "
        f"smoothie_lib/framing.py via /api/library/write"
    )


def execute_in_blender(code, label, timeout=120):
    print(f"\n=== {label} ===")
    result = post("/api/execute", {"code": code}, timeout=timeout)
    if not result.get("success"):
        print("FAIL: Blender-side execution error")
        print(f"  error_type: {result.get('error_type')}")
        err = result.get("error") or ""
        for line in err.rstrip().split("\n"):
            print(f"  {line}")
        return False
    output = result.get("output", "")
    if output:
        print(output.rstrip())
    return True


# ---------------------------------------------------------------------------
# Phase 1: build the scene
# ---------------------------------------------------------------------------

BUILD_ROBOT = r'''
import bpy
import math
from mathutils import Vector

SCENE_NAME = "SmoothieTest_Framing"
OBJ_NAMES = [
    "SmoothieTest_Rig",
    "SmoothieTest_Body", "SmoothieTest_Head",
    "SmoothieTest_LegL", "SmoothieTest_LegR",
    "SmoothieTest_ArmL", "SmoothieTest_ArmR",
    "SmoothieTest_Camera",
]

# Fresh test scene so we don't clobber the user's work. Before removing any
# existing test scene, switch the window to a *different* scene so we're not
# removing the active one (which Blender handles poorly).
if SCENE_NAME in bpy.data.scenes:
    others = [s for s in bpy.data.scenes if s.name != SCENE_NAME]
    if others:
        bpy.context.window.scene = others[0]
    bpy.data.scenes.remove(bpy.data.scenes[SCENE_NAME])

# Scrub leftover data blocks from prior test runs. If we don't do this,
# Blender auto-renames our new objects to *.001 / *.002 and downstream
# scene["SmoothieTest_Body"] lookups fail. All names are SmoothieTest_-
# prefixed so this cannot clobber unrelated user data.
for name in OBJ_NAMES:
    if name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
    mesh_name = name + "_mesh"
    if mesh_name in bpy.data.meshes:
        bpy.data.meshes.remove(bpy.data.meshes[mesh_name])
if "SmoothieTest_Camera" in bpy.data.cameras:
    bpy.data.cameras.remove(bpy.data.cameras["SmoothieTest_Camera"])

scene = bpy.data.scenes.new(SCENE_NAME)
bpy.context.window.scene = scene


def make_cube_mesh(name, scale):
    sx, sy, sz = scale
    verts = [
        (-sx / 2, -sy / 2, -sz / 2),
        ( sx / 2, -sy / 2, -sz / 2),
        ( sx / 2,  sy / 2, -sz / 2),
        (-sx / 2,  sy / 2, -sz / 2),
        (-sx / 2, -sy / 2,  sz / 2),
        ( sx / 2, -sy / 2,  sz / 2),
        ( sx / 2,  sy / 2,  sz / 2),
        (-sx / 2,  sy / 2,  sz / 2),
    ]
    faces = [
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 4, 0),
    ]
    mesh = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def add_part(name, scale, location, parent):
    mesh = make_cube_mesh(name, scale)
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    obj.parent = parent
    scene.collection.objects.link(obj)
    return obj


rig = bpy.data.objects.new("SmoothieTest_Rig", None)
scene.collection.objects.link(rig)

# Roughly humanoid proportions, ~3m tall
body = add_part("SmoothieTest_Body",  (1.2, 0.6, 1.8), (0.0,  0.0, 1.8), rig)
head = add_part("SmoothieTest_Head",  (0.8, 0.8, 0.8), (0.0,  0.0, 3.1), rig)
legL = add_part("SmoothieTest_LegL",  (0.3, 0.3, 1.8), (-0.35, 0.0, 0.9), rig)
legR = add_part("SmoothieTest_LegR",  (0.3, 0.3, 1.8), ( 0.35, 0.0, 0.9), rig)
armL = add_part("SmoothieTest_ArmL",  (0.3, 0.3, 1.6), (-0.95, 0.0, 1.8), rig)
armR = add_part("SmoothieTest_ArmR",  (0.3, 0.3, 1.6), ( 0.95, 0.0, 1.8), rig)

# Walk a circle: 12 keys around a 6m radius over 120 frames, facing motion.
scene.frame_start = 1
scene.frame_end = 120
RADIUS = 6.0
NUM_KEYS = 13
for i in range(NUM_KEYS):
    t = i / (NUM_KEYS - 1)
    frame = 1 + int(t * (scene.frame_end - scene.frame_start))
    angle = t * 2 * math.pi
    rig.location = (RADIUS * math.cos(angle), RADIUS * math.sin(angle), 0.0)
    rig.rotation_euler = (0.0, 0.0, angle + math.pi / 2)
    rig.keyframe_insert(data_path="location", frame=frame)
    rig.keyframe_insert(data_path="rotation_euler", frame=frame)

# Camera — initial position doesn't matter, aim_and_fit_camera handles it.
cam_data = bpy.data.cameras.new("SmoothieTest_Camera")
camera = bpy.data.objects.new("SmoothieTest_Camera", cam_data)
camera.location = (15.0, -15.0, 10.0)
scene.collection.objects.link(camera)
scene.camera = camera

bpy.context.view_layer.update()
print(f"Built scene '{SCENE_NAME}': 6 robot parts, rig={rig.name}, camera={camera.name}")
print(f"Frame range: {scene.frame_start}-{scene.frame_end}")
print(f"Walking radius: {RADIUS}m")
'''


# ---------------------------------------------------------------------------
# Phase 2: verify framing at multiple frames + padding sweep
# ---------------------------------------------------------------------------

VERIFY_FRAMING = r'''
import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

scene = bpy.data.scenes["SmoothieTest_Framing"]
bpy.context.window.scene = scene
camera = scene.objects["SmoothieTest_Camera"]
rig = scene.objects["SmoothieTest_Rig"]
PART_NAMES = ["SmoothieTest_Body", "SmoothieTest_Head", "SmoothieTest_LegL", "SmoothieTest_LegR", "SmoothieTest_ArmL", "SmoothieTest_ArmR"]
parts = [scene.objects[n] for n in PART_NAMES]


def occupancy(parts, camera, scene):
    corners = []
    for obj in parts:
        mw = obj.matrix_world
        for c in obj.bound_box:
            corners.append(mw @ Vector(c))
    projs = [world_to_camera_view(scene, camera, c) for c in corners]
    return {
        "x_min": min(p.x for p in projs),
        "x_max": max(p.x for p in projs),
        "y_min": min(p.y for p in projs),
        "y_max": max(p.y for p in projs),
        "z_min": min(p.z for p in projs),
    }


def check_framing(label, occ, padding, pos_tol=0.03, span_tol=0.05):
    """All corners must be in view. Max span should match 1 - padding."""
    lo = -pos_tol
    hi = 1.0 + pos_tol
    all_in_view = (
        occ["x_min"] >= lo and occ["x_max"] <= hi and
        occ["y_min"] >= lo and occ["y_max"] <= hi and
        occ["z_min"] > 0
    )
    x_span = occ["x_max"] - occ["x_min"]
    y_span = occ["y_max"] - occ["y_min"]
    max_span = max(x_span, y_span)
    expected = 1.0 - padding
    span_ok = abs(max_span - expected) <= span_tol

    status = "PASS" if (all_in_view and span_ok) else "FAIL"
    print(
        f"  [{status}] {label}: "
        f"x=[{occ['x_min']:+.2f},{occ['x_max']:+.2f}] "
        f"y=[{occ['y_min']:+.2f},{occ['y_max']:+.2f}] "
        f"max_span={max_span:.2f} (want ~{expected:.2f}) "
        f"z_min={occ['z_min']:.1f}"
    )
    return all_in_view and span_ok


results = []

# --- aim_and_fit_camera at 4 frames around the walk cycle ---
print("--- aim_and_fit_camera, padding=0.2 at frames 1, 30, 60, 90, 120 ---")
for frame in (1, 30, 60, 90, 120):
    scene.frame_set(frame)
    bpy.context.view_layer.update()
    aim_and_fit_camera(parts, padding=0.2)
    bpy.context.view_layer.update()
    results.append(check_framing(f"frame {frame:3d}", occupancy(parts, camera, scene), 0.2))

# --- fit_camera_to_objects (no aim) with manually-aimed camera ---
print("--- fit_camera_to_objects, pre-aimed camera, padding=0.2 at frame 45 ---")
scene.frame_set(45)
bpy.context.view_layer.update()
# Pre-aim: point camera at rig center
rig_center = rig.matrix_world @ Vector((0.0, 0.0, 1.5))
direction = rig_center - camera.location
camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
bpy.context.view_layer.update()
fit_camera_to_objects(parts, padding=0.2)
bpy.context.view_layer.update()
results.append(check_framing("frame  45", occupancy(parts, camera, scene), 0.2))

# --- padding sweep: span should shrink monotonically as padding rises ---
print("--- padding sweep at frame 1 ---")
scene.frame_set(1)
bpy.context.view_layer.update()
spans = []
for p in (0.0, 0.1, 0.2, 0.3, 0.5):
    aim_and_fit_camera(parts, padding=p)
    bpy.context.view_layer.update()
    occ = occupancy(parts, camera, scene)
    max_span = max(occ["x_max"] - occ["x_min"], occ["y_max"] - occ["y_min"])
    spans.append((p, max_span))
    print(f"  padding={p:.1f}: max_span={max_span:.3f} (expected ~{1 - p:.2f})")

# Each span should be <= previous span (monotonic decrease, allow tiny fuzz)
monotonic = all(
    spans[i + 1][1] <= spans[i][1] + 0.02 for i in range(len(spans) - 1)
)
# And each should match 1 - padding within tolerance
all_expected = all(abs(s - (1 - p)) <= 0.05 for p, s in spans)
results.append(monotonic)
results.append(all_expected)
print(f"  monotonic decrease: {'PASS' if monotonic else 'FAIL'}")
print(f"  all spans match (1 - padding) +/- 0.05: {'PASS' if all_expected else 'FAIL'}")

passed = sum(1 for r in results if r)
total = len(results)
print()
print(f"=== framing checks: {passed}/{total} passed ===")
'''


# ---------------------------------------------------------------------------
# Phase 3: edge cases (argument validation)
# ---------------------------------------------------------------------------

EDGE_CASES = r'''
import bpy
scene = bpy.data.scenes["SmoothieTest_Framing"]
bpy.context.window.scene = scene
body = scene.objects["SmoothieTest_Body"]

results = []

def expect_raises(fn, label):
    try:
        fn()
        print(f"  [FAIL] {label}: should have raised")
        return False
    except (ValueError, TypeError) as e:
        print(f"  [PASS] {label}: raised {type(e).__name__}: {e}")
        return True

def expect_ok(fn, label):
    try:
        fn()
        print(f"  [PASS] {label}")
        return True
    except Exception as e:
        print(f"  [FAIL] {label}: {type(e).__name__}: {e}")
        return False

print("--- argument handling ---")
results.append(expect_ok(
    lambda: aim_and_fit_camera(body, padding=0.2),
    "single object (not list) accepted",
))
results.append(expect_ok(
    lambda: aim_and_fit_camera([body], padding=0.2),
    "single-element list accepted",
))
results.append(expect_raises(
    lambda: aim_and_fit_camera(body, padding=0.95),
    "padding=0.95 rejected",
))
results.append(expect_raises(
    lambda: aim_and_fit_camera(body, padding=-0.1),
    "padding=-0.1 rejected",
))
results.append(expect_raises(
    lambda: aim_and_fit_camera([], padding=0.2),
    "empty list rejected",
))
results.append(expect_ok(
    lambda: aim_and_fit_camera(body, padding=0.0),
    "padding=0.0 accepted (tight fit)",
))

# Explicit camera argument
cam = scene.objects["SmoothieTest_Camera"]
results.append(expect_ok(
    lambda: fit_camera_to_objects(body, padding=0.2, camera=cam),
    "explicit camera= argument accepted",
))

passed = sum(1 for r in results if r)
total = len(results)
print()
print(f"=== edge-case checks: {passed}/{total} passed ===")
'''


# ---------------------------------------------------------------------------
# Phase 4: render a short movie so framing can be visually inspected.
# Movie path is passed in from the host side via __RENDER_PATH__ replacement
# so the Blender-side path matches the watcher's absolute filesystem view.
# Kept short and low-res so rendering fits inside Blender's 30s command timeout.
# ---------------------------------------------------------------------------

EMPTY_PARENT_TEST = r'''
import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

scene = bpy.data.scenes["SmoothieTest_Framing"]
bpy.context.window.scene = scene
camera = scene.objects["SmoothieTest_Camera"]

# Clean up any residue from prior runs
for name in (
    "SmoothieTest_EmptyParent", "SmoothieTest_ChildCube1",
    "SmoothieTest_ChildCube2", "SmoothieTest_ChildlessEmpty",
):
    if name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
for name in ("SmoothieTest_ChildCube1_mesh", "SmoothieTest_ChildCube2_mesh"):
    if name in bpy.data.meshes:
        bpy.data.meshes.remove(bpy.data.meshes[name])


def _make_cube_mesh(name, scale):
    sx, sy, sz = scale
    verts = [
        (-sx / 2, -sy / 2, -sz / 2),
        ( sx / 2, -sy / 2, -sz / 2),
        ( sx / 2,  sy / 2, -sz / 2),
        (-sx / 2,  sy / 2, -sz / 2),
        (-sx / 2, -sy / 2,  sz / 2),
        ( sx / 2, -sy / 2,  sz / 2),
        ( sx / 2,  sy / 2,  sz / 2),
        (-sx / 2,  sy / 2,  sz / 2),
    ]
    faces = [
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 4, 0),
    ]
    mesh = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


# Build an empty parent with two mesh children, placed far from the robot
# so they don't interfere with later phases.
empty_parent = bpy.data.objects.new("SmoothieTest_EmptyParent", None)
empty_parent.location = (20.0, 20.0, 2.0)
scene.collection.objects.link(empty_parent)

child1 = bpy.data.objects.new(
    "SmoothieTest_ChildCube1", _make_cube_mesh("SmoothieTest_ChildCube1", (1.0, 1.0, 1.0))
)
child1.location = (-1.2, 0.0, 0.0)
child1.parent = empty_parent
scene.collection.objects.link(child1)

child2 = bpy.data.objects.new(
    "SmoothieTest_ChildCube2", _make_cube_mesh("SmoothieTest_ChildCube2", (1.0, 1.0, 1.0))
)
child2.location = (1.2, 0.0, 0.0)
child2.parent = empty_parent
scene.collection.objects.link(child2)

bpy.context.view_layer.update()

results = []

# --- Test 1: aim_and_fit_camera with only the empty (descendants must be traversed) ---
print("--- aim_and_fit_camera(empty_parent) — recursive descendant traversal ---")
try:
    aim_and_fit_camera(empty_parent, padding=0.2, camera=camera)
    bpy.context.view_layer.update()
    corners = []
    for ch in (child1, child2):
        mw = ch.matrix_world
        for c in ch.bound_box:
            corners.append(mw @ Vector(c))
    projs = [world_to_camera_view(scene, camera, c) for c in corners]
    x_min = min(p.x for p in projs); x_max = max(p.x for p in projs)
    y_min = min(p.y for p in projs); y_max = max(p.y for p in projs)
    z_min = min(p.z for p in projs)
    in_view = (
        -0.03 <= x_min and x_max <= 1.03 and
        -0.03 <= y_min and y_max <= 1.03 and
        z_min > 0
    )
    max_span = max(x_max - x_min, y_max - y_min)
    span_ok = abs(max_span - 0.8) <= 0.06
    status = "PASS" if (in_view and span_ok) else "FAIL"
    print(
        f"  [{status}] x=[{x_min:+.2f},{x_max:+.2f}] y=[{y_min:+.2f},{y_max:+.2f}] "
        f"max_span={max_span:.2f} (want ~0.80)"
    )
    results.append(in_view and span_ok)
except Exception as e:
    print(f"  [FAIL] raised {type(e).__name__}: {e}")
    results.append(False)

# --- Test 2: fit_camera_to_objects with a childless empty must raise ValueError ---
print("--- fit_camera_to_objects(childless_empty) — must raise ValueError ---")
childless = bpy.data.objects.new("SmoothieTest_ChildlessEmpty", None)
childless.location = (30.0, 30.0, 5.0)
scene.collection.objects.link(childless)
bpy.context.view_layer.update()
try:
    fit_camera_to_objects(childless, padding=0.2, camera=camera)
    print("  [FAIL] expected ValueError, got no exception")
    results.append(False)
except ValueError as e:
    print(f"  [PASS] raised ValueError: {e}")
    results.append(True)
except Exception as e:
    print(f"  [FAIL] raised {type(e).__name__} (expected ValueError): {e}")
    results.append(False)

passed = sum(1 for r in results if r)
total = len(results)
print()
print(f"=== empty-parent checks: {passed}/{total} passed ===")
'''


VISIBILITY_TEST = r'''
import bpy
from mathutils import Vector

scene = bpy.data.scenes["SmoothieTest_Framing"]
bpy.context.window.scene = scene

# Clean up from prior runs
for name in (
    "SmoothieTest_VisTarget", "SmoothieTest_VisObstacle", "SmoothieTest_VisCam",
):
    if name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
for name in ("SmoothieTest_VisTarget_mesh", "SmoothieTest_VisObstacle_mesh"):
    if name in bpy.data.meshes:
        bpy.data.meshes.remove(bpy.data.meshes[name])
if "SmoothieTest_VisCam" in bpy.data.cameras:
    bpy.data.cameras.remove(bpy.data.cameras["SmoothieTest_VisCam"])


def _make_cube(name, scale, location):
    sx, sy, sz = scale
    verts = [
        (-sx / 2, -sy / 2, -sz / 2),
        ( sx / 2, -sy / 2, -sz / 2),
        ( sx / 2,  sy / 2, -sz / 2),
        (-sx / 2,  sy / 2, -sz / 2),
        (-sx / 2, -sy / 2,  sz / 2),
        ( sx / 2, -sy / 2,  sz / 2),
        ( sx / 2,  sy / 2,  sz / 2),
        (-sx / 2,  sy / 2,  sz / 2),
    ]
    faces = [
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 4, 0),
    ]
    mesh = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    scene.collection.objects.link(obj)
    return obj


# Build a minimal occlusion scenario. Offset from the robot so geometry
# doesn't interfere with earlier/later phases.
BASE = Vector((50.0, 50.0, 0.0))

# Target: 1m cube at BASE
target = _make_cube("SmoothieTest_VisTarget", (1.0, 1.0, 1.0), BASE)

# Obstacle: flat wall directly between any -Y-side camera and the target
obstacle = _make_cube(
    "SmoothieTest_VisObstacle", (4.0, 0.3, 4.0), BASE + Vector((0.0, -5.0, 0.0))
)

# Dedicated camera for this test
cam_data = bpy.data.cameras.new("SmoothieTest_VisCam")
cam = bpy.data.objects.new("SmoothieTest_VisCam", cam_data)
scene.collection.objects.link(cam)

bpy.context.view_layer.update()


def check_visibility_inline(camera, subject):
    """Mirror of the bridge command's raycast algorithm, run inline here so
    the test validates the *logic* without depending on a reloaded addon."""
    scene = bpy.context.scene
    depsgraph = bpy.context.evaluated_depsgraph_get()
    cam_loc = camera.matrix_world.translation
    mw = subject.matrix_world
    corners = [mw @ Vector(c) for c in subject.bound_box]
    total = len(corners)
    visible = 0
    blockers = {}
    for pt in corners:
        delta = pt - cam_loc
        distance = delta.length
        if distance < 1e-6:
            visible += 1
            continue
        direction = delta / distance
        hit, loc, normal, idx, hit_obj, mat = scene.ray_cast(
            depsgraph, cam_loc, direction, distance=distance + 0.001
        )
        if not hit:
            visible += 1
        elif hit_obj and hit_obj.name == subject.name:
            visible += 1
        else:
            bname = hit_obj.name if hit_obj else "<unknown>"
            blockers[bname] = blockers.get(bname, 0) + 1
    return visible / total, blockers


results = []

# --- Scenario 1: camera directly behind the obstacle ---
cam.location = BASE + Vector((0.0, -10.0, 0.0))
# Point camera at target (look along +Y)
direction = (BASE - cam.location).normalized()
cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
bpy.context.view_layer.update()

vf, blockers = check_visibility_inline(cam, target)
blk_str = ", ".join(f"{n}({c})" for n, c in blockers.items()) or "none"
print(f"--- camera behind obstacle: vf={vf:.2f} blockers=[{blk_str}] ---")
occluded_ok = vf < 0.3 and "SmoothieTest_VisObstacle" in blockers
status = "PASS" if occluded_ok else "FAIL"
print(f"  [{status}] expected occluded (vf<0.30, obstacle in blockers)")
results.append(occluded_ok)

# --- Scenario 2: camera to the side with clear line of sight ---
cam.location = BASE + Vector((8.0, -3.0, 0.0))
direction = (BASE - cam.location).normalized()
cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
bpy.context.view_layer.update()

vf, blockers = check_visibility_inline(cam, target)
blk_str = ", ".join(f"{n}({c})" for n, c in blockers.items()) or "none"
print(f"--- camera to the side: vf={vf:.2f} blockers=[{blk_str}] ---")
clear_ok = vf >= 0.9 and not blockers
status = "PASS" if clear_ok else "FAIL"
print(f"  [{status}] expected clear (vf>=0.90, no blockers)")
results.append(clear_ok)

# --- Scenario 3: camera above the obstacle, looking down at target ---
cam.location = BASE + Vector((0.0, -10.0, 8.0))
direction = (BASE - cam.location).normalized()
cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
bpy.context.view_layer.update()

vf, blockers = check_visibility_inline(cam, target)
blk_str = ", ".join(f"{n}({c})" for n, c in blockers.items()) or "none"
print(f"--- camera above obstacle: vf={vf:.2f} blockers=[{blk_str}] ---")
clear_above_ok = vf >= 0.9
status = "PASS" if clear_above_ok else "FAIL"
print(f"  [{status}] expected clear from above (vf>=0.90)")
results.append(clear_above_ok)

passed = sum(1 for r in results if r)
total = len(results)
print()
print(f"=== visibility checks: {passed}/{total} passed ===")
'''


RENDER_MOVIE = r'''
import bpy

scene = bpy.data.scenes["SmoothieTest_Framing"]
bpy.context.window.scene = scene
camera = scene.objects["SmoothieTest_Camera"]
PART_NAMES = [
    "SmoothieTest_Body", "SmoothieTest_Head",
    "SmoothieTest_LegL", "SmoothieTest_LegR",
    "SmoothieTest_ArmL", "SmoothieTest_ArmR",
]
parts = [scene.objects[n] for n in PART_NAMES]

# Clear any camera animation from previous phases so keyframes start clean.
if camera.animation_data:
    camera.animation_data_clear()

# Add a sun light (purge and recreate so repeated runs stay clean).
if "SmoothieTest_Sun" in bpy.data.objects:
    bpy.data.objects.remove(bpy.data.objects["SmoothieTest_Sun"], do_unlink=True)
if "SmoothieTest_Sun" in bpy.data.lights:
    bpy.data.lights.remove(bpy.data.lights["SmoothieTest_Sun"])
sun_data = bpy.data.lights.new("SmoothieTest_Sun", type="SUN")
sun_data.energy = 3.0
sun = bpy.data.objects.new("SmoothieTest_Sun", sun_data)
sun.rotation_euler = (0.9, 0.2, 0.3)
scene.collection.objects.link(sun)

# Ensure a world with a visible background colour.
if "SmoothieTest_World" in bpy.data.worlds:
    scene.world = bpy.data.worlds["SmoothieTest_World"]
else:
    scene.world = bpy.data.worlds.new("SmoothieTest_World")
scene.world.use_nodes = True
bg = scene.world.node_tree.nodes.get("Background")
if bg is not None:
    bg.inputs[0].default_value = (0.08, 0.10, 0.15, 1.0)
    bg.inputs[1].default_value = 1.0

# Simple ground plane so the robot doesn't float in a void.
if "SmoothieTest_Ground" in bpy.data.objects:
    bpy.data.objects.remove(bpy.data.objects["SmoothieTest_Ground"], do_unlink=True)
if "SmoothieTest_Ground_mesh" in bpy.data.meshes:
    bpy.data.meshes.remove(bpy.data.meshes["SmoothieTest_Ground_mesh"])
ground_mesh = bpy.data.meshes.new("SmoothieTest_Ground_mesh")
GSZ = 20.0
ground_mesh.from_pydata(
    [(-GSZ, -GSZ, 0.0), (GSZ, -GSZ, 0.0), (GSZ, GSZ, 0.0), (-GSZ, GSZ, 0.0)],
    [],
    [(0, 1, 2, 3)],
)
ground_mesh.update()
ground = bpy.data.objects.new("SmoothieTest_Ground", ground_mesh)
scene.collection.objects.link(ground)

# Trim to 60 frames so the render fits inside the 30s command timeout.
scene.frame_end = 60

# Key the camera using aim_and_fit_camera every KEY_STEP frames.
# Blender interpolates smoothly between keys, giving a clean tracking cam.
KEY_STEP = 5
for frame in range(scene.frame_start, scene.frame_end + 1, KEY_STEP):
    scene.frame_set(frame)
    bpy.context.view_layer.update()
    aim_and_fit_camera(parts, padding=0.2)
    camera.keyframe_insert(data_path="location", frame=frame)
    camera.keyframe_insert(data_path="rotation_euler", frame=frame)

# Always key the exact end frame so the camera doesn't drift past the walk.
scene.frame_set(scene.frame_end)
bpy.context.view_layer.update()
aim_and_fit_camera(parts, padding=0.2)
camera.keyframe_insert(data_path="location", frame=scene.frame_end)
camera.keyframe_insert(data_path="rotation_euler", frame=scene.frame_end)

# Render settings — small, fast Eevee.
try:
    scene.render.engine = "BLENDER_EEVEE_NEXT"
except TypeError:
    scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = 480
scene.render.resolution_y = 270
scene.render.resolution_percentage = 100
scene.render.fps = 24

# Low samples for speed (test cares about framing, not final-quality shading).
if hasattr(scene, "eevee"):
    for attr in ("taa_render_samples", "samples"):
        if hasattr(scene.eevee, attr):
            try:
                setattr(scene.eevee, attr, 8)
            except (AttributeError, TypeError):
                pass

scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGB"
scene.render.image_settings.compression = 15
scene.render.use_file_extension = True
scene.render.filepath = "__RENDER_PATH__"

print(
    f"Rendering frames {scene.frame_start}-{scene.frame_end} "
    f"at {scene.render.resolution_x}x{scene.render.resolution_y} "
    f"PNG to {scene.render.filepath}####.png"
)
bpy.ops.render.render(animation=True)
print("Render complete")
'''


def verify_png_sequence(base_path):
    """Find the rendered PNG sequence (frame_NNNN.png naming by Blender)."""
    import glob
    pattern = base_path + "*.png"
    frames = sorted(glob.glob(pattern))
    if not frames:
        raise RuntimeError(f"no PNGs found matching {pattern}")
    total_bytes = sum(os.path.getsize(f) for f in frames)
    if total_bytes == 0:
        raise RuntimeError(f"rendered PNGs are empty at {pattern}")
    return frames, total_bytes


def try_assemble_movie(frames, output_path, fps=24):
    """If ffmpeg is on PATH, assemble the PNG sequence into an MP4.

    Returns the MP4 path on success, None if ffmpeg is unavailable or fails.
    """
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None

    # Blender names frames like "frame_0001.png" starting from frame_start.
    # Use the first frame's numeric prefix to build an -i pattern.
    first = frames[0]
    parent = os.path.dirname(first)
    base_name = os.path.basename(first)
    # Strip trailing "NNNN.png" → keep prefix, then rebuild as "prefix%04d.png"
    if len(base_name) < 9 or not base_name[-8:-4].isdigit():
        return None  # unrecognised naming
    prefix = base_name[:-8]
    start_num = int(base_name[-8:-4])
    pattern = os.path.join(parent, f"{prefix}%04d.png")

    cmd = [
        ffmpeg,
        "-y",  # overwrite
        "-framerate", str(fps),
        "-start_number", str(start_num),
        "-i", pattern,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "23",
        "-preset", "fast",
        output_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            print(f"  ffmpeg failed (exit {proc.returncode}):")
            for line in (proc.stderr or "").rstrip().split("\n")[-6:]:
                print(f"    {line}")
            return None
    except Exception as e:
        print(f"  ffmpeg invocation error: {e}")
        return None

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return None
    return output_path


def main():
    print("Smoothie framing helpers — integration test")
    print("=" * 60)

    try:
        install_framing_library()
    except Exception as e:
        print(f"\nFAIL: could not install framing library: {e}")
        return 1

    ok = True
    if not execute_in_blender(BUILD_ROBOT, "BUILD SCENE"):
        print("\nAborting: build phase failed")
        return 1
    ok &= execute_in_blender(VERIFY_FRAMING, "VERIFY FRAMING + PADDING SWEEP")
    ok &= execute_in_blender(EDGE_CASES, "EDGE CASES")
    ok &= execute_in_blender(EMPTY_PARENT_TEST, "EMPTY-PARENT TRAVERSAL")
    ok &= execute_in_blender(VISIBILITY_TEST, "VISIBILITY RAYCAST")

    # Render a short PNG sequence so the user can eyeball framing quality.
    # This Blender build lacks FFMPEG support, so we render stills and then
    # optionally assemble them into an MP4 via host-side ffmpeg if available.
    movie_dir = os.path.join(_PROJECT_ROOT, "tests", "results", "framing_movie")
    os.makedirs(movie_dir, exist_ok=True)
    # Purge any previous render artifacts so verification picks up fresh output.
    for stale in _glob_safe(os.path.join(movie_dir, "frame_*.png")):
        try:
            os.remove(stale)
        except OSError:
            pass
    for stale in _glob_safe(os.path.join(movie_dir, "framing_test.mp4")):
        try:
            os.remove(stale)
        except OSError:
            pass

    render_base = os.path.join(movie_dir, "frame_")
    render_code = RENDER_MOVIE.replace("__RENDER_PATH__", render_base)
    render_ok = execute_in_blender(render_code, "RENDER MOVIE", timeout=300)

    if render_ok:
        try:
            frames, total_bytes = verify_png_sequence(render_base)
            print(
                f"\nPNG sequence: {len(frames)} frames, "
                f"{total_bytes:,} bytes total"
            )
            print(f"  directory: {movie_dir}")
            print(f"  first:     {os.path.basename(frames[0])}")
            print(f"  last:      {os.path.basename(frames[-1])}")

            mp4_path = os.path.join(movie_dir, "framing_test.mp4")
            assembled = try_assemble_movie(frames, mp4_path, fps=24)
            if assembled:
                size = os.path.getsize(assembled)
                print(f"\nMovie assembled: {assembled} ({size:,} bytes)")
                print("  Open with any video player (QuickTime, VLC, etc.)")
                # The PNG sequence is redundant once the MP4 is built; remove
                # it so the results directory doesn't accumulate 60 frames per
                # test run.
                removed = 0
                for f in frames:
                    try:
                        os.remove(f)
                        removed += 1
                    except OSError:
                        pass
                if removed:
                    print(f"  Removed {removed} PNG frames (MP4 supersedes them)")
            else:
                print("\nffmpeg not available or assembly failed.")
                print("To view the sequence:")
                print("  - Open QuickTime Player → File → Open Image Sequence")
                print(f"  - Or drag {os.path.basename(frames[0])} onto QuickTime")
        except Exception as e:
            print(f"\nRender verification failed: {e}")
            render_ok = False
    ok &= render_ok

    print()
    if ok:
        print("All phases executed without Blender-side errors.")
        print("Inspect output above for PASS/FAIL on individual checks.")
        return 0
    else:
        print("Some phases failed — see output above.")
        return 1


def _glob_safe(pattern):
    import glob
    return glob.glob(pattern)


if __name__ == "__main__":
    sys.exit(main())
