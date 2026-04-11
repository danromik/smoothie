"""MCP tool definitions for the sidecar agent."""

import json

from claude_agent_sdk import tool

from . import state
from .blender_proxy import execute_code, query_blender


def _mcp_result(text: str, is_error: bool = False) -> dict:
    """Format a tool result in the MCP CallToolResult format."""
    result = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["is_error"] = True
    return result


def _json_result(data) -> dict:
    """Format a data result as pretty-printed JSON."""
    return _mcp_result(json.dumps(data, indent=2, default=str))


# ─── Scene Exploration Tools ─────────────────────────────

@tool(
    "read_scene",
    "Get a full overview of the current Blender scene: all objects with types, "
    "locations, rotations, scales, materials, animation status, frame range, "
    "FPS, and selection state. Use this first to understand what's in the scene.",
    {"type": "object", "properties": {}, "required": []},
)
async def read_scene(args: dict) -> dict:
    result = await query_blender("/api/scene", method="GET")
    if result.get("text"):
        return _mcp_result(result["text"])
    return _mcp_result(json.dumps(result.get("data", result), indent=2, default=str))


@tool(
    "read_object",
    "Get deep detail on a single object by name: full transforms, materials "
    "with shader settings, modifiers, constraints, parent/children, vertex "
    "count, shape keys.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Exact object name"},
        },
        "required": ["name"],
    },
)
async def read_object(args: dict) -> dict:
    result = await query_blender("/api/object", {"name": args.get("name", "")})
    return _json_result(result.get("data", result))


@tool(
    "read_animation",
    "Get keyframe data for an object: which properties are animated, "
    "keyframe frames and values per channel, action name, frame range.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Exact object name"},
        },
        "required": ["name"],
    },
)
async def read_animation(args: dict) -> dict:
    result = await query_blender("/api/animation", {"name": args.get("name", "")})
    return _json_result(result.get("data", result))


@tool(
    "list_objects",
    "Get a lightweight list of all objects in the scene (names and types). "
    "Optionally filter by type. Faster than read_scene for large scenes.",
    {
        "type": "object",
        "properties": {
            "type_filter": {
                "type": "string",
                "description": "Filter by object type: MESH, CAMERA, LIGHT, ARMATURE, EMPTY, CURVE, etc. Leave empty for all.",
            },
        },
        "required": [],
    },
)
async def list_objects(args: dict) -> dict:
    result = await query_blender("/api/objects", {"type_filter": args.get("type_filter", "")})
    return _json_result(result.get("data", result))


@tool(
    "read_hierarchy",
    "Get the parent-child tree structure for an object. Walks up to the root "
    "parent and returns the full tree. Useful for rigs, multi-part models.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Object name (any object in the hierarchy)"},
        },
        "required": ["name"],
    },
)
async def read_hierarchy(args: dict) -> dict:
    result = await query_blender("/api/hierarchy", {"name": args.get("name", "")})
    return _json_result(result.get("data", result))


@tool(
    "search_objects",
    "Search for objects by name pattern and/or type. Supports wildcards "
    "(e.g. '*leg*', 'Robot*'). Can filter to animated objects only.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Name pattern with wildcards (e.g. '*leg*'). Empty matches all.",
            },
            "type_filter": {
                "type": "string",
                "description": "Filter by type: MESH, CAMERA, LIGHT, etc. Empty for all.",
            },
            "animated_only": {
                "type": "boolean",
                "description": "If true, only return objects with animation data.",
            },
        },
        "required": [],
    },
)
async def search_objects(args: dict) -> dict:
    result = await query_blender("/api/search/objects", {
        "query": args.get("query", ""),
        "type_filter": args.get("type_filter", ""),
        "animated_only": args.get("animated_only", False),
    })
    return _json_result(result.get("data", result))


@tool(
    "search_by_material",
    "Find objects using a specific material. Supports wildcards "
    "(e.g. '*metal*', 'Red*'). Returns object names and slot indices.",
    {
        "type": "object",
        "properties": {
            "material": {
                "type": "string",
                "description": "Material name or pattern with wildcards.",
            },
        },
        "required": ["material"],
    },
)
async def search_by_material(args: dict) -> dict:
    result = await query_blender("/api/search/material", {"material": args.get("material", "")})
    return _json_result(result.get("data", result))


