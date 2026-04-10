# Smoothie — AI-Powered Animation Add-on for Blender

> Architecture and conventions reference. Named `CLAUDE.md` so [Claude Code](https://claude.com/claude-code) picks it up automatically when working in this repo; contributors can read it as a general orientation doc.

## Project Overview

Smoothie is a Blender add-on that lets users create animations via natural language prompts. The Claude Agent SDK interprets user intent, generates `bpy` Python code, and the user approves or rejects execution within Blender — providing an agentic prompt-to-animation workflow. The user interacts with the AI assistant ("Smoothie") through a browser-based UI served from localhost.

## Tech Stack

- **Language**: Python 3.13 (Blender 5.1's embedded Python) + system Python (sidecar)
- **Blender API**: `bpy` — Blender's Python module for scene manipulation, animation, rendering, and export
- **AI Backend**: Claude Agent SDK (`claude-agent-sdk` v0.1.50+) wrapping the Claude Code CLI
- **Frontend**: Single-file HTML/CSS/JS UI served by the sidecar (Starlette + uvicorn)
- **Target Blender Version**: 5.x (5.1+)
- **Authentication**: Claude Code subscription (default) or Anthropic API key, configured in web UI

## Architecture

Three processes collaborate:

```
Process 1: Blender (bpy operations only)
  Main thread: bpy.app.timers callback (50ms) drains command_queue
  Daemon thread: Simplified HTTP server (stdlib) on port 8889
    - Code execution, undo, scene queries, library files, asset operations

Process 2: Sidecar (system Python, launched as subprocess by Blender)
  uvicorn + Starlette async server on port 8888
    - Serves frontend HTML
    - Handles chat API (send, stream, messages, clear, settings)
    - Proxies execute/undo/scene tools to Blender's internal API via httpx
    - Runs ClaudeSDKClient (which spawns Process 3)

Process 3: Claude CLI (spawned by Agent SDK)
  Managed by ClaudeSDKClient via stdin/stdout
```

### Directory Structure

```
smoothie/
├── __init__.py              # Add-on registration, preferences, sidecar lifecycle, load_post handler
├── sidecar_launcher.py      # Find system Python, start/stop/monitor sidecar subprocess
├── sidecar/
│   ├── main.py              # Entry point: argparse + uvicorn startup
│   ├── app.py               # Starlette routes, SSE endpoint, settings, library, project notes APIs
│   ├── agent.py             # ClaudeSDKClient wrapper, streaming, SDK session management
│   ├── tools.py             # MCP tools: code generation, scene exploration, library, assets, project notes
│   ├── blender_proxy.py     # httpx async client for Blender's internal API
│   ├── state.py             # Settings, ChatMessage, ConversationState, PendingToolAction
│   └── frontend.html        # Browser UI (chat | code | developer) with modals
├── blender_api/
│   ├── __init__.py          # start_server(), stop_server(), get_port()
│   ├── server.py            # ThreadingHTTPServer (stdlib)
│   ├── handlers.py          # HTTP routing for all Blender API endpoints
│   └── bridge.py            # command_queue + bpy.app.timers callback + all command handlers
├── ui/
│   ├── panel.py             # Minimal N-panel: "Open Chat" button + status
│   ├── operators.py         # open_browser, restart_sidecar operators
│   └── properties.py        # Minimal (no chat state — owned by sidecar)
├── ai/
│   ├── context.py           # Scene context builder + object/animation/material/search queries
│   └── templates.py         # System prompt and scene context template
├── executor/
│   ├── runner.py            # Executes generated bpy code with persistent namespace, library pre-loading, undo
│   └── sandbox.py           # Restricted exec environment (AST validation, blocked imports)
└── libs/                    # Empty (sidecar uses system Python)

# At the project root:
install.py                   # Cross-platform installer (prereqs, venv, CLI, add-on)
logs/
├── smoothie.log             # Blender-side log (appended)
└── sidecar.log              # Sidecar process log (overwritten each launch)
sample_project/              # Example video + chat transcript bundled with releases
├── space_battle.mp4         # Rendered sample animation
└── space_battle_chat.pdf    # Printed chat transcript that produced it
tests/
├── scripts/
│   ├── test_script.py       # Integration test (auto-triggered by watcher)
│   ├── test_watcher.py      # File watcher — runs test_script.py on changes
│   ├── conftest.py          # pytest fixtures (bpy stub)
│   ├── bpy_stub/            # bpy mock for unit tests outside Blender
│   ├── test_sandbox.py      # Unit tests for executor/sandbox
│   ├── test_runner.py       # Unit tests for executor/runner
│   └── test_context.py      # Unit tests for ai/context
└── results/
    └── test_results.txt     # Latest test output
.venv/                       # Python venv with claude-agent-sdk
```

## Key Design Decisions

### Sidecar Architecture
The Claude Agent SDK has heavy native dependencies that can't be vendored cross-platform into Blender's `libs/`. Solution: a **sidecar Python process** using a separate system Python. The sidecar launcher first checks for a `.venv` in the project root, then searches system Pythons. It launches whichever Python has `claude-agent-sdk` importable.

### SDK-Native Session Persistence
Chat sessions persist via the Claude Agent SDK's built-in session system. Only a session ID string is stored in the Blender document (`bpy.data.texts["smoothie_session"]`). On reload, `get_session_messages()` reconstructs the chat UI and `resume=session_id` gives the AI full context. No manual message serialization needed.

### Agentic Tool Execution (Human-in-the-Loop)
The `generate_blender_code` tool **blocks** via `asyncio.Event` until the user executes or rejects the code. The tool result (success/failure/rejection with reason) flows back to the AI, enabling multi-turn loops: generate → reject with feedback → revise → execute. With auto-execute enabled, the tool executes immediately without user interaction.

### Persistent Namespace
Functions and classes defined in one code execution persist in a session-level `_persistent_namespace` dict and are available in subsequent executions. Library files (`bpy.data.texts["smoothie_lib/..."]`) are pre-loaded into the namespace before each execution.

### Scene Exploration via Tools (Not Injected Context)
Scene context is NOT injected into every prompt. Instead, the AI uses MCP tools (`read_scene`, `search_objects`, `read_object`, etc.) to query the scene when needed. This avoids prompt bloat and gives the AI control over what information it retrieves.

### Project Notes (smoothie.md)
A project-specific notes file stored in `bpy.data.texts["smoothie.md"]`, included in the system prompt when present. The AI can read/update it via tools. Editable in the Library modal UI.

### Settings Persistence
User settings (model, auth mode, API key, auto-execute) persist to `~/.config/smoothie/settings.json` (or platform equivalent). Loaded on sidecar startup.

### Two Authentication Modes
- **Claude Code Subscription** (default): Agent SDK inherits CLI auth. No API key needed.
- **API Key**: User enters an Anthropic API key in the web UI settings.

## MCP Tools

### Code Generation
| Tool | Description |
|------|-------------|
| `generate_blender_code` | Generate and execute Python/bpy code (blocks for user approve/reject) |

### Scene Exploration
| Tool | Description |
|------|-------------|
| `read_scene` | Full scene overview |
| `read_object` | Deep detail on one object |
| `read_animation` | Keyframe data for one object |
| `list_objects` | Lightweight object list with optional type filter |
| `read_hierarchy` | Parent-child tree structure |
| `search_objects` | Search by name pattern / type / animation status |
| `search_by_material` | Find objects by material name |
| `read_materials` | All materials with shader settings |
| `read_render_settings` | Render engine, resolution, sampling, world |
| `read_timeline` | Frame range, markers, NLA strips |

### Project Notes & Library
| Tool | Description |
|------|-------------|
| `read_project_notes` | Read smoothie.md |
| `update_project_notes` | Create/update smoothie.md (triggers system prompt refresh) |
| `list_library_files` | List library files |
| `read_library_file` | Read a library file |
| `write_library_file` | Create/update a library file |
| `delete_library_file` | Delete a library file |

### Asset Management
| Tool | Description |
|------|-------------|
| `list_asset_libraries` | List Blender asset library paths |
| `search_assets` | Search local asset libraries |
| `import_asset` | Import asset from local library |
| `check_blenderkit` | Check if BlenderKit is installed/logged in |
| `search_blenderkit` | Search BlenderKit online catalog |
| `import_blenderkit_asset` | Download and import from BlenderKit |

## Frontend UI

The browser UI (`localhost:8888`) has:
- **Toolbar**: Settings dropdown, Library modal, project title, segmented [Code | Developer] pane toggle
- **Chat pane**: Messages, tool info events, code generation blocks with Execute/Reject/feedback controls, "..." menu with Clear Chat, Download Chat, and Print Chat
- **Code pane** (hidden by default, auto-opens on "View code"): Code viewer with line numbers and syntax highlighting, Copy code button
- **Developer pane** (hidden by default): Real-time stream events log

### Code Generation Block (in chat)
- Header with byte count + "View code" button
- Execute button, Reject button, feedback text input + Send
- Chat input disabled while code is pending action

### Download Chat
Exports a `.zip` containing:
- `chat-YYYY-MM-DD.md` — human-readable transcript
- `chat-YYYY-MM-DD.jsonl` — full SDK session data

### Print Chat
Opens a print-optimized view in a new browser window, styled to match the app UI (dark theme, message bubbles, tool info rows, code generation blocks). Code blocks are numbered inline with execution status, and full code listings with line numbers and syntax highlighting appear in an appendix. Uses the browser's native `window.print()` — the user can print or save as PDF via the OS print dialog.

## Development Workflow

### Prerequisites
1. **Project venv** with `claude-agent-sdk` installed
2. **Claude Code CLI** installed and logged in (for subscription auth)
3. **Blender 5.1+** installed

### Running in Blender
1. Symlink `smoothie/smoothie/` into Blender's add-ons path
2. Enable the add-on in Blender preferences
3. Open the N-panel → "Smoothie" tab → "Open Chat in Browser"
4. Configure authentication in Settings

### Testing
- **Integration tests**: `test_watcher.py` watches `test_script.py` for changes, runs automatically, writes to `test_results.txt`
- **Unit tests**: `python3 -m pytest tests/scripts/`

### Logging
- Blender-side: `logs/smoothie.log` (appended)
- Sidecar-side: `logs/sidecar.log` (overwritten each launch)

## Conventions

- Blender add-on conventions: `bl_info` dict, `register()`/`unregister()`
- Operator classes: `SMOOTHIE_OT_` prefix
- Panel classes: `SMOOTHIE_PT_` prefix
- Code organization: UI in `ui/`, AI in `sidecar/`, execution in `executor/`, internal API in `blender_api/`
- Blender internal API: stdlib only
- Sidecar: `starlette` + `uvicorn`
- Frontend: single-file HTML, CDN-loaded JS libraries
- MCP tool results: always use `{"content": [{"type": "text", "text": "..."}]}` format
- Library files: stored as `bpy.data.texts["smoothie_lib/..."]`
- BlenderKit addon detection: search for `"blenderkit"` in addon name (handles `bl_ext.user_default.blenderkit` format)
