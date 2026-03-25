from smoothie.executor.sandbox import validate_code, BLOCKED_MODULES, ALLOWED_MODULES


class TestValidateCode:
    def test_clean_code_passes(self):
        code = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
        assert validate_code(code) == []

    def test_import_os_blocked(self):
        code = "import os\nos.system('echo hi')"
        warnings = validate_code(code)
        assert len(warnings) == 1
        assert "os" in warnings[0]

    def test_import_subprocess_blocked(self):
        code = "import subprocess"
        warnings = validate_code(code)
        assert len(warnings) == 1
        assert "subprocess" in warnings[0]

    def test_from_import_blocked(self):
        code = "from os.path import join"
        warnings = validate_code(code)
        assert len(warnings) == 1
        assert "os" in warnings[0]

    def test_open_call_blocked(self):
        code = 'f = open("/etc/passwd")'
        warnings = validate_code(code)
        assert len(warnings) == 1
        assert "open" in warnings[0]

    def test_exec_call_blocked(self):
        code = 'exec("print(1)")'
        warnings = validate_code(code)
        assert len(warnings) == 1
        assert "exec" in warnings[0]

    def test_eval_call_blocked(self):
        code = 'eval("1+1")'
        warnings = validate_code(code)
        assert len(warnings) == 1
        assert "eval" in warnings[0]

    def test_syntax_error_caught(self):
        code = "def foo(:"
        warnings = validate_code(code)
        assert len(warnings) == 1
        assert "Syntax error" in warnings[0]

    def test_multiple_violations(self):
        code = "import os\nimport subprocess\nopen('x')"
        warnings = validate_code(code)
        assert len(warnings) == 3

    def test_allowed_modules_pass(self):
        code = "import math\nimport random\nimport colorsys"
        assert validate_code(code) == []

    def test_bpy_import_passes(self):
        code = "import bpy\nimport bmesh\nimport mathutils"
        assert validate_code(code) == []


class TestModuleSets:
    def test_no_overlap(self):
        assert BLOCKED_MODULES.isdisjoint(ALLOWED_MODULES)

    def test_os_is_blocked(self):
        assert "os" in BLOCKED_MODULES

    def test_bpy_is_allowed(self):
        assert "bpy" in ALLOWED_MODULES
