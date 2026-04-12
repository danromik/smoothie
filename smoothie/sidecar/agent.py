"""Claude Agent SDK client wrapper."""

import asyncio
import json
import logging
import traceback

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    create_sdk_mcp_server,
)
from claude_agent_sdk.types import TextBlock, ToolUseBlock

from . import state
from .blender_proxy import save_session_id
from .tools import (
    check_blenderkit,
    delete_library_file,
    generate_blender_code,
    import_asset,
    import_blenderkit_asset,
    list_asset_libraries,
    list_library_files,
    list_objects,
    read_animation,
    read_hierarchy,
    read_library_file,
    read_materials,
    read_object,
    read_project_notes,
    read_render_settings,
    read_scene,
    read_timeline,
    search_assets,
    search_blenderkit,
    search_by_material,
    search_objects,
    update_project_notes,
    write_library_file,
)

logger = logging.getLogger("smoothie.sidecar.agent")

# Map tool name suffix → argument key to extract for contextual tool info messages.
_TOOL_DETAIL_KEY = {
    "read_object": "name",
    "read_animation": "name",
    "list_objects": "type_filter",
    "read_hierarchy": "name",
    "search_objects": "query",
    "search_by_material": "material",
    "read_library_file": "name",
    "write_library_file": "name",
    "delete_library_file": "name",
    "search_assets": "query",
    "import_asset": "asset_name",
    "search_blenderkit": "keywords",
}


def _extract_tool_detail(tool_name: str, json_parts: list[str]) -> str:
    """Extract the key argument value from accumulated tool JSON for display."""
    suffix = tool_name.rsplit("__", 1)[-1] if "__" in tool_name else tool_name
    arg_key = _TOOL_DETAIL_KEY.get(suffix)
    if not arg_key:
        return ""
    try:
        input_data = json.loads("".join(json_parts))
        value = input_data.get(arg_key, "")
        return str(value) if value else ""
    except (json.JSONDecodeError, TypeError):
        return ""

# System prompt imported from existing ai module.
# The sidecar runs via `python -m smoothie.sidecar.main` with PYTHONPATH set
# to include the smoothie package parent directory.
from smoothie.ai.templates import SYSTEM_PROMPT
from .blender_proxy import query_blender

_client: ClaudeSDKClient | None = None

# The MCP-prefixed tool name used by the SDK
_CODE_TOOL = "mcp__smoothie__generate_blender_code"

# Track active tool blocks during streaming, keyed by session_id.
# Each value is a dict keyed by content block index with {name, id, json_parts, bytes}.
_active_tools: dict[str, dict[int, dict]] = {}


async def _build_system_prompt() -> str:
    """Build the system prompt, including smoothie.md content if it exists."""
    prompt = SYSTEM_PROMPT
    try:
        result = await query_blender("/api/project-notes", method="GET")
        if result.get("exists") and result.get("content", "").strip():
            notes = result["content"].strip()
            prompt += (
                "\n\n--- PROJECT NOTES (smoothie.md) ---\n"
                "The following are project-specific notes maintained by the user "
                "and assistant. Use this context to understand the project and "
                "update it when you make structural changes to the scene.\n\n"
                f"{notes}\n"
                "--- END PROJECT NOTES ---"
            )
    except Exception as e:
        logger.debug("Could not load project notes for system prompt: %s", e)
    return prompt


