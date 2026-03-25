import io
import re
import sys
import traceback
from dataclasses import dataclass

from .sandbox import validate_code, create_restricted_globals


@dataclass
class ExecutionResult:
    success: bool
    output: str
    error: str | None = None
    error_type: str | None = None


def _fix_blender5_compat(code: str) -> str:
    """Strip code patterns that are broken in Blender 5.x.

    AI models trained on Blender 4.x docs frequently generate `action.fcurves`
    access for setting interpolation. This attribute was removed in Blender 5.x
    (layered actions). The keyframes themselves work fine without it, so we
    strip these blocks rather than failing execution.
    """
    # Remove blocks that iterate over action.fcurves
    # Matches patterns like:
    #   for fcurve in action.fcurves:
    #       ...indented block...
    #   for fcurve in obj.animation_data.action.fcurves:
    #       ...indented block...
    code = re.sub(
        r'^\s*for\s+\w+\s+in\s+\S*\.fcurves\s*:.*?(?=\n\S|\n\s*\n|\Z)',
        '# [auto-fixed] fcurves block removed (not available in Blender 5.x)',
        code,
        flags=re.MULTILINE | re.DOTALL,
    )
    # Remove if-guards that lead into fcurves access
    # e.g.: if obj.animation_data and obj.animation_data.action:
    #            for fcurve in ...
    code = re.sub(
        r'^\s*if\s+\S*\.animation_data\s+and\s+\S*\.animation_data\.action\s*:.*?(?=\n\S|\n\s*\n|\Z)',
        '# [auto-fixed] fcurves guard block removed (not available in Blender 5.x)',
        code,
        flags=re.MULTILINE | re.DOTALL,
    )
    return code


def execute_generated_code(code: str) -> ExecutionResult:
    code = _fix_blender5_compat(code)

    warnings = validate_code(code)
    if warnings:
        return ExecutionResult(
            success=False,
            output="",
            error="Code validation failed:\n" + "\n".join(warnings),
            error_type="ValidationError",
        )

    import bpy
    bpy.ops.ed.undo_push(message="Smoothie: AI Generated Animation")

    restricted_globals = create_restricted_globals()
    captured_output = io.StringIO()
    old_stdout = sys.stdout

    try:
        sys.stdout = captured_output
        exec(code, restricted_globals)
        return ExecutionResult(
            success=True,
            output=captured_output.getvalue(),
        )
    except Exception as e:
        return ExecutionResult(
            success=False,
            output=captured_output.getvalue(),
            error=traceback.format_exc(),
            error_type=type(e).__name__,
        )
    finally:
        sys.stdout = old_stdout


def undo_last_execution():
    import bpy
    bpy.ops.ed.undo()
