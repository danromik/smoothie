import ast
import builtins

BLOCKED_MODULES = frozenset({
    "os", "subprocess", "shutil", "pathlib", "socket", "http", "urllib",
    "ftplib", "smtplib", "ctypes", "multiprocessing", "signal", "sys",
    "importlib", "code", "codeop", "compileall", "py_compile",
    "zipimport", "pkgutil", "tempfile", "glob", "fnmatch",
    "webbrowser", "xmlrpc", "asyncio", "concurrent",
})

ALLOWED_MODULES = frozenset({
    "bpy", "bmesh", "mathutils", "math", "random", "colorsys",
    "itertools", "functools", "collections", "enum", "dataclasses",
    "typing", "re",
})


def _restricted_import(name, *args, **kwargs):
    top_level = name.split(".")[0]
    if top_level in BLOCKED_MODULES:
        raise ImportError(
            f"Module '{name}' is blocked for security. "
            f"Allowed modules: {', '.join(sorted(ALLOWED_MODULES))}"
        )
    return builtins.__import__(name, *args, **kwargs)


def create_restricted_globals():
    safe_builtins = {k: v for k, v in builtins.__dict__.items()}
    safe_builtins["__import__"] = _restricted_import
    safe_builtins.pop("open", None)
    safe_builtins.pop("exec", None)
    safe_builtins.pop("eval", None)
    safe_builtins.pop("compile", None)
    safe_builtins.pop("breakpoint", None)

    import bpy
    import math
    import random

    return {
        "__builtins__": safe_builtins,
        "bpy": bpy,
        "math": math,
        "random": random,
    }


def validate_code(code: str) -> list[str]:
    warnings = []

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        warnings.append(f"Syntax error: {e}")
        return warnings

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in BLOCKED_MODULES:
                    warnings.append(
                        f"Blocked import: '{alias.name}' (line {node.lineno})"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in BLOCKED_MODULES:
                    warnings.append(
                        f"Blocked import: 'from {node.module}' (line {node.lineno})"
                    )
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in (
                "open", "exec", "eval", "compile", "breakpoint", "__import__",
            ):
                warnings.append(
                    f"Blocked builtin call: '{node.func.id}()' (line {node.lineno})"
                )

    return warnings
