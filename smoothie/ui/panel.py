"""Blender N-panel for Smoothie."""
import bpy


class SMOOTHIE_PT_main(bpy.types.Panel):
    bl_label = "Smoothie"
    bl_idname = "SMOOTHIE_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Smoothie"

    def draw(self, context):
        layout = self.layout

        from ..sidecar_launcher import is_running, get_port
        from ..blender_api import get_port as get_blender_port

        # Open browser button
        layout.operator("smoothie.open_browser", icon="URL")

        # Status section
        box = layout.box()

        sidecar_port = get_port()
        blender_port = get_blender_port()

        if is_running() and sidecar_port:
            box.label(text=f"Chat UI: localhost:{sidecar_port}", icon="CHECKMARK")
        else:
            box.label(text="Sidecar: Not running", icon="ERROR")
            box.operator("smoothie.restart_sidecar", icon="FILE_REFRESH")

        if blender_port:
            box.label(text=f"Blender API: port {blender_port}", icon="CHECKMARK")
        else:
            box.label(text="Blender API: Not running", icon="ERROR")


classes = (
    SMOOTHIE_PT_main,
)
