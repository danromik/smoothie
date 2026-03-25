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
        else:
            _send_json(handler, 404, {"error": "Not found"})

    elif method == "POST":
        body = _read_body(handler)
        if path == "/api/execute":
            _handle_command(handler, "execute_code", body)
        elif path == "/api/undo":
            _handle_command(handler, "undo")
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
