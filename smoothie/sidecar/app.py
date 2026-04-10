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
from .agent import _extract_tool_detail, reset_client, stream_chat
from .blender_proxy import execute_code, get_scene_context, get_status, load_session_id, query_blender, undo

logger = logging.getLogger("smoothie.sidecar.app")

_FRONTEND_PATH = os.path.join(os.path.dirname(__file__), "frontend.html")

# The MCP-prefixed tool name used by the SDK
_CODE_TOOL = "mcp__smoothie__generate_blender_code"


def _restore_messages_from_sdk(session_id: str) -> int:
    """Restore conversation messages from an SDK session for UI display.

    Parses SessionMessage objects into ChatMessage objects, tracking
    tool execution/rejection status from tool_result blocks.
    Returns the number of messages restored.
    """
    try:
        from claude_agent_sdk import get_session_messages
    except ImportError:
        logger.warning("get_session_messages not available in SDK")
        return 0

    try:
        messages = get_session_messages(session_id)
    except Exception as e:
        logger.warning("Failed to get session messages for %s: %s", session_id, e)
        return 0

    state.conversation.messages.clear()
    count = 0
    last_code_msg = None  # Track most recent code block for status from tool_result

    for sm in messages:
        msg_dict = sm.message if hasattr(sm, 'message') else {}
        role = msg_dict.get("role", sm.type if hasattr(sm, 'type') else "")
        content_blocks = msg_dict.get("content", [])

        # Track usage from the last message that has it
        usage = msg_dict.get("usage")
        if usage and isinstance(usage, dict):
            state.conversation.last_usage = usage

        if isinstance(content_blocks, str):
            state.conversation.messages.append(state.ChatMessage(
                id=state.new_message_id(),
                role="user" if role == "user" else "assistant",
                content=content_blocks,
            ))
            count += 1
            continue

        for block in content_blocks:
            if not isinstance(block, dict):
                continue

            block_type = block.get("type", "")

            if block_type == "text" and block.get("text"):
                state.conversation.messages.append(state.ChatMessage(
                    id=state.new_message_id(),
                    role="user" if role == "user" else "assistant",
                    content=block["text"],
                ))
                count += 1

            elif block_type == "tool_use" and block.get("name", "").endswith("generate_blender_code"):
                tool_input = block.get("input", {})
                code = tool_input.get("code", "")
                msg = state.ChatMessage(
                    id=state.new_message_id(),
                    role="tool_status",
                    code=code,
                    has_code=True,
                    code_bytes=len(code),
                )
                state.conversation.messages.append(msg)
                last_code_msg = msg
                count += 1

            elif block_type == "tool_use" and block.get("name", ""):
                # Non-code tool use — restore as tool_info message
                tool_name = block["name"]
                tool_detail = _extract_tool_detail(
                    tool_name,
                    [json.dumps(block.get("input", {}))],
                )
                msg = state.ChatMessage(
                    id=state.new_message_id(),
                    role="tool_info",
                    content=tool_name,
                    tool_detail=tool_detail,
                )
                state.conversation.messages.append(msg)
                count += 1

            elif block_type == "tool_result":
                # Parse tool result text to set status on the preceding code block
                result_content = block.get("content", "")
                if isinstance(result_content, list):
                    result_content = " ".join(
                        b.get("text", "") for b in result_content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                elif not isinstance(result_content, str):
                    result_content = str(result_content) if result_content else ""

                if last_code_msg and result_content:
                    lower = result_content.lower()
                    if "executed successfully" in lower:
                        last_code_msg.code_executed = True
                    elif "rejected" in lower:
                        last_code_msg.code_rejected = True
                last_code_msg = None

    logger.info("Restored %d messages from SDK session %s", count, session_id)
    return count


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
    return HTMLResponse(html, headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    })


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
    """Execute code in Blender. Resolves pending tool action if one exists."""
    body = await request.json()
    code = body.get("code", "")

    if not code:
        return JSONResponse({"error": "No code provided"}, status_code=400)

    result = await execute_code(code)

    # If there's a pending tool action, resolve it with the execution result
    action = state.pending_tool_action
    if action and action.code == code:
        if result.get("success", False):
            output = result.get("output", "")
            action.result = f"Code executed successfully.{' Output: ' + output if output else ''}"
        else:
            error = result.get("error", "Unknown error")
            action.result = f"Code execution failed: {error}"
        action.event.set()

    return JSONResponse(result)