async def ensure_client() -> ClaudeSDKClient:
    """Create and connect the SDK client if needed.

    If an SDK session ID is available, passes resume= to continue that session.
    Falls back to a fresh session if resume fails.
    """
    global _client
    if _client is not None:
        return _client

    sdk_session = state.conversation.sdk_session_id
    logger.info("Creating new ClaudeSDKClient (model=%s, resume=%s)",
                state.settings.model, sdk_session or "none")

    all_tools = [
        generate_blender_code,
        read_scene,
        read_object,
        read_animation,
        list_objects,
        read_hierarchy,
        search_objects,
        search_by_material,
        read_materials,
        read_render_settings,
        read_timeline,
        read_project_notes,
        update_project_notes,
        list_library_files,
        read_library_file,
        write_library_file,
        delete_library_file,
        list_asset_libraries,
        search_assets,
        import_asset,
        check_blenderkit,
        search_blenderkit,
        import_blenderkit_asset,
    ]

    # Layered products (e.g. Smoothie Studio) register extra tools via
    # smoothie.sidecar.factory.build_agent_app(). If none were registered,
    # this is a no-op and the baseline tool list is used.
    try:
        from smoothie.sidecar.factory import get_extra_tools
        extra_tools = get_extra_tools()
    except Exception as e:
        logger.warning("Could not load extra tools from factory: %s", e)
        extra_tools = []
    if extra_tools:
        logger.info("Appending %d extra tools from factory", len(extra_tools))
        all_tools.extend(extra_tools)

    server = create_sdk_mcp_server(
        name="smoothie",
        tools=all_tools,
    )

    env = {}
    if state.settings.api_key:
        env["ANTHROPIC_API_KEY"] = state.settings.api_key

    system_prompt = await _build_system_prompt()

    allowed_tools = [
        "mcp__smoothie__generate_blender_code",
        "mcp__smoothie__read_scene",
        "mcp__smoothie__read_object",
        "mcp__smoothie__read_animation",
        "mcp__smoothie__list_objects",
        "mcp__smoothie__read_hierarchy",
        "mcp__smoothie__search_objects",
        "mcp__smoothie__search_by_material",
        "mcp__smoothie__read_materials",
        "mcp__smoothie__read_render_settings",
        "mcp__smoothie__read_timeline",
        "mcp__smoothie__read_project_notes",
        "mcp__smoothie__update_project_notes",
        "mcp__smoothie__list_library_files",
        "mcp__smoothie__read_library_file",
        "mcp__smoothie__write_library_file",
        "mcp__smoothie__delete_library_file",
        "mcp__smoothie__list_asset_libraries",
        "mcp__smoothie__search_assets",
        "mcp__smoothie__import_asset",
        "mcp__smoothie__check_blenderkit",
        "mcp__smoothie__search_blenderkit",
        "mcp__smoothie__import_blenderkit_asset",
    ]
    for tool_fn in extra_tools:
        tool_name = getattr(tool_fn, "__name__", None) or getattr(tool_fn, "name", None)
        if tool_name:
            allowed_tools.append(f"mcp__smoothie__{tool_name}")

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={"smoothie": server},
        allowed_tools=allowed_tools,
        model=state.settings.model or None,
        include_partial_messages=True,
        max_turns=50,
        env=env,
    )

    # Resume from SDK session if available
    if sdk_session:
        options.resume = sdk_session

    try:
        _client = ClaudeSDKClient(options)
        await _client.connect()
        logger.info("ClaudeSDKClient connected")
    except Exception as e:
        # If resume failed, retry without it
        if sdk_session:
            logger.warning("Failed to resume session %s: %s — starting fresh", sdk_session, e)
            state.conversation.sdk_session_id = ""
            options.resume = None
            _client = ClaudeSDKClient(options)
            await _client.connect()
            logger.info("ClaudeSDKClient connected (fresh session)")
        else:
            raise

    return _client


async def reset_client() -> None:
    """Disconnect and clear the client. Preserves sdk_session_id for resume."""
    global _client
    if _client is not None:
        logger.info("Disconnecting ClaudeSDKClient")
        try:
            await _client.disconnect()
        except Exception as e:
            logger.warning("Error disconnecting client: %s", e)
        _client = None


