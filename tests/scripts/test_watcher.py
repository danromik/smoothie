#!/usr/bin/env python3
"""Watch test_script.py for changes and run it automatically.

Usage:
    python3 test_watcher.py

Watches test_script.py in the same directory. On each change:
  1. Runs test_script.py
  2. Writes combined stdout+stderr to test_results.txt
  3. Prints a summary to the terminal

The results file is in the same directory so Docker Claude can read it.
Press Ctrl+C to stop.
"""

import os
import subprocess
import sys
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_RESULTS_DIR = os.path.join(os.path.dirname(_SCRIPT_DIR), "results")
os.makedirs(_RESULTS_DIR, exist_ok=True)

WATCH_FILE = os.path.join(_SCRIPT_DIR, "test_script.py")
RESULTS_FILE = os.path.join(_RESULTS_DIR, "test_results.txt")
POLL_INTERVAL = 1.0  # seconds


def get_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def run_test():
    print(f"\n{'─'*60}")
    print(f"[{time.strftime('%H:%M:%S')}] test_script.py changed — running...")
    print(f"{'─'*60}")

    start = time.time()
    try:
        result = subprocess.run(
            [sys.executable, WATCH_FILE],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=os.path.dirname(WATCH_FILE),
        )
        output = result.stdout
        if result.stderr:
            output += f"\n--- stderr ---\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n--- exit code: {result.returncode} ---"
    except subprocess.TimeoutExpired:
        output = "ERROR: Test script timed out after 600 seconds"
    except Exception as e:
        output = f"ERROR: Failed to run test script: {e}"

    elapsed = time.time() - start

    # Write results
    header = f"# Test run: {time.strftime('%Y-%m-%d %H:%M:%S')} ({elapsed:.1f}s)\n\n"
    with open(RESULTS_FILE, "w") as f:
        f.write(header + output)

    # Print summary
    lines = output.strip().split("\n")
    print(f"Completed in {elapsed:.1f}s — {len(lines)} lines of output")
    # Show last 20 lines as preview
    preview = lines[-20:] if len(lines) > 20 else lines
    for line in preview:
        print(f"  {line}")
    print(f"\nFull results: {RESULTS_FILE}")


def main():
    print(f"Watching: {WATCH_FILE}")
    print(f"Results:  {RESULTS_FILE}")
    print(f"Polling every {POLL_INTERVAL}s — press Ctrl+C to stop\n")

    if not os.path.exists(WATCH_FILE):
        print(f"WARNING: {WATCH_FILE} does not exist yet. Waiting for it to appear...")

    last_mtime = get_mtime(WATCH_FILE)

    # Run once immediately if file exists
    if last_mtime > 0:
        run_test()
        last_mtime = get_mtime(WATCH_FILE)

    while True:
        try:
            time.sleep(POLL_INTERVAL)
            mtime = get_mtime(WATCH_FILE)
            if mtime > last_mtime:
                last_mtime = mtime
                run_test()
                # Re-check mtime after run in case the file changed during execution
                last_mtime = get_mtime(WATCH_FILE)
        except KeyboardInterrupt:
            print("\nStopped.")
            break


if __name__ == "__main__":
    main()
