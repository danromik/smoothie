#!/usr/bin/env python3
"""Integration test for the Phase 2 plugin-API refactor.

Validates that after the refactor in public Smoothie:
  - The on-disk smoothie package has the new factory module
  - The baseline sidecar subprocess is running and serves the frontend
  - Existing Blender-side code execution still works (no regression)
  - The Blender-side `smoothie.sidecar_launcher` has the new
    `_find_sidecar_module` helper (cache detection — will fail with a
    clear message if the add-on wasn't reloaded after the refactor)

Does NOT test Smoothie Studio yet (Studio's sidecar isn't running in
the user's Blender until Phase 4+5 of STUDIO_PLAN.local.md lands). This
test is purely for validating that the refactor didn't break anything.

Run via test_watcher.py. Exit code 0 on all-pass, 1 on any failure.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

SIDECAR_HOST = "127.0.0.1"
SIDECAR_PORT = 8888
BLENDER_PORTS = [8889, 8890, 8891]

# Where Blender lives on macOS. Set BLENDER env var to override.
_BLENDER_CANDIDATES = [
    os.environ.get("BLENDER"),
    "/Applications/Blender.app/Contents/MacOS/Blender",
    "/Applications/Blender/Blender.app/Contents/MacOS/Blender",
]
_BLENDER_STARTUP_TIMEOUT_SEC = 90

# Resolve the on-disk Smoothie package location from this file's path:
# tests/scripts/test_script.py → tests/scripts → tests → project root
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))
_SMOOTHIE_PKG = os.path.join(_PROJECT_ROOT, "smoothie")


def get_url(url, timeout=5):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def _port_open(port, host=SIDECAR_HOST, timeout=0.3):
    try:
        s = socket.socket()
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False


def _smoothie_is_reachable():
    """Quick check: sidecar on 8888 AND Blender API on at least one port."""
    sidecar_up = _port_open(SIDECAR_PORT)
    blender_up = any(_port_open(p) for p in BLENDER_PORTS)
    return sidecar_up and blender_up


def _find_blender_binary():
    for candidate in _BLENDER_CANDIDATES:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _launch_blender_detached(binary):
    """Launch Blender as a detached background process.

    Uses start_new_session so Blender survives the test script exiting,
    and redirects stdout/stderr to a log file so it doesn't mingle with
    the test output.
    """
    log_dir = os.path.join(_PROJECT_ROOT, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "blender_test_launch.log")
    log_fh = open(log_path, "w", buffering=1)
    print(f"  Launching Blender: {binary}")
    print(f"  Blender stdout/stderr → {log_path}")
    try:
        proc = subprocess.Popen(
            [binary],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        print(f"  pid={proc.pid}, detached")
        return proc
    except Exception as e:
        print(f"  Failed to launch Blender: {type(e).__name__}: {e}")
        return None


def _wait_for_sidecar(timeout=_BLENDER_STARTUP_TIMEOUT_SEC):
    """Poll for the sidecar and Blender API to come up."""
    print(f"  Waiting up to {timeout}s for sidecar + Blender API to come up...")
    deadline = time.time() + timeout
    poll_interval = 1.0
    last_msg = ""
    while time.time() < deadline:
        sidecar_up = _port_open(SIDECAR_PORT)
        blender_up_ports = [p for p in BLENDER_PORTS if _port_open(p)]
        if sidecar_up and blender_up_ports:
            print(f"  Ready: sidecar :{SIDECAR_PORT}, Blender API :{blender_up_ports[0]}")
            # Give the sidecar one more moment to finish serving /health
            time.sleep(0.5)
            return True
        # Progress indicator
        msg = f"    sidecar={'up' if sidecar_up else 'down'}, blender_api={'up' if blender_up_ports else 'down'}"
        if msg != last_msg:
            print(msg)
            last_msg = msg
        time.sleep(poll_interval)
    return False


def ensure_blender_running():
    """Ensure Blender + Smoothie sidecar are reachable. Launch if necessary.

    Returns True if ready to run tests, False if Blender could not be
    launched or did not come up in time.
    """
    section("PREFLIGHT: ensure Blender + Smoothie are running")

    if _smoothie_is_reachable():
        status_line("Smoothie already running", True)
        return True

    status_line("Smoothie already running", False, "neither sidecar nor Blender API reachable")

    binary = _find_blender_binary()
    if not binary:
        status_line("Blender binary found", False)
        print()
        print("  Could not locate the Blender binary. Tried:")
        for c in _BLENDER_CANDIDATES:
            if c:
                print(f"    {c}")
        print()
        print("  Either install Blender at /Applications/Blender.app,")
        print("  or set the BLENDER environment variable to the binary path")
        print("  before launching the watcher. Example:")
        print("    BLENDER=/path/to/Blender python3 tests/scripts/test_watcher.py")
        return False
    status_line("Blender binary found", True, binary)

    proc = _launch_blender_detached(binary)
    if proc is None:
        return False

    if not _wait_for_sidecar():
        status_line("Smoothie came up in time", False)
        print()
        print("  Blender launched but the Smoothie sidecar never came up.")
        print("  Possible causes:")
        print("    - The Smoothie add-on isn't enabled in Blender's preferences.")
        print("      Open Blender manually once, enable the add-on, and try again.")
        print(f"    - Startup error in Blender — check {_PROJECT_ROOT}/logs/blender_test_launch.log")
        print(f"    - Sidecar startup error — check {_PROJECT_ROOT}/logs/sidecar.log")
        return False
    status_line("Smoothie came up in time", True)
    return True


def post_blender(path, data, timeout=60):
    """POST to Blender's internal API, trying each candidate port."""
    body = json.dumps(data).encode("utf-8")
    last_err = None
    for port in BLENDER_PORTS:
        url = f"http://{SIDECAR_HOST}:{port}{path}"
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            # Blender returns 400 with a JSON error body when code execution
            # fails — read it so the caller sees the real Python traceback.
            try:
                return json.loads(e.read())
            except (json.JSONDecodeError, ValueError):
                return {"success": False, "error": f"HTTP {e.code}"}
        except (urllib.error.URLError, ConnectionRefusedError) as e:
            last_err = e
            continue
    raise RuntimeError(
        f"Could not reach Blender internal API on any of {BLENDER_PORTS}: {last_err}"
    )


