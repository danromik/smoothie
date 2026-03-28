"""Async HTTP client for Blender's internal API."""

import logging

import httpx

from . import state

logger = logging.getLogger("smoothie.sidecar.blender_proxy")


def _base_url() -> str:
    return f"http://127.0.0.1:{state.settings.blender_port}"


async def get_scene_context() -> str:
    """Fetch formatted scene context from Blender."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_base_url()}/api/scene")
            resp.raise_for_status()
            data = resp.json()
            return data.get("context", data.get("text", str(data)))
    except Exception as e:
        logger.warning("Failed to fetch scene context: %s", e)
        return "(Scene context unavailable)"


async def execute_code(code: str) -> dict:
    """Send code to Blender for execution."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{_base_url()}/api/execute",
                json={"code": code},
            )
            return resp.json()
    except Exception as e:
        logger.error("Failed to execute code in Blender: %s", e)
        return {"success": False, "error": str(e)}


async def undo() -> dict:
    """Send undo command to Blender."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{_base_url()}/api/undo")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error("Failed to undo in Blender: %s", e)
        return {"success": False, "error": str(e)}


async def save_session_id(session_id: str) -> dict:
    """Save SDK session ID to Blender's text data block."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_base_url()}/api/session/save",
                json={"session_id": session_id},
            )
            return resp.json()
    except Exception as e:
        logger.warning("Failed to save session ID to Blender: %s", e)
        return {"success": False, "error": str(e)}


async def load_session_id() -> str:
    """Load SDK session ID from Blender's text data block."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_base_url()}/api/session/load")
            resp.raise_for_status()
            data = resp.json()
            return data.get("session_id", "")
    except Exception as e:
        logger.warning("Failed to load session ID from Blender: %s", e)
        return ""


async def query_blender(endpoint: str, data: dict | None = None, method: str = "POST") -> dict:
    """Generic Blender API query. Returns the JSON response."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if method == "GET":
                resp = await client.get(f"{_base_url()}{endpoint}")
            else:
                resp = await client.post(f"{_base_url()}{endpoint}", json=data or {})
            return resp.json()
    except Exception as e:
        logger.warning("Blender query %s failed: %s", endpoint, e)
        return {"success": False, "error": str(e)}


async def get_status() -> dict:
    """Check Blender API status."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{_base_url()}/api/status")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.debug("Blender status check failed: %s", e)
        return {"status": "unavailable", "error": str(e)}
