#!/usr/bin/env python3
"""Smoothie installer — one-command setup for macOS, Linux, and Windows.

Installs Smoothie's Python sidecar environment, the Claude Code CLI, and the
Blender add-on itself. Cross-platform: detects the OS and uses the right
paths and install methods. Checks prerequisites up front and gives actionable
error messages if anything is missing.

Usage:
    python3 install.py [options]

Options:
    --symlink              Symlink the add-on instead of copying (for devs)
    --force                Overwrite an existing add-on install without asking
    --quiet                Run non-interactively with sensible defaults
    --blender-version VER  Target a specific Blender version (e.g. "5.1")
    -h, --help             Show this help message

If this script fails on your setup, see the "Manual installation" section
in README.md for the equivalent step-by-step instructions.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


# ---------- terminal formatting ----------

def _supports_color():
    if not hasattr(sys.stdout, 'isatty') or not sys.stdout.isatty():
        return False
    if os.environ.get('TERM') == 'dumb':
        return False
    if platform.system() == 'Windows':
        # Modern Windows Terminal / ConEmu / ANSICON support ANSI colors
        return 'WT_SESSION' in os.environ or 'ANSICON' in os.environ
    return True


if _supports_color():
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
else:
    RESET = BOLD = DIM = RED = GREEN = YELLOW = ''


def ok(msg):
    print("  {}OK{} {}".format(GREEN, RESET, msg))


def fail(msg):
    print("  {}X{}  {}".format(RED, RESET, msg))


def warn(msg):
    print("  {}!{}  {}".format(YELLOW, RESET, msg))


def info(msg):
    print("  " + msg)


def step(msg):
    print()
    print("{}{}{}".format(BOLD, msg, RESET))


# ---------- project root & command helpers ----------

def find_project_root():
    """Locate the directory containing the smoothie/ package."""
    script_dir = Path(__file__).resolve().parent
    if (script_dir / 'smoothie' / '__init__.py').exists():
        return script_dir
    cwd = Path.cwd()
    if (cwd / 'smoothie' / '__init__.py').exists():
        return cwd
    return None


def run_npm(args_list, **kwargs):
    """Run `npm <args>`. On Windows, npm is npm.cmd which needs shell=True."""
    if platform.system() == 'Windows':
        cmd_str = 'npm ' + ' '.join(args_list)
        return subprocess.run(cmd_str, shell=True, **kwargs)
    return subprocess.run(['npm'] + args_list, **kwargs)


def get_cmd_version(cmd, version_flag='--version'):
    """Return the trimmed stdout of `cmd <version_flag>`, or None on failure."""
    path = shutil.which(cmd)
    if not path:
        return None
    try:
        result = subprocess.run(
            [path, version_flag],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def get_npm_version():
    try:
        result = run_npm(['--version'], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


# ---------- prerequisites ----------

def check_prerequisites():
    all_ok = True

    py = sys.version_info
    py_str = "{}.{}.{}".format(py.major, py.minor, py.micro)
    if py >= (3, 10):
        ok("Python {} ({})".format(py_str, sys.executable))
    else:
        fail("Python {} is too old — Smoothie requires Python 3.10 or later.".format(py_str))
        info("    Get the latest Python from https://www.python.org/downloads/")
        all_ok = False

    node_ver = get_cmd_version('node')
    if node_ver:
        ok("Node.js {}".format(node_ver))
    else:
        fail("Node.js not found.")
        info("    Install Node.js (includes npm) from https://nodejs.org/")
        all_ok = False

    if node_ver:
        npm_ver = get_npm_version()
        if npm_ver:
            ok("npm {}".format(npm_ver))
        else:
            fail("npm not found. It should come bundled with Node.js.")
            info("    Try reinstalling Node.js from https://nodejs.org/")
            all_ok = False

    return all_ok


# ---------- Blender detection ----------

def blender_config_root():
    """Return Blender's per-user config root for the current OS, or None."""
    home = Path.home()
    system = platform.system()
    if system == 'Darwin':
        return home / 'Library' / 'Application Support' / 'Blender'
    if system == 'Linux':
        return home / '.config' / 'blender'
    if system == 'Windows':
        appdata = os.environ.get('APPDATA')
        return Path(appdata) / 'Blender Foundation' / 'Blender' if appdata else None
    return None


def detect_blender_versions():
    """Return {version_str: addons_dir_path} for installed Blender 5.x versions."""
    root = blender_config_root()
    if root is None or not root.exists():
        return {}

    versions = {}
    for child in root.iterdir():
        if not child.is_dir():
            continue
        version_str = child.name
        parts = version_str.split('.')
        if not all(p.isdigit() for p in parts):
            continue
        try:
            major = int(parts[0])
        except (ValueError, IndexError):
            continue
        if major < 5:
            continue
        versions[version_str] = child / 'scripts' / 'addons'
    return versions


def version_tuple(v):
    try:
        return tuple(int(p) for p in v.split('.'))
    except ValueError:
        return (0,)


