"""Launch and manage the sidecar subprocess."""
import subprocess
import shutil
import os
import logging
import threading

logger = logging.getLogger("smoothie.sidecar_launcher")

_process: subprocess.Popen | None = None
_sidecar_port: int | None = None


def start_sidecar(blender_port: int, sidecar_port: int = 8888) -> int | None:
    """Start the sidecar process. Returns the port or None on failure."""
    global _process, _sidecar_port

    if _process is not None and _process.poll() is None:
        logger.info("Sidecar already running (pid=%d)", _process.pid)
        return _sidecar_port

    # Kill any orphaned process holding our port (e.g. from a crashed session)
    _kill_port_holder(sidecar_port)

    python = _find_system_python()
    if not python:
        logger.error("Cannot find system Python with claude_agent_sdk installed")
        return None

    # The smoothie package dir (parent of this file)
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    # Parent dir containing the smoothie package
    pkg_parent = os.path.dirname(pkg_dir)

    cmd = [
        python, "-m", "smoothie.sidecar.main",
        "--port", str(sidecar_port),
        "--blender-port", str(blender_port),
    ]

    env = dict(os.environ)
    # Ensure the smoothie package is importable by the sidecar
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = pkg_parent + (os.pathsep + existing_pythonpath if existing_pythonpath else "")

    try:
        # stdout/stderr must NOT be PIPE: the sidecar writes to these via
        # Python logging, and nothing reads the pipes until the process
        # exits. On macOS the ~64KB pipe buffer fills quickly, which blocks
        # the next log write and freezes uvicorn's event loop. The sidecar
        # already logs to logs/sidecar.log, so DEVNULL is fine here.
        _process = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _sidecar_port = sidecar_port

        # Monitor in background
        monitor = threading.Thread(target=_monitor_sidecar, daemon=True, name="smoothie-sidecar-monitor")
        monitor.start()

        logger.info("Sidecar started (pid=%d) on port %d using Python: %s", _process.pid, sidecar_port, python)
        return sidecar_port
    except Exception as e:
        logger.error("Failed to start sidecar: %s", e)
        _process = None
        return None


def stop_sidecar():
    """Stop the sidecar process."""
    global _process, _sidecar_port
    if _process is not None:
        logger.info("Stopping sidecar (pid=%d)", _process.pid)
        _process.terminate()
        try:
            _process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _process.kill()
        _process = None
        _sidecar_port = None


def is_running() -> bool:
    """Check if the sidecar is running."""
    return _process is not None and _process.poll() is None


def get_port() -> int | None:
    """Return the sidecar port, or None if not running."""
    if is_running():
        return _sidecar_port
    return None