async def api_reject(request: Request) -> JSONResponse:
    """Reject pending code. Resolves pending tool action with rejection message."""
    body = await request.json()
    reason = body.get("reason", "").strip()

    action = state.pending_tool_action
    if not action:
        return JSONResponse({"error": "No pending code to reject"}, status_code=400)

    action.result = f"User rejected this code.{' Reason: ' + reason if reason else ' No reason provided.'}"
    action.event.set()

    logger.info("Code rejected: %s", reason or "(no reason)")
    return JSONResponse({"success": True})


async def api_undo(request: Request) -> JSONResponse:
    """Undo last execution in Blender."""
    result = await undo()
    return JSONResponse(result)


async def api_clear(request: Request) -> JSONResponse:
    """Clear conversation and reset agent."""
    state.conversation.messages.clear()
    state.conversation.is_streaming = False
    state.conversation.active_code_index = -1
    state.conversation.sdk_session_id = ""

    await reset_client()

    # Add welcome message
    welcome = state.ChatMessage(
        id=state.new_message_id(),
        role="assistant",
        content="Chat cleared! How can I help you with your Blender scene?",
    )
    state.conversation.messages.append(welcome)

    state.conversation.version += 1
    logger.info("Conversation cleared")
    return JSONResponse({"success": True})


async def api_chat_export(request: Request) -> JSONResponse:
    """Export the full SDK session as JSONL for download."""
    session_id = state.conversation.sdk_session_id
    if not session_id:
        return JSONResponse({"messages": []})
    try:
        from claude_agent_sdk import get_session_messages
        messages = get_session_messages(session_id)
        export = [{"type": m.type, "uuid": m.uuid, "message": m.message} for m in messages]
        return JSONResponse({"messages": export})
    except Exception as e:
        logger.warning("Chat export failed: %s", e)
        return JSONResponse({"messages": [], "error": str(e)})


async def api_chat_version(request: Request) -> JSONResponse:
    """Return the conversation version counter for change detection."""
    return JSONResponse({"version": state.conversation.version})



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
            "auto_execute": state.settings.auto_execute,
        })

    # POST
    body = await request.json()

    # auto_execute can be changed without resetting the client
    if "auto_execute" in body:
        state.settings.auto_execute = bool(body["auto_execute"])
        logger.info("auto_execute set to %s", state.settings.auto_execute)

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

    # Persist all settings to disk
    state.save_settings()

    return JSONResponse({"success": True})


async def api_reload(request: Request) -> JSONResponse:
    """Called by Blender when a file is loaded — reload session from the new document."""
    await reset_client()
    state.conversation.messages.clear()
    state.conversation.is_streaming = False
    state.conversation.active_code_index = -1

    # Load SDK session ID from Blender document
    session_id = await load_session_id()

    if session_id:
        state.conversation.sdk_session_id = session_id
        count = _restore_messages_from_sdk(session_id)
        logger.info("File reload: restored %d messages from SDK session %s", count, session_id)
    else:
        state.conversation.sdk_session_id = ""
        state.conversation.tool_status = {}
        welcome = state.ChatMessage(
            id=state.new_message_id(),
            role="assistant",
            content="Hi! I'm Smoothie, your AI animation assistant. What would you like to create?",
        )
        state.conversation.messages.append(welcome)

    state.conversation.version += 1
    return JSONResponse({"success": True, "message_count": len(state.conversation.messages)})


_PROJECT_NOTES_TEMPLATE = """\
# Project Notes

## Scene Structure
<!-- Describe the key objects and their roles -->

## Animation Plan
<!-- Outline the animations you want to create -->

## Technical Notes
<!-- Any specific requirements, constraints, or references -->
"""