async def stream_chat(prompt: str, session_id: str) -> None:
    """Main streaming function: sends prompt, processes responses, pushes to SSE queue."""
    sse_queue = state.sse_queues.get(session_id)
    if sse_queue is None:
        logger.error("No SSE queue for session_id=%s", session_id)
        return

    try:
        state.conversation.is_streaming = True
        client = await ensure_client()

        logger.info("Sending query (session=%s, prompt_len=%d)", session_id, len(prompt))

        # Send the query — scene context is available via tools, not injected
        await client.query(prompt, session_id=session_id)

        # Process response messages
        async for message in client.receive_messages():
            msg_type = type(message).__name__
            logger.debug("Received message: %s", msg_type)

            if isinstance(message, StreamEvent):
                _handle_stream_event(message, sse_queue, session_id)

            elif isinstance(message, AssistantMessage):
                _handle_assistant_message(message, sse_queue, session_id)

            elif isinstance(message, ResultMessage):
                _handle_result_message(message, sse_queue, session_id)
                # Final flush — in case no other handler caught the tool
                _flush_pending_tools(session_id, sse_queue, "result_message")

                # Capture SDK session ID for persistence
                if hasattr(message, 'session_id') and message.session_id:
                    state.conversation.sdk_session_id = message.session_id
                    logger.info("Captured SDK session_id: %s", message.session_id)
                    # Save session ID to Blender document
                    try:
                        await save_session_id(message.session_id)
                    except Exception as e:
                        logger.warning("Failed to save session ID: %s", e)

                break  # Result message signals end of response

        # Signal done — include usage data if available
        done_data = {}
        if state.conversation.last_usage:
            done_data["usage"] = state.conversation.last_usage
        await sse_queue.put({"type": "done", "data": done_data})
        logger.info("Stream completed for session=%s", session_id)

    except Exception as e:
        logger.error("stream_chat error: %s\n%s", e, traceback.format_exc())
        try:
            await sse_queue.put({"type": "error", "data": {"message": str(e)}})
        except Exception:
            pass
    finally:
        state.conversation.is_streaming = False
        _active_tools.pop(session_id, None)

        # Deferred client reset (e.g., after project notes update)
        if state.conversation.needs_client_reset:
            state.conversation.needs_client_reset = False
            await reset_client()
            logger.info("Deferred client reset completed (project notes changed)")


def _finalize_tool(tool_info: dict, sse_queue: asyncio.Queue, source: str) -> None:
    """Parse accumulated tool JSON and emit tool_complete SSE event."""
    raw_json = "".join(tool_info["json_parts"])
    try:
        tool_input = json.loads(raw_json)
    except json.JSONDecodeError:
        logger.error("Failed to parse tool JSON (%d bytes)", len(raw_json))
        tool_input = {}
    code = tool_input.get("code", "")

    msg = state.ChatMessage(
        id=state.new_message_id(),
        role="tool_status",
        code=code,
        has_code=True,
        code_bytes=len(code),
    )
    state.conversation.messages.append(msg)
    state.conversation.active_code_index = len(state.conversation.messages) - 1

    sse_queue.put_nowait({
        "type": "tool_complete",
        "data": {
            "name": tool_info["name"],
            "id": tool_info["id"],
            "code": code,
            "message_id": msg.id,
            "message_index": len(state.conversation.messages) - 1,
        },
    })
    logger.info("Tool use (%s): generate_blender_code (%d bytes)", source, len(code))


def _flush_pending_tools(session_id: str, sse_queue: asyncio.Queue, source: str) -> None:
    """Finalize any tracked tool blocks that haven't been completed yet."""
    tools = _active_tools.get(session_id, {})
    for idx in list(tools.keys()):
        tool_info = tools.pop(idx)
        if tool_info["name"] == _CODE_TOOL and tool_info["json_parts"]:
            _finalize_tool(tool_info, sse_queue, source)
        elif tool_info["name"] != _CODE_TOOL:
            # Non-code tool: store in conversation
            msg = state.ChatMessage(
                id=state.new_message_id(),
                role="tool_info",
                content=tool_info["name"],
            )
            state.conversation.messages.append(msg)
            sse_queue.put_nowait({
                "type": "tool_complete",
                "data": {"name": tool_info["name"], "id": tool_info["id"]},
            })


