"""HTTP server for the Blender API."""

import logging
import socketserver
from http.server import HTTPServer, BaseHTTPRequestHandler

from .handlers import handle_request

logger = logging.getLogger("smoothie.blender_api.server")


class BlenderAPIServer(socketserver.ThreadingMixIn, HTTPServer):
    """Threaded HTTP server — each request gets its own thread."""
    daemon_threads = True
    allow_reuse_address = True


class BlenderAPIRequestHandler(BaseHTTPRequestHandler):
    """Routes requests to handler functions."""

    def do_GET(self):
        handle_request(self, "GET")

    def do_POST(self):
        handle_request(self, "POST")

    def log_message(self, format, *args):
        logger.debug("%s - %s", self.address_string(), format % args)
