"""MCP tool definitions for the sidecar agent."""

from claude_agent_sdk import tool


@tool(
    "generate_blender_code",
    "Generate and deliver Python/bpy code to be executed in Blender. "
    "Use this tool whenever the user's request requires creating, modifying, "
    "or animating objects, materials, lighting, cameras, or any other aspect "
    "of the Blender scene. Do NOT use this tool for questions that can be "
    "answered with text alone.",
    {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "Complete, self-contained Python code using Blender's bpy module. "
                    "Must run top-to-bottom with no external dependencies beyond "
                    "bpy and allowed standard modules (bmesh, mathutils, math, random, "
                    "colorsys, itertools, functools, collections). "
                    "Do NOT import os, subprocess, sys, shutil, pathlib, socket, http, "
                    "urllib, ctypes, multiprocessing, or any networking/filesystem modules."
                ),
            },
            "post_message": {
                "type": "string",
                "description": (
                    "A brief, friendly message to display to the user after the code "
                    "has been delivered. Use this to summarize what the code does, "
                    "suggest next steps, or offer tips. Keep it concise (1-2 sentences)."
                ),
            },
        },
        "required": ["code"],
    },
)
async def generate_blender_code(args: dict) -> str:
    """Handle code generation tool call. Returns confirmation to the agent."""
    code = args.get("code", "")
    return f"Code delivered ({len(code)} bytes). User will review and execute."
