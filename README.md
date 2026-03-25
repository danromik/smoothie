# Smoothie

**AI-powered animation for Blender using natural language.**

Smoothie is a Blender add-on that lets you create animations by describing what you want in plain English. Type a prompt like *"make the cube bounce across the scene"* and Smoothie generates the Python code, shows it to you for review, and executes it in Blender with a single click.

## How It Works

1. Open the Smoothie chat panel in your browser from Blender's sidebar
2. Describe the animation you want in natural language
3. Review the generated code in the code pane
4. Click **Execute** to apply it — or **Undo** to revert

Smoothie uses the [Claude Agent SDK](https://docs.anthropic.com/en/docs/agents-and-tools/claude-agent-sdk) to interpret your intent, fetches your current scene context, and generates valid `bpy` Python code targeting Blender's API. All code runs in a sandboxed environment with full undo support.

## Interface

The browser-based UI (served at `localhost:8888`) has three panes:

- **Chat** — conversation with the AI assistant
- **Code** — generated code blocks with Execute, Undo, and Copy buttons
- **Developer** (hidden by default) — stream events, tool calls, and token usage for debugging

## Requirements

- **Blender 5.1+**
- **Python 3.10+** installed on your system (separate from Blender's embedded Python)
- **Claude Code CLI** installed and logged in, *or* an Anthropic API key

## Installation

### 1. Set up the Python environment

Smoothie's AI backend runs in a sidecar process using your system Python (not Blender's). Create a virtual environment in the project root:

```bash
cd /path/to/smoothie
python3 -m venv .venv
.venv/bin/pip install claude-agent-sdk
```

### 2. Install the Claude Code CLI (for subscription auth)

```bash
npm install -g @anthropic-ai/claude-code
```

Skip this if you plan to use an API key instead.

### 3. Install the add-on in Blender

Symlink the `smoothie/` package directory into Blender's add-ons folder:

```bash
# macOS
ln -s /path/to/smoothie/smoothie \
  ~/Library/Application\ Support/Blender/5.1/scripts/addons/smoothie

# Linux
ln -s /path/to/smoothie/smoothie \
  ~/.config/blender/5.1/scripts/addons/smoothie
```

Then in Blender: **Edit > Preferences > Add-ons** — search for "Smoothie" and enable it.

## Usage

1. In the 3D Viewport, press **N** to open the sidebar and find the **Smoothie** tab
2. Click **Open Chat in Browser** — your browser opens `localhost:8888`
3. (Optional) Click the settings icon to choose between Claude Code subscription or API key authentication, and select your preferred model
4. Start chatting!

## Architecture

Smoothie runs as three cooperating processes:

| Process | Role |
|---------|------|
| **Blender** | Executes `bpy` code on the main thread; exposes an internal HTTP API on port 8889 |
| **Sidecar** | System Python process running Starlette/uvicorn on port 8888; serves the UI, manages the AI conversation, proxies commands to Blender |
| **Claude CLI** | Spawned by the Agent SDK inside the sidecar; handles AI inference |

The sidecar architecture exists because the Claude Agent SDK has native dependencies that can't run inside Blender's embedded Python. The sidecar launcher automatically finds a suitable system Python with the SDK installed.

## Safety

- Generated code is validated via AST analysis before execution — dangerous imports (`os`, `subprocess`, `shutil`, etc.) are blocked
- Every execution is wrapped in an undo step so you can always revert
- Code is shown for review before execution (auto-execute is off by default)

## Project Structure

```
smoothie/
  __init__.py           # Add-on registration and preferences
  sidecar_launcher.py   # Finds system Python and launches the sidecar
  sidecar/              # AI backend + web UI server
  blender_api/          # Internal HTTP API for code execution
  executor/             # Sandboxed code runner with undo support
  ai/                   # Scene context builder and prompt templates
  ui/                   # Blender sidebar panel and operators
tests/
  scripts/              # Unit tests and integration test framework
```

## License

MIT
