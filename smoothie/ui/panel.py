"""Blender N-panel for Smoothie."""
import json
import os

import bpy

_branding = None

def _load_branding():
    global _branding
    if _branding is not None:
        return _branding
    _branding = {}
    cfg = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "panel_config.json")
    if os.path.isfile(cfg):
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                _branding = json.load(f)
        except Exception:
            pass
    return _branding


_b = _load_branding()


class SMOOTHIE_PT_main(bpy.types.Panel):
    bl_label = _b.get("panel_label", "Smoothie")
    bl_idname = "SMOOTHIE_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = _b.get("panel_category", "Smoothie")

    def draw(self, context):
        layout = self.layout
        b = _load_branding()

        from ..sidecar_launcher import is_running, get_port
        from ..blender_api import get_port as get_blender_port

        product = b.get("product_name", "Smoothie")

        welcome = b.get("welcome", (
            "I'm Smoothie, your animation assistant. "
            "Let's chat in a web browser window and "
            "I'll help you with your project."
        ))
        welcome_box = b.get("welcome_box", True)

        if welcome_box:
            ui_scale = context.preferences.system.ui_scale
            region_w = context.region.width
            char_w = 7.0 * ui_scale
            chars_per_line = max(12, int((region_w - 30) / char_w))
            first_line_chars = max(8, chars_per_line - 3)

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
        else:
            layout.label(text=welcome)

        sidecar_port = get_port()
        port_str = sidecar_port if sidecar_port else 8888
        btn_label = b.get("open_button", f"Open Smoothie Chat (localhost:{port_str})")
        if "{port}" in btn_label:
            btn_label = btn_label.replace("{port}", str(port_str))
        layout.operator("smoothie.open_browser", text=btn_label, icon="URL")

        blender_port = get_blender_port()
        if is_running() and sidecar_port and blender_port:
            layout.label(text=f"{product} is Live", icon="KEYTYPE_JITTER_VEC")
        else:
            if not blender_port:
                layout.label(text="Blender API not running", icon="KEYTYPE_EXTREME_VEC")
            elif not (is_running() and sidecar_port):
                layout.label(text="Sidecar not running", icon="KEYTYPE_EXTREME_VEC")
                layout.operator("smoothie.restart_sidecar", icon="FILE_REFRESH")

        footer = b.get("footer", "Smoothie 0.99 - Created by Dan Romik")
        if footer:
            layout.label(text=footer)


classes = (
    SMOOTHIE_PT_main,
)
