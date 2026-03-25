#!/usr/bin/env python3
"""Smoothie sidecar API integration test.

Three-phase test:
  Phase 1: Chat-only prompt (no code expected)
  Phase 2: Code generation + execution (create a ball)
  Phase 3: Animation code generation + execution (ball jumps over cube)

Automatically launches Blender if the sidecar isn't running.
Run 5: Added _fix_blender5_compat() to strip action.fcurves at runtime.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

SIDECAR = "http://127.0.0.1:8888"
BLENDER_PORT = 8889
TIMEOUT = 90

# Blender binary — try common macOS locations
BLENDER_CANDIDATES = [
    "/Applications/Blender.app/Contents/MacOS/Blender",
    "/Applications/Blender 5.1.app/Contents/MacOS/Blender",
    os.path.expanduser("~/Applications/Blender.app/Contents/MacOS/Blender"),
]

_blender_proc = None  # Track Blender process so we can clean up


# ── Blender Lifecycle ────────────────────────────────────────

def find_blender():
    """Find the Blender binary."""
    for path in BLENDER_CANDIDATES:
        if os.path.isfile(path):
            return path
    # Try PATH
    import shutil
    return shutil.which("blender")


def is_sidecar_up():
    """Check if the sidecar is responding."""
    try:
        req = urllib.request.Request(f"{SIDECAR}/api/health")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def is_blender_api_up():
    """Check if Blender's internal API is responding."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{BLENDER_PORT}/api/status")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_blender_running():
    """Launch Blender if sidecar + Blender API aren't both up."""
    global _blender_proc

    if is_sidecar_up() and is_blender_api_up():
        print("Sidecar and Blender API already running.")
        return True

    blender = find_blender()
    if not blender:
        print("ERROR: Cannot find Blender binary. Tried:")
        for p in BLENDER_CANDIDATES:
            print(f"  {p}")
        return False

    print(f"Launching Blender: {blender}")
    print("  (background mode with add-on enabled)")

    # Launch Blender in background with a Python script that enables the add-on
    # and keeps it running for the duration of the test.
    startup_script = _create_startup_script()

    # Launch Blender with GUI (not --background) so the event loop runs
    # and timer callbacks fire. The startup script just enables the add-on.
    _blender_proc = subprocess.Popen(
        [blender, "--python", startup_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for both sidecar and Blender API to come up
    print("  Waiting for sidecar + Blender API", end="", flush=True)
    for i in range(60):  # up to 60 seconds
        time.sleep(1)
        print(".", end="", flush=True)
        if is_sidecar_up() and is_blender_api_up():
            print(" UP!")
            return True
        # Check if Blender crashed
        if _blender_proc.poll() is not None:
            stdout = _blender_proc.stdout.read().decode(errors="replace")[-500:]
            stderr = _blender_proc.stderr.read().decode(errors="replace")[-500:]
            print(f"\nERROR: Blender exited with code {_blender_proc.returncode}")
            if stdout:
                print(f"  stdout: {stdout}")
            if stderr:
                print(f"  stderr: {stderr}")
            _blender_proc = None
            return False

    print("\nERROR: Sidecar/Blender did not start within 60 seconds")
    print(f"  Sidecar up: {is_sidecar_up()}, Blender API up: {is_blender_api_up()}")
    stop_blender()
    return False


def _create_startup_script():
    """Create a temporary Python script that Blender runs to enable the add-on and wait."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(script_dir, "_blender_startup.py")

    with open(script_path, "w") as f:
        f.write("""\
import bpy

# Enable the Smoothie add-on
bpy.ops.preferences.addon_enable(module="smoothie")
print("Smoothie test: Add-on enabled, Blender running.")
""")
    return script_path


def stop_blender():
    """Gracefully shut down sidecar, then stop Blender."""
    global _blender_proc

    # First, ask the sidecar to shut itself down gracefully
    if is_sidecar_up():
        print("Shutting down sidecar via API...")
        try:
            api("POST", "/api/shutdown")
            # Wait for it to actually exit
            for _ in range(10):
                time.sleep(0.5)
                if not is_sidecar_up():
                    break
        except Exception:
            pass

    # Then stop Blender
    if _blender_proc is not None:
        print("Stopping Blender...")
        _blender_proc.terminate()
        try:
            _blender_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _blender_proc.kill()
        _blender_proc = None


def cleanup():
    """Clean up on exit."""
    stop_blender()
    # Remove temp files
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for f in ("_blender_startup.py",):
        path = os.path.join(script_dir, f)
        if os.path.exists(path):
            os.remove(path)


# ── API Helpers ──────────────────────────────────────────────

def api(method, path, body=None):
    """Make an HTTP request to the sidecar. Returns (status, parsed_json)."""
    url = f"{SIDECAR}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            resp_body = json.loads(e.read())
        except Exception:
            resp_body = {"raw": e.reason}
        return e.code, resp_body
    except Exception as e:
        return 0, {"error": str(e)}


def send_and_wait(prompt, timeout=TIMEOUT):
    """Send a prompt and wait for the full response via SSE."""
    status, data = api("POST", "/api/send", {"prompt": prompt})
    if status != 200:
        return {"error": f"send failed ({status}): {data}", "done": False,
                "events": [], "text": "", "code": "", "post_message": ""}

    session_id = data["session_id"]
    url = f"{SIDECAR}/api/stream/{session_id}"
    req = urllib.request.Request(url)

    result = {
        "events": [],
        "text": "",
        "code": "",
        "post_message": "",
        "error": None,
        "done": False,
    }

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buf = ""
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace")
                buf += line
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    event_type, event_data = _parse_sse_block(block)
                    if event_type is None:
                        continue
                    result["events"].append({"type": event_type, "data": event_data})

                    if event_type == "text_delta":
                        result["text"] += event_data.get("text", "")
                    elif event_type == "tool_complete":
                        result["code"] = event_data.get("code", "")
                        result["post_message"] = event_data.get("post_message", "")
                    elif event_type == "error":
                        result["error"] = event_data.get("message", str(event_data))
                        result["done"] = True
                        return result
                    elif event_type == "done":
                        result["done"] = True
                        return result
    except Exception as e:
        result["error"] = f"SSE stream error: {e}"

    return result


def _parse_sse_block(block):
    event_type = None
    data_parts = []
    for line in block.strip().split("\n"):
        if line.startswith("event: "):
            event_type = line[7:].strip()
        elif line.startswith("data: "):
            data_parts.append(line[6:])
        elif line.startswith(":"):
            continue
    if not data_parts:
        return None, None
    try:
        data = json.loads("".join(data_parts))
    except json.JSONDecodeError:
        data = {"raw": "".join(data_parts)}
    return event_type or "message", data


def execute_code(code):
    status, data = api("POST", "/api/execute", {"code": code})
    return data


def clear_chat():
    _, data = api("POST", "/api/clear")
    return data


def health():
    _, data = api("GET", "/api/health")
    return data


def get_messages():
    _, data = api("GET", "/api/messages")
    return data


def scene():
    _, data = api("GET", "/api/scene")
    return data


# ── Reporting ────────────────────────────────────────────────

class TestReport:
    def __init__(self):
        self.lines = []
        self.phases = []
        self.current_phase = None

    def header(self, text):
        line = f"\n{'='*60}\n  {text}\n{'='*60}"
        self.lines.append(line)
        print(line)

    def log(self, text):
        self.lines.append(text)
        print(text)

    def phase_start(self, name):
        self.current_phase = {"name": name, "status": "RUNNING", "notes": []}
        self.phases.append(self.current_phase)
        self.header(f"PHASE: {name}")

    def phase_pass(self, note=""):
        if self.current_phase:
            self.current_phase["status"] = "PASS"
            if note:
                self.current_phase["notes"].append(note)
        self.log(f"  PASS{': ' + note if note else ''}")

    def phase_fail(self, note=""):
        if self.current_phase:
            self.current_phase["status"] = "FAIL"
            if note:
                self.current_phase["notes"].append(note)
        self.log(f"  FAIL{': ' + note if note else ''}")

    def phase_warn(self, note):
        if self.current_phase:
            self.current_phase["notes"].append(f"WARN: {note}")
        self.log(f"  WARN: {note}")

    def detail(self, label, value):
        if isinstance(value, dict):
            text = json.dumps(value, indent=2, default=str)
        else:
            text = str(value)
        if len(text) > 1500:
            text = text[:1500] + "\n  ... (truncated)"
        self.log(f"  {label}: {text}")

    def summary(self):
        self.header("TEST SUMMARY")
        for p in self.phases:
            status = "PASS" if p["status"] == "PASS" else "FAIL" if p["status"] == "FAIL" else "..."
            self.log(f"  [{status}] {p['name']}")
            for note in p["notes"]:
                self.log(f"        {note}")
        passed = sum(1 for p in self.phases if p["status"] == "PASS")
        total = len(self.phases)
        self.log(f"\n  Result: {passed}/{total} phases passed")
        return self.full_text()

    def full_text(self):
        return "\n".join(self.lines)


# ── Test Phases ──────────────────────────────────────────────

def test():
    report = TestReport()
    report.header("Smoothie Integration Test")
    report.log(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # Ensure Blender + sidecar are running
    if not ensure_blender_running():
        report.log("ABORT: Could not start Blender/sidecar")
        return report.full_text()

    # Verify health
    h = health()
    report.detail("Health", h)

    blender_ok = isinstance(h.get("blender"), dict) and h["blender"].get("status") != "unavailable"
    if not blender_ok:
        report.log("ABORT: Blender API not reachable")
        return report.full_text()

    # Clear state
    clear_chat()
    report.log("Chat cleared.\n")

    # Get initial scene
    s = scene()
    report.detail("Initial scene", s)

    # ── Phase 1: Chat-only prompt ────────────────────────────
    report.phase_start("Chat-only prompt (no code)")
    prompt1 = "Describe what's in the scene right now. Don't generate any code."
    report.detail("Prompt", prompt1)

    t0 = time.time()
    r1 = send_and_wait(prompt1)
    elapsed1 = time.time() - t0

    report.detail("Done", r1["done"])
    report.detail("Response time", f"{elapsed1:.1f}s")
    report.detail("Text length", len(r1["text"]))
    report.detail("Has code", bool(r1["code"]))
    report.detail("Error", r1["error"])
    report.detail("Event types", list(set(e["type"] for e in r1["events"])))

    if r1["text"]:
        report.detail("Assistant reply", r1["text"][:800])

    if r1["error"]:
        report.phase_fail(f"Error: {r1['error']}")
    elif not r1["done"]:
        report.phase_fail("Stream did not complete")
    elif r1["code"]:
        report.phase_warn("Code was generated for a chat-only prompt (unexpected but not fatal)")
        report.phase_pass("Response received")
    elif len(r1["text"]) < 10:
        report.phase_fail(f"Response too short: {r1['text']!r}")
    else:
        report.phase_pass(f"Got {len(r1['text'])} chars of text, no code")

    # ── Phase 2: Code generation + execution ─────────────────
    report.phase_start("Code generation + execution (create ball)")
    prompt2 = "Make me a ball and place it next to the cube"
    report.detail("Prompt", prompt2)

    t0 = time.time()
    r2 = send_and_wait(prompt2)
    elapsed2 = time.time() - t0

    report.detail("Done", r2["done"])
    report.detail("Response time", f"{elapsed2:.1f}s")
    report.detail("Text length", len(r2["text"]))
    report.detail("Code length", len(r2["code"]))
    report.detail("Post message", r2["post_message"])
    report.detail("Error", r2["error"])

    if r2["text"]:
        report.detail("Assistant text", r2["text"][:500])

    if r2["error"]:
        report.phase_fail(f"Error: {r2['error']}")
    elif not r2["code"]:
        report.phase_fail("No code generated")
    else:
        report.detail("Generated code", r2["code"])

        report.log("\n  Executing code in Blender...")
        exec_result = execute_code(r2["code"])
        report.detail("Execution result", exec_result)

        if exec_result.get("success"):
            report.phase_pass(f"Code executed successfully ({len(r2['code'])} bytes)")
            s2 = scene()
            report.detail("Scene after execution", s2)
        else:
            error_msg = exec_result.get("error", "Unknown error")
            report.phase_fail(f"Execution failed: {error_msg[:300]}")
            report.detail("Full error", error_msg)

    # ── Phase 3: Animation code generation + execution ───────
    report.phase_start("Animation code gen + execution (ball jumps over cube)")
    prompt3 = "Make an animation where the ball jumps over the cube"
    report.detail("Prompt", prompt3)

    t0 = time.time()
    r3 = send_and_wait(prompt3)
    elapsed3 = time.time() - t0

    report.detail("Done", r3["done"])
    report.detail("Response time", f"{elapsed3:.1f}s")
    report.detail("Text length", len(r3["text"]))
    report.detail("Code length", len(r3["code"]))
    report.detail("Post message", r3["post_message"])
    report.detail("Error", r3["error"])

    if r3["text"]:
        report.detail("Assistant text", r3["text"][:500])

    if r3["error"]:
        report.phase_fail(f"Error: {r3['error']}")
    elif not r3["code"]:
        report.phase_fail("No code generated")
    else:
        report.detail("Generated code", r3["code"])

        report.log("\n  Executing animation code in Blender...")
        exec_result = execute_code(r3["code"])
        report.detail("Execution result", exec_result)

        if exec_result.get("success"):
            report.phase_pass(f"Animation code executed ({len(r3['code'])} bytes)")
            s3 = scene()
            report.detail("Scene after animation", s3)
        else:
            error_msg = exec_result.get("error", "Unknown error")
            report.phase_fail(f"Execution failed: {error_msg[:300]}")
            report.detail("Full error", error_msg)

    # ── Final conversation state ─────────────────────────────
    report.header("Final Conversation State")
    msgs = get_messages()
    report.log(f"  Total messages: {len(msgs)}")
    for m in msgs:
        role = m.get("role", "?")
        content = (m.get("content", "") or "")[:120]
        has_code = m.get("has_code", False)
        report.log(f"    [{role}] {content}{'  [CODE]' if has_code else ''}")

    # ── Summary ──────────────────────────────────────────────
    full_report = report.summary()
    return full_report


if __name__ == "__main__":
    try:
        report_text = test()
        # Save dated report
        results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
        os.makedirs(results_dir, exist_ok=True)
        date_str = time.strftime("%Y-%m-%d_%H%M%S")
        report_path = os.path.join(results_dir, f"integration_test_{date_str}.txt")
        with open(report_path, "w") as f:
            f.write(report_text)
        print(f"\nReport saved: {report_path}")
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except Exception as e:
        print(f"\nFATAL: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # Leave Blender running so the user can inspect the results
        # and continue interacting via the browser UI.
        print("\nBlender left running — open http://localhost:8888 to interact.")
        print("To shut down: POST http://localhost:8888/api/shutdown")