def select_blender_version(versions, args):
    if args.blender_version:
        if args.blender_version in versions:
            info("Target: Blender {} (from --blender-version)".format(args.blender_version))
            return args.blender_version
        fail("Blender version {} not found.".format(args.blender_version))
        info("    Available: {}".format(', '.join(sorted(versions.keys()))))
        sys.exit(1)

    sorted_versions = sorted(versions.keys(), key=version_tuple, reverse=True)

    if len(sorted_versions) == 1:
        v = sorted_versions[0]
        ok("Blender {} detected".format(v))
        return v

    if args.quiet:
        v = sorted_versions[0]
        ok("Multiple Blender versions found; using highest ({})".format(v))
        return v

    print()
    print("  Multiple Blender versions detected:")
    for i, v in enumerate(sorted_versions):
        print("    {}) Blender {}".format(i + 1, v))
    print()
    while True:
        try:
            choice = input("  Select a version [1-{}, default=1]: ".format(len(sorted_versions))).strip()
        except EOFError:
            return sorted_versions[0]
        if not choice:
            return sorted_versions[0]
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(sorted_versions):
                return sorted_versions[idx]
        except ValueError:
            pass
        warn("Invalid choice, please try again.")


# ---------- install steps ----------

def create_venv(project_root):
    venv_path = project_root / '.venv'
    if venv_path.exists():
        ok(".venv already exists at {}".format(venv_path))
        return venv_path

    info("Creating virtual environment at {} ...".format(venv_path))
    result = subprocess.run([sys.executable, '-m', 'venv', str(venv_path)])
    if result.returncode != 0:
        fail("Failed to create virtual environment.")
        sys.exit(1)
    ok(".venv created")
    return venv_path


def venv_pip_path(venv_path):
    if platform.system() == 'Windows':
        return venv_path / 'Scripts' / 'pip.exe'
    return venv_path / 'bin' / 'pip'


def venv_python_path(venv_path):
    if platform.system() == 'Windows':
        return venv_path / 'Scripts' / 'python.exe'
    return venv_path / 'bin' / 'python3'


def install_sdk(project_root):
    venv_path = project_root / '.venv'
    pip = venv_pip_path(venv_path)
    if not pip.exists():
        fail("pip not found at {}".format(pip))
        sys.exit(1)

    info("Installing claude-agent-sdk into venv (pip will skip if up to date) ...")
    result = subprocess.run([str(pip), 'install', 'claude-agent-sdk'])
    if result.returncode != 0:
        fail("Failed to install claude-agent-sdk.")
        sys.exit(1)
    ok("claude-agent-sdk ready in .venv")


def write_venv_config(project_root):
    """Write smoothie/venv_path.txt so sidecar_launcher can find the venv
    reliably, regardless of whether the add-on is symlinked or copied.

    NB: we deliberately do NOT resolve the final symlink. On macOS/Linux,
    .venv/bin/python3 is itself a symlink to the base Python interpreter,
    and Python uses sys.executable (the symlink path) to locate pyvenv.cfg
    and activate the venv. Resolving through that symlink would give us
    the base Python, which doesn't know about the venv's site-packages.
    """
    venv_py = venv_python_path(project_root / '.venv')
    if not venv_py.exists():
        fail("Expected venv Python at {} but it's missing.".format(venv_py))
        sys.exit(1)
    # Use an absolute path for project_root, but keep the venv/bin/python3
    # symlink unresolved.
    abs_venv_py = (project_root.resolve() / venv_py.relative_to(project_root))
    config_file = project_root / 'smoothie' / 'venv_path.txt'
    config_file.write_text(str(abs_venv_py) + "\n")
    ok("Wrote venv pointer: smoothie/venv_path.txt")


def install_claude_cli():
    existing = shutil.which('claude')
    if existing:
        ok("Claude Code CLI already installed ({})".format(existing))
        return

    info("Running: npm install -g @anthropic-ai/claude-code")
    result = run_npm(['install', '-g', '@anthropic-ai/claude-code'])
    if result.returncode != 0:
        fail("Failed to install Claude Code CLI via npm.")
        warn("If this was a permission error (EACCES), try one of:")
        info("      * sudo python3 install.py")
        info("      * Configure an npm prefix in your home directory:")
        info("        https://docs.npmjs.com/resolving-eacces-permissions-errors")
        sys.exit(1)
    ok("Claude Code CLI installed")


def install_addon(project_root, addons_dir, use_symlink, force):
    addons_dir.mkdir(parents=True, exist_ok=True)
    target = addons_dir / 'smoothie'
    source = (project_root / 'smoothie').resolve()

    if target.exists() or target.is_symlink():
        if not force:
            warn("Existing installation at: {}".format(target))
            try:
                choice = input("  Overwrite? [y/N]: ").strip().lower()
            except EOFError:
                choice = 'n'
            if choice not in ('y', 'yes'):
                info("Skipped add-on installation (existing install preserved).")
                return
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(str(target))

    if use_symlink:
        try:
            target.symlink_to(source, target_is_directory=True)
            ok("Symlinked: {} -> {}".format(target, source))
            return
        except OSError as e:
            warn("Symlink failed ({}); falling back to copy.".format(e))

    shutil.copytree(str(source), str(target))
    ok("Copied add-on to: {}".format(target))


