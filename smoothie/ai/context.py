"""Scene context gathering functions for Blender.

These run on Blender's main thread via bridge commands.
"""

import fnmatch
import json

from .templates import SCENE_CONTEXT_TEMPLATE


def _fmt_tuple(t):
    return "({})".format(", ".join(f"{v:.2f}" for v in t))


# ─── read_scene ──────────────────────────────────────────

def gather_scene_context(context) -> dict:
    """Full scene overview."""
    scene = context.scene
    objects = []
    for obj in scene.objects:
        obj_info = {
            "name": obj.name,
            "type": obj.type,
            "location": tuple(obj.location),
            "rotation": tuple(obj.rotation_euler),
            "scale": tuple(obj.scale),
            "has_animation": obj.animation_data is not None,
            "materials": [mat.name for mat in obj.data.materials]
            if hasattr(obj.data, "materials") and obj.data is not None
            else [],
        }
        if obj.animation_data and obj.animation_data.action:
            try:
                obj_info["keyframe_count"] = sum(
                    len(fc.keyframe_points)
                    for fc in obj.animation_data.action.fcurves
                )
            except Exception:
                obj_info["keyframe_count"] = 0
        else:
            obj_info["keyframe_count"] = 0
        objects.append(obj_info)

    return {
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "fps": scene.render.fps,
        "current_frame": scene.frame_current,
        "objects": objects,
        "active_object": context.active_object.name
        if context.active_object
        else None,
        "selected_objects": [obj.name for obj in context.selected_objects]
        if hasattr(context, "selected_objects")
        else [],
    }


def format_context_for_prompt(context_dict: dict) -> str:
    if context_dict["objects"]:
        lines = []
        for obj in context_dict["objects"]:
            parts = [f'  - "{obj["name"]}" ({obj["type"]})']
            parts.append(
                f'    location={_fmt_tuple(obj["location"])}, '
                f'rotation={_fmt_tuple(obj["rotation"])}, '
                f'scale={_fmt_tuple(obj["scale"])}'
            )
            if obj["has_animation"]:
                parts.append(f'    animated ({obj["keyframe_count"]} keyframes)')
            if obj["materials"]:
                parts.append(f'    materials: {", ".join(obj["materials"])}')
            lines.append("\n".join(parts))
        object_list = "\n".join(lines)
    else:
        object_list = "  (empty scene)"

    selected = ", ".join(context_dict["selected_objects"]) or "none"

    return SCENE_CONTEXT_TEMPLATE.format(
        frame_start=context_dict["frame_start"],
        frame_end=context_dict["frame_end"],
        fps=context_dict["fps"],
        current_frame=context_dict["current_frame"],
        object_count=len(context_dict["objects"]),
        object_list=object_list,
        active_object=context_dict["active_object"] or "none",
        selected_objects=selected,
    )


# ─── read_object ─────────────────────────────────────────

def gather_object_detail(obj_name: str) -> dict:
    """Deep detail on a single object."""
    import bpy

    obj = bpy.data.objects.get(obj_name)
    if not obj:
        return {"error": f"Object '{obj_name}' not found"}

    info = {
        "name": obj.name,
        "type": obj.type,
        "location": tuple(obj.location),
        "rotation_euler": tuple(obj.rotation_euler),
        "scale": tuple(obj.scale),
        "dimensions": tuple(obj.dimensions),
        "parent": obj.parent.name if obj.parent else None,
        "children": [c.name for c in obj.children],
        "visible": obj.visible_get(),
        "has_animation": obj.animation_data is not None,
    }

    # Mesh data
    if obj.type == "MESH" and obj.data:
        info["vertex_count"] = len(obj.data.vertices)
        info["face_count"] = len(obj.data.polygons)
        info["edge_count"] = len(obj.data.edges)

    # Materials
    if hasattr(obj.data, "materials") and obj.data is not None:
        mats = []
        for mat in obj.data.materials:
            if mat is None:
                continue
            mat_info = {"name": mat.name}
            if mat.use_nodes and mat.node_tree:
                bsdf = mat.node_tree.nodes.get("Principled BSDF")
                if bsdf:
                    mat_info["base_color"] = list(bsdf.inputs["Base Color"].default_value)
                    mat_info["roughness"] = bsdf.inputs["Roughness"].default_value
                    mat_info["metallic"] = bsdf.inputs["Metallic"].default_value
            mats.append(mat_info)
        info["materials"] = mats

    # Modifiers
    info["modifiers"] = [{"name": m.name, "type": m.type} for m in obj.modifiers]

    # Constraints
    info["constraints"] = [{"name": c.name, "type": c.type} for c in obj.constraints]

    # Shape keys
    if obj.data and hasattr(obj.data, "shape_keys") and obj.data.shape_keys:
        info["shape_keys"] = [kb.name for kb in obj.data.shape_keys.key_blocks]

    return info


# ─── read_animation ──────────────────────────────────────

def gather_animation_data(obj_name: str) -> dict:
    """Keyframe data for an object."""
    import bpy

    obj = bpy.data.objects.get(obj_name)
    if not obj:
        return {"error": f"Object '{obj_name}' not found"}

    if not obj.animation_data or not obj.animation_data.action:
        return {"object": obj_name, "animated": False, "channels": []}

    action = obj.animation_data.action
    channels = []
    try:
        for fc in action.fcurves:
            channel = {
                "data_path": fc.data_path,
                "array_index": fc.array_index,
                "keyframes": [
                    {"frame": int(kp.co[0]), "value": round(kp.co[1], 4)}
                    for kp in fc.keyframe_points
                ],
            }
            channels.append(channel)
    except Exception:
        pass

    return {
        "object": obj_name,
        "animated": True,
        "action_name": action.name,
        "frame_range": [int(action.frame_range[0]), int(action.frame_range[1])],
        "channels": channels,
    }


