"""Camera framing helpers.

Use these whenever the user asks to frame, contain, capture, or keep objects
"in shot". The default padding leaves a generous safety margin so subjects
never sit flush against the frame edge — "in frame" should mean the subject
fits inside ~80% of the camera view, not 100%.

Main entry points:
    fit_camera_to_objects(objects, padding=0.2, camera=None)
        Solve the camera's position so the objects fit with margin.
        Preserves the camera's current rotation.

    aim_and_fit_camera(objects, padding=0.2, camera=None)
        Aim the camera at the objects' centroid, then fit.
        One-call convenience for the common "put X in shot" request.
"""


def _framing_corners(objects):
    """Return a list of world-space corner Vectors for the given objects.

    Traverses each input's descendants recursively and collects the bound_box
    corners (8 per mesh) of every mesh found. This means passing an Empty
    parent (e.g. an imported rigged asset) Just Works: the children's mesh
    corners are used, not just the empty's origin.

    Meshes contribute their eight bound_box corners transformed by
    matrix_world — the 8 vertices of the tight oriented bounding box in world
    space. If no mesh is found anywhere in the traversal, the inputs' own
    origin points are returned instead; this will produce a zero-extent point
    cloud that `fit_camera_to_objects` rejects with a clear error.
    """
    import bpy
    from mathutils import Vector

    # Walk the full tree of inputs + descendants.
    visited = set()
    all_objects = []
    stack = list(objects)
    while stack:
        obj = stack.pop()
        if obj is None or id(obj) in visited:
            continue
        visited.add(id(obj))
        all_objects.append(obj)
        for child in obj.children:
            stack.append(child)

    corners = []
    has_mesh = False
    for obj in all_objects:
        if obj.type == "MESH" and obj.data and len(obj.data.vertices) > 0:
            has_mesh = True
            mw = obj.matrix_world
            for c in obj.bound_box:
                corners.append(mw @ Vector(c))

    if not has_mesh:
        # Degenerate fallback — fit_camera_to_objects will reject this.
        for obj in all_objects:
            corners.append(obj.matrix_world @ Vector((0.0, 0.0, 0.0)))

    return corners


def _centroid(corners):
    from mathutils import Vector
    n = len(corners)
    return Vector((
        sum(c.x for c in corners) / n,
        sum(c.y for c in corners) / n,
        sum(c.z for c in corners) / n,
    ))


def _resolve_framing_inputs(objects, camera):
    import bpy

    if isinstance(objects, bpy.types.Object):
        objects_list = [objects]
    else:
        objects_list = list(objects)
    if not objects_list:
        raise ValueError("at least one object is required")

    if camera is None:
        camera = bpy.context.scene.camera
    if camera is None or camera.type != "CAMERA":
        raise ValueError(
            "no valid camera — pass camera= explicitly or set scene.camera"
        )
    return objects_list, camera


def fit_camera_to_objects(objects, padding=0.2, camera=None):
    """Position a camera so the given objects fit inside the frame with margin.

    Args:
        objects: a Blender object or an iterable of objects to frame.
        padding: fraction of the frame reserved as safety margin on each
            side. 0.2 (default) means subjects occupy at most ~80% of the
            frame. Must be in [0.0, 0.95).
        camera: camera object to move. Defaults to bpy.context.scene.camera.

    Returns:
        The camera object whose location was updated.

    The camera's rotation is preserved — only its location changes. The
    solver moves the camera along its current forward axis until the
    targets' tight oriented bounding corners (scaled outward around their
    centroid by 1/(1 - padding)) fit in view. Set up any tracking
    constraints or manual aim *before* calling this.
    """
    import bpy

    if not (0.0 <= padding < 0.95):
        raise ValueError(f"padding must be in [0.0, 0.95), got {padding}")

    objects_list, camera = _resolve_framing_inputs(objects, camera)

    # Pass the tight (world-space) OBB corners directly to the fit solver.
    # Do NOT build an intermediate AABB — an AABB of rotated OBB corners is
    # strictly larger than the OBB and would cause the solver to back the
    # camera off too far, leaving the visible subject smaller than padding
    # calls for.
    corners = _framing_corners(objects_list)
    centroid = _centroid(corners)

    # Reject degenerate input. If all corners collapse to (approximately) a
    # single point, the fit solver has no extent to work with and will
    # silently place the camera at some arbitrary (often wrong) spot —
    # including inside nearby geometry. This most commonly happens when the
    # caller passes an Empty whose children contain the actual meshes; the
    # traversal in _framing_corners handles that, but we still catch the
    # "no mesh anywhere" case here.
    diag = max(
        max(c.x for c in corners) - min(c.x for c in corners),
        max(c.y for c in corners) - min(c.y for c in corners),
        max(c.z for c in corners) - min(c.z for c in corners),
    )
    if diag < 0.01:
        raise ValueError(
            "fit_camera_to_objects: subject has no usable extent (all "
            "sample points collapse to a single location). If you passed "
            "an Empty, make sure it has mesh descendants; otherwise pass "
            "the mesh objects directly."
        )

    # Scale each corner outward from the centroid by 1/(1 - padding).
    # The fit solver then tightly fits the scaled cloud, leaving the
    # unscaled subject at (1 - padding) of the limiting axis of the frame.
    scale = 1.0 / (1.0 - padding)
    flat = []
    for c in corners:
        flat.extend((
            centroid.x + (c.x - centroid.x) * scale,
            centroid.y + (c.y - centroid.y) * scale,
            centroid.z + (c.z - centroid.z) * scale,
        ))

    depsgraph = bpy.context.evaluated_depsgraph_get()
    co, _unused = camera.camera_fit_coords(depsgraph, flat)
    camera.location = co

    return camera


def aim_and_fit_camera(objects, padding=0.2, camera=None):
    """Aim the camera at the objects' centroid, then fit them in frame.

    Convenience wrapper combining a point-at-target rotation with
    fit_camera_to_objects. Use this for the common "put X in shot" ask
    where you don't need special framing (rule of thirds, over-the-shoulder,
    etc.) — just "see the subject cleanly".

    Args match fit_camera_to_objects.
    """
    import bpy
    from mathutils import Vector

    objects_list, camera = _resolve_framing_inputs(objects, camera)

    corners = _framing_corners(objects_list)
    centroid = _centroid(corners)

    direction = centroid - camera.location
    if direction.length < 1e-6:
        # Camera sits on the subject; nudge it back along world -Y so the
        # solver has a sensible forward axis.
        camera.location = centroid + Vector((0.0, -5.0, 0.0))
        direction = centroid - camera.location

    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    return fit_camera_to_objects(objects_list, padding=padding, camera=camera)