def _handle_stream_event(message: StreamEvent, sse_queue: asyncio.Queue, session_id: str) -> None:
    """Handle partial streaming events (text deltas, tool input deltas)."""
    event = message.event
    event_type = event.get("type", "")

    # Log all event types for debugging
    logger.debug("StreamEvent: type=%s", event_type)

    if event_type == "content_block_delta":
        delta = event.get("delta", {})
        delta_type = delta.get("type", "")

        if delta_type == "text_delta":
            text = delta.get("text", "")
            if text:
                sse_queue.put_nowait({
                    "type": "text_delta",
                    "data": {"text": text},
                })

        elif delta_type == "input_json_delta":
            partial_json = delta.get("partial_json", "")
            if partial_json:
                idx = event.get("index", 0)
                # Accumulate JSON for the active tool block
                tools = _active_tools.get(session_id, {})
                if idx in tools:
                    tools[idx]["json_parts"].append(partial_json)
                    tools[idx]["bytes"] += len(partial_json)
                sse_queue.put_nowait({
                    "type": "tool_delta",
                    "data": {
                        "partial_json": partial_json,
                        "index": idx,
                        "bytes": tools.get(idx, {}).get("bytes", 0),
                    },
                })

    elif event_type == "content_block_start":
        block = event.get("content_block", {})
        if block.get("type") == "tool_use":
            idx = event.get("index", 0)
            name = block.get("name", "")
            tool_id = block.get("id", "")
            logger.info("Tool block started: name=%s, id=%s, index=%d", name, tool_id, idx)
            # Start tracking this tool block
            if session_id not in _active_tools:
                _active_tools[session_id] = {}
            _active_tools[session_id][idx] = {
                "name": name,
                "id": tool_id,
                "json_parts": [],
                "bytes": 0,
            }
            sse_queue.put_nowait({
                "type": "tool_start",
                "data": {"name": name, "id": tool_id, "index": idx},
            })

    elif event_type == "content_block_stop":
        idx = event.get("index", 0)
        tools = _active_tools.get(session_id, {})
        tool_info = tools.pop(idx, None)
        if tool_info:
            if tool_info["name"] == _CODE_TOOL:
                _finalize_tool(tool_info, sse_queue, "content_block_stop")
            else:
                # Non-code tool: store in conversation and emit tool_complete
                detail = _extract_tool_detail(tool_info["name"], tool_info["json_parts"])
                msg = state.ChatMessage(
                    id=state.new_message_id(),
                    role="tool_info",
                    content=tool_info["name"],
                    tool_detail=detail,
                )
                state.conversation.messages.append(msg)
                sse_queue.put_nowait({
                    "type": "tool_complete",
                    "data": {"name": tool_info["name"], "id": tool_info["id"], "detail": detail},
                })

    elif event_type == "message_stop":
        # Fallback: finalize any tools that weren't closed by content_block_stop
        _flush_pending_tools(session_id, sse_queue, "message_stop")


def _handle_assistant_message(message: AssistantMessage, sse_queue: asyncio.Queue, session_id: str) -> None:
    """Handle complete assistant messages with text and tool use blocks."""
    block_types = [type(b).__name__ for b in message.content]
    logger.info("AssistantMessage: blocks=%s", block_types)

    # Flush any tools still tracked from streaming (fallback if content_block_stop/message_stop missed)
    _flush_pending_tools(session_id, sse_queue, "assistant_message")

    for block in message.content:
        if isinstance(block, TextBlock):
            if block.text:
                msg = state.ChatMessage(
                    id=state.new_message_id(),
                    role="assistant",
                    content=block.text,
                )
                state.conversation.messages.append(msg)
                sse_queue.put_nowait({
                    "type": "text_complete",
                    "data": {"text": block.text, "message_id": msg.id},
                })

        elif isinstance(block, ToolUseBlock):
            # Check if already handled by streaming
            already_handled = any(
                m.has_code and m.code == block.input.get("code", "")
                for m in state.conversation.messages
                if m.role == "tool_status"
            )
            if already_handled:
                logger.debug("Skipping duplicate tool_complete for %s", block.id)
                continue

            if block.name == _CODE_TOOL:
                code = block.input.get("code", "")

                msg = state.ChatMessage(
                    id=state.new_message_id(),
                    role="tool_status",
                    code=code,
                    has_code=True,
                    code_bytes=len(code),
                )
                state.conversation.messages.append(msg)
                state.conversation.active_code_index = len(state.conversation.messages) - 1

                sse_queue.put_nowait({
                    "type": "tool_complete",
                    "data": {
                        "name": block.name,
                        "id": block.id,
                        "code": code,
                        "message_id": msg.id,
                        "message_index": len(state.conversation.messages) - 1,
                    },
                })
                logger.info("Tool use (assistant_msg): generate_blender_code (%d bytes)", len(code))


def _handle_result_message(message: ResultMessage, sse_queue: asyncio.Queue, session_id: str) -> None:
    """Handle result/completion message."""
    logger.info(
        "Result: subtype=%s, stop_reason=%s, turns=%d, cost=$%.4f, duration=%dms",
        message.subtype,
        message.stop_reason,
        message.num_turns,
        message.total_cost_usd or 0,
        message.duration_ms,
    )
    if message.usage:
        logger.info("Usage: %s", json.dumps(message.usage))
        # Store latest usage for context percentage tracking
        state.conversation.last_usage = message.usage