# ─── list_objects ────────────────────────────────────────

def list_objects(type_filter: str = "") -> list:
    """Lightweight object list (names + types)."""
    import bpy

    result = []
    for obj in bpy.context.scene.objects:
        if type_filter and obj.type != type_filter.upper():
            continue
        result.append({"name": obj.name, "type": obj.type})
    return result


# ─── read_hierarchy ──────────────────────────────────────

def gather_hierarchy(obj_name: str) -> dict:
    """Parent-child tree structure."""
    import bpy

    obj = bpy.data.objects.get(obj_name)
    if not obj:
        return {"error": f"Object '{obj_name}' not found"}

    def _build_tree(o):
        node = {"name": o.name, "type": o.type}
        children = [_build_tree(c) for c in o.children]
        if children:
            node["children"] = children
        return node

    # Walk up to root
    root = obj
    while root.parent:
        root = root.parent

    return _build_tree(root)


# ─── search_objects ──────────────────────────────────────

def search_objects(query: str = "", type_filter: str = "", animated_only: bool = False) -> list:
    """Search objects by name pattern and filters."""
    import bpy

    results = []
    for obj in bpy.context.scene.objects:
        if type_filter and obj.type != type_filter.upper():
            continue
        if animated_only and not (obj.animation_data and obj.animation_data.action):
            continue
        if query and not fnmatch.fnmatch(obj.name.lower(), query.lower()):
            continue
        results.append({
            "name": obj.name,
            "type": obj.type,
            "location": tuple(round(v, 3) for v in obj.location),
        })
    return results


# ─── search_by_material ─────────────────────────────────

def search_by_material(material: str) -> list:
    """Find objects using a material name/pattern."""
    import bpy

    results = []
    for obj in bpy.context.scene.objects:
        if not hasattr(obj.data, "materials") or obj.data is None:
            continue
        for i, mat in enumerate(obj.data.materials):
            if mat and fnmatch.fnmatch(mat.name.lower(), material.lower()):
                results.append({
                    "object": obj.name,
                    "material": mat.name,
                    "slot_index": i,
                })
    return results


# ─── read_materials ──────────────────────────────────────

def gather_all_materials() -> list:
    """All materials with shader settings."""
    import bpy

    materials = []
    for mat in bpy.data.materials:
        info = {"name": mat.name, "use_nodes": mat.use_nodes}
        if mat.use_nodes and mat.node_tree:
            bsdf = mat.node_tree.nodes.get("Principled BSDF")
            if bsdf:
                info["base_color"] = list(bsdf.inputs["Base Color"].default_value)
                info["roughness"] = bsdf.inputs["Roughness"].default_value
                info["metallic"] = bsdf.inputs["Metallic"].default_value
                try:
                    info["ior"] = bsdf.inputs["IOR"].default_value
                except Exception:
                    pass
                try:
                    info["alpha"] = bsdf.inputs["Alpha"].default_value
                except Exception:
                    pass
            # List connected texture nodes
            textures = []
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image:
                    textures.append({"name": node.image.name, "filepath": node.image.filepath})
            if textures:
                info["textures"] = textures
        # List objects using this material
        users = [obj.name for obj in bpy.data.objects
                 if hasattr(obj.data, "materials") and obj.data
                 and mat.name in [m.name for m in obj.data.materials if m]]
        if users:
            info["used_by"] = users
        materials.append(info)
    return materials


# ─── read_render_settings ────────────────────────────────

def gather_render_settings() -> dict:
    """Render engine, resolution, sampling, world settings."""
    import bpy

    scene = bpy.context.scene
    render = scene.render

    info = {
        "engine": render.engine,
        "resolution_x": render.resolution_x,
        "resolution_y": render.resolution_y,
        "resolution_percentage": render.resolution_percentage,
        "fps": render.fps,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "output_path": render.filepath,
        "file_format": render.image_settings.file_format,
    }

    # Sampling (engine-specific)
    if render.engine == "CYCLES":
        info["samples"] = scene.cycles.samples
        info["preview_samples"] = scene.cycles.preview_samples
    elif render.engine == "BLENDER_EEVEE_NEXT":
        try:
            info["samples"] = scene.eevee.taa_render_samples
            info["preview_samples"] = scene.eevee.taa_samples
        except Exception:
            pass

    # World
    world = scene.world
    if world:
        info["world"] = {"name": world.name}
        if world.use_nodes and world.node_tree:
            bg = world.node_tree.nodes.get("Background")
            if bg:
                info["world"]["color"] = list(bg.inputs["Color"].default_value)
                info["world"]["strength"] = bg.inputs["Strength"].default_value

    return info


# ─── read_timeline ───────────────────────────────────────

def gather_timeline() -> dict:
    """Timeline info: frame range, markers, NLA strips."""
    import bpy

    scene = bpy.context.scene
    info = {
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "frame_current": scene.frame_current,
        "fps": scene.render.fps,
    }

    # Markers
    markers = [{"name": m.name, "frame": m.frame} for m in scene.timeline_markers]
    if markers:
        info["markers"] = markers

    # NLA strips (per object)
    nla_objects = []
    for obj in scene.objects:
        if obj.animation_data and obj.animation_data.nla_tracks:
            tracks = []
            for track in obj.animation_data.nla_tracks:
                strips = [{
                    "name": s.name,
                    "action": s.action.name if s.action else None,
                    "frame_start": int(s.frame_start),
                    "frame_end": int(s.frame_end),
                    "mute": s.mute,
                } for s in track.strips]
                tracks.append({"name": track.name, "strips": strips})
            nla_objects.append({"object": obj.name, "tracks": tracks})
    if nla_objects:
        info["nla"] = nla_objects

    return info
