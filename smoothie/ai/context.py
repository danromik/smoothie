from .templates import SCENE_CONTEXT_TEMPLATE


def gather_scene_context(context) -> dict:
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
            obj_info["keyframe_count"] = sum(
                len(fc.keyframe_points)
                for fc in obj.animation_data.action.fcurves
            )
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


def _fmt_tuple(t):
    return "({})".format(", ".join(f"{v:.2f}" for v in t))
