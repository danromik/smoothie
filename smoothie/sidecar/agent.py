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
from .blender_proxy import get_scene_context
from .tools import generate_blender_code

logger = logging.getLogger("smoothie.sidecar.agent")

# System prompt imported from existing ai module.
# The sidecar runs via `python -m smoothie.sidecar.main` with PYTHONPATH set
# to include the smoothie package parent directory.
from smoothie.ai.templates import SYSTEM_PROMPT

_client: ClaudeSDKClient | None = None

# The MCP-prefixed tool name used by the SDK
_CODE_TOOL = "mcp__smoothie__generate_blender_code"

# Track active tool blocks during streaming, keyed by session_id.
# Each value is a dict keyed by content block index with {name, id, json_parts, bytes}.
_active_tools: dict[str, dict[int, dict]] = {}


async def ensure_client() -> ClaudeSDKClient:
    """Create and connect the SDK client if needed."""
    global _client
    if _client is not None:
        return _client

    logger.info("Creating new ClaudeSDKClient (model=%s)", state.settings.model)

    server = create_sdk_mcp_server(
        name="smoothie",
        tools=[generate_blender_code],
    )

    env = {}
    if state.settings.api_key:
        env["ANTHROPIC_API_KEY"] = state.settings.api_key

    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"smoothie": server},
        allowed_tools=["mcp__smoothie__generate_blender_code"],
        model=state.settings.model or None,
        include_partial_messages=True,
        max_turns=5,
        env=env,
    )

    _client = ClaudeSDKClient(options)
    await _client.connect()
    logger.info("ClaudeSDKClient connected")
    return _client


async def reset_client() -> None:
    """Disconnect and clear the client (called on settings change or clear chat)."""
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

        # Fetch scene context from Blender
        scene_context = await get_scene_context()
        full_prompt = f"{scene_context}\n\n{prompt}"
        logger.info("Sending query (session=%s, prompt_len=%d)", session_id, len(full_prompt))

        # Send the query
        await client.query(full_prompt, session_id=session_id)

        # Process response messages
        async for message in client.receive_messages():
            msg_type = type(message).__name__
            logger.debug("Received message: %s", msg_type)

            # Log for developer panel
            event_record = _message_to_event_record(message)
            if event_record:
                state.conversation.developer_events.append(event_record)

            if isinstance(message, StreamEvent):
                _handle_stream_event(message, sse_queue, session_id)

            elif isinstance(message, AssistantMessage):
                _handle_assistant_message(message, sse_queue, session_id)

            elif isinstance(message, ResultMessage):
                _handle_result_message(message, sse_queue, session_id)
                # Final flush — in case no other handler caught the tool
                _flush_pending_tools(session_id, sse_queue, "result_message")
                break  # Result message signals end of response

        # Signal done
        await sse_queue.put({"type": "done", "data": {}})
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


def _finalize_tool(tool_info: dict, sse_queue: asyncio.Queue, source: str) -> None:
    """Parse accumulated tool JSON and emit tool_complete SSE event."""
    raw_json = "".join(tool_info["json_parts"])
    try:
        tool_input = json.loads(raw_json)
    except json.JSONDecodeError:
        logger.error("Failed to parse tool JSON (%d bytes)", len(raw_json))
        tool_input = {}
    code = tool_input.get("code", "")
    post_message = tool_input.get("post_message", "")

    msg = state.ChatMessage(
        id=state.new_message_id(),
        role="tool_status",
        content=post_message,
        code=code,
        post_message=post_message,
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
            "post_message": post_message,
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
        if tool_info and tool_info["name"] == _CODE_TOOL:
            _finalize_tool(tool_info, sse_queue, "content_block_stop")

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
                post_message = block.input.get("post_message", "")

                msg = state.ChatMessage(
                    id=state.new_message_id(),
                    role="tool_status",
                    content=post_message,
                    code=code,
                    post_message=post_message,
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
                        "post_message": post_message,
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


def _message_to_event_record(message) -> dict | None:
    """Convert a message to a dict for the developer panel."""
    try:
        if isinstance(message, StreamEvent):
            return {"type": "stream_event", "event": message.event}
        elif isinstance(message, AssistantMessage):
            return {
                "type": "assistant_message",
                "model": message.model,
                "content": [
                    {"type": "text", "text": b.text} if isinstance(b, TextBlock)
                    else {"type": "tool_use", "name": b.name, "id": b.id, "input": b.input} if isinstance(b, ToolUseBlock)
                    else {"type": "unknown"}
                    for b in message.content
                ],
                "usage": message.usage,
            }
        elif isinstance(message, ResultMessage):
            return {
                "type": "result",
                "subtype": message.subtype,
                "stop_reason": message.stop_reason,
                "num_turns": message.num_turns,
                "total_cost_usd": message.total_cost_usd,
                "duration_ms": message.duration_ms,
                "usage": message.usage,
            }
    except Exception:
        pass
    return None
