"""Blender operators for Smoothie."""
import bpy
import webbrowser
import logging

logger = logging.getLogger("smoothie.operators")


class SMOOTHIE_OT_open_browser(bpy.types.Operator):
    bl_idname = "smoothie.open_browser"
    bl_label = "Open Chat in Browser"
    bl_description = "Open the Smoothie chat interface in your web browser"

    def execute(self, context):
        from ..sidecar_launcher import get_port
        port = get_port()
        if port:
            url = f"http://localhost:{port}"
            webbrowser.open(url)
            self.report({"INFO"}, f"Opened {url}")
        else:
            self.report({"ERROR"}, "Sidecar not running")
        return {"FINISHED"}


class SMOOTHIE_OT_restart_sidecar(bpy.types.Operator):
    bl_idname = "smoothie.restart_sidecar"
    bl_label = "Restart Sidecar"
    bl_description = "Restart the Smoothie AI sidecar process"

    def execute(self, context):
        from ..sidecar_launcher import stop_sidecar, start_sidecar
        from ..blender_api import get_port as get_blender_port

        stop_sidecar()
        blender_port = get_blender_port()
        if blender_port:
            result = start_sidecar(blender_port=blender_port)
            if result:
                self.report({"INFO"}, f"Sidecar restarted on port {result}")
            else:
                self.report({"ERROR"}, "Failed to start sidecar. Run: pip3 install claude-agent-sdk  — then check smoothie.log for details.")
        else:
            self.report({"ERROR"}, "Blender API server not running")
        return {"FINISHED"}


# Classes to register
classes = (
    SMOOTHIE_OT_open_browser,
    SMOOTHIE_OT_restart_sidecar,
)
