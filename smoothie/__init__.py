bl_info = {
    "name": "Smoothie",
    "author": "Smoothie Team",
    "version": (0, 2, 0),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > Smoothie",
    "description": "AI-powered animation generation via natural language prompts",
    "category": "Animation",
}

# Guard bpy import — the sidecar process imports smoothie.ai.templates
# without Blender, so __init__.py must not fail when bpy is absent.
try:
    import bpy
    _HAS_BPY = True
except ImportError:
    _HAS_BPY = False

# Set up file logging for the smoothie.* logger hierarchy (Blender side only)
if _HAS_BPY:
    import logging as _logging
    import os as _os
    _pkg_dir = _os.path.dirname(_os.path.realpath(__file__))
    _project_root = _os.path.dirname(_pkg_dir)
    _log_dir = _os.path.join(_project_root, "logs")
    _os.makedirs(_log_dir, exist_ok=True)
    _log_file = _os.path.join(_log_dir, "smoothie.log")
    _logger = _logging.getLogger("smoothie")
    if not _logger.handlers:
        _logger.setLevel(_logging.DEBUG)
        _fh = _logging.FileHandler(_log_file, mode="a", encoding="utf-8")
        _fh.setFormatter(_logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        _logger.addHandler(_fh)


if _HAS_BPY:
    class SmoothiePreferences(bpy.types.AddonPreferences):
        bl_idname = "smoothie"

        auto_execute: bpy.props.BoolProperty(
            name="Auto-Execute",
            description="Automatically execute generated code without preview",
            default=False,
        )

        sidecar_python: bpy.props.StringProperty(
            name="System Python",
            description="Path to system Python with claude_agent_sdk installed (leave default to auto-detect)",
            default="python3",
        )

        def draw(self, context):
            layout = self.layout
            layout.prop(self, "auto_execute")
            layout.prop(self, "sidecar_python")


def register():
    if not _HAS_BPY:
        return

    from .ui.properties import register_properties
    from .ui.operators import classes as operator_classes
    from .ui.panel import classes as panel_classes

    for cls in [SmoothiePreferences] + list(operator_classes) + list(panel_classes):
        bpy.utils.register_class(cls)

    register_properties()

    # Deferred startup: start blender_api server + sidecar
    def _deferred_startup():
        import traceback

        try:
            from .blender_api import start_server as start_blender_api
            from .blender_api.bridge import bridge_timer_callback
            from .sidecar_launcher import start_sidecar

            blender_port = start_blender_api(port=8889)

            if not bpy.app.timers.is_registered(bridge_timer_callback):
                bpy.app.timers.register(bridge_timer_callback, persistent=True)

            if blender_port:
                sidecar_port = start_sidecar(blender_port=blender_port, sidecar_port=8888)
                if sidecar_port:
                    print(f"[Smoothie] Sidecar started on http://localhost:{sidecar_port}")
                    print(f"[Smoothie] Blender API on http://127.0.0.1:{blender_port}")
                else:
                    print("[Smoothie] WARNING: Sidecar failed to start (is system Python with claude_agent_sdk available?)")
            else:
                print("[Smoothie] WARNING: Blender API server failed to start")

        except Exception:
            print(f"[Smoothie] ERROR during startup:\n{traceback.format_exc()}")

        return None  # Don't repeat

    bpy.app.timers.register(_deferred_startup, first_interval=1.0)


def unregister():
    if not _HAS_BPY:
        return

    from .ui.properties import unregister_properties
    from .ui.operators import classes as operator_classes
    from .ui.panel import classes as panel_classes

    # Stop sidecar
    from .sidecar_launcher import stop_sidecar
    stop_sidecar()

    # Stop blender_api server
    from .blender_api import stop_server
    stop_server()

    # Unregister bridge timer
    from .blender_api.bridge import bridge_timer_callback
    if bpy.app.timers.is_registered(bridge_timer_callback):
        bpy.app.timers.unregister(bridge_timer_callback)

    unregister_properties()

    for cls in reversed(list(panel_classes) + list(operator_classes) + [SmoothiePreferences]):
        bpy.utils.unregister_class(cls)