def _find_system_python() -> str | None:
    """Find a system Python 3.10+ with claude_agent_sdk installed.

    Blender on macOS strips PATH to a minimal set, so shutil.which() often
    fails to find homebrew/pyenv/system Pythons. We search explicit absolute
    paths in addition to PATH-based lookup.
    """
    import platform

    # Resolve the real on-disk location of this file (follow symlinks).
    pkg_dir = os.path.dirname(os.path.realpath(__file__))

    candidates = []

    # Highest priority: venv_path.txt written by install.py. This is the
    # only reliable way to find the venv when the add-on was copied into
    # Blender's addons folder (rather than symlinked), because in that
    # case there's no .venv alongside the copied package.
    venv_config = os.path.join(pkg_dir, "venv_path.txt")
    if os.path.isfile(venv_config):
        try:
            with open(venv_config, "r") as f:
                configured = f.read().strip()
            if configured and os.path.isfile(configured):
                candidates.append(configured)
                logger.info("Using Python from venv_path.txt: %s", configured)
            else:
                logger.warning("venv_path.txt points to non-existent file: %s", configured)
        except Exception as e:
            logger.warning("Failed to read venv_path.txt: %s", e)

    # Next: look for a .venv alongside the smoothie/ package (classic
    # symlinked-dev-install layout).
    project_root = os.path.dirname(pkg_dir)
    venv_python = os.path.join(project_root, ".venv", "bin", "python3")
    if platform.system() == "Windows":
        venv_python = os.path.join(project_root, ".venv", "Scripts", "python.exe")
    if os.path.isfile(venv_python):
        candidates.append(venv_python)

    # Finally: fall back to any system Python that has the SDK installed.
    candidates.extend(["python3", "python3.13", "python3.12", "python3.11"])

    if platform.system() == "Darwin":
        # Homebrew (Apple Silicon and Intel)
        candidates.extend([
            "/opt/homebrew/bin/python3",
            "/opt/homebrew/bin/python3.13",
            "/opt/homebrew/bin/python3.12",
            "/opt/homebrew/bin/python3.11",
            "/usr/local/bin/python3",
            "/usr/local/bin/python3.13",
            "/usr/local/bin/python3.12",
            "/usr/local/bin/python3.11",
        ])
        # pyenv (common default location)
        home = os.path.expanduser("~")
        pyenv_root = os.environ.get("PYENV_ROOT", os.path.join(home, ".pyenv"))
        pyenv_shims = os.path.join(pyenv_root, "shims", "python3")
        if os.path.isfile(pyenv_shims):
            candidates.append(pyenv_shims)
        # Framework Python (python.org installer)
        for ver in ("3.13", "3.12", "3.11"):
            candidates.append(f"/Library/Frameworks/Python.framework/Versions/{ver}/bin/python3")
        # Also try to recover the user's real PATH from their shell
        candidates.extend(_get_shell_python_paths())
    elif platform.system() == "Windows":
        candidates.extend(["python", "py"])
    else:
        candidates.extend(["/usr/bin/python3", "/usr/local/bin/python3"])

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    tried = []
    for candidate in unique:
        # Resolve PATH-based names
        path = shutil.which(candidate) if not os.path.isabs(candidate) else candidate
        if not path or not os.path.isfile(path):
            continue

        try:
            result = subprocess.run(
                [path, "-c", "import claude_agent_sdk; print('ok')"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and "ok" in result.stdout:
                logger.info("Found system Python with SDK: %s", path)
                return path
            else:
                reason = result.stderr.strip().split("\n")[-1] if result.stderr else f"exit code {result.returncode}"
                tried.append(f"{path}: {reason}")
        except Exception as e:
            tried.append(f"{path}: {e}")

    logger.error("No system Python found with claude_agent_sdk. Tried:\n  %s", "\n  ".join(tried) if tried else "(no python found on PATH or known locations)")
    return None


def _get_shell_python_paths() -> list[str]:
    """Try to get python3 paths from the user's login shell PATH.

    Blender on macOS doesn't inherit the user's full shell PATH, so we
    ask the shell for it.
    """
    shell = os.environ.get("SHELL", "/bin/zsh")
    try:
        result = subprocess.run(
            [shell, "-l", "-c", "which python3"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return [result.stdout.strip()]
    except Exception:
        pass
    return []


def _kill_port_holder(port: int):
    """Kill any process listening on the given port (handles orphaned sidecars)."""
    import platform
    if platform.system() != "Darwin" and platform.system() != "Linux":
        return
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            for pid_str in pids:
                pid = int(pid_str.strip())
                logger.warning("Killing orphaned process on port %d (pid=%d)", port, pid)
                try:
                    os.kill(pid, 15)  # SIGTERM
                except ProcessLookupError:
                    pass
            import time
            time.sleep(0.5)  # Brief wait for port to free up
    except Exception as e:
        logger.debug("Port check failed: %s", e)


def _monitor_sidecar():
    """Watch sidecar process and log if it exits."""
    global _process
    if _process is None:
        return
    proc = _process
    returncode = proc.wait()
    if returncode != 0:
        # Read tail of sidecar.log for diagnostics (stdout/stderr are DEVNULL).
        tail = ""
        try:
            pkg_dir = os.path.dirname(os.path.realpath(__file__))
            project_root = os.path.dirname(pkg_dir)
            log_file = os.path.join(project_root, "logs", "sidecar.log")
            if os.path.isfile(log_file):
                with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 2000))
                    tail = f.read()
        except Exception:
            pass
        logger.error("Sidecar exited with code %d. Last log lines:\n%s", returncode, tail)
    else:
        logger.info("Sidecar exited normally")