@tool(
    "read_materials",
    "List all materials in the scene with shader settings (base color, "
    "roughness, metallic, textures) and which objects use each material.",
    {"type": "object", "properties": {}, "required": []},
)
async def read_materials(args: dict) -> dict:
    result = await query_blender("/api/materials", method="GET")
    return _json_result(result.get("data", result))


@tool(
    "read_render_settings",
    "Get render settings: engine, resolution, sampling, output format, "
    "world/environment settings.",
    {"type": "object", "properties": {}, "required": []},
)
async def read_render_settings(args: dict) -> dict:
    result = await query_blender("/api/render", method="GET")
    return _json_result(result.get("data", result))


@tool(
    "read_timeline",
    "Get timeline info: frame range, FPS, current frame, markers, "
    "NLA strips per object.",
    {"type": "object", "properties": {}, "required": []},
)
async def read_timeline(args: dict) -> dict:
    result = await query_blender("/api/timeline", method="GET")
    return _json_result(result.get("data", result))


# ─── Camera Perception Tools ──────────────────────────────

@tool(
    "check_camera_visibility",
    "Verify that a camera has a clear line of sight to one or more subject "
    "objects by raycasting. For each sampled frame, casts rays from the "
    "camera to the bound_box corners of every mesh in the subject set (Empty "
    "parents are traversed recursively to their mesh children) and reports "
    "what fraction of sample points are visible, plus the names of any "
    "blocking objects. Useful after positioning a camera to confirm the shot "
    "is not obstructed by nearby geometry — the framing helpers fit extent "
    "but do not check occlusion.",
    {
        "type": "object",
        "properties": {
            "subjects": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Names of subject objects to check. Empty parents are "
                    "resolved to their mesh descendants automatically."
                ),
            },
            "camera": {
                "type": "string",
                "description": (
                    "Optional camera object name. Defaults to the active "
                    "scene camera."
                ),
            },
            "frames": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "Optional list of frame numbers to sample. Defaults to "
                    "the current frame. For animated cameras, pass several "
                    "key frames to check visibility throughout the shot."
                ),
            },
        },
        "required": ["subjects"],
    },
)
async def check_camera_visibility(args: dict) -> dict:
    result = await query_blender("/api/check_camera_visibility", {
        "subjects": args.get("subjects", []),
        "camera": args.get("camera", ""),
        "frames": args.get("frames", []),
    })
    if not result.get("success"):
        return _mcp_result(
            f"check_camera_visibility failed: {result.get('error', 'unknown')}",
            is_error=True,
        )
    return _mcp_result(result.get("content", ""))


# ─── Library File Tools ───────────────────────────────────

@tool(
    "list_library_files",
    "List all library files in the project. Library files contain reusable "
    "Python functions that are automatically available in all code executions.",
    {"type": "object", "properties": {}, "required": []},
)
async def list_library_files(args: dict) -> dict:
    result = await query_blender("/api/library", method="GET")
    return _json_result(result.get("data", []))


@tool(
    "read_library_file",
    "Read a library file by name. Always read before editing to avoid "
    "overwriting recent changes.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "File name (e.g. 'physics.py')"},
        },
        "required": ["name"],
    },
)
async def read_library_file(args: dict) -> dict:
    result = await query_blender("/api/library/read", {"name": args.get("name", "")})
    if not result.get("exists", False):
        return _mcp_result(f"Library file '{args.get('name', '')}' not found.", is_error=True)
    return _mcp_result(result.get("content", ""))


@tool(
    "write_library_file",
    "Create or update a library file. Functions defined in library files are "
    "automatically available in all code executions without import statements. "
    "Keep files focused and well-documented.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "File name (e.g. 'physics.py', 'generators.py')"},
            "content": {"type": "string", "description": "Full Python source code for the file"},
        },
        "required": ["name", "content"],
    },
)
async def write_library_file(args: dict) -> dict:
    result = await query_blender("/api/library/write", {
        "name": args.get("name", ""),
        "content": args.get("content", ""),
    })
    if result.get("success"):
        return _mcp_result(f"Library file '{args.get('name', '')}' saved successfully.")
    return _mcp_result(f"Failed to save library file: {result.get('error', 'unknown')}", is_error=True)


