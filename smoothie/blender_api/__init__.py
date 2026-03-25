"""Blender API server — simplified HTTP interface for bpy operations."""

import logging
import threading

logger = logging.getLogger("smoothie.blender_api")

_server = None
_server_thread = None
_active_port = None


def start_server(port=8889, max_attempts=3):
    """Start the Blender API server in a background daemon thread."""
    global _server, _server_thread, _active_port

    if _server is not None:
        logger.info("Server already running on port %d", _active_port)
        return _active_port

    from .server import BlenderAPIServer, BlenderAPIRequestHandler

    for attempt in range(max_attempts):
        try_port = port + attempt
        try:
            _server = BlenderAPIServer(("127.0.0.1", try_port), BlenderAPIRequestHandler)
            _active_port = try_port
            _server_thread = threading.Thread(
                target=_server.serve_forever,
                daemon=True,
                name="smoothie-blender-api",
            )
            _server_thread.start()
            logger.info("Blender API server started on http://127.0.0.1:%d", try_port)
            return try_port
        except OSError as e:
            logger.warning("Port %d unavailable: %s", try_port, e)
            _server = None
            continue

    logger.error("Could not start Blender API server on ports %d-%d", port, port + max_attempts - 1)
    return None


def stop_server():
    """Stop the Blender API server."""
    global _server, _server_thread, _active_port

    if _server is not None:
        logger.info("Stopping Blender API server on port %d", _active_port)
        _server.shutdown()
        _server = None
        _server_thread = None
        _active_port = None


def get_port():
    """Return the port the server is running on, or None."""
    return _active_port