def section(label):
    print()
    print("=" * 60)
    print(f"  {label}")
    print("=" * 60)


def status_line(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{tag}] {name}{suffix}")


def test_on_disk_factory():
    """The new factory.py file exists and defines build_agent_app."""
    section("ON-DISK: smoothie/sidecar/factory.py")
    factory_path = os.path.join(_SMOOTHIE_PKG, "sidecar", "factory.py")
    exists = os.path.isfile(factory_path)
    if not exists:
        status_line("factory.py exists", False, f"missing at {factory_path}")
        return False
    with open(factory_path, "r", encoding="utf-8") as f:
        content = f.read()
    has_build_agent_app = "def build_agent_app" in content
    has_get_extra_tools = "def get_extra_tools" in content
    has_get_frontend_path = "def get_frontend_path" in content
    status_line("factory.py exists", True, factory_path)
    status_line("defines build_agent_app", has_build_agent_app)
    status_line("defines get_extra_tools", has_get_extra_tools)
    status_line("defines get_frontend_path", has_get_frontend_path)
    return has_build_agent_app and has_get_extra_tools and has_get_frontend_path


def test_sidecar_frontend():
    """The baseline sidecar serves Smoothie's frontend.html at /.

    NOTE: this tests whatever version of the sidecar is currently
    running. If the sidecar was started before the Phase 2 refactor
    landed on disk, it's running pre-refactor code. That's fine for this
    test — baseline pre-refactor also serves the frontend correctly. The
    purpose here is to confirm the sidecar is UP and RESPONDING, not to
    verify which version of the code it's running.
    """
    section(f"RUNTIME: sidecar frontend at {SIDECAR_HOST}:{SIDECAR_PORT}")
    try:
        status, body = get_url(f"http://{SIDECAR_HOST}:{SIDECAR_PORT}/")
    except Exception as e:
        status_line("sidecar reachable", False, f"{type(e).__name__}: {e}")
        print(f"  Hint: is Blender running with the Smoothie add-on enabled?")
        return False

    body_text = body.decode("utf-8", errors="replace")
    body_len = len(body_text)
    has_smoothie = "Smoothie" in body_text
    has_studio_badge = "studio-badge" in body_text.lower()
    print(f"  HTTP {status}, {body_len} bytes")
    status_line("HTTP 200", status == 200)
    status_line("body > 10 KB (real frontend)", body_len > 10000)
    status_line("contains 'Smoothie'", has_smoothie)
    status_line(
        "does NOT contain studio-badge (baseline expected)",
        not has_studio_badge,
    )
    return status == 200 and body_len > 10000 and has_smoothie and not has_studio_badge


