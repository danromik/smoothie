"""Starlette web application for the sidecar process."""

import asyncio
import json
import logging
import os
import uuid

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

from . import state
from .agent import reset_client, stream_chat
from .blender_proxy import execute_code, get_scene_context, get_status, undo

logger = logging.getLogger("smoothie.sidecar.app")

_FRONTEND_PATH = os.path.join(os.path.dirname(__file__), "frontend.html")


async def homepage(request: Request) -> HTMLResponse:
    """Serve the frontend HTML."""
    try:
        with open(_FRONTEND_PATH, "r", encoding="utf-8") as f:
            html = f.read()
    except FileNotFoundError:
        html = (
            "<!DOCTYPE html><html><body>"
            "<h1>Smoothie Sidecar</h1><p>Frontend coming soon.</p>"
            "</body></html>"
        )
    return HTMLResponse(html)


async def api_messages(request: Request) -> JSONResponse:
    """Return conversation messages as JSON array."""
    messages = [msg.to_dict() for msg in state.conversation.messages]
    return JSONResponse(messages)


async def api_send(request: Request) -> JSONResponse:
    """Receive a user prompt, start streaming AI response."""
    body = await request.json()
    prompt = body.get("prompt", "").strip()

    if not prompt:
        return JSONResponse({"error": "Empty prompt"}, status_code=400)

    if state.conversation.is_streaming:
        return JSONResponse({"error": "Already streaming"}, status_code=409)

    # Add user message to conversation
    user_msg = state.ChatMessage(
        id=state.new_message_id(),
        role="user",
        content=prompt,
    )
    state.conversation.messages.append(user_msg)

    # Create SSE session
    session_id = str(uuid.uuid4())[:12]
    state.sse_queues[session_id] = asyncio.Queue()

    # Start streaming in background
    asyncio.create_task(stream_chat(prompt, session_id))

    logger.info("api_send: session=%s, prompt=%.80s", session_id, prompt)
    return JSONResponse({"session_id": session_id})


async def api_stream(request: Request) -> StreamingResponse:
    """SSE endpoint for streaming AI response."""
    session_id = request.path_params["session_id"]
    sse_queue = state.sse_queues.get(session_id)

    if sse_queue is None:
        return JSONResponse({"error": "Unknown session"}, status_code=404)

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(sse_queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield ": keepalive\n\n"
                    continue

                event_type = event.get("type", "message")
                event_data = json.dumps(event.get("data", {}))
                yield f"event: {event_type}\ndata: {event_data}\n\n"

                if event_type in ("done", "error"):
                    break
        finally:
            # Clean up
            state.sse_queues.pop(session_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def api_execute(request: Request) -> JSONResponse:
    """Execute code in Blender (by code string or message index)."""
    body = await request.json()
    code = body.get("code", "")
    message_index = body.get("message_index")

    if not code and message_index is not None:
        # Look up code by message index
        try:
            idx = int(message_index)
            if 0 <= idx < len(state.conversation.messages):
                msg = state.conversation.messages[idx]
                if msg.has_code:
                    code = msg.code
        except (ValueError, IndexError):
            pass

    if not code:
        return JSONResponse({"error": "No code provided"}, status_code=400)

    result = await execute_code(code)

    # Update code_executed in state if we found the message
    if result.get("success", False):
        if message_index is not None:
            try:
                idx = int(message_index)
                if 0 <= idx < len(state.conversation.messages):
                    state.conversation.messages[idx].code_executed = True
            except (ValueError, IndexError):
                pass
        else:
            # Try to find the message with this code
            for msg in reversed(state.conversation.messages):
                if msg.has_code and msg.code == code:
                    msg.code_executed = True
                    break

    return JSONResponse(result)


async def api_undo(request: Request) -> JSONResponse:
    """Undo last execution in Blender."""
    result = await undo()
    return JSONResponse(result)


async def api_clear(request: Request) -> JSONResponse:
    """Clear conversation and reset agent."""
    state.conversation.messages.clear()
    state.conversation.developer_events.clear()
    state.conversation.is_streaming = False
    state.conversation.active_code_index = -1

    await reset_client()

    # Add welcome message
    welcome = state.ChatMessage(
        id=state.new_message_id(),
        role="assistant",
        content="Chat cleared! How can I help you with your Blender scene?",
    )
    state.conversation.messages.append(welcome)

    logger.info("Conversation cleared")
    return JSONResponse({"success": True})


async def api_developer(request: Request) -> JSONResponse:
    """Return developer events for debugging."""
    return JSONResponse(state.conversation.developer_events)


async def api_scene(request: Request) -> JSONResponse:
    """Proxy scene context from Blender."""
    context_text = await get_scene_context()
    return JSONResponse({"context": context_text})


async def api_settings(request: Request) -> JSONResponse:
    """Get or update settings."""
    if request.method == "GET":
        return JSONResponse({
            "auth_mode": state.settings.auth_mode,
            "has_api_key": bool(state.settings.api_key),
            "model": state.settings.model,
        })

    # POST
    body = await request.json()

    changed = False
    if "auth_mode" in body:
        state.settings.auth_mode = body["auth_mode"]
        changed = True
    if "api_key" in body:
        state.settings.api_key = body["api_key"]
        changed = True
    if "model" in body:
        state.settings.model = body["model"]
        changed = True

    if changed:
        await reset_client()
        logger.info("Settings updated, client reset")

    return JSONResponse({"success": True})


async def api_health(request: Request) -> JSONResponse:
    """Health check — sidecar status + Blender connectivity."""
    blender_status = await get_status()
    return JSONResponse({
        "sidecar": "ok",
        "blender": blender_status,
    })


async def api_shutdown(request: Request) -> JSONResponse:
    """Gracefully shut down the sidecar process."""
    import os
    import signal
    logger.info("Shutdown requested via API")
    # Schedule shutdown after response is sent
    asyncio.get_event_loop().call_later(0.5, os.kill, os.getpid(), signal.SIGTERM)
    return JSONResponse({"success": True, "message": "Shutting down"})


routes = [
    Route("/", homepage, methods=["GET"]),
    Route("/api/messages", api_messages, methods=["GET"]),
    Route("/api/send", api_send, methods=["POST"]),
    Route("/api/stream/{session_id}", api_stream, methods=["GET"]),
    Route("/api/execute", api_execute, methods=["POST"]),
    Route("/api/undo", api_undo, methods=["POST"]),
    Route("/api/clear", api_clear, methods=["POST"]),
    Route("/api/developer", api_developer, methods=["GET"]),
    Route("/api/scene", api_scene, methods=["GET"]),
    Route("/api/settings", api_settings, methods=["GET", "POST"]),
    Route("/api/health", api_health, methods=["GET"]),
    Route("/api/shutdown", api_shutdown, methods=["POST"]),
]

app = Starlette(routes=routes)
