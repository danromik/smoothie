"""Shared state for the sidecar process."""

import asyncio
import json
import logging
import os
import platform
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("smoothie.sidecar.state")


@dataclass
class Settings:
    blender_port: int = 0
    api_key: str = ""
    model: str = "claude-opus-4-6"
    auth_mode: str = "subscription"
    auto_execute: bool = False


@dataclass
class ChatMessage:
    id: str = ""
    role: str = ""
    content: str = ""
    code: str = ""
    post_message: str = ""
    has_code: bool = False
    code_executed: bool = False
    code_rejected: bool = False
    code_bytes: int = 0
    tool_detail: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "code": self.code,
            "post_message": self.post_message,
            "has_code": self.has_code,
            "code_executed": self.code_executed,
            "code_rejected": self.code_rejected,
            "code_bytes": self.code_bytes,
            "tool_detail": self.tool_detail,
        }

@dataclass
class ConversationState:
    messages: list[ChatMessage] = field(default_factory=list)
    is_streaming: bool = False
    active_code_index: int = -1
    version: int = 0
    sdk_session_id: str = ""
    needs_client_reset: bool = False
    last_usage: dict = field(default_factory=dict)


@dataclass
class PendingToolAction:
    """Holds state for a blocking tool call awaiting user execute/reject."""
    code: str = ""
    event: asyncio.Event = field(default_factory=asyncio.Event)
    result: str | None = None


# Module-level singletons
settings = Settings()
conversation = ConversationState()
sse_queues: dict[str, asyncio.Queue] = {}
pending_tool_action: PendingToolAction | None = None


def _settings_path() -> Path:
    """Return the path to the persistent settings JSON file."""
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "smoothie" / "settings.json"


def save_settings() -> None:
    """Persist current settings to disk."""
    path = _settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "auth_mode": settings.auth_mode,
            "model": settings.model,
            "auto_execute": settings.auto_execute,
            "api_key": settings.api_key,
        }
        path.write_text(json.dumps(data, indent=2))
        logger.info("Settings saved to %s", path)
    except OSError as e:
        logger.warning("Failed to save settings: %s", e)


def load_settings() -> None:
    """Load settings from disk if the file exists."""
    path = _settings_path()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
        if "auth_mode" in data:
            settings.auth_mode = data["auth_mode"]
        if "model" in data:
            settings.model = data["model"]
        if "auto_execute" in data:
            settings.auto_execute = data["auto_execute"]
        if "api_key" in data:
            settings.api_key = data["api_key"]
        logger.info("Settings loaded from %s (model=%s, auth=%s)", path, settings.model, settings.auth_mode)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load settings: %s", e)


def init(blender_port: int, api_key: str = "", model: str = "claude-opus-4-6") -> None:
    """Configure sidecar settings."""
    settings.blender_port = blender_port
    # Load persisted settings first
    load_settings()
    # CLI args override persisted settings only if explicitly provided
    if api_key:
        settings.api_key = api_key
        settings.auth_mode = "api_key"
    if model and model != "claude-sonnet-4-20250514":
        settings.model = model


def new_message_id() -> str:
    return str(uuid.uuid4())[:8]