async def api_library(request: Request) -> JSONResponse:
    """List or manage library files."""
    result = await query_blender("/api/library", method="GET")
    return JSONResponse(result)


async def api_library_read(request: Request) -> JSONResponse:
    body = await request.json()
    result = await query_blender("/api/library/read", body)
    return JSONResponse(result)


async def api_library_write(request: Request) -> JSONResponse:
    body = await request.json()
    result = await query_blender("/api/library/write", body)
    return JSONResponse(result)


async def api_library_delete(request: Request) -> JSONResponse:
    body = await request.json()
    result = await query_blender("/api/library/delete", body)
    return JSONResponse(result)


async def api_project_notes(request: Request) -> JSONResponse:
    """Get or update project notes (smoothie.md)."""
    if request.method == "GET":
        result = await query_blender("/api/project-notes", method="GET")
        return JSONResponse({
            "content": result.get("content", ""),
            "exists": result.get("exists", False),
        })

    # POST — save content
    body = await request.json()
    content = body.get("content", "")
    result = await query_blender("/api/project-notes", {"content": content})

    # Reset client so system prompt picks up the new notes
    if result.get("success"):
        await reset_client()
        logger.info("Project notes updated, client reset for prompt refresh")

    return JSONResponse(result)


async def api_project_notes_create(request: Request) -> JSONResponse:
    """Create project notes with the default template."""
    result = await query_blender("/api/project-notes", {"content": _PROJECT_NOTES_TEMPLATE})
    if result.get("success"):
        await reset_client()
    return JSONResponse({"success": True, "content": _PROJECT_NOTES_TEMPLATE})


async def api_project_name(request: Request) -> JSONResponse:
    """Get the Blender project name, filename, file size, and modified time."""
    result = await query_blender("/api/project-name", method="GET")
    return JSONResponse({
        "name": result.get("name", "Untitled"),
        "filename": result.get("filename", ""),
        "file_size": result.get("file_size", 0),
        "modified_time": result.get("modified_time", 0),
    })


async def api_context_usage(request: Request) -> JSONResponse:
    """Return current context window usage percentage."""
    usage = state.conversation.last_usage
    if not usage:
        return JSONResponse({"percent": 0, "total_tokens": 0, "max_tokens": 0})
    # Total context = direct input + cached tokens (both created and read)
    total_tokens = (
        usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
    )
    # Claude context windows by model
    model = state.settings.model
    if "opus" in model:
        max_tokens = 200000
    elif "haiku" in model:
        max_tokens = 200000
    else:
        max_tokens = 200000  # sonnet default
    percent = round((total_tokens / max_tokens) * 100) if max_tokens else 0
    return JSONResponse({
        "percent": min(percent, 100),
        "total_tokens": total_tokens,
        "max_tokens": max_tokens,
    })


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
    Route("/api/reject", api_reject, methods=["POST"]),
    Route("/api/undo", api_undo, methods=["POST"]),
    Route("/api/clear", api_clear, methods=["POST"]),
    Route("/api/scene", api_scene, methods=["GET"]),
    Route("/api/settings", api_settings, methods=["GET", "POST"]),
    Route("/api/health", api_health, methods=["GET"]),
    Route("/api/chat/version", api_chat_version, methods=["GET"]),
    Route("/api/chat/export", api_chat_export, methods=["GET"]),
    Route("/api/reload", api_reload, methods=["POST"]),
    Route("/api/library", api_library, methods=["GET", "POST"]),
    Route("/api/library/read", api_library_read, methods=["POST"]),
    Route("/api/library/write", api_library_write, methods=["POST"]),
    Route("/api/library/delete", api_library_delete, methods=["POST"]),
    Route("/api/project-notes", api_project_notes, methods=["GET", "POST"]),
    Route("/api/project-notes/create", api_project_notes_create, methods=["POST"]),
    Route("/api/project-name", api_project_name, methods=["GET"]),
    Route("/api/context-usage", api_context_usage, methods=["GET"]),
    Route("/api/shutdown", api_shutdown, methods=["POST"]),
]

app = Starlette(routes=routes)
