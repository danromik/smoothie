"""Tests for code execution runner. Uses bpy stub."""

from smoothie.executor.runner import execute_generated_code


class TestExecuteGeneratedCode:
    def test_valid_code_succeeds(self):
        code = "x = 1 + 1"
        result = execute_generated_code(code)
        assert result.success is True
        assert result.error is None

    def test_print_captured(self):
        code = 'print("hello from smoothie")'
        result = execute_generated_code(code)
        assert result.success is True
        assert "hello from smoothie" in result.output

    def test_syntax_error_fails_validation(self):
        code = "def foo(:"
        result = execute_generated_code(code)
        assert result.success is False
        assert result.error_type == "ValidationError"

    def test_blocked_import_fails_validation(self):
        code = "import os\nos.system('echo hi')"
        result = execute_generated_code(code)
        assert result.success is False
        assert "os" in result.error

    def test_runtime_error_caught(self):
        code = "x = 1 / 0"
        result = execute_generated_code(code)
        assert result.success is False
        assert result.error_type == "ZeroDivisionError"

    def test_name_error_caught(self):
        code = "nonexistent_function()"
        result = execute_generated_code(code)
        assert result.success is False
        assert result.error_type == "NameError"

    def test_open_blocked_at_validation(self):
        code = 'open("/etc/passwd")'
        result = execute_generated_code(code)
        assert result.success is False
        assert "open" in result.error
