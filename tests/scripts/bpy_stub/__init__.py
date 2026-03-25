"""Minimal bpy stub for running unit tests outside Blender."""

import types


# --- Property descriptors ---

def StringProperty(**kwargs):
    return kwargs.get("default", "")

def BoolProperty(**kwargs):
    return kwargs.get("default", False)

def FloatProperty(**kwargs):
    return kwargs.get("default", 0.0)

def IntProperty(**kwargs):
    return kwargs.get("default", 0)

def EnumProperty(**kwargs):
    return kwargs.get("default", "")


# --- Mock types ---

class _MockBase:
    pass

class AddonPreferences(_MockBase):
    bl_idname = ""

class Operator(_MockBase):
    bl_idname = ""
    bl_label = ""
    bl_description = ""

    def report(self, level, message):
        pass

    def execute(self, context):
        return {"FINISHED"}

class Panel(_MockBase):
    bl_label = ""
    bl_idname = ""
    bl_space_type = ""
    bl_region_type = ""
    bl_category = ""

class PropertyGroup(_MockBase):
    pass

class Scene(_MockBase):
    pass


class _Types:
    AddonPreferences = AddonPreferences
    Operator = Operator
    Panel = Panel
    PropertyGroup = PropertyGroup
    Scene = Scene


# --- Mock ops ---

class _UndoPush:
    @staticmethod
    def __call__(**kwargs):
        _ops_log.append(("undo_push", kwargs))

class _Undo:
    @staticmethod
    def __call__():
        _ops_log.append(("undo",))

class _Ed:
    undo_push = _UndoPush()
    undo = _Undo()

class _Ops:
    ed = _Ed()

_ops_log = []


# --- Mock props ---

class _Props:
    StringProperty = staticmethod(StringProperty)
    BoolProperty = staticmethod(BoolProperty)
    FloatProperty = staticmethod(FloatProperty)
    IntProperty = staticmethod(IntProperty)
    EnumProperty = staticmethod(EnumProperty)


# --- Mock utils ---

class _Utils:
    @staticmethod
    def register_class(cls):
        pass

    @staticmethod
    def unregister_class(cls):
        pass


# --- Module-level attributes ---

types = _Types()
ops = _Ops()
props = _Props()
utils = _Utils()
context = None  # Set up per-test as needed
data = types.module = types  # placeholder


def get_ops_log():
    return _ops_log

def clear_ops_log():
    _ops_log.clear()
