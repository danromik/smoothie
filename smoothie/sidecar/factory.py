"""Factory for composing the Smoothie sidecar app with optional extensions.

This module exists so layered products built on top of Smoothie (e.g.
Smoothie Studio) can add their own MCP tools, library files, and
frontend *without* modifying any of Smoothie's source. The mechanism:

  1. The layered product's own sidecar process imports
     `build_agent_app` from this module and calls it with its extras.
  2. The factory stores those extras in module-level registries.
  3. When Smoothie's `agent.ensure_client()` later builds the Claude
     Agent SDK client, it reads from these registries and appends the
     extras to the baseline tool list.
  4. When Smoothie's `app.homepage()` handler serves the root page, it
     reads the frontend path from this module so a layered product can
     serve its own `frontend.html` while keeping Smoothie's routes.

Baseline Smoothie does not need to call `build_agent_app`: the getters
return sensible defaults when nothing has been registered, so
`python -m smoothie.sidecar.main` continues to work exactly as before.

This module is the **only** extension point on the backend. It has a
small, deliberately minimal API surface.
"""

import logging
import os
from typing import Any

logger = logging.getLogger("smoothie.sidecar.factory")

# Registry of extras populated by build_agent_app().
# These are module-level globals because both the agent and the app
# handlers need to read them at different points in the request
# lifecycle, and threading them through function arguments would ripple
# through a lot of call sites.
_extra_tools: list[Any] = []
_extra_library_files: dict[str, str] = {}
_frontend_path_override: str | None = None


def build_agent_app(
    extra_tools: list | None = None,
    extra_library_files: dict[str, str] | None = None,
    frontend_path: str | None = None,
):
    """Compose the Smoothie sidecar Starlette app with optional extensions.

    Args:
        extra_tools: Additional MCP tool functions (as returned by the
            ``@tool`` decorator from claude_agent_sdk) to register
            alongside Smoothie's baseline tools. Extras share the
            ``smoothie`` MCP server namespace, so the SDK exposes each
            as ``mcp__smoothie__<tool_name>``.
        extra_library_files: *Reserved for future use.* Pass ``{}`` or
            ``None`` for now. When implemented, this will let layered
            products ship library files that load into Blender's
            executor persistent namespace alongside user ``smoothie_lib/``
            files.
        frontend_path: Absolute path to a ``frontend.html`` file to
            serve at ``/``. Defaults to Smoothie's baseline frontend
            (next to ``app.py``).

    Returns:
        The configured Smoothie Starlette ``app`` instance, ready to
        hand to ``uvicorn.run(app, ...)``.

    Example (from Smoothie Studio's sidecar)::

        from smoothie.sidecar.factory import build_agent_app
        from studio_sidecar.tools import hello

        app = build_agent_app(
            extra_tools=[hello.hello],
            frontend_path="/path/to/studio_frontend/frontend.html",
        )
        uvicorn.run(app, host="127.0.0.1", port=8888)
    """
    global _extra_tools, _extra_library_files, _frontend_path_override

    _extra_tools = list(extra_tools) if extra_tools else []
    _extra_library_files = dict(extra_library_files) if extra_library_files else {}
    _frontend_path_override = frontend_path

    if _extra_library_files:
        logger.warning(
            "build_agent_app: extra_library_files is reserved for future use; "
            "ignoring %d entries passed this session",
            len(_extra_library_files),
        )

    logger.info(
        "build_agent_app: registered %d extra tools, frontend_path=%s",
        len(_extra_tools),
        _frontend_path_override or "(baseline)",
    )

    # Import app here to avoid a circular-import chain at module-load
    # time. app.py pulls in agent.py which imports tools.py which imports
    # blender_proxy.py — importing app eagerly from factory would make
    # factory.py transitively depend on the Blender side.
    from smoothie.sidecar.app import app
    return app


def get_extra_tools() -> list:
    """Return a copy of the extra tools registered via build_agent_app().

    Consumed by `agent.ensure_client()` when assembling the SDK client's
    tool list. Returns an empty list if no extras were registered, which
    is the baseline Smoothie case.
    """
    return list(_extra_tools)


def get_extra_library_files() -> dict[str, str]:
    """Return a copy of the extra library files registered via build_agent_app().

    *Reserved for future use.* Currently always returns the stored dict
    (which is empty unless a layered product passed library files, and
    those are logged + ignored for now).
    """
    return dict(_extra_library_files)


def get_frontend_path() -> str:
    """Return the path to the frontend.html file that `/` should serve.

    Returns the override path if one was set via ``build_agent_app``,
    otherwise the baseline Smoothie frontend path next to ``app.py``.
    """
    if _frontend_path_override:
        return _frontend_path_override
    return os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        "frontend.html",
    )
