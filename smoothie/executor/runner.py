import io
import re
import sys
import traceback
import types
from dataclasses import dataclass

from .sandbox import validate_code, create_restricted_globals


@dataclass
class ExecutionResult:
    success: bool
    output: str
    error: str | None = None
    error_type: str | None = None


# Persistent namespace — functions and classes survive across executions
_persistent_namespace: dict = {}
_loaded_library_versions: dict[str, int] = {}  # name → hash of content when last loaded


def reset_namespace():
    """Clear the persistent namespace (e.g., on clear chat or new project)."""
    _persistent_namespace.clear()
    _loaded_library_versions.clear()


def invalidate_library(name: str = ""):
    """Mark a library file as needing reload. Empty name invalidates all."""
    if name:
        _loaded_library_versions.pop(name, None)
    else:
        _loaded_library_versions.clear()


def _fix_blender5_compat(code: str) -> str:
    """Strip code patterns that are broken in Blender 5.x.

    AI models trained on Blender 4.x docs frequently generate `action.fcurves`
    access for setting interpolation. This attribute was removed in Blender 5.x
    (layered actions). The keyframes themselves work fine without it, so we
    strip these blocks rather than failing execution.
    """
    # Remove blocks that iterate over action.fcurves
    code = re.sub(
        r'^\s*for\s+\w+\s+in\s+\S*\.fcurves\s*:.*?(?=\n\S|\n\s*\n|\Z)',
        '# [auto-fixed] fcurves block removed (not available in Blender 5.x)',
        code,
        flags=re.MULTILINE | re.DOTALL,
    )
    # Remove if-guards that lead into fcurves access
    code = re.sub(
        r'^\s*if\s+\S*\.animation_data\s+and\s+\S*\.animation_data\.action\s*:.*?(?=\n\S|\n\s*\n|\Z)',
        '# [auto-fixed] fcurves guard block removed (not available in Blender 5.x)',
        code,
        flags=re.MULTILINE | re.DOTALL,
    )
    return code


def _load_library_files(namespace: dict) -> list[str]:
    """Load smoothie_lib/ files into the persistent namespace.

    Only re-executes files that are new or have changed since last load.
    Returns list of newly loaded file names.
    """
    import bpy

    loaded = []
    lib_files = sorted(
        name for name in bpy.data.texts.keys()
        if name.startswith("smoothie_lib/")
    )

    for name in lib_files:
        lib_code = bpy.data.texts[name].as_string()
        content_hash = hash(lib_code)

        # Skip if already loaded and unchanged
        if _loaded_library_versions.get(name) == content_hash:
            continue

        # Validate library code
        warnings = validate_code(lib_code)
        if warnings:
            continue

        try:
            exec(lib_code, namespace)
            _loaded_library_versions[name] = content_hash
            _persistent_namespace.update(
                {k: v for k, v in namespace.items()
                 if callable(v) or isinstance(v, type)}
            )
            loaded.append(name)
        except Exception:
            pass

    return loaded


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

    # Merge persistent namespace (previously defined functions/classes)
    restricted_globals.update(_persistent_namespace)

    # Pre-load library files from bpy.data.texts
    _load_library_files(restricted_globals)

    # Track which keys existed before execution
    pre_keys = set(restricted_globals.keys())

    captured_output = io.StringIO()
    old_stdout = sys.stdout

    try:
        sys.stdout = captured_output
        exec(code, restricted_globals)

        # Capture user-defined callables and classes into persistent namespace
        for key, value in restricted_globals.items():
            if key not in pre_keys and not key.startswith("_"):
                if callable(value) or isinstance(value, type):
                    _persistent_namespace[key] = value

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
