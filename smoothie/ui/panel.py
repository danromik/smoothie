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

        # Welcome message (chat-style) — wrap to panel width
        welcome = (
            "I'm Smoothie, your animation assistant. "
            "Let's chat in a web browser window and "
            "I'll help you with your project."
        )
        # Estimate chars per line from region width. ~7px per char at UI scale 1.0,
        # minus padding for box border + icon on first line.
        ui_scale = context.preferences.system.ui_scale
        region_w = context.region.width
        char_w = 7.0 * ui_scale
        chars_per_line = max(12, int((region_w - 30) / char_w))
        first_line_chars = max(8, chars_per_line - 3)  # icon takes ~3 chars

        msg = layout.box().column(align=True)
        msg.scale_y = 0.7
        words = welcome.split()
        lines, current, limit = [], "", first_line_chars
        for w in words:
            test = (current + " " + w).strip()
            if len(test) <= limit:
                current = test
            else:
                lines.append(current)
                current = w
                limit = chars_per_line
        if current:
            lines.append(current)
        for i, line in enumerate(lines):
            if i == 0:
                msg.label(text=line, icon="LIGHT")
            else:
                msg.label(text=line)

        # Open browser button
        sidecar_port = get_port()
        port_str = sidecar_port if sidecar_port else 8888
        op = layout.operator("smoothie.open_browser", text=f"Open Smoothie Chat (localhost:{port_str})", icon="URL")

        # Status
        blender_port = get_blender_port()
        if is_running() and sidecar_port and blender_port:
            layout.label(text="Smoothie is Live", icon="KEYTYPE_JITTER_VEC")
        else:
            if not blender_port:
                layout.label(text="Blender API not running", icon="KEYTYPE_EXTREME_VEC")
            elif not (is_running() and sidecar_port):
                layout.label(text="Sidecar not running", icon="KEYTYPE_EXTREME_VEC")
                layout.operator("smoothie.restart_sidecar", icon="FILE_REFRESH")

        # Version / credit
        layout.label(text="Smoothie 0.99 - Created by Dan Romik")


classes = (
    SMOOTHIE_PT_main,
)
