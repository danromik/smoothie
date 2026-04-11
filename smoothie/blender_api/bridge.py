"""Bridge between the Blender API server (background threads) and Blender's main thread.

All bpy operations must happen on the main thread. HTTP handlers place commands
on the command_queue, and a bpy.app.timers callback processes them.
"""

import logging
import queue
import threading
from dataclasses import dataclass, field

logger = logging.getLogger("smoothie.blender_api.bridge")

# Global command queue — HTTP handlers put, main thread timer gets
command_queue = queue.Queue()


@dataclass
class MainThreadCommand:
    action: str
    data: dict = field(default_factory=dict)
    result: dict | None = None
    done_event: threading.Event = field(default_factory=threading.Event)


def bridge_timer_callback():
    """Called by bpy.app.timers on the main thread at ~20Hz.

    Processes commands from API handlers.
    Returns 0.05 to keep the timer running.
    """
    while True:
        try:
            cmd = command_queue.get_nowait()
        except queue.Empty:
            break

        try:
            _process_command(cmd)
        except Exception as e:
            logger.error("Error processing command %s: %s", cmd.action, e, exc_info=True)
            cmd.result = {"success": False, "error": str(e)}
            cmd.done_event.set()

    return 0.05  # Run again in 50ms


def _process_command(cmd):
    """Process a single command on the main thread."""
    import bpy
    import json
    from ..executor.runner import execute_generated_code, undo_last_execution, reset_namespace, invalidate_library
    from ..ai import context as ctx

    if cmd.action == "execute_code":
        code = cmd.data.get("code", "").strip()
        if not code:
            cmd.result = {"success": False, "error": "No code provided"}
            cmd.done_event.set()
            return

        result = execute_generated_code(code)
        cmd.result = {
            "success": result.success,
            "output": result.output,
            "error": result.error or "",
        }
        if result.error_type:
            cmd.result["error_type"] = result.error_type
        cmd.done_event.set()

    elif cmd.action == "undo":
        undo_last_execution()
        cmd.result = {"success": True}
        cmd.done_event.set()

    elif cmd.action == "get_scene":
        data = ctx.gather_scene_context(bpy.context)
        formatted = ctx.format_context_for_prompt(data)
        cmd.result = {"success": True, "text": formatted, "data": data}
        cmd.done_event.set()

    elif cmd.action == "read_object":
        data = ctx.gather_object_detail(cmd.data.get("name", ""))
        cmd.result = {"success": "error" not in data, "data": data}
        cmd.done_event.set()

    elif cmd.action == "read_animation":
        data = ctx.gather_animation_data(cmd.data.get("name", ""))
        cmd.result = {"success": "error" not in data, "data": data}
        cmd.done_event.set()

    elif cmd.action == "list_objects":
        data = ctx.list_objects(cmd.data.get("type_filter", ""))
        cmd.result = {"success": True, "data": data}
        cmd.done_event.set()

    elif cmd.action == "read_hierarchy":
        data = ctx.gather_hierarchy(cmd.data.get("name", ""))
        cmd.result = {"success": "error" not in data, "data": data}
        cmd.done_event.set()

    elif cmd.action == "search_objects":
        data = ctx.search_objects(
            cmd.data.get("query", ""),
            cmd.data.get("type_filter", ""),
            cmd.data.get("animated_only", False),
        )
        cmd.result = {"success": True, "data": data}
        cmd.done_event.set()

    elif cmd.action == "search_by_material":
        data = ctx.search_by_material(cmd.data.get("material", ""))
        cmd.result = {"success": True, "data": data}
        cmd.done_event.set()

    elif cmd.action == "read_materials":
        data = ctx.gather_all_materials()
        cmd.result = {"success": True, "data": data}
        cmd.done_event.set()

    elif cmd.action == "read_render_settings":
        data = ctx.gather_render_settings()
        cmd.result = {"success": True, "data": data}
        cmd.done_event.set()

    elif cmd.action == "read_timeline":
        data = ctx.gather_timeline()
        cmd.result = {"success": True, "data": data}
        cmd.done_event.set()

    elif cmd.action == "save_session_id":
        session_id = cmd.data.get("session_id", "")
        text_name = "smoothie_session"
        if text_name in bpy.data.texts:
            bpy.data.texts[text_name].clear()
            bpy.data.texts[text_name].write(session_id)
        else:
            txt = bpy.data.texts.new(text_name)
            txt.write(session_id)
        cmd.result = {"success": True}
        cmd.done_event.set()

    elif cmd.action == "load_session_id":
        text_name = "smoothie_session"
        if text_name in bpy.data.texts:
            session_id = bpy.data.texts[text_name].as_string().strip()
            cmd.result = {"success": True, "session_id": session_id}
        else:
            cmd.result = {"success": True, "session_id": ""}
        cmd.done_event.set()

    elif cmd.action == "read_project_notes":
        text_name = "smoothie.md"
        if text_name in bpy.data.texts:
            content = bpy.data.texts[text_name].as_string()
            cmd.result = {"success": True, "content": content, "exists": True}
        else:
            cmd.result = {"success": True, "content": "", "exists": False}
        cmd.done_event.set()

    elif cmd.action == "write_project_notes":
        content = cmd.data.get("content", "")
        text_name = "smoothie.md"
        if text_name in bpy.data.texts:
            bpy.data.texts[text_name].clear()
            bpy.data.texts[text_name].write(content)
        else:
            txt = bpy.data.texts.new(text_name)
            txt.write(content)
        cmd.result = {"success": True}
        cmd.done_event.set()

    elif cmd.action == "list_asset_libraries":
        libs = []
        for lib in bpy.context.preferences.filepaths.asset_libraries:
            libs.append({"name": lib.name, "path": lib.path})
        cmd.result = {"success": True, "data": libs}
        cmd.done_event.set()

    elif cmd.action == "search_assets":
        import os
        query = cmd.data.get("query", "").lower()
        asset_type = cmd.data.get("asset_type", "").upper()
        results = []
        for lib in bpy.context.preferences.filepaths.asset_libraries:
            lib_path = lib.path
            if not os.path.isdir(lib_path):
                continue
            for root, dirs, files in os.walk(lib_path):
                for f in files:
                    if not f.endswith(".blend"):
                        continue
                    blend_path = os.path.join(root, f)
                    try:
                        with bpy.data.libraries.load(blend_path, assets_only=True) as (data_from, _):
                            for attr in ("objects", "materials", "collections", "worlds"):
                                if asset_type and attr.upper().rstrip("S") != asset_type:
                                    continue
                                for name in getattr(data_from, attr, []):
                                    if query in name.lower():
                                        results.append({
                                            "name": name,
                                            "type": attr.rstrip("s").upper(),
                                            "library": lib.name,
                                            "filepath": blend_path,
                                        })
                    except Exception:
                        continue
                if len(results) >= 50:
                    break
        cmd.result = {"success": True, "data": results}
        cmd.done_event.set()

    elif cmd.action == "import_asset":
        filepath = cmd.data.get("filepath", "")
        asset_name = cmd.data.get("asset_name", "")
        asset_type = cmd.data.get("asset_type", "").upper()
        link = cmd.data.get("link", False)
        type_map = {"OBJECT": "objects", "MATERIAL": "materials", "COLLECTION": "collections", "WORLD": "worlds"}
        attr = type_map.get(asset_type, "objects")
        try:
            with bpy.data.libraries.load(filepath, link=link) as (data_from, data_to):
                if asset_name in getattr(data_from, attr, []):
                    setattr(data_to, attr, [asset_name])
            # Link imported objects to the scene
            if asset_type == "OBJECT":
                for obj in data_to.objects:
                    if obj is not None:
                        bpy.context.collection.objects.link(obj)
            elif asset_type == "COLLECTION":
                for coll in data_to.collections:
                    if coll is not None:
                        bpy.context.scene.collection.children.link(coll)
            cmd.result = {"success": True}
        except Exception as e:
            cmd.result = {"success": False, "error": str(e)}
        cmd.done_event.set()

    elif cmd.action == "check_blenderkit":
        # Blender 5.x extensions use bl_ext.*.blenderkit naming
        bk_addon_name = None
        for name in bpy.context.preferences.addons.keys():
            if "blenderkit" in name.lower():
                bk_addon_name = name
                break
        installed = bk_addon_name is not None
        data = {"installed": installed, "addon_name": bk_addon_name or "", "logged_in": False}
        if installed:
            try:
                import blenderkit
                data["version"] = getattr(blenderkit, "__version__", "unknown")
                prefs = bpy.context.preferences.addons[bk_addon_name].preferences
                data["logged_in"] = bool(getattr(prefs, "api_key", ""))
            except Exception:
                # Try Blender 5.x extension import path
                try:
                    import importlib
                    bk = importlib.import_module(bk_addon_name)
                    data["version"] = getattr(bk, "__version__", "unknown")
                    prefs = bpy.context.preferences.addons[bk_addon_name].preferences
                    data["logged_in"] = bool(getattr(prefs, "api_key", ""))
                except Exception:
                    pass
        cmd.result = {"success": True, "data": data}
        cmd.done_event.set()

    elif cmd.action == "search_blenderkit":
        try:
            # Find BlenderKit addon name (supports both legacy and 5.x extension format)
            bk_addon_name = None
            for name in bpy.context.preferences.addons.keys():
                if "blenderkit" in name.lower():
                    bk_addon_name = name
                    break
            if not bk_addon_name:
                raise ImportError("BlenderKit add-on not found")

            import requests
            keywords = cmd.data.get("keywords", "")
            asset_type = cmd.data.get("asset_type", "model")
            prefs = bpy.context.preferences.addons[bk_addon_name].preferences
            api_key = getattr(prefs, "api_key", "")
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            url = f"https://www.blenderkit.com/api/v1/search/?query={keywords}&asset_type_str={asset_type}&page_size=20"
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            results = []
            for item in data.get("results", [])[:20]:
                results.append({
                    "asset_base_id": item.get("assetBaseId", ""),
                    "id": item.get("id", ""),
                    "name": item.get("name", ""),
                    "description": item.get("description", "")[:100],
                    "author": item.get("author", {}).get("fullName", ""),
                    "is_free": item.get("isFree", False),
                })
            cmd.result = {"success": True, "data": results}
        except ImportError:
            cmd.result = {"success": False, "error": "BlenderKit add-on not installed"}
        except Exception as e:
            cmd.result = {"success": False, "error": str(e)}
        cmd.done_event.set()

    elif cmd.action == "import_blenderkit_asset":
        try:
            import requests
            import importlib

            # Find BlenderKit addon and get API key
            bk_addon_name = None
            for name in bpy.context.preferences.addons.keys():
                if "blenderkit" in name.lower():
                    bk_addon_name = name
                    break
            if not bk_addon_name:
                raise ImportError("BlenderKit add-on not found")

            prefs = bpy.context.preferences.addons[bk_addon_name].preferences
            api_key = getattr(prefs, "api_key", "")

            asset_base_id = cmd.data.get("asset_base_id", cmd.data.get("asset_id", ""))
            location = cmd.data.get("location", [0, 0, 0])
            resolution = cmd.data.get("resolution", "blend")

            # Fetch full asset data from BlenderKit API using asset_base_id
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            resp = requests.get(
                f"https://www.blenderkit.com/api/v1/search/?query=asset_base_id:{asset_base_id}&page_size=1",
                headers=headers, timeout=15,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if not results:
                # Fallback: try as version ID via direct endpoint
                resp2 = requests.get(
                    f"https://www.blenderkit.com/api/v1/assets/{asset_base_id}/",
                    headers=headers, timeout=15,
                )
                if resp2.status_code == 200:
                    results = [resp2.json()]
                else:
                    cmd.result = {"success": False, "error": f"Asset {asset_base_id} not found on BlenderKit"}
                    cmd.done_event.set()
                    return

            asset_data = results[0]

            # Use BlenderKit's download module (handles download + append)
            try:
                from blenderkit import download as bk_download
            except ImportError:
                bk_download = importlib.import_module(f"{bk_addon_name}.download")

            # BlenderKit's start_download(asset_data, **kwargs) expects
            # model_location and model_rotation as keyword arguments
            bk_download.start_download(
                asset_data,
                model_location=location,
                model_rotation=(0, 0, 0),
                resolution=resolution,
            )
            cmd.result = {"success": True, "message": "Download started. The asset will appear in the scene shortly."}
        except ImportError:
            cmd.result = {"success": False, "error": "BlenderKit add-on not installed"}
        except Exception as e:
            import traceback as tb
            cmd.result = {"success": False, "error": str(e), "traceback": tb.format_exc()}
        cmd.done_event.set()

    elif cmd.action == "list_library_files":
        prefix = "smoothie_lib/"
        files = []
        for name in bpy.data.texts.keys():
            if name.startswith(prefix):
                short_name = name[len(prefix):]
                content = bpy.data.texts[name].as_string()
                files.append({"name": short_name, "size": len(content)})
        cmd.result = {"success": True, "data": files}
        cmd.done_event.set()

    elif cmd.action == "read_library_file":
        name = "smoothie_lib/" + cmd.data.get("name", "")
        if name in bpy.data.texts:
            content = bpy.data.texts[name].as_string()
            cmd.result = {"success": True, "content": content, "exists": True}
        else:
            cmd.result = {"success": True, "content": "", "exists": False}
        cmd.done_event.set()

    elif cmd.action == "write_library_file":
        name = "smoothie_lib/" + cmd.data.get("name", "")
        content = cmd.data.get("content", "")
        if name in bpy.data.texts:
            bpy.data.texts[name].clear()
            bpy.data.texts[name].write(content)
        else:
            txt = bpy.data.texts.new(name)
            txt.write(content)
        invalidate_library(name)
        cmd.result = {"success": True}
        cmd.done_event.set()

    elif cmd.action == "delete_library_file":
        name = "smoothie_lib/" + cmd.data.get("name", "")
        if name in bpy.data.texts:
            bpy.data.texts.remove(bpy.data.texts[name])
            invalidate_library(name)
            cmd.result = {"success": True}
        else:
            cmd.result = {"success": False, "error": f"File '{cmd.data.get('name', '')}' not found"}
        cmd.done_event.set()

    elif cmd.action == "reset_namespace":
        reset_namespace()
        cmd.result = {"success": True}
        cmd.done_event.set()

    elif cmd.action == "check_camera_visibility":
        from mathutils import Vector

        camera_name = cmd.data.get("camera", "") or ""
        subject_names = cmd.data.get("subjects", []) or []
        frames = cmd.data.get("frames", []) or []

        scene = bpy.context.scene

        if camera_name:
            camera = bpy.data.objects.get(camera_name)
            if camera is None or camera.type != "CAMERA":
                cmd.result = {"success": False, "error": f"Camera '{camera_name}' not found or not a camera"}
                cmd.done_event.set()
                return
        else:
            camera = scene.camera
            if camera is None:
                cmd.result = {"success": False, "error": "No active scene camera"}
                cmd.done_event.set()
                return

        # Resolve subjects + descendants
        subjects = {}
        missing = []
        for name in subject_names:
            obj = bpy.data.objects.get(name)
            if obj is None:
                missing.append(name)
                continue
            stack = [obj]
            while stack:
                o = stack.pop()
                if id(o) in subjects:
                    continue
                subjects[id(o)] = o
                for ch in o.children:
                    stack.append(ch)
        if missing:
            cmd.result = {"success": False, "error": f"Subjects not found: {', '.join(missing)}"}
            cmd.done_event.set()
            return
        if not subjects:
            cmd.result = {"success": False, "error": "No subjects specified"}
            cmd.done_event.set()
            return

        subject_name_set = {o.name for o in subjects.values()}

        if not frames:
            frames = [scene.frame_current]

        per_frame = []
        for frame in frames:
            try:
                scene.frame_set(int(frame))
                bpy.context.view_layer.update()
            except Exception as e:
                per_frame.append({"frame": frame, "error": f"frame_set failed: {e}"})
                continue

            depsgraph = bpy.context.evaluated_depsgraph_get()
            cam_loc = camera.matrix_world.translation

            # Sample points: each subject mesh's 8 bound_box corners
            sample_points = []
            for obj in subjects.values():
                if obj.type == "MESH" and obj.data and len(obj.data.vertices) > 0:
                    mw = obj.matrix_world
                    for c in obj.bound_box:
                        sample_points.append(mw @ Vector(c))

            if not sample_points:
                per_frame.append({
                    "frame": frame,
                    "visible_fraction": 0.0,
                    "error": "no mesh bounds (subjects have no mesh geometry to check)",
                })
                continue

            total = len(sample_points)
            visible = 0
            blockers = {}
            for pt in sample_points:
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
                elif hit_obj and hit_obj.name in subject_name_set:
                    visible += 1  # self-hit — subject is visible
                else:
                    bname = hit_obj.name if hit_obj else "<unknown>"
                    blockers[bname] = blockers.get(bname, 0) + 1

            per_frame.append({
                "frame": frame,
                "visible_fraction": visible / total,
                "sample_count": total,
                "visible_count": visible,
                "occluded_by": dict(sorted(blockers.items(), key=lambda x: -x[1])),
            })

        # Human-readable report
        lines = [f"Camera visibility check: {camera.name} -> [{', '.join(subject_names)}]"]
        for r in per_frame:
            frame = r["frame"]
            if "error" in r:
                lines.append(f"  frame {frame}: ERROR — {r['error']}")
                continue
            vf = r["visible_fraction"]
            vc = r["visible_count"]
            tc = r["sample_count"]
            lines.append(f"  frame {frame}: {vf * 100:.0f}% visible ({vc}/{tc} sample points)")
            if r["occluded_by"]:
                blockers_str = ", ".join(f"{n} ({c})" for n, c in r["occluded_by"].items())
                lines.append(f"    blocked by: {blockers_str}")

        cmd.result = {
            "success": True,
            "content": "\n".join(lines),
            "data": per_frame,
        }
        cmd.done_event.set()

    elif cmd.action == "get_project_name":
        filepath = bpy.data.filepath
        if filepath:
            import os
            name = os.path.splitext(os.path.basename(filepath))[0]
            filename = os.path.basename(filepath)
            try:
                stat = os.stat(filepath)
                file_size = stat.st_size
                modified_time = stat.st_mtime
            except OSError:
                file_size = 0
                modified_time = 0
        else:
            name = "Untitled"
            filename = ""
            file_size = 0
            modified_time = 0
        cmd.result = {
            "success": True,
            "name": name,
            "filename": filename,
            "file_size": file_size,
            "modified_time": modified_time,
        }
        cmd.done_event.set()

    elif cmd.action == "get_status":
        cmd.result = {"success": True}
        cmd.done_event.set()

    else:
        cmd.result = {"success": False, "error": f"Unknown action: {cmd.action}"}
        cmd.done_event.set()