def test_blender_code_execution():
    """Blender's /api/execute still runs generated code correctly."""
    section("RUNTIME: Blender /api/execute baseline")
    try:
        result = post_blender("/api/execute", {
            "code": (
                "import bpy\n"
                "v = bpy.app.version_string\n"
                "n = len(bpy.data.objects)\n"
                "print(f'blender_version={v}')\n"
                "print(f'scene_object_count={n}')\n"
            ),
        })
    except Exception as e:
        status_line("reached Blender API", False, f"{type(e).__name__}: {e}")
        return False

    success = result.get("success", False)
    output = result.get("output", "")
    error = result.get("error", "")
    print(f"  success: {success}")
    if output:
        for line in output.strip().split("\n"):
            print(f"  | {line}")
    if error and not success:
        for line in error.strip().split("\n")[:10]:
            print(f"  ! {line}")

    has_version = "blender_version=" in output
    has_count = "scene_object_count=" in output
    status_line("execution success=True", success)
    status_line("output contains version", has_version)
    status_line("output contains object count", has_count)
    return success and has_version and has_count


def test_launcher_cache():
    """Blender's in-process smoothie.sidecar_launcher has the refactor.

    This is the one test that detects the add-on needing a reload. If
    Blender's Python imported smoothie.sidecar_launcher *before* the
    Phase 2 refactor landed on disk, the in-memory version is pre-
    refactor and does not have `_find_sidecar_module`. The user needs
    to disable/re-enable the add-on to pick up the change.
    """
    section("RUNTIME: sidecar_launcher cache check (Blender-side)")
    try:
        result = post_blender("/api/execute", {
            "code": (
                "from smoothie import sidecar_launcher as L\n"
                "has_fn = hasattr(L, '_find_sidecar_module')\n"
                "print(f'has _find_sidecar_module: {has_fn}')\n"
                "if has_fn:\n"
                "    print(f'returns: {L._find_sidecar_module()}')\n"
            ),
        })
    except Exception as e:
        status_line("reached Blender API", False, f"{type(e).__name__}: {e}")
        return False

    output = result.get("output", "")
    for line in output.strip().split("\n"):
        print(f"  | {line}")

    has_fn = "has _find_sidecar_module: True" in output
    returns_baseline = "returns: smoothie.sidecar.main" in output

    if has_fn and returns_baseline:
        status_line("launcher refactor is live in Blender", True)
        return True

    if not has_fn:
        status_line(
            "launcher refactor is live in Blender",
            False,
            "cached pre-refactor version in memory",
        )
        print()
        print("  The Blender add-on's Python has an old version of")
        print("  smoothie/sidecar_launcher.py in memory, loaded before the")
        print("  Phase 2 refactor landed on disk. To pick up the new code:")
        print()
        print("    In Blender: Edit → Preferences → Add-ons → search 'Smoothie'")
        print("    → toggle the add-on off, then on again")
        print()
        print("    Or simply restart Blender.")
        print()
        print("  After reloading, save this test_script.py again to re-run.")
        return False

    status_line(
        "launcher refactor is live in Blender",
        False,
        f"has_fn={has_fn}, returns_baseline={returns_baseline}",
    )
    return False


def main():
    print("Smoothie Phase 2 refactor validation")
    print(f"Project root: {_PROJECT_ROOT}")

    # Preflight: make sure Blender + Smoothie are running. Launches Blender
    # automatically on macOS if it's down; leaves it alone if it's already up.
    if not ensure_blender_running():
        print()
        print("  Preflight failed. Skipping runtime tests.")
        print("  On-disk checks will still run for the code-review signal.")
        print()
        # Still run the on-disk check — it's useful independent of Blender.
        on_disk = test_on_disk_factory()
        section("SUMMARY")
        status_line("on-disk factory module", on_disk)
        print("  (runtime tests skipped — Blender unavailable)")
        return 1

    results = [
        ("on-disk factory module", test_on_disk_factory()),
        ("sidecar frontend serving", test_sidecar_frontend()),
        ("Blender code execution", test_blender_code_execution()),
        ("launcher refactor live in Blender", test_launcher_cache()),
    ]

    section("SUMMARY")
    for name, ok in results:
        status_line(name, ok)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print()
    print(f"  {passed}/{total} checks passed")

    if passed == total:
        print()
        print("  All Phase 2 checks passed. The refactor is safe to commit.")
        return 0

    print()
    print(f"  {total - passed} check(s) failed. See section output above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