# ---------- banner and next steps ----------

def print_banner():
    print()
    print("{}Smoothie Installer{}".format(BOLD, RESET))
    print("{}AI-powered animation for Blender{}".format(DIM, RESET))


def print_next_steps(target_version):
    print()
    print("{}{}Installation complete!{}".format(BOLD, GREEN, RESET))
    print()
    print("{}Two manual steps remain:{}".format(BOLD, RESET))
    print()
    print("  {}1) Authenticate with Claude.{} Choose ONE of:".format(BOLD, RESET))
    print()
    print("     {}a) Claude subscription{} (if you have Pro or Max) — run:".format(BOLD, RESET))
    print("        {}claude login{}".format(BOLD, RESET))
    print("        A browser opens; sign in with your Claude account.")
    print()
    print("     {}b) Anthropic API key{} (pay-per-use) — get one at:".format(BOLD, RESET))
    print("        https://console.anthropic.com/")
    print("        Paste it into Smoothie's Settings after launching the add-on.")
    print()
    print("  {}2) Enable the add-on in Blender {}:{}".format(BOLD, target_version, RESET))
    print("     Edit > Preferences > Add-ons > search 'Smoothie' > tick the checkbox")
    print()
    print("Then in the 3D viewport, press {}N{} to open the sidebar, find the".format(BOLD, RESET))
    print("'Smoothie' tab, and click 'Open Chat in Browser'.")
    print()


# ---------- main ----------

def parse_args():
    parser = argparse.ArgumentParser(
        description='Install Smoothie into Blender.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="See README.md for manual installation steps if this script fails.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--symlink', action='store_true',
                      help='Symlink the add-on into Blender (default on macOS/Linux)')
    mode.add_argument('--copy', action='store_true',
                      help='Copy the add-on into Blender (default on Windows)')
    parser.add_argument('--force', action='store_true',
                        help='Overwrite existing add-on installation without asking')
    parser.add_argument('--quiet', action='store_true',
                        help='Run non-interactively with sensible defaults')
    parser.add_argument('--blender-version', type=str, default=None,
                        help='Target a specific Blender version (e.g. "5.1")')
    return parser.parse_args()


def resolve_install_mode(args):
    """Return True if we should symlink, False if we should copy."""
    if args.symlink:
        return True
    if args.copy:
        return False
    # Auto: symlink on Unix, copy on Windows.
    return platform.system() != 'Windows'


def main():
    args = parse_args()
    if args.quiet:
        args.force = True
    use_symlink = resolve_install_mode(args)

    print_banner()

    project_root = find_project_root()
    if project_root is None:
        print()
        fail("Could not find the smoothie/ package directory.")
        info("Run this script from the Smoothie project root.")
        sys.exit(1)

    step("Checking prerequisites")
    if not check_prerequisites():
        print()
        fail("Missing prerequisites. Install them and re-run this script.")
        sys.exit(1)

    step("Detecting Blender installation")
    versions = detect_blender_versions()
    if not versions:
        fail("No Blender 5.x installation found.")
        root = blender_config_root()
        if root:
            info("Looked in: {}".format(root))
        info("Make sure Blender 5.1+ is installed and has been launched at least once.")
        info("Get Blender at https://www.blender.org/download/")
        sys.exit(1)
    target_version = select_blender_version(versions, args)
    addons_dir = versions[target_version]

    if not args.quiet:
        print()
        print("{}Ready to install:{}".format(BOLD, RESET))
        print("  * Python venv: {}".format(project_root / '.venv'))
        print("  * Install SDK: claude-agent-sdk (into venv)")
        print("  * Install CLI: @anthropic-ai/claude-code (global, via npm)")
        print("  * Add-on:      {} -> Blender {}".format(
            "symlink" if use_symlink else "copy", target_version))
        print("  * Target:      {}".format(addons_dir / 'smoothie'))
        print()
        try:
            input("Press Enter to continue, or Ctrl+C to cancel ...")
        except EOFError:
            pass

    step("Step 1/5: Creating Python virtual environment")
    create_venv(project_root)

    step("Step 2/5: Installing claude-agent-sdk into venv")
    install_sdk(project_root)

    step("Step 3/5: Recording venv location for sidecar launcher")
    write_venv_config(project_root)

    step("Step 4/5: Installing Claude Code CLI")
    install_claude_cli()

    step("Step 5/5: Installing Smoothie add-on into Blender")
    install_addon(project_root, addons_dir, use_symlink, args.force)

    print_next_steps(target_version)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print()
        print("Cancelled.")
        sys.exit(130)
