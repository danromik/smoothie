import sys
import types

# Patch bpy with our stub before any smoothie imports
from tests.scripts import bpy_stub

sys.modules["bpy"] = bpy_stub
sys.modules["bpy.types"] = types.ModuleType("bpy.types")
sys.modules["bpy.types"].AddonPreferences = bpy_stub.AddonPreferences
sys.modules["bpy.types"].Operator = bpy_stub.Operator
sys.modules["bpy.types"].Panel = bpy_stub.Panel
sys.modules["bpy.types"].PropertyGroup = bpy_stub.PropertyGroup
sys.modules["bpy.types"].Scene = bpy_stub.Scene
sys.modules["bpy.props"] = types.ModuleType("bpy.props")
sys.modules["bpy.props"].StringProperty = bpy_stub.StringProperty
sys.modules["bpy.props"].BoolProperty = bpy_stub.BoolProperty
sys.modules["bpy.props"].FloatProperty = bpy_stub.FloatProperty
sys.modules["bpy.props"].IntProperty = bpy_stub.IntProperty
sys.modules["bpy.props"].EnumProperty = bpy_stub.EnumProperty
sys.modules["bpy.utils"] = types.ModuleType("bpy.utils")
sys.modules["bpy.utils"].register_class = bpy_stub.utils.register_class
sys.modules["bpy.utils"].unregister_class = bpy_stub.utils.unregister_class
sys.modules["bpy.ops"] = types.ModuleType("bpy.ops")

# Stub out bmesh and mathutils as empty modules
sys.modules["bmesh"] = types.ModuleType("bmesh")
sys.modules["mathutils"] = types.ModuleType("mathutils")
