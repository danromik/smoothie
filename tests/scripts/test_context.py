"""Tests for scene context serialization. Runs against bpy stub."""

from smoothie.ai.context import gather_scene_context, format_context_for_prompt


class MockMaterial:
    def __init__(self, name):
        self.name = name


class MockMaterials:
    def __init__(self, names):
        self._mats = [MockMaterial(n) for n in names]

    def __iter__(self):
        return iter(self._mats)


class MockData:
    def __init__(self, materials=None):
        self.materials = MockMaterials(materials or [])


class MockObject:
    def __init__(self, name, obj_type="MESH", location=(0, 0, 0)):
        self.name = name
        self.type = obj_type
        self.location = location
        self.rotation_euler = (0, 0, 0)
        self.scale = (1, 1, 1)
        self.animation_data = None
        self.data = MockData()


class MockRender:
    fps = 24


class MockScene:
    def __init__(self, objects=None):
        self.objects = objects or []
        self.frame_start = 1
        self.frame_end = 250
        self.frame_current = 1
        self.render = MockRender()


class MockContext:
    def __init__(self, objects=None):
        self.scene = MockScene(objects or [])
        self.active_object = objects[0] if objects else None
        self.selected_objects = objects or []


class TestGatherSceneContext:
    def test_empty_scene(self):
        ctx = MockContext()
        result = gather_scene_context(ctx)
        assert result["objects"] == []
        assert result["frame_start"] == 1
        assert result["fps"] == 24

    def test_single_object(self):
        cube = MockObject("Cube", "MESH", (1, 2, 3))
        ctx = MockContext([cube])
        result = gather_scene_context(ctx)
        assert len(result["objects"]) == 1
        assert result["objects"][0]["name"] == "Cube"
        assert result["objects"][0]["type"] == "MESH"
        assert result["objects"][0]["location"] == (1, 2, 3)
        assert result["active_object"] == "Cube"

    def test_no_animation(self):
        cube = MockObject("Cube")
        ctx = MockContext([cube])
        result = gather_scene_context(ctx)
        assert result["objects"][0]["has_animation"] is False
        assert result["objects"][0]["keyframe_count"] == 0


class TestFormatContext:
    def test_empty_scene_format(self):
        ctx = MockContext()
        ctx_dict = gather_scene_context(ctx)
        text = format_context_for_prompt(ctx_dict)
        assert "empty scene" in text
        assert "Frame range: 1 to 250" in text

    def test_object_appears_in_output(self):
        cube = MockObject("MyCube", "MESH", (1, 2, 3))
        ctx = MockContext([cube])
        ctx_dict = gather_scene_context(ctx)
        text = format_context_for_prompt(ctx_dict)
        assert "MyCube" in text
        assert "MESH" in text
