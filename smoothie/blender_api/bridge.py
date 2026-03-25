"""Bridge between the Blender API server (background threads) and Blender's main thread.

All bpy operations must happen on the main thread. HTTP handlers place commands
on the command_queue, and a bpy.app.timers callback processes them.
"""

import logging
import queue
import threading
from dataclasses import dataclass, field

logger = logging.getLogger("smoothie.blender_api.bridge")

# Global command queue — HTTP handlers put, main thread timer gets
command_queue = queue.Queue()


@dataclass
class MainThreadCommand:
    action: str
    data: dict = field(default_factory=dict)
    result: dict | None = None
    done_event: threading.Event = field(default_factory=threading.Event)


def bridge_timer_callback():
    """Called by bpy.app.timers on the main thread at ~20Hz.

    Processes commands from API handlers.
    Returns 0.05 to keep the timer running.
    """
    while True:
        try:
            cmd = command_queue.get_nowait()
        except queue.Empty:
            break

        try:
            _process_command(cmd)
        except Exception as e:
            logger.error("Error processing command %s: %s", cmd.action, e, exc_info=True)
            cmd.result = {"success": False, "error": str(e)}
            cmd.done_event.set()

    return 0.05  # Run again in 50ms


def _process_command(cmd):
    """Process a single command on the main thread."""
    import bpy
    from ..executor.runner import execute_generated_code, undo_last_execution
    from ..ai.context import gather_scene_context, format_context_for_prompt

    if cmd.action == "execute_code":
        code = cmd.data.get("code", "").strip()
        if not code:
            cmd.result = {"success": False, "error": "No code provided"}
            cmd.done_event.set()
            return

        result = execute_generated_code(code)
        cmd.result = {
            "success": result.success,
            "output": result.output,
            "error": result.error or "",
        }
        if result.error_type:
            cmd.result["error_type"] = result.error_type
        cmd.done_event.set()

    elif cmd.action == "undo":
        undo_last_execution()
        cmd.result = {"success": True}
        cmd.done_event.set()

    elif cmd.action == "get_scene":
        ctx = gather_scene_context(bpy.context)
        formatted = format_context_for_prompt(ctx)
        cmd.result = {"success": True, "text": formatted, "data": ctx}
        cmd.done_event.set()

    elif cmd.action == "get_status":
        cmd.result = {"success": True}
        cmd.done_event.set()

    else:
        cmd.result = {"success": False, "error": f"Unknown action: {cmd.action}"}
        cmd.done_event.set()