@tool(
    "delete_library_file",
    "Delete a library file from the project.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "File name to delete"},
        },
        "required": ["name"],
    },
)
async def delete_library_file(args: dict) -> dict:
    result = await query_blender("/api/library/delete", {"name": args.get("name", "")})
    if result.get("success"):
        return _mcp_result(f"Library file '{args.get('name', '')}' deleted.")
    return _mcp_result(f"Failed to delete: {result.get('error', 'unknown')}", is_error=True)


# ─── Project Notes Tool ───────────────────────────────────

@tool(
    "read_project_notes",
    "Read the project notes (smoothie.md) for this Blender project. "
    "Contains project goals, scene structure, animation plans, and technical "
    "notes maintained by the user and assistant. Read this before making "
    "significant changes to understand the project context.",
    {"type": "object", "properties": {}, "required": []},
)
async def read_project_notes(args: dict) -> dict:
    result = await query_blender("/api/project-notes", method="GET")
    if not result.get("exists", False):
        return _mcp_result("No project notes exist yet. You can create them with update_project_notes.")
    return _mcp_result(result.get("content", ""))


@tool(
    "update_project_notes",
    "Create or update the project notes (smoothie.md) for this Blender project. "
    "Use this to maintain a concise reference document about the project: scene "
    "structure, object names, animation plans, and technical notes. Keep it under "
    "2000 words. Always read the current notes first before updating to avoid "
    "overwriting recent changes.",
    {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The full markdown content for smoothie.md. Replaces the entire file.",
            },
        },
        "required": ["content"],
    },
)
async def update_project_notes(args: dict) -> dict:
    content = args.get("content", "")
    result = await query_blender("/api/project-notes", {"content": content})
    if result.get("success"):
        # Flag for client reset after current query completes
        # (can't reset mid-query — would deadlock)
        state.conversation.needs_client_reset = True
        return _mcp_result("Project notes updated successfully.")
    return _mcp_result(f"Failed to update project notes: {result.get('error', 'unknown')}", is_error=True)


# ─── Asset Tools ─────────────────────────────────────────

@tool(
    "list_asset_libraries",
    "List the user's configured Blender asset libraries (local paths).",
    {"type": "object", "properties": {}, "required": []},
)
async def list_asset_libraries(args: dict) -> dict:
    result = await query_blender("/api/assets/libraries", method="GET")
    return _json_result(result.get("data", result))


@tool(
    "search_assets",
    "Search for assets by name in the user's local Blender asset libraries. "
    "Returns matching asset names, types, and library paths.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search term (name pattern)"},
            "asset_type": {"type": "string", "description": "Filter by type: OBJECT, MATERIAL, COLLECTION, WORLD. Empty for all."},
        },
        "required": ["query"],
    },
)
async def search_assets(args: dict) -> dict:
    result = await query_blender("/api/assets/search", {
        "query": args.get("query", ""),
        "asset_type": args.get("asset_type", ""),
    })
    return _json_result(result.get("data", result))


@tool(
    "import_asset",
    "Import an asset from a local Blender asset library into the current scene.",
    {
        "type": "object",
        "properties": {
            "filepath": {"type": "string", "description": "Path to the .blend file containing the asset"},
            "asset_name": {"type": "string", "description": "Name of the asset to import"},
            "asset_type": {"type": "string", "description": "Type: OBJECT, MATERIAL, COLLECTION, WORLD"},
            "link": {"type": "boolean", "description": "If true, link instead of append (default: false)"},
        },
        "required": ["filepath", "asset_name", "asset_type"],
    },
)
async def import_asset(args: dict) -> dict:
    result = await query_blender("/api/assets/import", {
        "filepath": args.get("filepath", ""),
        "asset_name": args.get("asset_name", ""),
        "asset_type": args.get("asset_type", ""),
        "link": args.get("link", False),
    })
    if result.get("success"):
        return _mcp_result(f"Asset '{args.get('asset_name', '')}' imported successfully.")
    return _mcp_result(f"Failed to import asset: {result.get('error', 'unknown')}", is_error=True)


@tool(
    "check_blenderkit",
    "Check if the BlenderKit add-on is installed and the user is logged in.",
    {"type": "object", "properties": {}, "required": []},
)
async def check_blenderkit(args: dict) -> dict:
    result = await query_blender("/api/blenderkit/status", method="GET")
    return _json_result(result.get("data", result))


