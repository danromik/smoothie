"""HTTP request handlers for the Blender API."""

import json
import logging

from .bridge import MainThreadCommand, command_queue

logger = logging.getLogger("smoothie.blender_api.handlers")

COMMAND_TIMEOUT = 30  # seconds


def handle_request(handler, method):
    """Route a request to the appropriate handler function."""
    path = handler.path.split("?")[0]  # Strip query string

    if method == "GET":
        if path == "/api/scene":
            _handle_command(handler, "get_scene")
        elif path == "/api/status":
            _handle_command(handler, "get_status")
        elif path == "/api/session/load":
            _handle_command(handler, "load_session_id")
        elif path == "/api/materials":
            _handle_command(handler, "read_materials")
        elif path == "/api/render":
            _handle_command(handler, "read_render_settings")
        elif path == "/api/timeline":
            _handle_command(handler, "read_timeline")
        elif path == "/api/project-notes":
            _handle_command(handler, "read_project_notes")
        elif path == "/api/project-name":
            _handle_command(handler, "get_project_name")
        elif path == "/api/library":
            _handle_command(handler, "list_library_files")
        elif path == "/api/assets/libraries":
            _handle_command(handler, "list_asset_libraries")
        elif path == "/api/blenderkit/status":
            _handle_command(handler, "check_blenderkit")
        else:
            _send_json(handler, 404, {"error": "Not found"})

    elif method == "POST":
        body = _read_body(handler)
        if path == "/api/execute":
            _handle_command(handler, "execute_code", body)
        elif path == "/api/undo":
            _handle_command(handler, "undo")
        elif path == "/api/session/save":
            _handle_command(handler, "save_session_id", body)
        elif path == "/api/object":
            _handle_command(handler, "read_object", body)
        elif path == "/api/animation":
            _handle_command(handler, "read_animation", body)
        elif path == "/api/objects":
            _handle_command(handler, "list_objects", body)
        elif path == "/api/hierarchy":
            _handle_command(handler, "read_hierarchy", body)
        elif path == "/api/search/objects":
            _handle_command(handler, "search_objects", body)
        elif path == "/api/search/material":
            _handle_command(handler, "search_by_material", body)
        elif path == "/api/project-notes":
            _handle_command(handler, "write_project_notes", body)
        elif path == "/api/library/read":
            _handle_command(handler, "read_library_file", body)
        elif path == "/api/library/write":
            _handle_command(handler, "write_library_file", body)
        elif path == "/api/library/delete":
            _handle_command(handler, "delete_library_file", body)
        elif path == "/api/assets/search":
            _handle_command(handler, "search_assets", body)
        elif path == "/api/assets/import":
            _handle_command(handler, "import_asset", body)
        elif path == "/api/blenderkit/search":
            _handle_command(handler, "search_blenderkit", body)
        elif path == "/api/blenderkit/import":
            _handle_command(handler, "import_blenderkit_asset", body)
        elif path == "/api/check_camera_visibility":
            _handle_command(handler, "check_camera_visibility", body)
        else:
            _send_json(handler, 404, {"error": "Not found"})


def _handle_command(handler, action, data=None):
    """Put a command on the queue and wait for the main thread to process it."""
    cmd = MainThreadCommand(action=action, data=data or {})
    command_queue.put(cmd)

    if cmd.done_event.wait(timeout=COMMAND_TIMEOUT):
        if cmd.result and cmd.result.get("success"):
            _send_json(handler, 200, cmd.result)
        else:
            error = cmd.result.get("error", "Unknown error") if cmd.result else "Unknown error"
            _send_json(handler, 400, {"error": error, **(cmd.result or {})})
    else:
        _send_json(handler, 504, {"error": "Timeout waiting for Blender main thread"})


def _read_body(handler):
    """Read and parse JSON request body."""
    content_length = int(handler.headers.get("Content-Length", 0))
    if content_length == 0:
        return {}
    try:
        raw = handler.rfile.read(content_length)
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}


def _send_json(handler, status, data):
    """Send a JSON response."""
    body = json.dumps(data, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
