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