@tool(
    "search_blenderkit",
    "Search the BlenderKit online catalog for assets. Requires BlenderKit "
    "add-on to be installed and user to be logged in.",
    {
        "type": "object",
        "properties": {
            "keywords": {"type": "string", "description": "Search keywords (e.g. 'car', 'tree', 'character')"},
            "asset_type": {"type": "string", "description": "Type: model, material, brush, scene, hdr. Default: model"},
        },
        "required": ["keywords"],
    },
)
async def search_blenderkit(args: dict) -> dict:
    result = await query_blender("/api/blenderkit/search", {
        "keywords": args.get("keywords", ""),
        "asset_type": args.get("asset_type", "model"),
    })
    return _json_result(result.get("data", result))


@tool(
    "import_blenderkit_asset",
    "Download and import a BlenderKit asset into the current scene. "
    "Use search_blenderkit first to find the asset_base_id. The download is "
    "asynchronous — the asset will appear in the scene shortly after.",
    {
        "type": "object",
        "properties": {
            "asset_base_id": {"type": "string", "description": "BlenderKit asset_base_id from search results"},
            "location": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Optional [x, y, z] location to place the asset. Default: origin.",
            },
            "resolution": {
                "type": "string",
                "description": "Texture resolution: 'blend' (original), 'resolution_0_5K', 'resolution_1K', 'resolution_2K', 'resolution_4K'. Default: 'blend'.",
            },
        },
        "required": ["asset_base_id"],
    },
)
async def import_blenderkit_asset(args: dict) -> dict:
    result = await query_blender("/api/blenderkit/import", {
        "asset_base_id": args.get("asset_base_id", ""),
        "location": args.get("location", [0, 0, 0]),
        "resolution": args.get("resolution", "blend"),
    })
    if result.get("success"):
        return _mcp_result(f"BlenderKit asset imported successfully.")
    return _mcp_result(f"Failed to import: {result.get('error', 'unknown')}", is_error=True)


# ─── Code Generation Tool ────────────────────────────────

@tool(
    "generate_blender_code",
    "Generate Python/bpy code to execute in Blender. Use this tool whenever "
    "the user's request requires creating, modifying, or animating objects, "
    "materials, lighting, cameras, or any other aspect of the Blender scene. "
    "The user will be prompted to execute or reject the code. If they execute "
    "it, you will receive the execution result (success + output, or error). "
    "If they reject it, you will receive their reason. On execution failure, "
    "analyze the error and immediately send a corrected version. On rejection "
    "with a reason, address the feedback and send revised code.",
    {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "Complete, self-contained Python code using Blender's bpy module. "
                    "Must run top-to-bottom with no external dependencies beyond "
                    "bpy and allowed standard modules (bmesh, mathutils, math, random, "
                    "colorsys, itertools, functools, collections). "
                    "Do NOT import os, subprocess, sys, shutil, pathlib, socket, http, "
                    "urllib, ctypes, multiprocessing, or any networking/filesystem modules."
                ),
            },
        },
        "required": ["code"],
    },
)
async def generate_blender_code(args: dict) -> dict:
    """Handle code generation tool call.

    If auto-execute is enabled, executes immediately and returns the result.
    Otherwise, blocks until the user clicks Execute or Reject in the UI.
    """
    code = args.get("code", "")

    # Auto-execute path: run immediately without user interaction
    if state.settings.auto_execute:
        result = await execute_code(code)
        if result.get("success", False):
            output = result.get("output", "")
            msg = f"Code auto-executed successfully.{' Output: ' + output if output else ''}"
            return _mcp_result(msg)
        else:
            error = result.get("error", "Unknown error")
            return _mcp_result(f"Code execution failed: {error}", is_error=True)

    # Human-in-the-loop path: wait for user to execute or reject
    action = state.PendingToolAction(code=code)
    state.pending_tool_action = action

    # Block until user acts (execute or reject resolves the event)
    await action.event.wait()

    # Clean up
    state.pending_tool_action = None

    text = action.result or "No result received."
    is_error = "failed" in text.lower() or "rejected" in text.lower()
    return _mcp_result(text, is_error=is_error)
